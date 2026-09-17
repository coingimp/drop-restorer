"""Render existing preview pages to PNG without a desktop window."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def main():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from ..core.models import Build
    from ..preview.server import PreviewServer
    from ..preview.window import DEVICES, LocalOnly

    parser = argparse.ArgumentParser()
    parser.add_argument('project', type=Path)
    parser.add_argument('--key')
    parser.add_argument('--cover', action='store_true')
    parser.add_argument('--device', choices=list(DEVICES), default='Desktop')
    args = parser.parse_args()
    build = Build.load(args.project)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    server = PreviewServer(build)
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    interceptor = LocalOnly(server.port, profile)
    profile.setUrlRequestInterceptor(interceptor)
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    page = QWebEnginePage(profile, view)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
    profile.downloadRequested.connect(lambda download: download.cancel())
    view.setPage(page)
    directory = build.root / ('card-preview' if args.cover else 'preview_screenshots')
    directory.mkdir(exist_ok=True)
    queue = [(p, args.device) for p in build.pages if p.key == args.key] if args.key else [
        (p, device) for i, p in enumerate(build.pages) if i == 0 or p.casino for device in DEVICES]
    if not queue:
        raise ValueError('No pages selected for capture')
    current = []
    saved = []
    code = 1

    def advance():
        nonlocal code
        if not queue:
            code = 0
            app.quit()
            return
        p, device = queue.pop(0)
        current[:] = [p, device]
        view.setFixedSize(*DEVICES[device])
        view.show()  # QT_QPA_PLATFORM=offscreen: never creates a visible native window.
        view.load(QUrl(server.origin + p.route))

    def capture():
        p, device = current
        target = directory / ('source.png' if args.cover else device.lower() + '_' + p.key + '.png')
        image = view.grab()
        if image.isNull() or not image.save(str(target)):
            app.quit()
            return
        saved.append(target.name)
        print('Saved ' + target.name, flush=True)
        advance()

    def loaded(ok):
        if not ok:
            app.quit()
        else:
            QTimer.singleShot(900, capture)

    view.loadFinished.connect(loaded)
    QTimer.singleShot(0, advance)
    QTimer.singleShot(180_000, app.quit)
    app.exec()
    view.stop()
    view.setPage(QWebEnginePage(view))
    page.deleteLater()
    app.processEvents()
    view.close()
    server.stop()
    print(f'Captured {len(saved)} images', flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
