"""
ログ管理ユーティリティ
パフォーマンスを考慮したログ出力制御
"""

import sys
from typing import Optional
from enum import IntEnum


class LogLevel(IntEnum):
    """ログレベル"""
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40


class Logger:
    """パフォーマンス最適化されたロガー"""
    
    def __init__(self, name: str = "VJ_yattaro"):
        self.name = name
        self._level = LogLevel.INFO  # デフォルトはINFO
        self._enabled = True
    
    def set_level(self, level: LogLevel):
        """ログレベルを設定"""
        self._level = level
    
    def set_enabled(self, enabled: bool):
        """ログ出力を有効/無効化"""
        self._enabled = enabled
    
    def _should_log(self, level: LogLevel) -> bool:
        """ログを出力すべきか判定"""
        return self._enabled and level >= self._level
    
    def debug(self, message: str, prefix: Optional[str] = None):
        """DEBUGレベルログ"""
        if self._should_log(LogLevel.DEBUG):
            prefix = prefix or self.name
            print(f"{prefix}: {message}")
    
    def info(self, message: str, prefix: Optional[str] = None):
        """INFOレベルログ"""
        if self._should_log(LogLevel.INFO):
            prefix = prefix or self.name
            print(f"{prefix}: {message}")
    
    def warning(self, message: str, prefix: Optional[str] = None):
        """WARNINGレベルログ"""
        if self._should_log(LogLevel.WARNING):
            prefix = prefix or self.name
            print(f"{prefix}: {message}")
    
    def error(self, message: str, prefix: Optional[str] = None):
        """ERRORレベルログ"""
        if self._should_log(LogLevel.ERROR):
            prefix = prefix or self.name
            print(f"{prefix}: {message}")
    
    def log(self, message: str, level: LogLevel = LogLevel.INFO, prefix: Optional[str] = None):
        """指定レベルでログ出力"""
        if self._should_log(level):
            prefix = prefix or self.name
            print(f"{prefix}: {message}")


# グローバルロガーインスタンス
_logger = Logger()

def get_logger() -> Logger:
    """グローバルロガーを取得"""
    return _logger

def configure_logging(level: LogLevel = LogLevel.INFO, enabled: bool = True):
    """ログ設定を構成"""
    _logger.set_level(level)
    _logger.set_enabled(enabled)

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

def log(message: str, level: LogLevel = LogLevel.INFO, prefix: Optional[str] = None):
    """指定レベルでログ出力"""
    _logger.log(message, level, prefix)
