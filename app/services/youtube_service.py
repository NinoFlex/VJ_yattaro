import re
import requests
from typing import Dict, Optional, List
from PySide6.QtCore import QObject, Signal, QThread
from PySide6.QtGui import QPixmap
import json
from urllib.parse import urlencode


class ThumbnailLoader(QThread):
    """サムネイルを非同期で読み込むスレッド"""
    thumbnail_loaded = Signal(str, QPixmap)  # video_id, thumbnail
    
    def __init__(self, video_id: str, thumbnail_url: str):
        super().__init__()
        self.video_id = video_id
        self.thumbnail_url = thumbnail_url
    
    def run(self):
        """サムネイルを読み込む"""
        try:
            response = requests.get(self.thumbnail_url, timeout=20)
            response.raise_for_status()
            
            # QPixmapとして読み込み
            pixmap = QPixmap()
            pixmap.loadFromData(response.content)
            
            if not pixmap.isNull():
                self.thumbnail_loaded.emit(self.video_id, pixmap)
            else:
                print(f"ThumbnailLoader: Failed to load thumbnail for {self.video_id}")
                self.thumbnail_loaded.emit(self.video_id, None)
                
        except Exception as e:
            print(f"ThumbnailLoader: Error loading thumbnail for {self.video_id}: {e}")
            self.thumbnail_loaded.emit(self.video_id, None)


class YouTubeSearchThread(QThread):
    """YouTube検索をバックグラウンドで実行するスレッド"""
    search_completed = Signal(list)
    search_error = Signal(str)
    
    def __init__(self, api_key: str, query: str):
        super().__init__()
        self.api_key = api_key
        self.query = query
    
    def run(self):
        """YouTube APIで検索を実行"""
        try:
            videos = self._search_youtube()
            self.search_completed.emit(videos)
        except Exception as e:
            self.search_error.emit(str(e))
    
    def stop_search(self):
        """検索を停止"""
        self.terminate()
        self.wait()
    
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
        
        response = requests.get(f"{base_url}?{urlencode(params)}", headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
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
            response = requests.get(f"{base_url}?{urlencode(params)}", headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if 'items' not in data:
                # エラー時はすべての動画に空のdurationを設定して返す
                for video in videos:
                    video['duration'] = ''
                return videos
            
        except Exception as e:
            print(f"Error filtering shorts: {e}")
            # エラー時はすべての動画に空のdurationを設定して返す
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
        import re
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
    thumbnail_ready = Signal(str, QPixmap)  # video_id, thumbnail
    
    def __init__(self):
        super().__init__()
        self.current_loader = None
        self.pending_videos = []
        self.loaded_video_ids = set()  # 読み込み済み動画IDを追跡
        self.is_loading = False
    
    def load_thumbnails_async(self, videos: List[Dict]):
        """複数のサムネイルを順番に非同期で読み込む"""
        # 新しい動画のみをペンディングリストに追加
        for video in videos:
            video_id = video.get('video_id')
            if video_id and video_id not in self.loaded_video_ids and 'thumbnail_url' in video:
                self.pending_videos.append(video)
        
        # 現在読み込み中でなければ開始
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
        self.current_loader.thumbnail_loaded.connect(self._on_thumbnail_loaded)
        self.current_loader.start()
    
    def _on_thumbnail_loaded(self, video_id: str, thumbnail: QPixmap):
        """サムネイル読み込み完了時のコールバック"""
        self.thumbnail_ready.emit(video_id, thumbnail)
        
        # 現在のローダーをクリーンアップ
        if self.current_loader:
            self.current_loader.deleteLater()
            self.current_loader = None
        
        # 次のサムネイルを読み込み
        self._load_next_thumbnail()
    
    def stop_all_loaders(self):
        """すべてのサムネイル読み込みスレッドを停止"""
        if self.current_loader and self.current_loader.isRunning():
            self.current_loader.terminate()
            self.current_loader.wait()
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
        self.config_service = ConfigService()
    
    def format_search_query(self, template: str, track_data: Dict[str, str]) -> str:
        """
        検索テンプレートの変数を実際のトラックデータに置換する
        
        Args:
            template: 検索テンプレート文字列（例: "%artist% %tracktitle%"）
            track_data: トラックデータを含む辞書
                       {"tracktitle": "曲名", "artist": "アーティスト名", "comment": "コメント"}
        
        Returns:
            置換された検索クエリ文字列
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
        
        # 連続するスペースを単一のスペースに変換
        result = re.sub(r'\s+', ' ', result).strip()
        
        return result
    
    def get_api_key(self) -> str:
        """設定からYouTube APIキーを取得"""
        return self.config_service.get("youtube_api_key", "")
    
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
        """YouTubeで動画を検索"""
        if not self.is_configured():
            raise Exception("YouTube API key not configured")
        
        # 検索スレッドを作成
        self.search_thread = YouTubeSearchThread(self.get_api_key(), query)
        
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
