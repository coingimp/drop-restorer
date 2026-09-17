"""Offline checks of saved preview files AND WordPress export, never stale flags."""
from collections import Counter
from datetime import datetime, timezone
import json
import re
from urllib.parse import urljoin, urlsplit, unquote

from lxml import etree

from .cleaner import parse_html
from .cleanup_policy import JUNK, TRACKER, foreign_language, script_problem
from .models import RestorationError
from .review import issues, source_metadata
from .usage import summary as usage_summary

VERSION = 1
CATEGORIES = {'links': 'Ссылки', 'languages': 'Языковые версии', 'scripts': 'Скрипты и ресурсы',
              'junk': 'Реклама и служебные блоки', 'canonical': 'Канониклы',
              'navigation': 'Главное меню и казино', 'metadata': 'Метаданные', 'export': 'Файлы WordPress'}
SCOPE = ('Проверяются сохранённые HTML, ресурсы и экспорт WordPress. Языки проверяются по lang, hreflang '
         'и переключателям; язык каждой фразы автоматически не определяется. Поиск рекламы и загрузчиков '
         'использует известные признаки и не гарантирует обнаружение произвольного скрытого кода. '
         'Вид страниц и работу меню проверяйте в превью.')


def local_asset(root, value):
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith('/assets/'):
        return None
    path = root / 'theme' / unquote(parsed.path).lstrip('/')
    return path if path.resolve().is_relative_to((root / 'theme' / 'assets').resolve()) else None


def inspect(build):
    findings, rows = [], []

    def add(category, detail, route='', severity='error'):
        finding = {'category': category, 'detail': detail, 'route': route, 'severity': severity}
        if finding not in findings:
            findings.append(finding)

    routes = {page.route for page in build.pages}
    casinos = {page.route for page in build.pages if page.casino}
    if len(casinos) != 3 or len(routes) != len(build.request.menu_pages) + 4:
        add('navigation', 'Неверное число восстановленных страниц или страниц казино.')
    sources = {}
    try:
        sources = {row['local']: row['source'] for row in json.loads((build.root / 'asset-manifest.json').read_text(encoding='utf-8'))}
    except (OSError, ValueError, KeyError, TypeError):
        add('export', 'Нет читаемого списка происхождения ресурсов.')
    bad_scripts = {}
    for path in (build.root / 'theme').rglob('*'):
        if path.is_symlink():
            add('export', 'Ссылка на внешний файл: ' + path.relative_to(build.root).as_posix())
            continue
        if path.is_file() and path.suffix.lower() == '.js':
            asset = '/' + path.relative_to(build.root / 'theme').as_posix()
            reason = script_problem(path.read_text(encoding='utf-8', errors='replace'), sources.get(asset, ''))
            if reason:
                bad_scripts[asset] = reason
                add('scripts', reason + ': ' + asset)
        if path.is_file() and path.suffix.lower() == '.css':
            code = path.read_text(encoding='utf-8', errors='replace')
            for value in re.findall(r'''url\(\s*["']?([^\s)'";]+)''', code, re.I):
                if value.startswith(('http:', 'https:', '//')):
                    add('scripts', 'Внешний ресурс CSS: ' + path.name)
    expected_files = {page.key + '.html' for page in build.pages}
    actual_files = {path.name for path in (build.root / 'pages').glob('*.html')}
    if expected_files != actual_files:
        add('export', 'Набор HTML-файлов не совпадает с выбранными страницами.')

    export, site = {}, {}
    try:
        from .wordpress import NS
        tree = etree.parse(str(build.root / 'content.xml'), etree.XMLParser(resolve_entities=False, no_network=True))
        for item in tree.findall('./channel/item'):
            if item.findtext('wp:post_type', namespaces=NS) != 'page':
                continue
            meta = {node.findtext('wp:meta_key', namespaces=NS): node.findtext('wp:meta_value', namespaces=NS) or ''
                    for node in item.findall('wp:postmeta', NS)}
            key = meta.get('_dr_key')
            if key in export:
                add('export', 'Повтор страницы в файле импорта.')
            export[key] = (item.findtext('content:encoded', namespaces=NS) or '', meta)
        site = json.loads((build.root / 'theme' / 'site.json').read_text(encoding='utf-8'))
        if set(export) != {page.key for page in build.pages} or set(site['pages']) != set(export):
            add('export', 'Набор страниц WordPress не совпадает с превью.')
        if site.get('lang') != build.request.lang or site.get('origin') != build.request.origin:
            add('export', 'Язык или домен WordPress отличается от проекта.')
    except (OSError, ValueError, KeyError, TypeError, etree.XMLSyntaxError):
        add('export', 'Файлы WordPress отсутствуют или повреждены.')
    metadata_notes = issues(build, 'original' if build.metadata_decision == 'keep' else 'proposed') if build.metadata_review else {}
    for page in build.pages:
        try:
            soup = parse_html((build.root / 'pages' / (page.key + '.html')).read_text(encoding='utf-8'))
        except OSError:
            add('export', 'HTML страницы отсутствует.', page.route)
            continue
        row = {'route': page.route, 'title': page.title, 'casino': page.casino, 'links': 0,
               'lang': soup.html.get('lang', ''), 'canonical': '', 'metadata': source_metadata(soup)}
        rows.append(row)
        for anchor in soup.select('a[href]'):
            row['links'] += 1
            href = anchor['href'].strip()
            parsed = urlsplit(urljoin(build.request.origin + page.route, href))
            route = parsed.path + ('?' + parsed.query if parsed.query else '')
            if (parsed.scheme + '://' + parsed.netloc != build.request.origin or route not in routes
                    or anchor.get('ping') or anchor.get('onclick')):
                add('links', 'Посторонняя ссылка или обработчик: ' + href[:180], page.route)
        if row['lang'] != build.request.lang:
            add('languages', 'Неверный lang: ' + row['lang'], page.route)
        for node in soup.find_all(True):
            if node.get('hreflang') or foreign_language(node.get('lang') or node.get('xml:lang'), build.request.lang):
                add('languages', 'Сохранилась языковая версия или языковой атрибут.', page.route)
            if (node.name == 'link' and 'alternate' in node.get('rel', [])) or (node.name == 'meta' and node.get('property', '').lower() == 'og:locale:alternate'):
                add('languages', 'Сохранилась ссылка или метатег альтернативной версии.', page.route)
            if node.name == 'meta' and (node.get('property', '').lower() == 'og:locale' or node.get('http-equiv', '').lower() == 'content-language') and foreign_language(node.get('content'), build.request.lang):
                add('languages', 'Язык метатега отличается от выбранного.', page.route)
            classes = node.get('class', []) + [node.get('id', '')]
            if any(JUNK.fullmatch(value) for value in classes) or node.get('id', '').startswith(('wm-ipp', 'donato')):
                add('junk', 'Остался служебный блок: ' + ' '.join(classes), page.route)
            if node.name in ('iframe', 'object', 'embed', 'base'):
                add('junk', 'Остался элемент ' + node.name, page.route)
            if node.name == 'form' and (node.get('action') != '#' or node.get('onsubmit') != 'return false;'):
                add('links', 'Форма может отправлять данные.', page.route)
            if node.name == 'meta' and node.get('http-equiv', '').lower() == 'refresh':
                add('links', 'Автоматическое перенаправление страницы.', page.route)
            if node.name == 'script':
                source = node.get('src', '')
                reason = bad_scripts.get(source) or script_problem(node.get_text(), sources.get(source, source))
                if reason:
                    add('scripts', reason, page.route)
            for attr, value in node.attrs.items():
                if attr.startswith('on') and script_problem(str(value)):
                    add('scripts', 'Загрузчик или отслеживание в обработчике события.', page.route)
            resources = [node.get(attr) for attr in ('src', 'poster', 'background', 'data-src', 'data-lazy-src') if node.get(attr)]
            if node.name == 'link' and set(node.get('rel', [])) - {'canonical', 'alternate'}:
                resources.append(node.get('href', ''))
            for value in resources:
                if value.startswith(('data:', '#')):
                    continue
                path = local_asset(build.root, value)
                if path is None or not path.is_file():
                    add('scripts', 'Внешний или отсутствующий ресурс: ' + value[:180], page.route)
            if node.get('style') and re.search(r'''url\(\s*["']?(?:https?:)?//''', node['style'], re.I):
                add('scripts', 'Внешний ресурс во встроенном стиле.', page.route)
        canonical = soup.select('link[rel~="canonical"]')
        row['canonical'] = canonical[0].get('href', '') if canonical else ''
        if len(canonical) != 1 or row['canonical'] != build.request.origin + page.route:
            add('canonical', 'Нужен один каноникл на финальный адрес страницы.', page.route)
        if (len(soup.select('#dr-primary-menu')) != 1 or
                {node['href'] for node in soup.select('#dr-primary-menu a[href]')} != routes or
                {node['href'] for node in soup.select('#dr-casino-menu a[href]')} != casinos):
            add('navigation', 'Главное меню или три ссылки казино не соответствуют набору страниц.', page.route)
        if page.casino:
            if soup.title or soup.select('meta[name="description" i]'):
                add('metadata', 'Метаданные казино должен задавать владелец.', page.route)
            slot = soup.select_one('#dr-editor-content')
            if slot is None or slot.decode_contents().strip():
                add('export', 'Область редактирования казино должна быть пустой.', page.route)
        elif row['metadata'] != {'title': page.seo_title, 'description': page.description}:
            add('metadata', 'Метаданные отличаются от сохранённого выбора.', page.route)
        for note in metadata_notes.get(page.key, []):
            add('metadata', note + (' — сохранено по вашему выбору.' if build.metadata_decision == 'keep' else ''), page.route, 'warning')
        content, meta = export.get(page.key, ('', {}))
        entry = site.get('pages', {}).get(page.key, {})
        for node in list(soup.select('title, link[rel~="canonical"], meta[name="description" i]')):
            node.decompose()
        if page.casino and build.seo_policy:
            for node in list(soup.select('meta[name="robots" i]')):
                node.decompose()
        if page.casino and site.get('article_formatting'):
            for node in list(soup.select('link[href="/assets/dr-article.css"]')):
                node.decompose()
        if (entry.get('head') != soup.head.decode_contents() or entry.get('body_attrs') != dict(soup.body.attrs)
                or entry.get('route') != page.route or meta.get('_dr_route') != page.route):
            add('export', 'Каркас WordPress отличается от проверяемой страницы.', page.route)
        if not page.casino:
            if (content != soup.body.decode_contents() or meta.get('_dr_seo_title') != page.seo_title
                    or meta.get('_dr_description') != page.description):
                add('export', 'Контент или метаданные импорта отличаются от превью.', page.route)
        else:
            slot = soup.select_one('#dr-editor-content')
            if slot is not None:
                if site.get('article_formatting'):
                    slot['class'] = list(dict.fromkeys(slot.get('class', []) + ['dr-article']))
                    heading = slot.find_previous_sibling('h1')
                    if heading is not None:
                        heading.replace_with('DR_ARTICLE_HEADING_SLOT')
                slot.string = 'DR_EDITOR_CONTENT_SLOT'
            path = build.root / 'theme' / 'views' / (page.key + '.html')
            if (content or '_dr_seo_title' in meta or '_dr_description' in meta or not path.exists()
                    or path.read_text(encoding='utf-8') != soup.body.decode_contents()):
                add('export', 'Шаблон казино или импорт отличается от превью.', page.route)
    errors = sum(f['severity'] == 'error' for f in findings)
    warnings = sum(f['severity'] == 'warning' for f in findings)
    for row in rows:
        row['checks'] = {key: sum(f['severity'] == 'error' and f['category'] == key and f['route'] == row['route'] for f in findings)
                         for key in CATEGORIES}
    return {'version': VERSION, 'checked_at': datetime.now(timezone.utc).isoformat(), 'passed': errors == 0,
            'errors': errors, 'warnings': warnings, 'scope': SCOPE, 'pages': rows, 'findings': findings,
            'removed': build.cleanup, 'removal_counts': dict(sum((Counter({row['kind']: row.get('count', 1)}) for row in build.cleanup), Counter())),
            'usage': usage_summary(build.root)}


def audit(build):
    report = inspect(build)
    report['digest'] = build.digest()
    path = build.root / 'checks.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return report


def require_pass(build):
    report = audit(build)
    if not report['passed']:
        first = next(f for f in report['findings'] if f['severity'] == 'error')
        raise RestorationError(f'Проверки не пройдены: {report["errors"]}. ' + first['route'] + ' ' + first['detail']
                               + ' Откройте «Проверки и расходы» для подробностей.')
    return report
