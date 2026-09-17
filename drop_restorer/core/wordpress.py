from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from importlib.resources import files
from urllib.parse import unquote

from lxml import etree

from .cleaner import parse_html
from .indexation import open_indexation
from .theme_identity import theme_identity


WP = 'http://wordpress.org/export/1.2/'
NS = {'wp': WP, 'content': 'http://purl.org/rss/1.0/modules/content/',
      'excerpt': 'http://wordpress.org/export/1.2/excerpt/', 'dc': 'http://purl.org/dc/elements/1.1/'}


def wxr(build, contents: dict):
    identity = theme_identity(build.request.origin)
    rss = etree.Element('rss', version='2.0', nsmap=NS)
    channel = etree.SubElement(rss, 'channel')
    def element(parent, name, value, cdata=False):
        name = ('{' + NS[name.split(':')[0]] + '}' + name.split(':')[1]) if ':' in name else name
        item = etree.SubElement(parent, name)
        item.text = etree.CDATA(str(value)) if cdata else str(value)
        return item
    element(channel, 'title', build.pages[0].title)
    element(channel, 'link', build.request.origin)
    element(channel, 'description', identity.name + ' import')
    element(channel, 'language', build.request.lang)
    element(channel, 'wp:wxr_version', '1.2')
    element(channel, 'wp:base_site_url', build.request.origin)
    element(channel, 'wp:base_blog_url', build.request.origin)
    author = etree.SubElement(channel, '{' + WP + '}author')
    for key, value in [('author_id', '1'), ('author_login', 'site-import'), ('author_email', ''), ('author_display_name', 'Site Import')]:
        element(author, 'wp:' + key, value)
    term = etree.SubElement(channel, '{' + WP + '}term')
    for key, value in [('term_id', '1'), ('term_taxonomy', 'nav_menu'), ('term_slug', 'dr-primary'), ('term_name', 'Primary Menu')]:
        element(term, 'wp:' + key, value)
    export_date = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    for number, page in enumerate(build.pages, 1):
        item = etree.SubElement(channel, 'item')
        element(item, 'title', page.title, True)
        element(item, 'link', build.request.origin + page.route)
        element(item, 'dc:creator', 'site-import', True)
        guid = element(item, 'guid', build.request.origin + '/?site-page=' + page.key)
        guid.set('isPermaLink', 'false')
        element(item, 'content:encoded', '' if page.casino else contents[page.key], True)
        element(item, 'excerpt:encoded', '', True)
        for key, value in [('post_id', number), ('post_date', export_date), ('post_date_gmt', export_date),
                           ('post_type', 'page'), ('status', 'publish'), ('post_name', unquote(page.route.strip('/')) if page.casino else 'dr-' + page.key),
                           ('post_parent', 0), ('menu_order', number), ('comment_status', 'closed'), ('ping_status', 'closed')]:
            element(item, 'wp:' + key, value)
        metadata = {'_dr_key': page.key, '_dr_route': page.route, '_dr_casino': '1' if page.casino else '0',
                    '_wp_page_template': 'casino-page.php' if page.casino else 'page-template.php'}
        if not page.casino:
            metadata.update({'_dr_seo_title': page.seo_title, '_dr_description': page.description})
        for key, value in metadata.items():
            meta = etree.SubElement(item, '{' + WP + '}postmeta')
            element(meta, 'wp:meta_key', key, True)
            element(meta, 'wp:meta_value', value, True)
    # Native menu objects let owners edit navigation in Appearance > Menus.
    parent_id = 1000 + len(build.pages) + 1
    menu_entries = [(i, page, 0) for i, page in enumerate(build.pages, 1) if not page.casino]
    menu_entries += [(0, None, 0)]
    menu_entries += [(i, page, parent_id) for i, page in enumerate(build.pages, 1) if page.casino]
    menu_labels = {}
    for anchor in parse_html(build.pages[0].html).select('.dr-menu a[href]'):
        menu_labels.setdefault(anchor['href'], anchor.get_text(' ', strip=True))
    menu_order = {route: index for index, route in enumerate(menu_labels)}
    primary = sorted([entry for entry in menu_entries if entry[1] and not entry[1].casino], key=lambda entry: menu_order.get(entry[1].route, 999))
    menu_entries = primary + [entry for entry in menu_entries if entry[1] is None or entry[1].casino]
    for order, (page_id, page, parent) in enumerate(menu_entries, 1):
        item = etree.SubElement(channel, 'item')
        title = menu_labels.get(page.route, page.title) if page else build.request.casino_label
        identifier = 1000 + page_id if page else parent_id
        element(item, 'title', title, True)
        element(item, 'dc:creator', 'site-import', True)
        for key, value in [('post_id', identifier), ('post_type', 'nav_menu_item'), ('status', 'publish'),
                           ('post_name', 'dr-menu-' + str(identifier)), ('menu_order', order), ('post_parent', 0)]:
            element(item, 'wp:' + key, value)
        category = element(item, 'category', 'Primary Menu', True)
        category.set('domain', 'nav_menu')
        category.set('nicename', 'dr-primary')
        for key, value in {'_menu_item_type': 'post_type' if page else 'custom', '_menu_item_object': 'page' if page else 'custom',
                           '_menu_item_object_id': page_id, '_menu_item_menu_item_parent': parent,
                           '_menu_item_url': '' if page else '#', '_menu_item_target': '', '_menu_item_classes': 'a:0:{}', '_menu_item_xfn': ''}.items():
            meta = etree.SubElement(item, '{' + WP + '}postmeta')
            element(meta, 'wp:meta_key', key, True)
            element(meta, 'wp:meta_value', value, True)
    (build.root / 'content.xml').write_bytes(etree.tostring(rss, encoding='UTF-8', xml_declaration=True, pretty_print=True))


def write_theme_assets(theme):
    templates = files('drop_restorer').joinpath('templates')
    (theme / 'assets').mkdir(parents=True, exist_ok=True)
    assets = {
        'drop-restorer.css': 'site-navigation.css',
        'drop-restorer.js': 'site-navigation.js',
        'site-404.css': 'site-404.css',
        'school-layout.css': 'school-layout.css',
        'legacy-casino.css': 'legacy-casino.css',
        'dr-widget.css': 'dr-widget.css',
        'dr-article.css': 'dr-article.css',
        'dr-article.js': 'dr-article.js',
    }
    for source, target in assets.items():
        (theme / 'assets' / target).write_text(templates.joinpath(source).read_text(encoding='utf-8'), encoding='utf-8')
    for stale in ('drop-restorer.css', 'drop-restorer.js'):
        path = theme / 'assets' / stale
        if path.is_file():
            path.unlink()
    article_css = templates.joinpath('dr-article.css').read_text(encoding='utf-8')
    editor_css = article_css.replace(':is(#dr-editor-content, .dr-article)', 'body')
    (theme / 'assets' / 'dr-article-editor.css').write_text(editor_css, encoding='utf-8')


def write_site(build):
    build.seo_policy['casino_pending'] = 'index'
    root = build.root
    theme = root / 'theme'
    views = theme / 'views'
    views.mkdir(parents=True, exist_ok=True)
    (root / 'pages').mkdir(exist_ok=True)
    (root / 'preview_screenshots').mkdir(exist_ok=True)
    templates = files('drop_restorer').joinpath('templates')
    identity = theme_identity(build.request.origin)
    for name in ('functions.php', 'header.php', 'footer.php', 'index.php', '404.php', 'page-template.php', 'casino-page.php', 'widget.php', 'seo.php', 'article.php'):
        (theme / name).write_text(templates.joinpath(name).read_text(encoding='utf-8'), encoding='utf-8')
    # One typography source for the published article and the native WP editors.
    write_theme_assets(theme)
    (theme / 'style.css').write_text('/*\nTheme Name: ' + identity.name +
                                    '\nVersion: 0.1.9\nTested up to: 7.1\nRequires PHP: 8.0\nText Domain: ' + identity.text_domain + '\n*/\n', encoding='utf-8')
    runtime_policy = {key:value for key,value in build.seo_policy.items()
                      if key in ('version','url_style','casino_pending','redirects','widget_id','casino_layout')}
    manifest = {'origin': build.request.origin, 'lang': build.request.lang, 'theme': identity.manifest(),
                'pages': {}, 'seo_policy': runtime_policy, 'article_formatting': 1}
    contents = {}
    for page in build.pages:
        soup = open_indexation(parse_html(page.html))
        for node in soup.select('link[href="/assets/drop-restorer.css"]'):
            node['href'] = '/assets/site-navigation.css'
        for node in soup.select('script[src="/assets/drop-restorer.js"]'):
            node['src'] = '/assets/site-navigation.js'
        page.html = str(soup)
        (root / 'pages' / (page.key + '.html')).write_text(str(soup), encoding='utf-8')
        for node in list(soup.select('title, link[rel~="canonical"], meta[name="description" i]')):
            node.decompose()
        if page.casino:
            for node in list(soup.select('meta[name="robots" i]')):
                node.decompose()
        contents[page.key] = soup.body.decode_contents()
        if page.casino:
            slot = soup.find(id='dr-editor-content')
            if slot is not None:
                slot['class'] = list(dict.fromkeys(slot.get('class', []) + ['dr-article']))
                heading = slot.find_previous_sibling('h1')
                if heading is not None:
                    heading.replace_with('DR_ARTICLE_HEADING_SLOT')
                slot.string = 'DR_EDITOR_CONTENT_SLOT'
            # WP enqueues this after donor styles; the static preview uses the link above.
            for node in list(soup.select('link[href="/assets/dr-article.css"]')):
                node.decompose()
            (views / (page.key + '.html')).write_text(soup.body.decode_contents(), encoding='utf-8')
        manifest['pages'][page.key] = {'route': page.route, 'title': page.title, 'casino': page.casino,
                                      'head': soup.head.decode_contents(), 'body_attrs': dict(soup.body.attrs)}
    (theme / 'site.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    wxr(build, contents)
    from .packager import installation_instructions
    (root / 'README.md').write_text(installation_instructions(identity.slug + '.zip'), encoding='utf-8')
    build.approved_digest = None
    build.save()
