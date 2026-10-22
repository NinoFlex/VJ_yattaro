"""New-install defaults and number-only Shazam settings (no actual Qt window)."""
import ast
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from app.services.config_service import ConfigService, SHAZAM_DEFAULT_LANES, normalize_shazam_lane_count
from tests.test_webview_integration import ShazamService

ROOT = Path(__file__).resolve().parents[1]


class DefaultLaneConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = object.__new__(ConfigService)
        self.config._config_file = str(Path(self.tmp.name) / 'config.json')
        self.config._load_default_config()

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_is_six_but_minimum_stays_four(self):
        self.assertEqual(SHAZAM_DEFAULT_LANES, 6)
        self.assertEqual(self.config.get('shazam_lane_count'), 6)
        self.assertEqual(normalize_shazam_lane_count(3), 4)
        self.assertEqual(normalize_shazam_lane_count(17), 16)

    def test_new_install_saves_six(self):
        self.config.load_config()
        saved = json.loads(Path(self.config._config_file).read_text(encoding='utf-8'))
        self.assertEqual(saved['shazam_lane_count'], 6)

    def test_old_configuration_without_key_uses_six(self):
        Path(self.config._config_file).write_text(json.dumps({'youtube_search_template_rekordbox': '%tracktitle%'}), encoding='utf-8')
        self.config.load_config()
        self.assertEqual(self.config.get('shazam_lane_count'), 6)

    def test_explicit_saved_four_eight_sixteen_are_not_overwritten(self):
        for count in (4, 8, 16):
            with self.subTest(count=count):
                Path(self.config._config_file).write_text(json.dumps({
                    'shazam_lane_count': count, 'youtube_search_template_rekordbox': '%tracktitle%'
                }), encoding='utf-8')
                self.config._load_default_config()
                self.config.load_config()
                self.assertEqual(self.config.get('shazam_lane_count'), count)
                self.assertEqual(json.loads(Path(self.config._config_file).read_text())['shazam_lane_count'], count)

    def test_invalid_non_numeric_value_uses_six(self):
        for value in (None, 'broken', '', float('inf'), float('nan'), {}):
            with self.subTest(value=value):
                self.assertEqual(normalize_shazam_lane_count(value), 6)

    def test_six_persists_across_load(self):
        self.config.save_config({'shazam_lane_count': 6})
        self.config.config['shazam_lane_count'] = 16
        self.config.load_config()
        self.assertEqual(self.config.get('shazam_lane_count'), 6)


class DefaultLaneServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.values = {}
        config = types.SimpleNamespace(get=lambda key, default=None: self.values.get(key, default))
        with patch('app.services.config_service.ConfigService', return_value=config), \
             patch.object(ShazamService, '_get_history_path', return_value=Path(self.tmp.name) / 'history.json'):
            self.service = ShazamService()

    def tearDown(self):
        self.service.shutdown()
        self.tmp.cleanup()

    def test_unconfigured_service_has_six_workers(self):
        self.assertEqual(self.service.RECOGNITION_GROUPS, 6)
        self.assertEqual(self.service.PARALLEL_RECOGNITION_LANES, 6)
        self.assertEqual(self.service._pool_capacity, 6)
        self.assertEqual(len(self.service._web_recognizers), 6)

    def test_sync_missing_key_returns_to_six(self):
        self.values['shazam_lane_count'] = 4
        self.service._sync_lane_count()
        self.assertEqual(self.service.RECOGNITION_GROUPS, 4)
        del self.values['shazam_lane_count']
        self.service._sync_lane_count()
        self.assertEqual(self.service.RECOGNITION_GROUPS, 6)

    def test_sixth_ready_worker_can_be_selected(self):
        for event in self.service._lane_ready:
            event.set()
        self.service._lane_busy[:] = [True] * 5 + [False]
        self.service._group_busy[:] = [True] * 5 + [False]
        self.assertEqual(self.service._find_available_group(), 5)

    def test_six_busy_workers_make_slot_wait(self):
        for event in self.service._lane_ready:
            event.set()
        self.service._lane_busy[:] = [True] * 6
        self.service._group_busy[:] = [True] * 6
        self.assertIsNone(self.service._find_available_group())

    def test_staged_startup_and_confirmation_constants_unchanged(self):
        self.assertEqual(self.service.LANE_INIT_MAX_CONCURRENT, 2)
        self.assertEqual(self.service.LANE_INIT_INTERVAL_SECONDS, 0.5)
        self.assertEqual(self.service.GROUP_STAGGER_SECONDS, 3.0)
        self.assertEqual(self.service.SHAZAM_NO_MATCH_WINDOW_SECONDS, 15.0)
        self.assertEqual(self.service.SHAZAM_NO_MATCH_MAX_GAPS, 2)


class LaneSettingsPresentationTests(unittest.TestCase):
    def test_actual_tab_builder_uses_label_and_no_suffix(self):
        source = (ROOT / 'ui/dialogs/settings_dialog.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_init_shazam_tab')
        scope = {name: MagicMock() for name in ('QWidget','QFormLayout','QComboBox','QPushButton','QHBoxLayout','QLabel','QSpinBox')}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(ROOT / 'ui/dialogs/settings_dialog.py'), 'exec'), scope)
        window = MagicMock()
        scope['_init_shazam_tab'](window)
        spin = scope['QSpinBox'].return_value
        spin.setRange.assert_called_once_with(4, 16)
        spin.setSingleStep.assert_called_once_with(1)
        spin.setSuffix.assert_not_called()
        spin.setPrefix.assert_not_called()
        scope['QFormLayout'].return_value.addRow.assert_any_call('\u30ec\u30fc\u30f3\u6570 :', spin)

    def test_actual_lane_load_expression_uses_shared_default_and_saved_value(self):
        source = (ROOT / 'ui/dialogs/settings_dialog.py').read_text(encoding='utf-8')
        function = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == '_load_current_settings')
        stmt = next(n for n in function.body if isinstance(n, ast.Expr) and
                    isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and
                    isinstance(n.value.func.value, ast.Attribute) and n.value.func.value.attr == 'shazam_lane_count_spin')
        code = compile(ast.Module(body=[stmt], type_ignores=[]), 'settings_lane_load', 'exec')
        for values, expected in (({},6), ({'shazam_lane_count':4},4), ({'shazam_lane_count':16},16), ({'shazam_lane_count':None},6)):
            with self.subTest(values=values):
                window = MagicMock()
                window.config_service.get.side_effect = lambda key, default=None: values.get(key,default)
                exec(code, {'self': window, 'SHAZAM_DEFAULT_LANES': SHAZAM_DEFAULT_LANES,
                            'normalize_shazam_lane_count': normalize_shazam_lane_count})
                window.shazam_lane_count_spin.setValue.assert_called_once_with(expected)


if __name__ == '__main__':
    unittest.main()
