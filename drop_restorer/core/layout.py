"""Structural repairs for the recovered fixed-width school layout.

Detection uses the archived layout structure, not a donor hostname. Other
themes keep their own layout and receive only the shared navigation styles.
"""
import posixpath
import re


def repair_layout(soup, root):
    repair_joomla_casino(soup, root)
    if not all(soup.select_one(selector) for selector in ('#content-holder', '#header-main', '#header-secondary', '#main-article')):
        return
    soup.body['class'] = list(dict.fromkeys([*soup.body.get('class', []), 'dr-school-layout']))
    for selector in ('#hp-nav', '#subpage-nav', '#i-am', '#header-icons-holder', '#search-holder'):
        for node in soup.select(selector):
            node.decompose()
    header = soup.select_one('#header-secondary')
    soup.select_one('#header-main').insert_after(header.extract())
    carousel = soup.select_one('#hp-carousel-content')
    if carousel:
        # The archived carousel hardcodes a 980px sliding strip. Preserve its
        # slides and replace only its controls/initialization with a fluid one.
        carousel['id'] = 'dr-school-slides'
        carousel['class'] = ['dr-slides']
        slides = carousel.find_all('li', recursive=False)
        fallback = ''
        for link in soup.select('link[rel~="stylesheet"][href]'):
            href = link['href']
            if not href.startswith('/assets/'):
                continue
            path = root / 'theme' / href.lstrip('/')
            if not path.is_file():
                continue
            match = re.search(r'body\.hp\s*\{[^}]*url\([\"\']?([^\"\')]+)', path.read_text(encoding='utf-8', errors='replace'))
            if match:
                candidate = posixpath.normpath(posixpath.join(posixpath.dirname(href), match[1]))
                if candidate.startswith('/assets/') and (root / 'theme' / candidate.lstrip('/')).is_file():
                    fallback = candidate
                    break
        for index, slide in enumerate(slides):
            slide['class'] = ['dr-slide']
            if index:
                slide['hidden'] = ''
            image = slide.find('img')
            if index == 0 and image is not None and not image.get('src') and fallback:
                image['src'] = fallback
        old = soup.select_one('#jcarousel-control')
        if old:
            old.decompose()
        controls = soup.new_tag('div', attrs={'class': 'dr-slide-controls', 'aria-label': 'Slides'})
        for index, slide in enumerate(slides):
            heading = slide.find(['h2', 'h3'])
            button = soup.new_tag('button', attrs={'type': 'button', 'data-dr-slide': str(index),
                'aria-label': heading.get_text(' ', strip=True) if heading else f'Slide {index + 1}',
                'aria-pressed': 'true' if index == 0 else 'false'})
            button.string = str(index + 1)
            controls.append(button)
        soup.select_one('#hp-carousel').append(controls)
    for node in soup.select('img:not([src])'):
        node['class'] = [*node.get('class', []), 'dr-missing-image']
    if not soup.select_one('link[href="/assets/school-layout.css"]'):
        soup.head.append(soup.new_tag('link', rel='stylesheet', href='/assets/school-layout.css'))


def repair_joomla_casino(soup, root):
    """Adapt the detected legacy columns only on new casino article pages."""
    if 'dr-casino-page' not in soup.body.get('class', []) or not all(
            soup.select_one(selector) for selector in ('#ja-wrapper', '#ja-header', '#ja-content', '#ja-mainnav')):
        return
    soup.body['class'] = list(dict.fromkeys([*soup.body.get('class', []), 'dr-joomla-casino']))
    # Homepage news and sponsor columns are not part of a blank casino article.
    for selector in ('#ja-col1', '#ja-col2', '#ja-botslwrap', '#ja-newsflash', '#ja-pathwaywrap', '#ja-usercolorswrap', '.accessibility'):
        for node in soup.select(selector):
            node.decompose()
    # Removed Joomla controls must not leave scripts changing widths, fonts or
    # news slides. New article navigation uses the shared local controller.
    for node in list(soup.select('script')):
        if node.get('src') not in ('/assets/site-navigation.js', '/assets/drop-restorer.js', '/assets/dr-article.js'):
            node.decompose()
    # The archived template name is a missing-logo placeholder. Real images
    # and site names remain part of the shell.
    for node in soup.select('#ja-mainnavwrap > .logo'):
        node['class'] = list(dict.fromkeys([*node.get('class', []), 'dr-legacy-logo']))
        if not node.select_one('img, svg') and node.get_text(strip=True).casefold() in ('ja_mageia', 'ja mageia'):
            node['class'].append('dr-template-placeholder')
    header = soup.select_one('#ja-header')
    background = re.search(r'url\([\"\']?(/assets/[^\"\')]+)', header.get('style', ''))
    if background:
        from PIL import Image
        path = root / 'theme' / background[1].lstrip('/')
        if path.resolve().is_relative_to((root / 'theme' / 'assets').resolve()) and path.is_file():
            try:
                with Image.open(path) as image:
                    header['style'] = header.get('style', '').rstrip(';') + f';--dr-banner-ratio:{image.width}/{image.height};'
            except OSError:
                pass
    if not soup.select_one('link[href="/assets/legacy-casino.css"]'):
        soup.head.append(soup.new_tag('link', rel='stylesheet', href='/assets/legacy-casino.css'))
