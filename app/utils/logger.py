import os
import sys
import threading
from datetime import datetime
from typing import Optional, List
from enum import IntEnum


class LogLevel(IntEnum):
    """ログレベル"""
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40


class Logger:
    """パフォーマンス最適化されたロガー（スレッドセーフ）"""
    
    def __init__(self, name: str = "VJ_yattaro"):
        self.name = name
        self._level = LogLevel.INFO  # デフォルトはINFO
        self._enabled = True
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        self._redirected = False
        
        # ファイル書き込みをスレッドセーフにするためのロック
        self._file_lock = threading.Lock()
        
        # ログファイルのパス（プロジェクトルート）
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            from pathlib import Path
            base_dir = Path(__file__).parent.parent.parent
        self._log_file_path = os.path.join(base_dir, "vj_yattaro.log")
    
    def set_level(self, level: LogLevel):
        """ログレベルを設定"""
        self._level = level
    
    def set_enabled(self, enabled: bool):
        """ログ出力を有効/無効化"""
        self._enabled = enabled
    
    def _should_log(self, level: LogLevel) -> bool:
        """ログを出力すべきか判定"""
        return self._enabled and level >= self._level
    
    def _log_to_file(self, formatted_message: str):
        """ファイルにログを記録（スレッドセーフ）"""
        if not self._enabled:
            return
            
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            # ロックを取得してから書き込む（複数スレッドの同時書き込みによる混入を防ぐ）
            with self._file_lock:
                with open(self._log_file_path, "a", encoding="utf-8") as f:
                    f.write(f"[{timestamp}] {formatted_message}\n")
        except:
            # ファイル書き込み失敗は無視する（無限ループ防止）
            pass

    def debug(self, message: str, prefix: Optional[str] = None):
        """DEBUGレベルログ"""
        if self._should_log(LogLevel.DEBUG):
            prefix = prefix or self.name
            formatted = f"{prefix}: {message}"
            if self._redirected:
                if self._stdout:
                    self._stdout.write(formatted + "\n")
            else:
                try:
                    print(formatted)
                except:
                    pass
            self._log_to_file(formatted)
    
    def info(self, message: str, prefix: Optional[str] = None):
        """INFOレベルログ"""
        if self._should_log(LogLevel.INFO):
            prefix = prefix or self.name
            formatted = f"{prefix}: {message}"
            if self._redirected:
                if self._stdout:
                    self._stdout.write(formatted + "\n")
            else:
                try:
                    print(formatted)
                except:
                    pass
            self._log_to_file(formatted)
    
    def warning(self, message: str, prefix: Optional[str] = None):
        """WARNINGレベルログ"""
        if self._should_log(LogLevel.WARNING):
            prefix = prefix or self.name
            formatted = f"{prefix}: {message}"
            if self._redirected:
                if self._stdout:
                    self._stdout.write(formatted + "\n")
            else:
                try:
                    print(formatted)
                except:
                    pass
            self._log_to_file(formatted)
    
    def error(self, message: str, prefix: Optional[str] = None):
        """ERRORレベルログ"""
        if self._should_log(LogLevel.ERROR):
            prefix = prefix or self.name
            formatted = f"{prefix}: {message}"
            if self._redirected:
                if self._stderr:
                    self._stderr.write(formatted + "\n")
            else:
                try:
                    print(formatted, file=sys.stderr)
                except:
                    pass
            self._log_to_file(formatted)

    def redirect_stdout(self):
        """標準出力をロガーにリダイレクト"""
        if not self._redirected:
            sys.stdout = LoggerStream(self, LogLevel.INFO)
            sys.stderr = LoggerStream(self, LogLevel.ERROR)
            self._redirected = True

    def restore_stdout(self):
        """標準出力を元に戻す"""
        if self._redirected:
            sys.stdout = self._stdout
            sys.stderr = self._stderr
            self._redirected = False


class LoggerStream:
    """sys.stdout/stderr をロガーにリダイレクトするためのストリームクラス（スレッドセーフ）
    
    line_buffer を threading.local にすることで、複数スレッドが同時に write() しても
    各スレッド固有のバッファを使用し、行データの混入（race condition）を防ぐ。
    """
    def __init__(self, logger: Logger, level: LogLevel):
        self.logger = logger
        self.level = level
        # スレッドごとに独立したバッファを持つ（他スレッドのバッファに干渉しない）
        self._local = threading.local()

    @property
    def line_buffer(self) -> str:
        """現在のスレッド固有のバッファを返す"""
        if not hasattr(self._local, 'buffer'):
            self._local.buffer = ""
        return self._local.buffer

    @line_buffer.setter
    def line_buffer(self, value: str):
        """現在のスレッド固有のバッファに書き込む"""
        self._local.buffer = value

    def write(self, data):
        if not data:
            return
        
        # self.line_buffer はスレッドローカルなので他スレッドと干渉しない
        self.line_buffer += data
        if "\n" in self.line_buffer:
            lines = self.line_buffer.split("\n")
            for line in lines[:-1]:
                # 空行やインデントされた行も重要なのでそのまま記録する
                self.logger._log_to_file(line)
                
                # 元のストリームにも出力（デバッグ用などに元のコンソールが生きている場合）
                try:
                    if self.level == LogLevel.ERROR:
                        if self.logger._stderr and hasattr(self.logger._stderr, 'write'):
                            self.logger._stderr.write(line + "\n")
                    else:
                        if self.logger._stdout and hasattr(self.logger._stdout, 'write'):
                            self.logger._stdout.write(line + "\n")
                except:
                    pass
            self.line_buffer = lines[-1]

    def flush(self):
        try:
            if self.level == LogLevel.ERROR:
                if self.logger._stderr and hasattr(self.logger._stderr, 'flush'):
                    self.logger._stderr.flush()
            else:
                if self.logger._stdout and hasattr(self.logger._stdout, 'flush'):
                    self.logger._stdout.flush()
        except:
            pass


# グローバルロガーインスタンス
_logger = Logger()

def get_logger() -> Logger:
    """グローバルロガーを取得"""
    return _logger

def configure_logging(level: LogLevel = LogLevel.INFO, enabled: bool = True, redirect: bool = False):
    """ログ設定を構成"""
    _logger.set_level(level)
    _logger.set_enabled(enabled)
    if enabled and redirect:
        _logger.redirect_stdout()
    else:
        _logger.restore_stdout()

# 便利関数
def debug(message: str, prefix: Optional[str] = None):
    """DEBUGログ出力"""
    _logger.debug(message, prefix)

def info(message: str, prefix: Optional[str] = None):
    """INFOログ出力"""
    _logger.info(message, prefix)

def warning(message: str, prefix: Optional[str] = None):
    """WARNINGログ出力"""
    _logger.warning(message, prefix)

def error(message: str, prefix: Optional[str] = None):
    """ERRORログ出力"""
    _logger.error(message, prefix)
