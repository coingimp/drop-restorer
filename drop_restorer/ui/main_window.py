from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from ..agents.client import AgentClient, AgentConfig
from ..core.assets import AssetDownloader
from ..core.branding import branding
from ..core.cleaner import parse_html, set_seo
from ..core.downloader import ArchiveClient
from ..core.metadata import metadata
from ..core.models import Build, CasinoPage, DEFAULT_CASINO, RestorationError, RestoreRequest, Snapshot
from ..core.pipeline import Pipeline
from ..core.wordpress import write_site
from ..preview.window import PreviewWindow
from .metadata_review import MetadataReview
from .checks import ChecksPanel, money_text
from ..core.usage import initialize as initialize_usage, metered, summary as usage_summary


STYLE = '''
QMainWindow,QDialog,QWidget{background:#f3f5f8;color:#172333;font:10pt "Segoe UI"}
QLabel{background:transparent}
QStatusBar{background:#f3f5f8;color:#172333}
QWidget#sidebar{background:#152638;color:#e7edf5}
QWidget#sidebar QLabel{color:#e7edf5}
QWidget#sidebar QPushButton{background:transparent;border:0;text-align:left;color:#d9e5f2;padding:14px;border-radius:6px}
QWidget#sidebar QPushButton:hover{background:#263d54}
QLabel#brand{font-size:21px;font-weight:700;padding:12px 0}
QLabel#heading{font-size:27px;font-weight:650;color:#152638}
QLabel#muted{color:#627184}
QFrame#card{background:white;border:1px solid #dfe5ed;border-radius:9px}
QLineEdit,QPlainTextEdit,QComboBox,QSpinBox{background:white;color:#172333;border:1px solid #cbd4df;border-radius:5px;padding:8px;selection-background-color:#236ca8}
QLineEdit:focus,QPlainTextEdit:focus{border:1px solid #287db0}
QTableWidget{background:white;border:1px solid #dfe5ed;gridline-color:#e1e7ee;selection-background-color:#dcecf8;selection-color:#17364f}
QTableWidget::item{padding:5px}
QHeaderView::section{background:#eaf0f6;color:#17364f;border:0;padding:7px}
QPushButton{background:#e5ecf3;color:#17364f;border:1px solid #cfdae5;border-radius:6px;padding:10px 16px}
QPushButton:hover{background:#d8e5f0}
QPushButton:disabled{color:#8795a4;background:#edf0f3}
QPushButton#primary{background:#176da0;color:white;border:0;font-weight:600;padding:13px 22px}
QPushButton#primary:hover{background:#125b87}
QPushButton#primary:disabled{background:#a1bdce}
QProgressBar{border:0;border-radius:4px;background:#dde5ed;text-align:center;min-height:20px}
QProgressBar::chunk{background:#217ea1;border-radius:4px}
'''


class Worker(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)
    line = Signal(str)

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.action = action
        self.cancel = threading.Event()

    def run(self):
        try:
            self.result.emit(self.action(self))
        except RestorationError as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit('Не удалось завершить действие: ' + type(error).__name__ + '. Подробности сохранены в локальном журнале.')
            self.line.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace
        self.output_root = workspace / 'var' / 'drop-restorer'
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.output_root / 'settings.json'
        self.settings = {}
        if self.settings_path.exists():
            try:
                self.settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                pass
        self.worker = None
        self.build = None
        self.pending = None
        self.pending_path = self.output_root / 'pending-project.json'
        self.preview = None
        self.agent = None
        self.connected_agents = {}
        self.last_archive = None
        self.setWindowTitle('DropRestorer')
        self.resize(1240, 900)
        self.setMinimumSize(950, 690)
        self.setStyleSheet(STYLE)
        outer = QWidget()
        row = QHBoxLayout(outer)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        side = QWidget(objectName='sidebar')
        side.setFixedWidth(210)
        sidebar = QVBoxLayout(side)
        sidebar.setContentsMargins(18, 24, 18, 22)
        sidebar.addWidget(QLabel('DropRestorer', objectName='brand'))
        intro = QLabel('Из архива\nв ваш WordPress')
        sidebar.addWidget(intro)
        sidebar.addSpacing(30)
        self.stack = QStackedWidget()
        for index, label in enumerate(('Восстановление', 'Агенты', 'Настройки', 'Логи', 'Метаданные', 'Проверки и расходы')):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, i=index: self.stack.setCurrentIndex(i))
            sidebar.addWidget(button)
        sidebar.addStretch()
        footer = QLabel('Сначала просмотр.\nЗатем одобрение.\nПотом готовый архив.')
        footer.setWordWrap(True)
        sidebar.addWidget(footer)
        row.addWidget(side)
        row.addWidget(self.stack, 1)
        self.create_restore_page()
        self.create_agent_page()
        self.create_settings_page()
        self.create_logs_page()
        layout = self.panel('Скачанные страницы и метаданные', 'Проверьте исходные значения. Выберите обработку агентом или продолжите сборку без изменений метаданных.')
        self.review = MetadataReview()
        self.review.connect_requested.connect(lambda: self.stack.setCurrentIndex(1))
        self.review.generate_requested.connect(self.generate_metadata)
        self.review.continue_requested.connect(self.continue_restore)
        layout.addWidget(self.review)
        layout.addStretch()
        layout = self.panel('Проверки и расходы', 'Автоматический отчёт по готовой сборке. Перед упаковкой файлы проверяются повторно.')
        self.checks = ChecksPanel()
        layout.addWidget(self.checks)
        self.setCentralWidget(outer)
        self.statusBar().showMessage('Добавьте ссылки на снимки Wayback, чтобы начать.')
        if self.pending_path.exists():
            try:
                root = Path(json.loads(self.pending_path.read_text(encoding='utf-8'))['root'])
                pending = Build.load(root)
                if pending.status == 'metadata_review':
                    self.review_ready(pending)
            except (OSError, ValueError, KeyError, TypeError):
                self.statusBar().showMessage('Сохранённый запуск недоступен. Можно открыть другой запуск вручную.')

    def panel(self, title, subtitle):
        wrapper = QScrollArea()
        wrapper.setWidgetResizable(True)
        wrapper.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(16)
        layout.addWidget(QLabel(title, objectName='heading'))
        note = QLabel(subtitle, objectName='muted')
        note.setWordWrap(True)
        layout.addWidget(note)
        wrapper.setWidget(page)
        self.stack.addWidget(wrapper)
        return layout

    def card(self, parent):
        frame = QFrame(objectName='card')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 18, 20, 18)
        parent.addWidget(frame)
        return layout

    def create_restore_page(self):
        layout = self.panel('Восстановить сайт', 'Укажите нужные страницы из Wayback. После обработки вы сможете проверить сайт во встроенном браузере.')
        card = self.card(layout)
        card.addWidget(QLabel('1. Страницы из архива'))
        form = QFormLayout()
        self.main_url = QLineEdit()
        self.main_url.setPlaceholderText('https://web.archive.org/web/20200101000000/https://example.com/')
        form.addRow('Главная', self.main_url)
        self.menu_urls = QPlainTextEdit()
        self.menu_urls.setPlaceholderText('Ссылки на страницы меню — по одной на строку')
        self.menu_urls.setFixedHeight(112)
        form.addRow('Страницы меню', self.menu_urls)
        self.lang = QLineEdit(self.settings.get('lang', 'cs-CZ'))
        self.lang.setPlaceholderText('cs-CZ')
        self.domain = QLineEdit(self.settings.get('final_domain', ''))
        self.domain.setPlaceholderText('Необязательно: example.com')
        form.addRow('Язык сайта', self.lang)
        form.addRow('Финальный домен', self.domain)
        card.addLayout(form)
        card = self.card(layout)
        card.addWidget(QLabel('2. Раздел казино'))
        hint = QLabel('Канониклы создаются автоматически. Контент, title и description вы добавите в WordPress.', objectName='muted')
        hint.setWordWrap(True)
        card.addWidget(hint)
        grid = QGridLayout()
        self.casino_label = QLineEdit('Казино')
        grid.addWidget(QLabel('Название раздела'), 0, 0)
        grid.addWidget(self.casino_label, 0, 1)
        self.casino_fields = []
        card.addWidget(QLabel('Публичный формат адреса: /casino/<слаг>/. В поле ниже указывается только слаг страницы.', objectName='muted'))
        for i, page in enumerate(DEFAULT_CASINO, 1):
            title, slug = QLineEdit(page.title), QLineEdit(page.slug)
            title.setPlaceholderText('Название на языке сайта')
            self.casino_fields.append((title, slug))
            grid.addWidget(QLabel(f'Страница {i}'), i, 0)
            grid.addWidget(title, i, 1)
            grid.addWidget(slug, i, 2)
        card.addLayout(grid)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.progress_text = QLabel('Готово к началу')
        self.progress_text.setWordWrap(True)
        layout.addWidget(self.progress_text)
        actions = QHBoxLayout()
        self.start_button = QPushButton('Запустить восстановление', objectName='primary')
        self.start_button.clicked.connect(self.start_restore)
        actions.addWidget(self.start_button)
        self.cancel_button = QPushButton('Отменить')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        actions.addWidget(self.cancel_button)
        self.preview_button = QPushButton('Открыть превью')
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.open_preview)
        actions.addWidget(self.preview_button)
        layout.addLayout(actions)
        self.load_button = QPushButton('Открыть скачанные страницы из сохранённого запуска')
        self.load_button.clicked.connect(self.open_saved)
        layout.addWidget(self.load_button)
        self.result_label = QLabel('')
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.result_label)
        self.open_folder_button = QPushButton('Открыть папку с результатом')
        self.open_folder_button.hide()
        self.open_folder_button.clicked.connect(self.open_folder)
        layout.addWidget(self.open_folder_button)
        self.new_project_button = QPushButton('Новый проект')
        self.new_project_button.clicked.connect(self.new_project)
        self.new_project_button.hide()
        layout.addWidget(self.new_project_button)
        layout.addStretch()

    def create_agent_page(self):
        layout = self.panel('Агенты', 'Подключите текстовую модель для метаданных и генерации отсутствующих логотипа и фавикона.')
        card = self.card(layout)
        form = QFormLayout()
        self.endpoint = QLineEdit(self.settings.get('endpoint', 'https://openrouter.ai/api/v1'))
        self.model = QLineEdit(self.settings.get('model', ''))
        self.model.setPlaceholderText('Точное название модели у провайдера')
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText('Ключ хранится только до закрытия приложения')
        form.addRow('Endpoint', self.endpoint)
        form.addRow('Модель', self.model)
        form.addRow('API key', self.api_key)
        card.addLayout(form)
        buttons = QHBoxLayout()
        self.connect_button = QPushButton('Подключить', objectName='primary')
        self.connect_button.clicked.connect(self.connect_agent)
        buttons.addWidget(self.connect_button)
        self.test_button = QPushButton('Тестировать подключение')
        self.test_button.clicked.connect(self.connect_agent)
        buttons.addWidget(self.test_button)
        self.disconnect_button = QPushButton('Отключить')
        self.disconnect_button.clicked.connect(self.disconnect_agent)
        buttons.addWidget(self.disconnect_button)
        card.addLayout(buttons)
        self.agent_status = QLabel('Агент не подключён')
        self.agent_status.setWordWrap(True)
        card.addWidget(self.agent_status)
        self.agent_usage_label = QLabel('Тесты подключения (отдельно от сборки):\n' + money_text(usage_summary(self.output_root)))
        self.agent_usage_label.setWordWrap(True)
        self.agent_usage_label.setTextFormat(Qt.TextFormat.PlainText)
        card.addWidget(self.agent_usage_label)
        card.addWidget(QLabel('Подключённые агенты · активный используется для восстановления'))
        self.agent_picker = QComboBox()
        self.agent_picker.currentIndexChanged.connect(self.select_agent)
        card.addWidget(self.agent_picker)
        note = QLabel('Поддерживаются OpenAI-совместимые сервисы и локальные модели. Проверка подключения отправляет короткий запрос выбранному провайдеру. Ключ не сохраняется в проекте или архиве.', objectName='muted')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.return_review_button = QPushButton('Вернуться к метаданным')
        self.return_review_button.clicked.connect(lambda: self.stack.setCurrentIndex(4))
        layout.addWidget(self.return_review_button)
        layout.addStretch()

    def create_settings_page(self):
        layout = self.panel('Настройки', 'Параметры просмотра и расположение результатов.')
        card = self.card(layout)
        self.auto_screens = QCheckBox('После открытия превью сохранить снимки главной и казино во всех режимах')
        self.auto_screens.setChecked(bool(self.settings.get('screenshots', False)))
        card.addWidget(self.auto_screens)
        label = QLabel('Папка результатов:\n' + str(self.output_root))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.addWidget(label)
        save = QPushButton('Сохранить настройки')
        save.clicked.connect(self.save_settings)
        card.addWidget(save)
        layout.addStretch()

    def create_logs_page(self):
        layout = self.panel('Логи', 'Ход загрузки, предупреждения и действия в превью.')
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(5000)
        layout.addWidget(self.logs, 1)

    def save_settings(self):
        self.settings = {'lang': self.lang.text().strip(), 'final_domain': self.domain.text().strip(),
                         'endpoint': self.endpoint.text().strip(), 'model': self.model.text().strip(),
                         'screenshots': self.auto_screens.isChecked()}
        self.settings_path.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding='utf-8')
        self.statusBar().showMessage('Настройки сохранены.', 4000)

    def read_request(self):
        request = RestoreRequest(self.main_url.text().strip(), [x.strip() for x in self.menu_urls.toPlainText().splitlines() if x.strip()],
            self.lang.text().strip(), self.domain.text().strip(),
            [CasinoPage(title.text().strip(), slug.text().strip()) for title, slug in self.casino_fields], self.casino_label.text().strip())
        request.validate()
        return request

    def task(self, action, done, cancellable=False):
        if self.worker is not None:
            return False
        worker = Worker(action, self)
        self.worker = worker
        worker.result.connect(done)
        worker.failed.connect(self.failed)
        worker.progress.connect(self.show_progress)
        worker.line.connect(self.log)
        worker.finished.connect(self.task_finished)
        self.start_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.test_button.setEnabled(False)
        self.disconnect_button.setEnabled(False)
        self.agent_picker.setEnabled(False)
        self.cancel_button.setEnabled(cancellable)
        self.load_button.setEnabled(False)
        self.review.set_busy(True, self.agent)
        worker.start()
        return True

    def task_finished(self):
        worker = self.worker
        self.worker = None
        if worker:
            worker.deleteLater()
        self.start_button.setEnabled(True)
        self.connect_button.setEnabled(True)
        self.test_button.setEnabled(True)
        self.disconnect_button.setEnabled(True)
        self.agent_picker.setEnabled(True)
        self.preview_button.setEnabled(self.build is not None)
        self.cancel_button.setEnabled(False)
        self.load_button.setEnabled(True)
        self.review.set_busy(False, self.agent)
        self.agent_usage_label.setText('Тесты подключения (отдельно от сборки):\n' + money_text(usage_summary(self.output_root)).replace('в этом запуске', 'в журнале тестов'))

    def start_restore(self):
        try:
            request = self.read_request()
            self.save_settings()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, 'Проверьте данные', str(error))
            return
        self.build = None
        self.pending = None
        self.review.set_build(None)
        self.checks.set_build(None)
        self.pending_path.unlink(missing_ok=True)
        self.last_archive = None
        self.result_label.clear()
        self.open_folder_button.hide()
        self.new_project_button.hide()
        self.logs.clear()
        agent = self.agent
        self.task(lambda worker: Pipeline(self.output_root, worker.progress.emit, worker.line.emit, worker.cancel, agent).run(request), self.review_ready, True)

    def open_saved(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Открыть сохранённый запуск', str(self.output_root), 'Проект DropRestorer (project.json)')
        if path:
            self.task(lambda worker: Pipeline(self.output_root, worker.progress.emit, worker.line.emit, worker.cancel).open_saved(Path(path).parent), self.review_ready, True)

    def review_ready(self, build):
        self.pending = build
        self.build = None
        self.checks.set_build(None)
        self.review.set_build(build)
        self.review.set_busy(self.worker is not None, self.agent)
        self.pending_path.write_text(json.dumps({'root': str(build.root)}, ensure_ascii=False), encoding='utf-8')
        self.fill_request(build.request)
        self.show_progress(55, 'Страницы скачаны. Сборка ожидает выбора на вкладке «Метаданные».')
        self.statusBar().showMessage('Ожидается ваш выбор метаданных. Скачанные страницы сохранены.')
        self.preview_button.setEnabled(False)
        self.result_label.setText(f'Скачано страниц: {len(build.pages)}. Метаданные готовы к проверке.')
        self.open_folder_button.show()
        self.stack.setCurrentIndex(4)

    def fill_request(self, request):
        self.main_url.setText(request.main_page)
        self.menu_urls.setPlainText('\n'.join(request.menu_pages))
        self.lang.setText(request.lang)
        self.domain.setText(request.final_domain)
        self.casino_label.setText(request.casino_label)
        for (title, slug), page in zip(self.casino_fields, request.casino_pages):
            title.setText(page.title)
            slug.setText(page.slug)

    def open_project(self, root):
        try:
            build = Build.load(root)
            self.fill_request(build.request)
            if build.status == 'ready':
                self.show_progress(100, 'Сборка открыта. Проверьте страницы в превью.')
                self.restored(build)
                self.preview_button.setEnabled(True)
            elif build.status == 'metadata_review':
                self.review_ready(build)
            else:
                raise RestorationError('Этот запуск ещё не готов к просмотру.')
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.failed('Не удалось открыть сохранённый проект: ' + str(error))

    def generate_metadata(self):
        if self.pending and self.agent:
            pending, agent = self.pending, self.agent
            self.task(lambda worker: Pipeline(self.output_root, worker.progress.emit, worker.line.emit, worker.cancel, agent).suggest_metadata(pending), self.review_ready, True)

    def continue_restore(self, decision):
        if self.pending:
            pending, agent = self.pending, self.agent
            self.stack.setCurrentIndex(0)
            self.task(lambda worker: Pipeline(self.output_root, worker.progress.emit, worker.line.emit, worker.cancel, agent).finish(pending, decision), self.restored, True)

    def restored(self, build):
        self.build = build
        self.checks.set_build(build)
        self.pending = None
        self.review.set_build(None)
        self.pending_path.unlink(missing_ok=True)
        self.result_label.setText(f'Подготовлено страниц: {len(build.pages)}. Предупреждений: {len(build.warnings)}.\n' + '\n'.join(build.warnings[:5]))
        self.open_folder_button.show()
        self.open_preview()

    def open_preview(self):
        if not self.build:
            return
        if self.preview:
            self.preview.raise_()
            return
        try:
            preview = PreviewWindow(self.build, self)
            self.preview = preview
            preview.log.connect(self.log)
            preview.edit_requested.connect(self.edit)
            preview.packaged.connect(self.packaged)
            preview.destroyed.connect(lambda *_: setattr(self, 'preview', None))
            preview.showFullScreen()
            if self.auto_screens.isChecked():
                def once(ok):
                    preview.view.loadFinished.disconnect(once)
                    if ok:
                        preview.capture_all()
                preview.view.loadFinished.connect(once)
        except Exception as error:
            self.failed('Не удалось открыть превью: ' + str(error))

    def edit(self, build):
        self.showNormal()
        self.raise_()
        options = ['Изменить страницы, язык или настройки', 'Перегенерировать логотип', 'Перегенерировать фавикон', 'Править HTML восстановленной страницы']
        choice, ok = QInputDialog.getItem(self, 'Исправления', 'Что нужно исправить?', options, 0, False)
        if not ok or choice == options[0]:
            self.stack.setCurrentIndex(0)
            return
        if choice == options[3]:
            self.edit_html()
            return
        if not self.agent and choice == options[1]:
            QMessageBox.information(self, 'Нужен агент', 'Подключите агента во вкладке «Агенты», затем вернитесь в превью.')
            self.stack.setCurrentIndex(1)
            return
        kind = 'logo' if choice == options[1] else 'favicon'
        agent = self.agent
        def regenerate(worker):
            soups = [parse_html(page.html) for page in build.pages]
            client = ArchiveClient(worker.cancel)
            downloader = AssetDownloader(build.root, client, build.warnings.append)
            try:
                with metered(agent, build.root):
                    branding(soups, build, downloader, Snapshot.parse(build.request.main_page), agent, kind)
                for soup, page in zip(soups, build.pages):
                    page.html = str(soup)
                write_site(build)
                return build
            finally:
                client.close()
        self.task(regenerate, self.restored)

    def edit_html(self):
        pages = [p for p in self.build.pages if not p.casino]
        title, ok = QInputDialog.getItem(self, 'HTML', 'Страница', [p.title for p in pages], 0, False)
        if not ok:
            return
        page = next(p for p in pages if p.title == title)
        dialog = QDialog(self)
        dialog.setWindowTitle('Правка HTML — ' + page.title)
        dialog.resize(1050, 740)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(page.html)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                soup = parse_html(text.toPlainText())
                soup.html['lang'] = self.build.request.lang
                metadata(soup, page, self.build.request.lang,
                         {p.seo_title for p in pages if p.key != page.key},
                         {p.description for p in pages if p.key != page.key}, self.build.warnings.append)
                set_seo(soup, page, self.build.request.origin)
                page.html = str(soup)
                write_site(self.build)
                self.checks.set_build(self.build)
                self.progress_text.setText('Исправления сохранены. Откройте превью для повторной проверки.')
            except (RestorationError, OSError) as error:
                QMessageBox.warning(self, 'Правка HTML', str(error))

    def connect_agent(self):
        try:
            agent = AgentClient(AgentConfig(self.endpoint.text().strip(), self.model.text().strip(), self.api_key.text()))
            agent.usage_path = initialize_usage(self.output_root)
        except RestorationError as error:
            QMessageBox.warning(self, 'Агент', str(error))
            return
        def connected(info):
            self.agent = agent
            identifier = agent.config.endpoint + '|' + agent.config.model
            self.connected_agents[identifier] = agent
            index = self.agent_picker.findData(identifier)
            if index < 0:
                self.agent_picker.addItem(agent.config.model + ' · ' + (urlsplit(agent.config.endpoint).hostname or ''), identifier)
                index = self.agent_picker.count() - 1
            self.agent_picker.setCurrentIndex(index)
            self.save_settings()
            self.agent_status.setText(f'Подключено: {agent.config.model}\nВремя ответа: {info["seconds"]} с')
        self.agent_status.setText('Проверка подключения…')
        self.task(lambda worker: agent.test(), connected)

    def disconnect_agent(self):
        identifier = self.agent_picker.currentData()
        self.connected_agents.pop(identifier, None)
        if self.agent_picker.currentIndex() >= 0:
            self.agent_picker.removeItem(self.agent_picker.currentIndex())
        self.agent = self.connected_agents.get(self.agent_picker.currentData())
        self.api_key.clear()
        self.agent_status.setText('Активный агент: ' + self.agent.config.model if self.agent else 'Агент отключён')
        self.review.set_busy(self.worker is not None, self.agent)

    def select_agent(self, *_):
        self.agent = self.connected_agents.get(self.agent_picker.currentData())
        if self.agent:
            self.endpoint.setText(self.agent.config.endpoint)
            self.model.setText(self.agent.config.model)
            self.api_key.setText(self.agent.config.api_key)
            self.agent_status.setText('Активный агент: ' + self.agent.config.model)
        if hasattr(self, 'review'):
            self.review.set_busy(self.worker is not None, self.agent)

    def packaged(self, path):
        self.last_archive = Path(path)
        self.result_label.setText(f'Готово! {self.last_archive.name}\nСтраниц: {len(self.build.pages)} · Размер: {self.last_archive.stat().st_size / 1024:.0f} КБ\n{path}')
        self.progress_text.setText('Результат одобрен. Архив сохранён.')
        self.new_project_button.show()
        self.showNormal()
        self.raise_()

    def new_project(self):
        if self.worker:
            return
        self.build = None
        self.checks.set_build(None)
        self.pending = None
        self.review.set_build(None)
        self.review.set_busy(False, self.agent)
        self.pending_path.unlink(missing_ok=True)
        self.last_archive = None
        self.main_url.clear()
        self.menu_urls.clear()
        self.progress.setValue(0)
        self.progress_text.setText('Добавьте ссылки нового сайта.')
        self.result_label.clear()
        self.preview_button.setEnabled(False)
        self.open_folder_button.hide()
        self.new_project_button.hide()
        self.main_url.setFocus()

    def open_folder(self):
        root = self.last_archive.parent if self.last_archive else self.build.root if self.build else self.pending.root if self.pending else self.output_root
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def log(self, message):
        # Never log form fields or provider keys.
        for secret in (self.api_key.text(), self.agent.config.api_key if self.agent else ''):
            if secret:
                message = message.replace(secret, '[скрыто]')
        self.logs.appendPlainText(message)
        with (self.output_root / 'application.log').open('a', encoding='utf-8') as output:
            output.write(message + '\n')

    def show_progress(self, value, message):
        self.progress.setValue(value)
        self.progress_text.setText(message)

    def failed(self, message):
        self.progress_text.setText(message)
        self.log('ERROR: ' + message)
        if self.agent_status.text() == 'Проверка подключения…':
            self.agent_status.setText(message)
        QMessageBox.warning(self, 'DropRestorer', message)

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.cancel_button.setEnabled(False)
            self.progress_text.setText('Отмена: ожидаем завершения текущего сетевого запроса…')

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.cancel()
            event.ignore()
            return
        if self.preview:
            self.preview.close()
        self.agent = None
        self.connected_agents.clear()
        self.api_key.clear()
        event.accept()
