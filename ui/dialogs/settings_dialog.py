from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, 
                               QLabel, QLineEdit, QPushButton, QTabWidget, 
                               QCheckBox, QSpinBox, QGroupBox, QWidget, QApplication, QFileDialog)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QClipboard, QKeyEvent

class HotkeyEdit(QLineEdit):
    """
    ホットキー入力用のカスタムウィジェット
    キーボード入力をキャプチャして "ctrl+shift+up" のような形式で表示する
    """
    hotkey_changed = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("クリックしてキーを入力...")
        self._current_keys = set()
    
    def keyPressEvent(self, event: QKeyEvent):
        """キーが押された時の処理"""
        key = event.key()
        
        # Escキーでクリア
        if key == Qt.Key_Escape:
            self.clear()
            self.hotkey_changed.emit("")
            return
            
        # 修飾キー単体（Ctrl, Shift, Alt, Meta）の場合は無視
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return
        
        # 修飾キーと通常キーを収集
        modifiers = event.modifiers()
        key_parts = []
        
        if modifiers & Qt.ControlModifier:
            key_parts.append("ctrl")
        if modifiers & Qt.ShiftModifier:
            # 文字キー（A, *, +等）で、すでにその文字がShiftを必要とする場合は含めない判断もあるが、
            # HotkeyService側で解釈できるように基本は付ける
            key_parts.append("shift")
        if modifiers & Qt.AltModifier:
            key_parts.append("alt")
        if modifiers & Qt.MetaModifier:
            key_parts.append("windows")
        # 通常キーを追加
        key_name = self._get_key_name(event)
        if key_name:
            key_parts.append(key_name)
        elif key in (Qt.Key_Backslash, Qt.Key_Yen, Qt.Key_Bar):
            # _get_key_name が何らかの理由で None を返した場合のバックアップ
            key_name = "\\" if key != Qt.Key_Bar else "|"
            key_parts.append(key_name)
        
        # ホットキー文字列を生成
        if key_parts:
            # 修飾キーのみの場合（例: ctrl だけ押されている）は、ホットキーとして不完全なのでテキストを更新しない
            # ただし、すでに通常キーが含まれている場合はOK。
            # 通常キーが含まれていない場合、表示を ctrl+... 等にして保留する
            has_normal_key = False
            for p in key_parts:
                if p not in ("ctrl", "shift", "alt", "windows"):
                    has_normal_key = True
                    break
            
            # ユーザーが求めているのは「ctrl + \」のような完成形なので、通常キーがある場合のみ emit する
            hotkey_str = "+".join(key_parts)
            self.setText(hotkey_str)
            if has_normal_key:
                self.hotkey_changed.emit(hotkey_str)
    
    def _get_key_name(self, event: QKeyEvent):
        """QtのキーイベントからOSが期待するキー名を取得"""
        key = event.key()
        text = event.text()
        
        key_map = {
            Qt.Key_Up: "up",
            Qt.Key_Down: "down",
            Qt.Key_Left: "left",
            Qt.Key_Right: "right",
            Qt.Key_Space: "space",
            Qt.Key_Return: "enter",
            Qt.Key_Enter: "enter",
            Qt.Key_Tab: "tab",
            Qt.Key_Backspace: "backspace",
            Qt.Key_Delete: "delete",
            Qt.Key_Home: "home",
            Qt.Key_End: "end",
            Qt.Key_PageUp: "page up",
            Qt.Key_PageDown: "page down",
            Qt.Key_Insert: "insert",
            Qt.Key_Escape: "esc",
            # 記号・テンキー明示マッピング
            Qt.Key_Asterisk: "*",
            Qt.Key_Plus: "+",
            Qt.Key_Minus: "-",
            Qt.Key_Period: ".",
            Qt.Key_Slash: "/",
            Qt.Key_Backslash: "\\",
            Qt.Key_Bar: "|",
        }
        
        # 1. まずは固定マップ（特殊キーや記号）を優先確認
        if key in key_map:
            return key_map[key]
        
        # JIS配列の円記号 (¥) やバックスラッシュが数値で報告される場合への対応
        if key == 165 or key == 167: # 165 は ¥
            return "\\"
        
        # 2. 記号系：event.text() 判定
        if text and text.isprintable() and text.strip():
            return text.lower()
            
        # 3. F1-F12キー
        if Qt.Key_F1 <= key <= Qt.Key_F12:
            return f"f{key - Qt.Key_F1 + 1}"
        
        return None


class SettingsDialog(QDialog):
    """
    アプリケーションの詳細設定を行うダイアログ
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        from app.services.config_service import ConfigService
        self.config_service = ConfigService()
        
        self.setWindowTitle("詳細設定")
        self.resize(500, 300)
        
        # メインレイアウト
        self.layout = QVBoxLayout(self)
        
        # タブウィジェット
        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs)
        
        # 各タブの構築
        self._init_general_tab()
        self._init_rekordbox_tab()
        self._init_hotkey_tab()
        self._init_youtube_tab()
        
        # 既存設定の読み込み
        self._load_current_settings()
        
        # ボタンエリア
        self._init_button_box()

    def _load_current_settings(self):
        """現在の設定値をUIに反映させる"""
        self.db_path_edit.setText(self.config_service.get("db_path", ""))
        self.interval_edit.setText(str(self.config_service.get("interval_s", 10)))
        self.player_port_spin.setValue(int(self.config_service.get("player_port", 8080)))
        self.always_on_top_checkbox.setChecked(bool(self.config_service.get("always_on_top", False)))
        self.bring_to_front_on_hotkey_checkbox.setChecked(bool(self.config_service.get("bring_to_front_on_hotkey", True)))
        self.bring_to_front_on_search_checkbox.setChecked(bool(self.config_service.get("bring_to_front_on_search", False)))
        self.bring_to_back_delay_spin.setValue(int(self.config_service.get("bring_to_back_delay_s", 3)))
        self._sync_window_placement_mode_ui()
        self.hotkey_up_edit.setText(self.config_service.get("hotkey_move_up", "ctrl+shift+up"))
        self.hotkey_down_edit.setText(self.config_service.get("hotkey_move_down", "ctrl+shift+down"))
        self.hotkey_left_edit.setText(self.config_service.get("hotkey_move_left", "ctrl+shift+left"))
        self.hotkey_right_edit.setText(self.config_service.get("hotkey_move_right", "ctrl+shift+right"))
        self.hotkey_preload_edit.setText(self.config_service.get("hotkey_preload", "ctrl+enter"))
        self.hotkey_play_edit.setText(self.config_service.get("hotkey_play", "shift+enter"))
        self.hotkey_search_edit.setText(self.config_service.get("hotkey_search", "ctrl+shift+enter"))
        self.youtube_api_key_edit.setText(self.config_service.get("youtube_api_key", ""))
        self.youtube_search_template_edit.setText(self.config_service.get("youtube_search_template", "%tracktitle% %comment%"))

    def _init_general_tab(self):
        """「全般」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # 更新間隔
        self.interval_edit = QLineEdit()
        layout.addRow("更新間隔 (秒):", self.interval_edit)

        # プレイヤーポート
        port_layout = QHBoxLayout()
        self.player_port_spin = QSpinBox()
        self.player_port_spin.setRange(1, 65535)
        self.player_port_spin.setValue(8080)
        self.player_port_spin.valueChanged.connect(self._update_player_url)
        port_layout.addWidget(QLabel("ポート番号:"))
        port_layout.addWidget(self.player_port_spin)
        
        # URL表示とコピーボタン
        url_layout = QVBoxLayout()
        self.player_url_label = QLabel()
        self.player_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.player_url_label.setStyleSheet("QLabel { background-color: #f0f0f0; padding: 5px; border: 1px solid #ccc; }")
        
        copy_button = QPushButton("コピー")
        copy_button.clicked.connect(self._copy_player_url)
        copy_button.setMaximumWidth(80)
        
        url_layout.addWidget(QLabel("プレイヤーURL:"))
        url_layout.addWidget(self.player_url_label)
        url_layout.addWidget(copy_button)
        
        port_layout.addLayout(url_layout)
        layout.addRow(port_layout)
        
        # 初期URLを設定
        self._update_player_url()

        # ウィンドウ配置モード
        window_group = QGroupBox("ウィンドウ配置モード")
        window_layout = QVBoxLayout(window_group)

        self.always_on_top_checkbox = QCheckBox("常に最前面表示する")
        self.bring_to_front_on_hotkey_checkbox = QCheckBox("ホットキー入力されたときに最前面表示し、しばらくしたら最背面に移動")
        self.bring_to_front_on_search_checkbox = QCheckBox("検索が完了したら最前面にする")

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("最前面にある時間"))
        self.bring_to_back_delay_spin = QSpinBox()
        self.bring_to_back_delay_spin.setRange(1, 3600)
        self.bring_to_back_delay_spin.setSuffix(" 秒")
        delay_row.addWidget(self.bring_to_back_delay_spin)
        delay_row.addStretch()

        window_layout.addWidget(self.always_on_top_checkbox)
        window_layout.addWidget(self.bring_to_front_on_hotkey_checkbox)
        window_layout.addWidget(self.bring_to_front_on_search_checkbox)
        window_layout.addLayout(delay_row)

        # 排他制御
        self.always_on_top_checkbox.stateChanged.connect(self._sync_window_placement_mode_ui)
        self.bring_to_front_on_hotkey_checkbox.stateChanged.connect(self._sync_window_placement_mode_ui)
        self.bring_to_front_on_search_checkbox.stateChanged.connect(self._sync_window_placement_mode_ui)

        layout.addRow(window_group)
        
        self.tabs.addTab(tab, "全般")

    def _sync_window_placement_mode_ui(self):
        """ウィンドウ配置モード（排他）のUI状態を同期"""
        always_on_top = self.always_on_top_checkbox.isChecked()
        hotkey_front = self.bring_to_front_on_hotkey_checkbox.isChecked()
        search_front = self.bring_to_front_on_search_checkbox.isChecked()

        # 排他処理（常に最前面と他のオプションは同時にON不可）
        if always_on_top and (hotkey_front or search_front):
            # シグナルを一時的に無効化して相互に排他
            sender = self.sender()
            if sender == self.always_on_top_checkbox:
                # always_on_topが変更された場合、他をOFF
                self.bring_to_front_on_hotkey_checkbox.blockSignals(True)
                self.bring_to_front_on_hotkey_checkbox.setChecked(False)
                self.bring_to_front_on_hotkey_checkbox.blockSignals(False)
                self.bring_to_front_on_search_checkbox.blockSignals(True)
                self.bring_to_front_on_search_checkbox.setChecked(False)
                self.bring_to_front_on_search_checkbox.blockSignals(False)
                hotkey_front = False
                search_front = False
            elif sender == self.bring_to_front_on_hotkey_checkbox:
                # hotkey_frontが変更された場合、always_on_topをOFF
                self.always_on_top_checkbox.blockSignals(True)
                self.always_on_top_checkbox.setChecked(False)
                self.always_on_top_checkbox.blockSignals(False)
                always_on_top = False
            elif sender == self.bring_to_front_on_search_checkbox:
                # search_frontが変更された場合、always_on_topをOFF
                self.always_on_top_checkbox.blockSignals(True)
                self.always_on_top_checkbox.setChecked(False)
                self.always_on_top_checkbox.blockSignals(False)
                always_on_top = False

        self.bring_to_back_delay_spin.setEnabled(hotkey_front)

    def _update_player_url(self):
        """プレイヤーURLを更新"""
        port = self.player_port_spin.value()
        url = f"http://localhost:{port}/player.html"
        self.player_url_label.setText(url)
        
    def _copy_player_url(self):
        """プレイヤーURLをクリップボードにコピー"""
        url = self.player_url_label.text()
        clipboard = QApplication.clipboard()
        clipboard.setText(url)
        print(f"Settings: Copied player URL to clipboard: {url}")

    def _init_rekordbox_tab(self):
        """「Rekordbox」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # データベースパス設定
        self.db_path_edit = QLineEdit()
        self.db_path_edit.setPlaceholderText("master.db のパスを選択してください")
        
        path_layout = QHBoxLayout()
        path_layout.addWidget(self.db_path_edit)
        self.browse_btn = QPushButton("参照...")
        self.browse_btn.setFixedWidth(80)
        self.browse_btn.clicked.connect(self.browse_db)
        path_layout.addWidget(self.browse_btn)
        
        layout.addRow("データベースパス:", path_layout)
        
        # 注釈
        help_label = QLabel("※ master.db は通常 PIONEER/Master フォルダ内にあります。")
        help_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addRow("", help_label)
        
        self.tabs.addTab(tab, "Rekordbox")
    
    def _init_hotkey_tab(self):
        """「ホットキー」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # 説明ラベル
        info_label = QLabel("グローバルホットキーを設定します。\nアプリがバックグラウンドでも動作します。")
        info_label.setStyleSheet("color: #666; font-size: 10px; margin-bottom: 10px;")
        layout.addRow("", info_label)
        
        # 上に移動するホットキー
        self.hotkey_up_edit = HotkeyEdit()
        up_layout = QHBoxLayout()
        up_layout.addWidget(self.hotkey_up_edit)
        clear_up_btn = QPushButton("クリア")
        clear_up_btn.setFixedWidth(60)
        clear_up_btn.clicked.connect(lambda: self.hotkey_up_edit.clear())
        up_layout.addWidget(clear_up_btn)
        layout.addRow("選択行を上に移動:", up_layout)
        
        # 下に移動するホットキー
        self.hotkey_down_edit = HotkeyEdit()
        down_layout = QHBoxLayout()
        down_layout.addWidget(self.hotkey_down_edit)
        clear_down_btn = QPushButton("クリア")
        clear_down_btn.setFixedWidth(60)
        clear_down_btn.clicked.connect(lambda: self.hotkey_down_edit.clear())
        down_layout.addWidget(clear_down_btn)
        layout.addRow("選択行を下に移動:", down_layout)
        
        # 左に移動するホットキー（YouTube用）
        self.hotkey_left_edit = HotkeyEdit()
        left_layout = QHBoxLayout()
        left_layout.addWidget(self.hotkey_left_edit)
        clear_left_btn = QPushButton("クリア")
        clear_left_btn.setFixedWidth(60)
        clear_left_btn.clicked.connect(lambda: self.hotkey_left_edit.clear())
        left_layout.addWidget(clear_left_btn)
        layout.addRow("YouTube動画を左に移動:", left_layout)
        
        # 右に移動するホットキー（YouTube用）
        self.hotkey_right_edit = HotkeyEdit()
        right_layout = QHBoxLayout()
        right_layout.addWidget(self.hotkey_right_edit)
        clear_right_btn = QPushButton("クリア")
        clear_right_btn.setFixedWidth(60)
        clear_right_btn.clicked.connect(lambda: self.hotkey_right_edit.clear())
        right_layout.addWidget(clear_right_btn)
        layout.addRow("YouTube動画を右に移動:", right_layout)
        
        # プリロードするホットキー（YouTube用）
        self.hotkey_preload_edit = HotkeyEdit()
        preload_layout = QHBoxLayout()
        preload_layout.addWidget(self.hotkey_preload_edit)
        clear_preload_btn = QPushButton("クリア")
        clear_preload_btn.setFixedWidth(60)
        clear_preload_btn.clicked.connect(lambda: self.hotkey_preload_edit.clear())
        preload_layout.addWidget(clear_preload_btn)
        layout.addRow("YouTube動画をプリロード:", preload_layout)
        
        # 再生するホットキー（YouTube用）
        self.hotkey_play_edit = HotkeyEdit()
        play_layout = QHBoxLayout()
        play_layout.addWidget(self.hotkey_play_edit)
        clear_play_btn = QPushButton("クリア")
        clear_play_btn.setFixedWidth(60)
        clear_play_btn.clicked.connect(lambda: self.hotkey_play_edit.clear())
        play_layout.addWidget(clear_play_btn)
        layout.addRow("YouTube動画を再生:", play_layout)
        
        # 検索するホットキー
        self.hotkey_search_edit = HotkeyEdit()
        search_layout = QHBoxLayout()
        search_layout.addWidget(self.hotkey_search_edit)
        clear_search_btn = QPushButton("クリア")
        clear_search_btn.setFixedWidth(60)
        clear_search_btn.clicked.connect(lambda: self.hotkey_search_edit.clear())
        search_layout.addWidget(clear_search_btn)
        layout.addRow("選択曲でYouTube検索:", search_layout)
        
        # 注釈
        help_label = QLabel("※ Escキーでもクリアできます。\n※ 左右キーはYouTubeリストの動画選択に使用します。\n※ プリロード/再生はYouTube動画の操作に使用します。\n※ 検索は右ペインの選択曲でYouTube検索します。")
        help_label.setStyleSheet("color: #666; font-size: 10px;")
        help_label.setWordWrap(True)
        layout.addRow("", help_label)
        
        self.tabs.addTab(tab, "ホットキー")
    
    def _init_youtube_tab(self):
        """「YouTube」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # APIキー入力
        self.youtube_api_key_edit = QLineEdit()
        self.youtube_api_key_edit.setPlaceholderText("AIzaSy...（APIキーを入力）")
        self.youtube_api_key_edit.setEchoMode(QLineEdit.Password)  # パスワード形式で表示
        layout.addRow("YouTube APIキー:", self.youtube_api_key_edit)
        
        # 表示/非表示ボタン
        key_layout = QHBoxLayout()
        key_layout.addWidget(self.youtube_api_key_edit)
        
        self.toggle_key_btn = QPushButton("表示")
        self.toggle_key_btn.setFixedWidth(60)
        self.toggle_key_btn.setCheckable(True)
        self.toggle_key_btn.clicked.connect(self._toggle_api_key_visibility)
        key_layout.addWidget(self.toggle_key_btn)
        
        layout.addRow("", key_layout)
        
        # 説明ラベル
        info_label = QLabel("YouTube Data API v3 の設定を行います。\nAPIキーは Google Cloud Console で取得してください。")
        info_label.setStyleSheet("color: #666; font-size: 10px; margin-bottom: 10px;")
        info_label.setWordWrap(True)
        layout.addRow("", info_label)

        # ヘルプリンク
        help_label = QLabel('<a href="https://console.cloud.google.com/apis/credentials">Google Cloud Console でAPIキーを取得</a>')
        help_label.setStyleSheet("color: #1976d2; font-size: 10px;")
        help_label.setOpenExternalLinks(True)
        layout.addRow("", help_label)
        
        # 注釈
        note_label = QLabel("※ APIキーは安全に保管してください。")
        note_label.setStyleSheet("color: #666; font-size: 10px;")
        layout.addRow("", note_label)
        
        # 検索テンプレート入力
        self.youtube_search_template_edit = QLineEdit()
        self.youtube_search_template_edit.setPlaceholderText("例: %artist% %tracktitle% official video")
        layout.addRow("検索テンプレート:", self.youtube_search_template_edit)

        # 変数一覧
        variables_label = QLabel("• %tracktitle% - トラックタイトル\n• %artist% - アーティスト名\n• %comment% - コメント")
        variables_label.setStyleSheet("color: #333; font-size: 9px; margin-left: 10px; margin-bottom: 10px;")
        layout.addRow("", variables_label)

        # テンプレート例
        examples_label = QLabel("例：\n• %artist% %tracktitle%\n• %tracktitle% official video\n• %artist% - %tracktitle% live")
        examples_label.setStyleSheet("color: #666; font-size: 9px; margin-top: 5px;")
        layout.addRow("", examples_label)
        
        self.tabs.addTab(tab, "YouTube")
    
    def _toggle_api_key_visibility(self):
        """APIキーの表示/非表示を切り替える"""
        if self.toggle_key_btn.isChecked():
            self.youtube_api_key_edit.setEchoMode(QLineEdit.Normal)
            self.toggle_key_btn.setText("非表示")
        else:
            self.youtube_api_key_edit.setEchoMode(QLineEdit.Password)
            self.toggle_key_btn.setText("表示")

    def _init_button_box(self):
        """下部のボタン（適用・キャンセル）の構築"""
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.apply_button = QPushButton("適用")
        self.apply_button.clicked.connect(self.accept)
        
        self.cancel_button = QPushButton("キャンセル")
        self.cancel_button.clicked.connect(self.reject)
        
        button_layout.addWidget(self.apply_button)
        button_layout.addWidget(self.cancel_button)
        
        self.layout.addLayout(button_layout)

    def browse_db(self):
        """ファイルダイアログを開いて master.db を選択する"""
        import os
        current_path = self.db_path_edit.text()
        initial_dir = os.path.dirname(current_path) if current_path else ""
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Rekordbox master.db を選択",
            initial_dir,
            "SQLite Database (master.db);;All Files (*)"
        )
        if file_path:
            self.db_path_edit.setText(file_path)

    def accept(self):
        """適用ボタンが押された時の処理"""
        db_path = self.db_path_edit.text()
        try:
            interval = int(self.interval_edit.text())
        except ValueError:
            interval = 10

        player_port = int(self.player_port_spin.value())

        always_on_top = self.always_on_top_checkbox.isChecked()
        bring_to_front_on_hotkey = self.bring_to_front_on_hotkey_checkbox.isChecked()
        bring_to_front_on_search = self.bring_to_front_on_search_checkbox.isChecked()
        bring_to_back_delay_s = int(self.bring_to_back_delay_spin.value())
        
        hotkey_up = self.hotkey_up_edit.text()
        hotkey_down = self.hotkey_down_edit.text()
        hotkey_left = self.hotkey_left_edit.text()
        hotkey_right = self.hotkey_right_edit.text()
        hotkey_preload = self.hotkey_preload_edit.text()
        hotkey_play = self.hotkey_play_edit.text()
        hotkey_search = self.hotkey_search_edit.text()
        youtube_api_key = self.youtube_api_key_edit.text()
        youtube_search_template = self.youtube_search_template_edit.text()
            
        print(f"Settings: Saving DB Path: {db_path}, Interval: {interval}")
        print(f"Settings: Saving Hotkeys - Up: {hotkey_up}, Down: {hotkey_down}, Left: {hotkey_left}, Right: {hotkey_right}")
        print(f"Settings: Saving YouTube Hotkeys - Preload: {hotkey_preload}, Play: {hotkey_play}, Search: {hotkey_search}")
        print(f"Settings: Saving Window Placement - AlwaysOnTop: {always_on_top}, HotkeyFront: {bring_to_front_on_hotkey}, SearchFront: {bring_to_front_on_search}, DelayS: {bring_to_back_delay_s}")
        print(f"Settings: Saving YouTube API Key: {'*' * len(youtube_api_key) if youtube_api_key else '(empty)'}")
        print(f"Settings: Saving YouTube Search Template: {youtube_search_template}")
        
        self.config_service.save_config({
            "db_path": db_path,
            "interval_s": interval,
            "player_port": player_port,
            "always_on_top": always_on_top,
            "bring_to_front_on_hotkey": bring_to_front_on_hotkey,
            "bring_to_front_on_search": bring_to_front_on_search,
            "bring_to_back_delay_s": bring_to_back_delay_s,
            "hotkey_move_up": hotkey_up,
            "hotkey_move_down": hotkey_down,
            "hotkey_move_left": hotkey_left,
            "hotkey_move_right": hotkey_right,
            "hotkey_preload": hotkey_preload,
            "hotkey_play": hotkey_play,
            "hotkey_search": hotkey_search,
            "youtube_api_key": youtube_api_key,
            "youtube_search_template": youtube_search_template
        })
        super().accept()
