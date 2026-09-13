import tempfile
import unittest
from pathlib import Path

from app.utils.logger import Logger, LogLevel


class LoggerPolicyTests(unittest.TestCase):
    def test_rotation_is_about_1000_kib_with_five_backups(self):
        self.assertEqual(Logger.MAX_LOG_BYTES, 1000 * 1024)
        self.assertEqual(Logger.BACKUP_COUNT, 5)

    def test_routine_info_is_hidden_at_info_but_available_in_debug(self):
        logger = Logger.__new__(Logger)
        logger._enabled = True
        logger._shutdown = False
        logger._level = LogLevel.INFO
        routine = "UI: Player feedback received - state: playing, player: A, video: abc"
        important = "ShazamService: Confirmed seq=12 title='Song' artist='Artist'"
        self.assertFalse(logger._should_capture_stream_line(routine, LogLevel.INFO))
        self.assertTrue(logger._should_capture_stream_line(important, LogLevel.INFO))
        logger._level = LogLevel.DEBUG
        self.assertTrue(logger._should_capture_stream_line(routine, LogLevel.INFO))

    def test_error_stream_is_never_suppressed_by_routine_filter(self):
        logger = Logger.__new__(Logger)
        logger._enabled = True
        logger._shutdown = False
        logger._level = LogLevel.INFO
        self.assertTrue(logger._should_capture_stream_line(
            "PlayerHttpServer: Error sending command: boom", LogLevel.ERROR
        ))

    def test_rotation_keeps_five_generations(self):
        with tempfile.TemporaryDirectory() as td:
            logger = Logger.__new__(Logger)
            logger._log_file_path = str(Path(td) / "vj_yattaro.log")
            active = Path(logger._log_file_path)
            active.write_text("current", encoding="utf-8")
            for index in range(1, 6):
                Path(f"{active}.{index}").write_text(f"old{index}", encoding="utf-8")

            logger._rotate_files()

            self.assertEqual(Path(f"{active}.1").read_text(encoding="utf-8"), "current")
            self.assertEqual(Path(f"{active}.2").read_text(encoding="utf-8"), "old1")
            self.assertEqual(Path(f"{active}.5").read_text(encoding="utf-8"), "old4")
            self.assertFalse(Path(f"{active}.6").exists())
            self.assertFalse(active.exists())


if __name__ == "__main__":
    unittest.main()
