import sys
import webbrowser
from PySide6.QtCore import Qt, QTimer
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
        self.right_table = RightTableView()
        # テーブル周囲の枠線設定
        self.right_table.setStyleSheet("border: 1px solid #ddd; border-radius: 4px;")
        content_layout.addWidget(self.right_table, 2)
        
        # 右テーブルにもフォーカスを設定
        self.right_table.setFocusPolicy(Qt.StrongFocus)
        
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
        from app.services.config_service import ConfigService
        self.hotkey_service = HotkeyService()
        self.config_service = ConfigService()

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

        # 起動時にプレイヤー（player.html）を既定ブラウザで開く
        self._player_browser_opened = False
        self._open_player_in_browser()
        
        # YouTube動画の状態管理
        self.preloaded_video_id = None
        self.last_clicked_video_id = None
        self.current_playing_video_id = None  # 現在再生中の動画ID
        self.youtube_video_state = None  # 'preloading', 'ready', 'playing', None
        self.pending_play_video_id = None
        
        # 起動時の状態をリセット
        self._reset_youtube_state()

    def _open_player_in_browser(self):
        """起動時にYouTubeプレイヤーを既定ブラウザで開く"""
        try:
            if self._player_browser_opened:
                return

            # サーバー配下の player.html を開き、デフォルト再生動画IDをクエリで渡す
            # 例: http://127.0.0.1:8080/player.html?defaultVideoId=xxxx
            port = int(self.config_service.get("player_port", 8080))
            default_video_id = "eyUUHfVm8Ik"
            url = f"http://127.0.0.1:{port}/player.html?defaultVideoId={default_video_id}"
            webbrowser.open(url, new=1, autoraise=True)
            self._player_browser_opened = True
            print(f"UI: Opened player in browser: {url}")
        except Exception as e:
            print(f"UI: Failed to open player in browser: {e}")

    def _reset_youtube_state(self):
        """YouTube動画の状態をリセット"""
        self.youtube_video_state = None
        self.preloaded_video_id = None
        self.last_clicked_video_id = None
        self.current_playing_video_id = None
        self.pending_play_video_id = None
        self._update_youtube_border_color(None)  # デリゲートもリセット
        self._update_youtube_border_color_safe('#a52a2a')  # デフォルトの枠線色
        print("UI: YouTube state reset to default")
    
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
        
        # 常に最前面モードなら、最背面化の予約はしない
        if bool(self.config_service.get("always_on_top", False)):
            if self._bring_to_back_timer.isActive():
                self._bring_to_back_timer.stop()

        # Windowsの場合は特別な処理
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                
                # Windows APIでフォアグラウンドウィンドウを設定
                user32 = ctypes.windll.user32
                
                # 現在のフォアグラウンドウィンドウを取得
                current_fg = user32.GetForegroundWindow()
                
                # 自分のウィンドウハンドルを取得
                my_hwnd = int(self.winId())
                
                # フォアグラウンドに設定
                if user32.SetForegroundWindow(my_hwnd):
                    print("UI: Successfully brought window to front using Windows API")
                    return
                    
            except Exception as e:
                print(f"UI: Windows API failed, falling back to Qt method: {e}")
        
        # フォールバック：Qtの標準手法
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()
        
        # 最終手段：一時的に最前面フラグ
        current_flags = self.windowFlags()
        self.setWindowFlags(current_flags | Qt.WindowStaysOnTopHint)
        self.show()
        
        # すぐにフラグを解除
        self.setWindowFlags(current_flags)
        self.show()
        self.activateWindow()

        # モード2（ホットキー時に最前面→一定秒で最背面）
        if bool(self.config_service.get("bring_to_front_on_hotkey", True)) and not bool(self.config_service.get("always_on_top", False)):
            delay_s = int(self.config_service.get("bring_to_back_delay_s", 3))
            delay_ms = max(0, delay_s) * 1000
            if delay_ms > 0:
                self._bring_to_back_timer.start(delay_ms)

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
        from app.services.youtube_service import YouTubeService
        
        # 既存のスレッドがあれば停止
        if self.youtube_search_thread and self.youtube_search_thread.isRunning():
            self.youtube_search_thread.terminate()
            self.youtube_search_thread.wait()
        
        youtube_service = YouTubeService()
        
        # APIキーが設定されているかチェック
        if not youtube_service.is_configured():
            print("UI: YouTube API key not configured")
            return
        
        # 検索クエリを作成
        search_query = youtube_service.create_search_query_from_track(
            track_title, artist, comment
        )
        
        print(f"UI: Searching YouTube for: {search_query}")
        
        try:
            # YouTube検索を実行
            self.youtube_search_thread = youtube_service.search_videos(
                search_query, 
                self.on_youtube_search_completed
            )
            
            # エラーシグナルも接続
            self.youtube_search_thread.search_error.connect(self.on_youtube_search_error)
            
            self.youtube_search_thread.start()
            
        except Exception as e:
            print(f"UI: YouTube search error: {e}")
            # エラー時はダミー結果を表示
            self._show_dummy_youtube_results()
    
    def on_youtube_search_completed(self, videos):
        """YouTube検索完了時のコールバック"""
        if not videos:
            print("UI: No YouTube videos found")
            self.left_pane.clear_results()
            return
        
        # サムネイルを読み込む
        processed_videos = []
        for video in videos:
            thumbnail = None
            if 'thumbnail_url' in video:
                from app.services.youtube_service import YouTubeService
                youtube_service = YouTubeService()
                thumbnail = youtube_service.load_thumbnail(video['thumbnail_url'])
            
            processed_videos.append({
                'video_id': video.get('video_id', ''),
                'title': video.get('title', ''),
                'thumbnail': thumbnail,
                'duration': video.get('duration', ''),  # APIから取得したdurationを使用
                'url': video.get('url', '')
            })
        
        # 左ペインに結果を表示
        self.left_pane.set_search_results(processed_videos)
        print(f"UI: Found {len(processed_videos)} YouTube videos")
    
    def on_youtube_search_error(self, error_message):
        """YouTube検索エラー時のコールバック"""
        print(f"UI: YouTube search error: {error_message}")
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
