from collections import Counter
from PySide6.QtCore import Signal, Qt, QDateTime
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.audit import audit, CATEGORIES, SCOPE


def money_text(usage):
    calls = usage['calls']
    if calls is None:
        return 'Расходы этого запуска ранее не учитывались; точная сумма неизвестна.'
    amount = usage['cost_usd']
    price = f'${amount:.6f}'.rstrip('0').rstrip('.') if amount is not None else 'неизвестна'
    text = f'Вызовов агента в этом запуске: {calls}. Стоимость API: {price}.'
    if amount is None:
        text += f' Подтверждено провайдером: ${usage["known_cost_usd"]:.6f}; без стоимости: {usage["unknown_cost_calls"]}.'
    if not usage['complete_history']:
        text += ' До включения учёта история неполная.'
    if calls:
        text += f' Токены по полученным данным: {usage["prompt_tokens"]} входящих / {usage["completion_tokens"]} исходящих.'
    return text


class ChecksPanel(QWidget):
    updated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.build = None
        self.report = None
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.status = QLabel('Сначала завершите сборку или откройте готовый проект.')
        self.status.setWordWrap(True)
        top.addWidget(self.status, 1)
        self.refresh = QPushButton('Проверить снова')
        self.refresh.setEnabled(False)
        self.refresh.clicked.connect(self.run_checks)
        top.addWidget(self.refresh)
        layout.addLayout(top)
        self.expenses = QLabel('')
        self.expenses.setWordWrap(True)
        self.expenses.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.expenses)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Страница', 'Ссылки', 'Язык', 'Скрипты', 'Каноникл', 'WordPress'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(310)
        self.table.itemSelectionChanged.connect(self.show_details)
        layout.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(210)
        layout.addWidget(self.details, 1)
        scope = QLabel(SCOPE + '\nРасходы относятся к API внутри DropRestorer. Тесты подключения показаны отдельно на вкладке «Агенты».')
        scope.setWordWrap(True)
        layout.addWidget(scope)

    def set_build(self, build):
        self.build = build
        self.refresh.setEnabled(build is not None)
        if build:
            self.run_checks()
        else:
            self.report = None
            self.table.setRowCount(0)
            self.details.clear()
            self.expenses.clear()
            self.status.setText('Сначала завершите сборку или откройте готовый проект.')

    def run_checks(self):
        if not self.build:
            return
        try:
            self.report = audit(self.build)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.report = None
            self.status.setText('Не удалось проверить файлы. Упаковка недоступна.')
            self.table.setRowCount(0)
            self.details.setPlainText(type(error).__name__)
            self.updated.emit({'passed': False})
            return
        report = self.report
        title = 'Проверки пройдены' if report['passed'] else 'Упаковка заблокирована'
        checked = QDateTime.fromString(report['checked_at'], Qt.DateFormat.ISODateWithMs).toLocalTime().toString('dd.MM.yyyy HH:mm:ss')
        self.status.setText(f'{title}. Страниц: {len(report["pages"])} · Ошибок: {report["errors"]} · Замечаний: {report["warnings"]}\nПроверено: {checked}')
        self.expenses.setText(money_text(report['usage']))
        self.table.blockSignals(True)
        self.table.setRowCount(len(report['pages']))
        for index, page in enumerate(report['pages']):
            def status(key, success='OK'):
                count = page['checks'][key]
                return f'Ошибок: {count}' if count else success
            values = [page['route'], status('links', str(page['links']) + ' ✓'), status('languages', page['lang']),
                      status('scripts'), status('canonical'), status('export')]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(page['title'] if column == 0 else page['canonical'] if column == 4 else value)
                self.table.setItem(index, column, item)
        self.table.blockSignals(False)
        if report['pages']:
            self.table.selectRow(0)
        self.show_details()
        self.updated.emit(report)

    def show_details(self):
        if not self.report:
            return
        report = self.report
        index = self.table.currentRow()
        page = report['pages'][index] if 0 <= index < len(report['pages']) else None
        route = page['route'] if page else ''
        lines = []
        if page:
            lines = [page['title'], 'Каноникл: ' + page['canonical'],
                     'Метаданные: ' + ('задаёт владелец в WordPress' if page['casino'] else 'сохранены согласно выбранному варианту'), '']
        findings = [row for row in report['findings'] if row['route'] in ('', route)]
        lines += [(('ОШИБКА' if row['severity'] == 'error' else 'Замечание') + ' · ' + CATEGORIES[row['category']] + ': ' + row['detail']) for row in findings]
        if not findings:
            lines.append('В этой странице и общих ресурсах нарушений правил не найдено.')
        lines += ['', 'Удалено при сборке:']
        removed = [row for row in report['removed'] if row.get('route', '') in ('', route)]
        counts = Counter()
        for row in removed:
            counts[row['detail']] += row.get('count', 1)
        lines += [detail + f': {count}' for detail, count in counts.items() if count]
        if not removed:
            lines.append('Нет записей об удалении для этой страницы.')
        lines += ['', 'Вызовы агента:']
        for row in report['usage']['events']:
            cost = f'${row["cost_usd"]:.6f}' if row.get('cost_usd') is not None else 'стоимость неизвестна'
            operation = {'metadata': 'Метаданные', 'logo': 'Логотип', 'favicon': 'Фавикон', 'connection_test': 'Тест подключения'}.get(row['operation'], 'Запрос')
            state = {'received': 'Ответ получен', 'pending': 'Нет подтверждённого результата', 'failed': 'Ошибка запроса'}.get(row['status'], row['status'])
            lines.append(f'{operation} · {row["model"]} · {state} · {cost}')
        self.details.setPlainText('\n'.join(lines))


def show_checks(build, parent=None, updated=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle('DropRestorer — Проверки и расходы')
    dialog.resize(1080, 850)
    layout = QVBoxLayout(dialog)
    panel = ChecksPanel(dialog)
    if updated:
        panel.updated.connect(updated)
    panel.set_build(build)
    layout.addWidget(panel)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText('Закрыть')
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
