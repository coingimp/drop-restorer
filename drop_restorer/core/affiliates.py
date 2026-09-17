"""Read provider tables and verify immutable brand -> exact URL bindings."""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import re
import socket
import time
from urllib.parse import parse_qsl, urlsplit, urljoin
from pathlib import Path
from io import BytesIO

from bs4 import BeautifulSoup
import requests
from PIL import Image

from .models import RestorationError
from .seo_contract import save_json

PROVIDER = 'https://cryptocasinokingdom.com'
WIDGET = PROVIDER + '/_widget/2.0/hds_widget.js'
USER_AGENT = 'Mozilla/5.0 (compatible; DropRestorer/1.0)'


def resolve_offer(offers, alias, origin, referer=''):
    """Resolve a clean brand URL from server-owned bindings, never client targets.

    Same-origin page context preserves page-specific tracking. Direct visits may
    share an offer only when the known tracker differs solely in its pg value.
    Conflicting campaigns on the same page cannot share a clean brand URL.
    """
    if not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', alias):
        return None
    rows = [o for o in offers if o.get('alias') == alias]
    try:
        ref, base = urlsplit(referer), urlsplit(origin)
        same_origin = (ref.scheme, ref.netloc.lower()) == (base.scheme, base.netloc.lower())
    except ValueError:
        same_origin = False
    contextual = [o for o in rows if same_origin and o.get('source_route') == (ref.path or '/')]
    rows = contextual or rows
    targets = {o['target'] for o in rows}
    if len(targets) == 1:
        return rows[0]
    if contextual or not rows:
        return None
    signatures = set()
    for row in rows:
        target = urlsplit(row['target'])
        if target.scheme != 'https' or target.netloc != 'gmbl.guru' or target.path != '/lldrp' or row.get('context_issues'):
            return None
        parameters = parse_qsl(target.query, keep_blank_values=True)
        if len([p for p in parameters if p[0] == 'pg']) != 1:
            return None
        signatures.add((target.fragment, tuple(p for p in parameters if p[0] != 'pg')))
    if len(signatures) != 1:
        return None
    return min(rows, key=lambda o: (o['source_route'], o['table_id'], o['id']))


def parameter_issues(parameters, context, target, alias, origin, route):
    tracking = urlsplit(target).hostname == 'gmbl.guru' and urlsplit(target).path == '/lldrp'
    mapping = {'dept':'d','st':'st','p':'pr'} if tracking else {'dept':'dept','st':'st','p':'p'}
    expected = [(mapping[k],v) for k,v in context.items() if k in mapping and v]
    expected += parse_qsl(context.get('sfx',''),keep_blank_values=True)
    if tracking:
        host=urlsplit(origin).hostname
        expected += [('br',alias),('s',host),('pg',host+route)]
    issues=[]
    for key,value in expected:
        found=[v for k,v in parameters if k==key]
        matches=found==[value]
        if tracking and key=='st':
            matches=len(found)==1 and found[0].casefold()==value.casefold()
        if not matches:
            issues.append('Не подтверждён параметр '+key+' из таблицы/страницы.')
    if tracking:
        for key in ('net','g'):
            found=[v for k,v in parameters if k==key]
            if len(found)!=1 or not found[0]:
                issues.append('Отсутствует или неоднозначен параметр '+key+'.')
    return issues


def validate_target(target):
    parsed = urlsplit(target)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port
            or re.search(r'[\s\\\x00-\x1f\x7f]', target) or re.search(r'%0[ad]', target, re.I)):
        raise RestorationError('Партнёрская ссылка содержит недопустимый адрес.')
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if '.' not in parsed.hostname or parsed.hostname.endswith(('.localhost', '.local', '.internal')):
            raise RestorationError('Партнёрская ссылка должна вести на публичный домен.')
    else:
        if not address.is_global:
            raise RestorationError('Партнёрская ссылка не может вести в локальную сеть.')
    return parsed


def public_host(url):
    parsed = validate_target(url)
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise RestorationError('Адрес назначения не относится к публичной сети.')


def parse_table(html, table_id, origin, route):
    soup = BeautifulSoup(html.lstrip('\ufeff'), 'lxml')
    links = soup.select('a.js-go-link[data-slug]')
    if not links:
        raise RestorationError('В ответе виджета нет таблицы с партнёрскими ссылками. Формат не распознан.')
    roots = soup.select('.casino-list, [data-style="folders"], [data-style="offer_wall"]')
    context = {key:(roots[0].get('data-' + key, '') if roots else '') for key in ('dept','st','p','sfx')}
    offers = {}
    for link in links:
        alias = link.get('data-slug', '').strip().lower()
        if not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', alias):
            raise RestorationError('Некорректный партнёрский алиас: ' + alias[:80])
        target = link.get('data-direct-url') or link.get('href', '')
        validate_target(target)
        image = link.find('img', alt=True)
        card = link.find_parent(class_=re.compile(r'casino-item|casino-card|casino-row|folder-item|ow-card'))
        heading = card.select_one('.casino-row__logo p:not(.casino-row__label), h2,h3,h4') if card else None
        label = link.find('p')
        brand = (link.get('data-brand') or (image.get('alt') if image else '') or
                 (label.get_text(' ',strip=True) if label else '') or (heading.get_text(' ', strip=True) if heading else '') or alias).strip()
        # No truncation or re-encoding: preserve all aliases, duplicate keys,
        # escapes, empty values and signed query parameters byte for byte.
        identifier = hashlib.sha256((str(table_id) + '\n' + origin + route + '\n' + alias + '\n' + target).encode()).hexdigest()[:24]
        parameters = parse_qsl(urlsplit(target).query, keep_blank_values=True)
        context_issues = parameter_issues(parameters,context,target,alias,origin,route)
        row = offers.setdefault(identifier, {'id':identifier, 'table_id':table_id, 'brand':brand, 'alias':alias,
                    'target':target, 'parameters':parameters, 'context':context, 'context_issues':context_issues,
                    'source_route':route, 'links':0, 'go':'/go/' + alias})
        if row['brand'] == alias and brand != alias:
            row['brand'] = brand
        row['links'] += 1
        link['href'] = row['go']
        link['rel'] = ['nofollow', 'sponsored', 'noopener']
        link['referrerpolicy'] = 'same-origin'
        link.attrs.pop('data-direct-url', None)
        link['data-dr-offer'] = identifier
    for node in list(soup.select('script, style, iframe, object, embed, form, base, meta, link')):
        node.decompose()
    for node in soup.find_all(True):
        for attr in list(node.attrs):
            if attr.lower().startswith('on'):
                del node.attrs[attr]
    for link in list(soup.select('a[href]')):
        if not link.get('data-dr-offer') and not link['href'].startswith('#'):
            link.unwrap()
    return {'table_id':table_id, 'origin':origin, 'route':route, 'offers':list(offers.values()),
            'html':soup.body.decode_contents() if soup.body else str(soup), 'source_sha256':hashlib.sha256(html.encode()).hexdigest()}


def fetch_table(table_id, origin, route, session=None):
    if type(table_id) is not int or not 1 <= table_id <= 1_000_000:
        raise RestorationError('ID таблицы должен быть целым положительным числом.')
    client = session or requests.Session()
    owns = session is None
    try:
        host = urlsplit(origin).hostname
        response = client.get(PROVIDER + '/', params={'hds_table_id':table_id, 's':host, 'pg':host + route, 'match_slug':'','cb':str(int(time.time()*1000))},
                              headers={'User-Agent':USER_AGENT,'Accept':'text/html,*/*;q=0.8'},timeout=(15,40), allow_redirects=False)
        if response.status_code != 200:
            raise RestorationError(f'Провайдер таблицы {table_id} вернул HTTP {response.status_code}. Данные не подтверждены.')
        if len(response.content) > 3_000_000:
            raise RestorationError('Ответ таблицы превышает допустимый размер.')
        return parse_table(response.text, table_id, origin, route)
    finally:
        if owns:
            client.close()


def sync(build, table_id, log=lambda message:None, cancel=None):
    path = build.root / 'affiliate-manifest.json'
    result = {'version':1, 'provider':PROVIDER, 'table_id':table_id, 'checked_at':datetime.now(timezone.utc).isoformat(),
              'tables':[], 'errors':[], 'offers':[]}
    for page in [p for p in build.pages if p.casino]:
        if cancel and cancel.is_set():
            raise RestorationError('Проверка таблиц отменена.')
        try:
            table = fetch_table(table_id, build.request.origin, page.route)
            localize_table(table,build.root)
            result['tables'].append(table)
            result['offers'].extend(table['offers'])
            log(f'Таблица {table_id}: {page.route}, предложений {len(table["offers"])}')
        except (RestorationError, requests.RequestException, OSError) as error:
            result['errors'].append({'route':page.route, 'error':str(error)})
    save_json(path, result)
    build.seo_policy['widget_id'] = table_id
    build.seo_policy['affiliate_reviews'] = {}
    build.status = 'site_review'
    build.approved_digest = None
    build.save()
    return result


def localize_table(table, root):
    """Cache passive widget assets; previews never execute the provider loader."""
    folder=root/'theme/assets/widget';folder.mkdir(parents=True,exist_ok=True)
    cache={};errors=[]
    def asset(url,kind='image'):
        absolute=urljoin(PROVIDER,url)
        if absolute in cache:
            return cache[absolute]
        try:
            public_host(absolute)
            with requests.get(absolute,headers={'User-Agent':USER_AGENT},timeout=(10,20),allow_redirects=False,stream=True) as response:
                if response.status_code!=200:
                    raise RestorationError('HTTP '+str(response.status_code))
                chunks=[];total=0
                for chunk in response.iter_content(65536):
                    total+=len(chunk)
                    if total>8_000_000:
                        raise RestorationError('Ресурс слишком большой')
                    chunks.append(chunk)
                raw=b''.join(chunks);mime=response.headers.get('Content-Type','').split(';')[0]
            suffix=Path(urlsplit(absolute).path).suffix.lower()
            if kind=='image':
                with Image.open(BytesIO(raw)) as image:
                    image.verify()
                if suffix not in ('.png','.jpg','.jpeg','.webp','.gif','.ico','.avif'):
                    suffix='.png'
            elif kind=='font':
                if suffix not in ('.woff','.woff2','.ttf','.otf'):
                    raise RestorationError('Нераспознанный шрифт')
            else:
                if 'css' not in mime:
                    raise RestorationError('Провайдер не вернул CSS')
                code=raw.decode('utf-8','replace')
                if re.search(r'expression\(|javascript:',code,re.I):
                    raise RestorationError('CSS содержит неподдерживаемый загрузчик')
                imports=[]
                def import_css(match):
                    local=asset(urljoin(absolute,match.group(1)),'css')
                    if local: imports.append('@import url("'+local+'");')
                    return ''
                code=re.sub(r'''@import\s+(?:url\(\s*)?["']([^"']+)["']\s*\)?\s*;''',import_css,code,flags=re.I)
                if re.search(r'@import',code,re.I):
                    raise RestorationError('Неподдерживаемый формат CSS import')
                def replace(match):
                    value=match.group(1).strip(' \"\'')
                    if value.startswith('data:'):
                        return match.group(0)
                    resource=urljoin(absolute,value)
                    return 'url("'+asset(resource,'font' if re.search(r'\.(woff2?|ttf|otf)(?:\?|$)',resource) else 'image')+'")'
                raw=('\n'.join(imports)+'\n'+re.sub(r'url\(([^)]+)\)',replace,code)).encode();suffix='.css'
            name=hashlib.sha256(absolute.encode()).hexdigest()[:20]+suffix
            (folder/name).write_bytes(raw);cache[absolute]='/assets/widget/'+name
            return cache[absolute]
        except (OSError,ValueError,requests.RequestException,RestorationError) as error:
            errors.append({'url':absolute,'error':str(error)[:180]});cache[absolute]='';return ''
    soup=BeautifulSoup(table['html'],'lxml')
    for image in soup.select('img'):
        original=image.get('src') or image.get('data-src','')
        if original.startswith('data:image/'):
            continue
        image['src']=asset(original) if original else ''
        for name in ('srcset','data-src','data-srcset','data-lazy-src'):
            image.attrs.pop(name,None)
    styles=['/_widget/2.0/style.css']
    if soup.select('[data-style="folders"]'): styles.append('/_widget/2.0/folders.css')
    if soup.select('[data-style="offer_wall"]'): styles.append('/_widget/2.0/offer-wall.css')
    if soup.select('.hds-world-cup-widget'): styles.append('/_widget/2.0/world-cup-2026.css')
    table['styles']=[asset(url,'css') for url in styles]+['/assets/dr-widget.css']
    table['asset_errors']=errors
    table['html']=soup.body.decode_contents() if soup.body else str(soup)


def verify_offer(offer, session=None):
    """Record each actual hop; access/geo/JS barriers stay unknown, never PASS."""
    result = {'offer':offer['id'], 'target':offer['target'], 'checked_at':datetime.now(timezone.utc).isoformat(),
              'chain':[], 'status':'unknown', 'final_url':None}
    client = session or requests.Session()
    current = offer['target']
    try:
        for _ in range(8):
            public_host(current)
            response = client.get(current, headers={'User-Agent':USER_AGENT}, timeout=(10,20), allow_redirects=False, stream=True)
            code, location = response.status_code, response.headers.get('Location')
            response.close()
            result['chain'].append({'url':current, 'status':code, 'location':location})
            if code in (301,302,303,307,308) and location:
                following = urljoin(current, location)
                if any(hop['url'] == following for hop in result['chain']):
                    result['status'] = 'fail'; result['error'] = 'Цикл редиректов.'; break
                current = following
                continue
            result['final_url'] = current
            result['status'] = 'reachable' if code == 200 else 'unknown' if code in (401,403,429,451) else 'fail'
            break
        else:
            result['status'] = 'fail'; result['error'] = 'Слишком длинная цепочка редиректов.'
    except (requests.RequestException, OSError, RestorationError) as error:
        result['error'] = str(error)[:300]
    finally:
        if session is None:
            client.close()
    return result
