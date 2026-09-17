"""Explicit URL plan shared by preview auditing and the generated WP manifest."""
from collections import Counter
import re
from urllib.parse import urlsplit, unquote, quote

from .cleaner import parse_html, set_seo
from .models import RestorationError, latin_url_path
from .seo_contract import default_policy, save_json
from .indexation import open_indexation
from .casino_layout import apply_to_soup


def canonical_route(route, style):
    path = urlsplit(route).path
    if path == '/':
        return '/'
    if re.search(r'/index\.(?:php|html?|asp|aspx)$', path, re.I):
        path = path.rsplit('/', 1)[0] or '/'
    if path == '/':
        return '/'
    # File-shaped selected URLs remain file URLs; directory URLs use one style.
    if re.search(r'/[^/]+\.[a-zA-Z0-9]{1,6}$', path.rstrip('/')):
        return path.rstrip('/')
    return path.rstrip('/') + ('/' if style == 'slash' else '')


def route_plan(build):
    return {page.key: page.route for page in build.pages}


def pending_casino(build, page):
    # Compatibility for older builds: review must never close a public page.
    return False


def indexable(build, page):
    return True


def prepare(build):
    if not build.seo_policy:
        build.seo_policy = default_policy()
    build.seo_policy['casino_pending'] = 'index'
    style = build.seo_policy.get('url_style', 'slash')
    if style not in ('slash', 'no_slash'):
        raise RestorationError('Выберите структуру URL со слешем или без слеша.')
    originals = build.seo_policy.setdefault('original_routes', {p.key:p.route for p in build.pages})
    mapping = {}
    for page in build.pages:
        original = originals.get(page.key, page.route)
        override = build.seo_policy.get('url_overrides', {}).get(page.key)
        # Distinct query-selected pages require an owner-selected readable path.
        target = override or (original if '?' in original else canonical_route(original, style))
        if not target.startswith('/') or target.startswith('//') or '?' in target and override:
            raise RestorationError('Новый URL должен быть локальным путём без параметров.')
        if '\\' in target or any(x in ('.', '..') for x in unquote(target).split('/')):
            raise RestorationError('Некорректный путь страницы.')
        if override:
            target = canonical_route(quote(unquote(target), safe='/-._~'), style)
        target = latin_url_path(target)
        decoded_path = unquote(urlsplit(target).path).lower()
        if (re.match(r'^/(?:go|assets|wp-admin|wp-json|wp-content|wp-includes)(?:/|$)', decoded_path)
                or decoded_path in ('/robots.txt','/sitemap.xml','/wp-login.php','/wp-sitemap.xml')
                or '#' in target or re.search(r'[\x00-\x20\x7f]', target)):
            raise RestorationError('Этот адрес зарезервирован для служебного раздела.')
        mapping[page.route] = target
        mapping[original] = target
    targets = [mapping[p.route] for p in build.pages]
    if len({route_key(target) for target in targets}) != len(targets):
        raise RestorationError('URL после нормализации совпадают. Задайте отдельные понятные адреса страниц.')
    redirects = {}
    for old, new in mapping.items():
        if route_key(old) != route_key(new):
            redirects[old] = new
    link_mapping = {route_key(old): new for old, new in mapping.items()}
    for page in build.pages:
        page.route = mapping[page.route]
        path = page.route.split('?')[0]
        if path != '/' and '?' not in page.route:
            redirects[path.rstrip('/') if path.endswith('/') else path + '/'] = page.route
        if '?' not in page.route and (path == '/' or not re.search(r'\.[a-zA-Z0-9]{1,6}$', path)):
            for suffix in ('index.php', 'index.html', 'index.htm'):
                redirects[path.rstrip('/') + '/' + suffix] = page.route
        soup = parse_html(page.html)
        for link in soup.select('a[href]'):
            value = link['href']
            local = value[len(build.request.origin):] if value.startswith(build.request.origin + '/') else value
            local_path, fragment_separator, fragment = local.partition('#')
            if route_key(local_path) in link_mapping:
                link['href'] = link_mapping[route_key(local_path)] + fragment_separator + fragment
        set_seo(soup, page, build.request.origin, preserve_metadata=True)
        # A logo is not the page heading. Only this unambiguous structural repair
        # is automatic; content H1 conflicts remain explicit failures.
        for node in soup.select('h1#logo, h1.logo'):
            node.name = 'div'
        open_indexation(soup)
        if page.casino:
            apply_to_soup(soup, build.seo_policy)
        page.html = str(soup)
    build.seo_policy['redirects'] = redirects
    build.approved_digest = None
    build.status = 'site_review'
    write_staging(build)


def write_staging(build):
    build.seo_policy['casino_pending'] = 'index'
    (build.root / 'pages').mkdir(exist_ok=True)
    from .wordpress import write_theme_assets
    write_theme_assets(build.root / 'theme')
    for page in build.pages:
        soup = open_indexation(parse_html(page.html))
        if page.casino:
            apply_to_soup(soup, build.seo_policy)
        page.html = str(soup)
        (build.root / 'pages' / (page.key + '.html')).write_text(page.html, encoding='utf-8')
    build.save()


def route_key(target):
    """Compare URI path encodings once; preserve query-selected page identity."""
    path, separator, query = target.partition('?')
    return unquote(path), separator, query


def resolve(build, target):
    """Return the intended status/route, never accept client-supplied targets."""
    redirects = {route_key(source): destination for source, destination in build.seo_policy.get('redirects', {}).items()}
    key = route_key(target)
    if key in redirects:
        return 301, redirects[key]
    routes = {route_key(p.route): p.route for p in build.pages}
    if key in routes:
        return 200, routes[key]
    path = target.split('?')[0]
    if '?' in target:
        if route_key(path) in routes:
            return 301, routes[route_key(path)]
        if route_key(path) in redirects:
            return 301, redirects[route_key(path)]
    return 404, ''


def robots(build):
    return 'User-agent: *\nAllow: /\nSitemap: ' + build.request.origin + '/sitemap.xml\n'


def sitemap(build):
    from xml.sax.saxutils import escape
    return '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(
        '<url><loc>' + escape(build.request.origin + page.route) + '</loc></url>' for page in build.pages if indexable(build, page)) + '</urlset>'
