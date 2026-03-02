from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import QTableView, QHeaderView


class RightTableModel(QAbstractTableModel):
    """
    右ペインの履歴や情報を表示するためのデータモデル
    """
    def __init__(self, data=None):
        super().__init__()
        # 最大10行の制約があるため、初期データも制限
        self._data = data or []
        self._headers = ["トラックタイトル", "アーティスト", "コメント"]

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return 3

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        
        if role == Qt.DisplayRole:
            row = index.row()
            col = index.column()
            # データの構造に合わせて取得 (例: tuple or dict)
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
        """
        データを更新し、最大10件に制限する
        """
        self.beginResetModel()
        self._data = new_data[:10]
        self.endResetModel()


class RightTableView(QTableView):
    """
    右ペインのカスタムテーブルビュー
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 基本設定
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QTableView.SelectRows)  # 行単位の選択を設定
        self.setSelectionMode(QTableView.SingleSelection) # 単一選択
        self.setEditTriggers(QTableView.NoEditTriggers)   # 直接の編集を無効化
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)  # グリッドを非表示にして「行」の一体感を出す
        
        # ヘッダーの選択強調をオフにする
        self.horizontalHeader().setHighlightSections(False)
        
        # 行の高さをクリックしやすいサイズに設定
        self.verticalHeader().setDefaultSectionSize(32)
        
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 行全体の選択・ホバーを自然に見せるためのスタイル
        self.setStyleSheet("""
            QTableView {
                background-color: white;
                alternate-background-color: #f9f9f9;
                selection-background-color: #e3f2fd;
                selection-color: black;
                border: 1px solid #ddd;
                border-radius: 4px;
                outline: none; /* フォーカスの点線（focus rect）を表示させない */
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
        # データがある場合、デフォルトで最初の行を選択
        if model.rowCount() > 0:
            self.selectRow(0)
    
    def keyPressEvent(self, event):
        """キーイベント処理"""
        if (event.key() == Qt.Key_Up or event.key() == Qt.Key_Down):
            # 上下矢印キーで選択移動（修飾キーなしの場合のみ）
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
            # その他のキーは無視してグローバルホットキーに委譲
            event.ignore()
            super().keyPressEvent(event)
