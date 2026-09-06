import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class _DummyQObject:
    pass


class _DummyQThread:
    pass


class _DummySignal:
    def __init__(self, *args, **kwargs):
        pass


class _DummyQt:
    KeepAspectRatio = 0
    SmoothTransformation = 0


class _DummyPixmap:
    pass


qtcore = types.ModuleType('PySide6.QtCore')
qtcore.QObject = _DummyQObject
qtcore.Signal = _DummySignal
qtcore.QThread = _DummyQThread
qtcore.Qt = _DummyQt
qtgui = types.ModuleType('PySide6.QtGui')
qtgui.QPixmap = _DummyPixmap
pyside = types.ModuleType('PySide6')
pyside.QtCore = qtcore
pyside.QtGui = qtgui

with patch.dict(sys.modules, {
    'PySide6': pyside,
    'PySide6.QtCore': qtcore,
    'PySide6.QtGui': qtgui,
}):
    spec = importlib.util.spec_from_file_location(
        '_youtube_service_under_test', ROOT / 'app/services/youtube_service.py'
    )
    youtube_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(youtube_module)

YouTubeService = youtube_module.YouTubeService


class _Config:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)


class SearchTemplateTests(unittest.TestCase):
    def _service(self, values):
        service = object.__new__(YouTubeService)
        service.config_service = _Config(values)
        return service

    def test_default_templates_are_mode_specific(self):
        service = self._service({})
        self.assertEqual(service.get_search_template('rekordbox'), '%tracktitle% %comment%')
        self.assertEqual(service.get_search_template('shazam'), '%tracktitle% %artist%')

    def test_rekordbox_and_shazam_build_different_queries(self):
        service = self._service({
            'youtube_search_template_rekordbox': '%tracktitle% %comment%',
            'youtube_search_template_shazam': '%tracktitle% %artist%',
        })
        self.assertEqual(
            service.create_search_query_from_track(
                'Song A', 'Artist A', 'DJ edit', source_mode='rekordbox'
            ),
            'Song A DJ edit',
        )
        self.assertEqual(
            service.create_search_query_from_track(
                'Song A', 'Artist A', 'DJ edit', source_mode='shazam'
            ),
            'Song A Artist A',
        )

    def test_legacy_template_remains_rekordbox_fallback(self):
        service = self._service({'youtube_search_template': '%artist% %tracktitle%'})
        self.assertEqual(service.get_search_template('rekordbox'), '%artist% %tracktitle%')
        self.assertEqual(service.get_search_template('shazam'), '%tracktitle% %artist%')

    def test_custom_templates_do_not_cross_modes(self):
        service = self._service({
            'youtube_search_template_rekordbox': 'RB %tracktitle%',
            'youtube_search_template_shazam': 'SZ %artist% %tracktitle%',
        })
        self.assertEqual(
            service.create_search_query_from_track('Tune', 'Singer', source_mode='rekordbox'),
            'RB Tune',
        )
        self.assertEqual(
            service.create_search_query_from_track('Tune', 'Singer', source_mode='shazam'),
            'SZ Singer Tune',
        )

    def test_settings_and_queue_keep_separate_mode_keys(self):
        settings = (ROOT / 'ui/dialogs/settings_dialog.py').read_text(encoding='utf-8')
        main = (ROOT / 'main.py').read_text(encoding='utf-8')
        config = (ROOT / 'app/services/config_service.py').read_text(encoding='utf-8')
        self.assertIn('youtube_search_template_rekordbox_edit', settings)
        self.assertIn('youtube_search_template_shazam_edit', settings)
        self.assertIn('"youtube_search_template_shazam": "%tracktitle% %artist%"', config)
        self.assertIn('allow_auto_play, search_source_mode', main)
        self.assertIn('source_mode=search_source_mode', main)


if __name__ == '__main__':
    unittest.main()
