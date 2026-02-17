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
        
        # 外観を標準のライトテーマにリセット
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setSelectionMode(QTableView.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(True)
        self.setGridStyle(Qt.SolidLine)
        
        # スタイルシート（ライトテーマ用）
        self.setStyleSheet("""
            QTableView {
                background-color: #ffffff;
                alternate-background-color: #f9f9f9;
                color: #000000;
                gridline-color: #eeeeee;
                selection-background-color: #0078d7;
                selection-color: #ffffff;
                border: none;
                outline: none;
                font-family: 'Meiryo UI', 'MS Gothic', sans-serif;
            }
            QTableView::item {
                color: #000000; /* 文字色を黒に固定 */
                padding: 4px;
                border-bottom: 1px solid #eeeeee;
            }
            QTableView::item:selected {
                background-color: #0078d7;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #f0f0f0;
                color: #333333;
                padding: 6px;
                border: none;
                border-bottom: 1px solid #cccccc;
                font-weight: bold;
                text-align: left;
            }
        """)
        
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
    def setModel(self, model):
        super().setModel(model)
        # データがある場合、デフォルトで最初の行を選択
        if model.rowCount() > 0:
            self.selectRow(0)
        
        # データ変更時に選択状態を維持・調整するロジックは必要に応じて追加
