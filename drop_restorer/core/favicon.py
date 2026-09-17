"""Local topic-based favicon design, independent of provider credentials."""
import colorsys
import re
import unicodedata
from urllib.parse import urlsplit

from PIL import Image

TOPICS = {
    'cinema': ('Кино и театр', 'film kino cinema movie movies theatre theater divadlo divadelni televize television tv',
               '<rect x="48" y="92" width="160" height="112" rx="12"/><path d="M48 88V52h160v36z"/><path d="m74 52 24 36m32-36 24 36m32-36 22 33" stroke="BACKGROUND" stroke-width="16"/><path d="m112 122 48 28-48 28z" fill="BACKGROUND"/>'),
    'cycling': ('Велоспорт', 'cycling bicycle bike bikes cyklistika cyklisticky cyklisticke mtb cyklo',
                '<g fill="none" stroke="white" stroke-width="13"><circle cx="62" cy="165" r="37"/><circle cx="195" cy="165" r="37"/><path d="m62 165 43-79 40 79H62l35-62h70l28 62m-32-90h23m-91 11h25"/></g>'),
    'sport': ('Спорт', 'sport sports fotbal football tennis atletika hokej hockey worldcup rally',
              '<path d="M84 46h88v54q0 60-44 60t-44-60zm-5 16H46v30q0 36 43 36m88-66h33v30q0 36-43 36M118 156h20v40h34v16H84v-16h34z"/>'),
    'education': ('Образование', 'school skola skoly skolni university education vzdela vzdelavani college courses kurzy',
                  '<path d="M32 88 128 44l96 44-96 44zm35 34v55q61 44 122 0v-55l-61 29zM210 102h12v87h-12z"/>'),
    'travel': ('Путешествия', 'travel hotel hotels hotelu accommodation tourism turistika cestovani pension penzion',
               '<path d="M128 30a65 65 0 0 0-65 65c0 48 65 127 65 127s65-79 65-127a65 65 0 0 0-65-65z"/><circle cx="128" cy="95" r="27" fill="BACKGROUND"/>'),
    'food': ('Еда и рестораны', 'restaurant restaurace food kuchyne cooking recipes recepty cafe kavarna cuisine',
             '<path d="M53 39h12v48h13V39h12v48h13V39h12v63q0 23-25 29v84H77v-84q-24-6-24-29zm104 0h27v176h-15v-74h-25V82q0-30 13-43z"/>'),
    'music': ('Музыка', 'music musical hudba hudebni koncert concert kapela band festival',
              '<path d="M100 62 209 39v127h-16V78l-77 16v94h-16z"/><ellipse cx="78" cy="187" rx="38" ry="25"/><ellipse cx="172" cy="166" rx="37" ry="25"/>'),
    'nature': ('Природа', 'nature priroda forest lesni garden zahrada zahrady ecology ekologie',
               '<path d="M211 39C67 26 32 88 59 155c14 35 58 47 91 19 38-33 49-86 61-135z"/><path d="M48 215 166 85" fill="none" stroke="BACKGROUND" stroke-width="13"/>'),
    'technology': ('Технологии', 'technology technologie software computer computers pocitace programovani internet hosting',
                   '<rect x="69" y="69" width="118" height="118" rx="14"/><rect x="94" y="94" width="68" height="68" rx="5" fill="BACKGROUND"/><path d="M96 38v31m32-31v31m32-31v31M96 187v31m32-31v31m32-31v31M38 96h31m-31 32h31m-31 32h31m118-64h31m-31 32h31m-31 32h31" stroke="white" stroke-width="12"/>'),
    'casino': ('Казино', 'casino kasino kasyno gambling poker roulette ruleta',
               '<rect x="51" y="51" width="154" height="154" rx="25"/><g fill="BACKGROUND"><circle cx="88" cy="88" r="14"/><circle cx="168" cy="88" r="14"/><circle cx="128" cy="128" r="14"/><circle cx="88" cy="168" r="14"/><circle cx="168" cy="168" r="14"/></g>'),
    'general': ('Общая тема — требуется просмотр', '',
                '<g fill="none" stroke="white" stroke-width="12"><circle cx="128" cy="128" r="82"/><ellipse cx="128" cy="128" rx="38" ry="82"/><path d="M47 128h162M59 87h138M59 169h138"/></g>'),
}


def words(text):
    plain = ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if not unicodedata.combining(c))
    return set(re.findall(r'[a-z]+', plain))


def profile(soups, build, override='auto'):
    if override != 'auto' and override not in TOPICS:
        raise ValueError('Unknown favicon topic')
    scores = {key: 0 for key in TOPICS if key != 'general'}
    evidence = {}
    for soup, page in zip(soups, build.pages):
        if page.casino:
            continue  # Added casino pages must not redefine an archive's topic.
        title = page.title + ' ' + ' '.join(node.get_text(' ', strip=True) for node in soup.select('title,h1,h2')[:12])
        metas = ' '.join(node.get('content','') for node in soup.select('meta[name="description" i],meta[name="keywords" i]'))
        for text, weight in ((title, 4), (metas, 2)):
            tokens = words(text)
            for key, (_, keywords, _) in TOPICS.items():
                hits = tokens & set(keywords.split())
                if hits:
                    scores[key] += len(hits) * weight
                    evidence.setdefault(key, set()).update(hits)
    topic = override if override != 'auto' else max(scores, key=scores.get)
    if override == 'auto' and not scores[topic]:
        topic = 'general'
    color = '#245a87'
    # Prefer a saturated logo color; white/black background pixels are ignored.
    for node in soups[0].select('img.logo,img#logo,[id*="logo"] img,[class*="logo"] img'):
        href = node.get('src', '')
        path = build.root / 'theme' / urlsplit(href).path.lstrip('/')
        if not href.startswith('/assets/') or not path.resolve().is_relative_to((build.root / 'theme/assets').resolve()):
            continue
        try:
            with Image.open(path) as image:
                image = image.convert('RGBA'); image.thumbnail((64, 64))
                choices = {}
                pixels = image.get_flattened_data() if hasattr(image, 'get_flattened_data') else image.getdata()
                for red, green, blue, alpha in pixels:
                    hue, saturation, value = colorsys.rgb_to_hsv(red/255, green/255, blue/255)
                    if alpha > 200 and saturation > .4 and .25 < value:
                        bucket = round(hue * 24) % 24
                        choices[bucket] = choices.get(bucket, 0) + 1
                if choices:
                    hue = max(choices, key=choices.get) / 24
                    rgb = colorsys.hsv_to_rgb(hue, .95, .72)
                    color = '#' + ''.join(f'{round(c*255):02x}' for c in rgb)
                    break
        except (OSError, ValueError):
            continue
    return {'topic':topic, 'label':TOPICS[topic][0], 'color':color, 'matched_words':sorted(evidence.get(topic, [])),
            'needs_topic_review':topic == 'general', 'selected_by':'owner' if override != 'auto' else 'page_titles_and_metadata'}


def svg_for(brief):
    color = brief['color']
    shapes = TOPICS[brief['topic']][2].replace('BACKGROUND', color)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
            f'<rect width="256" height="256" rx="52" fill="{color}"/><g fill="white">{shapes}</g></svg>')
