import os

try:
    from PySide6.QtCore import QObject, QTimer, Signal
except ImportError as e:
    print(f"HistoryWatcher: Import error: {e}")
    # フォールバックとして基本的なクラスを使用
    class QObject:
        pass
    
    try:
        from PySide6.QtCore import Signal
    except ImportError:
        print("HistoryWatcher: Signal import failed, using basic function")
        def Signal(*args):
            return lambda *args: None

try:
    from app.services.rekordbox_service import RekordboxService
except ImportError as e:
    print(f"HistoryWatcher: RekordboxService import error: {e}")
    # ダミークラスを定義
    class RekordboxService:
        def __init__(self, db_path=None):
            print("HistoryWatcher: Using dummy RekordboxService")
            self.db_path = db_path
        
        def get_latest_history(self, limit=10):
            print("HistoryWatcher: Dummy get_latest_history called")
            return []

class HistoryWatcher(QObject):
    """
    rekordboxのデータベースを定期的に監視し、更新があれば信号を出すクラス
    """
    # 更新されたデータ全件を送信する信号
    updated = Signal(list)
    # 新しい曲が検出されたことを知らせる信号 (最新の1件を送信)
    new_track_detected = Signal(tuple)

    def __init__(self, interval_ms=None):
        super().__init__()
        from app.services.config_service import ConfigService
        self.config = ConfigService()
        
        if interval_ms is None:
            interval_ms = self.config.get("interval_s", 10) * 1000
            
        self.service = RekordboxService()
        self.last_top_track = None
        
        # タイマーの設定
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_database)
        self.interval = interval_ms

    def reload_settings(self):
        """設定を再読み込みし、サービスを再初期化する"""
        print("HistoryWatcher: Reloading settings...")
        new_path = self.config.get("db_path")
        new_interval = self.config.get("interval_s", 10) * 1000
        
        # サービスを新しいパスで再生成
        if self.service:
            del self.service
        self.service = RekordboxService(new_path)
        
        # タイマー間隔の更新
        self.interval = new_interval
        if self.timer.isActive():
            self.timer.start(self.interval)
        
        # データを即座にリフレッシュ
        self.check_database()

    def start(self):
        """監視を開始する"""
        # 初回チェック
        self.check_database()
        self.timer.start(self.interval)
        print(f"HistoryWatcher: Started monitoring every {self.interval/1000}s")

    def stop(self):
        """監視を停止する"""
        self.timer.stop()
        print("HistoryWatcher: Stopped monitoring")

    def check_database(self):
        """データベースをチェックし、必要に応じて信号を発行する"""
        try:
            # データベースが存在するかチェック（警告出力を抑制）
            if not self.service.db_path or not os.path.exists(self.service.db_path):
                # 警告をコンソールに出力せず、静かに処理
                return
            
            new_history = self.service.get_latest_history(limit=10)
            
            if not new_history:
                print("HistoryWatcher: No history data found")
                return

            # 全件更新信号を発行
            self.updated.emit(new_history)

            # 新曲の検出チェック
            new_top_track = new_history[0]
            if self.last_top_track is None:
                # 初回起動時
                self.last_top_track = new_top_track
                print(f"HistoryWatcher: Initial track loaded: {new_top_track}")
            elif new_top_track != self.last_top_track:
                # 新曲が追加された場合
                print(f"HistoryWatcher: New track detected! {new_top_track}")
                self.last_top_track = new_top_track
                self.new_track_detected.emit(new_top_track)
                
        except KeyboardInterrupt:
            print("HistoryWatcher: Database check interrupted by user")
            # ユーザーによる割り込みは無視して継続
            return
        except Exception as e:
            print(f"HistoryWatcher: Error checking database: {e}")
            import traceback
            traceback.print_exc()
            
            # 重大なエラーの場合は一時停止
            if "database is locked" in str(e).lower() or "permission denied" in str(e).lower():
                print("HistoryWatcher: Database access issue, pausing monitoring...")
                self.timer.stop()
