import json
import os

class ConfigService:
    """
    アプリケーションの設定（DBパス、更新間隔など）を管理するシングルトンサービス
    """
    _instance = None
    _config_file = "config.json"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigService, cls).__new__(cls)
            cls._instance._load_default_config()
            cls._instance.load_config()
        return cls._instance

    def _load_default_config(self):
        self.config = {
            "db_path": "J:/PIONEER/Master/master.db",
            "interval_s": 10,
            "hotkey_move_up": "ctrl+shift+up",
            "hotkey_move_down": "ctrl+shift+down",
            "hotkey_move_left": "ctrl+shift+left",
            "hotkey_move_right": "ctrl+shift+right",
            "bring_to_front_on_hotkey": True,
            "always_on_top": False,
            "bring_to_back_delay_s": 3,
            "player_port": 8080,
            "youtube_api_key": "",
            "youtube_search_template": "%tracktitle% %comment%"
        }


    def load_config(self):
        """ファイルから設定を読み込む"""
        if os.path.exists(self._config_file):
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    file_config = json.load(f)
                    self.config.update(file_config)
            except Exception as e:
                print(f"ConfigService: Error loading config file: {e}")
        return self.config

    def save_config(self, new_config):
        """設定をファイルに保存する"""
        self.config.update(new_config)
        try:
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            print(f"ConfigService: Config saved to {self._config_file}")
            return True
        except Exception as e:
            print(f"ConfigService: Error saving config file: {e}")
            return False

    def get(self, key, default=None):
        """特定の設定値を取得する"""
        return self.config.get(key, default)
