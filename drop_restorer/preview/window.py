from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ..core.models import RestorationError
from ..core.packager import approve, package
from ..core.audit import audit
from ..ui.checks import show_checks
from .server import PreviewServer


DEVICES = {'Desktop': (1920, 1080), 'Tablet': (768, 1024), 'Mobile': (375, 812)}


class LocalOnly(QWebEngineUrlRequestInterceptor):
    def __init__(self, port, parent=None):
        super().__init__(parent)
        self.port = port

    def interceptRequest(self, info):
        url = info.requestUrl()
        if url.scheme() in ('data', 'blob', 'about'):
            return
        if url.scheme() != 'http' or url.host() != '127.0.0.1' or url.port() != self.port:
            info.block(True)


class PreviewWindow(QMainWindow):
    edit_requested = Signal(object)
    packaged = Signal(str)
    log = Signal(str)

    def __init__(self, build, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.build = build
        self.server = PreviewServer(build)
        self.viewed_digest = build.digest()
        self.checks_report = audit(build)
        self.loaded = False
        self.current_device = 'Desktop'
        self.capture_queue = []
        self.capturing = False
        self.setWindowTitle('DropRestorer — проверка восстановленного сайта')
        root = QWidget()
        layout = QVBoxLayout(root)
        controls = QHBoxLayout()
        self.devices = QComboBox()
        self.devices.addItems(DEVICES)
        self.devices.currentTextChanged.connect(self.device_changed)
        controls.addWidget(self.devices)
        self.zoom = QSpinBox()
        self.zoom.setRange(25, 200)
        self.zoom.setValue(100)
        self.zoom.setSuffix('%')
        self.zoom.valueChanged.connect(lambda value: self.view.setZoomFactor(value / 100))
        controls.addWidget(QLabel('Масштаб'))
        controls.addWidget(self.zoom)
        self.page_picker = QComboBox()
        for page in build.pages:
            self.page_picker.addItem(('Казино · ' if page.casino else '') + page.title, page.route)
        self.page_picker.currentIndexChanged.connect(self.navigate)
        controls.addWidget(self.page_picker, 1)
        self.size_label = QLabel()
        controls.addWidget(self.size_label)
        self.screen_button = QPushButton('Скриншот')
        self.screen_button.clicked.connect(self.screenshot)
        controls.addWidget(self.screen_button)
        self.all_screens = QPushButton('Снимки всех режимов')
        self.all_screens.clicked.connect(self.capture_all)
        controls.addWidget(self.all_screens)
        layout.addLayout(controls)
        self.hint = QLabel('Проверьте меню, изображения и адаптивность. При большом размере просмотра доступны полосы прокрутки рамки.')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.profile = QWebEngineProfile(self)
        self.interceptor = LocalOnly(self.server.port, self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        self.profile.downloadRequested.connect(lambda download: download.cancel())
        self.view = QWebEngineView()
        self.web_page = QWebEnginePage(self.profile, self.view)
        self.view.setPage(self.web_page)
        settings = self.web_page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False)
        self.view.loadStarted.connect(self.load_started)
        self.view.loadFinished.connect(self.load_finished)
        self.view.urlChanged.connect(self.url_changed)
        self.frame = QScrollArea()
        self.frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame.setWidget(self.view)
        self.frame.setWidgetResizable(False)
        layout.addWidget(self.frame, 1)
        bottom = QHBoxLayout()
        self.back = QPushButton('Вернуться и исправить')
        self.back.clicked.connect(self.go_back)
        bottom.addWidget(self.back)
        self.checks_button = QPushButton('Проверки и расходы')
        self.checks_button.clicked.connect(lambda: show_checks(self.build, self, self.checks_updated))
        bottom.addWidget(self.checks_button)
        warnings = QLabel(f'Проверки: {self.checks_report["errors"]} ошибок · {self.checks_report["warnings"]} замечаний')
        self.checks_label = warnings
        warnings.setToolTip('\n'.join(build.warnings))
        bottom.addWidget(warnings, 1)
        self.approve_button = QPushButton('Одобрить и упаковать')
        self.approve_button.setStyleSheet('background:#19734a;color:white;padding:12px 22px;border:0;border-radius:6px;font-weight:600')
        self.approve_button.clicked.connect(self.approve_and_package)
        bottom.addWidget(self.approve_button)
        layout.addLayout(bottom)
        self.setCentralWidget(root)
        self.devtools = None
        QShortcut(QKeySequence('F12'), self, self.show_devtools)
        QShortcut(QKeySequence('Escape'), self, self.leave_fullscreen)
        self.device_changed('Desktop')
        self.navigate()

    def leave_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized()

    def checks_updated(self, report):
        self.checks_report = report
        self.checks_label.setText(f'Проверки: {report.get("errors", "?")} ошибок · {report.get("warnings", "?")} замечаний')
        self.approve_button.setEnabled(self.loaded and not self.capturing and report['passed'])

    def device_changed(self, name):
        self.current_device = name
        width, height = DEVICES[name]
        self.view.setFixedSize(width, height)
        self.size_label.setText(f'{width} × {height}')
        self.log.emit(f'PREVIEW: {name} {width}×{height}')

    def navigate(self, *_):
        self.view.setUrl(QUrl(self.server.origin + self.page_picker.currentData()))

    def url_changed(self, url):
        route = url.path(QUrl.ComponentFormattingOption.FullyEncoded) + ('?' + url.query() if url.hasQuery() else '')
        index = self.page_picker.findData(route)
        if index >= 0:
            self.page_picker.blockSignals(True)
            self.page_picker.setCurrentIndex(index)
            self.page_picker.blockSignals(False)

    def load_started(self):
        self.loaded = False
        self.approve_button.setEnabled(False)

    def load_finished(self, ok):
        self.loaded = ok
        self.approve_button.setEnabled(ok and not self.capturing and self.checks_report['passed'])
        if not ok:
            self.hint.setText('Страница не загрузилась. Вернитесь к исправлениям или выберите другую страницу.')
            if self.capturing:
                self.finish_capture('Не удалось создать все снимки: страница не загрузилась.')
        elif self.capturing:
            QTimer.singleShot(800, self.capture_next_image)

    def screenshot(self):
        if not self.loaded:
            return
        page = self.build.pages[self.page_picker.currentIndex()]
        path = self.build.root / 'preview_screenshots' / f'{self.current_device.lower()}_{page.key}.png'
        if self.view.grab().save(str(path)):
            self.hint.setText('Скриншот сохранён: ' + path.name)
            self.log.emit('PREVIEW: Сохранён ' + path.name)

    def capture_all(self):
        if self.capturing:
            return
        self.capture_restore = (self.page_picker.currentIndex(), self.devices.currentText(), self.zoom.value())
        self.capture_queue = [(i, device) for i, page in enumerate(self.build.pages) if i == 0 or page.casino for device in DEVICES]
        self.capturing = True
        self.zoom.setValue(100)
        for control in (self.approve_button, self.back, self.all_screens, self.screen_button, self.devices, self.page_picker, self.zoom):
            control.setEnabled(False)
        self.capture_advance()

    def capture_advance(self):
        if not self.capture_queue:
            self.finish_capture('Скриншоты главной и казино-страниц во всех режимах сохранены.')
            return
        index, device = self.capture_queue.pop(0)
        self.devices.setCurrentText(device)
        self.page_picker.blockSignals(True)
        self.page_picker.setCurrentIndex(index)
        self.page_picker.blockSignals(False)
        self.navigate()

    def capture_next_image(self):
        if self.capturing and self.loaded:
            self.screenshot()
            self.capture_advance()

    def finish_capture(self, message):
        self.capturing = False
        self.capture_queue = []
        for control in (self.back, self.all_screens, self.screen_button, self.devices, self.page_picker, self.zoom):
            control.setEnabled(True)
        index, device, zoom = self.capture_restore
        self.devices.setCurrentText(device)
        self.zoom.setValue(zoom)
        self.page_picker.setCurrentIndex(index)
        self.approve_button.setEnabled(self.loaded and self.checks_report['passed'])
        self.hint.setText(message)

    def show_devtools(self):
        if self.devtools is None:
            self.devtools = QWebEngineView()
            self.devtools.setWindowTitle('DropRestorer — DevTools')
            self.devtools.resize(1100, 700)
            self.web_page.setDevToolsPage(self.devtools.page())
        self.devtools.show()

    def go_back(self):
        self.build.approved_digest = None
        self.build.save()
        self.edit_requested.emit(self.build)
        self.close()

    def approve_and_package(self):
        if not self.loaded or self.capturing:
            return
        from ..core.theme_identity import theme_identity
        name = theme_identity(self.build.request.origin).slug + '.zip'
        destination, _ = QFileDialog.getSaveFileName(self, 'Сохранить тему для установщика WordPress', str(self.build.root / name), 'ZIP (*.zip)')
        if not destination:
            return
        try:
            approve(self.build, self.viewed_digest)
            path = package(self.build, Path(destination))
            self.log.emit('APPROVED: Результат одобрен и упакован.')
            QMessageBox.information(self, 'Установка WordPress', 'ZIP темы готов для «Внешний вид → Темы → Загрузить тему». Рядом сохранены XML страниц для «Инструменты → Импорт → WordPress» и полный комплект с суффиксом -bundle.zip.')
            self.packaged.emit(str(path))
            self.close()
        except (RestorationError, OSError) as error:
            QMessageBox.warning(self, 'Упаковка', str(error))

    def closeEvent(self, event):
        self.capturing = False
        self.view.stop()
        self.web_page.setUrl(QUrl('about:blank'))
        if self.devtools:
            self.devtools.close()
        self.server.stop()
        # Delete pages before their off-the-record profile.
        self.view.setPage(QWebEnginePage(self.view))
        self.web_page.deleteLater()
        event.accept()
