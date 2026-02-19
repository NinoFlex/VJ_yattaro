import json
import os
import sys
from pathlib import Path

class ConfigService:
    """
    アプリケーションの設定（DBパス、更新間隔など）を管理するシングルトンサービス
    """
    _instance = None
    _config_file = "config.json"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigService, cls).__new__(cls)
            # 実行ファイルの場所に基づいてconfig.jsonのパスを設定
            if getattr(sys, 'frozen', False):
                # PyInstallerでビルドされた場合（exeと同じ階層）
                base_dir = os.path.dirname(sys.executable)
            else:
                # 開発環境の場合（app/services/からプロジェクトルートへ）
                base_dir = Path(__file__).parent.parent.parent
            cls._instance._config_file = os.path.join(base_dir, "config.json")
            
            cls._instance._load_default_config()
            cls._instance.load_config()
        return cls._instance

    def _load_default_config(self):
        # 現在のユーザー名を取得してrekordboxのデフォルトパスを生成
        username = os.getlogin()
        default_db_path = f"C:\\Users\\{username}\\AppData\\Roaming\\Pioneer\\rekordbox\\master.db"
        
        self.config = {
            "db_path": default_db_path,
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
        config_exists = os.path.exists(self._config_file)
        
        if config_exists:
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    file_config = json.load(f)
                    self.config.update(file_config)
            except Exception as e:
                print(f"ConfigService: Error loading config file: {e}")
        else:
            # config.jsonが存在しない場合はデフォルト値を保存
            self.save_config({})
            print(f"ConfigService: Created default config file: {self._config_file}")
            
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
