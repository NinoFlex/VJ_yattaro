import ctypes
import ctypes.wintypes
import threading
from PySide6.QtCore import QObject, Signal, QTimer, QAbstractNativeEventFilter, QCoreApplication

# Windows API Constants
WM_HOTKEY = 0x0312

# Modifiers
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

class Win32HotkeyFilter(QAbstractNativeEventFilter):
    """Windows Messagesを傍受してホットキーを処理するフィルタ"""
    def __init__(self, service):
        super().__init__()
        self.service = service

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG" or event_type == b"windows_dispatcher_MSG":
            msg = ctypes.wintypes.MSG.from_address(message.__int__())
            if msg.message == WM_HOTKEY:
                self.service._handle_hotkey(msg.wParam)
                return True, 0
        return False, 0

class HotkeyService(QObject):
    """
    グローバルホットキーを管理するシングルトンサービス (Win32ネイティブAPI版)
    IME入力時のフックドロップ問題を回避するため、RegisterHotKeyを使用します。
    """
    _instance = None
    
    # ホットキーが押された時のシグナル
    move_up_triggered = Signal()
    move_down_triggered = Signal()
    move_left_triggered = Signal()
    move_right_triggered = Signal()
    preload_triggered = Signal()
    play_triggered = Signal()
    search_triggered = Signal()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(HotkeyService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        super().__init__()
        self._initialized = True
        
        self.user32 = ctypes.windll.user32
        
        # ID -> Callback mapping
        self._hotkeys = {}
        self._next_id = 1
        
        # ホットキーフィルタをアプリケーションに登録
        self._filter = Win32HotkeyFilter(self)
        app = QCoreApplication.instance()
        if app:
            app.installNativeEventFilter(self._filter)
            
        self._last_hotkey_configs = {}
        
        print("HotkeyService: Initialized using Win32 RegisterHotKey")

    def _parse_hotkey_string(self, hotkey_str):
        """'ctrl+shift+up' のような文字列を (vk, modifiers) に変換"""
        if not hotkey_str:
            return None, 0
            
        hotkey_str = hotkey_str.lower().strip()
        
        # パース処理：'+' を区切り文字として扱うが、単体の '+' や '++' などの記号としての '+' を保護する
        if hotkey_str == '+':
            parts = ['+']
        elif '++' in hotkey_str:
            parts = hotkey_str.replace('++', '+plus').split('+')
            parts = [p if p != 'plus' else '+' for p in parts if p]
        else:
            # 文字列中の '+' をパース。ただし末尾の '+' や '+ ' などケースを考慮
            parts = [p.strip() for p in hotkey_str.split('+') if p]
        
        modifiers = 0
        vk = 0
        
        # 仮想キーコードのマッピング表 (一部)
        vk_map = {
            'backspace': 0x08, 'tab': 0x09, 'enter': 0x0D, 'shift': 0x10, 'ctrl': 0x11, 'alt': 0x12,
            'esc': 0x1B, 'space': 0x20, 'page up': 0x21, 'page down': 0x22, 'end': 0x23, 'home': 0x24,
            'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28, 'insert': 0x2D, 'delete': 0x2E,
            # テンキー優先指定（記号単体ならテンキーのコードを割り当てる）
            '*': 0x6A, '+': 0x6B, '-': 0x6D, '.': 0x6E, '/': 0x6F,
            'numpad *': 0x6A, 'numpad +': 0x6B, 'numpad -': 0x6D, 'numpad /': 0x6F, 'numpad .': 0x6E,
            'subtract': 0x6D, 'add': 0x6B, 'multiply': 0x6A, 'divide': 0x6F, 'numpad_period': 0x6E,
            # Windows/JIS配列 特殊記号
            '\\': 0xE2, '¥': 0xDC, '_': 0xE2,
            'meta': 0x5B, 'command': 0x5B, 'windows': 0x5B
        }
        # A-Z, 0-9
        for i in range(26): vk_map[chr(ord('a') + i)] = 0x41 + i
        for i in range(10): vk_map[str(i)] = 0x30 + i
        
        key_found = False
        user32 = ctypes.windll.user32
        
        for part in parts:
            if part in ('ctrl', 'control'): modifiers |= MOD_CONTROL
            elif part in ('shift',): modifiers |= MOD_SHIFT
            elif part in ('alt', 'menu'): modifiers |= MOD_ALT
            elif part in ('win', 'windows'): modifiers |= MOD_WIN
            elif part in vk_map:
                vk = vk_map[part]
                key_found = True
            elif part == '\\' or part == '¥':
                # JIS配列対応: 'ろ'のキー(0xE2)を優先
                vk = 0xE2
                key_found = True
            elif len(part) == 1:
                # 記号などの1文字が直接指定された場合はVkKeyScanWで現在のレイアウトに基づく仮想キーコードを取得
                res = user32.VkKeyScanW(ord(part))
                if res != -1:
                    vk = res & 0xFF
                    # VkKeyScanW は上位バイトに修飾キー状態を含める
                    # (1:Shift, 2:Ctrl, 4:Alt)
                    shift_state = (res >> 8) & 0xFF
                    if shift_state & 1: modifiers |= MOD_SHIFT
                    if shift_state & 2: modifiers |= MOD_CONTROL
                    if shift_state & 4: modifiers |= MOD_ALT
                    key_found = True
                 
        if not key_found:
            print(f"HotkeyService: Warning, could not parse key from {hotkey_str}")
        
        return vk, modifiers

    def _register_single(self, hotkey_str, callback):
        vk, modifiers = self._parse_hotkey_string(hotkey_str)
        if not vk:
            return False
            
        # 登録したい仮想キーのリスト（基本は1つだが、記号の場合はテンキーとメインを両方含める）
        vks_to_register = [vk]
        
        # 記号系の場合、対になるキーも自動登録対象にする
        # 0x6A-0x6F はテンキーの * + - . /
        symbol_pairs = {
            0x6A: 0xBA, # Numpad * -> Main * (JISではShift+:) ※レイアウトにより変動するが代表をセット
            0x6B: 0xBB, # Numpad + -> Main + (JISではShift+;)
            0x6D: 0xBD, # Numpad - -> Main - (JISでは-)
            0x6E: 0xBE, # Numpad . -> Main .
            0x6F: 0xBF, # Numpad / -> Main /
            # 逆方向（メイン -> テンキー）
            0xBA: 0x6A, 0xBB: 0x6B, 0xBD: 0x6D, 0xBE: 0x6E, 0xBF: 0x6F
        }
        
        if vk in symbol_pairs:
            pair_vk = symbol_pairs[vk]
            if pair_vk not in vks_to_register:
                vks_to_register.append(pair_vk)

        success = False
        for target_vk in vks_to_register:
            # 修飾キーがない場合、テンキー以外の記号キーなどは制限する
            # (タイピング中の誤爆を防ぐため)
            is_numpad = (0x60 <= target_vk <= 0x6F) # Numpad 0-9, * + - . /
            is_arrow_or_nav = (0x21 <= target_vk <= 0x28) # Arrows, PgUp, PgDn, End, Home
            is_function_key = (0x70 <= target_vk <= 0x87) # F1-F24
            
            if modifiers == 0:
                if not (is_numpad or is_arrow_or_nav or is_function_key):
                    # テンキー以外のキー（メインキーボードの記号など）で修飾キーがない場合はスキップ
                    continue

            hotkey_id = self._next_id
            self._next_id += 1
            
            # RegisterHotKey(hWnd, id, fsModifiers, vk)
            result = self.user32.RegisterHotKey(None, hotkey_id, modifiers, target_vk)
            if result:
                self._hotkeys[hotkey_id] = callback
                print(f"HotkeyService: Registered Win32 hotkey '{hotkey_str}' (ID:{hotkey_id}, VK:{hex(target_vk)}, Mod:{hex(modifiers)})")
                success = True
            else:
                err = ctypes.GetLastError()
                # 既に登録されているなどのエラーは無視して進める
                if err != 1409: # 1409 = Hotkey already registered
                    print(f"HotkeyService: Failed to register hotkey '{hotkey_str}' (VK:{hex(target_vk)}). Error code: {err}")
        
        return success

    def register_hotkeys(self, hotkey_move_up, hotkey_move_down, hotkey_move_left=None, hotkey_move_right=None, hotkey_preload=None, hotkey_play=None, hotkey_search=None):
        """グローバルホットキーを登録する"""
        
        self._last_hotkey_configs = {
            'up': hotkey_move_up,
            'down': hotkey_move_down,
            'left': hotkey_move_left,
            'right': hotkey_move_right,
            'preload': hotkey_preload,
            'play': hotkey_play,
            'search': hotkey_search
        }
        
        self.unregister_all()
        
        import time
        time.sleep(0.1)
        
        try:
            if hotkey_move_up: self._register_single(hotkey_move_up, self.move_up_triggered.emit)
            if hotkey_move_down: self._register_single(hotkey_move_down, self.move_down_triggered.emit)
            if hotkey_move_left: self._register_single(hotkey_move_left, self.move_left_triggered.emit)
            if hotkey_move_right: self._register_single(hotkey_move_right, self.move_right_triggered.emit)
            if hotkey_preload: self._register_single(hotkey_preload, self.preload_triggered.emit)
            if hotkey_play: self._register_single(hotkey_play, self.play_triggered.emit)
            if hotkey_search: self._register_single(hotkey_search, self.search_triggered.emit)
        except Exception as e:
            print(f"HotkeyService: Error registering hotkeys: {e}")

    def _handle_hotkey(self, hotkey_id):
        """フィルタから呼ばれるコールバック"""
        if hotkey_id in self._hotkeys:
            self._hotkeys[hotkey_id]()
    
    def _reregister_hotkeys(self):
        """保存された設定でホットキーを再登録"""
        if not self._last_hotkey_configs:
            return
        
        print("HotkeyService: Re-registering Win32 hotkeys...")
        self.unregister_all()
        configs = self._last_hotkey_configs
        self.register_hotkeys(
            configs['up'], configs['down'], configs['left'], configs['right'],
            configs['preload'], configs['play'], configs['search']
        )
    
    def stop(self):
        """サービスの停止処理"""
        self.unregister_all()
        app = QCoreApplication.instance()
        if app and hasattr(self, '_filter'):
            app.removeNativeEventFilter(self._filter)

    def unregister_all(self):
        """登録されているすべてのホットキーを解除する"""
        for hotkey_id in list(self._hotkeys.keys()):
            self.user32.UnregisterHotKey(None, hotkey_id)
        self._hotkeys.clear()
        print("HotkeyService: Unregistered all Win32 hotkeys")
