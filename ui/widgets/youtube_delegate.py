from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOption
from PySide6.QtCore import Qt, QSize, QRect
from PySide6.QtGui import QPainter, QPixmap, QFont, QBrush, QColor, QPen

class YouTubeItemDelegate(QStyledItemDelegate):
    """
    YouTube動画のサムネイルと情報を表示するカスタムデリゲート
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.thumbnail_size = QSize(320, 180)  # 16:9比
        self.item_height = 180  # 高さは維持
        self.item_width = 320  # 16:9比を維持
        self.padding = 0  # パディングをゼロに
        self.preloaded_state = None  # preloading/ready
        self.preloaded_video_id = None
        self.playing_video_id = None
    
    def set_video_state(self, state, video_id=None):
        """現在の動画状態を設定"""
        print(f"Delegate: set_video_state called - state: {state}, video_id: {video_id}")

        if state == 'playing':
            self.playing_video_id = video_id
        elif state in ['preloading', 'ready']:
            self.preloaded_state = state
            self.preloaded_video_id = video_id
        elif state is None:
            self.preloaded_state = None
            self.preloaded_video_id = None
            self.playing_video_id = None
        
        # 親ウィジェット（QListView）を取得して全アイテムを再描画
        parent_widget = self.parent()
        if parent_widget:
            try:
                # 全アイテムを再描画して現在の動画の枠線を維持
                parent_widget.viewport().update()
                print(f"Delegate: Updated all items to maintain current video border")
            except Exception as e:
                print(f"Delegate: Error updating viewport: {e}")
        else:
            print(f"Delegate: No parent widget available")
    
    def sizeHint(self, option, index):
        """アイテムのサイズを返す"""
        return QSize(self.item_width, self.item_height)
    
    def paint(self, painter, option, index):
        """アイテムを描画する"""
        # データを取得
        data = index.data(Qt.DisplayRole)
        if not data:
            return
        
        # 背景を描画
        if option.state & QStyle.State_Selected:
            # 選択状態：白背景
            painter.fillRect(option.rect, QBrush(Qt.white))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, QColor(240, 240, 240))
        else:
            painter.fillRect(option.rect, QBrush(Qt.white))
        
        # サムネイル領域（アイテム全体を使用）
        thumbnail_rect = QRect(
            option.rect.left(),
            option.rect.top(),
            self.item_width,
            self.item_height
        )
        
        # サムネイルを描画
        thumbnail = data.get('thumbnail', None)
        if thumbnail and not thumbnail.isNull():
            # サムネイルが存在する場合 - アイテム全体にフィット
            scaled_pixmap = thumbnail.scaled(
                thumbnail_rect.size(),
                Qt.KeepAspectRatio,  # アスペクト比を維持
                Qt.SmoothTransformation
            )
            painter.drawPixmap(thumbnail_rect, scaled_pixmap)
        else:
            # サムネイルがない場合のプレースホルダー
            painter.fillRect(thumbnail_rect, QColor(230, 230, 230))
            painter.setPen(QColor(150, 150, 150))
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            painter.drawText(thumbnail_rect, Qt.AlignCenter, "No Image")
        
        # 動画時間を右下に表示
        duration = data.get('duration', '')
        if duration:
            font = QFont()
            font.setPointSize(9)  # 小さめのサイズ
            font.setBold(True)
            painter.setFont(font)
            
            # 時間表示の背景
            time_rect = QRect(
                option.rect.right() - 45,  # 右端から45px
                option.rect.bottom() - 23,  # 下端から23px
                40,  # 幅
                18   # 高さ
            )
            
            # 半透明の黒背景
            painter.fillRect(time_rect, QColor(0, 0, 0, 153))  # 透明度60%
            
            # 白色文字で時間を表示
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(time_rect, Qt.AlignCenter, duration)
        
        # タイトル領域（上側に重ねて表示）
        title_rect = QRect(
            option.rect.left(),
            option.rect.top() + 5,  # 上端から5px
            self.item_width,
            25  # 高さ
        )
        
        # 半透明のグレー背景
        overlay_color = QColor(0, 0, 0, 102)  # 透明度60%の黒
        painter.fillRect(title_rect, overlay_color)
        
        # タイトルを描画
        title = data.get('title', '')
        if title:
            font = QFont()
            font.setPointSize(12)  # 大きくして読みやすく
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255))  # 白色文字
            
            # 長いタイトルは省略
            elided_title = painter.fontMetrics().elidedText(
                title, Qt.ElideRight, title_rect.width() - 10  # 左右に5pxずつ余白
            )
            painter.drawText(
                title_rect.adjusted(5, 1, -5, -1),  # 内側に余白
                Qt.AlignLeft | Qt.AlignVCenter,
                elided_title
            )
        
        # 選択状態の枠線を描画
        if option.state & QStyle.State_Selected:
            pen = QPen(QColor(165, 42, 42))  # 茶 #a52a2a
            pen.setWidth(4)
            painter.setPen(pen)
            painter.drawRect(option.rect.adjusted(2, 2, -2, -2))
        
        # 状態を持つ動画の枠線を描画（選択状態に関わらず）- 最前面に表示
        data = index.data(Qt.DisplayRole)
        video_id = data.get('video_id', '') if data else ''

        if self.preloaded_video_id == video_id and self.preloaded_state:
            if self.preloaded_state == 'preloading':
                border_color = QColor(255, 215, 0)  # 金色
            else:
                border_color = QColor(0, 255, 0)  # 緑色
            painter.setPen(QPen(border_color, 4))
            painter.drawRect(option.rect.adjusted(2, 2, -2, -2))

        if self.playing_video_id == video_id:
            painter.setPen(QPen(QColor(0, 123, 255), 4))
            painter.drawRect(option.rect.adjusted(2, 2, -2, -2))
