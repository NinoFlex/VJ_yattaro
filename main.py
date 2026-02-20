import sys
import webbrowser
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
    QFrame, QPushButton, QLabel, QLineEdit
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
        self.title_label = QLabel("あんたの代わりにVJやっ太郎")
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
    def _configure_logging(self):
        """ログレベルを設定"""
        from app.utils.logger import configure_logging, LogLevel
        
        # 設定からログレベルを取得
        log_level_str = self.config_service.get("log_level", "INFO")
        log_level_map = {
            "DEBUG": LogLevel.DEBUG,
            "INFO": LogLevel.INFO,
            "WARNING": LogLevel.WARNING,
            "ERROR": LogLevel.ERROR
        }
        log_level = log_level_map.get(log_level_str.upper(), LogLevel.INFO)
        
        # ログを設定
        configure_logging(log_level, enabled=True)
        print(f"UI: Logging configured at level {log_level_str}")

    def __init__(self):
        super().__init__()
        self.setWindowTitle("VJ_yattaro")
        self.resize(1920, 240)
        
        # 前面化状態管理
        self._is_bringing_to_front = False
        self._last_front_time = 0
        
        # メモリ管理
        self._memory_check_timer = QTimer(self)
        self._memory_check_timer.timeout.connect(self._check_memory_usage)
        self._memory_check_timer.start(30000)  # 30秒ごとにチェック
        
        # 画面一番下にウィンドウを配置
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geometry = screen.availableGeometry()
            window_width = 1920
            window_height = 240
            x = (screen_geometry.width() - window_width) // 2  # 中央揃え
            y = screen_geometry.height() - window_height - 10  # 下から10px上
            self.move(x, y)
        
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
        content_layout.setContentsMargins(5, 5, 5, 5)  # マージンを縮小
        content_layout.setSpacing(5)  # スペースを縮小
        self.root_layout.addWidget(content_widget, 1)

        # 左ペイン
        from ui.widgets.youtube_list_view import YouTubeListView
        self.left_pane = YouTubeListView()
        # スタイルシートはYouTubeListView内部で設定
        content_layout.addWidget(self.left_pane, 3)
        
        # YouTubeリストにフォーカスを設定
        self.left_pane.setFocusPolicy(Qt.StrongFocus)

        # 右ペイン
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(5)
        
        # YouTube検索ボックス
        from PySide6.QtWidgets import QLineEdit
        self.youtube_search_box = QLineEdit()
        self.youtube_search_box.setPlaceholderText("YouTube検索 (Enterで実行)")
        self.youtube_search_box.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 1px solid #ddd;
                border-radius:4px;
                font-size: 12px;
                background-color: white;
            }
            QLineEdit:focus {
                border: 2px solid #4CAF50;
            }
        """)
        self.youtube_search_box.returnPressed.connect(self.search_youtube_from_box)
        right_layout.addWidget(self.youtube_search_box)
        
        # 右テーブル
        self.right_table = RightTableView()
        # テーブル周囲の枠線設定
        self.right_table.setStyleSheet("border: 1px solid #ddd; border-radius:4px;")
        right_layout.addWidget(self.right_table)
        
        # 右テーブルにもフォーカスを設定
        self.right_table.setFocusPolicy(Qt.StrongFocus)
        
        content_layout.addWidget(right_container, 2)
        
        # 設定サービスの初期化
        from app.services.config_service import ConfigService
        self.config_service = ConfigService()
        
        # ログレベルを設定
        self._configure_logging()
        
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
        
        # ホットキーサービスの初期化
        from app.services.hotkey_service import HotkeyService
        self.hotkey_service = HotkeyService()

        # ホットキー前面化→最背面化のタイマー
        self._bring_to_back_timer = QTimer(self)
        self._bring_to_back_timer.setSingleShot(True)
        self._bring_to_back_timer.timeout.connect(self._send_to_back)

        # ウィンドウ配置モードを反映
        self.apply_window_placement_mode()
        
        # ホットキーの登録
        self.reload_hotkeys()
        
        # ホットキーのシグナルを接続
        self.hotkey_service.move_up_triggered.connect(self.move_selection_up)
        self.hotkey_service.move_down_triggered.connect(self.move_selection_down)
        self.hotkey_service.move_left_triggered.connect(self.move_youtube_selection_left)
        self.hotkey_service.move_right_triggered.connect(self.move_youtube_selection_right)
        
        # YouTube操作用のホットキーを追加
        self.hotkey_service.preload_triggered.connect(self.preload_current_video)
        self.hotkey_service.play_triggered.connect(self.play_current_video)
        self.hotkey_service.search_triggered.connect(self.search_selected_track)
        
        # 右テーブルのダブルクリックシグナルを接続
        self.right_table.doubleClicked.connect(self.on_table_double_click)
        
        # YouTubeリストのダブルクリックシグナルを接続
        self.left_pane.doubleClicked.connect(self.on_youtube_double_click)
        
        # YouTube検索スレッドの管理
        self.youtube_search_thread = None
        
        # プレイヤーHTTPサーバーの初期化
        from app.services.player_http_server import start_player_server
        player_port = int(self.config_service.get("player_port", 8080))
        self._player_port = player_port
        self.player_server = start_player_server(port=player_port)
        
        # 状態フィードバック用のコールバックを設定
        self.player_server.set_state_callback(self._handle_player_feedback)
        print("UI: Player HTTP server started")

        # イベントフィルターをインストール（フォーカス管理用）
        self.installEventFilter(self)

        # 起動時にプレイヤー（player.html）を既定ブラウザで開く
        self._player_browser_opened = False
        self._open_player_in_browser()
        
        # YouTube動画の状態管理（初期化は_reset_youtube_stateで実施）
        self._reset_youtube_state()

    def _open_player_in_browser(self):
        """起動時にYouTubeプレイヤーを既定ブラウザで開く"""
        from app.utils.logger import info, error
        
        try:
            if self._player_browser_opened:
                return

            # サーバー配下の player.html を開き、デフォルト再生動画IDをクエリで渡す
            # 例: http://localhost:8080/player.html?defaultVideoId=xxxx
            port = int(self.config_service.get("player_port", 8080))
            default_video_id = "eyUUHfVm8Ik"
            url = f"http://localhost:{port}/player.html?defaultVideoId={default_video_id}"
            webbrowser.open(url, new=1, autoraise=True)
            self._player_browser_opened = True
            info(f"Opened player in browser: {url}", "UI")
        except Exception as e:
            error(f"Failed to open player in browser: {e}", "UI")

    def _reset_youtube_state(self):
        """YouTube動画の状態をリセット"""
        from app.utils.logger import info
        
        self.youtube_video_state = None
        self.preloaded_video_id = None
        self.last_clicked_video_id = None
        self.current_playing_video_id = None
        self.pending_play_video_id = None
        self._update_youtube_border_color_safe('#a52a2a')  # デフォルトの枠線色
        info("YouTube state reset to default", "UI")
    
    def open_settings(self):
        """詳細設定画面を別ウィンドウとして開く"""
        from ui.dialogs.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self)
        if dialog.exec():
            print("UI: Settings dialog accepted. Reflecting changes...")
            self.watcher.reload_settings()
            self.reload_hotkeys()  # ホットキーを再登録
            self.apply_window_placement_mode()  # ウィンドウ配置モードを反映
            self._restart_player_server_if_needed()  # プレイヤーサーバー設定を反映
        else:
            print("UI: Settings dialog cancelled.")

    def _restart_player_server_if_needed(self):
        """player_port が変更されていたらプレイヤーサーバーを再起動する"""
        try:
            new_port = int(self.config_service.get("player_port", 8080))
            old_port = int(getattr(self, "_player_port", 8080))
            if new_port == old_port:
                return

            from app.services.player_http_server import stop_player_server, start_player_server

            print(f"UI: Restarting player server due to port change: {old_port} -> {new_port}")
            stop_player_server()
            self.player_server = start_player_server(port=new_port)
            self.player_server.set_state_callback(self._handle_player_feedback)
            self._player_port = new_port

            # ブラウザ自動オープンをやり直したい場合は、再度開ける
            self._player_browser_opened = False
            self._open_player_in_browser()
        except Exception as e:
            print(f"UI: Failed to restart player server: {e}")

    def apply_window_placement_mode(self):
        """設定に基づいてウィンドウ配置モードを反映する"""
        always_on_top = bool(self.config_service.get("always_on_top", False))
        current_flags = self.windowFlags()

        if always_on_top:
            if not (current_flags & Qt.WindowStaysOnTopHint):
                self.setWindowFlags(current_flags | Qt.WindowStaysOnTopHint)
                self.show()
                print("UI: Window placement mode -> always on top")
        else:
            if current_flags & Qt.WindowStaysOnTopHint:
                self.setWindowFlags(current_flags & ~Qt.WindowStaysOnTopHint)
                self.show()
                print("UI: Window placement mode -> not always on top")

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
        
        # 元々の表から更新されていた場合、一番上の項目で自動で検索を実行
        if len(new_history) > 0:
            new_top_track = new_history[0]
            if hasattr(self, '_last_top_track') and self._last_top_track != new_top_track:
                # 一番上の項目が変更された場合
                print(f"UI: Top track updated from {self._last_top_track} to {new_top_track}")
                self._last_top_track = new_top_track
                
                # 一番上の項目で自動検索
                if len(new_top_track) >= 3:
                    track_title = new_top_track[0] or ""
                    artist = new_top_track[1] or ""
                    comment = new_top_track[2] or ""
                    
                    print(f"UI: Auto-searching YouTube for updated top track: {track_title} by {artist}")
                    self.search_youtube(track_title, artist, comment)
            elif not hasattr(self, '_last_top_track'):
                # 初回設定
                self._last_top_track = new_top_track
                print(f"UI: Initial top track set: {self._last_top_track}")

    def on_new_track_detected(self, track):
        """新しい曲が検出された時の処理"""
        # 最上段（最新曲）を選択
        self.right_table.selectRow(0)
        print(f"UI: New track detected! Auto-selected row 0.")
    
    def move_selection_up(self):
        """選択行を1つ上に移動する（右ペイン専用）"""
        # 設定に応じてウィンドウを最前面に表示
        if self.config_service.get("bring_to_front_on_hotkey", True):
            self._bring_to_front()
        
        selection_model = self.right_table.selectionModel()
        if not selection_model:
            return
        
        current_indexes = selection_model.selectedRows()
        if not current_indexes:
            # 何も選択されていない場合は最上行を選択
            self.right_table.selectRow(0)
            print("UI: No selection, selected row 0")
            return
        
        current_row = current_indexes[0].row()
        if current_row > 0:
            new_row = current_row - 1
            self.right_table.selectRow(new_row)
            print(f"UI: Moved selection from row {current_row} to {new_row}")
        else:
            print("UI: Already at the top row")
    
    def move_selection_down(self):
        """選択行を1つ下に移動する（右ペイン専用）"""
        # 設定に応じてウィンドウを最前面に表示
        if self.config_service.get("bring_to_front_on_hotkey", True):
            self._bring_to_front()
        
        selection_model = self.right_table.selectionModel()
        if not selection_model:
            return
        
        current_indexes = selection_model.selectedRows()
        if not current_indexes:
            # 何も選択されていない場合は最上行を選択
            self.right_table.selectRow(0)
            print("UI: No selection, selected row 0")
            return
        
        current_row = current_indexes[0].row()
        max_row = self.table_model.rowCount() - 1
        if current_row < max_row:
            new_row = current_row + 1
            self.right_table.selectRow(new_row)
            print(f"UI: Moved selection from row {current_row} to {new_row}")
        else:
            print("UI: Already at the bottom row")
    
    def move_youtube_selection_left(self):
        """YouTubeリストの選択を1つ左に移動"""
        # 設定に応じてウィンドウを最前面に表示
        if self.config_service.get("bring_to_front_on_hotkey", True):
            self._bring_to_front()
        
        current_index = self.left_pane.currentIndex()
        if not current_index.isValid():
            # 何も選択されていない場合は最初の動画を選択
            if self.left_pane.model.rowCount() > 0:
                first_index = self.left_pane.model.index(0, 0)
                self.left_pane.setCurrentIndex(first_index)
            return
        
        current_row = current_index.row()
        if current_row > 0:
            new_row = current_row - 1
            new_index = self.left_pane.model.index(new_row, 0)
            self.left_pane.setCurrentIndex(new_index)
            print(f"UI: Moved YouTube selection from {current_row} to {new_row}")
        else:
            print("UI: Already at the first YouTube video")
    
    def move_youtube_selection_right(self):
        """YouTubeリストの選択を1つ右に移動"""
        # 設定に応じてウィンドウを最前面に表示
        if self.config_service.get("bring_to_front_on_hotkey", True):
            self._bring_to_front()
        
        current_index = self.left_pane.currentIndex()
        if not current_index.isValid():
            # 何も選択されていない場合は最初の動画を選択
            if self.left_pane.model.rowCount() > 0:
                first_index = self.left_pane.model.index(0, 0)
                self.left_pane.setCurrentIndex(first_index)
            return
        
        current_row = current_index.row()
        max_row = self.left_pane.model.rowCount() - 1
        if current_row < max_row:
            new_row = current_row + 1
            new_index = self.left_pane.model.index(new_row, 0)
            self.left_pane.setCurrentIndex(new_index)
            print(f"UI: Moved YouTube selection from {current_row} to {new_row}")
        else:
            print("UI: Already at the last YouTube video")
    
    def _bring_to_front(self):
        """ウィンドウを確実に最前面に表示する（Windows対応）"""
        import sys
        import time
        
        # 重複呼び出しを防止（500ms以内は無視）
        current_time = time.time()
        if self._is_bringing_to_front and (current_time - self._last_front_time) < 0.5:
            print("UI: Ignoring rapid front operation")
            return
        
        # 状態を更新
        self._is_bringing_to_front = True
        self._last_front_time = current_time
        
        # 既存のタイマーを停止
        if self._bring_to_back_timer.isActive():
            self._bring_to_back_timer.stop()
            print("UI: Stopped existing bring-to-back timer")
        
        try:
            # Windowsの場合は特別処理
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes
                
                # 現在のウィンドウフラグを取得
                current_flags = self.windowFlags()
                
                # 一時的に最前面フラグを設定して確実に前面化
                temp_flags = current_flags | Qt.WindowStaysOnTopHint
                self.setWindowFlags(temp_flags)
                self.show()
                self.activateWindow()
                
                # 100ms後に元のフラグに戻す
                QTimer.singleShot(100, lambda: self._finalize_bring_to_front(current_flags))
                
                print("UI: Successfully brought window to front using Windows API")
            else:
                # Windows以外の場合は通常処理
                self.raise_()
                self.activateWindow()
                print("UI: Brought window to front (standard method)")
                
        except Exception as e:
            print(f"UI: Error bringing window to front: {e}")
        
        # 500ms後に状態をリセット
        QTimer.singleShot(500, lambda: setattr(self, '_is_bringing_to_front', False))
        
        # モード2（ホットキー時に最前面→一定秒で最背面）
        if bool(self.config_service.get("bring_to_front_on_hotkey", True)) and not bool(self.config_service.get("always_on_top", False)):
            delay_s = int(self.config_service.get("bring_to_back_delay_s", 3))
            delay_ms = max(0, delay_s) * 1000
            if delay_ms > 0:
                self._bring_to_back_timer.start(delay_ms)
                print(f"UI: Scheduled bring to back in {delay_s} seconds")

    def _finalize_bring_to_front(self, original_flags):
        """最前面表示の最終処理（タイマー遅延実行）"""
        try:
            # 常に最前面モードの場合はWindowStaysOnTopHintを維持
            if bool(self.config_service.get("always_on_top", False)):
                final_flags = original_flags | Qt.WindowStaysOnTopHint
            else:
                final_flags = original_flags
            
            # フラグを設定
            self.setWindowFlags(final_flags)
            self.show()
            self.activateWindow()
            print("UI: Finalized bring to front operation")
        except Exception as e:
            print(f"UI: Error finalizing bring to front: {e}")

    def _schedule_bring_to_back(self, delay_seconds):
        """指定時間後にウィンドウを最背面に移動するタイマーを設定"""
        delay_ms = max(0, delay_seconds) * 1000
        if delay_ms > 0:
            self._bring_to_back_timer.start(delay_ms)
            print(f"UI: Scheduled bring to back in {delay_seconds} seconds")

    def _send_to_back(self):
        """ウィンドウを最背面へ移動する（モード2用）"""
        try:
            if bool(self.config_service.get("always_on_top", False)):
                return
            if not bool(self.config_service.get("bring_to_front_on_hotkey", True)):
                return
            self.lower()
            print("UI: Sent window to back (after hotkey)")
        except Exception as e:
            print(f"UI: Failed to send window to back: {e}")
    
    def reload_hotkeys(self):
        """設定からホットキーを読み込んで再登録する"""
        try:
            hotkey_up = self.config_service.get("hotkey_move_up", "ctrl+shift+up")
            hotkey_down = self.config_service.get("hotkey_move_down", "ctrl+shift+down")
            hotkey_left = self.config_service.get("hotkey_move_left", "ctrl+shift+left")
            hotkey_right = self.config_service.get("hotkey_move_right", "ctrl+shift+right")
            
            # YouTube操作用のホットキー
            hotkey_preload = self.config_service.get("hotkey_preload", "ctrl+enter")
            hotkey_play = self.config_service.get("hotkey_play", "shift+enter")
            hotkey_search = self.config_service.get("hotkey_search", "ctrl+shift+enter")
            
            self.hotkey_service.register_hotkeys(hotkey_up, hotkey_down, hotkey_left, hotkey_right, 
                                               hotkey_preload, hotkey_play, hotkey_search)
            print(f"UI: Hotkeys reloaded - Up: {hotkey_up}, Down: {hotkey_down}, Left: {hotkey_left}, Right: {hotkey_right}, Preload: {hotkey_preload}, Play: {hotkey_play}, Search: {hotkey_search}")
        except Exception as e:
            print(f"UI: Error reloading hotkeys: {e}")
            # 再試行
            import time
            time.sleep(1)
            try:
                self.hotkey_service.register_hotkeys(hotkey_up, hotkey_down, hotkey_left, hotkey_right, 
                                                   hotkey_preload, hotkey_play, hotkey_search)
                print("UI: Hotkeys reloaded successfully after retry")
            except Exception as e2:
                print(f"UI: Failed to reload hotkeys after retry: {e2}")
    
    def preload_current_video(self):
        """現在選択中のYouTube動画をプリロード（Ctrl+Enter）"""
        # 設定に応じてウィンドウを最前面に表示
        if self.config_service.get("bring_to_front_on_hotkey", True):
            self._bring_to_front()
        
        current_index = self.left_pane.currentIndex()
        if not current_index.isValid():
            print("UI: No YouTube video selected for preload")
            return
        
        # 選択された動画データを取得
        video_data = current_index.data(Qt.DisplayRole)
        if not video_data:
            print("UI: No video data available for selected item")
            return
        
        video_id = video_data.get('video_id', '')
        title = video_data.get('title', '')
        
        if not video_id:
            print("UI: No video ID found for selected YouTube video")
            return
        
        print(f"UI: Preloading YouTube video via hotkey: {title} ({video_id})")

        # Ctrl+Enter 仕様:
        # - ready のサムネイルなら再生
        # - それ以外はプリロードして ready でキープ
        is_selected_ready = (self.youtube_video_state == 'ready' and self.preloaded_video_id == video_id)
        if is_selected_ready:
            print(f"UI: Video is ready, playing immediately (preload hotkey): {video_id}")
            if hasattr(self, 'player_server') and self.player_server:
                self.player_server.send_command('PLAY', video_id)
                self._update_youtube_video_state('playing', video_id)
                print(f"UI: Sent PLAY command for ready video (preload hotkey): {video_id}")
            else:
                print("UI: Player server not available for preload")
            return

        if hasattr(self, 'player_server') and self.player_server:
            self.preloaded_video_id = video_id
            self.pending_play_video_id = None
            self.player_server.send_command('PRELOAD', video_id)
            self._update_youtube_video_state('preloading', video_id)
            print(f"UI: Sent PRELOAD command via hotkey for video: {video_id}")
        else:
            print("UI: Player server not available for preload")
    
    def search_youtube_from_box(self):
        """検索ボックスからYouTube検索を実行"""
        search_text = self.youtube_search_box.text().strip()
        if not search_text:
            print("UI: Empty search text")
            return
        
        print(f"UI: Searching YouTube from search box: {search_text}")
        
        # 検索ボックスのフォーカスを外す
        self.youtube_search_box.clearFocus()
        
        # YouTube検索を実行
        self.search_youtube(search_text, "", "")
    
    def play_current_video(self):
        """現在選択中のYouTube動画を再生（Shift+Enter）"""
        # 設定に応じてウィンドウを最前面に表示
        if self.config_service.get("bring_to_front_on_hotkey", True):
            self._bring_to_front()
        
        current_index = self.left_pane.currentIndex()
        if not current_index.isValid():
            print("UI: No YouTube video selected for play")
            return
        
        # 選択された動画データを取得
        video_data = current_index.data(Qt.DisplayRole)
        if not video_data:
            print("UI: No video data available for selected item")
            return
        
        video_id = video_data.get('video_id', '')
        title = video_data.get('title', '')
        
        if not video_id:
            print("UI: No video ID found for selected YouTube video")
            return
        
        print(f"UI: Playing YouTube video via hotkey: {title} ({video_id})")
        
        # Shift+Enter 仕様:
        # - ready のサムネイルなら即再生
        # - それ以外はプリロード開始→ready 到達次第自動再生
        is_selected_ready = (self.youtube_video_state == 'ready' and self.preloaded_video_id == video_id)
        print(f"UI: Current state: {self.youtube_video_state}, preloaded_video_id: {self.preloaded_video_id}, selected video: {video_id}")
        
        if is_selected_ready:
            print(f"UI: Video is ready, playing immediately (play hotkey): {video_id}")
            if hasattr(self, 'player_server') and self.player_server:
                self.player_server.send_command('PLAY', video_id)
                self._update_youtube_video_state('playing', video_id)
                print(f"UI: Sent PLAY command for ready video (play hotkey): {video_id}")
            else:
                print("UI: Player server not available for play")
            return

        if hasattr(self, 'player_server') and self.player_server:
            self.preloaded_video_id = video_id
            self.pending_play_video_id = video_id
            self.player_server.send_command('PRELOAD', video_id)
            self._update_youtube_video_state('preloading', video_id)
            print(f"UI: Sent PRELOAD command, will auto-play when ready (play hotkey): {video_id}")
        else:
            print("UI: Player server not available for play")
    
    def on_table_double_click(self, index):
        """右テーブルがダブルクリックされた時の処理"""
        if not index.isValid():
            return
        
        # 選択された行のデータを取得
        row = index.row()
        if row >= self.table_model.rowCount():
            return
        
        # 履歴データを取得
        history_data = self.table_model._data
        if row >= len(history_data):
            return
        
        track_info = history_data[row]
        if len(track_info) >= 3:
            track_title = track_info[0] or ""
            artist = track_info[1] or ""
            comment = track_info[2] or ""
            
            print(f"UI: Double clicked on track: {track_title} by {artist}")
            
            # YouTube検索を実行
            self.search_youtube(track_title, artist, comment)
    
    def search_selected_track(self):
        """右ペインで選択中の楽曲でYouTube検索する（Ctrl+Shift+Enter）"""
        # 設定に応じてウィンドウを最前面に表示
        if self.config_service.get("bring_to_front_on_hotkey", True):
            self._bring_to_front()
        
        # 選択中の行を取得
        selection_model = self.right_table.selectionModel()
        selected_indexes = selection_model.selectedRows()
        
        if not selected_indexes:
            print("UI: No track selected for search")
            return
        
        # 最初の選択行を取得
        row = selected_indexes[0].row()
        if row >= self.table_model.rowCount():
            print("UI: Selected row is out of bounds")
            return
        
        # 履歴データを取得
        history_data = self.table_model._data
        if row >= len(history_data):
            print("UI: Selected row index exceeds data length")
            return
        
        track_info = history_data[row]
        if len(track_info) >= 3:
            track_title = track_info[0] or ""
            artist = track_info[1] or ""
            comment = track_info[2] or ""
            
            print(f"UI: Searching YouTube for selected track: {track_title} by {artist}")
            
            # YouTube検索を実行
            self.search_youtube(track_title, artist, comment)
        else:
            print("UI: Invalid track data for search")
    
    def search_youtube(self, track_title, artist, comment):
        """YouTubeで動画を検索"""
        from app.utils.logger import info, debug, error
        from app.services.youtube_service import YouTubeService
        
        # 既存のスレッドがあれば停止
        if self.youtube_search_thread and self.youtube_search_thread.isRunning():
            self.youtube_search_thread.terminate()
            self.youtube_search_thread.wait()
        
        youtube_service = YouTubeService()
        
        # APIキーが設定されているかチェック
        if not youtube_service.is_configured():
            error("YouTube API key not configured", "UI")
            return
        
        # 検索クエリを作成
        search_query = youtube_service.create_search_query_from_track(
            track_title, artist, comment
        )
        
        info(f"Searching YouTube for: {search_query}", "UI")
        
        # 検索中のUI状態を設定（検索ボックスのみ無効化）
        self._set_searching_state(True)
        
        try:
            # YouTube検索を実行
            self.youtube_search_thread = youtube_service.search_videos(
                search_query, 
                self.on_youtube_search_completed
            )
            
            # エラーシグナルも接続
            self.youtube_search_thread.search_error.connect(self.on_youtube_search_error)
            
            # スレッド終了時のクリーンアップも接続
            self.youtube_search_thread.finished.connect(self._on_search_finished)
            
            self.youtube_search_thread.start()
            
        except Exception as e:
            error(f"YouTube search error: {e}", "UI")
            # エラー時はダミー結果を表示
            self._set_searching_state(False)
            self._show_dummy_youtube_results()
    
    def _set_searching_state(self, is_searching):
        """検索中のUI状態を設定（検索ボックスのみ無効化）"""
        if is_searching:
            # 検索中は検索ボックスのみ無効化してインジケーター表示
            self.youtube_search_box.setEnabled(False)
            self.youtube_search_box.setPlaceholderText("検索中...")
            # カーソルを待機カーソルに変更（検索ボックスのみ）*
            from PySide6.QtGui import QCursor
            self.youtube_search_box.setCursor(QCursor(Qt.WaitCursor))
            print("UI: Search started - search box disabled")
        else:
            # 検索完了で検索ボックスを有効化
            self.youtube_search_box.setEnabled(True)
            self.youtube_search_box.setPlaceholderText("YouTube検索 (Enterで実行)")
            # カーソルを通常に戻す
            self.youtube_search_box.unsetCursor()
            print("UI: Search completed - search box enabled")
    
    def _on_search_finished(self):
        """検索スレッド終了時のクリーンアップ"""
        self._set_searching_state(False)
        self.youtube_search_thread = None

    def on_youtube_search_completed(self, videos):
        """YouTube検索完了時のコールバック"""
        from app.utils.logger import info, debug
        
        if not videos:
            info("No YouTube videos found", "UI")
            self.left_pane.clear_results()
            return
        
        # 設定に応じてウィンドウを最前面に表示（ホットキーと同じ実装）
        if self.config_service.get("bring_to_front_on_search", False):
            self._bring_to_front()
            info("Brought window to front after search completion", "UI")
        
        # 検索完了を通知
        self._on_search_finished()
        
        # 段階的表示：まず5件だけ即時表示
        initial_display_count = min(5, len(videos))
        initial_videos = videos[:initial_display_count]
        remaining_videos = videos[initial_display_count:]
        
        # 最初の5件を即時表示（サムネイルなし）
        processed_videos = []
        for video in initial_videos:
            processed_videos.append({
                'video_id': video.get('video_id', ''),
                'title': video.get('title', ''),
                'thumbnail': None,  # 後で非同期読み込み
                'duration': video.get('duration', ''),
                'url': video.get('url', '')
            })
        
        # 左ペインに即時表示
        self.left_pane.set_search_results(processed_videos)
        info(f"Found {len(videos)} YouTube videos (showing {initial_display_count} immediately)", "UI")
        
        # 最初の動画を選択状態にする（遅延実行で確実に設定）
        if processed_videos:
            QTimer.singleShot(200, self._select_first_video)  # 50msから200msに延長
        
        # 非同期でサムネイルを読み込む（最初の5件）
        self._load_thumbnails_async(initial_videos)
        
        # 残りの動画をバックグラウンドで追加
        if remaining_videos:
            self._schedule_remaining_videos(remaining_videos)
        
        # ホットキー設定が有効な場合、指定時間後に最背面に移動
        if self.config_service.get("bring_to_front_on_hotkey", True):
            delay_seconds = int(self.config_service.get("bring_to_back_delay_s", 3))
            self._schedule_bring_to_back(delay_seconds)
    
    def _schedule_remaining_videos(self, remaining_videos):
        """残りの動画をバックグラウンドで追加表示"""
        from PySide6.QtCore import QTimer
        
        # 500ms後に残りの動画を追加
        QTimer.singleShot(500, lambda: self._add_remaining_videos(remaining_videos))
    
    def _add_remaining_videos(self, remaining_videos):
        """残りの動画をリストに追加"""
        if not remaining_videos:
            return
        # 追加前の選択動画IDを保存しておく
        previous_selected_id = None
        try:
            sel = self.left_pane.get_selected_video()
            if sel:
                previous_selected_id = sel.get('video_id')
        except Exception:
            previous_selected_id = None

        # 現在のリストを取得
        current_videos = []
        for i in range(self.left_pane.model.rowCount()):
            video = self.left_pane.model.get_video_at(i)
            if video:
                current_videos.append(video)

        # 残りの動画を追加
        for video in remaining_videos:
            current_videos.append({
                'video_id': video.get('video_id', ''),
                'title': video.get('title', ''),
                'thumbnail': None,  # 後で非同期読み込み
                'duration': video.get('duration', ''),
                'url': video.get('url', '')
            })

        # リストを更新
        self.left_pane.model.set_videos(current_videos)
        print(f"UI: Added {len(remaining_videos)} remaining videos to list")

        # 更新後、リセットで選択が外れるため、以前の選択を復元する
        try:
            restored = False
            if previous_selected_id:
                for i in range(self.left_pane.model.rowCount()):
                    v = self.left_pane.model.get_video_at(i)
                    if v and v.get('video_id') == previous_selected_id:
                        idx = self.left_pane.model.index(i, 0)
                        if idx.isValid():
                            self.left_pane.setCurrentIndex(idx)
                            restored = True
                            print(f"UI: Restored selection to video {previous_selected_id} at index {i}")
                            break

            # 以前の選択がない／見つからない場合は先頭を選択しておく
            if not restored and self.left_pane.model.rowCount() > 0:
                first_index = self.left_pane.model.index(0, 0)
                if first_index.isValid():
                    self.left_pane.setCurrentIndex(first_index)
                    print("UI: Selected first video after adding remaining videos")
        except Exception as e:
            print(f"UI: Error restoring selection after adding videos: {e}")

        # 残りの動画のサムネイルも非同期読み込み
        self._load_thumbnails_async(remaining_videos)
    
    def _check_memory_usage(self):
        """メモリ使用量を監視し、必要に応じてクリーンアップ"""
        try:
            import psutil
            process = psutil.Process()
            memory_mb = process.memory_info().rss / 1024 / 1024
            
            # メモリ使用量が200MBを超えたら警告
            if memory_mb > 200:
                print(f"UI: Memory usage high: {memory_mb:.1f}MB - performing cleanup")
                self._perform_memory_cleanup()
            
            # 500MBを超えたら強制クリーンアップ
            if memory_mb > 500:
                print(f"UI: Critical memory usage: {memory_mb:.1f}MB - forcing cleanup")
                self._force_memory_cleanup()
                
        except ImportError:
            # psutilがインストールされていない場合は代替手段
            pass
        except Exception as e:
            print(f"UI: Error checking memory usage: {e}")
    
    def _perform_memory_cleanup(self):
        """メモリクリーンアップを実行"""
        try:
            # サムネイル読み込みスレッドを停止
            if hasattr(self, '_thumbnail_manager') and self._thumbnail_manager:
                self._thumbnail_manager.stop_all_loaders()
                self._thumbnail_manager = None
                print("UI: Stopped thumbnail loaders for memory cleanup")
            
            # 不要なオブジェクトを解放
            if hasattr(self, 'youtube_search_thread') and self.youtube_search_thread:
                if self.youtube_search_thread.isFinished():
                    self.youtube_search_thread = None
                    print("UI: Cleaned up finished search thread")
            
            # ガベージコレクションを促進
            import gc
            gc.collect()
            print("UI: Memory cleanup completed")
            
        except Exception as e:
            print(f"UI: Error during memory cleanup: {e}")
    
    def _force_memory_cleanup(self):
        """強制メモリクリーンアップを実行"""
        try:
            # すべてのスレッドを強制停止
            self._cleanup_thumbnail_loaders()
            
            # YouTube検索スレッドを強制停止
            if hasattr(self, 'youtube_search_thread') and self.youtube_search_thread:
                if self.youtube_search_thread.isRunning():
                    self.youtube_search_thread.terminate()
                    self.youtube_search_thread.wait()
                self.youtube_search_thread = None
                print("UI: Force stopped search thread")
            
            # UIコンポーネントのデータをクリア
            if hasattr(self, 'left_pane') and self.left_pane.model:
                self.left_pane.model.clear_videos()
                print("UI: Cleared YouTube list for memory cleanup")
            
            # ガベージコレクションを複数回実行
            import gc
            for _ in range(3):
                gc.collect()
            
            print("UI: Force memory cleanup completed")
            
        except Exception as e:
            print(f"UI: Error during force memory cleanup: {e}")

    def _select_first_video(self):
        """最初の動画を選択状態にする"""
        try:
            if hasattr(self, 'left_pane') and self.left_pane.model.rowCount() > 0:
                # 選択をクリアしてから最初のアイテムを選択
                self.left_pane.clearSelection()
                first_index = self.left_pane.model.index(0, 0)
                self.left_pane.setCurrentIndex(first_index)
                # フォーカスも設定
                self.left_pane.setFocus()
                print("UI: Selected first YouTube video after search")
            else:
                print("UI: No videos available for selection")
        except Exception as e:
            print(f"UI: Error selecting first video: {e}")
    
    def _load_thumbnails_async(self, videos):
        """サムネイルを非同期で読み込む"""
        from app.services.youtube_service import AsyncThumbnailManager
        
        # 既存のサムネイル読み込みを停止しない（複数の読み込みを許容）
        if not hasattr(self, '_thumbnail_manager') or not self._thumbnail_manager:
            self._thumbnail_manager = AsyncThumbnailManager()
            self._thumbnail_manager.thumbnail_ready.connect(self._on_thumbnail_ready)
        
        # 非同期読み込みを開始
        self._thumbnail_manager.load_thumbnails_async(videos)
    
    def _on_thumbnail_ready(self, video_id: str, thumbnail):
        """サムネイル読み込み完了時の処理"""
        # 左ペインのモデルを更新
        if hasattr(self, 'left_pane') and self.left_pane.model:
            self.left_pane.model.update_thumbnail(video_id, thumbnail)
            print(f"UI: Thumbnail loaded for video {video_id}")
    
    def _cleanup_thumbnail_loaders(self):
        """サムネイル読み込みスレッドをクリーンアップ"""
        if hasattr(self, '_thumbnail_manager') and self._thumbnail_manager:
            self._thumbnail_manager.stop_all_loaders()
            self._thumbnail_manager = None
    
    def on_youtube_search_error(self, error_message):
        """YouTube検索エラー時のコールバック"""
        print(f"UI: YouTube search error: {error_message}")
        # UI状態をリセット
        self._set_searching_state(False)
        # エラー時はダミー結果を表示
        self._show_dummy_youtube_results()
    
    def _show_dummy_youtube_results(self):
        """ダミーのYouTube検索結果を表示（テスト用）"""
        import random
        
        dummy_videos = []
        for i in range(5):
            dummy_videos.append({
                'video_id': f'dummy_{i}',
                'title': f'Test Video {i+1}',
                'thumbnail': None,  # 後でサムネイルを設定
                'duration': f'{random.randint(2,10)}:{random.randint(10,59):02d}',
                'url': f'https://youtube.com/watch?v=dummy_{i}'
            })
        
        self.left_pane.set_search_results(dummy_videos)
        print("UI: Displaying dummy YouTube results")
    
    def on_youtube_double_click(self, index):
        """YouTube動画のダブルクリック処理"""
        try:
            print(f"UI: YouTube double-click event received for index {index}")
            
            # 選択された動画情報を取得
            video_data = self.left_pane.model.get_video_at(index.row())
            if not video_data:
                print("UI: No video data found for selected index")
                return
            
            video_id = video_data.get('video_id', '')
            title = video_data.get('title', '')
            
            print(f"UI: Video data extracted - ID: {video_id}, Title: {title}")
            
            if not video_id:
                print("UI: No video ID found for selected YouTube video")
                return
            
            print(f"UI: YouTube video double-clicked: {title} ({video_id})")
            print(f"UI: Current last_clicked_video_id: {getattr(self, 'last_clicked_video_id', 'None')}")
            
            # 状態に応じてコマンドを送信
            if self.last_clicked_video_id == video_id:
                # 2回目のダブルクリック：再生
                print(f"UI: Second click detected - sending PLAY for {video_id}")
                if hasattr(self, 'player_server') and self.player_server:
                    self.player_server.send_command('PLAY', video_id)
                    self._update_youtube_video_state('playing', video_id)
                    print(f"UI: Sent PLAY command for video: {video_id}")
                else:
                    print("UI: Player server not available")
            else:
                # 1回目のダブルクリック：プリロード
                print(f"UI: First click detected - sending PRELOAD for {video_id}")
                self.last_clicked_video_id = video_id
                if hasattr(self, 'player_server') and self.player_server:
                    self.player_server.send_command('PRELOAD', video_id)
                    self._update_youtube_video_state('preloading', video_id)
                    print(f"UI: Sent PRELOAD command for video: {video_id}")
                else:
                    print("UI: Player server not available for preload")
            
            print(f"UI: YouTube double-click processing completed successfully")
            
        except Exception as e:
            print(f"UI: ERROR in on_youtube_double_click: {e}")
            import traceback
            print(f"UI: Traceback: {traceback.format_exc()}")
    
    def _handle_player_feedback(self, feedback_data):
        """プレイヤーからのフィードバックを処理"""
        try:
            state = feedback_data.get('state')
            video_id = feedback_data.get('videoId')  # videoId で取得
            timestamp = feedback_data.get('timestamp')
            
            print(f"UI: Player feedback received - state: {state}, video: {video_id}")
            
            # 状態に応じて枠の色を更新
            if state == 'ready':
                self._update_youtube_video_state('ready', video_id)

                # Shift+Enterで「ready到達次第PLAY」待ちの場合のみ自動再生
                print(f"UI: Checking auto-play - pending_play_video_id: {self.pending_play_video_id}, video_id: {video_id}")
                if self.pending_play_video_id == video_id:
                    print(f"UI: Auto-playing video after ready: {video_id}")
                    if hasattr(self, 'player_server') and self.player_server:
                        self.player_server.send_command('PLAY', video_id)
                        self._update_youtube_video_state('playing', video_id)
                        self.pending_play_video_id = None
                        print(f"UI: Sent PLAY command for auto-play: {video_id}")
                    else:
                        print("UI: Player server not available for auto-play")
                else:
                    print("UI: No auto-play - pending_play_video_id doesn't match or not set")
                        
            elif state == 'playing':
                self._update_youtube_video_state('playing', video_id)
                # 再生開始したらlast_clicked_video_idをリセット
                print(f"UI: Checking reset condition - last_clicked_video_id: {getattr(self, 'last_clicked_video_id', 'None')}, video_id: {video_id}")
                if hasattr(self, 'last_clicked_video_id') and self.last_clicked_video_id == video_id:
                    self.last_clicked_video_id = None
                    self.current_playing_video_id = video_id  # 再生中の動画IDを記録
                    print(f"UI: Reset last_clicked_video_id after playing: {video_id}")
                else:
                    # 再生開始した動画IDを記録
                    if self.current_playing_video_id != video_id:
                        self.current_playing_video_id = video_id
                        print(f"UI: Updated current_playing_video_id to: {video_id}")
                if self.pending_play_video_id == video_id:
                    self.pending_play_video_id = None
            elif state == 'preloading':
                self._update_youtube_video_state('preloading', video_id)
                    
        except Exception as e:
            print(f"UI: Error handling player feedback: {e}")
    
    def _update_youtube_video_state(self, state, video_id):
        """YouTube動画の状態を更新し、枠の色を変更"""
        self.youtube_video_state = state
        print(f"UI: YouTube video state updated to {state} for video: {video_id}")
        
        # 2本柱のID管理
        if state == 'playing':
            self.current_playing_video_id = video_id
            print(f"UI: Set current_playing_video_id to: {video_id}")
        elif state in ['preloading', 'ready']:
            self.preloaded_video_id = video_id
        
        # YouTubeリストの枠の色を更新
        self._update_youtube_border_color(state, video_id)

    def _update_youtube_border_color(self, state, video_id=None):
        """YouTubeリストの状態枠線を更新（preload/ready と playing を別IDで保持）"""
        try:
            if hasattr(self.left_pane, 'delegate') and self.left_pane.delegate:
                if not video_id:
                    if state == 'playing':
                        video_id = getattr(self, 'current_playing_video_id', None)
                    elif state in ['ready', 'preloading']:
                        video_id = getattr(self, 'preloaded_video_id', None)
                
                print(f"UI: Updating delegate - state: {state}, video_id: {video_id}")
                self.left_pane.delegate.set_video_state(state, video_id)
                print(f"UI: Selected thumbnail border color updated for state: {state}, video: {video_id}")
            else:
                print(f"UI: Delegate not available - left_pane.delegate: {getattr(self.left_pane, 'delegate', 'None')}")
        except Exception as e:
            print(f"UI: Error updating thumbnail border color: {e}")
            import traceback
            print(f"UI: Traceback: {traceback.format_exc()}")
    
    def _update_youtube_border_color_safe(self, border_color):
        """YouTubeリストの枠の色を安全に更新"""
        try:
            # より安全なスタイルシート更新方法
            import re
            current_style = self.left_pane.styleSheet()
            
            # 枠線の色のみを更新（既存のスタイルを維持）
            new_style = re.sub(r'border: 2px solid [^;]+;', f'border: 2px solid {border_color};', current_style)
            
            # 枠線が見つからない場合は追加
            if "border:" not in new_style:
                new_style = new_style.replace("QListView {", f"QListView {{\n                    border: 2px solid {border_color};")
            
            # 選択状態の枠線も更新
            new_style = re.sub(r'border: 4px solid [^;]+;', f'border: 4px solid {border_color};', new_style)
            
            self.left_pane.setStyleSheet(new_style)
            print(f"UI: YouTube border color updated to {border_color}")
            
        except Exception as e:
            print(f"UI: Error in safe border color update: {e}")
            # フォールバック：最小限りのスタイルシートを設定
            try:
                self.left_pane.setStyleSheet(f"""
                    QListView {{
                        background-color: #ffffff;
                        border: 2px solid {border_color};
                        border-radius: 4px;
                        outline: none;
                    }}
                    QListView::item {{
                        border: none;
                        padding: 0px;
                        margin: 0px;
                    }}
                    QListView::item:selected {{
                        border: 4px solid {border_color};
                        border-radius: 4px;
                        background-color: #f0f0f0;
                    }}
                """)
                print(f"UI: Fallback border color update successful")
            except Exception as e2:
                print(f"UI: Fallback also failed: {e2}")
    
    def _preload_video(self, video_id):
        """動画をプリロード"""
        if not video_id:
            return
        
        if hasattr(self, 'player_server') and self.player_server:
            self.player_server.send_command('PRELOAD', video_id)
            self._update_youtube_video_state('preloading', video_id)
            print(f"UI: Sent PRELOAD command for video: {video_id}")
        else:
            print("UI: Player server not available for preload")
    
    def closeEvent(self, event):
        """アプリケーション終了時のクリーンアップ"""
        try:
            print("UI: Cleaning up on application exit...")
            
            # メモリ監視タイマーを停止
            if hasattr(self, '_memory_check_timer'):
                self._memory_check_timer.stop()
            
            # 強制メモリクリーンアップを実行
            self._force_memory_cleanup()
            
            # サムネイル読み込みスレッドの停止
            self._cleanup_thumbnail_loaders()
            
            # ホットキーサービスの停止
            if hasattr(self, 'hotkey_service'):
                self.hotkey_service.stop()
                print("UI: Hotkey service stopped")
            
            # 履歴監視サービスの停止
            if hasattr(self, 'watcher'):
                self.watcher.stop()
                print("UI: History watcher stopped")
            
            # プレイヤーサーバーの停止
            if hasattr(self, 'player_server'):
                from app.services.player_http_server import stop_player_server
                stop_player_server()
                print("UI: Player server stopped")
            
            # 最終ガベージコレクション
            import gc
            gc.collect()
            
            event.accept()
            
        except Exception as e:
            print(f"UI: Error during cleanup: {e}")
            event.accept()  # エラーがあっても終了を許可する

    def _force_memory_cleanup(self):
        """強制メモリクリーンアップ"""
        import gc
        gc.collect()
        print("UI: Forced memory cleanup")

    def eventFilter(self, obj, event):
        """イベントフィルター - 検索ボックス以外のフォーカスで検索ボックスのフォーカスを外す"""
        if event.type() == QEvent.FocusIn:
            # 検索ボックス以外にフォーカスが移ったら検索ボックスのフォーカスを外す
            if obj != self.youtube_search_box and self.youtube_search_box.hasFocus():
                self.youtube_search_box.clearFocus()
                print("UI: Cleared search box focus due to focus change")
        
        return super().eventFilter(obj, event)

def main():
    try:
        print("UI: Starting application...")
        app = QApplication(sys.argv)
        
        # アプリケーション全体でホバー色をデフォルトに設定
        app.setStyleSheet("""
            QTableView::item:hover {
                background-color: palette(base);
            }
            QTableView::item:alternate:hover {
                background-color: palette(alternate-base);
            }
        """)
        
        # 日本語文字化け対策: フォントの設定
        from PySide6.QtGui import QFont
        font = QFont("Meiryo UI", 10)
        if not QFont("Meiryo UI").exactMatch():
            font = QFont("MS Gothic", 10)
            if not QFont("MS Gothic").exactMatch():
                font = QFont("sans-serif", 10)
        app.setFont(font)

        print("UI: Creating main window...")
        window = MainWindow()
        window.show()
        
        print("UI: Starting event loop...")
        sys.exit(app.exec())
        
    except Exception as e:
        print(f"UI: FATAL ERROR in main: {e}")
        import traceback
        print(f"UI: Traceback: {traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
