import re
import unicodedata
import requests
from typing import Dict, Optional, List
from PySide6.QtCore import QObject, Signal, QThread, Qt
from PySide6.QtGui import QPixmap
import json
from urllib.parse import urlencode


class ThumbnailLoader(QThread):
    """サムネイルを非同期で読み込むスレッド"""
    thumbnail_loaded = Signal(str, object)  # video_id, thumbnail (QImage)
    
    def __init__(self, video_id: str, thumbnail_url: str):
        super().__init__()
        self.video_id = video_id
        self.thumbnail_url = thumbnail_url
        self._is_aborted = False
    
    def run(self):
        """サムネイルを読み込む"""
        if self._is_aborted:
            return
            
        try:
            from PySide6.QtGui import QImage
            response = requests.get(self.thumbnail_url, timeout=20)
            response.raise_for_status()
            
            # QImageとして読み込み（スレッドセーフ）
            image = QImage()
            image.loadFromData(response.content)
            
            if self._is_aborted:
                return

            if not image.isNull():
                self.thumbnail_loaded.emit(self.video_id, image)
            else:
                print(f"ThumbnailLoader: Failed to load thumbnail for {self.video_id}")
                self.thumbnail_loaded.emit(self.video_id, None)
                
        except Exception as e:
            print(f"ThumbnailLoader: Error loading thumbnail for {self.video_id}: {e}")
            self.thumbnail_loaded.emit(self.video_id, None)


class YouTubeQuotaExceededError(Exception):
    """YouTube Data API のクォータ上限を示す内部例外。"""

    def __init__(self, reason: str = "quotaExceeded", message: str = ""):
        self.reason = reason or "quotaExceeded"
        self.message = message or "YouTube API quota exceeded"
        super().__init__(self.message)


class YouTubeSearchThread(QThread):
    """YouTube検索をバックグラウンドで実行するスレッド。"""
    search_completed = Signal(list)
    search_error = Signal(str)
    api_key_switched = Signal(int, int)  # 使用中番号(1-origin), 登録件数

    # YouTube Data API が返す代表的なクォータ/利用上限系 reason。
    QUOTA_REASONS = {
        "quotaExceeded",
        "dailyLimitExceeded",
        "dailyLimitExceededUnreg",
        "rateLimitExceeded",
        "userRateLimitExceeded",
    }

    def __init__(self, api_keys, active_index: int, query: str, api_key_store=None):
        super().__init__()
        self.api_keys = [str(key).strip() for key in (api_keys or []) if str(key).strip()]
        if self.api_keys:
            try:
                active_index = int(active_index)
            except (TypeError, ValueError):
                active_index = 0
            self.active_index = max(0, min(active_index, len(self.api_keys) - 1))
            self.api_key = self.api_keys[self.active_index]
        else:
            self.active_index = -1
            self.api_key = ""

        self.query = query
        self.api_key_store = api_key_store
        self._is_aborted = False

    def run(self):
        """YouTube APIで検索を実行。クォータ上限時は次キーへ自動切替する。"""
        if self._is_aborted:
            return

        try:
            videos = self._search_with_key_rotation()
            if self._is_aborted:
                return
            self.search_completed.emit(videos)
        except Exception as e:
            if not self._is_aborted:
                self.search_error.emit(str(e))

    def stop_search(self):
        """検索を停止"""
        self._is_aborted = True
        # terminate() は危険なので使用せず、フラグで停止させてから待機
        if self.isRunning():
            self.quit()
            self.wait(2000)  # 最大2秒待機

    def _search_with_key_rotation(self) -> List[Dict]:
        """現在キーから開始し、クォータ上限なら次キーで同じ検索を再試行する。"""
        if not self.api_keys:
            raise Exception("YouTube API key not configured")

        attempted = 0
        total = len(self.api_keys)

        while attempted < total and not self._is_aborted:
            current_no = self.active_index + 1
            try:
                return self._search_youtube()
            except YouTubeQuotaExceededError as e:
                attempted += 1
                print(
                    f"YouTubeSearchThread: API key #{current_no} reached quota "
                    f"({e.reason}); tried {attempted}/{total}"
                )

                if attempted >= total:
                    raise Exception(
                        f"YouTube API usage limit reached for all {total} configured key(s)."
                    )

                self._switch_to_next_key()

        if self._is_aborted:
            return []
        raise Exception("YouTube search failed")

    def _switch_to_next_key(self):
        """次のAPIキーへ循環切替し、使用中番号を専用ファイルへ即時保存する。"""
        if not self.api_keys:
            return

        self.active_index = (self.active_index + 1) % len(self.api_keys)
        self.api_key = self.api_keys[self.active_index]

        saved = False
        if self.api_key_store is not None:
            try:
                saved = bool(self.api_key_store.save(self.api_keys, self.active_index))
            except Exception as e:
                print(f"YouTubeSearchThread: Failed to persist active API key: {e}")

        print(
            f"YouTubeSearchThread: Switched active API key to #{self.active_index + 1}"
            + (" (saved)" if saved else "")
        )
        self.api_key_switched.emit(self.active_index + 1, len(self.api_keys))

    @classmethod
    def _is_quota_error(cls, response, data) -> tuple[bool, str, str]:
        """YouTube APIレスポンスがクォータ/利用上限系か判定する。"""
        error_obj = data.get("error", {}) if isinstance(data, dict) else {}
        message = str(error_obj.get("message", "") or "")
        reasons = []

        errors = error_obj.get("errors", []) if isinstance(error_obj, dict) else []
        if isinstance(errors, list):
            for item in errors:
                if isinstance(item, dict):
                    reason = str(item.get("reason", "") or "")
                    if reason:
                        reasons.append(reason)
                    if not message:
                        message = str(item.get("message", "") or "")

        details = error_obj.get("details", []) if isinstance(error_obj, dict) else []
        if isinstance(details, list):
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                reason = str(detail.get("reason", "") or "")
                if reason:
                    reasons.append(reason)

        for reason in reasons:
            if reason in cls.QUOTA_REASONS:
                return True, reason, message

        # reasonが欠ける実装差にも備え、403/429かつquota/limitを含む場合だけ補助判定。
        lower_message = message.lower()
        if response.status_code in (403, 429) and (
            "quota" in lower_message
            or "daily limit" in lower_message
            or "rate limit" in lower_message
        ):
            return True, reasons[0] if reasons else "quotaExceeded", message

        return False, reasons[0] if reasons else "", message

    def _request_json(self, base_url: str, params: dict, headers: dict, timeout: int = 10):
        """APIキーをログへ露出させずにYouTube APIエラーを分類する。"""
        response = requests.get(
            f"{base_url}?{urlencode(params)}",
            headers=headers,
            timeout=timeout,
        )

        try:
            data = response.json()
        except Exception:
            data = {}

        if response.ok:
            return data

        is_quota, reason, message = self._is_quota_error(response, data)
        if is_quota:
            raise YouTubeQuotaExceededError(reason=reason, message=message)

        # HTTPErrorの文字列にはURL(APIキー)が含まれる可能性があるため、独自メッセージにする。
        detail = reason or message or response.reason or "unknown error"
        raise Exception(f"YouTube API error HTTP {response.status_code}: {detail}")

    def _search_youtube(self) -> List[Dict]:
        """YouTube Data API v3で動画検索（ショート動画を除外）"""
        base_url = "https://www.googleapis.com/youtube/v3/search"

        params = {
            'part': 'snippet',
            'q': self.query,
            'type': 'video',
            'maxResults': 20,  # より多く取得してフィルタリング
            'key': self.api_key
            # videoDurationパラメータを削除してすべての動画を取得
        }

        headers = {
            'User-Agent': 'VJ_yattaro/1.0'
        }

        data = self._request_json(base_url, params, headers, timeout=10)

        if 'items' not in data:
            return []

        videos = []
        video_ids = []

        # まず検索結果から動画IDを収集
        for item in data['items']:
            video_id = item['id']['videoId']
            video_ids.append(video_id)

            snippet = item['snippet']

            # サムネイルURLを取得
            thumbnails = snippet.get('thumbnails', {})
            thumbnail_url = thumbnails.get('high', {}).get('url') or thumbnails.get('default', {}).get('url', '')

            videos.append({
                'video_id': video_id,
                'title': snippet['title'],
                'thumbnail_url': thumbnail_url,
                'description': snippet.get('description', ''),
                'url': f"https://www.youtube.com/watch?v={video_id}"
            })

        # 動画の詳細情報を取得して長さを確認
        if video_ids:
            videos = self._filter_shorts(videos, video_ids)

        return videos[:20]  # 上位20件を返す

    def _filter_shorts(self, videos: List[Dict], video_ids: List[str]) -> List[Dict]:
        """ショート動画をフィルタリング"""
        base_url = "https://www.googleapis.com/youtube/v3/videos"

        params = {
            'part': 'contentDetails',
            'id': ','.join(video_ids),
            'key': self.api_key
        }

        headers = {
            'User-Agent': 'VJ_yattaro/1.0'
        }

        try:
            data = self._request_json(base_url, params, headers, timeout=10)

            if 'items' not in data:
                # エラー時はすべての動画に空のdurationを設定して返す
                for video in videos:
                    video['duration'] = ''
                return videos

        except YouTubeQuotaExceededError:
            # 詳細取得側でクォータに達した場合も、次のAPIキーで検索全体を再試行する。
            raise
        except Exception as e:
            print(f"Error filtering shorts: {e}")
            # クォータ以外の詳細取得エラーでは検索結果自体は利用可能なのでそのまま返す。
            for video in videos:
                video['duration'] = ''
            return videos

        # 動画IDから長さ情報を作成
        duration_map = {}
        for item in data['items']:
            video_id = item['id']
            duration_str = item['contentDetails']['duration']
            duration_seconds = self._parse_duration(duration_str)
            duration_map[video_id] = duration_seconds

        # ショート動画（60秒未満）を除外
        filtered_videos = []
        for video in videos:
            video_id = video['video_id']
            duration = duration_map.get(video_id, 0)

            # 60秒以上の動画のみを含める
            if duration >= 60:
                video['duration'] = self._format_duration(duration)
                filtered_videos.append(video)

        return filtered_videos

    def _parse_duration(self, duration_str: str) -> int:
        """ISO 8601期間フォーマットを秒数に変換"""
        pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
        match = re.match(pattern, duration_str)

        if not match:
            return 0

        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)

        return hours * 3600 + minutes * 60 + seconds

    def _format_duration(self, duration_seconds: int) -> str:
        """秒数をMM:SS形式に変換"""
        minutes = duration_seconds // 60
        seconds = duration_seconds % 60
        return f"{minutes}:{seconds:02d}"


class AsyncThumbnailManager(QObject):
    """非同期サムネイル読み込みを管理するクラス（シーケンシャル版）"""
    thumbnail_ready = Signal(str, object)  # video_id, thumbnail (QImage)
    
    def __init__(self):
        super().__init__()
        self.current_loader = None
        self.pending_videos = []
        self.loaded_video_ids = set()  # 読み込み済み動画IDを追跡
        self.is_loading = False
    
    def reset(self):
        """新しい検索開始時に呼ぶ。キューと読み込み済みIDをリセットする"""
        self.pending_videos.clear()
        self.loaded_video_ids.clear()
    
    def load_thumbnails_async(self, videos: List[Dict]):
        """複数のサムネイルを順番に非同期で読み込む"""
        # 新しい動画のみをペンディングリストに追加（同一検索の2回目呼び出しを考慮してclearしない）
        for video in videos:
            video_id = video.get('video_id')
            if video_id and video_id not in self.loaded_video_ids and 'thumbnail_url' in video:
                self.pending_videos.append(video)
        
        # 現在読み込み中でなければ開始
        # 読み込み中の場合は _on_thumbnail_loaded が次の pending_videos を拾う
        if not self.is_loading and self.pending_videos:
            self._load_next_thumbnail()
    
    def _load_next_thumbnail(self):
        """次のサムネイルを読み込む"""
        if not self.pending_videos:
            self.is_loading = False
            return
        
        self.is_loading = True
        video = self.pending_videos.pop(0)  # 先頭から取得（1位から順番）
        video_id = video['video_id']
        thumbnail_url = video['thumbnail_url']
        
        self.loaded_video_ids.add(video_id)
        self.current_loader = ThumbnailLoader(video_id, thumbnail_url)
        # スレッド終了時に自動破棄されるように接続
        self.current_loader.finished.connect(self.current_loader.deleteLater)
        # スレッド間通信のため明示的に QueuedConnection を設定（メインスレッドで安全に受信）
        self.current_loader.thumbnail_loaded.connect(self._on_thumbnail_loaded, type=Qt.QueuedConnection)
        self.current_loader.start()
    
    def _on_thumbnail_loaded(self, video_id: str, thumbnail):
        """サムネイル読み込み完了時のコールバック"""
        self.thumbnail_ready.emit(video_id, thumbnail)
        
        # 参照をクリア（finishedシグナルによって自動的にdeleteLaterが実行されるため安全）
        self.current_loader = None
        
        # 次のサムネイルを読み込み
        self._load_next_thumbnail()
    
    def stop_all_loaders(self):
        """すべてのサムネイル読み込みスレッドを停止"""
        if self.current_loader and self.current_loader.isRunning():
            try:
                self.current_loader._is_aborted = True
                self.current_loader.thumbnail_loaded.disconnect()
            except:
                pass
            self.current_loader.quit()
            self.current_loader.wait(1000)
            self.current_loader.deleteLater()
        
        self.current_loader = None
        self.pending_videos.clear()
        self.loaded_video_ids.clear()
        self.is_loading = False


class YouTubeService(QObject):
    """
    YouTube APIと検索機能を管理するサービス
    """
    
    def __init__(self):
        from app.services.config_service import ConfigService
        from app.services.youtube_api_key_store import YouTubeApiKeyStore
        self.config_service = ConfigService()
        self.api_key_store = YouTubeApiKeyStore(self.config_service)
    
    @staticmethod
    def sanitize_search_query(text: str) -> str:
        """YouTube検索用に文字列を安全化する。

        表示・履歴に使う元文字列は変更せず、検索時だけ適用する。
        NFKC正規化後、Unicode上の句読点(P*)・記号(S*)を空白へ置換することで、
        全角の「！」「☆」や「♪」「★」「【】」などを含む曲名でも安定して検索できるようにする。
        """
        if not text:
            return ""

        normalized = unicodedata.normalize("NFKC", str(text))
        # B'z / C++ / C# / AC-DC のように検索語として意味を持ちやすい
        # ASCII記号は残し、それ以外の装飾記号・句読点を空白化する。
        safe_symbols = {"'", "’", "&", "+", "#", "-", ".", "_"}
        cleaned = []
        for char in normalized:
            category = unicodedata.category(char)
            if char in safe_symbols:
                cleaned.append(char)
            elif category.startswith(("P", "S")) or category in ("Cc", "Cf"):
                cleaned.append(" ")
            else:
                cleaned.append(char)

        return re.sub(r'\s+', ' ', ''.join(cleaned)).strip()

    def format_search_query(self, template: str, track_data: Dict[str, str]) -> str:
        """
        検索テンプレートの変数を実際のトラックデータに置換し、
        YouTube検索で問題になりやすい記号を除去する。
        
        Args:
            template: 検索テンプレート文字列（例: "%artist% %tracktitle%"）
            track_data: トラックデータを含む辞書
                       {"tracktitle": "曲名", "artist": "アーティスト名", "comment": "コメント"}
        
        Returns:
            検索用に正規化されたクエリ文字列
        """
        if not template:
            return ""
        
        # 利用可能な変数を定義
        variables = {
            "%tracktitle%": track_data.get("tracktitle", ""),
            "%artist%": track_data.get("artist", ""),
            "%comment%": track_data.get("comment", ""),
        }
        
        # テンプレート変数を置換
        result = template
        for var, value in variables.items():
            result = result.replace(var, value)

        return self.sanitize_search_query(result)
    
    def get_api_key(self) -> str:
        """専用APIキーファイルから現在使用中のキーを取得。"""
        return self.api_key_store.get_active_key()
    
    def get_search_template(self) -> str:
        """設定から検索テンプレートを取得"""
        return self.config_service.get("youtube_search_template", "%tracktitle% %comment%")
    
    def is_configured(self) -> bool:
        """YouTube APIが設定されているかチェック"""
        api_key = self.get_api_key()
        return bool(api_key and api_key.strip())
    
    def create_search_query_from_track(self, track_title: str, artist: str, comment: str = "") -> str:
        """
        トラック情報からYouTube検索クエリを作成
        
        Args:
            track_title: トラックタイトル
            artist: アーティスト名
            comment: コメント（オプション）
        
        Returns:
            YouTube検索クエリ
        """
        template = self.get_search_template()
        track_data = {
            "tracktitle": track_title,
            "artist": artist,
            "comment": comment
        }
        
        return self.format_search_query(template, track_data)
    
    def validate_template(self, template: str) -> tuple[bool, str]:
        """
        検索テンプレートの妥当性をチェック
        
        Args:
            template: 検証するテンプレート文字列
        
        Returns:
            (is_valid, error_message)
        """
        if not template or not template.strip():
            return False, "テンプレートが空です"
        
        # サンプルデータでテスト
        sample_data = {
            "tracktitle": "Test Song",
            "artist": "Test Artist", 
            "comment": "Test Comment"
        }
        
        try:
            result = self.format_search_query(template, sample_data)
            if not result.strip():
                return False, "置換後の検索クエリが空になります"
            return True, ""
        except Exception as e:
            return False, f"テンプレートの処理中にエラーが発生しました: {str(e)}"
    
    def search_videos(self, query: str, callback=None):
        """YouTubeで動画を検索。クォータ到達時は登録済み次キーへ自動切替する。"""
        keys, active_index = self.api_key_store.load()
        if not keys or active_index < 0:
            raise Exception("YouTube API key not configured")

        # 登録済みキー一覧と現在使用中番号を検索スレッドへ渡す。
        self.search_thread = YouTubeSearchThread(
            keys,
            active_index,
            query,
            api_key_store=self.api_key_store,
        )

        # コールバックを接続
        if callback:
            self.search_thread.search_completed.connect(callback)

        return self.search_thread
    
    def load_thumbnail(self, thumbnail_url: str) -> QPixmap:
        """サムネイル画像を読み込む"""
        try:
            response = requests.get(thumbnail_url, timeout=20)
            response.raise_for_status()
            
            # QPixmapとして読み込み
            pixmap = QPixmap()
            pixmap.loadFromData(response.content)
            
            if not pixmap.isNull():
                return pixmap
            else:
                print(f"YouTubeService: Failed to load thumbnail from {thumbnail_url}")
                return None
                
        except Exception as e:
            print(f"YouTubeService: Error loading thumbnail: {e}")
            return None
