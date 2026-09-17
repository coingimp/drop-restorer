"""Saved build identities, card metadata and bounded directory removal."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from urllib.parse import urlsplit

from .models import RestorationError, Snapshot, site_origin

RUN_NAME = re.compile(r'\d{8}-\d{6}-[A-Za-z0-9_-]+')
RESERVED = {'development-qa', 'backups', 'backup', 'source', 'theme', 'pages'}


def is_link(path: Path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def project_directory(base: Path, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', identifier) or identifier in RESERVED:
        raise RestorationError('Некорректный идентификатор сборки.')
    base = base.resolve()
    original = base / identifier
    if not original.is_dir() or is_link(original):
        raise RestorationError('Сохранённая сборка не найдена или её папка является ссылкой.')
    root = original.resolve()
    if root.parent != base:
        raise RestorationError('Папка сборки находится вне каталога проектов.')
    marker = root / 'project.json'
    if marker.exists() and is_link(marker):
        raise RestorationError('Файл сборки является ссылкой.')
    if not marker.is_file() and not RUN_NAME.fullmatch(identifier):
        raise RestorationError('Сохранённая сборка не найдена.')
    return root


def load_card(root: Path):
    try:
        data = json.loads((root / 'project.json').read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    pages = [p for p in data.get('pages', []) if isinstance(p, dict)] if isinstance(data.get('pages'), list) else []
    home = next((p for p in pages if p.get('route') == '/'), next((p for p in pages if not p.get('casino')), {}))
    request = data.get('request') if isinstance(data.get('request'), dict) else {}
    origin = ''
    try:
        target = request.get('final_domain')
        if not target:
            parsed = urlsplit(Snapshot.parse(request.get('main_page', '')).original)
            target = parsed.scheme + '://' + parsed.netloc
        origin = site_origin(target)
    except (RestorationError, ValueError, TypeError, AttributeError):
        pass
    title = str(home.get('seo_title') or home.get('title') or origin or 'Незавершённая сборка').strip()
    status = data.get('status', 'incomplete') if request else 'incomplete'
    return {'id': root.name, 'status': status, 'title': title, 'pages': len(pages), 'origin': origin,
            'url': origin + str(home.get('route') or '/') if origin else '',
            'can_open': bool(request), 'home': home}


def inventory(root: Path):
    """Never follow Windows junctions or symlinks while counting or deleting."""
    total = count = 0
    stamp = hashlib.sha256()
    def inaccessible(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=inaccessible):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            if is_link(path):
                raise RestorationError('В папке сборки обнаружена ссылка на другой путь. Удаление остановлено.')
            info = path.stat()
            stamp.update(f'{path.relative_to(root).as_posix()}:{info.st_size}:{info.st_mtime_ns}'.encode())
            if path.is_file():
                total += info.st_size
                count += 1
    return {'files': count, 'bytes': total, 'version': stamp.hexdigest()}


def remove_project(base: Path, identifier):
    root = project_directory(base, identifier)
    inventory(root)
    # Both the resolved absolute target and every descendant were checked above.
    shutil.rmtree(root)
    if root.exists():
        raise RestorationError('Не удалось удалить всю папку сборки. Повторите удаление.')
