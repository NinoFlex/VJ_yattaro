import os
import shutil
import tempfile
from pyrekordbox.db6 import Rekordbox6Database, DjmdContent, DjmdSongHistory, DjmdArtist

class RekordboxService:
    def __init__(self, db_path="/mnt/j/PIONEER/Master/master.db"):
        self.db_path = db_path
        self.db = None
        self.temp_dir = None
        self.db_dir = None
        self.db_name = None
        self.local_db_path = None
        self._initialize_db()

    def _initialize_db(self):
        try:
            self.temp_dir = tempfile.mkdtemp()
            self.db_dir = os.path.dirname(self.db_path)
            self.db_name = os.path.basename(self.db_path)
            
            # 初期化時は本体 (master.db) と SHM をコピー
            # WAL は get_latest_history で随時更新する
            for ext in ['', '-shm']:
                f_name = self.db_name + ext
                src_path = os.path.join(self.db_dir, f_name)
                if os.path.exists(src_path):
                    shutil.copy2(src_path, os.path.join(self.temp_dir, f_name))
            
            self.local_db_path = os.path.join(self.temp_dir, self.db_name)
            
            # Rekordbox6Database をローカルコピーに対して初期化
            self.db = Rekordbox6Database(self.local_db_path)
        except Exception as e:
            print(f"Error initializing Rekordbox database: {e}")

    def __del__(self):
        # 終了時に一時ディレクトリを削除
        try:
            if self.temp_dir and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
        except:
            pass

    def get_latest_history(self, limit=10):
        if not self.db or not self.db.session:
            return []

        try:
            # 最新の WAL および SHM ファイルを同期
            for ext in ['-wal', '-shm']:
                f_name = self.db_name + ext
                src_path = os.path.join(self.db_dir, f_name)
                dst_path = os.path.join(self.temp_dir, f_name)
                
                if os.path.exists(src_path):
                    shutil.copy2(src_path, dst_path)
                elif os.path.exists(dst_path):
                    # 元のファイルが消えた場合はローカルも消す
                    os.remove(dst_path)

            session = self.db.session
            # セッションのキャッシュをクリアして最新状態を反映させる
            session.expire_all()
            
            # 最新の履歴を取得
            # DjmdSongHistory: 演奏履歴
            # DjmdContent: 曲の詳細
            # DjmdArtist: アーティスト名
            query = (
                session.query(DjmdContent.Title, DjmdArtist.Name, DjmdContent.Commnt, DjmdSongHistory.created_at)
                .join(DjmdSongHistory, DjmdSongHistory.ContentID == DjmdContent.ID)
                .join(DjmdArtist, DjmdContent.ArtistID == DjmdArtist.ID)
                .order_by(DjmdSongHistory.created_at.desc())
                .limit(limit)
            )
            
            results = query.all()
            # テーブルに渡しやすい形式 (Title, Artist, Comment) に変換
            return [(r[0], r[1], r[2] if r[2] else "") for r in results]
            
        except Exception as e:
            print(f"Error fetching history: {e}")
            import traceback
            traceback.print_exc()
            return []
