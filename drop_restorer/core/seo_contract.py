"""Versioned owner checklist. Every original row retains its own stable ID."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

VERSION = 'owner-seo-2026-09-09-v3'
ROWS = [
    ('url_style', 'Выбрана единая структура URL со слешем или без слеша'),
    ('alternatives', 'Все альтернативные варианты URL → основная версия через 301'),
    ('http_200', 'Основные страницы сайта возвращают HTTP 200'),
    ('http_404', 'Страницы, которые должны отсутствовать, возвращают корректный 404'),
    ('links_redirect', 'Нет внутренних ссылок на URL, которые отдают редирект'),
    ('links_broken', 'Нет внутренних битых ссылок'),
    ('robots_allow', 'robots.txt не блокирует важные страницы'),
    ('robots_sitemap', 'В robots.txt указан актуальный XML Sitemap'),
    ('sitemap_http', 'XML Sitemap существует и открывается'),
    ('sitemap_urls', 'XML Sitemap содержит только актуальные индексируемые URL'),
    ('canonical', 'На всех индексируемых страницах установлен корректный canonical'),
    ('title_count', 'На каждой индексируемой странице присутствует ровно 1 <title>'),
    ('title_meaning', 'Title уникален и соответствует содержанию страницы'),
    ('h1_count', 'На каждой индексируемой странице присутствует ровно 1 <h1>'),
    ('h1_meaning', 'H1 соответствует содержанию страницы'),
    ('description_count', 'На каждой индексируемой странице присутствует <meta name="description">'),
    ('description_meaning', 'Description уникален и соответствует содержанию страницы'),
    ('lang', 'Корректно установлен <html lang>'),
    ('og_locale', 'og:locale соответствует GEO/языку страницы, если используется'),
    ('favicon_exists', 'Favicon присутствует'),
    ('favicon_browser', 'Favicon корректно отображается в браузере'),
    ('url_readable', 'Все основные URL имеют понятную и логичную структуру; нет случайных параметров в индексируемых URL'),
    ('technical', 'Нет технических URL, доступных для индексации'),
    ('query_duplicates', 'Нет дублирования страниц из-за параметров URL'),
    ('url_duplicates', 'Нет дублирования страниц из-за разных вариантов URL'),
    ('index_duplicates', 'Нет дублей из-за /index.php, /index.html и подобных вариантов'),
    ('empty_200', 'Нет пустых страниц с HTTP 200'),
    ('planned_pages', 'Все запланированные страницы созданы'),
    ('content_present', 'На всех страницах присутствует необходимый контент'),
    ('content_visual', 'Контент корректно отображается; изображения отображаются корректно'),
    ('images_broken', 'Нет битых изображений'),
    ('content_geo', 'Контент соответствует целевому GEO и языку проекта'),
    ('menu_visual', 'Главное меню (при наличии) отображается корректно'),
    ('menu_links', 'Все пункты главного меню ведут на корректные страницы'),
    ('menu_mobile', 'Мобильное меню работает корректно'),
    ('footer', 'Footer отображается корректно; нет битых внутренних ссылок'),
    ('links_errors', 'Нет внутренних ссылок с 404, 5хх'),
    ('links_redirect_repeat', 'Нет внутренних ссылок на URL с редиректом'),
    ('affiliate_mobile', 'Блок с партнерками отображается корректно в мобильной версии'),
    ('affiliate_desktop', 'Блок с партнерками отображается корректно в ПК версии'),
    ('affiliate_links', 'Все партнерские ссылки работают'),
    ('affiliate_go', 'Партнерские ссылки обернуты через /go/'),
    ('desktop', 'Сайт корректно отображается на ПК версии'),
    ('mobile', 'Сайт корректно отображается в мобильной версии'),
    ('overflow', 'Нет горизонтального скролла'),
    ('readable', 'Текст читаемый на мобильных устройствах; заголовки не обрезаются'),
    ('images_bounds', 'Изображения не выходят за пределы экрана'),
    ('menu_mobile_repeat', 'Меню работает на мобильных устройствах'),
    ('organizations', 'Нет названий сторонних организаций, которые не должны присутствовать на сайте'),
    ('trademarks', 'Нет чужих торговых марок, которые не должны присутствовать на сайте'),
    ('people', 'Нет чужих ФИО'),
    ('phones', 'Нет чужих номеров телефонов'),
    ('addresses', 'Нет чужих адресов'),
    ('emails', 'Нет чужих email-адресов'),
    ('donor_links', 'Нет ссылок на сторонние проекты, оставшихся от исходного сайта'),
    ('robots_sections', 'В robots.txt разрешено сканирование необходимых разделов'),
    ('noindex', 'На индексируемых страницах отсутствует noindex'),
    ('canonical_index', 'Canonical не препятствует индексации'),
]
DEFINITIONS = [{'id': f'SEO-{i:02}', 'key': key, 'requirement': label} for i, (key, label) in enumerate(ROWS, 1)]


def default_policy():
    from .casino_layout import default_layout
    return {'version': VERSION, 'url_style': 'slash', 'casino_pending': 'index',
            'widget_id': None, 'casino_layout': default_layout(), 'decisions': {}, 'url_overrides': {}, 'affiliate_reviews': {}}


def fingerprint(build):
    """Bind evidence to the exact planned content, policy, and every local asset."""
    digest = hashlib.sha256()
    policy = {k:v for k,v in build.seo_policy.items() if k not in ('decisions', 'affiliate_reviews')}
    digest.update(json.dumps({'pages':[asdict(p) for p in build.pages], 'request':asdict(build.request),
                             'policy':policy}, ensure_ascii=False, sort_keys=True).encode())
    for folder in ('pages', 'theme/assets'):
        for path in sorted((build.root / folder).rglob('*')):
            if path.is_file():
                digest.update(path.relative_to(build.root).as_posix().encode())
                digest.update(path.read_bytes())
    widget = build.root / 'affiliate-manifest.json'
    if widget.exists():
        manifest = json.loads(widget.read_text(encoding='utf-8'))
        manifest.pop('checked_at', None)
        for offer in manifest.get('offers', []):
            offer.pop('verification', None)
        digest.update(json.dumps(manifest,sort_keys=True,ensure_ascii=False).encode())
    from importlib.resources import files
    for name in ('core/models.py','core/seo_routes.py', 'core/indexation.py','core/checklist.py','core/cleaner.py','core/casino_layout.py','core/primary_navigation.py','core/branding.py','core/favicon.py','core/affiliates.py','web/site_probe.py','preview/server.py'):
        digest.update(files('drop_restorer').joinpath(name).read_bytes())
    for template in sorted(files('drop_restorer').joinpath('templates').iterdir(), key=lambda p:p.name):
        if template.is_file():
            digest.update(template.name.encode()); digest.update(template.read_bytes())
    return digest.hexdigest()


def save_json(path: Path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
