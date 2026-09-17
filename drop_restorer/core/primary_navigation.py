"""Select a site-wide menu without turning secondary lists into primary menus."""
import re

HEADER = 'header, [role="banner"], #header, #masthead, #site-header, #header-main, #header-secondary, #ja-header'
PRIMARY = ('#primary-nav', '#main-nav', '#main-navigation', '#ja-mainnav', '#mainmenu', '#main-menu',
           '.primary-navigation', '.main-navigation', '.mainmenu', '.main-menu', '.mainnav', '.main-nav',
           '#topmenu', '#top-menu', '.topmenu', '.top-menu', 'nav[aria-label="Main"]',
           'nav[aria-label="Main navigation"]', '.dr-navigation')
SECONDARY = re.compile(r'^(?:sidebar.*|.*sidebar|secondary|secondary-menu|secondary-nav|secondary-navigation|nav-secondary|menu-secondary|side-menu|side-nav|smenu|'
                        r'(?:right|left)(?:col(?:umn)?|-col|-column)|ja-col\d+|footer.*|.*footer|'
                        r'breadcrumbs?|toc|table-of-contents|utility.*|social.*|related.*)$', re.I)

# Fixed-width legacy pages commonly put the primary links in a ``topmenu``
# block before a ``#wrapper`` that contains ``#left`` and ``#content``.  The
# archived ``topmenu`` name describes the old source location, not the visual
# role the owner expects after recovery.  Keep the selectors narrow enough to
# avoid moving modern header navigation merely because a site has a sidebar.
LEGACY_LEFT_COLUMN = ('#left', '#leftcol', '#left-column', '.leftcol', '.left-column')
LEGACY_CONTENT_COLUMN = ('#content', '#content-column', '#main-content')


def _numeric_dimension(value):
    match = re.search(r'\d+', str(value or ''))
    return int(match.group()) if match else 0


def legacy_flash_primary(soup):
    """Find the original narrow column used by a removed Flash menu."""
    existing = soup.select_one('nav.dr-navigation[data-dr-placement="legacy-flash-menu"]')
    if existing is not None:
        order = existing.get('data-dr-flash-items', '').split()
        return existing, None, existing.parent, order
    slot = soup.select_one('[data-dr-flash-menu-slot]')
    if slot is not None:
        host = slot.find_parent(['td', 'aside', 'div', 'section']) or slot.parent
        return None, slot, host, slot.get('data-dr-flash-menu-slot', '').split()
    # Compatibility for builds downloaded before the marker existed.  A narrow
    # dark table cell containing hr.menu is the surviving shell around the old
    # SWF buttons and company/contact block.
    for marker in soup.select('hr.menu, hr[class*="menu"]'):
        host = marker.find_parent('td')
        if host is None or host.find_parent(['main', 'article']):
            continue
        width = _numeric_dimension(host.get('width'))
        if 80 <= width <= 360:
            return None, None, host, []
    return None, None, None, []


def legacy_dynamic_primary(soup):
    """Return a server-rendered target and link donor for old JS menus.

    FelixNET and similar early CMS engines emitted an empty ``menuframebox``
    in the visual menu column, then repeated the usable links in a
    ``linklistmenuframebox`` near the bottom of the document.  The archived
    JavaScript often depends on a missing ``rawmoduleout`` request, so leaving
    the placeholder to that script produces a blank site menu.
    """
    donors = []
    for node in soup.find_all(['div', 'span']):
        classes = ' '.join(node.get('class', []))
        if 'linklistmenuframebox' in classes.casefold() and len(node.select('a[href]')) >= 2:
            donors.append(node)
    if not donors:
        return None, None
    for node in soup.find_all(['div', 'span']):
        classes = ' '.join(node.get('class', []))
        folded = classes.casefold()
        if 'menuframebox' not in folded or 'linklistmenuframebox' in folded:
            continue
        matching = next((donor for donor in donors if node.get('id') and donor.get('id') == node.get('id')), None)
        if matching is None and len(donors) == 1:
            matching = donors[0]
        if matching is not None:
            return node, matching
    return None, donors[0]


def legacy_primary(node):
    """Recognise compact pre-HTML5 navigation without accepting article lists.

    Early sites often used a bare ``ul#menu`` inside a graphical ``div``
    banner.  It is a strong primary-menu signal, but only outside semantic
    article regions and when it contains several real navigation links.
    """
    if node.name not in ('ul', 'ol') or node.get('id', '').casefold() != 'menu':
        return False
    if node.find_parent(['main', 'article']) or secondary_context(node):
        return False
    links = [anchor for anchor in node.select(':scope > li a[href]')
             if anchor.get('href', '').strip() not in ('', '#')]
    if len(links) < 2:
        return False
    marker = node.find_previous_sibling()
    marker_text = marker.get_text(' ', strip=True) if marker is not None else ''
    has_access_keys = any(anchor.has_attr('accesskey') for anchor in links)
    has_root_link = any(anchor.get('href', '').split('#', 1)[0].rstrip('/') in ('', 'http:', 'https:')
                        or anchor.get('href', '').startswith('/') for anchor in links)
    return has_root_link and (has_access_keys or bool(re.search(r'\bmenu\b', marker_text, re.I)))


def secondary_context(node):
    for parent in [node, *node.parents]:
        if parent.name in ('aside', 'footer') or parent.get('role') in ('complementary', 'contentinfo'):
            return True
        names = [parent.get('id', ''), *parent.get('class', [])]
        if any(SECONDARY.fullmatch(name) for name in names):
            return True
        if re.search(r'\b(secondary|social|breadcrumb|utility)\b', parent.get('aria-label', ''), re.I):
            return True
    return False


def legacy_sidebar_target(soup, primary):
    """Return the legacy left column that should own a topmenu navigation.

    This is deliberately structural.  A menu is moved only when it is named
    ``topmenu``/``top-menu`` and a left/content pair lives inside the same
    wrapper-like shell.  Semantic/header menus and modern sidebars therefore
    keep their original placement.
    """
    if primary is None or primary.find_parent(['aside', 'footer']):
        return None
    if primary.get('data-dr-layout') in ('legacy-vertical', 'legacy-dynamic', 'legacy-sidebar'):
        return None
    names = [primary.get('id', ''), *primary.get('class', [])]
    if not any(re.search(r'(?:^|[-_])(topmenu|top-menu)(?:$|[-_])', str(name), re.I) for name in names):
        return None
    left = next((soup.select_one(selector) for selector in LEGACY_LEFT_COLUMN
                 if soup.select_one(selector) is not None), None)
    content = next((soup.select_one(selector) for selector in LEGACY_CONTENT_COLUMN
                    if soup.select_one(selector) is not None), None)
    if left is None or content is None or left is content or left in content.parents:
        return None
    # Require a wrapper/layout ancestor rather than the document body.  This
    # prevents moving a modern header menu on an unrelated page that happens
    # to contain a generic left sidebar.
    shell = None
    for ancestor in left.parents:
        if ancestor.name in ('html', 'body'):
            break
        names = [ancestor.get('id', ''), *ancestor.get('class', [])]
        if any(re.search(r'(?:wrapper|layout|page|container)', str(name), re.I) for name in names):
            shell = ancestor
            break
    if shell is None or content not in shell.descendants or primary in left.parents:
        return None
    return left


def select_primary(soup):
    headers = soup.select(HEADER)
    explicit_nodes = {id(node) for selector in PRIMARY for node in soup.select(selector)}
    dynamic, _ = legacy_dynamic_primary(soup)
    candidates = []
    selectors = ','.join(PRIMARY) + ',nav,[role="navigation"],ul.menu,ul[id*="menu"],ul[class*="nav"]'
    nodes = list(soup.select(selectors))
    if dynamic is not None and dynamic not in nodes:
        nodes.append(dynamic)
    for order, node in enumerate(nodes):
        if secondary_context(node):
            continue
        legacy = legacy_primary(node)
        archived_dynamic = node is dynamic
        explicit = id(node) in explicit_nodes
        in_header = any(header is node or header in node.parents for header in headers)
        if not explicit and not legacy and not archived_dynamic and (node.find_parent(['main', 'article']) or not in_header):
            continue
        # Do not clear a wrapper that also contains the logo or article.
        if node.select_one('main,article,header,footer,#logo,.site-logo'):
            continue
        score = 100 * explicit + 50 * in_header + 140 * legacy + 300 * archived_dynamic + (10 if node.name in ('nav', 'ul', 'ol') else 0)
        if node.get('data-dr-placement') == 'header-fallback':
            score -= 50
        candidates.append((score, -order, node))
    return max(candidates, key=lambda row: row[:2])[2] if candidates else None


def primary_menu_issues(soup):
    menus = soup.select('.dr-navigation')
    if len(menus) != 1:
        return ['Должно быть ровно одно главное меню с разделом казино.']
    menu = menus[0]
    issues = []
    if secondary_context(menu):
        issues.append('Раздел казино находится в боковом или второстепенном меню.')
    elif select_primary(soup) is not menu:
        issues.append('Раздел казино находится отдельно от распознанного главного меню.')
    if len(menu.select(':scope > #dr-primary-menu > .dr-casino')) != 1:
        issues.append('Раздел казино должен быть пунктом верхнего уровня главного меню.')
    if len(soup.select('.dr-casino')) != 1:
        issues.append('Раздел казино продублирован вне главного меню.')
    return issues
