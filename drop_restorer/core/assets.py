from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import tinycss2
from bs4 import BeautifulSoup

from .models import RestorationError, Snapshot
from .cleanup_policy import TRACKER, WIDGET, script_problem


def original_url(value: str, base: str) -> str:
    value = value.strip()
    if value.startswith('//web.archive.org/'):
        value = 'https:' + value
    # /web/ can also be a real directory on the donor (e.g. /web/_css/).
    # Treat it as a replay wrapper only when a timestamp and original URL follow.
    replay_path = r'/web/\d{1,14}(?:[a-z]{1,3}_)?/(https?://.+)'
    if re.match('^' + replay_path, value):
        value = 'https://web.archive.org' + value
    if urlsplit(value).hostname == 'web.archive.org':
        match = re.search(replay_path, value)
        if match:
            value = match.group(1)
    return urljoin(base, value)




class AssetDownloader:
    def __init__(self, root: Path, client, warn, cached_only=False, activity=None, log=None):
        self.root, self.client, self.warn = root, client, warn
        self.cache: dict[tuple[str, str], str] = {}
        self.records: list[dict] = []
        self.cached_only = cached_only
        self.activity = activity or (lambda event: None)
        self.log = log or (lambda message: None)
        self.stats = {'attempted': 0, 'downloaded': 0, 'cached': 0, 'skipped': 0, 'bytes': 0}
        self.counted = set()
        self.page = {'page': 0, 'total': 0, 'route': ''}
        self.removed = []
        manifest = root / 'asset-manifest.json'
        if manifest.exists():
            self.records = json.loads(manifest.read_text(encoding='utf-8'))
            for record in self.records:
                self.cache[(record['snapshot'], record['source'])] = record['local']
        # Legacy checkpoints may already contain downloaded trackers under
        # innocent local hashes. Inspect bytes as well as provenance.
        sources = {row['local']: row['source'] for row in self.records}
        for local in (root / 'theme' / 'assets').rglob('*.js'):
            if local.is_symlink() or not local.resolve().is_relative_to((root / 'theme' / 'assets').resolve()):
                raise RestorationError('Недопустимый путь ресурса в сохранённой сборке.')
            path = '/' + local.relative_to(root / 'theme').as_posix()
            reason = script_problem(local.read_text(encoding='utf-8', errors='replace'), sources.get(path, ''))
            if reason:
                local.unlink()
                self.removed.append({'kind': 'script', 'detail': reason, 'asset': path})
                for key, value in list(self.cache.items()):
                    if value == path:
                        self.cache[key] = ''
                self.records = [row for row in self.records if row['local'] != path]

    def set_page(self, number: int, total: int, route: str):
        self.page = {'page': number, 'total': total, 'route': route}
        self._activity('page', '')

    @staticmethod
    def _size(value: int) -> str:
        if value < 1024:
            return f'{value} B'
        if value < 1024 * 1024:
            return f'{value / 1024:.1f} KB'
        return f'{value / 1024 / 1024:.1f} MB'

    def _activity(self, status: str, source: str, **extra):
        resource = self.client.safe_url(source) if source and hasattr(self.client, 'safe_url') else source
        self.activity({**self.page, **self.stats, 'status': status, 'resource': resource or '', **extra})

    def fetch(self, value: str, snapshot: Snapshot, kind: str = '', css_depth: int = 1) -> str:
        if not value or value.startswith('#'):
            return value
        if value.startswith('data:'):
            return value if re.match(r'data:(image/|font/|application/(?:font|x-font|vnd.ms-fontobject))', value, re.I) else ''
        original = original_url(value, snapshot.original)
        parsed = urlsplit(original)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or TRACKER.search(original) or WIDGET.search(original):
            return ''
        original = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))
        key = (snapshot.timestamp, original)
        if key in self.cache:
            status = 'cached' if self.cache[key] else 'skipped'
            if key not in self.counted:
                self.counted.add(key)
                self.stats[status] += 1
            self._activity(status, original, kind=kind or 'file')
            return self.cache[key]
        ext = Path(parsed.path).suffix.lower()
        allowed = {'.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.otf', '.eot', '.mp4', '.webm', '.mp3', '.ogg', '.wav', '.pdf'}
        ext = ext if ext in allowed else {'css': '.css', 'js': '.js', 'image': '.png'}.get(kind, '.bin')
        folder = 'css' if kind == 'css' or ext == '.css' else 'js' if kind == 'js' or ext == '.js' else 'media'
        path = f'/assets/{folder}/{hashlib.sha256((snapshot.timestamp + original).encode()).hexdigest()[:24]}{ext}'
        if self.cached_only:
            local = self.root / 'theme' / path.lstrip('/')
            if local.is_file():
                self.cache[key] = path
                self.counted.add(key)
                self.records.append({'source': original, 'snapshot': snapshot.timestamp, 'local': path,
                                     'sha256': hashlib.sha256(local.read_bytes()).hexdigest(),
                                     'recovered_from_cache': True})
                self.stats['cached'] += 1
                self._activity('cached', original, kind=kind or folder)
                return path
            self.cache[key] = ''
            self.counted.add(key)
            self.stats['skipped'] += 1
            self._activity('skipped', original, kind=kind or folder, detail='Файл отсутствует в кэше.')
            self.warn(f'Ресурс отсутствует среди скачанных: {parsed.path or parsed.hostname}.')
            return ''
        self.cache[key] = ''
        self.counted.add(key)
        self.stats['attempted'] += 1
        self._activity('downloading', original, kind=kind or folder, request_bytes=0)
        started = time.monotonic()
        try:
            data, content_type = self.client.get(f'https://web.archive.org/web/{snapshot.timestamp}id_/{original}')
            if 'text/html' in content_type.lower() or data.lstrip()[:60].lower().startswith((b'<!doctype html', b'<html')):
                raise RestorationError('Архив вернул HTML вместо ресурса.')
            self.cache[key] = path
            source_sha = hashlib.sha256(data).hexdigest()
            if folder == 'js':
                reason = script_problem(data.decode('utf-8', errors='replace'), original)
                if reason:
                    self.cache[key] = ''
                    self.removed.append({'kind': 'script', 'detail': reason, 'asset': path})
                    return ''
            if folder == 'css':
                rules, encoding = tinycss2.parse_stylesheet_bytes(data)
                self._css_tokens(rules, Snapshot(snapshot.timestamp, original), css_depth)
                data = tinycss2.serialize(rules).replace('/assets/', '../').encode('utf-8')
            local = self.root / 'theme' / path.lstrip('/')
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            self.stats['downloaded'] += 1
            self.stats['bytes'] += len(data)
            self.records.append({'source': original, 'snapshot': snapshot.timestamp, 'local': path,
                                 'source_sha256': source_sha, 'sha256': hashlib.sha256(data).hexdigest()})
            elapsed = max(time.monotonic() - started, .001)
            self._activity('downloaded', original, kind=kind or folder, resource_bytes=len(data),
                           resource_seconds=round(elapsed, 2), speed_bps=round(len(data) / elapsed))
            safe = self.client.safe_url(original) if hasattr(self.client, 'safe_url') else original
            self.log(f'RESOURCE: скачан {self.stats["downloaded"]} · {self._size(len(data))} · '
                     f'{elapsed:.2f} с · {safe}')
            return path + (('#' + parsed.fragment) if parsed.fragment else '')
        except RestorationError as error:
            self.client.check_cancel()
            self.cache[key] = ''
            self.stats['skipped'] += 1
            self._activity('skipped', original, kind=kind or folder, detail=str(error))
            self.warn(f'Ресурс недоступен: {parsed.path or parsed.hostname}. {error}')
            return ''

    def _css_tokens(self, tokens, snapshot, depth):
        for token in tokens:
            if token.type == 'at-rule' and token.lower_at_keyword == 'import':
                for child in token.prelude:
                    if child.type == 'string':
                        value = self.fetch(child.value, snapshot, 'css', depth - 1) if depth > 0 else ''
                        child.value, child.representation = value, json.dumps(value)
                self._css_tokens(token.prelude, snapshot, depth)
            elif token.type == 'url':
                value = self.fetch(token.value, snapshot, css_depth=depth - 1) if depth > 0 else ''
                token.value, token.representation = value, 'url(' + json.dumps(value) + ')'
            elif token.type == 'function' and token.lower_name == 'url':
                value = tinycss2.serialize(token.arguments).strip().strip('\"\'')
                value = self.fetch(value, snapshot, css_depth=depth - 1) if depth > 0 else ''
                token.arguments = tinycss2.parse_component_value_list(json.dumps(value))
            else:
                for name in ('prelude', 'content', 'arguments'):
                    children = getattr(token, name, None)
                    if children is not None:
                        self._css_tokens(children, snapshot, depth)

    def css(self, source: str, snapshot: Snapshot) -> str:
        tokens = tinycss2.parse_component_value_list(source)
        self._css_tokens(tokens, snapshot, 1)
        return tinycss2.serialize(tokens)

    def localize(self, soup: BeautifulSoup, snapshot: Snapshot):
        base = soup.find('base', href=True)
        if base:
            snapshot = Snapshot(snapshot.timestamp, original_url(base['href'], snapshot.original))
        for node in soup.find_all('base'):
            node.decompose()
        for node in soup.find_all(True):
            if node.name == 'script' and node.get('src'):
                local = self.fetch(node['src'], snapshot, 'js')
                if not local:
                    node.decompose()
                    continue
                node['src'] = local
                node.attrs.pop('integrity', None)
                node.attrs.pop('crossorigin', None)
            elif node.name == 'link':
                rel = set(node.get('rel', []))
                if rel & {'stylesheet', 'icon', 'apple-touch-icon', 'mask-icon'}:
                    local = self.fetch(node.get('href', ''), snapshot, 'css' if 'stylesheet' in rel else 'image')
                    if local:
                        node['href'] = local
                        node.attrs.pop('integrity', None)
                        node.attrs.pop('crossorigin', None)
                    else:
                        node.decompose()
                        continue
                elif rel & {'preload', 'prefetch', 'preconnect', 'dns-prefetch', 'modulepreload', 'manifest'}:
                    node.decompose()
                    continue
            else:
                for attr in ('src', 'poster', 'background', 'data-src', 'data-lazy-src'):
                    if node.get(attr):
                        value = self.fetch(node[attr], snapshot, 'image' if node.name in ('img', 'input') or attr == 'poster' else '')
                        if value:
                            node[attr] = value
                            if node.name == 'img' and attr in ('data-src', 'data-lazy-src'):
                                node['src'] = value
                        else:
                            node.attrs.pop(attr, None)
                for attr in ('srcset', 'data-srcset'):
                    if node.get(attr) and not node[attr].startswith('data:'):
                        items = []
                        for candidate in node[attr].split(','):
                            parts = candidate.strip().split()
                            if parts:
                                local = self.fetch(parts[0], snapshot, 'image')
                                if local:
                                    items.append(' '.join([local, *parts[1:]]))
                        node[attr] = ', '.join(items)
            if node.name and node.get('style'):
                node['style'] = self.css(node['style'], snapshot)
            if node.name == 'style':
                node.string = self.css(node.get_text(), snapshot)

    def save(self):
        (self.root / 'asset-manifest.json').write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding='utf-8')
