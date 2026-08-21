from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, 
                               QLabel, QLineEdit, QPushButton, QTabWidget, 
                               QCheckBox, QSpinBox, QGroupBox, QWidget, QApplication, QFileDialog, QComboBox,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QClipboard, QKeyEvent, QColor

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
        from app.services.hotkey_service import HotkeyService
        from app.services.midi_service import MidiService
        from app.services.youtube_api_key_store import YouTubeApiKeyStore
        self.config_service = ConfigService()
        from ui.theme import normalize_theme
        self.ui_theme = normalize_theme(self.config_service.get("ui_theme", "dark"))
        self.hotkey_service = HotkeyService()
        self.midi_service = MidiService()
        self.youtube_api_key_store = YouTubeApiKeyStore(self.config_service)
        self.youtube_api_keys = []
        self.youtube_active_key_index = -1
        self._youtube_keys_visible = False
        
        self.setWindowTitle("詳細設定")
        self.resize(620, 520)
        
        # メインレイアウト
        self.layout = QVBoxLayout(self)
        
        # タブウィジェット
        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs)
        
        # 各タブの構築
        self._init_general_tab()
        self._init_rekordbox_tab()
        self._init_shazam_tab()
        self._init_hotkey_tab()
        self._init_midi_tab()
        self._init_youtube_tab()
        self._init_player_tab()
        
        # 既存設定の読み込み
        self._load_current_settings()
        
        # ボタンエリア
        self._init_button_box()
        
        # 設定画面が開いている間はホットキーを無効化
        self.hotkey_service.unregister_all()
        # MIDIもトリガーが発火しないよう一時的にマッピングを空に（Learn用にデバイス受信は維持）
        device_name = self.config_service.get("midi_port_name", "")
        self.midi_service.set_config(device_name, {})
        print("SettingsDialog: Hotkeys and MIDI triggers disabled while settings dialog is open")

    def _theme_color(self, name):
        from ui.theme import colors
        return colors(self.ui_theme)[name]

    def _muted_style(self, extra=""):
        return f"color: {self._theme_color('muted')}; {extra}"

    def _error_style(self, extra=""):
        return f"color: {self._theme_color('error')}; {extra}"

    def _load_current_settings(self):
        """現在の設定値をUIに反映させる"""
        self.db_path_edit.setText(self.config_service.get("db_path", ""))
        self.interval_edit.setText(str(self.config_service.get("interval_s", 10)))
        self.player_port_spin.setValue(int(self.config_service.get("player_port", 8080)))
        theme_index = self.theme_combo.findData(self.ui_theme)
        self.theme_combo.setCurrentIndex(theme_index if theme_index >= 0 else 0)

        shazam_device = self.config_service.get("shazam_input_device", None)
        shazam_index = self.shazam_input_device_combo.findData(shazam_device)
        if shazam_index < 0 and shazam_device is not None:
            try:
                shazam_index = self.shazam_input_device_combo.findData(int(shazam_device))
            except (TypeError, ValueError):
                shazam_index = -1
        self.shazam_input_device_combo.setCurrentIndex(shazam_index if shazam_index >= 0 else 0)

        shazam_language = str(self.config_service.get("shazam_language", "ja-JP") or "ja-JP")
        language_index = self.shazam_language_combo.findText(shazam_language)
        if language_index >= 0:
            self.shazam_language_combo.setCurrentIndex(language_index)
        else:
            self.shazam_language_combo.setEditText(shazam_language)

        shazam_country = str(self.config_service.get("shazam_endpoint_country", "JP") or "JP").upper()
        country_index = self.shazam_country_combo.findText(shazam_country)
        if country_index >= 0:
            self.shazam_country_combo.setCurrentIndex(country_index)
        else:
            self.shazam_country_combo.setEditText(shazam_country)

        try:
            shazam_recording_seconds = int(self.config_service.get("shazam_recording_seconds", 6))
        except (TypeError, ValueError):
            shazam_recording_seconds = 6
        self.shazam_recording_seconds_spin.setValue(max(5, min(20, shazam_recording_seconds)))

        self.always_on_top_checkbox.setChecked(bool(self.config_service.get("always_on_top", False)))
        self.bring_to_front_on_hotkey_checkbox.setChecked(bool(self.config_service.get("bring_to_front_on_hotkey", True)))
        self.bring_to_front_on_search_checkbox.setChecked(bool(self.config_service.get("bring_to_front_on_search", False)))
        self.bring_to_back_delay_spin.setValue(int(self.config_service.get("bring_to_back_delay_s", 3)))
        self.rewind_seconds_spin.setValue(int(self.config_service.get("rewind_seconds", 2)))
        self.forward_seconds_spin.setValue(int(self.config_service.get("forward_seconds", 2)))
        self._sync_window_placement_mode_ui()
        self.enable_logging_checkbox.setChecked(bool(self.config_service.get("enable_logging", True)))
        self.hotkey_up_edit.setText(self.config_service.get("hotkey_move_up", "ctrl+shift+up"))
        self.hotkey_down_edit.setText(self.config_service.get("hotkey_move_down", "ctrl+shift+down"))
        self.hotkey_left_edit.setText(self.config_service.get("hotkey_move_left", "ctrl+shift+left"))
        self.hotkey_right_edit.setText(self.config_service.get("hotkey_move_right", "ctrl+shift+right"))
        self.hotkey_preload_edit.setText(self.config_service.get("hotkey_preload", "ctrl+enter"))
        self.hotkey_play_edit.setText(self.config_service.get("hotkey_play", "shift+enter"))
        self.hotkey_search_edit.setText(self.config_service.get("hotkey_search", "ctrl+shift+enter"))
        self.hotkey_rewind_edit.setText(self.config_service.get("hotkey_rewind", "ctrl+;"))
        self.hotkey_forward_edit.setText(self.config_service.get("hotkey_forward", "ctrl+:"))
        
        self.midi_up_edit.setText(str(self.config_service.get("midi_move_up", -1)))
        self.midi_down_edit.setText(str(self.config_service.get("midi_move_down", -1)))
        self.midi_left_edit.setText(str(self.config_service.get("midi_move_left", -1)))
        self.midi_right_edit.setText(str(self.config_service.get("midi_move_right", -1)))
        self.midi_preload_edit.setText(str(self.config_service.get("midi_preload", -1)))
        self.midi_play_edit.setText(str(self.config_service.get("midi_play", -1)))
        self.midi_search_edit.setText(str(self.config_service.get("midi_search", -1)))
        self.midi_rewind_edit.setText(str(self.config_service.get("midi_rewind", -1)))
        self.midi_forward_edit.setText(str(self.config_service.get("midi_forward", -1)))
        
        port_name = self.config_service.get("midi_port_name", "")
        idx = self.midi_device_combo.findText(port_name)
        if idx >= 0:
            self.midi_device_combo.setCurrentIndex(idx)
        else:
            self.midi_device_combo.setCurrentIndex(0)
            
        self.youtube_api_keys, self.youtube_active_key_index = self.youtube_api_key_store.load()
        self._refresh_youtube_api_key_table()
        self.youtube_search_template_edit.setText(self.config_service.get("youtube_search_template", "%tracktitle% %comment%"))
        
        # プレイヤータブの設定値を読み込み
        position = self.config_service.get("player_track_info_position", "top-right")
        
        self.rb_fixed.blockSignals(True)
        self.rb_scroll.blockSignals(True)
        self.rb_none.blockSignals(True)
        
        if position == "none":
            self.rb_none.setChecked(True)
            self.track_info_position_combo.setEnabled(False)
        elif position == "scroll":
            self.rb_scroll.setChecked(True)
            self.track_info_position_combo.setEnabled(False)
        else:
            self.rb_fixed.setChecked(True)
            self.track_info_position_combo.setEnabled(True)
            position_map = {
                "top-right": 0,
                "top-left": 1,
                "bottom-right": 2,
                "bottom-left": 3
            }
            self.track_info_position_combo.setCurrentIndex(position_map.get(position, 0))
            
        self.rb_fixed.blockSignals(False)
        self.rb_scroll.blockSignals(False)
        self.rb_none.blockSignals(False)


    def _init_general_tab(self):
        """「全般」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # UIテーマ
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("ダーク", "dark")
        self.theme_combo.addItem("ライト", "light")
        layout.addRow("UIテーマ:", self.theme_combo)

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
        self.player_url_label.setStyleSheet(f"QLabel {{ background-color: {self._theme_color('panel_alt')}; color: {self._theme_color('text')}; padding: 5px; border: 1px solid {self._theme_color('border')}; }}")
        
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
        
        # ログ設定
        self.enable_logging_checkbox = QCheckBox("ログ出力を有効にする (vj_yattaro.log)")
        layout.addRow("デバッグ:", self.enable_logging_checkbox)
        
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
        help_label.setStyleSheet(self._muted_style("font-size: 10px;"))
        layout.addRow("", help_label)
        
        self.tabs.addTab(tab, "Rekordbox")

    def _init_shazam_tab(self):
        """「Shazam」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)

        self.shazam_input_device_combo = QComboBox()
        refresh_button = QPushButton("再取得")
        refresh_button.setFixedWidth(80)
        refresh_button.clicked.connect(self._refresh_shazam_devices)

        device_layout = QHBoxLayout()
        device_layout.addWidget(self.shazam_input_device_combo, 1)
        device_layout.addWidget(refresh_button)
        layout.addRow("使用するマイク:", device_layout)

        self.shazam_device_status_label = QLabel("")
        self.shazam_device_status_label.setWordWrap(True)
        self.shazam_device_status_label.setStyleSheet(self._muted_style("font-size: 10px;"))
        layout.addRow("", self.shazam_device_status_label)

        self.shazam_language_combo = QComboBox()
        self.shazam_language_combo.setEditable(True)
        self.shazam_language_combo.addItems(["ja-JP", "en-US", "en-GB"])
        layout.addRow("取得言語:", self.shazam_language_combo)

        self.shazam_country_combo = QComboBox()
        self.shazam_country_combo.setEditable(True)
        self.shazam_country_combo.addItems(["JP", "US", "GB"])
        layout.addRow("国/地域:", self.shazam_country_combo)

        locale_help = QLabel(
            "日本語の曲名・アーティスト名を優先する場合は「ja-JP / JP」を指定します。\n"
            "Shazam側に日本語表記がない曲は、英字表記のまま返る場合があります。"
        )
        locale_help.setWordWrap(True)
        locale_help.setStyleSheet(self._muted_style("font-size: 10px;"))
        layout.addRow("", locale_help)

        self.shazam_recording_seconds_spin = QSpinBox()
        self.shazam_recording_seconds_spin.setRange(5, 20)
        self.shazam_recording_seconds_spin.setSuffix(" 秒")
        self.shazam_recording_seconds_spin.setValue(6)
        self.shazam_recording_seconds_spin.setToolTip(
            "Shazamへ送る直近の音声時間を5〜20秒で指定します。"
        )
        layout.addRow("録音時間:", self.shazam_recording_seconds_spin)

        info_label = QLabel(
            "mono / int16 で常時取り込み、可能なら16 kHzを使用します。\n"
            "16 kHz非対応のマイクはネイティブ周波数で取得し、判定時だけ16 kHzへ変換します。\n"
            "指定した5〜20秒分を保持し、3秒ごとに最新の録音区間をShazam判定します。"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._muted_style("font-size: 10px;"))
        layout.addRow("", info_label)

        history_label = QLabel("Shazam履歴は最大50件。shazam_history.json に保存します。")
        history_label.setStyleSheet(self._muted_style("font-size: 10px;"))
        layout.addRow("", history_label)

        self._refresh_shazam_devices()
        self.tabs.addTab(tab, "Shazam")

    def _refresh_shazam_devices(self):
        current_data = None
        if hasattr(self, "shazam_input_device_combo") and self.shazam_input_device_combo.count() > 0:
            current_data = self.shazam_input_device_combo.currentData()

        self.shazam_input_device_combo.clear()
        self.shazam_input_device_combo.addItem("(システム既定)", None)

        try:
            from app.services.shazam_service import ShazamService
            devices, error = ShazamService.list_input_devices()
            for device_index, display_name in devices:
                self.shazam_input_device_combo.addItem(display_name, device_index)
            if error:
                message = f"入力デバイス一覧の取得に失敗: {error}"
                self.shazam_input_device_combo.setToolTip(message)
                if hasattr(self, "shazam_device_status_label"):
                    self.shazam_device_status_label.setText(message)
                    self.shazam_device_status_label.setStyleSheet(self._error_style("font-size: 10px;"))
            else:
                self.shazam_input_device_combo.setToolTip("")
                if hasattr(self, "shazam_device_status_label"):
                    self.shazam_device_status_label.setText(f"入力デバイスを {len(devices)} 件検出しました。WindowsではWASAPIを優先表示します。")
                    self.shazam_device_status_label.setStyleSheet(self._muted_style("font-size: 10px;"))
        except Exception as e:
            message = f"入力デバイス一覧の取得に失敗: {e}"
            self.shazam_input_device_combo.setToolTip(message)
            if hasattr(self, "shazam_device_status_label"):
                self.shazam_device_status_label.setText(message)
                self.shazam_device_status_label.setStyleSheet(self._error_style("font-size: 10px;"))

        idx = self.shazam_input_device_combo.findData(current_data)
        if idx >= 0:
            self.shazam_input_device_combo.setCurrentIndex(idx)

    def _init_hotkey_tab(self):
        """「ホットキー」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # 説明ラベル
        info_label = QLabel("グローバルホットキーを設定します。\nアプリがバックグラウンドでも動作します。")
        info_label.setStyleSheet(self._muted_style("font-size: 10px; margin-bottom: 10px;"))
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

        # 巻き戻しホットキー
        self.hotkey_rewind_edit = HotkeyEdit()
        clear_rewind_btn = QPushButton("クリア")
        clear_rewind_btn.setFixedWidth(60)
        clear_rewind_btn.clicked.connect(lambda: self.hotkey_rewind_edit.clear())
        rewind_layout = QHBoxLayout()
        rewind_layout.addWidget(self.hotkey_rewind_edit)
        rewind_layout.addWidget(clear_rewind_btn)
        layout.addRow("巻き戻し:", rewind_layout)

        # 早送りホットキー
        self.hotkey_forward_edit = HotkeyEdit()
        clear_forward_btn = QPushButton("クリア")
        clear_forward_btn.setFixedWidth(60)
        clear_forward_btn.clicked.connect(lambda: self.hotkey_forward_edit.clear())
        forward_layout = QHBoxLayout()
        forward_layout.addWidget(self.hotkey_forward_edit)
        forward_layout.addWidget(clear_forward_btn)
        layout.addRow("早送り:", forward_layout)
        
        # 注釈
        help_label = QLabel("※ Escキーでもクリアできます。\n※ 左右キーはYouTubeリストの動画選択に使用します。\n※ プリロード/再生はYouTube動画の操作に使用します。\n※ 検索は右ペインの選択曲でYouTube検索します。")
        help_label.setStyleSheet(self._muted_style("font-size: 10px;"))
        help_label.setWordWrap(True)
        layout.addRow("", help_label)
        
        self.tabs.addTab(tab, "ホットキー")

    def _init_midi_tab(self):
        """「MIDI」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # デバイス選択
        self.midi_device_combo = QComboBox()
        self.midi_device_combo.addItem("(なし)")
        devices = self.midi_service.get_input_devices()
        for idx, name in devices:
            self.midi_device_combo.addItem(name)
        
        refresh_btn = QPushButton("更新")
        refresh_btn.setFixedWidth(60)
        refresh_btn.clicked.connect(self._refresh_midi_devices)
        
        device_layout = QHBoxLayout()
        device_layout.addWidget(self.midi_device_combo)
        device_layout.addWidget(refresh_btn)
        
        layout.addRow("MIDI入力デバイス:", device_layout)
        
        info_label = QLabel("MIDIパッド等を操作して、各アクションに割り当てるノート番号を設定できます。\n「Learn」ボタンを押してからMIDIパッドを叩いてください。")
        info_label.setStyleSheet(self._muted_style("font-size: 10px; margin-bottom: 10px;"))
        layout.addRow("", info_label)
        
        # 学習中のターゲットを保持する変数
        self._current_midi_learning_edit = None
        self.midi_service.raw_note_received.connect(self._on_raw_midi_note)
        
        def create_midi_row(label, attr_name):
            edit = QLineEdit()
            edit.setReadOnly(True)
            edit.setPlaceholderText("-1")
            edit.setMaximumWidth(60)
            setattr(self, attr_name, edit)
            
            learn_btn = QPushButton("Learn")
            learn_btn.setFixedWidth(60)
            learn_btn.setCheckable(True)
            
            clear_btn = QPushButton("クリア")
            clear_btn.setFixedWidth(60)
            clear_btn.clicked.connect(lambda: edit.setText("-1"))
            
            # Learnのクリック時の処理
            def on_learn_clicked(checked, btn=learn_btn, e=edit):
                if checked:
                    # 他の学習中を解除
                    if self._current_midi_learning_edit and self._current_midi_learning_edit != btn:
                        self._current_midi_learning_edit.setChecked(False)
                        self._current_midi_learning_edit.setText("Learn")
                    self._current_midi_learning_edit = btn
                    btn.setText("待機中...")
                else:
                    if self._current_midi_learning_edit == btn:
                        self._current_midi_learning_edit = None
                    btn.setText("Learn")
                    
            learn_btn.clicked.connect(on_learn_clicked)
            # btnオブジェクトへの参照をQObjectに持たせる（コールバック用）
            setattr(edit, "_learn_btn", learn_btn)
            
            row = QHBoxLayout()
            row.addWidget(edit)
            row.addWidget(learn_btn)
            row.addWidget(clear_btn)
            row.addStretch()
            layout.addRow(label, row)
        
        create_midi_row("選択行を上に移動:", "midi_up_edit")
        create_midi_row("選択行を下に移動:", "midi_down_edit")
        create_midi_row("YouTube動画を左に移動:", "midi_left_edit")
        create_midi_row("YouTube動画を右に移動:", "midi_right_edit")
        create_midi_row("YouTube動画をプリロード:", "midi_preload_edit")
        create_midi_row("YouTube動画を再生:", "midi_play_edit")
        create_midi_row("選択曲でYouTube検索:", "midi_search_edit")
        create_midi_row("巻き戻し:", "midi_rewind_edit")
        create_midi_row("早送り:", "midi_forward_edit")

        # デバイス変更時に接続し直す処理
        self.midi_device_combo.currentTextChanged.connect(
            lambda txt: self.midi_service.set_config(txt if txt != "(なし)" else "", {})
        )
        
        self.tabs.addTab(tab, "MIDI")

    def _refresh_midi_devices(self):
        curr = self.midi_device_combo.currentText()
        self.midi_device_combo.clear()
        self.midi_device_combo.addItem("(なし)")
        for idx, name in self.midi_service.get_input_devices():
            self.midi_device_combo.addItem(name)
        idx = self.midi_device_combo.findText(curr)
        self.midi_device_combo.setCurrentIndex(max(0, idx))

    def _on_raw_midi_note(self, note):
        if self._current_midi_learning_edit:
            btn = self._current_midi_learning_edit
            # 親を探すハックより、_learn_btnで逆引き
            for attr in dir(self):
                if attr.startswith("midi_") and attr.endswith("_edit"):
                    edit = getattr(self, attr)
                    if hasattr(edit, "_learn_btn") and getattr(edit, "_learn_btn") == btn:
                        edit.setText(str(note))
                        btn.setChecked(False)
                        btn.setText("Learn")
                        self._current_midi_learning_edit = None
                        break
    
    def _init_youtube_tab(self):
        """「YouTube」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)

        # APIキー追加欄
        key_add_layout = QHBoxLayout()
        self.youtube_api_key_edit = QLineEdit()
        self.youtube_api_key_edit.setPlaceholderText("AIzaSy...（APIキーを追加）")
        self.youtube_api_key_edit.setEchoMode(QLineEdit.Password)
        self.youtube_api_key_edit.returnPressed.connect(self._add_youtube_api_key)
        key_add_layout.addWidget(self.youtube_api_key_edit, 1)

        self.add_youtube_key_btn = QPushButton("追加")
        self.add_youtube_key_btn.setFixedWidth(60)
        self.add_youtube_key_btn.clicked.connect(self._add_youtube_api_key)
        key_add_layout.addWidget(self.add_youtube_key_btn)

        self.toggle_key_btn = QPushButton("表示")
        self.toggle_key_btn.setFixedWidth(60)
        self.toggle_key_btn.setCheckable(True)
        self.toggle_key_btn.clicked.connect(self._toggle_api_key_visibility)
        key_add_layout.addWidget(self.toggle_key_btn)
        layout.addRow("APIキー追加:", key_add_layout)

        # 最大10件のAPIキー一覧。削除時は再描画して常に1から上詰めする。
        self.youtube_api_key_table = QTableWidget(0, 3)
        self.youtube_api_key_table.setHorizontalHeaderLabels(["No.", "APIキー", "状態"])
        self.youtube_api_key_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.youtube_api_key_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.youtube_api_key_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.youtube_api_key_table.setAlternatingRowColors(False)
        self.youtube_api_key_table.verticalHeader().setVisible(False)
        self.youtube_api_key_table.setMinimumHeight(180)
        header = self.youtube_api_key_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addRow("APIキー一覧:", self.youtube_api_key_table)

        key_action_layout = QHBoxLayout()
        self.use_youtube_key_btn = QPushButton("選択したキーを使用")
        self.use_youtube_key_btn.clicked.connect(self._set_selected_youtube_api_key_active)
        key_action_layout.addWidget(self.use_youtube_key_btn)

        self.delete_youtube_key_btn = QPushButton("選択したキーを削除")
        self.delete_youtube_key_btn.clicked.connect(self._delete_selected_youtube_api_key)
        key_action_layout.addWidget(self.delete_youtube_key_btn)
        key_action_layout.addStretch()
        layout.addRow("", key_action_layout)

        self.youtube_key_count_label = QLabel("")
        self.youtube_key_count_label.setStyleSheet(self._muted_style("font-size: 10px;"))
        layout.addRow("", self.youtube_key_count_label)

        info_label = QLabel(
            "YouTube Data API v3 のAPIキーを最大10件まで保存できます。\n"
            "青色の行が現在使用中です。削除すると残りのキーは上から詰めて1〜10で再番号付けします。"
        )
        info_label.setStyleSheet(self._muted_style("font-size: 10px; margin-bottom: 10px;"))
        info_label.setWordWrap(True)
        layout.addRow("", info_label)

        help_label = QLabel('<a href="https://console.cloud.google.com/apis/credentials">Google Cloud Console でAPIキーを取得</a>')
        help_label.setStyleSheet(f"color: {self._theme_color('link')}; font-size: 10px;")
        help_label.setOpenExternalLinks(True)
        layout.addRow("", help_label)

        note_label = QLabel("※ APIキーは config.json 内に保存します。")
        note_label.setStyleSheet(self._muted_style("font-size: 10px;"))
        layout.addRow("", note_label)

        self.youtube_search_template_edit = QLineEdit()
        self.youtube_search_template_edit.setPlaceholderText("例: %artist% %tracktitle% official video")
        layout.addRow("検索テンプレート:", self.youtube_search_template_edit)

        variables_label = QLabel("• %tracktitle% - トラックタイトル\n• %artist% - アーティスト名\n• %comment% - コメント")
        variables_label.setStyleSheet(f"color: {self._theme_color('text')}; font-size: 9px; margin-left: 10px; margin-bottom: 10px;")
        layout.addRow("", variables_label)

        examples_label = QLabel("例：\n• %artist% %tracktitle%\n• %tracktitle% official video\n• %artist% - %tracktitle% live")
        examples_label.setStyleSheet(self._muted_style("font-size: 9px; margin-top: 5px;"))
        layout.addRow("", examples_label)

        self.tabs.addTab(tab, "YouTube")

    @staticmethod
    def _masked_youtube_api_key(key):
        key = str(key or "")
        if len(key) <= 10:
            return "*" * len(key)
        return f"{key[:4]}{'*' * max(6, len(key) - 8)}{key[-4:]}"

    def _refresh_youtube_api_key_table(self, select_row=None):
        if not hasattr(self, "youtube_api_key_table"):
            return

        table = self.youtube_api_key_table
        table.setRowCount(len(self.youtube_api_keys))

        active_bg = QColor(self._theme_color("youtube_active"))
        active_fg = QColor(self._theme_color("youtube_active_text"))
        normal_bg = QColor(self._theme_color("panel"))
        normal_fg = QColor(self._theme_color("text"))

        for row, key in enumerate(self.youtube_api_keys):
            display_key = key if self._youtube_keys_visible else self._masked_youtube_api_key(key)
            values = [str(row + 1), display_key, "使用中" if row == self.youtube_active_key_index else ""]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(active_bg if row == self.youtube_active_key_index else normal_bg)
                item.setForeground(active_fg if row == self.youtube_active_key_index else normal_fg)
                if row == self.youtube_active_key_index:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if col in (0, 2):
                    item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, col, item)

        if hasattr(self, "youtube_key_count_label"):
            active_no = self.youtube_active_key_index + 1 if self.youtube_active_key_index >= 0 else "なし"
            self.youtube_key_count_label.setText(
                f"登録数: {len(self.youtube_api_keys)}/10    使用中: {active_no}"
            )

        if self.youtube_api_keys:
            if select_row is None:
                select_row = self.youtube_active_key_index
            if select_row is not None and 0 <= select_row < len(self.youtube_api_keys):
                table.selectRow(select_row)

    def _add_youtube_api_key(self):
        key = self.youtube_api_key_edit.text().strip()
        if not key:
            return
        if len(self.youtube_api_keys) >= 10:
            QMessageBox.warning(self, "YouTube APIキー", "APIキーは最大10件までです。")
            return
        if key in self.youtube_api_keys:
            row = self.youtube_api_keys.index(key)
            self._refresh_youtube_api_key_table(row)
            QMessageBox.information(self, "YouTube APIキー", "同じAPIキーは既に登録されています。")
            return

        self.youtube_api_keys.append(key)
        if self.youtube_active_key_index < 0:
            self.youtube_active_key_index = 0
        self.youtube_api_key_edit.clear()
        self._refresh_youtube_api_key_table(len(self.youtube_api_keys) - 1)

    def _delete_selected_youtube_api_key(self):
        row = self.youtube_api_key_table.currentRow()
        if row < 0 or row >= len(self.youtube_api_keys):
            return

        old_active = self.youtube_active_key_index
        del self.youtube_api_keys[row]

        if not self.youtube_api_keys:
            self.youtube_active_key_index = -1
            next_row = None
        elif row < old_active:
            self.youtube_active_key_index = old_active - 1
            next_row = min(row, len(self.youtube_api_keys) - 1)
        elif row == old_active:
            self.youtube_active_key_index = min(row, len(self.youtube_api_keys) - 1)
            next_row = self.youtube_active_key_index
        else:
            next_row = min(row, len(self.youtube_api_keys) - 1)

        self._refresh_youtube_api_key_table(next_row)

    def _set_selected_youtube_api_key_active(self):
        row = self.youtube_api_key_table.currentRow()
        if row < 0 or row >= len(self.youtube_api_keys):
            return
        self.youtube_active_key_index = row
        self._refresh_youtube_api_key_table(row)

    def _init_player_tab(self):
        """「プレイヤー」タブの構築"""
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # 説明ラベル
        info_label = QLabel("プレイヤー画面の表示に関する設定を行います。")
        info_label.setStyleSheet(self._muted_style("font-size: 10px; margin-bottom: 10px;"))
        layout.addRow("", info_label)
        
        # 楽曲情報の表示位置
        track_info_group = QGroupBox("楽曲情報表示")
        track_info_layout = QVBoxLayout(track_info_group)
        
        from PySide6.QtWidgets import QRadioButton, QButtonGroup
        self.track_info_style_group = QButtonGroup(self)
        
        self.rb_fixed = QRadioButton("四隅に固定表示")
        self.rb_scroll = QRadioButton("画面下部を横スクロールで表示")
        self.rb_none = QRadioButton("表示しない")
        
        self.track_info_style_group.addButton(self.rb_fixed)
        self.track_info_style_group.addButton(self.rb_scroll)
        self.track_info_style_group.addButton(self.rb_none)
        
        # 四隅表示の位置選択用コンボボックス
        fixed_layout = QHBoxLayout()
        self.track_info_position_combo = QComboBox()
        self.track_info_position_combo.addItems([
            "右上",    # top-right
            "左上",    # top-left
            "右下",    # bottom-right
            "左下"     # bottom-left
        ])
        fixed_layout.addWidget(self.rb_fixed)
        fixed_layout.addWidget(self.track_info_position_combo)
        fixed_layout.addStretch()
        
        track_info_layout.addLayout(fixed_layout)
        track_info_layout.addWidget(self.rb_scroll)
        track_info_layout.addWidget(self.rb_none)
        
        # ラジオボタンの切り替えトグルイベント
        def on_style_toggled():
            # 固定表示が選択されている場合のみコンボボックスを有効化
            self.track_info_position_combo.setEnabled(self.rb_fixed.isChecked())
            
        self.rb_fixed.toggled.connect(on_style_toggled)
        self.rb_scroll.toggled.connect(on_style_toggled)
        self.rb_none.toggled.connect(on_style_toggled)
        
        # 説明
        desc_label = QLabel("※ 右カラムのリストから検索した場合に、\n　曲名・アーティスト名・コメントをプレイヤーに表示します。")
        desc_label.setStyleSheet(self._muted_style("font-size: 10px; margin-top: 5px;"))
        desc_label.setWordWrap(True)
        track_info_layout.addWidget(desc_label)
        
        layout.addRow(track_info_group)

        
        # 巻き戻し・早送り設定
        seek_group = QGroupBox("巻き戻し・早送り")
        seek_layout = QVBoxLayout(seek_group)

        # 巻き戻し秒数
        rewind_row = QHBoxLayout()
        rewind_row.addWidget(QLabel("巻き戻し秒数:"))
        self.rewind_seconds_spin = QSpinBox()
        self.rewind_seconds_spin.setRange(1, 60)
        self.rewind_seconds_spin.setSuffix(" 秒")
        self.rewind_seconds_spin.setValue(2)
        rewind_row.addWidget(self.rewind_seconds_spin)
        rewind_row.addStretch()

        # 早送り秒数
        forward_row = QHBoxLayout()
        forward_row.addWidget(QLabel("早送り秒数:"))
        self.forward_seconds_spin = QSpinBox()
        self.forward_seconds_spin.setRange(1, 60)
        self.forward_seconds_spin.setSuffix(" 秒")
        self.forward_seconds_spin.setValue(2)
        forward_row.addWidget(self.forward_seconds_spin)
        forward_row.addStretch()

        seek_layout.addLayout(rewind_row)
        seek_layout.addLayout(forward_row)

        layout.addRow(seek_group)
        
        self.tabs.addTab(tab, "プレイヤー")
    
    def _toggle_api_key_visibility(self):
        """追加欄とAPIキー一覧の表示/非表示を切り替える。"""
        self._youtube_keys_visible = self.toggle_key_btn.isChecked()
        if self._youtube_keys_visible:
            self.youtube_api_key_edit.setEchoMode(QLineEdit.Normal)
            self.toggle_key_btn.setText("非表示")
        else:
            self.youtube_api_key_edit.setEchoMode(QLineEdit.Password)
            self.toggle_key_btn.setText("表示")
        self._refresh_youtube_api_key_table(self.youtube_api_key_table.currentRow())

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
        ui_theme = self.theme_combo.currentData() or "dark"

        always_on_top = self.always_on_top_checkbox.isChecked()
        bring_to_front_on_hotkey = self.bring_to_front_on_hotkey_checkbox.isChecked()
        bring_to_front_on_search = self.bring_to_front_on_search_checkbox.isChecked()
        bring_to_back_delay_s = int(self.bring_to_back_delay_spin.value())
        rewind_seconds = int(self.rewind_seconds_spin.value())
        forward_seconds = int(self.forward_seconds_spin.value())
        
        hotkey_up = self.hotkey_up_edit.text()
        hotkey_down = self.hotkey_down_edit.text()
        hotkey_left = self.hotkey_left_edit.text()
        hotkey_right = self.hotkey_right_edit.text()
        hotkey_preload = self.hotkey_preload_edit.text()
        hotkey_play = self.hotkey_play_edit.text()
        hotkey_search = self.hotkey_search_edit.text()
        hotkey_rewind = self.hotkey_rewind_edit.text()
        hotkey_forward = self.hotkey_forward_edit.text()
        youtube_api_keys = list(self.youtube_api_keys)
        youtube_active_key_index = self.youtube_active_key_index
        youtube_search_template = self.youtube_search_template_edit.text()
        enable_logging = self.enable_logging_checkbox.isChecked()
        shazam_input_device = self.shazam_input_device_combo.currentData()
        shazam_language = self.shazam_language_combo.currentText().strip() or "ja-JP"
        if shazam_language.lower() == "jp-jp":
            shazam_language = "ja-JP"
        shazam_endpoint_country = self.shazam_country_combo.currentText().strip().upper() or "JP"
        shazam_recording_seconds = max(5, min(20, int(self.shazam_recording_seconds_spin.value())))
        
        # MIDI設定の取得
        midi_port_name = self.midi_device_combo.currentText()
        if midi_port_name == "(なし)": midi_port_name = ""
        def try_int_text(txt):
            try: return int(txt)
            except: return -1
        midi_move_up = try_int_text(self.midi_up_edit.text())
        midi_move_down = try_int_text(self.midi_down_edit.text())
        midi_move_left = try_int_text(self.midi_left_edit.text())
        midi_move_right = try_int_text(self.midi_right_edit.text())
        midi_preload = try_int_text(self.midi_preload_edit.text())
        midi_play = try_int_text(self.midi_play_edit.text())
        midi_search = try_int_text(self.midi_search_edit.text())
        midi_rewind = try_int_text(self.midi_rewind_edit.text())
        midi_forward = try_int_text(self.midi_forward_edit.text())
            
        print(f"Settings: Saving DB Path: {db_path}, Interval: {interval}")
        print(f"Settings: Saving Hotkeys - Up: {hotkey_up}, Down: {hotkey_down}, Left: {hotkey_left}, Right: {hotkey_right}")
        print(f"Settings: Saving YouTube Hotkeys - Preload: {hotkey_preload}, Play: {hotkey_play}, Search: {hotkey_search}, Rewind: {hotkey_rewind}, Forward: {hotkey_forward}")
        print(f"Settings: Saving Window Placement - AlwaysOnTop: {always_on_top}, HotkeyFront: {bring_to_front_on_hotkey}, SearchFront: {bring_to_front_on_search}, DelayS: {bring_to_back_delay_s}")
        print(f"Settings: Saving Seek Settings - Rewind: {rewind_seconds}s, Forward: {forward_seconds}s")
        print(
            f"Settings: Saving YouTube API Keys: {len(youtube_api_keys)} key(s), "
            f"active={youtube_active_key_index + 1 if youtube_active_key_index >= 0 else 'none'}"
        )
        print(f"Settings: Saving YouTube Search Template: {youtube_search_template}")
        print(f"Settings: Saving Shazam recording duration: {shazam_recording_seconds}s")

        if not self.youtube_api_key_store.save(youtube_api_keys, youtube_active_key_index):
            QMessageBox.critical(
                self,
                "YouTube APIキー",
                "config.json へのAPIキー保存に失敗しました。設定を適用できません。"
            )
            return

        self.config_service.save_config({
            "db_path": db_path,
            "interval_s": interval,
            "player_port": player_port,
            "ui_theme": ui_theme,
            "always_on_top": always_on_top,
            "bring_to_front_on_hotkey": bring_to_front_on_hotkey,
            "bring_to_front_on_search": bring_to_front_on_search,
            "bring_to_back_delay_s": bring_to_back_delay_s,
            "rewind_seconds": rewind_seconds,
            "forward_seconds": forward_seconds,
            "hotkey_move_up": hotkey_up,
            "hotkey_move_down": hotkey_down,
            "hotkey_move_left": hotkey_left,
            "hotkey_move_right": hotkey_right,
            "hotkey_preload": hotkey_preload,
            "hotkey_play": hotkey_play,
            "hotkey_search": hotkey_search,
            "hotkey_rewind": hotkey_rewind,
            "hotkey_forward": hotkey_forward,
            "youtube_search_template": youtube_search_template,
            "enable_logging": enable_logging,
            "shazam_input_device": shazam_input_device,
            "shazam_language": shazam_language,
            "shazam_endpoint_country": shazam_endpoint_country,
            "shazam_recording_seconds": shazam_recording_seconds,
            "player_track_info_position": self._get_track_info_position_value(),
            "midi_port_name": midi_port_name,
            "midi_move_up": midi_move_up,
            "midi_move_down": midi_move_down,
            "midi_move_left": midi_move_left,
            "midi_move_right": midi_move_right,
            "midi_preload": midi_preload,
            "midi_play": midi_play,
            "midi_search": midi_search,
            "midi_rewind": midi_rewind,
            "midi_forward": midi_forward
        })
        
        # 設定画面を閉じる際に再登録
        self._restore_hotkeys()
        self._restore_midi_config()
        
        super().accept()
    
    def reject(self):
        """キャンセルボタンが押された時の処理"""
        # 設定画面を閉じる際に再登録
        self._restore_hotkeys()
        self._restore_midi_config()
        print("SettingsDialog: Restored hotkeys and MIDI on cancel")
        super().reject()
        
    def _restore_midi_config(self):
        try:
            device_name = self.config_service.get("midi_port_name", "")
            mappings = {}
            def add_map(action, key):
                val = int(self.config_service.get(key, -1))
                if val >= 0: mappings[val] = action
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
        except Exception as e:
            print(f"Error restoring midi data: {e}")
            
    def _restore_hotkeys(self):
        """ホットキーを再登録する"""
        try:
            # 設定からホットキーを取得して再登録
            hotkey_up = self.config_service.get("hotkey_move_up", "ctrl+shift+up")
            hotkey_down = self.config_service.get("hotkey_move_down", "ctrl+shift+down")
            hotkey_left = self.config_service.get("hotkey_move_left", "ctrl+shift+left")
            hotkey_right = self.config_service.get("hotkey_move_right", "ctrl+shift+right")
            hotkey_preload = self.config_service.get("hotkey_preload", "ctrl+enter")
            hotkey_play = self.config_service.get("hotkey_play", "shift+enter")
            hotkey_search = self.config_service.get("hotkey_search", "ctrl+shift+enter")
            hotkey_rewind = self.config_service.get("hotkey_rewind", "ctrl+;")
            hotkey_forward = self.config_service.get("hotkey_forward", "ctrl+:")
            
            self.hotkey_service.register_hotkeys(
                hotkey_up, hotkey_down, hotkey_left, hotkey_right,
                hotkey_preload, hotkey_play, hotkey_search,
                hotkey_rewind, hotkey_forward
            )
            print("SettingsDialog: Hotkeys restored")
        except Exception as e:
            print(f"SettingsDialog: Error restoring hotkeys: {e}")
    
    def _get_track_info_position_value(self):
        """設定用の文字列に変換"""
        if self.rb_none.isChecked():
            return "none"
        elif self.rb_scroll.isChecked():
            return "scroll"
        else:
            index = self.track_info_position_combo.currentIndex()
            values = ["top-right", "top-left", "bottom-right", "bottom-left"]
            return values[index] if 0 <= index < len(values) else "top-right"

