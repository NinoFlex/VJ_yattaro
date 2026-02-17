import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
    QFrame, QPushButton, QLabel
)
from ui.widgets.right_table_view import RightTableView, RightTableModel

class TitleBar(QWidget):
    """
    カスタムタイトルバー (ライトテーマ版)
    """
    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self.setFixedHeight(32)
        self.setStyleSheet("""
            QWidget {
                background-color: #f0f0f0;
                color: #333333;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                border-bottom: 1px solid #ddd;
            }
            QPushButton {
                background-color: transparent;
                border: none;
                color: #333333;
                font-size: 14px;
                padding: 0 12px;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            #close_button:hover {
                background-color: #e81123;
                color: white;
            }
            #settings_button {
                font-size: 11px;
                background-color: #ffffff;
                border: 1px solid #ccc;
                border-radius: 4px;
                margin: 4px 8px;
                padding: 0 10px;
                color: #333;
            }
            #settings_button:hover {
                background-color: #f8f8f8;
                border-color: #999;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(0)

        # ⚙ 詳細設定ボタン (左側)
        self.settings_button = QPushButton("⚙ 詳細設定")
        self.settings_button.setObjectName("settings_button")
        self.settings_button.clicked.connect(self._main_window.open_settings)
        layout.addWidget(self.settings_button)

        # タイトル
        self.title_label = QLabel("VJ_yattaro")
        self.title_label.setStyleSheet("font-weight: bold; color: #666; margin-left: 5px;")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_label)
        
        layout.addStretch()

        # 最小化ボタン
        self.min_button = QPushButton("—")
        self.min_button.clicked.connect(self._main_window.showMinimized)
        layout.addWidget(self.min_button)

        # 閉じるボタン
        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("close_button")
        self.close_button.clicked.connect(self._main_window.close)
        layout.addWidget(self.close_button)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.window().windowHandle().startSystemMove()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VJ_yattaro")
        self.resize(1920, 240)
        
        # 枠なしウィンドウ設定
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # 外枠コンテナ (ライトテーマ用)
        self.main_container = QFrame()
        self.main_container.setStyleSheet("""
            QFrame#main_container {
                background-color: #ffffff;
                border: 1px solid #ccc;
                border-radius: 8px;
            }
        """)
        self.main_container.setObjectName("main_container")
        self.setCentralWidget(self.main_container)

        self.root_layout = QVBoxLayout(self.main_container)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)

        # カスタムタイトルバー
        self.title_bar = TitleBar(self)
        self.root_layout.addWidget(self.title_bar)

        # メインコンテンツ
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(10)
        self.root_layout.addWidget(content_widget, 1)

        # 左ペイン
        self.left_pane = QFrame()
        self.left_pane.setFrameShape(QFrame.StyledPanel)
        self.left_pane.setMinimumWidth(800)
        self.left_pane.setStyleSheet("background-color: #fafafa; border: 1px solid #ddd; border-radius: 4px;")
        content_layout.addWidget(self.left_pane, 3)

        # 右ペイン
        self.right_table = RightTableView()
        # テーブル周囲の枠線設定
        self.right_table.setStyleSheet("border: 1px solid #ddd; border-radius: 4px;")
        content_layout.addWidget(self.right_table, 2)
        
        # 履歴監視サービスの初期化
        from app.services.history_watcher import HistoryWatcher
        self.watcher = HistoryWatcher()
        
        # 初期データの取得とモデル設定
        initial_history = self.watcher.service.get_latest_history(limit=10)
        self.table_model = RightTableModel(initial_history)
        self.right_table.setModel(self.table_model)
        
        # 信号の接続
        self.watcher.updated.connect(self.on_history_updated)
        self.watcher.new_track_detected.connect(self.on_new_track_detected)
        self.watcher.start()

    def open_settings(self):
        """詳細設定画面を別ウィンドウとして開く"""
        from ui.dialogs.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self)
        if dialog.exec():
            print("UI: Settings dialog accepted.")
        else:
            print("UI: Settings dialog cancelled.")

    def on_history_updated(self, new_history):
        """Watcherから新しい履歴データを受け取った時の処理"""
        # 現在の選択行（インデックス）を退避
        selection_model = self.right_table.selectionModel()
        if not selection_model:
            return

        current_indexes = selection_model.selectedRows()
        current_row = current_indexes[0].row() if current_indexes else -1

        # モデルのデータを更新
        self.table_model.update_data(new_history)

        # 選択状態を再適用
        if current_row != -1:
            self.right_table.selectRow(current_row)

    def on_new_track_detected(self, track):
        """新しい曲が検出された時の処理"""
        # 最上段（最新曲）を選択
        self.right_table.selectRow(0)
        print(f"UI: New track detected! Auto-selected row 0.")


def main():
    app = QApplication(sys.argv)
    
    # 日本語文字化け対策: フォントの設定
    from PySide6.QtGui import QFont
    font = QFont("Meiryo UI", 10)
    if not QFont("Meiryo UI").exactMatch():
        font = QFont("MS Gothic", 10)
        if not QFont("MS Gothic").exactMatch():
            font = QFont("sans-serif", 10)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
