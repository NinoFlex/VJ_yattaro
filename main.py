import sys
import webbrowser
from PySide6.QtCore import Qt, QTimer, QEvent, QRectF, QSize
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
    QFrame, QPushButton, QLabel, QLineEdit, QAbstractButton
)
from PySide6.QtGui import QColor, QPainter, QPen


class ImeAwareLineEdit(QLineEdit):
    """IME入力（日本語変換など）に対応した検索ボックス。
    
    IMEで文字を変換中（組み立て中）にEnterキーを押しても
    検索が誤発動しないよう、returnPressedを自前で制御する。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._composing = False  # IME組み立て中フラグ

    def inputMethodEvent(self, event):
        """IMEイベントを受け取り、組み立て中かどうかを追跡する"""
        # preeditString が空でなければIMEで変換候補を選択中
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        """Enterキー押下時にIME組み立て中なら検索を発動しない"""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._composing:
                # IME確定のEnterは通常処理に流すだけ（returnPressedは出さない）
                super().keyPressEvent(event)
                return
        super().keyPressEvent(event)


from ui.widgets.right_table_view import RightTableView, RightTableModel


class SourceToggleSwitch(QAbstractButton):
    """Rekordbox / Shazam 用のカスタムトグルスイッチ。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(118, 30)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip("入力ソース: Rekordbox")

        self._track_off = QColor("#2e2e2e")
        self._track_off_border = QColor("#1f1f1f")
        self._track_on = QColor("#0d5ea8")
        self._track_on_border = QColor("#084a85")
        self._thumb = QColor("#f7f7f7")
        self._thumb_border = QColor("#d4d4d4")
        self._shadow = QColor(0, 0, 0, 45)
        self._text = QColor("#ffffff")

    def sizeHint(self):
        return QSize(118, 30)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect().adjusted(1, 1, -1, -1)
        radius = rect.height() / 2
        checked = self.isChecked()

        track_color = self._track_on if checked else self._track_off
        border_color = self._track_on_border if checked else self._track_off_border

        painter.setPen(QPen(border_color, 1))
        painter.setBrush(track_color)
        painter.drawRoundedRect(QRectF(rect), radius, radius)

        margin = 3
        thumb_size = rect.height() - margin * 2
        if checked:
            thumb_x = rect.right() - margin - thumb_size
            text_rect = rect.adjusted(10, 0, -thumb_size - 10, 0)
            label = "Shazam"
        else:
            thumb_x = rect.left() + margin
            text_rect = rect.adjusted(thumb_size + 10, 0, -10, 0)
            label = "Rekordbox"

        shadow_rect = QRectF(thumb_x, rect.top() + margin + 1, thumb_size, thumb_size)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._shadow)
        painter.drawEllipse(shadow_rect)

        thumb_rect = QRectF(thumb_x, rect.top() + margin, thumb_size, thumb_size)
        painter.setPen(QPen(self._thumb_border, 1))
        painter.setBrush(self._thumb)
        painter.drawEllipse(thumb_rect)

        painter.setPen(self._text)
        font = painter.font()
        font.setBold(True)
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(text_rect, Qt.AlignCenter, label)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.toggle()
            event.accept()
            return
        super().keyPressEvent(event)


class TitleBar(QWidget):
    """
    カスタムタイトルバー (ライトテーマ版)
    """
    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self.setFixedHeight(32)
        self._theme = "light"


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
        self.title_label.setObjectName("title_label")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.title_label)

        # Rekordbox / Shazam の入力ソース切替トグル
        self.source_toggle = SourceToggleSwitch(self)
        self.source_toggle.setObjectName("source_toggle")
        self.source_toggle.setChecked(False)
        self.source_toggle.toggled.connect(self._on_source_toggled)
        layout.addWidget(self.source_toggle)

        # 検索結果1位を自動再生するトグルボタン
        self.autoplay_button = QPushButton("自動再生 OFF")
        self.autoplay_button.setObjectName("autoplay_button")
        self.autoplay_button.setCheckable(True)
        self.autoplay_button.setChecked(False)
        self.autoplay_button.setToolTip("検索後に検索結果1位の動画を自動再生します")
        self.autoplay_button.toggled.connect(self._on_autoplay_toggled)
        layout.addWidget(self.autoplay_button)

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

    def apply_theme(self, theme):
        from ui.theme import colors, normalize_theme
        self._theme = normalize_theme(theme)
        c = colors(self._theme)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {c['titlebar']};
                color: {c['text']};
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                border-bottom: 1px solid {c['border_soft']};
            }}
            QPushButton {{
                background-color: transparent;
                border: none;
                color: {c['text']};
                font-size: 14px;
                padding: 0 12px;
            }}
            QPushButton:hover {{
                background-color: {c['hover']};
            }}
            #close_button:hover {{
                background-color: {c['danger']};
                color: white;
            }}
            #settings_button {{
                background-color: {c['input']};
                border: 1px solid {c['border']};
                border-radius: 4px;
                color: {c['text']};
            }}
            #settings_button {{
                font-size: 11px;
                margin: 4px 8px;
                padding: 0 10px;
            }}
            #settings_button:hover {{
                background-color: {c['hover']};
            }}
            #autoplay_button {{
                font-size: 11px;
                font-weight: bold;
                background-color: {c['input']};
                border: 1px solid {c['border']};
                border-radius: 4px;
                margin: 4px 4px;
                padding: 0 9px;
                color: {c['text']};
                min-width: 86px;
            }}
            #autoplay_button:hover {{
                background-color: {c['hover']};
            }}
            #autoplay_button:checked {{
                background-color: #1f6fb2;
                border-color: #15598f;
                color: white;
            }}
            #title_label {{
                font-weight: bold;
                color: {c['muted']};
                margin-left: 5px;
            }}
        """)

    def _on_source_toggled(self, checked):
        mode = "shazam" if checked else "rekordbox"
        self.source_toggle.setToolTip(f"入力ソース: {'Shazam' if checked else 'Rekordbox'}")
        self.source_toggle.update()
        self._main_window.set_source_mode(mode)

    def _on_autoplay_toggled(self, checked):
        self.autoplay_button.setText("自動再生 ON" if checked else "自動再生 OFF")
        self.autoplay_button.setToolTip(
            "検索後に検索結果1位の動画を自動再生します" if checked
            else "自動再生はオフです"
        )
        self._main_window.set_auto_play_enabled(checked)

    def set_auto_play_checked(self, checked):
        """設定読み込み時など、外部から自動再生ボタンの状態を同期する。"""
        checked = bool(checked)
        self.autoplay_button.blockSignals(True)
        self.autoplay_button.setChecked(checked)
        self.autoplay_button.setText("自動再生 ON" if checked else "自動再生 OFF")
        self.autoplay_button.setToolTip(
            "検索後に検索結果1位の動画を自動再生します" if checked
            else "自動再生はオフです"
        )
        self.autoplay_button.blockSignals(False)

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
        
        # ログを設定 (以前のリダイレクト状態を維持するように redirect=True を追加)
        configure_logging(log_level, enabled=True, redirect=True)
        print(f"UI: Logging configured at level {log_level_str} (Redirection active)")

    def __init__(self):
        super().__init__()
        self.setWindowTitle("VJ_yattaro")
        # Default height is sized to the title bar + compact player controls +
        # the 180 px main panes, avoiding unused vertical space.
        self.resize(1920, 285)
        self.source_mode = "rekordbox"

        # UI生成前にテーマ設定を読み込む。
        from app.services.config_service import ConfigService
        from ui.theme import normalize_theme
        self.config_service = ConfigService()
        self.ui_theme = normalize_theme(self.config_service.get("ui_theme", "dark"))

        # 前面化状態管理
        self._is_bringing_to_front = False
        self._last_front_time = 0
        self._last_user_interacted_time = 0  # ユーザーの直接操作があった時刻
        self._user_has_clicked_since_front = True  # 前面化後にクリックされたか (初期は前面扱いとする)
        
        # プレイヤー接続状態
        import time
        self._last_player_feedback_time = 0
        
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
            window_height = self.height()
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

        # Browser player A/B operation panels.
        from ui.widgets.player_control_panel import DualPlayerControlBar
        self.player_controls = DualPlayerControlBar(self)
        self.player_controls.playback_requested.connect(self.toggle_player_playback)
        self.player_controls.rewind_requested.connect(self.rewind_video)
        self.player_controls.forward_requested.connect(self.forward_video)
        self.player_controls.target_changed.connect(self._on_player_target_changed)
        self.root_layout.addWidget(self.player_controls)

        # Local progress clock. Browser timing is sampled only on state changes.
        self._player_panel_timer = QTimer(self)
        self._player_panel_timer.setInterval(200)
        self._player_panel_timer.timeout.connect(self.player_controls.tick)
        self._player_panel_timer.start()

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
        content_layout.addWidget(self.left_pane, 3, Qt.AlignTop)
        
        # YouTubeリストにフォーカスを設定
        self.left_pane.setFocusPolicy(Qt.StrongFocus)

        # 右ペイン
        right_container = QWidget()
        # Match the right search+table block to the fixed 180 px YouTube list.
        # Keeping both panes the same height prevents the history table from
        # stretching vertically when the main window has spare space.
        right_container.setFixedHeight(180)
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        
        # YouTube検索ボックス（IME対応版）
        self.youtube_search_box = ImeAwareLineEdit()
        self.youtube_search_box.setPlaceholderText("YouTube検索 (Enterで実行)")
        self.youtube_search_box.setFixedHeight(30)
        self.youtube_search_box.setStyleSheet("""
            QLineEdit {
                padding: 3px 6px;
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
        # 枠線などはウィジェット側のスタイル設定を活かしつつ、必要なら追加
        right_layout.addWidget(self.right_table)
        
        # 右テーブルにもフォーカスを設定
        self.right_table.setFocusPolicy(Qt.StrongFocus)
        
        content_layout.addWidget(right_container, 2, Qt.AlignTop)
        
        self.auto_play_top_result = bool(self.config_service.get("auto_play_top_result", False))
        self.title_bar.set_auto_play_checked(self.auto_play_top_result)
        self.apply_theme(self.ui_theme, persist=False)
        
        # ログレベルを設定
        self._configure_logging()
        
        # 履歴監視サービスの初期化
        from app.services.history_watcher import HistoryWatcher
        self.watcher = HistoryWatcher()
        
        # 初期データの取得とモデル設定
        initial_history = self.watcher.service.get_latest_history(limit=10)
        self.rekordbox_table_model = RightTableModel(
            initial_history,
            headers=["トラックタイトル", "アーティスト", "コメント"],
            max_rows=10,
        )

        from app.services.shazam_service import ShazamService
        self.shazam_service = ShazamService(self)
        self.shazam_table_model = RightTableModel(
            self.shazam_service.get_history(),
            headers=["認識時刻", "トラックタイトル", "アーティスト"],
            max_rows=50,
        )

        self.table_model = self.rekordbox_table_model
        self.right_table.setModel(self.table_model)
        
        # 信号の接続
        self.watcher.updated.connect(self.on_history_updated)
        self.watcher.new_track_detected.connect(self.on_new_track_detected)
        self.shazam_service.history_updated.connect(self.on_shazam_history_updated)
        self.shazam_service.new_track_detected.connect(self.on_shazam_new_track_detected)
        self.shazam_service.status_changed.connect(self.on_shazam_status_changed)
        self.shazam_service.error_occurred.connect(lambda message: print(f"UI: {message}"))
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
        self.hotkey_service.rewind_triggered.connect(self.rewind_video)
        self.hotkey_service.forward_triggered.connect(self.forward_video)
        
        # MIDIサービスの初期化
        from app.services.midi_service import MidiService
        self.midi_service = MidiService()
        self.reload_midi_config()
        
        # MIDIのシグナルを接続
        self.midi_service.move_up_triggered.connect(self.move_selection_up)
        self.midi_service.move_down_triggered.connect(self.move_selection_down)
        self.midi_service.move_left_triggered.connect(self.move_youtube_selection_left)
        self.midi_service.move_right_triggered.connect(self.move_youtube_selection_right)
        self.midi_service.preload_triggered.connect(self.preload_current_video)
        self.midi_service.play_triggered.connect(self.play_current_video)
        self.midi_service.search_triggered.connect(self.search_selected_track)
        self.midi_service.rewind_triggered.connect(self.rewind_video)
        self.midi_service.forward_triggered.connect(self.forward_video)

        # USB MIDI devices can briefly disappear/re-enumerate on Windows.
        # Keep this low-frequency so it has no meaningful UI cost.
        self._midi_watchdog = QTimer(self)
        self._midi_watchdog.setInterval(3000)
        self._midi_watchdog.timeout.connect(self.midi_service.ensure_connected)
        self._midi_watchdog.start()
        
        # 右テーブルのダブルクリックシグナルを接続
        self.right_table.doubleClicked.connect(self.on_table_double_click)
        
        # YouTubeリストのダブルクリックシグナルを接続
        self.left_pane.doubleClicked.connect(self.on_youtube_double_click)
        
        # YouTube検索スレッドの管理
        self.youtube_search_thread = None
        # 検索中に追加された「最新の保留検索」（1件のみ保持、古いものは上書き）
        self._pending_search_args = None
        # 保留検索の実行タイマー
        self._pending_search_timer = QTimer(self)
        self._pending_search_timer.setSingleShot(True)
        self._pending_search_timer.timeout.connect(self._execute_pending_search)
        
        # プレイヤーHTTPサーバーの初期化
        from app.services.player_http_server import start_player_server
        player_port = int(self.config_service.get("player_port", 8080))
        self._player_port = player_port
        self.player_server = start_player_server(port=player_port)
        
        # 状態フィードバック用の信号を接続（スレッドセーフ）
        from app.services.player_http_server import feedback_signals
        feedback_signals.feedback_received.connect(self._handle_player_feedback)
        print("UI: Player HTTP server started")

        # イベントフィルターをインストール（フォーカス管理やタイマー制御用）
        # すべての操作可能な領域に対してフィルターを設定し、クリックを確実に捕捉する
        self.installEventFilter(self)
        self.main_container.installEventFilter(self)
        self.left_pane.installEventFilter(self)
        self.left_pane.viewport().installEventFilter(self)
        self.right_table.installEventFilter(self)
        self.right_table.viewport().installEventFilter(self)
        
        # タイトルバーとその中の操作ボタン
        self.title_bar.installEventFilter(self)
        for btn in [self.title_bar.settings_button, self.title_bar.source_toggle,
                   self.title_bar.autoplay_button, self.title_bar.min_button, self.title_bar.close_button]:
            btn.installEventFilter(self)

        self.player_controls.installEventFilter(self)
        self.player_controls.panel_a.installEventFilter(self)
        self.player_controls.panel_b.installEventFilter(self)
        for control in self.player_controls.interactive_widgets():
            control.installEventFilter(self)
            
        # 検索ボックス
        self.youtube_search_box.installEventFilter(self)

        # 起動時にプレイヤー（player.html）を既定ブラウザで開く
        self._player_browser_opened = False
        self._open_player_in_browser()
        
        # YouTube動画の状態管理（初期化は_reset_youtube_stateで実施）
        self._reset_youtube_state()
        
        # 起動時にプレイヤー設定を送信（既にブラウザが開いている場合に備えて遅延送信）
        QTimer.singleShot(3000, self._send_player_config)
        QTimer.singleShot(3400, self._request_player_state)

    def _open_player_in_browser(self):
        """起動時にYouTubeプレイヤーを既定ブラウザで開く（既に開いている場合はスキップ）"""
        from app.utils.logger import info, error
        import time
        
        try:
            if self._player_browser_opened:
                return

            # フィードバックを待つために少し遅延させてチェック
            # もし既にブラウザが開いていれば、1秒以内にフィードバックが届くはず
            def check_and_open():
                # プレイヤーがサーバーにアクセス（ポーリング）しているか確認
                # サーバー起動から間もない場合、既にブラウザがあるなら数ミリ秒〜数百ミリ秒で最初のアクセスが来るはず
                # 最終アクセス（ポーリング）が3秒以内であれば「既に起動中」とみなす
                last_access = 0
                try:
                    from app.services.player_http_server import PlayerCommandHandler
                    import time
                    # 前回のポーリング時刻を取得（PlayerCommandHandlerに持たせるか、サーバー内部の状態を見る）
                    # ここではシンプルに前回フィードバック時刻も併用
                    last_access = getattr(PlayerCommandHandler, '_last_poll_time', 0)
                except:
                    pass
                
                current_time = time.time()
                time_since_last_poll = current_time - last_access
                time_since_feedback = current_time - self._last_player_feedback_time
                
                # 5秒以内にアクセスまたはフィードバックがあれば起動中とみなす
                if time_since_last_poll < 5.0 or time_since_feedback < 5.0:
                    info(f"Player already running (last poll {time_since_last_poll:.1f}s ago, feedback {time_since_feedback:.1f}s ago). Skipping browser open.", "UI")
                    self._player_browser_opened = True
                    return

                # サーバー配下の player.html を開き、デフォルト再生動画IDをクエリで渡す
                port = int(self.config_service.get("player_port", 8080))
                default_video_id = "eyUUHfVm8Ik"
                track_info_pos = self.config_service.get("player_track_info_position", "top-right")
                url = f"http://localhost:{port}/player.html?defaultVideoId={default_video_id}&trackInfoPosition={track_info_pos}"
                webbrowser.open(url, new=1, autoraise=True)
                self._player_browser_opened = True
                info(f"Opened player in browser: {url}", "UI")

            # 1.5秒待ってから判定（ポーリング周期を考慮）
            QTimer.singleShot(1500, check_and_open)
            
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
        # 自動検索→自動再生時だけ、playing到達後に指定秒数早送りする対象。
        self.pending_auto_seek_video_id = None
        # 楽曲情報の保持（右カラムから検索した場合のみ設定される）
        self._current_track_info = {"title": "", "artist": "", "comment": ""}
        self._update_youtube_border_color_safe(None)  # テーマ標準の枠線色へ戻す
        info("YouTube state reset to default", "UI")

    def _on_player_target_changed(self, player_id):
        player_id = str(player_id or "A").upper()
        if player_id not in ("A", "B"):
            return

        # The center toggle now controls both the operation target and which
        # physical browser player is shown on the output.  Update the panel
        # highlight immediately; browser feedback will confirm the final state.
        if hasattr(self, "player_controls"):
            for panel_id in ("A", "B"):
                self.player_controls.panel(panel_id).set_active_output(
                    panel_id == player_id
                )

        if hasattr(self, "player_server") and self.player_server:
            self.player_server.send_command("SELECT_PLAYER", player_id=player_id)
            print(f"UI: Player operation/display target -> {player_id}")
        else:
            print(f"UI: Player operation target -> {player_id} (server not ready)")

    def _selected_player_id(self):
        if hasattr(self, "player_controls"):
            return self.player_controls.selected_player()
        return "A"

    def _find_video_data(self, video_id):
        if not video_id or not hasattr(self, "left_pane"):
            return None
        try:
            model = self.left_pane.model
            for row in range(model.rowCount()):
                video = model.get_video_at(row)
                if video and video.get("video_id") == video_id:
                    return video
        except Exception as e:
            print(f"UI: Failed to find video metadata for {video_id}: {e}")
        return None

    def _build_player_media_info(self, video_data=None, video_id=""):
        video_data = video_data or self._find_video_data(video_id) or {}
        resolved_video_id = str(video_data.get("video_id", "") or video_id or "")
        thumbnail_url = str(video_data.get("thumbnail_url", "") or "")
        if not thumbnail_url and resolved_video_id:
            thumbnail_url = f"https://i.ytimg.com/vi/{resolved_video_id}/hqdefault.jpg"
        return {
            "videoTitle": str(video_data.get("title", "") or ""),
            "thumbnailUrl": thumbnail_url,
            "durationText": str(video_data.get("duration", "") or ""),
        }

    def _send_video_command(self, command, video_id, video_data=None):
        if not hasattr(self, "player_server") or not self.player_server:
            return False
        self.player_server.send_command(
            command,
            video_id,
            track_info=dict(self._current_track_info),
            media_info=self._build_player_media_info(video_data, video_id),
        )
        return True

    def _request_player_state(self):
        if hasattr(self, "player_server") and self.player_server:
            self.player_server.send_command("REQUEST_PLAYER_STATE")
            print("UI: Requested one-shot A/B player state snapshots")

    def toggle_player_playback(self, player_id):
        player_id = str(player_id or "A").upper()
        if player_id not in ("A", "B") or not hasattr(self, "player_controls"):
            return
        panel = self.player_controls.panel(player_id)
        if not panel.video_id:
            print(f"UI: Player {player_id} has no loaded video")
            return
        if not hasattr(self, "player_server") or not self.player_server:
            print("UI: Player server not available for play/pause")
            return

        selected_before_click = self.player_controls.selected_player()
        if selected_before_click != player_id:
            # Clicking the play button on the non-selected side means
            # "make this side current and play it".  Changing the toggle emits
            # SELECT_PLAYER first, then RESUME_PLAYER is queued immediately after.
            self.player_controls.set_selected_player(player_id)
            command = "RESUME_PLAYER"
            panel.set_state("playing", is_current=True)
        elif panel.state in ("playing", "buffering"):
            command = "PAUSE_PLAYER"
            panel.set_state("paused")
        else:
            command = "RESUME_PLAYER"
            panel.set_state("playing")

        self.player_server.send_command(command, player_id=player_id)
        print(f"UI: Sent {command} to player {player_id}")

    def _update_player_control_panel(self, feedback_data):
        if not hasattr(self, "player_controls"):
            return
        state = str(feedback_data.get("state", "") or "")
        if state.upper() == "HEARTBEAT":
            return
        player_id = str(feedback_data.get("playerId", "") or "").upper()
        if player_id not in ("A", "B"):
            return

        is_current = feedback_data.get("isCurrent")
        if is_current is True:
            # Keep the center toggle aligned with the browser's actual visible
            # player after normal PRELOAD/PLAY crossfades too.  This update is
            # silent so it does not echo another SELECT_PLAYER command.
            self.player_controls.set_selected_player(player_id, notify=False)
            for panel_id in ("A", "B"):
                self.player_controls.panel(panel_id).set_active_output(panel_id == player_id)

        self.player_controls.panel(player_id).set_state(
            state=state,
            current_time=feedback_data.get("currentTime"),
            duration=feedback_data.get("duration"),
            video_id=feedback_data.get("videoId"),
            track_info=feedback_data.get("trackInfo"),
            media_info=feedback_data.get("mediaInfo"),
            is_current=is_current,
        )
    
    def open_settings(self):
        """詳細設定画面を別ウィンドウとして開く"""
        from ui.dialogs.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self)
        if dialog.exec():
            print("UI: Settings dialog accepted. Reflecting changes...")
            
            # ログ設定を反映
            from app.utils.logger import configure_logging
            enable_logging = self.config_service.get("enable_logging", True)
            configure_logging(enabled=enable_logging, redirect=True)

            self.apply_theme(self.config_service.get("ui_theme", "dark"), persist=False)
            self.watcher.reload_settings()
            if hasattr(self, "shazam_service"):
                self.shazam_service.reload_settings()
            self.reload_hotkeys()  # ホットキーを再登録
            self.reload_midi_config() # MIDI設定を再登録
            self.apply_window_placement_mode()  # ウィンドウ配置モードを反映
            self._restart_player_server_if_needed()  # プレイヤーサーバー設定を反映
            self._send_player_config()  # プレイヤー設定を送信
            QTimer.singleShot(300, self._request_player_state)
        else:
            print("UI: Settings dialog cancelled.")

    def apply_theme(self, theme, persist=False):
        """アプリ全体とカスタム描画ウィジェットへテーマを反映する。"""
        from PySide6.QtWidgets import QApplication
        from ui.theme import apply_application_theme, colors, normalize_theme

        self.ui_theme = normalize_theme(theme)
        c = colors(self.ui_theme)
        app = QApplication.instance()
        if app is not None:
            apply_application_theme(app, self.ui_theme)

        if hasattr(self, "main_container"):
            self.main_container.setStyleSheet(f"""
                QFrame#main_container {{
                    background-color: {c['panel']};
                    border: 1px solid {c['border']};
                    border-radius: 8px;
                }}
            """)

        if hasattr(self, "title_bar"):
            self.title_bar.apply_theme(self.ui_theme)
        if hasattr(self, "player_controls"):
            self.player_controls.apply_theme(self.ui_theme)

        if hasattr(self, "youtube_search_box"):
            self.youtube_search_box.setStyleSheet(f"""
                QLineEdit {{
                    padding: 8px;
                    border: 1px solid {c['border_soft']};
                    border-radius: 4px;
                    font-size: 12px;
                    background-color: {c['input']};
                    color: {c['text']};
                    selection-background-color: {c['selection']};
                    selection-color: {c['selection_text']};
                }}
                QLineEdit:focus {{
                    border: 2px solid {c['accent']};
                }}
            """)

        if hasattr(self, "left_pane") and hasattr(self.left_pane, "apply_theme"):
            self.left_pane.apply_theme(self.ui_theme)
        if hasattr(self, "right_table") and hasattr(self.right_table, "apply_theme"):
            self.right_table.apply_theme(self.ui_theme)

        if persist and hasattr(self, "config_service"):
            self.config_service.save_config({"ui_theme": self.ui_theme})
        print(f"UI: Theme -> {self.ui_theme}")

    def set_auto_play_enabled(self, enabled):
        """検索結果1位の自動再生を切り替え、設定を保存する。"""
        self.auto_play_top_result = bool(enabled)
        if not self.auto_play_top_result:
            # 自動再生をOFFにした時点で、未実行の自動早送りも破棄する。
            self.pending_auto_seek_video_id = None
        if hasattr(self, "config_service"):
            self.config_service.save_config({"auto_play_top_result": self.auto_play_top_result})
        print(f"UI: Auto-play top search result -> {'ON' if self.auto_play_top_result else 'OFF'}")

    def set_source_mode(self, mode):
        """タイトルバーのトグルに合わせて入力ソースと右カラムを切り替える。"""
        if mode not in ("rekordbox", "shazam"):
            return

        self.source_mode = mode

        # タイトルバー側の状態も同期（外部から呼ばれた場合を含む）。
        if hasattr(self, "title_bar") and hasattr(self.title_bar, "source_toggle"):
            toggle = self.title_bar.source_toggle
            should_check = mode == "shazam"
            if toggle.isChecked() != should_check:
                toggle.blockSignals(True)
                toggle.setChecked(should_check)
                toggle.blockSignals(False)
            toggle.update()

        # 初期化途中にトグルが操作された場合は、サービス生成後に通常動作へ入る。
        if not hasattr(self, "right_table") or not hasattr(self, "watcher"):
            return

        if mode == "shazam":
            self.watcher.stop()
            if hasattr(self, "shazam_table_model"):
                self.table_model = self.shazam_table_model
                self.right_table.setModel(self.table_model)
            self.youtube_search_box.setPlaceholderText("YouTube検索 / Shazam履歴から選択")
            if hasattr(self, "shazam_service"):
                self.shazam_service.start()
            print("UI: Source mode -> Shazam")
        else:
            if hasattr(self, "shazam_service"):
                self.shazam_service.stop()
            if hasattr(self, "rekordbox_table_model"):
                self.table_model = self.rekordbox_table_model
                self.right_table.setModel(self.table_model)
            self.youtube_search_box.setPlaceholderText("YouTube検索 (Enterで実行)")
            self.watcher.start()
            print("UI: Source mode -> Rekordbox")

    def _get_track_search_fields(self, row):
        if row < 0 or row >= self.table_model.rowCount():
            return None
        if row >= len(self.table_model._data):
            return None

        track_info = self.table_model._data[row]
        if self.source_mode == "shazam":
            # Shazam model: (timestamp, title, artist). Timestamp is display/log metadata only.
            if len(track_info) < 3:
                return None
            return track_info[1] or "", track_info[2] or "", ""

        # Rekordbox model: (title, artist, comment)
        if len(track_info) < 3:
            return None
        return track_info[0] or "", track_info[1] or "", track_info[2] or ""

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
        """Watcherから新しいRekordbox履歴データを受け取った時の処理"""
        # beginResetModel()/endResetModel() はQtの選択状態を無効化するため、
        # リセットが必要な場合に備えて「更新前」に現在選択を保存する。
        selected_row = -1
        selected_track = None
        if self.source_mode == "rekordbox":
            selection_model = self.right_table.selectionModel()
            if selection_model:
                current_indexes = selection_model.selectedRows()
                if current_indexes:
                    selected_row = current_indexes[0].row()
                    if 0 <= selected_row < len(self.rekordbox_table_model._data):
                        selected_track = self.rekordbox_table_model._data[selected_row]

        model_changed = self.rekordbox_table_model.update_data(new_history)

        # Shazam表示中はRekordboxの更新でUIや自動検索を動かさない。
        if self.source_mode != "rekordbox":
            return

        # 内容が同一ならモデル自体をリセットしていないので、Qtの選択は
        # そのまま残る。実データが変わった時だけ選択を復元する。
        if model_changed and selected_row >= 0:
            restore_row = -1
            if selected_track is not None:
                # 行の挿入などで位置がずれても、同じ履歴項目を優先して探す。
                for row, track in enumerate(self.rekordbox_table_model._data):
                    if track == selected_track:
                        restore_row = row
                        break

            # 選択していた履歴が消えた場合は、可能なら同じ行番号を維持。
            if restore_row < 0 and self.rekordbox_table_model.rowCount() > 0:
                restore_row = min(selected_row, self.rekordbox_table_model.rowCount() - 1)

            if restore_row >= 0:
                self.right_table.selectRow(restore_row)
                print(
                    f"UI: Restored right-table selection after history refresh: "
                    f"row {selected_row} -> {restore_row}"
                )
        
        # 元々の表から更新されていた場合、一番上の項目で自動で検索を実行
        if len(new_history) > 0:
            new_top_track = new_history[0]
            if hasattr(self, '_last_top_track') and self._last_top_track != new_top_track:
                # 一番上の項目が変更された場合
                print(f"UI: Top track updated from {self._last_top_track} to {new_top_track}")
                self._last_top_track = new_top_track
                # 右カラムが更新されたら検索ボックスをクリア
                if hasattr(self, 'youtube_search_box') and self.youtube_search_box.text().strip():
                    self.youtube_search_box.clear()
                    print("UI: Cleared YouTube search box due to right column update")        
                # 一番上の項目で自動検索
                if len(new_top_track) >= 3:
                    track_title = new_top_track[0] or ""
                    artist = new_top_track[1] or ""
                    comment = new_top_track[2] or ""
                    
                    print(f"UI: Auto-searching YouTube for updated top track: {track_title} by {artist}")
                    self.search_youtube(track_title, artist, comment, from_list=True)
            elif not hasattr(self, '_last_top_track'):
                # 初回設定
                self._last_top_track = new_top_track
                print(f"UI: Initial top track set: {self._last_top_track}")

    def on_new_track_detected(self, track):
        """Rekordboxで新しい曲が検出された時の処理"""
        if self.source_mode != "rekordbox":
            return
        self.right_table.selectRow(0)
        print("UI: New Rekordbox track detected! Auto-selected row 0.")

    def on_shazam_history_updated(self, new_history):
        """Shazam履歴を最大50件で右カラムへ反映する。"""
        self.shazam_table_model.update_data(new_history)
        if self.source_mode == "shazam" and self.shazam_table_model.rowCount() > 0:
            self.right_table.selectRow(0)

    def on_shazam_new_track_detected(self, entry):
        """Shazamで曲が変わった時、既存のYouTube検索フローへ渡す。"""
        if self.source_mode != "shazam" or len(entry) < 3:
            return

        timestamp, track_title, artist = entry[0], entry[1], entry[2]
        self.right_table.selectRow(0)
        if self.youtube_search_box.text().strip():
            self.youtube_search_box.clear()
        print(f"UI: Shazam detected {artist} - {track_title} at {timestamp}")
        self.search_youtube(track_title or "", artist or "", "", from_list=True)

    def on_shazam_status_changed(self, status):
        if hasattr(self, "title_bar") and hasattr(self.title_bar, "source_toggle"):
            self.title_bar.source_toggle.setToolTip(status)
    
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
        
        # 重複呼び出しを防止（1000ms以内は無視。OSのフラグ反映待ちを考慮）
        current_time = time.time()
        if self._is_bringing_to_front and (current_time - self._last_front_time) < 1.0:
            print("UI: Ignoring rapid front operation")
            return
        
        # 状態を更新
        self._is_bringing_to_front = True
        self._last_front_time = current_time
        
        # もともと背面にある場合のみ、クリック済みフラグをリセットして自動帰還の対象にする
        # すでに前面（アクティブ）にある場合は、現在のクリック状態を維持する
        if not self.isActiveWindow():
            self._user_has_clicked_since_front = False
            print("UI: Window was background, reset _user_has_clicked_since_front to False")
        else:
            print(f"UI: Window already active, current _user_has_clicked_since_front is {self._user_has_clicked_since_front}")
        
        # 既存のタイマーを停止
        if self._bring_to_back_timer.isActive():
            self._bring_to_back_timer.stop()
            print("UI: Stopped existing bring-to-back timer")
        
        try:
            # Windowsの場合は特別処理
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes
                
                # 現在のウィンドウフラグから最前面フラグを除外したものを「元の状態」として保存
                # こうすることで、万が一フラグが残っている状態で再度呼ばれても、復元時に確実に解除される
                current_flags = self.windowFlags() & ~Qt.WindowStaysOnTopHint
                
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
        
        # 1000ms後に状態をリセット
        QTimer.singleShot(1000, lambda: setattr(self, '_is_bringing_to_front', False))
        
        # モード2（ホットキー時に最前面→一定秒で最背面）
        # ただし、既にユーザーが操作中（クリック済み）の場合は背面送りにしない
        if bool(self.config_service.get("bring_to_front_on_hotkey", True)) and not bool(self.config_service.get("always_on_top", False)):
            if not self._user_has_clicked_since_front:
                delay_s = int(self.config_service.get("bring_to_back_delay_s", 3))
                delay_ms = max(0, delay_s) * 1000
                if delay_ms > 0:
                    self._bring_to_back_timer.start(delay_ms)
                    print(f"UI: Scheduled/Restarted bring to back in {delay_s} seconds")
            else:
                print("UI: User has interacted with the window, skipping bring-to-back timer")

    def _finalize_bring_to_front(self, original_flags):
        """最前面表示の最終処理（タイマー遅延実行）"""
        try:
            # 常に最前面モードの場合はWindowStaysOnTopHintを維持
            if bool(self.config_service.get("always_on_top", False)):
                final_flags = original_flags | Qt.WindowStaysOnTopHint
            else:
                # 常に最前面モードでない場合は、ここで確実にフラグを落とす
                final_flags = original_flags & ~Qt.WindowStaysOnTopHint
            
            # フラグを設定
            self.setWindowFlags(final_flags)
            self.show()
            self.activateWindow()
            print("UI: Finalized bring to front operation")
        except Exception as e:
            print(f"UI: Error finalizing bring to front: {e}")

    def _schedule_bring_to_back(self, delay_seconds):
        """指定時間後にウィンドウを最背面に移動するタイマーを設定"""
        if self.isActiveWindow() or getattr(self, '_user_has_clicked_since_front', False):
            print("UI: skipping _schedule_bring_to_back because window is active or recently clicked")
            return
            
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
            
            # ホットキー前面化後にユーザー操作（マウスクリック等）があった場合は背面移動をキャンセル
            if getattr(self, '_user_has_clicked_since_front', False):
                print("UI: Skipping bring-to-back because user interacted with the window since it was brought to front")
                return

            import time
            if (time.time() - getattr(self, '_last_user_interacted_time', 0)) < 0.5:
                print("UI: Skipping bring-to-back because user recently interacted with the window")
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
            hotkey_rewind = self.config_service.get("hotkey_rewind", "ctrl+;")
            hotkey_forward = self.config_service.get("hotkey_forward", "ctrl+:")
            
            self.hotkey_service.register_hotkeys(hotkey_up, hotkey_down, hotkey_left, hotkey_right, 
                                               hotkey_preload, hotkey_play, hotkey_search,
                                               hotkey_rewind, hotkey_forward)
            print(f"UI: Hotkeys reloaded - Up: {hotkey_up}, Down: {hotkey_down}, Left: {hotkey_left}, Right: {hotkey_right}, Preload: {hotkey_preload}, Play: {hotkey_play}, Search: {hotkey_search}, Rewind: {hotkey_rewind}, Forward: {hotkey_forward}")
        except Exception as e:
            print(f"UI: Error reloading hotkeys: {e}")
            # 再試行
            import time
            time.sleep(1)
            try:
                self.hotkey_service.register_hotkeys(hotkey_up, hotkey_down, hotkey_left, hotkey_right, 
                                                   hotkey_preload, hotkey_play, hotkey_search,
                                                   hotkey_rewind, hotkey_forward)
                print("UI: Hotkeys reloaded successfully after retry")
            except Exception as e2:
                print(f"UI: Failed to reload hotkeys after retry: {e2}")

    def reload_midi_config(self):
        """設定からMIDIコンフィグを読み込んで再適用する"""
        try:
            device_name = self.config_service.get("midi_port_name", "")
            mappings = {}
            
            def add_map(action, key):
                val = int(self.config_service.get(key, -1))
                if val >= 0:
                    mappings[val] = action
                    
            add_map("move_up", "midi_move_up")
            add_map("move_down", "midi_move_down")
            add_map("move_left", "midi_move_left")
            add_map("move_right", "midi_move_right")
            add_map("preload", "midi_preload")
            add_map("play", "midi_play")
            add_map("search", "midi_search")
            add_map("rewind", "midi_rewind")
            add_map("forward", "midi_forward")
            
            self.midi_service.set_config(device_name, mappings)
            print(f"UI: MIDI config reloaded - Device: {device_name}")
        except Exception as e:
            print(f"UI: Error reloading MIDI config: {e}")
    
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

        # 手動プリロードでは、自動検索由来の早送り待ちを解除する。
        self.pending_auto_seek_video_id = None
        
        print(f"UI: Preloading YouTube video via hotkey: {title} ({video_id})")

        # Ctrl+Enter 仕様:
        # - ready のサムネイルなら再生
        # - それ以外はプリロードして ready でキープ
        is_selected_ready = (self.youtube_video_state == 'ready' and self.preloaded_video_id == video_id)
        if is_selected_ready:
            print(f"UI: Video is ready, playing immediately (preload hotkey): {video_id}")
            if hasattr(self, 'player_server') and self.player_server:
                self._send_video_command('PLAY', video_id, video_data)
                self._update_youtube_video_state('playing', video_id)
                print(f"UI: Sent PLAY command for ready video (preload hotkey): {video_id}")
            else:
                print("UI: Player server not available for preload")
            return

        if hasattr(self, 'player_server') and self.player_server:
            self.preloaded_video_id = video_id
            self.pending_play_video_id = None
            self._send_video_command('PRELOAD', video_id, video_data)
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
        
        # 検索ボックスからの検索では楽曲情報をクリア
        self._current_track_info = {"title": "", "artist": "", "comment": ""}
        
        # YouTube検索を実行（from_list=Falseなので楽曲情報は保持しない）
        self.search_youtube(search_text, "", "", allow_auto_play=False)
    
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

        # 手動再生では、自動検索由来の早送り待ちを解除する。
        self.pending_auto_seek_video_id = None
        
        print(f"UI: Playing YouTube video via hotkey: {title} ({video_id})")
        
        # Shift+Enter 仕様:
        # - ready のサムネイルなら即再生
        # - それ以外はプリロード開始→ready 到達次第自動再生
        is_selected_ready = (self.youtube_video_state == 'ready' and self.preloaded_video_id == video_id)
        print(f"UI: Current state: {self.youtube_video_state}, preloaded_video_id: {self.preloaded_video_id}, selected video: {video_id}")
        
        if is_selected_ready:
            print(f"UI: Video is ready, playing immediately (play hotkey): {video_id}")
            if hasattr(self, 'player_server') and self.player_server:
                self._send_video_command('PLAY', video_id, video_data)
                self._update_youtube_video_state('playing', video_id)
                print(f"UI: Sent PLAY command for ready video (play hotkey): {video_id}")
            else:
                print("UI: Player server not available for play")
            return

        if hasattr(self, 'player_server') and self.player_server:
            self.preloaded_video_id = video_id
            self.pending_play_video_id = video_id
            self._send_video_command('PRELOAD', video_id, video_data)
            self._update_youtube_video_state('preloading', video_id)
            print(f"UI: Sent PRELOAD command, will auto-play when ready (play hotkey): {video_id}")
        else:
            print("UI: Player server not available for play")
    
    def rewind_video(self):
        """トグルで選択されたブラウザプレイヤーだけを巻き戻す。"""
        try:
            rewind_seconds = max(0, int(self.config_service.get("rewind_seconds", 2)))
        except (TypeError, ValueError):
            rewind_seconds = 2
        player_id = self._selected_player_id()
        if hasattr(self, 'player_server') and self.player_server:
            self.player_server.send_command(
                'REWIND', str(rewind_seconds), player_id=player_id
            )
            if hasattr(self, "player_controls"):
                self.player_controls.panel(player_id).adjust_position(-rewind_seconds)
            print(
                f"UI: Sent REWIND command ({rewind_seconds} seconds) "
                f"to player {player_id}"
            )
        else:
            print("UI: Player server not available for rewind")

    def forward_video(self):
        """トグルで選択されたブラウザプレイヤーだけを早送りする。"""
        try:
            forward_seconds = max(0, int(self.config_service.get("forward_seconds", 2)))
        except (TypeError, ValueError):
            forward_seconds = 2
        player_id = self._selected_player_id()
        if hasattr(self, 'player_server') and self.player_server:
            self.player_server.send_command(
                'FORWARD', str(forward_seconds), player_id=player_id
            )
            if hasattr(self, "player_controls"):
                self.player_controls.panel(player_id).adjust_position(forward_seconds)
            print(
                f"UI: Sent FORWARD command ({forward_seconds} seconds) "
                f"to player {player_id}"
            )
        else:
            print("UI: Player server not available for forward")

    def on_table_double_click(self, index):
        """右テーブルがダブルクリックされた時の処理"""
        if not index.isValid():
            return

        fields = self._get_track_search_fields(index.row())
        if not fields:
            return

        track_title, artist, comment = fields
        print(f"UI: Double clicked on track: {track_title} by {artist}")
        self.search_youtube(
            track_title, artist, comment, from_list=True, allow_auto_play=False
        )

    def search_selected_track(self):
        """右ペインで選択中の楽曲でYouTube検索する。"""
        if self.config_service.get("bring_to_front_on_hotkey", True):
            self._bring_to_front()

        selection_model = self.right_table.selectionModel()
        selected_indexes = selection_model.selectedRows() if selection_model else []
        if not selected_indexes:
            print("UI: No track selected for search")
            return

        fields = self._get_track_search_fields(selected_indexes[0].row())
        if not fields:
            print("UI: Invalid track data for search")
            return

        track_title, artist, comment = fields
        print(f"UI: Searching YouTube for selected track: {track_title} by {artist}")
        self.search_youtube(
            track_title, artist, comment, from_list=True, allow_auto_play=False
        )

    def search_youtube(
        self, track_title, artist, comment, from_list=False, allow_auto_play=True
    ):
        """YouTubeで動画を検索。
        
        - 検索中でなければ即座に実行する。
        - 既に検索中の場合は「保留キュー」に登録する（最新1件のみ）。
          既にキューに入っていた曲は破棄し、最後に追加されたものだけを保持する。
        - 前の検索完了後、1秒待機してから保留中の検索を実行する。
        
        Args:
            from_list: 右カラムのリスト由来の曲情報を保持する場合True
            allow_auto_play: 自動検索ならTrue。手動検索ではFalseにして自動再生を抑止する。
        """
        from app.utils.logger import info, error
        from app.services.youtube_service import YouTubeService
        
        # 右カラムのリストから検索された場合、楽曲情報を保持
        if from_list:
            self._current_track_info = {
                "title": track_title or "",
                "artist": artist or "",
                "comment": comment or ""
            }
            print(f"UI: Track info saved - title: {track_title}, artist: {artist}, comment: {comment}")
        
        # 検索中なら保留キューに登録して終了
        if self.youtube_search_thread and self.youtube_search_thread.isRunning():
            # 古い保留は破棄して最新の1件だけ保持
            self._pending_search_args = (
                track_title, artist, comment, from_list, allow_auto_play
            )
            info(f"Search queued (previous search running): {track_title}", "UI")
            return
        
        # この検索が自動再生対象かを検索完了まで保持する。
        # 検索は同時に1本だけなので、アクティブ検索の属性として保持できる。
        self._active_search_allow_auto_play = bool(allow_auto_play)

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
        """検索QThreadが実際に終了した後だけ参照を解放する。
        保留中の検索があれば 1秒後に実行する。
        """
        from app.utils.logger import info

        finished_thread = self.sender()
        # 古いスレッドの finished が遅れて届いても、新しい検索を消さない。
        if (
            finished_thread is not None
            and self.youtube_search_thread is not None
            and finished_thread is not self.youtube_search_thread
        ):
            info("Ignoring stale YouTube search finished signal.", "UI")
            return

        self._set_searching_state(False)
        if finished_thread is None or finished_thread is self.youtube_search_thread:
            self.youtube_search_thread = None

        if self._pending_search_args is not None:
            info("Search finished. Pending search found - will execute in 1 second.", "UI")
            self._pending_search_timer.start(1000)  # 1秒後に保留検索を実行

    def _execute_pending_search(self):
        """保留中のYouTube検索を実行する（検索完了から1秒後に呼ばれる）"""
        from app.utils.logger import info
        
        if self._pending_search_args is None:
            return
        
        track_title, artist, comment, from_list, allow_auto_play = self._pending_search_args
        self._pending_search_args = None  # キューをクリア
        
        info(f"Executing pending search: {track_title}", "UI")
        self.search_youtube(
            track_title,
            artist,
            comment,
            from_list=from_list,
            allow_auto_play=allow_auto_play,
        )

    def _auto_play_top_video(self, video):
        """検索結果1位を既存のPRELOAD -> ready -> PLAY経路で自動再生する。"""
        if not getattr(self, "auto_play_top_result", False):
            return
        if not video:
            return

        video_id = video.get("video_id", "")
        title = video.get("title", "")
        if not video_id:
            print("UI: Auto-play skipped - top search result has no video ID")
            return
        if not hasattr(self, "player_server") or not self.player_server:
            print("UI: Auto-play skipped - player server not available")
            return

        # 既存の手動再生と同じ経路を使う。readyフィードバック後にPLAYされる。
        self.preloaded_video_id = video_id
        self.pending_play_video_id = video_id

        # Rekordbox/Shazamの自動検索→自動再生にだけ適用する。
        # 0秒なら従来どおり何もしない。
        try:
            auto_seek_seconds = int(self.config_service.get("auto_play_seek_seconds", 0))
        except (TypeError, ValueError):
            auto_seek_seconds = 0
        self.pending_auto_seek_video_id = video_id if auto_seek_seconds > 0 else None

        self._send_video_command("PRELOAD", video_id, video)
        self._update_youtube_video_state("preloading", video_id)
        print(f"UI: Auto-play queued top search result: {title} ({video_id})")

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
        
        # 検索結果は受信済みだが、QThread の run() はまだ終了処理中の可能性がある。
        # 参照解放は finished シグナルから _on_search_finished() が呼ばれた時だけ行う。
        
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
                'thumbnail_url': video.get('thumbnail_url', ''),
                'duration': video.get('duration', ''),
                'url': video.get('url', '')
            })
        
        # 左ペインに即時表示
        self.left_pane.set_search_results(processed_videos)
        info(f"Found {len(videos)} YouTube videos (showing {initial_display_count} immediately)", "UI")

        # 自動検索だけ、自動再生ONなら検索結果1位をPRELOAD -> ready -> PLAYへ送る。
        # 検索ボックス、右カラムのダブルクリック、検索ホットキーなどの手動検索では
        # タイトルバーの自動再生設定がONでも自動再生しない。
        if initial_videos and getattr(self, "_active_search_allow_auto_play", True):
            self._auto_play_top_video(initial_videos[0])
        elif initial_videos:
            print("UI: Auto-play skipped - manual YouTube search")
        
        # 最初の動画を選択状態にする（遅延実行で確実に設定）
        if processed_videos:
            QTimer.singleShot(200, self._select_first_video)  # 50msから200msに延長
        
        # 非同期でサムネイルを読み込む（最初の5件）
        # 新しい検索開始なのでキューをリセットしてから追加
        if hasattr(self, '_thumbnail_manager') and self._thumbnail_manager:
            self._thumbnail_manager.reset()
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
                'thumbnail_url': video.get('thumbnail_url', ''),
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
            
            # メモリ使用量が600MBを超えたら警告 (サムネイル表示アプリとして200MBは低すぎる)
            if memory_mb > 600:
                print(f"UI: Memory usage high: {memory_mb:.1f}MB - performing cleanup")
                self._perform_memory_cleanup()
            
            # 1500MBを超えたら強制クリーンアップ
            if memory_mb > 1500:
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
            # 完了済みの検索スレッドのみ解放（実行中は止めない）
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
            
            # YouTube検索スレッドを協調停止。実行中QThreadは finished 前に破棄しない。
            if hasattr(self, 'youtube_search_thread') and self.youtube_search_thread:
                search_thread = self.youtube_search_thread
                if search_thread.isRunning():
                    try:
                        search_thread.search_completed.disconnect()
                        search_thread.search_error.disconnect()
                    except Exception as e:
                        print(f"UI: Error disconnecting search thread signals: {e}")
                    search_thread.stop_search()

                if not search_thread.isRunning():
                    self.youtube_search_thread = None
                    print("UI: Search thread stopped safely")
                else:
                    # requests の通信が戻るまでは thread の寿命を維持する。
                    # youtube_service 側の active-thread registry も finished まで保持する。
                    print("UI: Search thread is still finishing; deferred release")

            # UIコンポーネントのデータをクリア
            if hasattr(self, 'left_pane'):
                self.left_pane.clear_results()
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
                'title': f'取得失敗しました。APIキーを確認してください。',
                'thumbnail': None,  # 後でサムネイルを設定
                'thumbnail_url': '',
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
                    self._send_video_command('PLAY', video_id, video_data)
                    self._update_youtube_video_state('playing', video_id)
                    print(f"UI: Sent PLAY command for video: {video_id}")
                else:
                    print("UI: Player server not available")
            else:
                # 1回目のダブルクリック：プリロード
                print(f"UI: First click detected - sending PRELOAD for {video_id}")
                self.last_clicked_video_id = video_id
                if hasattr(self, 'player_server') and self.player_server:
                    self._send_video_command('PRELOAD', video_id, video_data)
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
        """ブラウザのA/B状態をパネルと従来の検索結果表示へ反映する。"""
        try:
            state = str(feedback_data.get('state', '') or '')
            video_id = str(feedback_data.get('videoId', '') or '')
            player_id = str(feedback_data.get('playerId', '') or '').upper()
            is_current = feedback_data.get('isCurrent')

            import time
            self._last_player_feedback_time = time.time()
            self._update_player_control_panel(feedback_data)

            print(
                f"UI: Player feedback received - state: {state}, "
                f"player: {player_id or '-'}, video: {video_id}"
            )

            if state.upper() == 'HEARTBEAT':
                return

            # ready/preloading identify the prepared next video even though it is not visible yet.
            if state == 'ready':
                self._update_youtube_video_state('ready', video_id)

                print(
                    f"UI: Checking auto-play - pending_play_video_id: "
                    f"{self.pending_play_video_id}, video_id: {video_id}"
                )
                if self.pending_play_video_id == video_id:
                    if self._send_video_command(
                        'PLAY', video_id, self._find_video_data(video_id)
                    ):
                        self._update_youtube_video_state('playing', video_id)
                        self.pending_play_video_id = None
                        print(f"UI: Sent PLAY command for auto-play: {video_id}")
                    else:
                        print("UI: Player server not available for auto-play")

            elif state == 'preloading':
                self._update_youtube_video_state('preloading', video_id)

            elif state == 'playing':
                # A target can briefly enter PLAYING before the crossfade makes it current.
                # Only the visible player controls the legacy green border/current video fields.
                visible_playback = is_current is not False
                if visible_playback:
                    self._update_youtube_video_state('playing', video_id)

                    # Automatic search playback applies its configured initial seek once.
                    if self.pending_auto_seek_video_id == video_id:
                        self.pending_auto_seek_video_id = None
                        try:
                            auto_seek_seconds = int(
                                self.config_service.get("auto_play_seek_seconds", 0)
                            )
                        except (TypeError, ValueError):
                            auto_seek_seconds = 0
                        auto_seek_seconds = max(0, min(60, auto_seek_seconds))
                        if auto_seek_seconds > 0:
                            target_player = player_id if player_id in ("A", "B") else self._selected_player_id()
                            if hasattr(self, 'player_server') and self.player_server:
                                self.player_server.send_command(
                                    'FORWARD',
                                    str(auto_seek_seconds),
                                    player_id=target_player,
                                )
                                if hasattr(self, "player_controls"):
                                    self.player_controls.panel(target_player).adjust_position(
                                        auto_seek_seconds
                                    )
                                print(
                                    f"UI: Auto-play started; sent FORWARD "
                                    f"({auto_seek_seconds} seconds) to player "
                                    f"{target_player} for video: {video_id}"
                                )

                    if self.last_clicked_video_id == video_id:
                        self.last_clicked_video_id = None
                        print(f"UI: Reset last_clicked_video_id after playing: {video_id}")
                    self.current_playing_video_id = video_id
                    if self.pending_play_video_id == video_id:
                        self.pending_play_video_id = None

        except Exception as e:
            print(f"UI: Error handling player feedback: {e}")
            import traceback
            print(f"UI: Traceback: {traceback.format_exc()}")

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
        """YouTubeリスト外枠を更新する。Noneならテーマ標準色へ戻す。"""
        try:
            if hasattr(self.left_pane, "set_border_color"):
                self.left_pane.set_border_color(border_color)
            elif border_color:
                current_style = self.left_pane.styleSheet()
                self.left_pane.setStyleSheet(current_style + f"\nQListView {{ border: 2px solid {border_color}; }}")
            print(f"UI: YouTube border color updated to {border_color or 'theme default'}")
        except Exception as e:
            print(f"UI: Error in safe border color update: {e}")

    def _preload_video(self, video_id):
        """動画をプリロード"""
        if not video_id:
            return
        
        if hasattr(self, 'player_server') and self.player_server:
            self._send_video_command('PRELOAD', video_id, self._find_video_data(video_id))
            self._update_youtube_video_state('preloading', video_id)
            print(f"UI: Sent PRELOAD command for video: {video_id}")
        else:
            print("UI: Player server not available for preload")
    
    def _send_player_config(self):
        """プレイヤーに設定を送信する"""
        try:
            if hasattr(self, 'player_server') and self.player_server:
                position = self.config_service.get("player_track_info_position", "top-right")
                config_data = {
                    "trackInfoPosition": position
                }
                import json
                self.player_server.send_command('SET_CONFIG', json.dumps(config_data))
                print(f"UI: Sent SET_CONFIG to player - trackInfoPosition: {position}")
        except Exception as e:
            print(f"UI: Error sending player config: {e}")
    
    def closeEvent(self, event):
        """アプリケーション終了時のクリーンアップ"""
        try:
            print("UI: Cleaning up on application exit...")
            
            # メモリ監視タイマーを停止
            if hasattr(self, '_memory_check_timer'):
                self._memory_check_timer.stop()
            if hasattr(self, '_player_panel_timer'):
                self._player_panel_timer.stop()
            
            # 強制メモリクリーンアップを実行
            self._force_memory_cleanup()
            
            # サムネイル読み込みスレッドの停止
            self._cleanup_thumbnail_loaders()
            
            # ホットキーサービスの停止
            if hasattr(self, 'hotkey_service'):
                self.hotkey_service.stop()
                print("UI: Hotkey service stopped")
            
            # MIDIサービスの停止
            if hasattr(self, '_midi_watchdog'):
                self._midi_watchdog.stop()
            if hasattr(self, 'midi_service'):
                self.midi_service.shutdown()
                print("UI: Midi service stopped")
            
            # 履歴監視サービスの停止
            if hasattr(self, 'watcher'):
                self.watcher.stop()
                print("UI: History watcher stopped")

            # Shazamサービスの停止
            if hasattr(self, 'shazam_service'):
                self.shazam_service.shutdown()
                print("UI: Shazam service stopped")
            
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

    # 旧 _force_memory_cleanup は上記 1181 行付近に統合済み

    def eventFilter(self, obj, event):
        """イベントフィルター - フォーカス管理、ホットキー、タイマー制御"""
        
        # タイマー動作中にユーザーによる手動操作（クリック）があったらその時刻とフラグを記録
        if event.type() == QEvent.MouseButtonPress:
            import time
            self._last_user_interacted_time = time.time()
            self._user_has_clicked_since_front = True  # 前面化後のクリックを記録
            if hasattr(self, '_bring_to_back_timer') and self._bring_to_back_timer.isActive():
                self._bring_to_back_timer.stop()
                print(f"UI: Stopped bring-to-back timer due to MouseButtonPress on {obj}")
        
        elif event.type() == QEvent.WindowActivate:
            # プログラムによるフラグ変更でも発生するため、ここでのタイマー停止は行わず
            # WindowActivate 自体は無視する（クリックのみを操作とみなす）
            pass

        if obj == self.youtube_search_box:
            if event.type() == QEvent.FocusIn:
                print("UI: Search box focused - temporarily unregistering hotkeys")
                if hasattr(self, 'hotkey_service'):
                    self.hotkey_service.unregister_all()
            elif event.type() == QEvent.FocusOut:
                print("UI: Search box focus lost - re-registering hotkeys")
                # 少し遅延させて再登録することで、IME系のイベント処理が終わってからフックし直す
                if hasattr(self, 'hotkey_service'):
                    QTimer.singleShot(100, self.hotkey_service._reregister_hotkeys)

        return super().eventFilter(obj, event)

def main():
    try:
        # ログ初期設定
        from app.services.config_service import ConfigService
        from app.utils.logger import configure_logging
        config = ConfigService()
        enable_logging = config.get("enable_logging", True)
        configure_logging(enabled=enable_logging, redirect=True)
        
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
        # ロガー経由で致命的エラーを出力
        import traceback
        error_msg = f"UI: FATAL ERROR in main: {e}\n{traceback.format_exc()}"
        print(error_msg)
        try:
            from app.utils.logger import error
            error(error_msg, "FATAL")
        except:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
