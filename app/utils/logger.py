import atexit
import os
import queue
import sys
import threading
from datetime import datetime
from enum import IntEnum
from typing import Optional


class LogLevel(IntEnum):
    """ログレベル"""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40


class Logger:
    """Non-blocking application logger with a single background file writer."""

    MAX_LOG_BYTES = 5 * 1024 * 1024
    BACKUP_COUNT = 4
    QUEUE_MAX = 10000
    BATCH_MAX = 256

    def __init__(self, name: str = "VJ_yattaro"):
        self.name = name
        self._level = LogLevel.INFO
        self._enabled = True
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        self._redirected = False
        self._shutdown = False

        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
        else:
            from pathlib import Path

            base_dir = Path(__file__).parent.parent.parent
        self._log_file_path = os.path.join(base_dir, "vj_yattaro.log")

        self._queue = queue.Queue(maxsize=self.QUEUE_MAX)
        self._stop_token = object()
        self._dropped_count = 0
        self._dropped_lock = threading.Lock()
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="VJLogWriter",
            daemon=True,
        )
        self._writer_thread.start()

    def set_level(self, level: LogLevel):
        self._level = level

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)

    def _should_log(self, level: LogLevel) -> bool:
        return self._enabled and level >= self._level

    def _enqueue_line(self, formatted_message: str):
        if not self._enabled or self._shutdown:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {formatted_message}\n"
        try:
            self._queue.put_nowait(line)
        except queue.Full:
            # Logging must never block playback/UI. Drop the oldest queued line and keep the
            # newest diagnostic information instead.
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(line)
            except queue.Full:
                pass
            with self._dropped_lock:
                self._dropped_count += 1

    # Backwards-compatible name used by LoggerStream.
    def _log_to_file(self, formatted_message: str):
        self._enqueue_line(formatted_message)

    def _rotation_needed(self, extra_bytes: int) -> bool:
        try:
            return os.path.getsize(self._log_file_path) + extra_bytes > self.MAX_LOG_BYTES
        except OSError:
            return False

    def _rotate_files(self):
        try:
            oldest = f"{self._log_file_path}.{self.BACKUP_COUNT}"
            if os.path.exists(oldest):
                os.remove(oldest)
            for index in range(self.BACKUP_COUNT - 1, 0, -1):
                src = f"{self._log_file_path}.{index}"
                dst = f"{self._log_file_path}.{index + 1}"
                if os.path.exists(src):
                    os.replace(src, dst)
            if os.path.exists(self._log_file_path):
                os.replace(self._log_file_path, f"{self._log_file_path}.1")
        except OSError:
            # Logging failures must never affect the player.
            pass

    def _write_batch(self, batch):
        if not batch:
            return

        with self._dropped_lock:
            dropped = self._dropped_count
            self._dropped_count = 0
        if dropped:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            batch.insert(0, f"[{stamp}] Logger: dropped {dropped} queued log line(s)\n")

        payload = "".join(batch)
        encoded_size = len(payload.encode("utf-8", errors="replace"))
        try:
            if self._rotation_needed(encoded_size):
                self._rotate_files()
            with open(self._log_file_path, "a", encoding="utf-8") as log_file:
                log_file.write(payload)
        except OSError:
            pass

    def _writer_loop(self):
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._shutdown:
                    break
                continue

            if item is self._stop_token:
                self._queue.task_done()
                break

            batch = [item]
            self._queue.task_done()
            while len(batch) < self.BATCH_MAX:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is self._stop_token:
                    self._queue.task_done()
                    self._shutdown = True
                    break
                batch.append(item)
                self._queue.task_done()

            self._write_batch(batch)
            if self._shutdown:
                break

        # Final drain on normal application exit.
        remaining = []
        while len(remaining) < self.QUEUE_MAX:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not self._stop_token:
                remaining.append(item)
            self._queue.task_done()
        self._write_batch(remaining)

    def _console_write(self, formatted: str, error=False):
        stream = self._stderr if error else self._stdout
        try:
            if stream and hasattr(stream, "write"):
                stream.write(formatted + "\n")
        except Exception:
            pass

    def debug(self, message: str, prefix: Optional[str] = None):
        if self._should_log(LogLevel.DEBUG):
            formatted = f"{prefix or self.name}: {message}"
            self._console_write(formatted)
            self._enqueue_line(formatted)

    def info(self, message: str, prefix: Optional[str] = None):
        if self._should_log(LogLevel.INFO):
            formatted = f"{prefix or self.name}: {message}"
            self._console_write(formatted)
            self._enqueue_line(formatted)

    def warning(self, message: str, prefix: Optional[str] = None):
        if self._should_log(LogLevel.WARNING):
            formatted = f"{prefix or self.name}: {message}"
            self._console_write(formatted)
            self._enqueue_line(formatted)

    def error(self, message: str, prefix: Optional[str] = None):
        if self._should_log(LogLevel.ERROR):
            formatted = f"{prefix or self.name}: {message}"
            self._console_write(formatted, error=True)
            self._enqueue_line(formatted)

    def redirect_stdout(self):
        if not self._redirected:
            sys.stdout = LoggerStream(self, LogLevel.INFO)
            sys.stderr = LoggerStream(self, LogLevel.ERROR)
            self._redirected = True

    def restore_stdout(self):
        if self._redirected:
            sys.stdout = self._stdout
            sys.stderr = self._stderr
            self._redirected = False

    def shutdown(self, timeout=1.5):
        if self._shutdown:
            return
        self._shutdown = True
        self.restore_stdout()
        try:
            self._queue.put_nowait(self._stop_token)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(self._stop_token)
            except (queue.Empty, queue.Full):
                pass
        if self._writer_thread.is_alive() and threading.current_thread() is not self._writer_thread:
            self._writer_thread.join(timeout=timeout)


class LoggerStream:
    """stdout/stderr proxy that keeps a per-thread line buffer."""

    def __init__(self, logger: Logger, level: LogLevel):
        self.logger = logger
        self.level = level
        self._local = threading.local()

    @property
    def line_buffer(self) -> str:
        if not hasattr(self._local, "buffer"):
            self._local.buffer = ""
        return self._local.buffer

    @line_buffer.setter
    def line_buffer(self, value: str):
        self._local.buffer = value

    def write(self, data):
        if not data:
            return 0
        self.line_buffer += str(data)
        if "\n" in self.line_buffer:
            lines = self.line_buffer.split("\n")
            for line in lines[:-1]:
                self.logger._enqueue_line(line)
                try:
                    stream = self.logger._stderr if self.level == LogLevel.ERROR else self.logger._stdout
                    if stream and hasattr(stream, "write"):
                        stream.write(line + "\n")
                except Exception:
                    pass
            self.line_buffer = lines[-1]
        return len(str(data))

    def flush(self):
        try:
            stream = self.logger._stderr if self.level == LogLevel.ERROR else self.logger._stdout
            if stream and hasattr(stream, "flush"):
                stream.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            stream = self.logger._stderr if self.level == LogLevel.ERROR else self.logger._stdout
            return bool(stream and hasattr(stream, "isatty") and stream.isatty())
        except Exception:
            return False


_logger = Logger()
atexit.register(_logger.shutdown)


def get_logger() -> Logger:
    return _logger


def configure_logging(level: LogLevel = LogLevel.INFO, enabled: bool = True, redirect: bool = False):
    _logger.set_level(level)
    _logger.set_enabled(enabled)
    if enabled and redirect:
        _logger.redirect_stdout()
    else:
        _logger.restore_stdout()


def shutdown_logging():
    _logger.shutdown()


def debug(message: str, prefix: Optional[str] = None):
    _logger.debug(message, prefix)


def info(message: str, prefix: Optional[str] = None):
    _logger.info(message, prefix)


def warning(message: str, prefix: Optional[str] = None):
    _logger.warning(message, prefix)


def error(message: str, prefix: Optional[str] = None):
    _logger.error(message, prefix)
