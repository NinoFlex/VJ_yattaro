import os
import threading

from PySide6.QtCore import QObject, QThread, Signal

from app.services.config_service import ConfigService
from app.services.rekordbox_service import RekordboxService


class HistoryWorker(QThread):
    """Run all Rekordbox file copying/query work outside the GUI thread."""

    history_ready = Signal(list)
    worker_error = Signal(str)
    status_ready = Signal(str, str)

    def __init__(self, db_path=None, interval_ms=10000, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._db_path = db_path
        self._interval_ms = max(500, int(interval_ms or 10000))
        self._enabled = False
        self._force_check = True
        self._last_status = (None, None)

    def _emit_status(self, state, detail):
        state = str(state or "off").lower()
        detail = str(detail or "")
        status = (state, detail)
        if status == self._last_status:
            return
        self._last_status = status
        self.status_ready.emit(state, detail)

    def update_settings(self, db_path, interval_ms):
        with self._lock:
            self._db_path = db_path
            self._interval_ms = max(500, int(interval_ms or 10000))
            self._force_check = True
        self._wake_event.set()

    def set_enabled(self, enabled, force_check=False):
        with self._lock:
            self._enabled = bool(enabled)
            if force_check:
                self._force_check = True
        self._wake_event.set()

    def request_check(self):
        with self._lock:
            self._force_check = True
        self._wake_event.set()

    def request_stop(self):
        self._stop_event.set()
        self._wake_event.set()
        self.requestInterruption()

    @staticmethod
    def _source_signature(db_path):
        """Return a cheap signature for master.db + WAL/SHM without reading contents."""
        if not db_path:
            return None
        normalized = os.path.normpath(str(db_path))
        directory = os.path.dirname(normalized)
        base = os.path.basename(normalized)
        signature = []
        any_file = False
        for suffix in ("", "-wal", "-shm"):
            path = os.path.join(directory, base + suffix)
            try:
                stat = os.stat(path)
                signature.append((suffix, int(stat.st_size), int(stat.st_mtime_ns)))
                any_file = True
            except FileNotFoundError:
                signature.append((suffix, None, None))
            except OSError:
                # Treat stat failures as a change so the service can retry safely.
                signature.append((suffix, "error", "error"))
        return tuple(signature) if any_file else None

    def run(self):
        service = None
        service_path = None
        last_signature = None

        while not self._stop_event.is_set() and not self.isInterruptionRequested():
            with self._lock:
                db_path = self._db_path
                interval_ms = self._interval_ms
                enabled = self._enabled
                force_check = self._force_check
                self._force_check = False

            if not enabled:
                self._emit_status("off", "Rekordbox DB: monitoring paused")
                self._wake_event.wait(1.0)
                self._wake_event.clear()
                continue

            try:
                normalized_path = os.path.normpath(str(db_path)) if db_path else None
                if normalized_path != service_path:
                    self._emit_status(
                        "warn",
                        f"Rekordbox DB: initializing\nPath: {normalized_path or '(not configured)'}",
                    )
                    # Construct RekordboxService in this worker thread. Its initial DB copy can be
                    # relatively expensive, so it must never happen on the Qt GUI thread.
                    service = RekordboxService(normalized_path)
                    service_path = normalized_path
                    last_signature = None
                    force_check = True

                signature = self._source_signature(normalized_path)
                if signature is None:
                    self._emit_status(
                        "error",
                        f"Rekordbox DB: source database not found\nPath: {normalized_path or '(not configured)'}",
                    )
                if signature is not None and service is not None and not getattr(service, "db_name", None):
                    # The DB may have appeared after startup (e.g. Rekordbox launched later).
                    service = RekordboxService(normalized_path)
                    last_signature = None
                    force_check = True
                if signature is not None and service is not None and not getattr(service, "db_name", None):
                    self._emit_status(
                        "error",
                        f"Rekordbox DB: failed to open database\nPath: {normalized_path or '(not configured)'}",
                    )
                if signature is not None and (force_check or signature != last_signature):
                    history = service.get_latest_history(limit=10) if service else []
                    # RekordboxService marks whether the query itself succeeded. Empty history is
                    # valid; transient copy/query failures are retried on the next interval.
                    if service is not None and getattr(service, "last_query_succeeded", False):
                        with self._lock:
                            current_path = (
                                os.path.normpath(str(self._db_path)) if self._db_path else None
                            )
                            still_enabled = self._enabled
                        if still_enabled and current_path == normalized_path:
                            last_signature = signature
                            self._emit_status(
                                "ok",
                                f"Rekordbox DB: normal\nPath: {normalized_path}\nHistory rows: {len(history or [])}",
                            )
                            self.history_ready.emit(list(history or []))
                    elif service is not None:
                        self._emit_status(
                            "error",
                            f"Rekordbox DB: query failed\nPath: {normalized_path}",
                        )
                elif signature is not None and service is not None and getattr(service, "last_query_succeeded", False):
                    self._emit_status(
                        "ok",
                        f"Rekordbox DB: normal\nPath: {normalized_path}\nNo file changes since last check",
                    )
            except Exception as exc:
                self._emit_status(
                    "error",
                    f"Rekordbox DB: worker error\n{exc}",
                )
                self.worker_error.emit(str(exc))

            self._wake_event.wait(max(0.5, interval_ms / 1000.0))
            self._wake_event.clear()

        # Release pyrekordbox handles/temp files in the worker thread.
        if service is not None:
            try:
                service._close_db()
            except Exception:
                pass
        service = None
        self._emit_status("off", "Rekordbox DB: monitoring stopped")


class HistoryWatcher(QObject):
    """Asynchronous Rekordbox history watcher exposed to the MainWindow."""

    updated = Signal(list)
    new_track_detected = Signal(tuple)
    status_changed = Signal(str, str)

    def __init__(self, interval_ms=None, parent=None):
        super().__init__(parent)
        self.config = ConfigService()
        if interval_ms is None:
            interval_ms = self.config.get("interval_s", 10) * 1000
        self.interval = max(500, int(interval_ms))
        self.last_top_track = None
        self._started = False

        self.worker = HistoryWorker(
            self.config.get("db_path"),
            self.interval,
            self,
        )
        self.worker.history_ready.connect(self._handle_history)
        self.worker.status_ready.connect(self._relay_status)
        self.worker.worker_error.connect(
            lambda message: print(f"HistoryWatcher: Worker error: {message}")
        )
        self.worker.start()

    def _relay_status(self, state, detail):
        self.status_changed.emit(state, detail)

    def _handle_history(self, new_history):
        if not self._started:
            return

        self.updated.emit(new_history)
        if not new_history:
            return

        new_top_track = tuple(new_history[0])
        if self.last_top_track is None:
            self.last_top_track = new_top_track
            print(f"HistoryWatcher: Initial track loaded: {new_top_track}")
        elif new_top_track != self.last_top_track:
            self.last_top_track = new_top_track
            print(f"HistoryWatcher: New track detected! {new_top_track}")
            self.new_track_detected.emit(new_top_track)

    def reload_settings(self):
        """Apply settings without doing any DB I/O on the GUI thread."""
        new_path = self.config.get("db_path")
        self.interval = max(500, int(self.config.get("interval_s", 10) * 1000))
        self.last_top_track = None
        self.worker.update_settings(new_path, self.interval)
        if self._started:
            self.worker.set_enabled(True, force_check=True)
        print(
            f"HistoryWatcher: Settings reloaded (interval={self.interval / 1000:.1f}s)"
        )

    def start(self):
        """Resume monitoring. The actual check runs in HistoryWorker."""
        if self._started:
            self.worker.request_check()
            return
        self._started = True
        self.status_changed.emit("warn", "Rekordbox DB: checking database...")
        self.worker.set_enabled(True, force_check=True)
        print(f"HistoryWatcher: Started monitoring every {self.interval / 1000:.1f}s")

    def stop(self):
        """Pause monitoring while keeping the worker alive for fast source-mode switching."""
        self._started = False
        self.worker.set_enabled(False)
        self.status_changed.emit("off", "Rekordbox DB: monitoring paused (Shazam mode)")
        print("HistoryWatcher: Paused monitoring")

    def check_database(self):
        """Compatibility API: request an asynchronous check."""
        if self._started:
            self.worker.request_check()

    def shutdown(self, timeout_ms=3000):
        """Stop the worker thread during application shutdown."""
        self._started = False
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            if not self.worker.wait(timeout_ms):
                print("HistoryWatcher: Worker shutdown timed out")
        print("HistoryWatcher: Stopped monitoring")
