from PySide6.QtWidgets import QListView, QAbstractItemView
from PySide6.QtCore import Qt, QSize, QAbstractListModel, QModelIndex
from PySide6.QtGui import QPixmap

class YouTubeListModel(QAbstractListModel):
    """
    YouTube検索結果を管理するモデル
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._videos = []
    
    def rowCount(self, parent=QModelIndex()):
        return len(self._videos)
    
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._videos):
            return None
        
        video = self._videos[index.row()]
        
        if role == Qt.DisplayRole:
            return {
                'video_id': video.get('video_id', ''),
                'title': video.get('title', ''),
                'thumbnail': video.get('thumbnail', QPixmap()),
                'duration': video.get('duration', ''),
                'url': video.get('url', '')
            }
        
        return None
    
    def set_videos(self, videos):
        """動画リストを設定"""
        self.beginResetModel()
        self._videos = videos
        self.endResetModel()
    
    def get_video_at(self, index):
        """指定インデックスの動画情報を取得"""
        if 0 <= index < len(self._videos):
            return self._videos[index]
        return None


class YouTubeListView(QListView):
    """
    YouTube検索結果を表示するリストビュー
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 基本設定
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(False)  # 折り返しを無効にして横スクロールのみ
        self.setResizeMode(QListView.Adjust)
        self.setSpacing(0)  # スペースをゼロにして最大限に表示
        self.setMovement(QListView.Static)
        
        # 選択設定
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        
        # 選択シグナルを接続
        self.clicked.connect(self.on_item_clicked)
        self.pressed.connect(self.on_item_pressed)
        
        # スクロールバー設定
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # 垂直スクロールは無効に
        
        # 横スクロールを優先する設定
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)  # ピクセル単位でスムーズにスクロール
        
        # モデルとデリゲートを設定
        self.model = YouTubeListModel()
        self.setModel(self.model)
        
        from .youtube_delegate import YouTubeItemDelegate
        self.delegate = YouTubeItemDelegate(self)  # 親を設定
        self.setItemDelegate(self.delegate)
        
        # サイズ設定 - 高さ180pxを維持
        self.setMinimumHeight(180)  # 高さを維持
        self.setMaximumHeight(180)  # 高さを固定
        
        # スタイルシートのエラーを修正
        self.setStyleSheet("""
            QListView {
                background-color: #fafafa; 
                border: 1px solid #ddd; 
                border-radius: 4px;
            }
        """)
    
    def set_search_results(self, videos):
        """検索結果を設定"""
        self.model.set_videos(videos)
        
        # 最初のアイテムを選択
        if self.model.rowCount() > 0:
            first_index = self.model.index(0, 0)
            self.setCurrentIndex(first_index)
    
    def get_selected_video(self):
        """選択中の動画情報を取得"""
        current_index = self.currentIndex()
        if current_index.isValid():
            return self.model.get_video_at(current_index.row())
        return None
    
    def clear_results(self):
        """検索結果をクリア"""
        self.model.set_videos([])
    
    def on_item_clicked(self, index):
        """アイテムクリック時の処理"""
        self.setCurrentIndex(index)
        print(f"YouTubeListView: Selected video at index {index.row()}")
    
    def on_item_pressed(self, index):
        """アイテムプレス時の処理"""
        self.setCurrentIndex(index)
    
    def keyPressEvent(self, event):
        """キーイベント処理"""
        if event.key() == Qt.Key_Left or event.key() == Qt.Key_Right:
            # 左右矢印キーで選択移動（修飾キーなしの場合のみ）
            if not (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier)):
                current_index = self.currentIndex()
                if not current_index.isValid():
                    return
                
                row = current_index.row()
                if event.key() == Qt.Key_Left and row > 0:
                    new_index = self.model.index(row - 1, 0)
                    self.setCurrentIndex(new_index)
                    print(f"YouTubeListView: Moved selection from {row} to {row-1} (arrow key)")
                elif event.key() == Qt.Key_Right and row < self.model.rowCount() - 1:
                    new_index = self.model.index(row + 1, 0)
                    self.setCurrentIndex(new_index)
                    print(f"YouTubeListView: Moved selection from {row} to {row+1} (arrow key)")
                return
        else:
            # その他のキーは無視してグローバルホットキーに委譲
            event.ignore()
            super().keyPressEvent(event)
