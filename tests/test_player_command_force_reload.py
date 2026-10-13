"""Ensure forced PRELOAD retry survives serialization through the local HTTP bridge."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / 'app' / 'services' / 'player_http_server.py'


class PlayerCommandForceReloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt = types.ModuleType('PySide6.QtCore')
        qt.QObject = type('QObject', (), {})
        qt.Signal = lambda *args: None
        qt_package = types.ModuleType('PySide6')
        qt_package.QtCore = qt
        with patch.dict(sys.modules, {'PySide6': qt_package, 'PySide6.QtCore': qt}):
            spec = importlib.util.spec_from_file_location('_test_player_http_server', SERVER_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        cls.module = mod

    def setUp(self):
        self.module.PlayerCommandHandler.reset_state('test-session')

    def test_force_reload_survives_queue_serialization(self):
        server = self.module.PlayerHttpServer()
        command_id = server.send_command('PRELOAD', 'video-1', force_reload=True)
        self.assertTrue(command_id)
        self.assertEqual(len(self.module.PlayerCommandHandler.command_queue), 1)
        command = self.module.PlayerCommandHandler._public_command(
            self.module.PlayerCommandHandler.command_queue[0]
        )
        self.assertEqual(command['videoId'], 'video-1')
        self.assertIs(command['forceReload'], True)
        self.assertEqual(command['commandId'], command_id)

    def test_non_retry_command_does_not_gain_force_reload(self):
        server = self.module.PlayerHttpServer()
        server.send_command('PRELOAD', 'normal')
        server.send_command('PLAY', 'normal', force_reload=True)
        commands = self.module.PlayerCommandHandler.command_queue
        self.assertEqual([command['cmd'] for command in commands], ['PRELOAD', 'PLAY'])
        self.assertTrue(all('forceReload' not in cmd for cmd in commands))


if __name__ == '__main__':
    unittest.main()
