from __future__ import annotations

import re
import json
from pathlib import Path

from lxml import etree
from PIL import Image
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from .models import RestorationError, Snapshot


def render_svg(svg: str, path: Path, size: int = 256):
    try:
        root = etree.fromstring(svg.encode(), parser=etree.XMLParser(resolve_entities=False, no_network=True))
        if etree.QName(root).localname != 'svg' or '<!DOCTYPE' in svg.upper() or '<!ENTITY' in svg.upper():
            raise ValueError()
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            if etree.QName(node).localname in ('script', 'foreignObject', 'image', 'use', 'a', 'animate', 'set'):
                raise ValueError()
            for name, value in node.attrib.items():
                if etree.QName(name).localname.lower().startswith('on') or 'href' in name or re.search(r'url\(\s*[\"\']?(?!#)', value):
                    raise ValueError()
        if re.search(r'https?://|@import', ''.join(root.itertext()), re.I):
            raise ValueError()
    except (etree.XMLSyntaxError, ValueError) as error:
        raise RestorationError('Агент вернул SVG с неподдерживаемыми элементами или внешними ресурсами.') from error
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    if not renderer.isValid():
        raise RestorationError('Не удалось открыть SVG от агента.')
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(path)):
        raise RestorationError('Не удалось сохранить сгенерированное изображение.')
    path.with_suffix('.svg').write_text(svg, encoding='utf-8')


def branding(soups, build, downloader, snapshot, agent=None, regenerate: str = '', favicon_topic='auto'):
    main = soups[0]
    logos = main.select('img.logo, img#logo, [class*="logo"] img, [id*="logo"] img')
    has_logo = any(node.get('src', '').startswith(('/assets/', 'data:image/')) for node in logos)
    favicon = main.select_one('link[rel~="icon"]')
    icon_url = favicon.get('href') if favicon else ''
    if not icon_url and regenerate != 'favicon':
        icon_url = downloader.fetch('/favicon.ico', snapshot, 'image')
    # An href is not proof that the archived file was recovered successfully.
    valid_icon = False
    if icon_url and icon_url.startswith('/assets/'):
        icon_path = build.root / 'theme' / icon_url.lstrip('/')
        if icon_path.resolve().is_relative_to((build.root / 'theme/assets').resolve()):
            try:
                with Image.open(icon_path) as image:
                    image.verify()
                valid_icon = True
            except (OSError, ValueError):
                pass
    if not valid_icon:
        icon_url = ''
    assets = build.root / 'theme' / 'assets' / 'branding'
    site_title = build.pages[0].title
    for kind, needed in [('logo', not has_logo), ('favicon', not icon_url)]:
        if regenerate and kind != regenerate:
            continue
        if not needed and regenerate != kind:
            continue
        if not agent and kind == 'logo':
            build.warnings.append('Логотип не найден. Для генерации подключите агента.')
            continue
        path = assets / (kind + '.png')
        if kind == 'favicon':
            from .favicon import profile, svg_for
            brief = profile(soups, build, favicon_topic)
            method = 'local_topic_vector'
            if agent and hasattr(agent, 'svg'):
                try:
                    svg = agent.svg(json.dumps({'site_title':site_title, **brief}, ensure_ascii=False), kind, brief['color'])
                    render_svg(svg, path, 512)
                    with Image.open(path) as image:
                        if image.getbbox() is None:
                            raise RestorationError('Агент создал пустой фавикон.')
                    method = 'agent_topic_vector'
                except (RestorationError, OSError):
                    render_svg(svg_for(brief), path, 512)
                    method = 'local_after_agent_failure'
            else:
                render_svg(svg_for(brief), path, 512)
            with Image.open(path) as image:
                image.save(assets / 'favicon.ico', sizes=[(n, n) for n in (16, 32, 48, 64, 128, 256)])
                for size in (16, 32, 64, 128, 180, 192, 512):
                    image.resize((size, size), Image.Resampling.LANCZOS).save(assets / f'favicon-{size}.png')
            icon_url = '/assets/branding/favicon.ico'
            from .seo_contract import save_json
            save_json(build.root / 'favicon-report.json', {**brief, 'method':method, 'href':icon_url,
                      'preview':'/assets/branding/favicon-128.png', 'sizes':[16,32,48,64,128,180,192,256,512]})
            build.warnings[:] = [message for message in build.warnings if not message.startswith('Фавикон не найден.')]
        else:
            svg = agent.svg(site_title, kind)
            render_svg(svg, path)
            for soup in soups:
                existing = soup.select('img.logo, img#logo, [class*="logo"] img, [id*="logo"] img')
                if existing:
                    for logo in existing:
                        logo['src'] = '/assets/branding/logo.png'
                        logo.attrs.pop('srcset', None)
                else:
                    node = soup.new_tag('img', src='/assets/branding/logo.png', alt=site_title, attrs={'class': 'dr-fallback-logo'})
                    (soup.find('header') or soup.body).insert(0, node)
    if icon_url:
        for soup in soups:
            for old in soup.select('link[rel~="icon"]'):
                old.decompose()
            soup.head.append(soup.new_tag('link', rel='icon', href=icon_url))
            if icon_url == '/assets/branding/favicon.ico':
                for old in soup.select('link[rel="apple-touch-icon"]'):
                    old.decompose()
                soup.head.append(soup.new_tag('link', rel='icon', type='image/png', sizes='32x32', href='/assets/branding/favicon-32.png'))
                soup.head.append(soup.new_tag('link', rel='apple-touch-icon', sizes='180x180', href='/assets/branding/favicon-180.png'))
