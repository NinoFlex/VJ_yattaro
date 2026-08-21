from typing import List, Tuple


class YouTubeApiKeyStore:
    """YouTube APIキー一覧をconfig.json内に保存する。"""

    MAX_KEYS = 10
    CONFIG_KEY = "youtube_api_keys"

    def __init__(self, config_service=None):
        if config_service is None:
            from app.services.config_service import ConfigService
            config_service = ConfigService()
        self.config_service = config_service

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
        data = self.config_service.get(self.CONFIG_KEY, {})
        if not isinstance(data, dict):
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

        try:
            saved = bool(self.config_service.save_config({self.CONFIG_KEY: data}))
            if saved:
                print(
                    f"YouTubeApiKeyStore: Saved {len(keys)} key(s), "
                    f"active={active_index + 1 if active_index >= 0 else 'none'} to config.json"
                )
            else:
                print("YouTubeApiKeyStore: Failed to save API keys to config.json")
            return saved
        except Exception as e:
            print(f"YouTubeApiKeyStore: Failed to save API keys to config.json: {e}")
            return False

    def get_active_key(self) -> str:
        keys, active_index = self.load()
        if not keys or active_index < 0:
            return ""
        return keys[active_index]
