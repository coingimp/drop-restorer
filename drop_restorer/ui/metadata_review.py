from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.cleaner import parse_html
from ..core.review import can_apply, content_node, issues
from ..preview.server import PreviewServer
from ..preview.window import LocalOnly


class DownloadedPreview(QDialog):
    def __init__(self, build, route, parent):
        super().__init__(parent)
        self.setWindowTitle('Скачанная страница — до сборки темы')
        self.resize(1120, 800)
        self.server = PreviewServer(build)
        layout = QVBoxLayout(self)
        note = QLabel('Это скачанная страница. Меню и канониклы будут настроены при продолжении сборки.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.profile = QWebEngineProfile(self)
        self.interceptor = LocalOnly(self.server.port, self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.profile.downloadRequested.connect(lambda item: item.cancel())
        self.view = QWebEngineView()
        self.page = QWebEnginePage(self.profile, self.view)
        self.view.setPage(self.page)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        layout.addWidget(self.view)
        self.view.setUrl(QUrl(self.server.origin + route))

    def done(self, result):
        self.view.stop()
        self.server.stop()
        self.page.deleteLater()
        super().done(result)
        self.deleteLater()


class MetadataReview(QWidget):
    connect_requested = Signal()
    generate_requested = Signal()
    continue_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.build = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel('Сначала скачайте страницы или откройте сохранённый запуск.')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Скачанная страница', 'Title', 'Description', 'Замечания к исходным'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 65)
        self.table.setColumnWidth(2, 105)
        self.table.setFixedHeight(185)
        self.table.currentCellChanged.connect(self.show_page)
        layout.addWidget(self.table)
        self.canonical = QLabel()
        self.canonical.setWordWrap(True)
        self.canonical.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.canonical)
        comparison = QGridLayout()
        self.fields = {}
        self.notes = {}
        for column, (variant, heading) in enumerate([('original', 'Исходные метаданные'), ('proposed', 'Вариант агента · ещё не применён')]):
            comparison.addWidget(QLabel(heading), 0, column)
            form = QFormLayout()
            for field, label, height in [('title', 'Title', 64), ('description', 'Description', 94)]:
                text = QPlainTextEdit()
                text.setReadOnly(True)
                text.setFixedHeight(height)
                self.fields[variant, field] = text
                form.addRow(label, text)
            comparison.addLayout(form, 1, column)
            note = QLabel()
            note.setWordWrap(True)
            self.notes[variant] = note
            comparison.addWidget(note, 2, column)
        layout.addLayout(comparison)
        row = QHBoxLayout()
        row.addWidget(QLabel('Текст выбранной страницы'), 1)
        self.view_button = QPushButton('Посмотреть скачанную страницу')
        self.view_button.clicked.connect(self.view_page)
        row.addWidget(self.view_button)
        layout.addLayout(row)
        self.content = QPlainTextEdit()
        self.content.setReadOnly(True)
        self.content.setFixedHeight(110)
        layout.addWidget(self.content)
        self.agent_label = QLabel('Агент не подключён')
        self.agent_label.setWordWrap(True)
        layout.addWidget(self.agent_label)
        row = QHBoxLayout()
        self.connect_button = QPushButton('Подключить / выбрать агента')
        self.connect_button.clicked.connect(self.connect_requested)
        row.addWidget(self.connect_button)
        self.generate_button = QPushButton('Подготовить варианты агентом')
        self.generate_button.clicked.connect(self.generate_requested)
        row.addWidget(self.generate_button)
        row.addStretch()
        layout.addLayout(row)
        row = QHBoxLayout()
        self.keep_button = QPushButton('Ничего не изменять и продолжить', objectName='primary')
        self.keep_button.clicked.connect(lambda: self.continue_requested.emit('keep'))
        row.addWidget(self.keep_button)
        self.apply_button = QPushButton('Применить варианты и продолжить')
        self.apply_button.clicked.connect(lambda: self.continue_requested.emit('agent'))
        row.addWidget(self.apply_button)
        layout.addLayout(row)
        note = QLabel('Замечания не мешают оставить исходные значения, включая пропуски. Канониклы всех страниц создаются автоматически. Метаданные казино вы зададите в WordPress.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.set_busy(False, None)

    def set_build(self, build):
        selected = max(0, self.table.currentRow())
        self.build = build
        self.table.setRowCount(0)
        if not build:
            self.summary.setText('Сначала скачайте страницы или откройте сохранённый запуск.')
            self.show_page()
            return
        warnings = issues(build)
        self.summary.setText(f'Скачано страниц: {len(build.pages)}. С замечаниями: {sum(bool(value) for value in warnings.values())}. Сборка ожидает вашего выбора; страницы сохранены.')
        self.table.setRowCount(len(build.pages))
        for row, page in enumerate(build.pages):
            original = build.metadata_review[page.key]['original']
            values = [page.route + ' · ' + page.title, str(len(original['title'])), str(len(original['description'])), '; '.join(warnings[page.key]) or 'Без замечаний']
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        self.table.selectRow(min(selected, len(build.pages) - 1))
        self.show_page()

    def show_page(self, *_):
        row = self.table.currentRow()
        if not self.build or not 0 <= row < len(self.build.pages):
            for text in self.fields.values():
                text.clear()
            for note in self.notes.values():
                note.clear()
            self.content.clear()
            self.canonical.clear()
            return
        page = self.build.pages[row]
        data = self.build.metadata_review[page.key]
        self.canonical.setText('Каноникл при сборке: ' + self.build.request.origin + page.route)
        for variant in ('original', 'proposed'):
            for field in ('title', 'description'):
                self.fields[variant, field].setPlainText(data.get(variant, {}).get(field, ''))
                self.fields[variant, field].setPlaceholderText('Отсутствует' if variant == 'original' else 'Нажмите «Подготовить варианты агентом»')
            notes = issues(self.build, variant)[page.key] if variant == 'original' or 'proposed' in data or 'error' in data else []
            self.notes[variant].setText('; '.join(notes))
        self.content.setPlainText(content_node(parse_html(page.html)).get_text('\n', strip=True))

    def set_busy(self, busy, agent):
        ready = self.build is not None and not busy
        self.keep_button.setEnabled(ready)
        self.apply_button.setEnabled(ready and can_apply(self.build))
        self.generate_button.setEnabled(ready and agent is not None)
        self.connect_button.setEnabled(not busy)
        self.view_button.setEnabled(ready)
        self.agent_label.setText('Агент: ' + agent.config.model if agent else 'Агент не подключён. Можно подключить его или продолжить с исходными значениями.')

    def view_page(self):
        row = self.table.currentRow()
        if self.build and row >= 0:
            DownloadedPreview(self.build, self.build.pages[row].route, self).exec()
