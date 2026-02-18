import keyboard
from PySide6.QtCore import QObject, Signal

class HotkeyService(QObject):
    """
    グローバルホットキーを管理するシングルトンサービス
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
        self._registered_hotkeys = []
    
    def register_hotkeys(self, hotkey_move_up, hotkey_move_down, hotkey_move_left=None, hotkey_move_right=None, hotkey_preload=None, hotkey_play=None, hotkey_search=None):
        """
        グローバルホットキーを登録する
        
        Args:
            hotkey_move_up: 上に移動するホットキー (例: "ctrl+shift+up")
            hotkey_move_down: 下に移動するホットキー (例: "ctrl+shift+down")
            hotkey_move_left: 左に移動するホットキー (例: "ctrl+shift+left")
            hotkey_move_right: 右に移動するホットキー (例: "ctrl+shift+right")
            hotkey_preload: プリロードするホットキー (例: "ctrl+enter")
            hotkey_play: 再生するホットキー (例: "shift+enter")
            hotkey_search: 検索するホットキー (例: "ctrl+shift+enter")
        """
        # 既存のホットキーを解除
        self.unregister_all()
        
        # 少し待機して完全に解放されるのを待つ
        import time
        time.sleep(0.1)
        
        try:
            # 上に移動するホットキーを登録
            if hotkey_move_up:
                keyboard.add_hotkey(hotkey_move_up, self._on_move_up)
                self._registered_hotkeys.append(hotkey_move_up)
                print(f"HotkeyService: Registered hotkey '{hotkey_move_up}' for move up")
            
            # 下に移動するホットキーを登録
            if hotkey_move_down:
                keyboard.add_hotkey(hotkey_move_down, self._on_move_down)
                self._registered_hotkeys.append(hotkey_move_down)
                print(f"HotkeyService: Registered hotkey '{hotkey_move_down}' for move down")
            
            # 左に移動するホットキーを登録
            if hotkey_move_left:
                keyboard.add_hotkey(hotkey_move_left, self._on_move_left)
                self._registered_hotkeys.append(hotkey_move_left)
                print(f"HotkeyService: Registered hotkey '{hotkey_move_left}' for move left")
            
            # 右に移動するホットキーを登録
            if hotkey_move_right:
                keyboard.add_hotkey(hotkey_move_right, self._on_move_right)
                self._registered_hotkeys.append(hotkey_move_right)
                print(f"HotkeyService: Registered hotkey '{hotkey_move_right}' for move right")
            
            # プリロードするホットキーを登録
            if hotkey_preload:
                keyboard.add_hotkey(hotkey_preload, self._on_preload)
                self._registered_hotkeys.append(hotkey_preload)
                print(f"HotkeyService: Registered hotkey '{hotkey_preload}' for preload")
            
            # 再生するホットキーを登録
            if hotkey_play:
                keyboard.add_hotkey(hotkey_play, self._on_play)
                self._registered_hotkeys.append(hotkey_play)
                print(f"HotkeyService: Registered hotkey '{hotkey_play}' for play")
            
            # 検索するホットキーを登録
            if hotkey_search:
                keyboard.add_hotkey(hotkey_search, self._on_search)
                self._registered_hotkeys.append(hotkey_search)
                print(f"HotkeyService: Registered hotkey '{hotkey_search}' for search")
                
        except Exception as e:
            print(f"HotkeyService: Error registering hotkeys: {e}")
    
    def unregister_all(self):
        """登録されているすべてのホットキーを解除する"""
        for hotkey in self._registered_hotkeys:
            try:
                keyboard.remove_hotkey(hotkey)
                print(f"HotkeyService: Unregistered hotkey '{hotkey}'")
            except Exception as e:
                print(f"HotkeyService: Error unregistering hotkey '{hotkey}': {e}")
        self._registered_hotkeys.clear()
    
    def _on_move_up(self):
        """上に移動するホットキーが押された時の処理"""
        print("HotkeyService: Move up hotkey triggered")
        self.move_up_triggered.emit()
    
    def _on_move_down(self):
        """下に移動するホットキーが押された時の処理"""
        print("HotkeyService: Move down hotkey triggered")
        self.move_down_triggered.emit()
    
    def _on_move_left(self):
        """左に移動するホットキーが押された時の処理"""
        print("HotkeyService: Move left hotkey triggered")
        self.move_left_triggered.emit()
    
    def _on_move_right(self):
        """右に移動するホットキーが押された時の処理"""
        print("HotkeyService: Move right hotkey triggered")
        self.move_right_triggered.emit()
    
    def _on_preload(self):
        """プリロードするホットキーが押された時の処理"""
        print("HotkeyService: Preload hotkey triggered")
        self.preload_triggered.emit()
    
    def _on_play(self):
        """再生するホットキーが押された時の処理"""
        print("HotkeyService: Play hotkey triggered")
        self.play_triggered.emit()
    
    def _on_search(self):
        """検索するホットキーが押された時の処理"""
        print("HotkeyService: Search hotkey triggered")
        self.search_triggered.emit()
