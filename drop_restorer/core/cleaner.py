from __future__ import annotations

import re
from copy import deepcopy
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup, Comment

from .assets import TRACKER, original_url
from .models import Page, RestoreRequest, RestorationError, route_for
from .cleanup_policy import JUNK, TRACKER_CODE, foreign_language, script_problem


def _numeric_dimension(value):
    match = re.search(r'\d+', str(value or ''))
    return int(match.group()) if match else 0


def _flash_menu_token(node):
    embed = node.find('embed')
    movie = node.find('param', attrs={'name': re.compile(r'^movie$', re.I)})
    source = (movie.get('value', '') if movie else '') or (embed.get('src', '') if embed else '')
    if not re.search(r'\.swf(?:[?#]|$)', source, re.I):
        return ''
    raw = node.get('id', '') or (embed.get('name', '') if embed else '')
    if not raw:
        match = re.search(r'([^/\\?#]+)\.swf(?:[?#]|$)', source, re.I)
        raw = match.group(1) if match else ''
    return re.sub(r'[^a-z0-9_-]+', '-', raw.casefold()).strip('-')


def preserve_flash_menu_slot(soup):
    """Keep the location of compact Flash navigation before embeds are removed.

    Early table-based sites often rendered a vertical menu as several narrow
    SWF buttons.  Flash itself must be removed, but losing the slot makes the
    later navigation pass create an unrelated horizontal fallback above the
    whole page.  A hidden marker preserves the exact insertion point.
    """
    if soup.select_one('[data-dr-flash-menu-slot]'):
        return
    groups = {}
    for node in soup.find_all('object'):
        token = _flash_menu_token(node)
        if not token:
            continue
        host = node.find_parent(['td', 'aside', 'div', 'section'])
        if host is None:
            continue
        embed = node.find('embed')
        width = _numeric_dimension(node.get('width') or (embed.get('width') if embed else ''))
        height = _numeric_dimension(node.get('height') or (embed.get('height') if embed else ''))
        groups.setdefault(id(host), {'host': host, 'items': []})['items'].append((node, token, width, height))
    candidates = []
    for order, row in enumerate(groups.values()):
        host, items = row['host'], row['items']
        tokens = list(dict.fromkeys(item[1] for item in items))
        widths = [item[2] for item in items if item[2]]
        heights = [item[3] for item in items if item[3]]
        host_width = _numeric_dimension(host.get('width'))
        names = ' '.join([host.get('id', ''), *host.get('class', [])])
        narrow_sidebar = host.name == 'td' and 80 <= (host_width or max(widths, default=0)) <= 360
        named_menu = bool(re.search(r'(?:menu|nav|sidebar)', names, re.I))
        compact_buttons = widths and heights and max(widths) <= 360 and max(heights) <= 80
        if len(tokens) < 3 or not compact_buttons or not (narrow_sidebar or named_menu):
            continue
        candidates.append((len(tokens) * 20 + narrow_sidebar * 30 + named_menu * 15, -order, items, host, tokens))
    if not candidates:
        return
    _, _, items, host, tokens = max(candidates, key=lambda row: row[:2])
    slot = soup.new_tag('span', attrs={'data-dr-flash-menu-slot': ' '.join(tokens), 'hidden': 'hidden'})
    items[0][0].insert_before(slot)
    host['class'] = list(dict.fromkeys([*host.get('class', []), 'dr-legacy-flash-menu-host']))




def parse_html(raw: str | bytes) -> BeautifulSoup:
    # Normalize line endings once, before HTML is saved on Windows or embedded
    # in XML. Otherwise CRLF can become CRCRLF and change the exported content.
    raw = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n') if isinstance(raw, bytes) else raw.replace('\r\n', '\n').replace('\r', '\n')
    soup = BeautifulSoup(raw, 'lxml')
    if not soup.html:
        shell = BeautifulSoup('<!doctype html><html><head></head><body></body></html>', 'lxml')
        for node in list(soup.contents):
            shell.body.append(node.extract())
        soup = shell
    if not soup.head:
        soup.html.insert(0, soup.new_tag('head'))
    if not soup.body:
        soup.html.append(soup.new_tag('body'))
    return soup


def clean(soup: BeautifulSoup, lang: str, removed=None):
    removed = removed if removed is not None else []
    preserve_flash_menu_slot(soup)
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        if any(x in comment.lower() for x in ('wayback', 'archive.org', 'begin wayback', 'end wayback')):
            comment.extract()
    for node in list(soup.find_all(True)):
        if not node.name:
            continue
        classes = node.get('class', []) + [node.get('id', '')]
        if any(JUNK.fullmatch(x) for x in classes) or node.get('id', '').startswith(('wm-ipp', 'donato')):
            removed.append({'kind': 'junk', 'detail': ' '.join(classes)})
            node.decompose()
            continue
        if node.name in ('iframe', 'object', 'embed'):
            removed.append({'kind': 'embed', 'detail': node.name})
            node.decompose()
            continue
        if node.name == 'img' and not any(node.get(attr) for attr in ('src', 'srcset', 'data-src')):
            removed.append({'kind': 'asset', 'detail': 'Пустое изображение без источника'})
            node.decompose()
            continue
        if node.name == 'img':
            width_value, height_value = str(node.get('width', '')).strip(), str(node.get('height', '')).strip()
            if (re.fullmatch(r'\d+(?:px)?', width_value, re.I) and
                    re.fullmatch(r'\d+(?:px)?', height_value, re.I)):
                width, height = _numeric_dimension(width_value), _numeric_dimension(height_value)
                style = node.get('style', '').strip().rstrip(';')
                if width and height and not re.search(r'(?:^|;)\s*aspect-ratio\s*:', style, re.I):
                    node['style'] = (style + ';' if style else '') + f'aspect-ratio:{width}/{height}'
        reason = script_problem(node.get_text(), node.get('src', '')) if node.name == 'script' else ''
        if reason:
            removed.append({'kind': 'script', 'detail': reason})
            node.decompose()
            continue
        # A root language tag can be stale; only remove explicitly marked
        # alternate content blocks, never the document or its main containers.
        if node.name not in ('html', 'body', 'head') and foreign_language(node.get('lang') or node.get('xml:lang'), lang):
            removed.append({'kind': 'language', 'detail': node.get('lang') or node.get('xml:lang')})
            node.decompose()
            continue
        if node.get('hreflang') or (node.name == 'meta' and node.get('http-equiv', '').lower() in ('refresh', 'content-security-policy')):
            removed.append({'kind': 'language' if node.get('hreflang') else 'junk', 'detail': 'Удалена служебная ссылка или метатег'})
            node.decompose()
            continue
        if ((node.name == 'link' and 'alternate' in node.get('rel', [])) or
                (node.name == 'meta' and node.get('property', '').lower() == 'og:locale:alternate')):
            removed.append({'kind': 'language', 'detail': 'Удалена альтернативная версия'})
            node.decompose()
            continue
        if node.name == 'meta' and (node.get('property', '').lower() == 'og:locale' or node.get('http-equiv', '').lower() == 'content-language'):
            node['content'] = lang.replace('-', '_') if node.get('property', '').lower() == 'og:locale' else lang
        if node.name == 'meta' and (node.get('charset') or node.get('http-equiv', '').lower() == 'content-type'):
            node.decompose()
            continue
        for attr in list(node.attrs):
            if attr.startswith('on') and TRACKER_CODE.search(str(node[attr])):
                del node[attr]
            if attr in ('lang', 'xml:lang'):
                del node[attr]
    soup.html['lang'] = lang
    soup.head.insert(0, soup.new_tag('meta', charset='utf-8'))
    if not soup.find('meta', attrs={'name': 'viewport'}):
        soup.head.append(soup.new_tag('meta', attrs={'name': 'viewport', 'content': 'width=device-width, initial-scale=1'}))
    for form in soup.find_all('form'):
        form['action'] = '#'
        form['onsubmit'] = 'return false;'
        form.attrs.pop('method', None)


def clean_links(soup, base: str, route_map: dict[str, str]):
    for node in list(soup.find_all('a')):
        href = node.get('href', '')
        if href.startswith('#') and (node.get('aria-controls') or node.find_parent('nav') or node.find_parent(class_=re.compile('menu|nav', re.I))):
            continue
        target = original_url(href, base)
        parsed = urlsplit(target)
        key = (parsed.hostname or '').removeprefix('www.') + route_for(target) if parsed.scheme in ('http', 'https') else ''
        if key in route_map:
            node['href'] = route_map[key] + (('#' + parsed.fragment) if parsed.fragment else '')
            node.attrs.pop('target', None)
            node.attrs.pop('onclick', None)
            node.attrs.pop('ping', None)
        else:
            # Keep visual wrappers for cards/images; remove the link itself.
            node.name = 'span'
            for attr in ('href', 'target', 'ping', 'onclick', 'download'):
                node.attrs.pop(attr, None)
            node['class'] = list(node.get('class', [])) + ['dr-unlinked']


def deactivate_bottom_menu_links(soup: BeautifulSoup) -> int:
    """Remove anchors from footer and explicitly named bottom-menu regions.

    Restored archives often contain a second, decorative menu at the bottom of
    the page.  It is not the editable primary menu and its destinations are
    frequently stale or point back to the donor site.  Keep its visible text
    and images, but unwrap every ``<a>`` so the exported page cannot present
    those entries as clickable navigation.  The primary menu is rebuilt by
    :func:`navigation` separately and is therefore unaffected.

    Return the number of unwrapped anchors so callers can add an auditable
    cleanup record to the build log.
    """
    hosts = []
    for node in soup.find_all(True):
        if node.name == 'footer' or str(node.get('role', '')).casefold() == 'contentinfo':
            hosts.append(node)
            continue
        token = ' '.join([str(node.get('id', '')), *[str(item) for item in node.get('class', [])]])
        if re.search(r'(?:^|[-_ ])(?:menu|nav)[-_ ]*bottom(?:$|[-_ ])|(?:^|[-_ ])bottom[-_ ]*(?:menu|nav)(?:$|[-_ ])|menubottom', token, re.I):
            hosts.append(node)
    removed = 0
    seen = set()
    for host in hosts:
        if id(host) in seen:
            continue
        seen.add(id(host))
        for anchor in list(host.select('a')):
            # ``unwrap`` preserves nested images, spans and text while
            # removing href/target/onclick and the clickable element itself.
            anchor.unwrap()
            removed += 1
    return removed


def _navigation_route(href: str) -> str:
    """Normalize an archive menu href to the route used by ``Page`` objects.

    Legacy menus commonly use relative ``index.html``/``./kontakt.html``
    links.  ``clean_links`` normally normalizes these before this function is
    called, but collecting labels before the old menu is removed also needs to
    understand the raw form.
    """
    value = str(href or '').strip().split('#', 1)[0]
    if not value or value.startswith(('javascript:', 'mailto:', 'tel:')):
        return ''
    parsed = urlsplit(value)
    path = unquote(parsed.path or '/')
    if not path.startswith('/'):
        path = '/' + path.lstrip('./')
    if re.search(r'/index(?:\.html?|\.php)?$', path, re.I):
        path = '/'
    path = quote(path, safe="/!$&'()*+,;=:@-._~")
    return path + (('?' + parsed.query) if parsed.query else '')


def navigation_labels(soup, pages: list[Page]) -> dict[str, str]:
    """Collect visible labels from the donor's existing menu links.

    A lot of archived sites use one document title for every page.  Using
    that title as the menu label hides the fact that the selected archive
    pages are different routes.  Keep the first useful donor label for each
    selected route before the legacy menu is cleared or replaced.
    """
    routes = {page.route for page in pages if not page.casino}
    labels: dict[str, str] = {}
    for anchor in soup.select('a[href]'):
        route = _navigation_route(anchor.get('href', ''))
        if route not in routes or route in labels:
            continue
        text = anchor.get_text(' ', strip=True)
        if not text or len(text) > 120 or text in ('...', '…'):
            continue
        labels[route] = text
    return labels


def _neutralize_wix_menu_host(primary):
    """Turn a downloaded Wix menu host into an ordinary visible container.

    Wix's ``wix-dropdown-menu`` custom element hides itself until its original
    generated child IDs are present and the client-side menu has completed its
    layout pass.  The restored menu intentionally replaces those generated
    children with a small deterministic ``nav``; leaving the custom element in
    place therefore makes the menu disappear even though the links are present
    in the HTML.  Replace only the nearest Wix menu host and keep its position
    in the header so the recovered navigation remains in the main menu area.
    """
    if primary is None:
        return None
    host = primary.find_parent(
        lambda node: getattr(node, 'name', None) == 'wix-dropdown-menu'
        or any(token in ' '.join(node.get('class', [])).casefold()
               for token in ('wix-dropdown-menu', 'wixui-dropdown-menu', 'hidden-during-prewarmup'))
    )
    if host is None:
        return None
    # A custom-element name is enough to trigger Wix's visibility lifecycle;
    # use a neutral div and discard only Wix runtime attributes/classes.
    host.name = 'div'
    host['class'] = ['dr-menu-host']
    for attr in list(host.attrs):
        if attr == 'id':
            continue
        if attr.startswith('data-') or attr in ('hidden', 'tabindex', 'aria-hidden', 'role', 'style'):
            host.attrs.pop(attr, None)
    return host


def navigation(soup, pages: list[Page], request: RestoreRequest, label_overrides: dict[str, str] | None = None):
    # A site's primary navigation is distinct from sidebars, mega-menu columns
    # and footer lists. Never populate every list with the whole site.
    from .primary_navigation import (HEADER, legacy_dynamic_primary, legacy_flash_primary,
                                     legacy_sidebar_target, legacy_table_primary, select_primary,
                                     secondary_context)
    # Capture donor labels before legacy table/Flash placeholders are cleared.
    # ``label_overrides`` is used by the exporter when a checkpoint already
    # contains a generated menu and the raw source is still available.
    donor_labels = navigation_labels(soup, pages)
    if label_overrides:
        donor_labels.update({route: text for route, text in label_overrides.items() if text})
    flash_primary, flash_slot, flash_host, flash_order = legacy_flash_primary(soup)
    dynamic_primary, dynamic_donor = legacy_dynamic_primary(soup)
    primary = flash_primary or select_primary(soup)
    legacy_menu, legacy_host = legacy_table_primary(soup)
    # Dreamweaver-era image navigation has no semantic menu for
    # ``select_primary`` to recognise. Replace the obsolete button table in
    # its original strip below the banner so the generated menu does not jump
    # to the top of the document and create a duplicate header.
    if (primary is None or primary.get('data-dr-placement') == 'header-fallback') and legacy_menu is not None:
        if primary is not None and primary is not legacy_menu:
            primary.decompose()
        primary = legacy_menu
    elif (primary is None or primary.get('data-dr-placement') == 'header-fallback') and legacy_host is not None:
        if primary is not None:
            primary.decompose()
        primary = soup.new_tag('nav', attrs={'aria-label': 'Main navigation',
                                             'data-dr-placement': 'legacy-table-menu',
                                             'data-dr-layout': 'legacy-table'})
        legacy_host.clear()
        legacy_host.append(primary)
        shell_tables = [node for node in legacy_host.parents if getattr(node, 'name', None) == 'table']
        shell_table = shell_tables[-1] if shell_tables else None
        if shell_table is not None:
            for table in shell_table.find_all('table'):
                table['class'] = list(dict.fromkeys([*table.get('class', []), 'dr-legacy-layout-table']))
            shell_table['class'] = list(dict.fromkeys([*shell_table.get('class', []), 'dr-legacy-width-table']))
        soup.body['class'] = list(dict.fromkeys([*soup.body.get('class', []), 'dr-legacy-table-layout']))
    if flash_host is not None and flash_primary is None:
        primary = soup.new_tag('nav', attrs={'aria-label': 'Main navigation',
                                             'data-dr-placement': 'legacy-flash-menu',
                                             'data-dr-layout': 'legacy-dynamic'})
        if flash_order:
            primary['data-dr-flash-items'] = ' '.join(flash_order)
        if flash_slot is not None:
            flash_slot.replace_with(primary)
        else:
            spacer = next((node for node in flash_host.find_all('img', recursive=False)
                           if 80 <= _numeric_dimension(node.get('width')) <= 360
                           and 1 <= _numeric_dimension(node.get('height')) <= 40), None)
            if spacer is not None:
                spacer.insert_after(primary)
            else:
                flash_host.insert(0, primary)
    # Repair an earlier misplaced generated menu without touching sidebar content.
    for old in list(soup.select('.dr-navigation')):
        if old is not primary and (primary is None or old not in primary.parents):
            old.decompose()
    if primary is None:
        primary = soup.new_tag('nav', attrs={'aria-label': 'Main navigation', 'data-dr-placement': 'header-fallback'})
        header = next((node for node in soup.select(HEADER) if not secondary_context(node)
                       and not node.find_parent(['main', 'article'])), None)
        if header:
            header.insert_after(primary)
        else:
            soup.body.insert(0, primary)
    elif primary.name in ('ul', 'ol'):
        source_id = primary.get('id', '')
        source_classes = list(primary.get('class', []))
        attrs = {'aria-label': 'Navigation', 'data-dr-placement': 'legacy-menu'}
        if source_id:
            attrs['id'] = source_id
        if source_classes:
            attrs['class'] = source_classes
        if source_id.casefold() == 'menu':
            attrs['data-dr-layout'] = 'legacy-vertical'
        container = soup.new_tag('nav', attrs=attrs)
        primary.wrap(container)
        primary = container
    # Old fixed-width shells use ``#topmenu`` as a source marker even though
    # the usable navigation belongs in the left column beside ``#content``.
    # Move the existing node instead of styling it with absolute offsets; this
    # keeps the visual and accessibility order correct on every page type.
    sidebar = legacy_sidebar_target(soup, primary)
    if sidebar is not None and primary not in sidebar.descendants:
        primary.extract()
        sidebar.insert(0, primary)
        primary['data-dr-layout'] = 'legacy-sidebar'
        primary['data-dr-placement'] = 'legacy-sidebar'
    labels = {}
    label_source = dynamic_donor if primary is dynamic_primary and dynamic_donor is not None else primary
    for anchor in label_source.select('a[href]'):
        route = anchor['href'].split('#')[0]
        if route not in labels:
            labels[route] = anchor.get_text(' ', strip=True)
    home = {'cs': 'Úvod', 'en': 'Home', 'ru': 'Главная', 'de': 'Startseite', 'it': 'Home', 'fr': 'Accueil'}.get(request.lang.split('-')[0], 'Home')
    flash_layout = flash_host is not None or primary.get('data-dr-placement') == 'legacy-flash-menu'
    if flash_layout:
        order = {token: index for index, token in enumerate(flash_order)}
        def flash_rank(page):
            if page.route == '/':
                matches = ('uvod', 'home', 'index', 'start')
            else:
                stem = page.route.rstrip('/').rsplit('/', 1)[-1].rsplit('.', 1)[0].casefold()
                matches = (re.sub(r'[^a-z0-9_-]+', '-', stem).strip('-'),)
            return min((order[token] for token in matches if token in order), default=len(order) + pages.index(page))
        regular_pages = [page for page in pages if not page.casino]
        if flash_order:
            regular_pages.sort(key=flash_rank)
        for page in regular_pages:
            if page.route == '/':
                labels[page.route] = home
                continue
            compact = re.fullmatch(r'[^()]{1,80}\(([^()]{2,100})\)\s*', page.title.strip())
            labels[page.route] = compact.group(1).strip() if compact else page.title
    else:
        regular_pages = [page for page in pages if not page.casino]
    # Prefer a real label from the archived menu when the selected node had
    # no links of its own, or when the only label it supplied is the repeated
    # document title.  This keeps entries such as Öffnungszeiten, Kontakt and
    # Impressum distinct even when every archive page shares one <title>.
    for page in regular_pages:
        candidate = donor_labels.get(page.route, '').strip()
        current = labels.get(page.route, '').strip()
        if candidate and (not current or current.casefold() == page.title.strip().casefold()):
            labels[page.route] = candidate
    if primary.get('data-dr-layout') == 'legacy-vertical':
        soup.body['class'] = list(dict.fromkeys([*soup.body.get('class', []), 'dr-legacy-vertical-navigation']))
    if primary.get('data-dr-layout') == 'legacy-sidebar':
        soup.body['class'] = list(dict.fromkeys([*soup.body.get('class', []), 'dr-legacy-sidebar-navigation']))
    if primary.get('data-dr-layout') == 'legacy-table':
        soup.body['class'] = list(dict.fromkeys([*soup.body.get('class', []), 'dr-legacy-table-layout']))
    if primary is dynamic_primary or primary.get('data-dr-layout') == 'legacy-dynamic':
        primary['data-dr-layout'] = 'legacy-dynamic'
        soup.body['class'] = list(dict.fromkeys([*soup.body.get('class', []),
                                                 'dr-legacy-dynamic-navigation', 'dr-legacy-table-layout']))
        soup.body.attrs.pop('onload', None)
        for placeholder in list(soup.select('[class*="infobarframebox_"][id$="_placeholder"]')):
            placeholder.decompose()
        primary_tables = [node for node in primary.parents if getattr(node, 'name', None) == 'table']
        shell_table = primary_tables[-1] if primary_tables else None
        for table in soup.find_all('table'):
            names = ' '.join([table.get('id', ''), *table.get('class', [])])
            if table in primary.parents or re.search(r'_table_0?[1-5](?:\b|_)', names, re.I):
                table['class'] = list(dict.fromkeys([*table.get('class', []), 'dr-legacy-layout-table']))
            # A Flash-era shell often has several sibling 700-1000px tables:
            # header art, banner slices and the row that owns sidebar/content.
            # The menu table is stacked on mobile; its sibling tables only need
            # their fixed desktop widths constrained to the viewport.
            if shell_table is not None and (table is shell_table or table in shell_table.descendants):
                table['class'] = list(dict.fromkeys([*table.get('class', []), 'dr-legacy-width-table']))
    _neutralize_wix_menu_host(primary)
    primary.clear()
    primary['class'] = list(dict.fromkeys([*primary.get('class', []), 'dr-navigation']))
    primary['aria-label'] = 'Main navigation'
    for header in soup.select(HEADER):
        if header in primary.parents:
            header['class'] = list(dict.fromkeys([*header.get('class', []), 'dr-navigation-header']))
    toggle = soup.new_tag('button', attrs={'type': 'button', 'class': 'dr-mobile-toggle', 'aria-label': 'Menu', 'aria-expanded': 'false', 'aria-controls': 'dr-primary-menu'})
    toggle.string = '☰  Menu'
    menu = soup.new_tag('ul', id='dr-primary-menu', attrs={'class': 'dr-menu'})
    menu.append(Comment('DR_MENU_START'))
    for page in regular_pages:
        li, anchor = soup.new_tag('li'), soup.new_tag('a', href=page.route)
        anchor.string = home if page.route == '/' else labels.get(page.route) or page.title
        li.append(anchor)
        menu.append(li)
    li = soup.new_tag('li', attrs={'class': 'dr-casino'})
    button = soup.new_tag('button', attrs={'type': 'button', 'aria-expanded': 'false', 'aria-controls': 'dr-casino-menu', 'class': 'dr-casino-toggle'})
    button.string = request.casino_label
    submenu = soup.new_tag('ul', id='dr-casino-menu', attrs={'class': 'dr-submenu'})
    for page in pages:
        if page.casino:
            child, anchor = soup.new_tag('li'), soup.new_tag('a', href=page.route)
            anchor.string = page.title
            child.append(anchor)
            submenu.append(child)
    li.extend([button, submenu])
    menu.append(li)
    menu.append(Comment('DR_MENU_END'))
    primary.extend([toggle, menu])
    if dynamic_donor is not None and dynamic_donor is not primary and primary not in dynamic_donor.descendants:
        dynamic_donor.decompose()
    for node in list(soup.select('link[href="/assets/drop-restorer.css"], script[src="/assets/drop-restorer.js"], link[href="/assets/site-navigation.css"], script[src="/assets/site-navigation.js"]')):
        node.decompose()
    for path, kind in [('/assets/site-navigation.css', 'css'), ('/assets/site-navigation.js', 'js')]:
        if kind == 'css':
            soup.head.append(soup.new_tag('link', rel='stylesheet', href=path))
        else:
            soup.body.append(soup.new_tag('script', src=path, defer='defer'))


def set_seo(soup, page: Page, origin: str, preserve_metadata: bool = False):
    selector = 'link[rel~="canonical"]' if preserve_metadata and not page.casino else 'link[rel~="canonical"], meta[name="description" i], title'
    for node in list(soup.select(selector)):
        node.decompose()
    soup.head.append(soup.new_tag('link', rel='canonical', href=origin + page.route))
    if not page.casino and not preserve_metadata:
        title = soup.new_tag('title')
        title.string = page.seo_title
        soup.head.append(title)
        soup.head.append(soup.new_tag('meta', attrs={'name': 'description', 'content': page.description}))


SHELL_REGIONS = {
    'header': ('header, [role="banner"], #ja-header, #header-main, #masthead, #header, #site-header', 'шапка'),
    'navigation': ('.dr-navigation, #ja-mainnav, #primary-nav, #main-nav, #menu, header nav, [role="navigation"]', 'главная навигация'),
    'footer': ('footer, [role="contentinfo"], #ja-footer, #footer, #site-footer', 'подвал'),
}


def casino_shell_issues(source, soup):
    """An empty editor is intentional; losing the surrounding site is not."""
    issues = []
    for key, (selector, label) in SHELL_REGIONS.items():
        if source.select_one(selector) is not None and soup.select_one(selector) is None:
            issues.append((key, 'При создании казино-страницы потеряна область сайта: ' + label + '.'))
    if len(soup.select('.dr-casino-content')) != 1 or len(soup.select('#dr-editor-content')) != 1:
        issues.append(('content', 'Нужна одна область статьи казино и один контейнер редактора.'))
    casino_content = soup.select_one('.dr-casino-content')
    if casino_content is not None:
        detected, _ = _casino_content_region(soup)
        if detected is not None and detected is not casino_content:
            issues.append(('content', 'Основное содержимое исходной страницы осталось вне казино-страницы; главная или лента записей не заменена.'))
    return issues


CASINO_CONTENT_SELECTORS = (
    '#main-article', '#ja-content', 'main', '[role="main"]', '#text2', '#text',
    '#main-content', '#content-main', '#dle-content', '#content', '#main',
    '#mainContent', '#contentarea', '.site-content', 'article',
)


def _casino_content_region(soup, primary=None):
    """Find the page body without mistaking article headings for site chrome."""
    from .primary_navigation import select_primary
    primary = primary if primary is not None else select_primary(soup)
    main = None
    protected_shell = None
    for selector in CASINO_CONTENT_SELECTORS:
        for node in soup.select(selector):
            if node.find_parent(['header', 'footer', 'nav', 'aside']):
                continue
            branch = None
            if primary is not None and (primary is node or primary in node.descendants):
                branch = primary
                while branch.parent is not node:
                    branch = branch.parent
                # Preserve a compact banner branch containing the real menu;
                # never clear an arbitrary wrapper that owns the whole site.
                if branch is primary or branch.name not in ('header', 'nav') and branch.get('id') not in ('top', 'header', 'masthead', 'site-header'):
                    continue
            shell = None
            for candidate in node.select('header, footer, [role="banner"], [role="contentinfo"], .dr-navigation, #ja-mainnav'):
                # Blog cards commonly use <header> for their title. They are
                # page content, not the reusable website header. Likewise, a
                # semantic main container owns its descendants by definition.
                if node.name in ('main', 'article') or node.get('role') in ('main', 'article'):
                    continue
                if any(parent.name in ('article', 'main') or parent.get('role') in ('main', 'article')
                       for parent in candidate.parents if parent is not node):
                    continue
                shell = candidate
                break
            if shell is not None and (branch is None or shell is not branch and shell not in branch.descendants):
                continue
            main = node
            protected_shell = branch
            break
        if main is not None:
            break
    if main is None:
        main = _inferred_article_region(soup, primary)
    return main, protected_shell


def _inferred_article_region(soup, primary):
    """Find the smallest credible article box in pre-semantic table layouts."""
    # Flash-era table themes commonly placed a 120-300px navigation cell and a
    # content cell in the same row.  Once the SWF buttons are removed, scoring
    # the whole outer wrapper can erase both columns on generated casino pages.
    flash_landmark = (soup.select_one('[data-dr-flash-menu-slot]') or
                      soup.select_one('nav[data-dr-placement="legacy-flash-menu"]') or
                      soup.select_one('hr.menu'))
    menu_cell = flash_landmark.find_parent('td') if flash_landmark is not None else None
    if menu_cell is not None:
        row = menu_cell.find_parent('tr')
        candidates = []
        for order, node in enumerate(row.find_all('td', recursive=False) if row is not None else []):
            if node is menu_cell or primary is not None and (node is primary or primary in node.descendants):
                continue
            text = ' '.join(node.get_text(' ', strip=True).split())
            if len(text) < 60:
                continue
            links = node.select('a[href]')
            link_text = sum(len(' '.join(link.get_text(' ', strip=True).split())) for link in links)
            density = link_text / max(len(text), 1)
            blocks = len(node.select('p,h1,h2,h3,h4,ul,ol,table,blockquote,img'))
            candidates.append((min(len(text), 5000) / 20 + min(blocks, 20) * 5 - density * 100,
                               -order, node))
        if candidates:
            return max(candidates, key=lambda item: item[:2])[2]
    # Dreamweaver shells often have a full-width banner/menu table followed by
    # a nested three-column table (left rail, 585px article cell, right rail).
    # The article cell may not have an id or semantic landmark, so use the
    # same table-menu host to locate the row and preserve its background/width
    # attributes when generating a casino page.
    from .primary_navigation import legacy_table_primary
    _, table_menu = legacy_table_primary(soup)
    if table_menu is not None:
        shell_tables = list(table_menu.find_parents('table'))
        shell_table = shell_tables[-1] if shell_tables else None
        candidates = []
        if shell_table is not None:
            for order, row in enumerate(shell_table.find_all('tr')):
                cells = row.find_all('td', recursive=False)
                if len(cells) < 2:
                    continue
                for cell_order, node in enumerate(cells):
                    if node is table_menu or node.find('.dr-navigation'):
                        continue
                    text = ' '.join(node.get_text(' ', strip=True).split())
                    if len(text) < 60:
                        continue
                    blocks = len(node.select('p,h1,h2,h3,h4,ul,ol,table,blockquote,img'))
                    width = _numeric_dimension(node.get('width'))
                    names = ' '.join([node.get('id', ''), *node.get('class', [])])
                    hints = bool(re.search(r'(?:content|article|main|text)', names, re.I))
                    # The narrow news rail can contain many short paragraphs,
                    # so block count alone would beat the actual article cell.
                    # Give the broad middle column a decisive structural
                    # preference while retaining text/content hints.
                    score = min(len(text), 5000) / 20 + min(blocks, 20) * 5 + hints * 35 + min(width, 900) / 5
                    candidates.append((score, -order, -cell_order, node))
        if candidates:
            return max(candidates, key=lambda item: item[:3])[3]
    # Several early CMS generators used a numbered table template.  The first
    # cell in its fifth table is the stable article slot, while later rows hold
    # a repeated link list and the footer.
    for node in soup.find_all(['td', 'div', 'section']):
        names = ' '.join([node.get('id', ''), *node.get('class', [])])
        if re.search(r'_table_0?5_tr_0?1_td_0?1(?:\b|_)', names, re.I):
            if primary is None or primary is not node and primary not in node.descendants:
                return node

    candidates = []
    for order, node in enumerate(soup.find_all(['td', 'div', 'section'])):
        if node.find_parent(['header', 'footer', 'nav', 'aside']):
            continue
        if primary is not None and (node is primary or primary in node.descendants):
            continue
        names = ' '.join([node.get('id', ''), *node.get('class', [])])
        if re.search(r'(?:menu|nav|footer|header|sidebar|banner|toolbar|breadcrumb)', names, re.I):
            continue
        text = ' '.join(node.get_text(' ', strip=True).split())
        if len(text) < 60:
            continue
        links = node.select('a[href]')
        link_text = sum(len(' '.join(link.get_text(' ', strip=True).split())) for link in links)
        density = link_text / max(len(text), 1)
        if len(links) >= 3 and density > .55:
            continue
        blocks = len(node.select('p,h1,h2,h3,h4,ul,ol,table,blockquote,img'))
        hints = bool(re.search(r'(?:content|article|story|text|body|main)', names, re.I))
        depth = len(list(node.parents))
        score = min(len(text), 4000) / 25 + min(blocks, 20) * 6 + hints * 45 + min(depth, 18) * 2 - density * 100
        candidates.append((score, depth, -order, node))
    return max(candidates, key=lambda row: row[:3])[3] if candidates else None


def _remove_legacy_table_sidebars(soup, main, primary):
    """Drop decorative side columns from table-based casino shells.

    The recovered article should keep the site's primary menu and footer, but
    the donor's narrow news/Flash rails are unrelated to the casino article.
    Remove only direct sibling cells of the selected article cell and only
    when a separate primary-menu source exists.  This protects true sidebar
    menu layouts that have no independent top navigation.
    """
    if main is None or main.name != 'td' or soup.body is None:
        return
    if 'dr-legacy-table-casino' not in soup.body.get('class', []):
        return
    from .primary_navigation import legacy_table_primary
    legacy_menu, legacy_host = legacy_table_primary(soup)
    if primary is None and legacy_menu is None and legacy_host is None:
        return
    # Once the decorative rails are removed, the article cell must no longer
    # keep the donor's narrow column width.  Mark its nearest table as the
    # reusable content shell so the casino layout CSS can expand it to the
    # full archive shell width while leaving the banner/menu/footer intact.
    content_shell = main.find_parent('table')
    if content_shell is not None:
        content_shell['class'] = list(dict.fromkeys([
            *content_shell.get('class', []), 'dr-casino-content-shell'
        ]))
    main.attrs.pop('width', None)
    row = main.find_parent('tr')
    cells = row.find_all('td', recursive=False) if row is not None else []
    if len(cells) < 2:
        return
    for cell in list(cells):
        if cell is main:
            continue
        if cell.find('.dr-navigation') or (primary is not None and (cell is primary or primary in cell.descendants)):
            continue
        cell.decompose()


def casino_shell(source: BeautifulSoup, title: str) -> BeautifulSoup:
    soup = deepcopy(source)
    # Older CMS themes use divs rather than HTML5 landmarks. Retain the
    # ancestors, banner and menu; replace only the identified article area.
    from .primary_navigation import select_primary
    primary = select_primary(soup)
    main, protected_shell = _casino_content_region(soup, primary)
    if main is None:
        # Unknown markup must not destroy the downloaded body or stop a build.
        # Add an explicit article landmark before the footer and leave every
        # source node intact for the owner's review.
        main = soup.new_tag('main', attrs={'data-dr-content-source': 'generated-fallback'})
        footer = soup.select_one('footer, [role="contentinfo"], #ja-footer, #footer, #site-footer')
        if footer is not None:
            footer.insert_before(main)
        else:
            soup.body.append(main)
    elif main.name == 'td':
        soup.body['class'] = list(dict.fromkeys([*soup.body.get('class', []), 'dr-legacy-table-casino']))
        layout_tables = list(main.find_parents('table'))
        if primary is not None:
            layout_tables.extend(primary.find_parents('table'))
        for table in layout_tables:
            table['class'] = list(dict.fromkeys([*table.get('class', []), 'dr-legacy-layout-table']))
        _remove_legacy_table_sidebars(soup, main, primary)
    if protected_shell is None:
        main.clear()
    else:
        for child in list(main.contents):
            if child is protected_shell:
                continue
            child.extract()
    main['role'] = 'main'
    main['class'] = list(main.get('class', [])) + ['dr-casino-content']
    soup.body['class'] = list(soup.body.get('class', [])) + ['dr-casino-page']
    h1 = soup.new_tag('h1', attrs={'class': 'dr-article-title'})
    h1.string = title
    if protected_shell is None:
        main.append(h1)
    else:
        protected_shell.insert_before(h1)
        # A few early catalogue layouts keep their results list beside the
        # text/banner column.  It belongs to the archived page content, not to
        # the reusable shell of a new casino article.
        for stale in list(soup.select('#vypis')):
            if stale is not main and protected_shell not in stale.descendants:
                stale.decompose()
    main.append(soup.new_tag('div', attrs={'id': 'dr-editor-content', 'class': 'dr-article'}))
    soup.head.append(soup.new_tag('link', rel='stylesheet', href='/assets/dr-article.css'))
    problems = casino_shell_issues(source, soup)
    if problems:
        raise RestorationError(' '.join(detail for _, detail in problems))
    return soup
