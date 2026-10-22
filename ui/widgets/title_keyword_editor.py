"""Settings-only editor. Edits are a private copy until the dialog is applied."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from app.services.title_keywords import MAX_KEYWORDS, normalize_keywords, strip_title_keywords


class KeywordLineEdit(QLineEdit):
    """Enter adds a keyword, never activates the dialog's default Apply button."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._composing = False

    def inputMethodEvent(self, event):
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._composing:
                super().keyPressEvent(event)
            else:
                self.returnPressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class TitleKeywordEditor(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._keywords = []
        layout = QVBoxLayout(self)
        description = QLabel(
            "曲名から除去する文字列を登録します（最大50件）。\n"
            "Rekordbox / Shazam共通。仮登録・確定登録・検索に適用します。\n"
            "括弧・記号も含めて入力してください。英大小文字は区別せず、全角・半角は区別します。")
        description.setWordWrap(True)
        layout.addWidget(description)
        add_row = QHBoxLayout()
        self.keyword_edit = KeywordLineEdit()
        self.keyword_edit.setPlaceholderText("例: (Instrumental) / -Instrumental- / （TVサイズ）")
        add_row.addWidget(self.keyword_edit, 1)
        self.add_button = QPushButton("追加")
        self.add_button.setAutoDefault(False)
        self.add_button.clicked.connect(self.add_keyword)
        self.keyword_edit.returnPressed.connect(self.add_keyword)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["No.", "除去キーワード"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setMinimumHeight(155)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        self.count_label = QLabel()
        controls.addWidget(self.count_label, 1)
        self.delete_button = QPushButton("選択した項目を削除")
        self.delete_button.setAutoDefault(False)
        self.delete_button.clicked.connect(self.delete_selected)
        controls.addWidget(self.delete_button)
        layout.addLayout(controls)
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)
        self.preview_edit = KeywordLineEdit()
        self.preview_edit.setPlaceholderText("動作確認用の曲名（保存されません）")
        self.preview_edit.textChanged.connect(self._update_preview)
        layout.addWidget(self.preview_edit)
        self.preview_label = QLabel("除去後: ")
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextFormat(Qt.PlainText)
        layout.addWidget(self.preview_label)
        self._refresh()

    def keywords(self):
        return list(self._keywords)

    def set_keywords(self, values):
        self._keywords = normalize_keywords(values)
        self.message_label.clear()
        self._refresh()

    def add_keyword(self):
        text = self.keyword_edit.text().strip()
        if not text or "\r" in text or "\n" in text:
            self.message_label.setText("空欄や改行を含む項目は登録できません。")
            return
        for i, word in enumerate(self._keywords):
            if word.casefold() == text.casefold():
                self.table.selectRow(i)
                self.message_label.setText("この項目は登録済みです。")
                return
        if len(self._keywords) >= MAX_KEYWORDS:
            self.message_label.setText("登録は50件までです。")
            return
        self._keywords.append(text)
        self.keyword_edit.clear()
        self.message_label.clear()
        self._refresh(len(self._keywords) - 1)

    def delete_selected(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self._keywords):
            return
        del self._keywords[row]
        self.message_label.clear()
        self._refresh(min(row, len(self._keywords) - 1))

    def _selection_changed(self):
        self.delete_button.setEnabled(bool(self.table.selectedItems()))

    def _refresh(self, select_row=-1):
        self.table.setRowCount(len(self._keywords))
        for row, keyword in enumerate(self._keywords):
            self.table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self.table.setItem(row, 1, QTableWidgetItem(keyword))
        if 0 <= select_row < len(self._keywords):
            self.table.selectRow(select_row)
        self.count_label.setText(f"登録数: {len(self._keywords)} / {MAX_KEYWORDS}")
        self.add_button.setEnabled(len(self._keywords) < MAX_KEYWORDS)
        self._selection_changed()
        self._update_preview()

    def _update_preview(self, *_args):
        original = self.preview_edit.text()
        result = strip_title_keywords(original, self._keywords)
        self.preview_label.setText("除去後: " + result)
