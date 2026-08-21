import json
import os
import sys
from pathlib import Path
from typing import List, Tuple


class YouTubeApiKeyStore:
    """YouTube APIキーをEXEと同じフォルダの専用JSONに保存する。"""

    MAX_KEYS = 10
    FILE_NAME = "youtube_api_keys.json"

    def __init__(self, config_service=None):
        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).resolve().parent
        else:
            base_dir = Path(__file__).resolve().parent.parent.parent

        self.path = base_dir / self.FILE_NAME
        self.config_service = config_service
        self._migrate_legacy_key_if_needed()

    @staticmethod
    def _normalize_keys(keys) -> List[str]:
        result = []
        seen = set()
        for value in keys or []:
            key = str(value or "").strip()
            if not key or key in seen:
                continue
            result.append(key)
            seen.add(key)
            if len(result) >= YouTubeApiKeyStore.MAX_KEYS:
                break
        return result

    def load(self) -> Tuple[List[str], int]:
        if not self.path.exists():
            return [], -1

        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"YouTubeApiKeyStore: Failed to load {self.path}: {e}")
            return [], -1

        keys = self._normalize_keys(data.get("keys", []))
        if not keys:
            return [], -1

        try:
            active_index = int(data.get("active_index", 0))
        except (TypeError, ValueError):
            active_index = 0

        active_index = max(0, min(active_index, len(keys) - 1))
        return keys, active_index

    def save(self, keys, active_index=0) -> bool:
        keys = self._normalize_keys(keys)
        if keys:
            try:
                active_index = int(active_index)
            except (TypeError, ValueError):
                active_index = 0
            active_index = max(0, min(active_index, len(keys) - 1))
        else:
            active_index = -1

        data = {
            "active_index": active_index,
            "keys": keys,
        }

        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp_path, self.path)
            print(
                f"YouTubeApiKeyStore: Saved {len(keys)} key(s), "
                f"active={active_index + 1 if active_index >= 0 else 'none'} to {self.path}"
            )
            return True
        except Exception as e:
            print(f"YouTubeApiKeyStore: Failed to save {self.path}: {e}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            return False

    def get_active_key(self) -> str:
        keys, active_index = self.load()
        if not keys or active_index < 0:
            return ""
        return keys[active_index]

    def _migrate_legacy_key_if_needed(self):
        """旧config.jsonのyoutube_api_keyを初回のみ専用ファイルへ移行する。"""
        if self.path.exists() or self.config_service is None:
            return

        legacy_key = str(self.config_service.get("youtube_api_key", "") or "").strip()
        if not legacy_key:
            return

        if self.save([legacy_key], 0):
            # 移行後はAPIキーをconfig.jsonに重複保持しない。
            self.config_service.save_config({"youtube_api_key": ""})
            print("YouTubeApiKeyStore: Migrated legacy YouTube API key from config.json")
