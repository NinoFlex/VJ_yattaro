from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import QTableView, QHeaderView


class RightTableModel(QAbstractTableModel):
    """Generic right-pane table model used by Rekordbox and Shazam history."""

    def __init__(self, data=None, headers=None, max_rows=10):
        super().__init__()
        self._max_rows = max(1, int(max_rows))
        self._data = list(data or [])[:self._max_rows]
        self._headers = headers or ["トラックタイトル", "アーティスト", "コメント"]

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self._headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None

        if role == Qt.DisplayRole:
            row = index.row()
            col = index.column()
            item = self._data[row]
            if isinstance(item, (list, tuple)) and col < len(item):
                return item[col]
            return ""

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            if section < len(self._headers):
                return self._headers[section]
        return None

    def update_data(self, new_data):
        """Replace table contents only when they actually changed.

        QAbstractItemModel.reset invalidates QTableView's selection model.
        HistoryWatcher polls periodically even when the DB contents are unchanged,
        so avoiding a needless reset keeps the user's selected row intact.

        Returns True when the model was reset, otherwise False.
        """
        normalized = list(new_data or [])[:self._max_rows]
        if normalized == self._data:
            return False

        self.beginResetModel()
        self._data = normalized
        self.endResetModel()
        return True


class RightTableView(QTableView):
    """Right-pane custom table view."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setSelectionMode(QTableView.SingleSelection)
        self.setEditTriggers(QTableView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.horizontalHeader().setHighlightSections(False)
        self.verticalHeader().setDefaultSectionSize(27)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self._theme = "light"
        self.apply_theme(self._theme)

    def apply_theme(self, theme):
        from ui.theme import colors, normalize_theme
        self._theme = normalize_theme(theme)
        c = colors(self._theme)
        self.setStyleSheet(f"""
            QTableView {{
                background-color: {c['panel']};
                alternate-background-color: {c['panel_alt']};
                selection-background-color: {c['selection_soft']};
                selection-color: {c['selection_text']};
                color: {c['text']};
                border: 1px solid {c['border_soft']};
                border-radius: 4px;
                outline: none;
            }}
            QTableView::item {{
                padding: 3px 4px;
            }}
            QTableView::item:selected {{
                background-color: {c['selection']};
                color: {c['selection_text']};
            }}
            QHeaderView::section {{
                background-color: {c['header']};
                color: {c['text']};
                border: none;
                border-right: 1px solid {c['border']};
                border-bottom: 1px solid {c['border']};
                padding: 3px 4px;
            }}
        """)

    def setModel(self, model):
        super().setModel(model)
        if model.rowCount() > 0:
            self.selectRow(0)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            if not (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier)):
                current_row = self.currentIndex().row()
                if event.key() == Qt.Key_Up and current_row > 0:
                    self.selectRow(current_row - 1)
                    print(f"RightTableView: Moved selection from {current_row} to {current_row-1} (arrow key)")
                elif event.key() == Qt.Key_Down and current_row < self.model().rowCount() - 1:
                    self.selectRow(current_row + 1)
                    print(f"RightTableView: Moved selection from {current_row} to {current_row+1} (arrow key)")
                return
        else:
            event.ignore()
            super().keyPressEvent(event)
