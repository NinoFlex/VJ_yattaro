import os
import shutil
import tempfile
import logging
from pyrekordbox.db6 import Rekordbox6Database, DjmdContent, DjmdSongHistory, DjmdArtist

# pyrekordboxの警告出力を抑制
logging.getLogger('pyrekordbox').setLevel(logging.ERROR)

class RekordboxService:
    def __init__(self, db_path=None):
        from app.services.config_service import ConfigService
        config = ConfigService()
        self.db_path = db_path if db_path else config.get("db_path")
        self.db = None
        self.temp_dir = None
        self.db_dir = None
        self.db_name = None
        self.local_db_path = None
        
        # pyrekordbox の設定を自動で行う (キーなどが未設定の場合の対策)
        self._setup_pyrekordbox_config()
        self._initialize_db()

    def _setup_pyrekordbox_config(self):
        """pyrekordbox の動作に必要な最小限の設定を行う"""
        import pyrekordbox
        try:
            # 既に設定されているか確認。空、またはデフォルトすぎる場合に補完を試みる
            # Windows では通常自動でキーが拾えるはずだが、明示的な呼び出しが安全
            pass
        except Exception as e:
            print(f"RekordboxService: Warning during pyrekordbox config: {e}")

    def _safe_copy(self, src, dst):
        """
        Windows の WinError 1224 (メモリマップされたファイル) を回避するための安全なコピー
        """
        if not os.path.exists(src):
            return False
            
        try:
            # shutil.copy2 の代わりにバイナリ読み書きを使用
            with open(src, 'rb') as fsrc:
                with open(dst, 'wb') as fdst:
                    while True:
                        buf = fsrc.read(1024 * 1024) # 1MBずつ
                        if not buf:
                            break
                        fdst.write(buf)
            return True
        except Exception as e:
            print(f"RekordboxService: Safe copy failed for {src}: {e}")
            return False

    def _initialize_db(self):
        if not self.db_path or not os.path.exists(self.db_path):
            # 警告をコンソールに出力せず、静かに処理
            return

        try:
            self.temp_dir = tempfile.mkdtemp()
            # パスを正規化してWindows形式に統一
            self.db_path = os.path.normpath(self.db_path)
            self.db_dir = os.path.dirname(self.db_path)
            self.db_name = os.path.basename(self.db_path)
            
            # 初期化時は本体 (master.db) と SHM をコピー
            # WAL は get_latest_history で随時更新する
            for ext in ['', '-shm', '-wal']:
                f_name = self.db_name + ext
                src_path = os.path.normpath(os.path.join(self.db_dir, f_name))
                dst_path = os.path.normpath(os.path.join(self.temp_dir, f_name))
                if os.path.exists(src_path):
                    self._safe_copy(src_path, dst_path)
            
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

    def _close_db(self):
        """DB接続を完全に閉じ、ファイルロックを解除する"""
        if self.db:
            try:
                if self.db.session:
                    self.db.session.close()
                if hasattr(self.db, 'engine'):
                    self.db.engine.dispose()
            except Exception as e:
                print(f"RekordboxService: Error closing DB: {e}")
            finally:
                self.db = None

    def get_latest_history(self, limit=50):
        # 同期のために一度接続を閉じる (Windowsのファイルロック回避)
        self._close_db()

        # db_nameがNoneの場合は処理しない
        if not self.db_name:
            print("RekordboxService: db_name is None, cannot sync files")
            return []

        try:
            # 最新の WAL および SHM ファイルを同期
            # 本体(master.db)も更新されている可能性があるため、安全策としてチェック
            files_to_sync = ['', '-wal', '-shm']
            
            for ext in files_to_sync:
                f_name = self.db_name + ext
                src_path = os.path.normpath(os.path.join(self.db_dir, f_name))
                dst_path = os.path.normpath(os.path.join(self.temp_dir, f_name))
                
                if os.path.exists(src_path):
                    self._safe_copy(src_path, dst_path)
                elif os.path.exists(dst_path):
                    # 元のファイルが消えた場合はローカルも消す
                    try:
                        os.remove(dst_path)
                    except OSError:
                        pass

            # DB接続を再初期化
            if not self.db:
                self.db = Rekordbox6Database(self.local_db_path)

            if not self.db or not self.db.session:
                return []

            session = self.db.session
            
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
