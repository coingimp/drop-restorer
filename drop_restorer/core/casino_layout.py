"""Validated, site-wide presentation settings for generated casino pages."""
from __future__ import annotations

from bs4 import BeautifulSoup

from .primary_navigation import LEGACY_CONTENT_COLUMN, LEGACY_LEFT_COLUMN


CELL_KEYS = ('logo', 'bonus', 'characteristics', 'rating', 'button')


def default_layout() -> dict:
    return {
        'content_width_px': 1120,
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
    legacy_shell_width = 'fit-content' if layout['content_width_px'] else 'calc(100% - 32px)'
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
    return f"""
<style id="dr-casino-layout">
body.dr-casino-page .dr-casino-content {{
  box-sizing: border-box !important; width: 100% !important; max-width: {content} !important; margin-left: auto !important; margin-right: auto !important;
}}
body.dr-casino-page .dr-legacy-layout-table {{ box-sizing: border-box; width: 100% !important; max-width: 100% !important; }}
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
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-sidebar-column {{
    box-sizing: border-box !important; flex: 0 0 auto !important; float: none !important; position: static !important;
    left: auto !important; right: auto !important; min-width: 0 !important; max-width: 100% !important;
    margin-left: 0 !important; margin-right: 0 !important; overflow: visible !important;
  }}
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-content-column.dr-casino-content {{
    box-sizing: border-box !important; flex: {legacy_content_flex} !important; float: none !important; position: static !important;
    left: auto !important; right: auto !important; width: {legacy_content_width} !important; max-width: {content} !important;
    min-width: 0 !important; margin-left: 0 !important; margin-right: 0 !important; overflow: visible !important;
  }}
  body.dr-casino-page.dr-legacy-sidebar-navigation .dr-legacy-sidebar-shell > .cistic {{
    display: none !important;
  }}
}}
{columns}
@media (max-width: 768px) {{ body.dr-casino-page .dr-casino-content {{ max-width: 100% !important; }} body.dr-casino-page .dr-casino-content .dr-casino-table {{ width: 100% !important; }} {mobile_columns} }}
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


def apply_to_soup(soup: BeautifulSoup, policy: dict | None) -> BeautifulSoup:
    """Add scoped layout CSS to casino HTML without touching normal pages."""
    if not soup.select_one('body.dr-casino-page'):
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
