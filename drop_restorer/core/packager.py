from __future__ import annotations

import json
import hashlib
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .models import RestorationError
from .audit import audit, require_pass
from .theme_identity import theme_identity


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
    destination = destination.with_suffix('.zip')
    bundle = bundle_path(destination)
    content = content_path(destination)
    if any(p.exists() for p in (destination, bundle, content)):
        raise RestorationError('Файл уже существует. Выберите новое имя архива.')
    report = audit(build) if accepted else require_pass(build)
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
