"""One background renderer for build cards; no changes to approved site files."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading

from PIL import Image

from ..core.library import inventory, is_link, load_card, project_directory
from ..core.models import RestorationError
from ..runtime import python_executable, workspace_root


class Thumbnails:
    def __init__(self, root, busy=lambda: False):
        self.root = root
        self.busy = busy
        self.guard = threading.RLock()
        self.work = threading.Lock()
        self.queue = queue.Queue()
        self.pending = set()
        self.blocked = set()
        self.stopped = threading.Event()
        self.child = None
        self.active = None
        self.thread = None

    def fingerprint(self, root, card):
        home = card['home']
        digest = hashlib.sha256(json.dumps(home, ensure_ascii=False, sort_keys=True).encode())
        for path in (root / 'pages', root / 'theme', root / 'theme/assets', root / 'card-preview'):
            if path.exists() and is_link(path):
                raise RestorationError('Каталог ресурсов является ссылкой.')
        targets = [root / 'pages' / (str(home.get('key', '')) + '.html')]
        targets += sorted((root / 'theme/assets').rglob('*'))
        for path in targets:
            if path.exists() and is_link(path):
                raise RestorationError('Ресурс сборки является ссылкой.')
            if path.is_file():
                info = path.stat()
                digest.update(f'{path.relative_to(root).as_posix()}:{info.st_size}:{info.st_mtime_ns}'.encode())
        return digest.hexdigest()

    def ensure(self, root, card):
        if not isinstance(card['home'].get('html'), str) or not card['home']['html'] or not re.fullmatch(r'[A-Za-z0-9_-]+', str(card['home'].get('key', ''))):
            return {'status': 'unavailable', 'message': 'Главная ещё не загружена'}
        version = self.fingerprint(root, card)
        try:
            saved = json.loads((root / 'card-preview/preview.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = {}
        image = root / 'card-preview/homepage.jpg'
        if saved.get('version') == version:
            if saved.get('status') == 'ready' and image.is_file():
                return {'status': 'ready', 'url': '/project-thumbnails/' + root.name + '?v=' + version[:16]}
            if saved.get('status') == 'error':
                return {'status': 'error', 'message': 'Снимок пока недоступен'}
        with self.guard:
            if root.name not in self.pending and root.name not in self.blocked and not self.stopped.is_set():
                self.pending.add(root.name)
                self.queue.put(root.name)
                if self.thread is None:
                    self.thread = threading.Thread(target=self.run, daemon=True, name='drop-restorer-card-previews')
                    self.thread.start()
        return {'status': 'pending', 'message': 'Готовим снимок главной…'}

    def render(self, root, card, version):
        inventory(root)
        folder = root / 'card-preview'
        folder.mkdir(exist_ok=True)
        key = card['home']['key']
        home_file = root / 'pages' / (key + '.html')
        modified = home_file.stat().st_mtime if home_file.is_file() else (root / 'project.json').stat().st_mtime
        candidates = [root / name / ('desktop_' + key + '.png') for name in ('preview_screenshots', 'checklist-screenshots')]
        candidates = [p for p in candidates if p.is_file() and p.stat().st_mtime >= modified]
        if candidates:
            source = max(candidates, key=lambda p: p.stat().st_mtime)
        else:
            source = folder / 'source.png'
            executable = python_executable(workspace_root(__file__))
            with (folder / 'capture.log').open('w', encoding='utf-8') as log:
                with self.guard:
                    if root.name in self.blocked or self.stopped.is_set():
                        return
                    self.child = subprocess.Popen([str(executable), '-X', 'utf8', '-m', 'drop_restorer.web.capture', str(root), '--key', key, '--cover'],
                        cwd=workspace_root(__file__), stdout=log, stderr=log,
                        env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu'),
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    child = self.child
                try:
                    if child.wait(timeout=45):
                        raise RestorationError('Не удалось создать снимок главной.')
                finally:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=10)
                    with self.guard:
                        self.child = None
        if self.fingerprint(root, load_card(root)) != version:
            return
        with Image.open(source) as original:
            if original.width < 100 or original.height < 60:
                raise RestorationError('Некорректный размер снимка.')
            image = original.convert('RGB')
            image.thumbnail((960, 540), Image.Resampling.LANCZOS)
            temporary = folder / 'homepage.tmp'
            image.save(temporary, format='JPEG', quality=86, optimize=True)
            temporary.replace(folder / 'homepage.jpg')
        (folder / 'preview.json').write_text(json.dumps({'version': version, 'status': 'ready'}), encoding='utf-8')

    def run(self):
        while not self.stopped.is_set():
            try:
                identifier = self.queue.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                while self.busy() and not self.stopped.wait(.2):
                    pass
                with self.work:
                    with self.guard:
                        if self.stopped.is_set() or identifier in self.blocked:
                            continue
                        self.active = identifier
                    root = project_directory(self.root, identifier)
                    card = load_card(root)
                    version = self.fingerprint(root, card)
                    try:
                        self.render(root, card, version)
                    except (OSError, ValueError, KeyError, TypeError, RestorationError, subprocess.SubprocessError):
                        with self.guard:
                            if not self.stopped.is_set() and identifier not in self.blocked and (root / 'card-preview').is_dir():
                                (root / 'card-preview/preview.json').write_text(json.dumps({'version': version, 'status': 'error'}), encoding='utf-8')
            except (OSError, ValueError, KeyError, TypeError, RestorationError):
                pass
            finally:
                with self.guard:
                    self.active = None
                    self.pending.discard(identifier)
                self.queue.task_done()

    def cancel(self, identifier):
        with self.guard:
            self.blocked.add(identifier)
            wait = self.active == identifier
            if self.active == identifier and self.child is not None and self.child.poll() is None:
                self.child.terminate()
        # A renderer cannot recreate this directory after removal.
        if wait:
            with self.work:
                pass

    def close(self):
        self.stopped.set()
        with self.guard:
            if self.child is not None and self.child.poll() is None:
                self.child.terminate()
        if self.thread:
            self.thread.join(timeout=12)
