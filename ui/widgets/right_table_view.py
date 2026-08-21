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
        self.beginResetModel()
        self._data = list(new_data or [])[:self._max_rows]
        self.endResetModel()


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
        self.verticalHeader().setDefaultSectionSize(32)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.setStyleSheet("""
            QTableView {
                background-color: white;
                alternate-background-color: #f9f9f9;
                selection-background-color: #e3f2fd;
                selection-color: black;
                border: 1px solid #ddd;
                border-radius: 4px;
                outline: none;
            }
            QTableView::item {
                padding: 5px;
            }
            QTableView::item:selected {
                background-color: #bbdefb;
                color: black;
            }
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
