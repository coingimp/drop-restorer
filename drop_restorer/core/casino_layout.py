"""Validated, site-wide presentation settings for generated casino pages."""
from __future__ import annotations

from bs4 import BeautifulSoup

from .primary_navigation import LEGACY_CONTENT_COLUMN, LEGACY_LEFT_COLUMN


CELL_KEYS = ('logo', 'bonus', 'characteristics', 'rating', 'button')


def default_layout() -> dict:
    return {
        'content_width_px': 1120,
        'navigation_width_px': 0,
        'table_width_px': 0,
        'row_height_px': 110,
        'cell_padding_px': 12,
        'cell_widths_px': {key: 0 for key in CELL_KEYS},
    }


def _integer(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def normalize_layout(value) -> dict:
    """Return a safe, JSON-serialisable layout; zero means automatic/full width."""
    source = value if isinstance(value, dict) else {}
    defaults = default_layout()
    widths = source.get('cell_widths_px', {})
    if not isinstance(widths, dict):
        widths = {}
    return {
        'content_width_px': _integer(source.get('content_width_px'), defaults['content_width_px'], 0, 2400),
        'navigation_width_px': _integer(source.get('navigation_width_px'), defaults['navigation_width_px'], 0, 2400),
        'table_width_px': _integer(source.get('table_width_px'), defaults['table_width_px'], 0, 2400),
        'row_height_px': _integer(source.get('row_height_px'), defaults['row_height_px'], 72, 400),
        'cell_padding_px': _integer(source.get('cell_padding_px'), defaults['cell_padding_px'], 0, 32),
        'cell_widths_px': {
            key: _integer(widths.get(key), 0, 0, 1200) for key in CELL_KEYS
        },
    }


def layout_from_policy(policy: dict | None) -> dict:
    policy = policy if isinstance(policy, dict) else {}
    return normalize_layout(policy.get('casino_layout'))


def css_for_layout(layout: dict) -> str:
    """CSS shared by preview and the installed theme; values are already integers."""
    layout = normalize_layout(layout)
    content = f"{layout['content_width_px']}px" if layout['content_width_px'] else 'none'
    table = f"{layout['table_width_px']}px" if layout['table_width_px'] else '100%'
    pad = f"{layout['cell_padding_px']}px"
    row = f"{layout['row_height_px']}px"
    navigation_width = layout['navigation_width_px'] or layout['content_width_px']
    shell_width = max(layout['content_width_px'], navigation_width)
    shell_width_css = (f"min({shell_width}px, calc(100vw - 32px))" if shell_width
                       else 'calc(100% - 32px)')
    navigation_width_css = (f"min({navigation_width}px, calc(100vw - 32px))"
                            if navigation_width else '100%')
    legacy_navigation_width = layout['navigation_width_px'] or 180
    legacy_navigation_width_css = f'{legacy_navigation_width}px'
    legacy_shell_width = (f"min(calc({layout['content_width_px']}px + {legacy_navigation_width}px), calc(100vw - 32px))"
                          if layout['content_width_px'] else 'calc(100vw - 32px)')
    legacy_content_width = f"{layout['content_width_px']}px" if layout['content_width_px'] else 'auto'
    legacy_content_flex = (f"1 1 {layout['content_width_px']}px"
                           if layout['content_width_px'] else '1 1 auto')
    selectors = {
        'logo': '.casino-row__logo',
        'bonus': '.casino-row__bonus',
        'characteristics': '.casino-row__characteristics',
        'rating': '.casino-row__rating',
        'button': '.casino-row__button',
    }
    columns = '\n'.join(
        f"body.dr-casino-page .dr-casino-content .dr-casino-table {selector} {{ width: {width}px !important; }}"
        for key, selector in selectors.items()
        for width in ([layout['cell_widths_px'][key]] if layout['cell_widths_px'][key] else [])
    )
    mobile_columns = '\n'.join(
        f"body.dr-casino-page .dr-casino-content .dr-casino-table {selector} {{ width: auto !important; }}"
        for key, selector in selectors.items()
        if layout['cell_widths_px'][key]
    )
    wordpress_main_slot = f"""
@media (min-width: 801px) {{
  body.dr-casino-page.dr-wordpress-main-slot .gesamt {{ box-sizing: border-box !important; width: {shell_width_css} !important; max-width: calc(100vw - 32px) !important; margin-left: auto !important; margin-right: auto !important; }}
  body.dr-casino-page.dr-wordpress-main-slot .header {{ position: relative !important; width: 100% !important; }}
  body.dr-casino-page.dr-wordpress-main-slot .header_image {{ display: block !important; max-width: 100% !important; height: auto !important; margin-left: auto !important; margin-right: auto !important; }}
  body.dr-casino-page.dr-wordpress-main-slot .header .logo, body.dr-casino-page.dr-wordpress-main-slot .header .slogan {{ left: max(0px, calc((100% - 1000px) / 2)) !important; }}
  body.dr-casino-page.dr-wordpress-main-slot #head-menu {{ box-sizing: border-box !important; width: {navigation_width_css} !important; max-width: 100% !important; margin-left: auto !important; margin-right: auto !important; }}
  body.dr-casino-page.dr-wordpress-main-slot #head-menu .main-width {{ box-sizing: border-box !important; width: 100% !important; max-width: 100% !important; margin-left: auto !important; margin-right: auto !important; }}
}}
@media (max-width: 800px) {{
  body.dr-casino-page.dr-wordpress-main-slot .gesamt {{ box-sizing: border-box !important; width: 100% !important; max-width: 100vw !important; margin-left: auto !important; margin-right: auto !important; }}
  body.dr-casino-page.dr-wordpress-main-slot .header {{ position: relative !important; width: 100% !important; }}
  body.dr-casino-page.dr-wordpress-main-slot .header_image {{ display: block !important; width: 100% !important; max-width: 100% !important; height: auto !important; margin-left: auto !important; margin-right: auto !important; }}
  body.dr-casino-page.dr-wordpress-main-slot .header .logo, body.dr-casino-page.dr-wordpress-main-slot .header .slogan {{ left: 0 !important; max-width: calc(100% - 32px) !important; margin-left: 16px !important; }}
  body.dr-casino-page.dr-wordpress-main-slot #head-menu {{ box-sizing: border-box !important; width: 100% !important; max-width: 100% !important; height: auto !important; min-height: 0 !important; margin-top: 0 !important; }}
  body.dr-casino-page.dr-wordpress-main-slot #head-menu .main-width, body.dr-casino-page.dr-wordpress-main-slot #head-menu .dr-navigation {{ box-sizing: border-box !important; width: 100% !important; max-width: 100% !important; height: auto !important; min-height: 0 !important; }}
  body.dr-casino-page.dr-wordpress-main-slot #dr-primary-menu {{ position: relative !important; inset: auto !important; top: auto !important; right: auto !important; bottom: auto !important; left: auto !important; }}
}}
"""
    return f"""
<style id="dr-casino-layout">
body.dr-casino-page .dr-casino-content {{
  box-sizing: border-box !important; width: 100% !important; max-width: {content} !important; margin-left: auto !important; margin-right: auto !important;
}}
body.dr-casino-page .dr-legacy-layout-table {{ box-sizing: border-box; width: 100% !important; max-width: 100% !important; }}
/* Keep the donor width on legacy table shells on desktop.  The previous global
   width:100% rule expanded an 870px archive shell to the viewport and left a
   large empty coloured band around the casino article. */
body.dr-casino-page.dr-legacy-table-casino .dr-legacy-layout-table {{ width: auto !important; max-width: 100% !important; margin-left: auto !important; margin-right: auto !important; }}
body.dr-casino-page.dr-legacy-table-casino .dr-casino-content-shell {{ box-sizing: border-box !important; width: 100% !important; max-width: 100% !important; min-width: 0 !important; margin-left: auto !important; margin-right: auto !important; table-layout: auto !important; }}
body.dr-casino-page.dr-legacy-table-casino .dr-casino-content-shell > tbody > tr > td.dr-casino-content {{ box-sizing: border-box !important; width: 100% !important; max-width: 100% !important; min-width: 0 !important; background-repeat: no-repeat !important; background-size: 100% 100% !important; }}
body.dr-casino-page.dr-legacy-table-casino .dr-casino-content {{ width: 100% !important; max-width: 100% !important; }}
body.dr-casino-page .dr-casino-content > #dr-editor-content, body.dr-casino-page .dr-casino-content > .dr-article {{ box-sizing: border-box; width: 100%; max-width: 100%; }}
body.dr-casino-page .dr-casino-content .dr-casino-table {{ --dr-casino-table-width: {table}; --dr-offer-row-height: {row}; --dr-casino-cell-padding: {pad}; box-sizing: border-box; width: min(100%, var(--dr-casino-table-width)) !important; max-width: 100% !important; margin-left: auto !important; margin-right: auto !important; }}
body.dr-casino-page .dr-casino-content .dr-casino-table .casino-table tbody tr {{ min-height: var(--dr-offer-row-height); }}
body.dr-casino-page .dr-casino-content .dr-casino-table .casino-table tbody tr td {{ padding-top: var(--dr-casino-cell-padding) !important; padding-bottom: var(--dr-casino-cell-padding) !important; }}
@media (min-width: 801px) {{
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-sidebar-shell {{
    box-sizing: border-box !important; display: flex !important; flex-flow: row nowrap !important; align-items: flex-start !important;
    width: {legacy_shell_width} !important; max-width: calc(100vw - 32px) !important; min-width: 0 !important;
    margin-left: auto !important; margin-right: auto !important;
  }}
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-sidebar-layout-table {{
    box-sizing: border-box !important; width: {legacy_shell_width} !important; max-width: calc(100vw - 32px) !important;
    min-width: 0 !important; margin-left: auto !important; margin-right: auto !important; table-layout: fixed !important;
  }}
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-sidebar-shell > .dr-legacy-sidebar-column {{
    box-sizing: border-box !important; flex: 0 0 {legacy_navigation_width_css} !important; width: {legacy_navigation_width_css} !important;
    float: none !important; position: static !important; left: auto !important; right: auto !important;
    min-width: 0 !important; max-width: {legacy_navigation_width_css} !important;
    margin-left: 0 !important; margin-right: 0 !important; overflow: visible !important;
  }}
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-sidebar-shell > .dr-legacy-content-column {{
    box-sizing: border-box !important; flex: {legacy_content_flex} !important; float: none !important; position: static !important;
    left: auto !important; right: auto !important; width: auto !important; max-width: none !important;
    min-width: 0 !important; margin-left: 0 !important; margin-right: 0 !important; overflow: visible !important;
  }}
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-sidebar-shell > td:not(.dr-legacy-sidebar-column):not(.dr-legacy-content-column) {{ display: none !important; }}
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-sidebar-shell > .dr-legacy-sidebar-column > table,
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-sidebar-shell > .dr-legacy-content-column > table {{
    box-sizing: border-box !important; width: 100% !important; max-width: 100% !important; table-layout: fixed !important;
  }}
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-content-column .dr-casino-content {{
    box-sizing: border-box !important; width: 100% !important; max-width: 100% !important;
  }}
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-sidebar-shell > .cistic {{
    display: none !important;
  }}
}}
{columns}
{wordpress_main_slot}
@media (max-width: 768px) {{ body.dr-casino-page .dr-casino-content {{ max-width: 100% !important; }} body.dr-casino-page .dr-casino-content .dr-casino-table {{ width: 100% !important; }} body.dr-casino-page.dr-legacy-table-casino .dr-legacy-layout-table, body.dr-casino-page.dr-legacy-table-casino .dr-casino-content-shell {{ width: 100% !important; max-width: 100% !important; }} {mobile_columns} }}
</style>
"""


def _mark_legacy_sidebar_layout(soup: BeautifulSoup) -> None:
    """Mark the old two-column shell so casino width controls can replace its float hack."""
    body = soup.body
    if body is None or 'dr-legacy-sidebar-navigation' not in body.get('class', []):
        return
    navigation = soup.select_one('.dr-navigation[data-dr-layout="legacy-sidebar"]')
    content = soup.select_one('.dr-casino-content')
    if navigation is None or content is None:
        return
    sidebar = next((node for selector in LEGACY_LEFT_COLUMN for node in soup.select(selector)
                    if node is navigation.parent or navigation in node.descendants), None)
    if sidebar is None:
        return
    recognised = any(content is soup.select_one(selector) for selector in LEGACY_CONTENT_COLUMN)
    if not recognised and 'dr-casino-content' not in content.get('class', []):
        return
    shell = next((ancestor for ancestor in sidebar.parents
                  if ancestor.name not in ('html', 'body') and content in ancestor.descendants), None)
    if shell is None:
        return
    shell['class'] = list(dict.fromkeys([*shell.get('class', []), 'dr-legacy-sidebar-shell']))
    sidebar['class'] = list(dict.fromkeys([*sidebar.get('class', []), 'dr-legacy-sidebar-column']))
    content['class'] = list(dict.fromkeys([*content.get('class', []), 'dr-legacy-content-column']))
    direct_sidebar = next((node for node in shell.find_all('td', recursive=False)
                           if node is sidebar or sidebar in node.descendants), None)
    direct_content = next((node for node in shell.find_all('td', recursive=False)
                           if node is content or content in node.descendants), None)
    if direct_sidebar is not None and direct_content is not None and direct_sidebar is not direct_content:
        direct_sidebar['class'] = list(dict.fromkeys([*direct_sidebar.get('class', []), 'dr-legacy-sidebar-column']))
        direct_content['class'] = list(dict.fromkeys([*direct_content.get('class', []), 'dr-legacy-content-column']))
        shell_table = shell.find_parent('table')
        if shell_table is not None:
            shell_table['class'] = list(dict.fromkeys([*shell_table.get('class', []), 'dr-legacy-sidebar-layout-table']))


def apply_to_soup(soup: BeautifulSoup, policy: dict | None) -> BeautifulSoup:
    """Add scoped layout CSS to casino HTML without touching normal pages."""
    body = soup.body
    if body is None:
        return soup
    classes = list(body.get('class', []))
    has_wordpress_main_slot = soup.select_one(
        'body.dr-casino-page #head-menu .dr-navigation[data-dr-layout="wordpress-main-slot"]'
    ) is not None
    if has_wordpress_main_slot:
        classes = list(dict.fromkeys([*classes, 'dr-wordpress-main-slot']))
    else:
        classes = [name for name in classes if name != 'dr-wordpress-main-slot']
    body['class'] = classes
    if 'dr-casino-page' not in classes:
        return soup
    _mark_legacy_sidebar_layout(soup)
    head = soup.head
    if head is None:
        head = soup.new_tag('head')
        soup.insert(0, head)
    old = head.select_one('#dr-casino-layout')
    if old is not None:
        old.decompose()
    fragment = BeautifulSoup(css_for_layout(layout_from_policy(policy)), 'html.parser')
    style = fragment.select_one('#dr-casino-layout')
    if style is not None:
        head.append(style)
    return soup
