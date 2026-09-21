"""Keep check results separate from the owner's decision to continue."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit, urljoin, unquote
from urllib.robotparser import RobotFileParser

from lxml import etree
from PIL import Image
import requests

from .cleaner import parse_html, casino_shell_issues, SHELL_REGIONS
from .cleanup_policy import script_problem, foreign_language
from .models import RestorationError
from .review import content_node, source_metadata
from .seo_contract import DEFINITIONS, VERSION, fingerprint, save_json
from .seo_routes import indexable
from .indexation import indexation_blockers, robot_tags


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def text_key(value):
    return re.sub(r'\s+', ' ', value).strip().casefold()


def inspect_site(build):
    if not build.seo_policy:
        raise RestorationError('Откройте сайт на новом этапе SEO-чек-листа.')
    from ..preview.server import PreviewServer
    current = fingerprint(build)
    server = PreviewServer(build, audit_mode=True)
    session = requests.Session()
    session.trust_env = False
    responses = {}
    def fetch(route):
        if route not in responses:
            response = session.get(server.origin + route, timeout=10, allow_redirects=False)
            responses[route] = {'status':response.status_code, 'location':response.headers.get('Location', ''),
                                'robots':response.headers.get('X-Robots-Tag', ''),
                                'content_type':response.headers.get('Content-Type',''), 'text':response.text}
        return responses[route]
    checks = {d['key']:{'fail':[], 'evidence':[]} for d in DEFINITIONS}
    manual = set()
    unchecked = set()
    na = {}
    def record(key, ok, detail, route=''):
        checks[key]['evidence'].append({'route':route, 'detail':detail})
        if not ok:
            checks[key]['fail'].append({'route':route, 'detail':detail})
    documents, pages = {}, []
    home = next((p for p in build.pages if p.route == '/' and not p.casino), build.pages[0])
    source_shell = parse_html(home.html)
    try:
        route_set = {p.route for p in build.pages}
        expected = len(build.request.menu_pages) + 4
        record('planned_pages', len(build.pages) == expected and len(route_set) == expected,
               f'Запланировано {expected}, создано {len(build.pages)}, уникальных URL {len(route_set)}.')
        record('url_style', build.seo_policy.get('url_style') in ('slash','no_slash'),
               'Структура: ' + str(build.seo_policy.get('url_style')))
        absent = '/__dr-check-missing-' + current[:12] + '/'
        record('http_404', fetch(absent)['status'] == 404, f'{absent}: HTTP {fetch(absent)["status"]}')
        robot = fetch('/robots.txt')
        sm = fetch('/sitemap.xml')
        expected_sitemap = build.request.origin + '/sitemap.xml'
        record('robots_sitemap', robot['status'] == 200 and 'Sitemap: ' + expected_sitemap in robot['text'], robot['text'])
        record('sitemap_http', sm['status'] == 200, 'GET /sitemap.xml: HTTP ' + str(sm['status']))
        robot_parser = RobotFileParser(); robot_parser.parse(robot['text'].splitlines())
        try:
            xml = etree.fromstring(sm['text'].encode(), etree.XMLParser(resolve_entities=False,no_network=True))
            sitemap_urls = xml.xpath('//*[local-name()="loc"]/text()')
        except etree.XMLSyntaxError:
            sitemap_urls = []
        expected_urls = [build.request.origin + p.route for p in build.pages if indexable(build,p)]
        record('sitemap_urls', sorted(sitemap_urls) == sorted(expected_urls), 'URL в sitemap: ' + ', '.join(sitemap_urls))
        for source, target in build.seo_policy.get('redirects', {}).items():
            response = fetch(source)
            ok = response['status'] == 301 and response['location'] == target and fetch(target)['status'] == 200
            record('alternatives', ok, f'{source} → {response["status"]} → {response["location"]}', target)
            record('url_duplicates', ok, f'{source} → {response["status"]} → {target}', target)
            if re.search(r'/index\.(php|html?)$', source):
                record('index_duplicates', ok, f'{source}: HTTP {response["status"]}', target)
        for page in build.pages:
            route = page.route
            response = fetch(route)
            record('http_200', response['status'] == 200, 'HTTP ' + str(response['status']), route)
            soup = parse_html(response['text']); documents[page.key] = soup
            from .primary_navigation import primary_menu_issues
            for detail in primary_menu_issues(soup):
                record('menu_visual', False, detail, route)
            if page.casino:
                problems = casino_shell_issues(source_shell, soup)
                for region, detail in problems:
                    record('footer' if region == 'footer' else 'menu_visual' if region == 'navigation' else 'content_visual', False, detail, route)
                if not problems:
                    record('content_visual', True, 'Оболочка казино сохраняет шапку, навигацию и подвал исходной страницы; контейнер редактора присутствует.', route)
            for script in soup.select('script'):
                src=script.get('src',''); code=script.string or script.get_text()
                if src.startswith('/assets/'):
                    script_path=build.root/'theme'/src.lstrip('/')
                    if script_path.resolve().is_relative_to((build.root/'theme').resolve()) and script_path.is_file():
                        code=script_path.read_text(encoding='utf-8',errors='replace')
                problem=script_problem(code,src)
                record('technical',not problem and (not src or src.startswith('/assets/')),
                       problem or ('Локальный скрипт: '+src if src else 'Встроенный скрипт проверен.'),route)
            main = content_node(soup)
            metadata = source_metadata(soup)
            body_text = main.get_text(' ',strip=True)
            indexed = indexable(build,page)
            record('url_readable', '?' not in route and not re.search(r'/(?:dr-[a-f0-9]{16}|\d{8,})(?:/|$)', route), route, route)
            polluted = route + ('&' if '?' in route else '?') + 'utm_source=dr-check&unexpected=1'
            alt = fetch(polluted)
            record('query_duplicates', alt['status'] == 301 and alt['location'] == route, f'{polluted}: {alt["status"]} → {alt["location"]}', route)
            canonical = soup.select('link[rel~="canonical"]')
            correct_canonical = len(canonical)==1 and canonical[0].get('href')==build.request.origin+route
            for key in ('canonical','canonical_index'):
                record(key, correct_canonical, 'Каноникл: ' + (canonical[0].get('href','') if canonical else 'отсутствует'),route)
            record('lang', soup.html.get('lang') == build.request.lang, 'html lang=' + soup.html.get('lang',''), route)
            og = soup.select('meta[property="og:locale"]')
            expected_locale=build.request.lang.replace('-','_')
            record('og_locale', not og or all(n.get('content','').casefold()==expected_locale.casefold() for n in og),
                   'Не используется' if not og else ', '.join(n.get('content','') for n in og),route)
            robots_meta=','.join([n.get('content','') for n in robot_tags(soup)] + [response['robots']]).lower()
            if indexed:
                for key in ('robots_allow','robots_sections'):
                    record(key, robot_parser.can_fetch('*',build.request.origin+route), 'Сканирование разрешено.',route)
                restricted = bool(re.search(r'\b(?:noindex|none)\b', robots_meta) or indexation_blockers(soup))
                record('noindex',not restricted, 'robots='+(robots_meta or 'не задан'),route)
                record('title_count',len(soup.select('title'))==1 and bool(metadata['title'].strip()),f'Title: {len(soup.select("title"))}',route)
                record('description_count',len(soup.select('meta[name="description" i]'))==1 and bool(metadata['description'].strip()),
                       f'Description: {len(soup.select("meta[name=description]"))}',route)
                record('h1_count',len(soup.select('h1'))==1 and bool(soup.select_one('h1').get_text(strip=True)),f'H1: {len(soup.select("h1"))}',route)
                record('empty_200',len(body_text)>=80,'Текст основного блока: '+str(len(body_text))+' знаков.',route)
                record('content_present',len(body_text)>=80,body_text[:350],route)
            local_links=[]
            external=[]
            for anchor in soup.select('a[href]'):
                href=anchor['href']
                if href.startswith('#'):
                    continue
                parsed=urlsplit(urljoin(build.request.origin+route,href))
                if parsed.netloc != urlsplit(build.request.origin).netloc:
                    external.append(href);continue
                local=parsed.path+('?' + parsed.query if parsed.query else '')
                if local.startswith('/go/'):
                    record('affiliate_go',bool(re.fullmatch(r'/go/[a-z0-9]+(?:[.-][a-z0-9]+)*',parsed.path)) and not parsed.query and not parsed.fragment,
                           'Публичная партнёрская ссылка: '+href,route)
                    continue
                status=fetch(local)['status'];local_links.append({'url':local,'status':status})
            bad=[v for v in local_links if v['status']>=400]
            redirects=[v for v in local_links if 300<=v['status']<400]
            for key in ('links_broken','links_errors'):
                record(key,not bad,json.dumps(bad,ensure_ascii=False) if bad else f'{len(local_links)} внутренних ссылок без ошибок.',route)
            for key in ('links_redirect','links_redirect_repeat'):
                record(key,not redirects,json.dumps(redirects,ensure_ascii=False) if redirects else 'Нет ссылок с редиректом.',route)
            record('donor_links',not external,', '.join(external) or 'Внешних ссылок исходного сайта нет.',route)
            menu=soup.select('#dr-primary-menu')
            menu_targets={n['href'] for n in soup.select('#dr-primary-menu a[href]')}
            record('menu_links',len(menu)==1 and menu_targets==route_set, 'Меню: '+', '.join(sorted(menu_targets)),route)
            footers = soup.select(SHELL_REGIONS['footer'][0])
            record('footer',not any(fetch(a['href'].split('#')[0] or '/')['status']>=400
                                     for footer in footers for a in footer.select('a[href]') if a['href'].startswith('/') and not a['href'].startswith(('/go/', '//'))),
                   'Ссылки подвала проверены; внешний вид требует просмотра.',route)
            icons=soup.select('link[rel~="icon"]')
            valid_icon=False
            for icon in icons:
                path=build.root/'theme'/icon.get('href','').lstrip('/')
                if path.resolve().is_relative_to((build.root/'theme').resolve()) and path.is_file():
                    try:
                        with Image.open(path) as image:
                            image.verify()
                        valid_icon=valid_icon or fetch(icon.get('href',''))['status'] == 200
                    except (OSError,ValueError):
                        pass
            record('favicon_exists',valid_icon,'Локальный favicon декодируется.' if valid_icon else 'Favicon отсутствует или повреждён.',route)
            image_errors=[]
            for image in soup.select('img'):
                src=image.get('src','')
                if src.startswith('data:image/'):
                    continue
                path=build.root/'theme'/src.lstrip('/')
                if not src.startswith('/assets/') or not path.resolve().is_relative_to((build.root/'theme').resolve()) or not path.is_file():
                    image_errors.append(src or '(нет src)')
            record('images_broken',not image_errors,', '.join(image_errors) or 'Все ссылки изображений ведут на локальные файлы.',route)
            foreign=soup.select('[lang], [hreflang]')
            record('content_geo',not any(foreign_language(n.get('lang') or n.get('hreflang',''),build.request.lang) for n in foreign),
                   'Языковые атрибуты проверены. Смысл текста и GEO подтверждаются отдельно.',route)
            page_text=soup.get_text(' ',strip=True)
            entities={'emails':sorted(set(re.findall(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}',page_text))),
                      'phones':sorted(set(re.findall(r'(?<!\w)(?:\+\d[\d ()-]{7,20}\d|\b\d{3}[ -]\d{3}[ -]\d{3}\b)',page_text))),
                      'organizations':[n.get_text(' ',strip=True)[:240] for n in soup.select('h1,h2,h3,footer strong')][:35],
                      'trademarks':[n.get('alt','')[:160] for n in soup.select('img[alt]') if n.get('alt')][:40],
                      'people':sorted(set(re.findall(r'\b[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]+(?: [A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]+){1,2}',page_text)))[:50],
                      'addresses':(soup.find('footer').get_text(' ',strip=True)[:2500] if soup.find('footer') else '')}
            pages.append({'key':page.key,'route':route,'title':page.title,'indexable':indexed,'casino':page.casino,
                          'metadata':metadata,'h1':[n.get_text(' ',strip=True) for n in soup.select('h1')],
                          'content_excerpt':body_text[:1500], 'entities':entities})
        for field, key in (('title','title_meaning'),('description','description_meaning')):
            counts=Counter(text_key(p['metadata'][field]) for p in pages if p['indexable'])
            for p in pages:
                if p['indexable']:
                    value=p['metadata'][field]
                    record(key,bool(value.strip()) and counts[text_key(value)]==1,value or 'Отсутствует',p['route'])
        for route in ('/wp-json/','/feed/','/?s=test','/author/admin/','/wp-sitemap.xml','/wp-login.php','/index.php?unknown=1'):
            response=fetch(route)
            ok=response['status'] in (301,302,403,404,410) or 'noindex' in response['text']
            record('technical',ok,f'{route}: HTTP {response["status"]}')
    finally:
        session.close();server.stop()

    manual.update(('title_meaning','h1_meaning','description_meaning','content_present','content_visual','content_geo',
                   'menu_visual','footer','desktop','mobile','readable','favicon_browser',
                   'organizations','trademarks','people','phones','addresses','emails'))
    visual=read_json(build.root/'visual-checks.json',{})
    expected_views={(p.route,device) for p in build.pages for device in ('Desktop','Tablet','Mobile')}
    actual_views={(p.get('route'),p.get('device')) for p in visual.get('pages',[])}
    if visual.get('fingerprint')!=current or not visual.get('complete') or actual_views!=expected_views:
        visual={}
    visual_fields={'images_broken':'broken_images','overflow':'overflow','images_bounds':'images_outside',
                   'readable':'unreadable','menu_mobile':'menu_failed','menu_mobile_repeat':'menu_failed',
                   'favicon_browser':'favicon_failed'}
    for key, field in visual_fields.items():
        if not visual.get('pages'):
            unchecked.add(key)
            record(key,True,'Нужна автоматическая проверка в браузере и просмотр результата.')
        else:
            for row in visual['pages']:
                record(key,not row.get(field),json.dumps(row.get(field) or 'OK',ensure_ascii=False),row['route']+' · '+row['device'])
    affiliate=read_json(build.root/'affiliate-manifest.json',{})
    affiliate_keys=('affiliate_mobile','affiliate_desktop','affiliate_links','affiliate_go')
    if not build.seo_policy.get('widget_id'):
        for key in affiliate_keys:
            na[key]='Таблица ещё не добавлена владельцем; страницы открыты для индексации. Проверка обязательна после добавления шорткода.'
    elif affiliate.get('errors') or not affiliate.get('offers'):
        for key in affiliate_keys:
            record(key,False,json.dumps(affiliate.get('errors') or 'Нет проверенных данных таблицы.',ensure_ascii=False))
    else:
        for table in affiliate.get('tables',[]):
            for key in ('affiliate_mobile','affiliate_desktop'):
                record(key,not table.get('asset_errors'),json.dumps(table.get('asset_errors') or 'Ресурсы таблицы загружены.',ensure_ascii=False),table.get('route',''))
        server=PreviewServer(build,audit_mode=True)
        try:
            for offer in affiliate['offers']:
                clean=offer['go']=='/go/'+offer['alias']
                response=requests.get(server.origin+offer['go'],headers={'Referer':server.origin+offer['source_route']},timeout=10,allow_redirects=False)
                record('affiliate_go',clean and response.status_code==302 and response.headers.get('Location')==offer['target'],
                       f'{offer["brand"]} · {offer["go"]} → {response.status_code} → {response.headers.get("Location","")}',offer['source_route'])
                review=build.seo_policy.get('affiliate_reviews',{}).get(offer['id'],{})
                good=review.get('target')==offer['target'] and review.get('status')=='confirmed'
                record('affiliate_links',not offer.get('context_issues'), '; '.join(offer.get('context_issues',[])) or 'Параметры: '+json.dumps(offer['parameters'],ensure_ascii=False),offer['source_route'])
                if not good:
                    manual.add('affiliate_links')
            manual.update(('affiliate_mobile','affiliate_desktop'))
        finally:
            server.stop()
    result=[]
    decisions=build.seo_policy.get('decisions',{})
    for definition in DEFINITIONS:
        key=definition['key']; value=checks[key]; decision=decisions.get(definition['id'],{})
        status='fail' if value['fail'] else 'not_checked' if key in unchecked else 'review' if key in manual else 'pass'
        if key in na:
            status='not_applicable';value['evidence'].append({'route':'','detail':na[key]})
        if status=='review' and decision.get('fingerprint')==current and decision.get('decision')=='confirm':
            status='confirmed'
        if status=='review' and decision.get('fingerprint')==current and decision.get('decision')=='reject':
            status='fail'
        result.append({**definition,'status':status,'failures':value['fail'],'evidence':value['evidence'],
                       'manual':key in manual,'decision':decision if decision.get('fingerprint')==current else None})
    counts=Counter(row['status'] for row in result)
    checks_passed=not counts['fail'] and not counts['review'] and not counts['not_checked']
    approval=build.owner_approvals.get('site_review',{})
    if approval.get('fingerprint')!=current or approval.get('by')!='owner':
        approval=None
    report={'version':VERSION,'definition_hash':hashlib.sha256(json.dumps(DEFINITIONS,ensure_ascii=False).encode()).hexdigest(),
             'fingerprint':current,'checked_at':datetime.now(timezone.utc).isoformat(),'stage':'before_theme',
            'items':result,'counts':dict(counts),'checks_passed':checks_passed,
            'owner_approval':approval,'can_build':checks_passed or bool(approval),
            'pages':pages,'visual':visual,'affiliates':affiliate,'favicon':read_json(build.root/'favicon-report.json',{}),'ready_for_public_indexing':False,
            'deployment_required':['На установленном сайте проверить HTTP/HTTPS и www/apex, 301 на выбранный домен, TLS и настройки веб-сервера.',
                                   'Повторить HTTP, robots/sitemap и метаданные на настоящем WordPress после установки.',
                                   'Заполнить и проверить казино, контент, SEO и таблицы до открытия их индексации.'],
            'scope':'HTTP проверен локальными запросами к подготовленному сайту. Смысл/внешний вид подтверждает владелец; публичный домен ещё не проверен.'}
    suggestion=read_json(build.root/'checklist-agent.json',{})
    if suggestion.get('fingerprint')==current:
        report['agent_review']=suggestion.get('text','')
    save_json(build.root/'seo-checklist.json',report)
    return report


def require_pass(build):
    report=inspect_site(build)
    if not report['can_build']:
        raise RestorationError(f'Чек-лист не завершён: ошибок {report["counts"].get("fail",0)}, требуют просмотра {report["counts"].get("review",0)}, не проверено {report["counts"].get("not_checked",0)}. Исправьте замечания или одобрите текущую сборку для продолжения.')
    return report


def approval_snapshot(report):
    return {'by':'owner','at':datetime.now(timezone.utc).isoformat(),
            'fingerprint':report['fingerprint'],'counts':dict(report['counts']),
            'unresolved':[{'id':row['id'],'status':row['status']} for row in report['items']
                          if row['status'] in ('fail','review','not_checked')]}


def approve_site(build, viewed_fingerprint):
    if build.status!='site_review':
        raise RestorationError('Одобрение сайта доступно после подготовки страниц и метаданных.')
    if not viewed_fingerprint or viewed_fingerprint!=fingerprint(build):
        raise RestorationError('Сайт изменился. Обновите отчёт и одобрите текущую версию.')
    report=inspect_site(build)
    if report['fingerprint']!=viewed_fingerprint or fingerprint(build)!=viewed_fingerprint:
        raise RestorationError('Сайт изменился во время проверки. Обновите отчёт.')
    build.owner_approvals['site_review']=approval_snapshot(report)
    build.approved_digest=None
    build.save()


def decide(build, identifier, decision, note):
    report=inspect_site(build)
    item=next((row for row in report['items'] if row['id']==identifier),None)
    if not item or not item['manual'] or item['failures'] or item['status']=='not_checked':
        raise RestorationError('Автоматическую ошибку нельзя заменить ручной отметкой. Исправьте причину.')
    if item['key']=='affiliate_links' and decision=='confirm':
        offers=report['affiliates'].get('offers',[])
        reviews=build.seo_policy.get('affiliate_reviews',{})
        if any(reviews.get(o['id'],{}).get('status')!='confirmed' or reviews[o['id']].get('target')!=o['target'] for o in offers):
            raise RestorationError('Сначала проверьте и подтвердите каждый бренд и адрес в блоке партнёрских ссылок.')
    if decision not in ('confirm','reject') or not isinstance(note,str) or not note.strip():
        raise RestorationError('Выберите результат и кратко укажите, что проверили или что нужно исправить.')
    build.seo_policy.setdefault('decisions',{})[identifier]={'fingerprint':report['fingerprint'],'decision':decision,
                        'note':note.strip()[:3000],'at':datetime.now(timezone.utc).isoformat(),'by':'owner'}
    build.approved_digest=None;build.save()
    return inspect_site(build)
