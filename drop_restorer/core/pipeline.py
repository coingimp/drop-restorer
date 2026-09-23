from __future__ import annotations

import hashlib
import json
import shutil
import threading
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from ..agents.client import AgentRequestError
from .assets import AssetDownloader
from .branding import branding
from .cleaner import (casino_shell, clean, clean_links, deactivate_bottom_menu_links,
                      navigation, parse_html, set_seo)
from .downloader import ArchiveClient
from .metadata import normalize
from .layout import repair_layout
from .models import Build, Page, RestoreRequest, RestorationError, route_for
from .review import can_apply, content_node, initialize_review, page_label, require_review, save_review
from .wordpress import write_site
from .audit import audit
from .usage import initialize as initialize_usage, metered
from .seo_contract import save_json


def validate_download(raw):
    soup = parse_html(raw)
    if not soup.body.get_text(strip=True):
        raise RestorationError('Архив вернул страницу без текста.')
    if soup.title and 'wayback machine' in soup.title.get_text().lower():
        raise RestorationError('Wayback вернул служебную страницу вместо снимка сайта. Проверьте выбранную ссылку.')
    return soup


class Pipeline:
    def __init__(self, output_root: Path, progress=None, log=None, cancel=None, agent=None, client=None, activity=None):
        self.output_root = output_root
        self.progress = progress or (lambda value, message: None)
        self.on_log = log or (lambda message: None)
        self.on_activity = activity or (lambda event: None)
        self.cancel = cancel or threading.Event()
        self.agent = agent
        self.client = client or ArchiveClient(self.cancel, on_event=lambda message: self.log('WARNING: ' + message),
                                               on_activity=self.network_activity)
        if client is not None and hasattr(client, 'on_activity'):
            client.on_activity = self.network_activity
        self.build = None

    def log(self, message: str):
        line = f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}'
        self.on_log(line)
        if self.build:
            with (self.build.root / 'run.log').open('a', encoding='utf-8') as output:
                output.write(line + '\n')

    def warn(self, message: str):
        self.build.warnings.append(message)
        self.log('WARNING: ' + message)

    def stage(self, value: int, message: str):
        self.client.check_cancel()
        self.progress(value, message)
        self.log('INFO: ' + message)

    def network_activity(self, event):
        self.on_activity({'scope': 'network', **event})

    def asset_activity(self, event):
        self.on_activity({'scope': 'asset', **event})

    def new_build(self, request):
        root = self.output_root / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        root.mkdir(parents=True, exist_ok=False)
        self.build = Build(root, request, status='downloading')
        initialize_usage(root)
        self.build.save()
        return self.build

    def run(self, request: RestoreRequest) -> Build:
        request.validate()
        build = self.new_build(request)
        (build.root / 'source').mkdir()
        return self.prepare(build)

    def open_saved(self, root: Path) -> Build:
        """Resume a checkpoint, or copy a legacy fully downloaded run for review."""
        try:
            saved = Build.load(root)
            if saved.status == 'metadata_review':
                require_review(saved)
                return saved
            snapshots = saved.request.validate()
            complete = True
            for number, snapshot in enumerate(snapshots):
                route = '/' if number == 0 else route_for(snapshot.original)
                key = hashlib.sha256(route.encode()).hexdigest()[:16]
                try:
                    validate_download((root / 'source' / (key + '.html')).read_bytes())
                except (OSError, RestorationError):
                    complete = False
            build = self.new_build(saved.request)
            shutil.copytree(root / 'source', build.root / 'source')
            if (root / 'theme' / 'assets').exists():
                shutil.copytree(root / 'theme' / 'assets', build.root / 'theme' / 'assets')
            if (root / 'asset-manifest.json').exists():
                shutil.copy2(root / 'asset-manifest.json', build.root / 'asset-manifest.json')
            if (root / 'source-manifest.json').exists():
                shutil.copy2(root / 'source-manifest.json', build.root / 'source-manifest.json')
            self.log('INFO: Продолжаем из сохранённых файлов; оригинал сохранён. ' +
                     ('Все исходники доступны локально.' if complete else 'Недостающие страницы будут скачаны повторно.'))
            return self.prepare(build, cached_only=complete, reuse_sources=True)
        finally:
            self.client.close()

    def prepare(self, build, cached_only=False, reuse_sources=False):
        request = build.request
        snapshots = request.validate()
        downloader = AssetDownloader(build.root, self.client, self.warn, cached_only=cached_only,
                                     activity=self.asset_activity, log=self.log)
        build.cleanup.extend(downloader.removed)
        downloader.removed.clear()
        source_manifest = build.root / 'source-manifest.json'
        try:
            sources = json.loads(source_manifest.read_text(encoding='utf-8')) if source_manifest.exists() else {}
            if not isinstance(sources, dict):
                raise ValueError('Invalid manifest')
        except (OSError, ValueError) as error:
            raise RestorationError('Повреждён список исходных страниц сборки.') from error
        try:
            for number, snapshot in enumerate(snapshots):
                self.stage(5 + round(number / len(snapshots) * 45), f'Подготовка скачанной страницы {number + 1} из {len(snapshots)}' if cached_only else f'Загрузка страницы {number + 1} из {len(snapshots)}')
                route = '/' if number == 0 else route_for(snapshot.original)
                downloader.set_page(number + 1, len(snapshots), route)
                key = hashlib.sha256(route.encode()).hexdigest()[:16]
                raw_path = build.root / 'source' / (key + '.html')
                raw = None
                if cached_only or reuse_sources:
                    try:
                        candidate = raw_path.read_bytes()
                        validate_download(candidate)
                        if key in sources and sources[key]['sha256'] != hashlib.sha256(candidate).hexdigest():
                            raise RestorationError('Контрольная сумма исходной страницы не совпадает.')
                        raw = candidate
                        self.log('CACHE: исходная страница сохранена: ' + route)
                    except (OSError, KeyError, RestorationError):
                        if cached_only:
                            raise RestorationError('Исходная страница отсутствует или повреждена: ' + route)
                if raw is None:
                    raw, mime = self.client.get(snapshot.archive_url, limit=15_000_000)
                    if mime and not any(x in mime.lower() for x in ('html', 'text/plain', 'octet-stream')):
                        raise RestorationError('Архивная ссылка должна вести на HTML-страницу.')
                    validate_download(raw)
                    temporary = raw_path.with_suffix('.tmp')
                    temporary.write_bytes(raw)
                    temporary.replace(raw_path)
                soup = validate_download(raw)
                sources[key] = {'url':snapshot.archive_url,'sha256':hashlib.sha256(raw).hexdigest()}
                save_json(source_manifest, sources)
                page = Page(key, route, page_label(soup, urlsplit(snapshot.original).hostname), '', snapshot.archive_url)
                build.pages.append(page)
                removed = []
                clean(soup, request.lang, removed)
                build.cleanup.extend(dict(row, route=page.route) for row in removed)
                downloader.localize(soup, snapshot)
                build.cleanup.extend(downloader.removed)
                downloader.removed.clear()
                page.html = str(soup)
                downloader.save()
                build.save()
            if not parse_html(build.pages[0].html).select_one('link[rel~="icon"]'):
                downloader.fetch('/favicon.ico', snapshots[0], 'image')
            downloader.save()
            initialize_review(build)
            self.stage(55, 'Страницы скачаны. Проверьте метаданные и выберите, как продолжить.')
            return build
        except Exception as error:
            self.log('ERROR: Восстановление остановлено. Финальный архив не создан.')
            if isinstance(error, RestorationError):
                self.log('ERROR: ' + str(error))
            build.status = 'download_failed'
            build.save()
            save_json(build.root / 'download-error.json', {'stage':'download','error':str(error),
                      'completed_pages':len([p for p in build.pages if p.html]),'planned_pages':len(snapshots),
                      'can_resume':True})
            raise
        finally:
            self.client.close()

    def suggest_metadata(self, pending: Build) -> Build:
        require_review(pending)
        if not self.agent:
            raise RestorationError('Подключите агента для подготовки вариантов метаданных.')
        self.build = build = deepcopy(pending)
        stopped = False
        try:
            for number, page in enumerate(build.pages):
                self.client.check_cancel()
                self.stage(55, f'Агент готовит метаданные: {number + 1} из {len(build.pages)}')
                row = build.metadata_review[page.key]
                row.pop('proposed', None)
                row.pop('error', None)
                try:
                    with metered(self.agent, build.root):
                        generated = self.agent.metadata(page.title, normalize(content_node(parse_html(page.html)).get_text(' ', strip=True)), build.request.lang)
                    row['proposed'] = {field: normalize(generated.get(field)) for field in ('title', 'description')}
                except RestorationError as error:
                    row['error'] = str(error)
                    self.warn(page.title + ': ' + str(error))
                    if isinstance(error, AgentRequestError) and error.status_code in (401, 402, 403, 404, 429, 502, 503):
                        for remaining in build.pages[number + 1:]:
                            pending_row = build.metadata_review[remaining.key]
                            pending_row.pop('proposed', None)
                            pending_row['error'] = 'Не запрашивалось: генерация остановлена после ошибки подключения или лимита.'
                        stopped = True
                        break
            self.client.check_cancel()
            save_review(build)
            self.stage(55, 'Генерация остановлена после ошибки провайдера. Можно повторить позже или продолжить без изменений.' if stopped else 'Варианты агента готовы к сравнению. Исходные метаданные пока не изменены.')
            return build
        finally:
            self.client.close()

    def finish(self, pending: Build, decision: str) -> Build:
        require_review(pending)
        if decision not in ('keep', 'agent'):
            raise RestorationError('Выберите: оставить исходные метаданные или применить варианты агента.')
        if decision == 'agent' and not can_apply(pending):
            raise RestorationError('В вариантах агента остались замечания. Повторите обработку или сохраните исходные метаданные.')
        self.build = build = deepcopy(pending)
        request = build.request
        snapshots = request.validate()
        downloader = AssetDownloader(build.root, self.client, self.warn, cached_only=True,
                                     activity=self.asset_activity, log=self.log)
        removed_assets = {row['asset'] for row in downloader.removed if row.get('asset')}
        build.cleanup.extend(downloader.removed)
        try:
            self.stage(60, 'Сохраняем исходные метаданные' if decision == 'keep' else 'Применяем выбранные метаданные агента')
            soups = [parse_html(page.html) for page in build.pages]
            for page in build.pages:
                values = build.metadata_review[page.key]['original' if decision == 'keep' else 'proposed']
                page.seo_title, page.description = values['title'], values['description']
            route_map = {(urlsplit(snapshot.original).hostname or '').removeprefix('www.') + route_for(snapshot.original): page.route
                         for snapshot, page in zip(snapshots, build.pages)}
            for casino in request.casino_pages:
                route = casino.route
                page = Page(hashlib.sha256(route.encode()).hexdigest()[:16], route, casino.title.strip(), '', casino=True)
                build.pages.append(page)
                soups.append(casino_shell(soups[0], page.title))
            self.stage(66, 'Меню, канониклы и пустые страницы казино')
            for index, (soup, page) in enumerate(zip(soups, build.pages)):
                removed = []
                clean(soup, request.lang, removed)
                for node in list(soup.select('script[src]')):
                    if node['src'] in removed_assets:
                        node.decompose()
                build.cleanup.extend(dict(row, route=page.route) for row in removed)
                base = snapshots[index].original if index < len(snapshots) else snapshots[0].original
                before_links = len(soup.select('a[href]'))
                clean_links(soup, base, route_map)
                bottom_links = deactivate_bottom_menu_links(soup)
                if bottom_links:
                    build.cleanup.append({'kind': 'links', 'route': page.route,
                                          'count': bottom_links,
                                          'detail': 'Удалены теги <a> из нижнего меню/подвала'})
                build.cleanup.append({'kind': 'links', 'route': page.route,
                                      'count': before_links - len(soup.select('a[href]')),
                                      'detail': 'Удалены ссылки вне выбранных страниц'})
                navigation(soup, build.pages, request)
                set_seo(soup, page, request.origin, preserve_metadata=decision == 'keep')
                repair_layout(soup, build.root)
            self.stage(78, 'Логотип и фавикон')
            with metered(self.agent, build.root):
                branding(soups, build, downloader, snapshots[0], self.agent)
            from .checklist import read_json
            icon = read_json(build.root/'favicon-report.json', {})
            if icon:
                self.log('Фавикон: ' + icon.get('label', '') + '; ' + icon.get('method', ''))
            for soup, page in zip(soups, build.pages):
                page.html = str(soup)
            self.stage(90, 'Подготовка сайта к обязательному SEO-чек-листу')
            build.metadata_decision = decision
            from .seo_routes import prepare
            from .checklist import inspect_site
            prepare(build)
            downloader.save()
            save_review(build)
            self.stage(96, 'Проверяем все 58 пунктов пользовательского чек-листа')
            report = inspect_site(build)
            self.log(f'CHECKLIST: ошибок {report["counts"].get("fail",0)}, требуют просмотра {report["counts"].get("review",0)}.')
            self.stage(100, 'Сайт подготовлен. Проверьте SEO-чек-лист; тема и архив ожидают вашего решения.')
            return build
        except Exception as error:
            pending.save()
            self.log('ERROR: Сборка остановлена; скачанные страницы и выбор метаданных доступны для повторного продолжения.')
            if isinstance(error, RestorationError):
                self.log('ERROR: ' + str(error))
            raise
        finally:
            self.client.close()

    def build_theme(self, build, *, owner_approved=False, viewed_fingerprint=None):
        if build.status != 'site_review':
            raise RestorationError('Сначала завершите проверку сайта перед созданием темы.')
        from .checklist import require_pass, approve_site
        if owner_approved is True:
            approve_site(build, viewed_fingerprint)
        require_pass(build)
        self.build = build
        self.stage(95, 'Создаём тему и импорт после решения по сайту')
        build.status = 'ready'
        try:
            write_site(build)
            audit(build)
            self.stage(100, 'Тема готова. Проверьте результат и одобрите упаковку.')
            return build
        except Exception:
            build.status = 'site_review'
            build.save()
            raise
        finally:
            self.client.close()
