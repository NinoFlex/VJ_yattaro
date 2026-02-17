from PySide6.QtCore import QObject, QTimer, Signal
from app.services.rekordbox_service import RekordboxService

class HistoryWatcher(QObject):
    """
    rekordboxのデータベースを定期的に監視し、更新があれば信号を出すクラス
    """
    # 更新されたデータ全件を送信する信号
    updated = Signal(list)
    # 新しい曲が検出されたことを知らせる信号 (最新の1件を送信)
    new_track_detected = Signal(tuple)

    def __init__(self, interval_ms=10000):
        super().__init__()
        self.service = RekordboxService()
        self.last_top_track = None
        
        # タイマーの設定
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_database)
        self.interval = interval_ms

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
        print("HistoryWatcher: Checking rekordbox DB for updates...")
        new_history = self.service.get_latest_history(limit=10)
        
        if not new_history:
            return

        # 全件更新信号を発行
        self.updated.emit(new_history)

        # 新曲の検出チェック
        new_top_track = new_history[0]
        if self.last_top_track is None:
            # 初回起動時
            self.last_top_track = new_top_track
        elif new_top_track != self.last_top_track:
            # 新曲が追加された場合
            print(f"HistoryWatcher: New track detected! {new_top_track}")
            self.last_top_track = new_top_track
            self.new_track_detected.emit(new_top_track)
