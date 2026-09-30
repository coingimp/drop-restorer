from __future__ import annotations

import json
import hashlib
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .models import RestorationError
from .audit import audit, require_pass
from .theme_identity import theme_identity


def _static_file_for_route(route: str) -> str:
    parsed = urlsplit(route)
    if parsed.query or parsed.fragment:
        raise RestorationError('Для статической HTML-сборки задайте чистые URL без параметров и фрагментов: ' + route)
    path = unquote(parsed.path or '/')
    if not path.startswith('/') or '\\' in path or any(ord(char) < 32 for char in path):
        raise RestorationError('Небезопасный путь для HTML-файла: ' + route)
    raw_segments = path.strip('/').split('/') if path.strip('/') else []
    if any(not part or part in ('.', '..') or any(char in '<>:"|?*' for char in part)
           or part.endswith(('.', ' ')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part)
           for part in raw_segments):
        raise RestorationError('Путь страницы нельзя безопасно сохранить в ZIP: ' + route)
    if raw_segments and raw_segments[0].casefold() == 'assets':
        raise RestorationError('Путь страницы конфликтует с папкой ресурсов статического сайта: ' + route)
    if len(raw_segments) == 1 and raw_segments[0].casefold() in {
        '404.html', 'robots.txt', 'sitemap.xml', '.htaccess', '_redirects', 'readme.md'
    }:
        raise RestorationError('Путь совпадает со служебным файлом статического сайта: ' + route)
    if not raw_segments:
        return 'index.html'
    if path.endswith('/'):
        return '/'.join(raw_segments + ['index.html'])
    if re.search(r'\.[a-zA-Z0-9]{1,8}$', raw_segments[-1]):
        return '/'.join(raw_segments)
    return '/'.join(raw_segments + ['index.html'])


def _static_redirects(build):
    redirects = dict(build.seo_policy.get('redirects', {}))
    pages = []
    for page in build.pages:
        route = page.route
        parsed = urlsplit(route)
        if parsed.query or parsed.fragment:
            raise RestorationError('Статический HTML не может обслужить URL с параметрами. Задайте этой странице чистый путь в плане URL: ' + route)
        path = parsed.path or '/'
        if path == '/' or re.search(r'\.[a-zA-Z0-9]{1,8}$', path.rstrip('/')):
            pages.append((path, _static_file_for_route(route)))
            continue
        alternate = path.rstrip('/') if path.endswith('/') else path + '/'
        if build.seo_policy.get('url_style', 'slash') == 'slash':
            redirects.setdefault(alternate, path.rstrip('/') + '/')
        else:
            redirects.setdefault(alternate, path.rstrip('/'))
        pages.append((path, _static_file_for_route(route)))
    available = {urlsplit(route).path or '/' for route, _ in pages}
    for source, target in redirects.items():
        old = urlsplit(source)
        new = urlsplit(target)
        if old.query or old.fragment or new.query or new.fragment:
            raise RestorationError('Для HTML-сборки найдена переадресация со старого URL с параметрами. Задайте чистые пути в плане URL или выберите WordPress: ' + source)
        if old.scheme or old.netloc or new.scheme or new.netloc or not old.path.startswith('/') or not new.path.startswith('/'):
            raise RestorationError('HTML-редиректы должны использовать внутренние пути сайта: ' + source + ' → ' + target)
        _static_file_for_route(old.path)
        _static_file_for_route(new.path)
        canonical_target = new.path.rstrip('/') or '/'
        if canonical_target not in {path.rstrip('/') or '/' for path in available}:
            raise RestorationError('HTML-редирект ведёт на отсутствующую страницу: ' + source + ' → ' + target)
    return redirects, pages


def _static_rules(build, redirects, pages):
    rules = ['Options -MultiViews', 'DirectoryIndex index.html', 'ErrorDocument 404 /404.html', 'RewriteEngine On']
    netlify = []
    for source, target in sorted(redirects.items(), key=lambda pair: (-len(pair[0]), pair[0])):
        if source == target:
            continue
        source_path = urlsplit(source).path.lstrip('/')
        target_path = urlsplit(target).path or '/'
        pattern = re.escape(unquote(source_path)).replace(r'\ ', r'\x20')
        rules.append('RewriteRule ^' + pattern + '$ ' + target_path + ' [R=301,L,NE]')
        netlify.append(source + ' ' + target_path + ' 301!')
    for route, file_path in pages:
        path = urlsplit(route).path or '/'
        if path == '/' or re.search(r'\.[a-zA-Z0-9]{1,8}$', path.rstrip('/')):
            continue
        visible = path.rstrip('/')
        pattern = re.escape(unquote(visible.lstrip('/'))).replace(r'\ ', r'\x20')
        static_target = quote(file_path, safe='/-._~')
        rules.append('RewriteRule ^' + pattern + '/?$ ' + static_target + ' [END]')
        netlify.append(visible + ('/' if route.endswith('/') else '') + ' ' + static_target + ' 200!')
    return '\n'.join(rules) + '\n', '\n'.join(netlify) + '\n'


def _validate_static_zip(path: Path, build, expected_pages):
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names or len(names) != len(set(names)) or any(
                name.startswith('/') or '\\' in name or any(part in ('', '.', '..') for part in name.split('/'))
                for name in names
            ):
                raise RestorationError('HTML ZIP содержит повторяющиеся или небезопасные пути.')
            if any(name.lower().endswith(('.php', '.xml.gz')) or name.lower() in ('content.xml', 'site.json') for name in names):
                raise RestorationError('В HTML ZIP попали файлы WordPress или пакет импорта.')
            if any(name.endswith('.php') or name.startswith(('theme/', 'wp-content/')) for name in names):
                raise RestorationError('В HTML ZIP обнаружены файлы WordPress.')
            required = {'index.html', '404.html', 'robots.txt', 'sitemap.xml', '.htaccess', '_redirects', 'README.md'}
            if not required.issubset(set(names)):
                raise RestorationError('В HTML ZIP отсутствуют обязательные страницы или правила сайта.')
            missing = set(expected_pages) - set(names)
            if missing:
                raise RestorationError('В HTML ZIP не попали страницы: ' + ', '.join(sorted(missing)[:5]))
            for page_name in expected_pages:
                source = archive.read(page_name).decode('utf-8-sig', errors='replace')
                for meta in re.findall(r'(?is)<meta\b[^>]*>', source):
                    if re.search(r'(?i)\bnoindex\b', meta):
                        raise RestorationError('В статической странице сохранился запрет индексации: ' + page_name)
                for asset in re.findall(r'''(?i)(?:src|href|poster)\s*=\s*["'](/assets/[^"'?#]+)''', source):
                    if unquote(asset.lstrip('/')) not in names:
                        raise RestorationError('Не найден ресурс статической страницы: ' + asset)
            if archive.testzip() is not None:
                raise RestorationError('Контроль целостности HTML ZIP не пройден.')
            return {'passed': True, 'kind': 'static_html', 'pages': len(expected_pages), 'files': len(names),
                    'checks': ['html_routes', 'assets_included', '404_page', 'robots_and_sitemap',
                               'static_host_rules', 'no_wordpress_or_xml', 'safe_unique_paths', 'zip_crc']}
    except zipfile.BadZipFile as error:
        raise RestorationError('Повреждён архив статического сайта.') from error


def _package_static_html(build, destination, report, seo_report):
    from .seo_routes import robots, sitemap
    from .theme_identity import not_found_document
    redirects, routes = _static_redirects(build)
    theme_root = build.root / 'theme'
    assets_root = build.root / 'theme' / 'assets'
    pages_root = build.root / 'pages'
    if (theme_root.is_symlink() or not assets_root.is_dir() or assets_root.is_symlink()
            or pages_root.is_symlink() or not pages_root.is_dir()):
        raise RestorationError('Не найдены HTML-страницы или ресурсы статического сайта, либо обнаружена ссылка на внешний путь.')
    page_files = []
    for page in build.pages:
        source = build.root / 'pages' / (page.key + '.html')
        if source.is_symlink() or not source.is_file():
            raise RestorationError('Не найден HTML восстановленной страницы: ' + page.route)
        page_files.append((source, _static_file_for_route(page.route)))
    route_names = [target for _, target in page_files]
    folded = [name.casefold() for name in route_names]
    if len(folded) != len(set(folded)):
        raise RestorationError('Две страницы совпали по имени файла в ZIP. Измените URL в плане сайта.')
    if any(path.is_symlink() for folder in (build.root / 'pages', assets_root)
           for path in folder.rglob('*')):
        raise RestorationError('В файлах сайта обнаружена ссылка на внешний файл.')
    css_path = assets_root / 'site-404.css'
    css = css_path.read_text(encoding='utf-8') if css_path.is_file() else ''
    apache, netlify = _static_rules(build, redirects, routes)
    readme = (
        '# Статический сайт\n\n'
        'Распакуйте содержимое ZIP в корневую папку домена или статического хостинга. Главная страница — `index.html`; '
        'архивные и казино-страницы сохранены по выбранным URL; файлы оформления и изображений находятся в `assets/`.\n\n'
        '`robots.txt`, `sitemap.xml` и оформленная `404.html` уже включены. Правила 301 и маршрутизация сохранены в '
        '`.htaccess` для Apache и `_redirects` для Netlify/Cloudflare Pages. Для другого сервера перенесите правила из '
        'этих файлов в его конфигурацию. Размещение рассчитано на корень домена; при смене домена обновите canonical '
        'в страницах и адрес sitemap.\n'
    )
    destination = destination.with_suffix('.zip')
    if destination.exists():
        raise RestorationError('Файл уже существует. Выберите новое имя архива.')
    created = False
    try:
        with destination.open('xb') as output:
            created = True
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for source, name in page_files:
                    archive.write(source, name)
                for source in sorted(assets_root.rglob('*')):
                    if source.is_file():
                        archive.write(source, 'assets/' + source.relative_to(assets_root).as_posix())
                archive.writestr('404.html', not_found_document(build.request.origin, build.request.lang, css))
                archive.writestr('robots.txt', robots(build))
                archive.writestr('sitemap.xml', sitemap(build))
                archive.writestr('.htaccess', apache)
                archive.writestr('_redirects', netlify)
                archive.writestr('README.md', readme)
                if build.digest() != build.approved_digest:
                    raise RestorationError('Файлы изменились во время упаковки. Повторите просмотр.')
        install_checks = _validate_static_zip(destination, build, route_names)
        if build.digest() != build.approved_digest:
            raise RestorationError('Файлы изменились во время упаковки. Повторите просмотр.')
        report['output_format'] = 'static_html'
        report['static_package'] = install_checks
        receipt = {'approved_at': datetime.now(timezone.utc).isoformat(), 'digest': build.approved_digest,
                   'format': 'static-html-v1', 'archive': str(destination), 'pages': len(build.pages),
                   'owner_approvals': build.owner_approvals, 'install_checks': install_checks,
                   'sha256': {destination.name: hashlib.sha256(destination.read_bytes()).hexdigest()}}
        from .seo_contract import save_json
        save_json(build.root / 'approval.json', receipt)
        save_json(build.root / 'static-install-checks.json', install_checks)
        save_json(build.root / 'seo-checklist.json', seo_report)
        save_json(build.root / 'checks.json', report)
        return destination
    except Exception:
        if created and destination.is_file():
            destination.unlink()
        raise


def bundle_path(theme_zip: Path) -> Path:
    return theme_zip.with_name(theme_zip.stem + '-bundle.zip')


def content_path(theme_zip: Path) -> Path:
    return theme_zip.with_name(theme_zip.stem + '-content.xml')


def installation_instructions(theme_name='wordpress-theme.zip', content_name='content.xml'):
    return ('# Установка восстановленного сайта\n\n'
        f'1. WordPress → Внешний вид → Темы → Добавить тему → Загрузить тему: выберите `{theme_name}` и активируйте тему.\n'
        f'2. Инструменты → Импорт → WordPress: импортируйте `{content_name}` и назначьте страницы своему пользователю.\n'
        '3. Настройки → Чтение: выберите импортированную главную как статическую главную страницу.\n'
        '4. Импортированное меню «Primary Menu» назначается области «Основное меню» автоматически. Если на сайте уже были меню, после импорта обновите страницу; тема использует только элементы импортированного меню и сохраняет встроенный fallback, если импорт ещё не завершён.\n'
        '5. Настройки → Постоянные ссылки: проверьте, что отключён Plain. При активации и импорте тема автоматически включает «Название записи», если структура была пустой; существующая непустая структура сохраняется. Маршруты `/casino/<слаг>/` теперь разрешаются и до первого flush rewrite-правил, а после импорта тема сама публикует принадлежащие ей страницы. Если в админке показано сообщение о пропущенных или дублирующихся страницах, импортируйте XML из комплекта ровно один раз.\n'
        '6. Проверьте все архивные пути, три казино-страницы формата `/casino/<слаг>/` и /go/ на установленном домене. Превью не проверяет правила вашего хостинга. Если остаётся Apache 404 даже после импорта XML, проверьте WordPress-правила .htaccess и AllowOverride/mod_rewrite; для Nginx — передачу отсутствующих путей в index.php. Контент и метаданные казино редактируются владельцем; canonical уже задан.\n'
        '7. Для таблицы партнёров используйте блок «Шорткод»: `[casino_table id="88"]`. Высота конкретной таблицы переопределяется параметром `row_height`, например `[casino_table id="88" row_height="110"]`; допустимо целое значение от 72 до 240 пикселей. Общая ширина казино-контента, таблицы, колонок и отступы ячеек уже записаны в настройках сборки.\n\n'
        'Title и description восстановленных страниц соответствуют выбору на этапе метаданных. '
        'Казино редактируются в Gutenberg, метаданные — в блоке «SEO страницы» или SEO-плагине. '
        'Во «Внешний вид → Партнёрские ссылки» проверьте бренды, цепочки и параметры. '
        'Опубликованные казино уже открыты для индексации; отдельное разрешение в редакторе не требуется.\n\n'
        '## Текст статей\n\n'
        'Вставляйте текст из Google Docs в визуальный редактор обычным Ctrl+V. '
        'В документе используйте настоящие заголовки и списки: они сохраняются как редактируемые блоки WordPress. '
        'Тема автоматически оформляет заголовки, абзацы, списки, цитаты, ссылки, таблицы и изображения; '
        'эти же стили подключены в редакторе. Ctrl+Shift+V вставляет только текст и убирает структуру. '
        'Если статья содержит H1, отдельный заголовок из названия страницы не дублируется. '
        'При трёх и более H2 появляется сворачиваемое оглавление на языке сайта. '
        'Широкая таблица прокручивается внутри своего блока на телефоне. '
        'Содержимое статьи и партнёрские адреса оформление не изменяет.\n\n'
        'ZIP с суффиксом `-bundle.zip` — полный комплект и отчёты. Его не загружают в установщик тем. '
        'Тема устанавливается первым ZIP, страницы — через XML. Повторный импорт теперь безопасен: тема сама удаляет повторные пакетные пункты меню и сохраняет один раздел Casino.\n\n'
        'Тема использует API WordPress и не заменяет базу данных. Сохраняются пользователи, настройки и ранее существовавшие данные. '
        'Развёртывание рассчитано на WordPress в корне выбранного домена. '
        'Основной адрес, HTTPS и www тема берёт из WordPress → Настройки → Общие → Адрес сайта. '
        'Архивный HTTP-адрес не переопределяет настройки установленного сайта.\n\n'
        'Проверено с WordPress 7.0.4 и 7.1. Для PHP 8 используйте поддерживаемую ветку; WordPress рекомендует PHP 8.3+, '
        'MySQL 8.0+ или MariaDB 10.11+. Актуальные требования: https://wordpress.org/about/requirements/\n\n'
        'До индексации проверьте публичные HTTP/HTTPS и www/apex, TLS, robots.txt, sitemap.xml, настройки «Чтение», '
        'вывод SEO-плагина и партнёрские переходы. Все опубликованные страницы, включая казино, открыты для индексации и включены в sitemap сразу.\n')


def validate_theme_zip(path: Path):
    """Mandatory installer checks; accepting SEO findings cannot waive them."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if (not names or len(names) != len(set(names)) or any('\\' in n or n.startswith('/')
                    or any(part in ('', '.', '..') for part in n.rstrip('/').split('/')) for n in names)):
                raise RestorationError('ZIP темы содержит недопустимые или повторяющиеся пути.')
            roots = {n.split('/')[0] for n in names}
            if len(roots) != 1 or any('/' not in n for n in names):
                raise RestorationError('В установочном ZIP должна быть одна папка темы, без соседних страниц и отчётов.')
            prefix = next(iter(roots)) + '/'
            required = ('style.css', 'index.php', '404.php', 'functions.php', 'header.php', 'footer.php',
                        'page-template.php', 'casino-page.php', 'site.json', 'seo.php', 'widget.php')
            missing = [n for n in required if prefix + n not in names]
            if missing:
                raise RestorationError('Тему нельзя установить: отсутствует ' + ', '.join(missing) + '.')
            style = archive.read(prefix + 'style.css')[:8192].decode('utf-8-sig', errors='replace')
            if not re.search(r'(?im)^[ \t/*#@]*Theme Name:\s*\S', style):
                raise RestorationError('В style.css отсутствует обязательный заголовок Theme Name.')
            site = json.loads(archive.read(prefix + 'site.json'))
            if not isinstance(site, dict):
                raise RestorationError('В ZIP отсутствует корректное описание сайта.')
            identity = site.get('theme') if isinstance(site.get('theme'), dict) else {}
            if identity.get('slug') != prefix.rstrip('/'):
                raise RestorationError('Имя папки темы не соответствует домену из описания сайта.')
            expected_name = identity.get('name', '')
            declared_name = re.search(r'(?im)^[ \t/*#@]*Theme Name:\s*(.+?)\s*$', style)
            if not expected_name or not declared_name or declared_name.group(1) != expected_name:
                raise RestorationError('Название темы в style.css не соответствует домену сайта.')
            brand = re.compile(r'drop[\s_-]*restorer', re.I)
            text_extensions = {'.css', '.js', '.json', '.php', '.txt', '.xml', '.md'}
            branded = []
            for name in names:
                if brand.search(name):
                    branded.append(name)
                    continue
                if Path(name).suffix.lower() in text_extensions:
                    value = archive.read(name).decode('utf-8-sig', errors='replace')
                    if brand.search(value):
                        branded.append(name)
            if branded:
                raise RestorationError('В теме осталось служебное название инструмента: ' + ', '.join(branded[:5]) + '.')
            if site.get('article_formatting'):
                article_files = ('article.php', 'assets/dr-article.css', 'assets/dr-article.js', 'assets/dr-article-editor.css')
                missing = [n for n in article_files if prefix + n not in names or not archive.read(prefix + n).strip()]
                if missing:
                    raise RestorationError('Не упаковано оформление статей: ' + ', '.join(missing) + '.')
            if archive.testzip() is not None:
                raise RestorationError('Контроль целостности ZIP темы не пройден.')
            return {'passed': True, 'kind': 'wordpress_theme', 'theme_folder': prefix.rstrip('/'),
                    'stylesheet': prefix + 'style.css', 'files': len(names),
                    'checks': ['single_theme_directory', 'required_files', 'designed_404', 'domain_theme_identity',
                               'no_tool_branding', 'theme_name_header', 'safe_unique_paths', 'zip_crc']}
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError) as error:
        raise RestorationError('Повреждён установочный ZIP темы.') from error


def approve(build, viewed_digest: str, *, accept_findings=False):
    if build.status != 'ready':
        raise RestorationError('Сначала завершите проверку метаданных и сборку темы.')
    if not viewed_digest or viewed_digest != build.digest():
        raise RestorationError('Проект изменился после открытия превью. Откройте актуальное превью и проверьте результат.')
    from .checklist import inspect_site, approval_snapshot, require_pass as require_checklist
    if accept_findings is True:
        seo_report=inspect_site(build)
        report=audit(build)
        if viewed_digest!=build.digest():
            raise RestorationError('Проект изменился во время проверки. Откройте актуальное превью.')
        build.owner_approvals['package']={**approval_snapshot(seo_report),'digest':viewed_digest,
            'audit_errors':report['errors'],'audit_warnings':report['warnings']}
    else:
        require_checklist(build)
        require_pass(build)
        build.owner_approvals.pop('package',None)
    build.approved_digest = viewed_digest
    build.save()


def package(build, destination: Path) -> Path:
    if build.status != 'ready':
        raise RestorationError('Сначала завершите проверку метаданных и сборку темы.')
    if not build.approved_digest or build.approved_digest != build.digest():
        raise RestorationError('Перед упаковкой необходимо визуально одобрить текущий результат.')
    from .checklist import inspect_site, require_pass as require_checklist
    from .seo_contract import fingerprint, save_json
    owner=build.owner_approvals.get('package',{})
    accepted=(owner.get('by')=='owner' and owner.get('digest')==build.approved_digest
              and owner.get('fingerprint')==fingerprint(build))
    seo_report = inspect_site(build) if accepted else require_checklist(build)
    report = audit(build) if accepted else require_pass(build)
    if build.request.output_format == 'static_html':
        return _package_static_html(build, destination, report, seo_report)
    destination = destination.with_suffix('.zip')
    bundle = bundle_path(destination)
    content = content_path(destination)
    if any(p.exists() for p in (destination, bundle, content)):
        raise RestorationError('Файл уже существует. Выберите новое имя архива.')
    identity = theme_identity(build.request.origin)
    selected = []
    for folder in ('theme', 'pages', 'preview_screenshots'):
        for path in (build.root / folder).rglob('*'):
            if path.is_symlink():
                raise RestorationError('В проекте обнаружена ссылка на внешний файл.')
            if path.is_file():
                selected.append(path)
    selected.append(build.root / 'content.xml')
    for path in (build.root / 'theme', build.root / 'pages', build.root / 'preview_screenshots', build.root / 'content.xml'):
        if path.is_symlink():
            raise RestorationError('В проекте обнаружена ссылка на внешний файл.')
    created = []
    try:
        with destination.open('xb') as output:
            created.append(destination)
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for path in selected:
                    if path.is_relative_to(build.root / 'theme'):
                        archive.write(path, identity.slug + '/' + path.relative_to(build.root / 'theme').as_posix())
        install_checks = validate_theme_zip(destination)
        with content.open('xb') as output:
            created.append(content)
            output.write((build.root / 'content.xml').read_bytes())
        with bundle.open('xb') as output:
            created.append(bundle)
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for path in selected:
                    archive.write(path, path.relative_to(build.root).as_posix())
                archive.write(destination, identity.slug + '.zip')
                archive.writestr('README.md', installation_instructions(identity.slug + '.zip'))
                if build.digest() != build.approved_digest:
                    raise RestorationError('Файлы изменились во время упаковки. Повторите просмотр.')
                archive.writestr('checks.json', json.dumps(report, ensure_ascii=False, indent=2))
                archive.writestr('seo-checklist.json', json.dumps(seo_report, ensure_ascii=False, indent=2))
                archive.writestr('owner-approvals.json', json.dumps(build.owner_approvals, ensure_ascii=False, indent=2))
                archive.writestr('install-checks.json', json.dumps(install_checks, ensure_ascii=False, indent=2))
        if build.digest() != build.approved_digest:
            raise RestorationError('Файлы изменились во время упаковки. Повторите просмотр.')
        receipt = {'approved_at': datetime.now(timezone.utc).isoformat(), 'digest': build.approved_digest,
                   'format': 'wordpress-theme-v1', 'archive': str(destination), 'bundle': str(bundle), 'content': str(content),
                   'pages': len(build.pages), 'owner_approvals':build.owner_approvals, 'install_checks': install_checks,
                   'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in created}}
        save_json(build.root / 'approval.json', receipt)
        return destination
    except Exception:
        for path in created:
            if path.is_file():
                path.unlink()
        raise
