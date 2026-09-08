"""Offline selection/UI-wiring tests using the actual main.py method bodies.

Only widgets, timers and communication are fakes; no GUI, browser or API is used.
"""
import ast
from copy import deepcopy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.autoplay import _duration_seconds, select_auto_play_index

SOURCE = (ROOT / 'main.py').read_text(encoding='utf-8')
TREE = ast.parse(SOURCE)


def _logic_class(class_name, names, namespace=None):
    """Load real methods while avoiding Windows/Qt application startup."""
    cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in methods} == set(names)
    scope = dict(namespace or {})
    exec(compile(ast.Module(body=methods, type_ignores=[]), 'main.py', 'exec'), scope)
    return type(class_name + 'Logic', (), {n: scope[n] for n in names})


class _Signal:
    def __init__(self):
        self.handlers = []

    def connect(self, handler):
        self.handlers.append(handler)

    def emit(self, value):
        for handler in self.handlers:
            handler(value)


class _Button:
    def __init__(self):
        self.checked = False
        self.enabled = True
        self.blocked = False
        self.toggled = _Signal()
        self.text = self.tooltip = ''

    def blockSignals(self, value):
        previous, self.blocked = self.blocked, value
        return previous

    def setChecked(self, value):
        changed = value != self.checked
        self.checked = value
        if changed and not self.blocked:
            self.toggled.emit(value)

    def setEnabled(self, value):
        self.enabled = value

    def setText(self, value):
        self.text = value

    def setToolTip(self, value):
        self.tooltip = value

    def click(self):
        if self.enabled:
            self.setChecked(not self.checked)


class _Config:
    def __init__(self, values):
        self.values = dict(values)
        self.saved = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def save_config(self, values):
        self.saved.append(dict(values))
        self.values.update(values)
        return True


class _Index:
    def __init__(self, model, row):
        self.model, self.row = model, row

    def isValid(self):
        return 0 <= self.row < len(self.model.videos)

    def data(self, role=None):
        return self.model.get_video_at(self.row)


class _Model:
    def __init__(self):
        self.videos = []

    def index(self, row, column):
        return _Index(self, row)

    def get_video_at(self, row):
        return self.videos[row] if 0 <= row < len(self.videos) else None

    def rowCount(self):
        return len(self.videos)

    def set_videos(self, videos):
        self.videos = list(videos)


class _ListView:
    def __init__(self):
        self.model = _Model()
        self.row = -1
        self.focused = False

    def set_search_results(self, videos):
        self.model.set_videos(videos)
        self.row = 0 if videos else -1

    def setCurrentIndex(self, index):
        self.row = index.row

    def currentIndex(self):
        return self.model.index(self.row, 0)

    def get_selected_video(self):
        return self.model.get_video_at(self.row)

    def clearSelection(self):
        self.row = -1

    def setFocus(self):
        self.focused = True

    def clear_results(self):
        self.set_search_results([])


class _Timer:
    callbacks = []

    @classmethod
    def singleShot(cls, delay, callback):
        cls.callbacks.append((delay, callback))

    @classmethod
    def flush(cls):
        callbacks, cls.callbacks = cls.callbacks, []
        for _, callback in callbacks:
            callback()


_TitleBar = _logic_class('TitleBar', [
    'set_auto_play_checked', '_on_autoplay_toggled', '_on_anime_op_toggled',
])
_Window = _logic_class('MainWindow', [
    '_restore_auto_play_settings', 'set_auto_play_enabled', 'set_anime_op_mode_enabled',
    'on_youtube_search_completed', '_auto_play_video', '_select_search_result',
    '_handle_player_feedback', 'play_current_video', 'preload_current_video',
    'on_youtube_double_click', '_add_remaining_videos',
], {'select_auto_play_index': select_auto_play_index, 'QTimer': _Timer,
    'Qt': types.SimpleNamespace(DisplayRole=0)})


def _videos(*durations):
    return [{'video_id': f'video-{i}', 'title': f'Song {i}', 'duration': duration}
            for i, duration in enumerate(durations)]


def _window(auto=True, anime=True, **settings):
    window = _Window()
    window.config_service = _Config({
        'auto_play_top_result': auto, 'anime_op_mode': anime,
        'bring_to_front_on_search': False, 'bring_to_front_on_hotkey': False,
        **settings,
    })
    window.title_bar = _TitleBar()
    bar = window.title_bar
    bar._main_window = window
    bar.autoplay_button, bar.anime_op_button = _Button(), _Button()
    bar.autoplay_button.toggled.connect(bar._on_autoplay_toggled)
    bar.anime_op_button.toggled.connect(bar._on_anime_op_toggled)
    window._restore_auto_play_settings()
    window.left_pane = _ListView()
    window._active_search_allow_auto_play = True
    window._refresh_connection_statuses = Mock()
    window._bring_to_front = Mock()
    window._load_thumbnails_async = Mock()
    window._schedule_remaining_videos = Mock()
    window._schedule_bring_to_back = Mock()
    window._send_video_command = Mock(return_value=True)
    window._update_youtube_video_state = Mock()
    window._update_player_control_panel = Mock()
    window._update_youtube_border_color = Mock()
    window._find_video_data = Mock(return_value={})
    window._selected_player_id = Mock(return_value='A')
    window.player_server = Mock()
    window.pending_play_video_id = None
    window.pending_auto_play_video_id = None
    window.pending_auto_seek_video_id = None
    window.preloaded_video_id = None
    window.current_playing_video_id = None
    window.last_clicked_video_id = None
    window.youtube_video_state = None
    return window


class SelectionTests(unittest.TestCase):
    def test_empty_results(self):
        self.assertIsNone(select_auto_play_index([], True))
        self.assertIsNone(select_auto_play_index([], False))

    def test_default_and_disabled_mode_select_rank_one(self):
        videos = _videos('4:00', '1:30')
        self.assertEqual(select_auto_play_index(videos), 0)
        self.assertEqual(select_auto_play_index(videos, False), 0)

    def test_every_top_four_position_is_eligible(self):
        for row in range(4):
            with self.subTest(row=row):
                videos = _videos('4:00', '4:00', '4:00', '4:00', '1:30')
                videos[row]['duration'] = '1:35'
                self.assertEqual(select_auto_play_index(videos, True), row)

    def test_inclusive_90_through_105_seconds(self):
        for seconds in range(90, 106):
            with self.subTest(seconds=seconds):
                self.assertEqual(select_auto_play_index(_videos('4:00', f'1:{seconds-60}'), True), 1)

    def test_89_and_106_seconds_are_not_eligible(self):
        self.assertEqual(select_auto_play_index(_videos('4:00', '1:29', '1:46'), True), 0)

    def test_multiple_matches_keep_highest_rank(self):
        self.assertEqual(select_auto_play_index(_videos('4:00', '1:45', '1:30', '1:35'), True), 1)

    def test_fifth_and_later_matches_are_ignored(self):
        self.assertEqual(select_auto_play_index(_videos('4:00', '2:00', '1:20', '5:00', '1:30', '1:40'), True), 0)

    def test_one_result_is_safe(self):
        self.assertEqual(select_auto_play_index(_videos('1:30'), True), 0)
        self.assertEqual(select_auto_play_index(_videos('3:30'), True), 0)

    def test_unknown_durations_fall_back_to_first(self):
        self.assertEqual(select_auto_play_index(_videos('', None, 'LIVE', '--:--'), True), 0)

    def test_missing_or_unusable_id_does_not_get_priority(self):
        videos = _videos('4:00', '1:30', '1:45')
        del videos[1]['video_id']
        self.assertEqual(select_auto_play_index(videos, True), 2)

    def test_results_are_not_mutated_or_reordered(self):
        videos = _videos('4:00', '1:30')
        original = deepcopy(videos)
        select_auto_play_index(videos, True)
        self.assertEqual(videos, original)

    def test_disabled_mode_does_not_parse_durations(self):
        with patch('app.services.autoplay._duration_seconds') as parse:
            self.assertEqual(select_auto_play_index(_videos('4:00', '1:30'), False), 0)
        parse.assert_not_called()

    def test_no_match_parses_only_four_results(self):
        with patch('app.services.autoplay._duration_seconds', wraps=_duration_seconds) as parse:
            select_auto_play_index(_videos(*(['4:00'] * 20)), True)
        self.assertEqual(parse.call_count, 4)

    def test_formatted_duration_parsing(self):
        for value, seconds in [('1:30', 90), ('01:45', 105), ('0:01:30', 90),
                               (' 1:45 ', 105), ('60:00', 3600), ('1:30:00', 5400)]:
            with self.subTest(value=value):
                self.assertEqual(_duration_seconds(value), seconds)

    def test_invalid_duration_is_not_a_match(self):
        for value in [None, '', '90', 90, True, [], {}, 'PT1M30S', '-1:30',
                      '1:60', '0:61:00', '0:0:1:30', '1:3.5', 'LIVE', 'abc:30']:
            with self.subTest(value=value):
                self.assertIsNone(_duration_seconds(value))


class ToggleTests(unittest.TestCase):
    def test_initial_defaults_are_off(self):
        window = _window(False, False)
        self.assertFalse(window.title_bar.autoplay_button.checked)
        self.assertFalse(window.title_bar.anime_op_button.checked)
        self.assertFalse(window.title_bar.anime_op_button.enabled)
        self.assertEqual(window.config_service.saved, [])

    def test_legacy_config_without_mode_defaults_to_off(self):
        window = _window()
        del window.config_service.values['anime_op_mode']
        window._restore_auto_play_settings()
        self.assertFalse(window.anime_op_mode)
        self.assertTrue(window.title_bar.anime_op_button.enabled)
        self.assertEqual(window.config_service.saved, [])

    def test_saved_on_modes_are_restored(self):
        window = _window()
        self.assertTrue(window.title_bar.anime_op_button.checked)
        self.assertTrue(window.title_bar.anime_op_button.enabled)
        self.assertEqual(window.config_service.saved, [])

    def test_inconsistent_saved_mode_is_corrected(self):
        window = _window(False, True)
        self.assertFalse(window.anime_op_mode)
        self.assertFalse(window.title_bar.anime_op_button.enabled)
        self.assertEqual(window.config_service.saved, [{'anime_op_mode': False}])

    def test_autoplay_on_enables_mode_but_does_not_turn_it_on(self):
        window = _window(False, False)
        window.title_bar.autoplay_button.click()
        self.assertTrue(window.auto_play_top_result)
        self.assertTrue(window.title_bar.anime_op_button.enabled)
        self.assertFalse(window.anime_op_mode)
        self.assertEqual(len(window.config_service.saved), 1)

    def test_mode_click_changes_state_and_saves_once(self):
        window = _window(True, False)
        window.title_bar.anime_op_button.click()
        self.assertTrue(window.anime_op_mode)
        self.assertTrue(window.title_bar.anime_op_button.text.endswith(' ON'))
        self.assertEqual(window.config_service.saved, [{'anime_op_mode': True}])
        window.title_bar.anime_op_button.click()
        self.assertFalse(window.anime_op_mode)

    def test_turning_autoplay_off_clears_disables_and_saves_mode(self):
        window = _window()
        window.title_bar.autoplay_button.click()
        self.assertFalse(window.anime_op_mode)
        self.assertFalse(window.title_bar.anime_op_button.checked)
        self.assertFalse(window.title_bar.anime_op_button.enabled)
        self.assertTrue(window.title_bar.anime_op_button.text.endswith(' OFF'))
        self.assertEqual(window.config_service.saved, [{'auto_play_top_result': False, 'anime_op_mode': False}])

    def test_off_mode_cannot_be_clicked(self):
        window = _window(False, False)
        window.title_bar.anime_op_button.click()
        self.assertFalse(window.anime_op_mode)
        self.assertEqual(window.config_service.saved, [])

    def test_programmatic_enable_is_rejected_while_autoplay_off(self):
        window = _window(False, False)
        window.set_anime_op_mode_enabled(True)
        self.assertFalse(window.anime_op_mode)
        self.assertFalse(window.title_bar.anime_op_button.checked)

    def test_autoplay_reenable_does_not_resurrect_mode(self):
        window = _window()
        window.set_auto_play_enabled(False)
        window.set_auto_play_enabled(True)
        self.assertFalse(window.anime_op_mode)
        self.assertTrue(window.title_bar.anime_op_button.enabled)
        self.assertFalse(window.title_bar.anime_op_button.checked)

    def test_off_remains_off_after_restart(self):
        window = _window()
        window.set_auto_play_enabled(False)
        window._restore_auto_play_settings()
        window.set_auto_play_enabled(True)
        self.assertFalse(window.anime_op_mode)

    def test_ui_sync_preserves_existing_signal_blocking(self):
        window = _window()
        bar = window.title_bar
        bar.autoplay_button.blockSignals(True)
        bar.anime_op_button.blockSignals(True)
        bar.set_auto_play_checked(False, True)
        self.assertTrue(bar.autoplay_button.blocked)
        self.assertTrue(bar.anime_op_button.blocked)
        self.assertFalse(bar.anime_op_button.checked)
        self.assertEqual(window.config_service.saved, [])

    def test_off_cancels_only_automatic_pending_play(self):
        window = _window()
        window._auto_play_video(_videos('1:30')[0])
        window.set_auto_play_enabled(False)
        self.assertIsNone(window.pending_play_video_id)
        self.assertIsNone(window.pending_auto_play_video_id)
        self.assertIsNone(window.pending_auto_seek_video_id)

    def test_off_does_not_cancel_a_manual_pending_play(self):
        window = _window()
        window.pending_play_video_id = 'manual'
        window.set_auto_play_enabled(False)
        self.assertEqual(window.pending_play_video_id, 'manual')

    def test_mode_changes_do_not_start_playback(self):
        window = _window()
        window.set_anime_op_mode_enabled(False)
        window.set_anime_op_mode_enabled(True)
        window._send_video_command.assert_not_called()

    def test_button_is_beside_autoplay_and_has_disabled_style(self):
        auto = SOURCE.index('layout.addWidget(self.autoplay_button)')
        anime = SOURCE.index('layout.addWidget(self.anime_op_button)')
        stretch = SOURCE.index('layout.addStretch()', auto)
        self.assertLess(auto, anime)
        self.assertLess(anime, stretch)
        self.assertIn('#anime_op_button:disabled', SOURCE)
        self.assertIn('self.title_bar.autoplay_button, self.title_bar.anime_op_button', SOURCE)


class SearchCompletionTests(unittest.TestCase):
    def setUp(self):
        _Timer.callbacks = []
        logger = types.ModuleType('app.utils.logger')
        logger.info, logger.debug = Mock(), Mock()
        patcher = patch.dict(sys.modules, {'app.utils.logger': logger})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prefer_op_and_keep_selection_in_sync(self):
        window = _window()
        videos = _videos('4:00', '2:50', '1:30', '1:45', '3:00')
        window.on_youtube_search_completed(videos)
        window._send_video_command.assert_called_once_with('PRELOAD', 'video-2', videos[2])
        self.assertEqual(window.pending_play_video_id, 'video-2')
        self.assertEqual(window.left_pane.row, 2)
        _Timer.flush()
        self.assertEqual(window.left_pane.row, 2)
        self.assertTrue(window.left_pane.focused)
        self.assertEqual([v['video_id'] for v in window.left_pane.model.videos],
                         [v['video_id'] for v in videos])

    def test_fourth_is_eligible_and_fifth_is_not(self):
        for durations, expected in [(['4:00', '4:00', '4:00', '1:45', '1:30'], 'video-3'),
                                    (['4:00', '4:00', '4:00', '4:00', '1:30'], 'video-0')]:
            with self.subTest(durations=durations):
                window = _window()
                window.on_youtube_search_completed(_videos(*durations))
                self.assertEqual(window.pending_play_video_id, expected)

    def test_mode_off_keeps_rank_one(self):
        window = _window(True, False)
        window.on_youtube_search_completed(_videos('4:00', '1:30'))
        self.assertEqual(window.pending_play_video_id, 'video-0')

    def test_autoplay_off_never_queues_play(self):
        window = _window(False, False)
        window.on_youtube_search_completed(_videos('4:00', '1:30'))
        window._send_video_command.assert_not_called()
        self.assertEqual(window.left_pane.row, 0)

    def test_manual_search_does_not_autoplay_even_in_op_mode(self):
        window = _window()
        window._active_search_allow_auto_play = False
        window.on_youtube_search_completed(_videos('4:00', '1:30'))
        window._send_video_command.assert_not_called()
        self.assertEqual(window.left_pane.row, 0)

    def test_no_match_or_missing_durations_fall_back(self):
        window = _window()
        window.on_youtube_search_completed(_videos('', None, 'LIVE', '1:46'))
        self.assertEqual(window.pending_play_video_id, 'video-0')

    def test_empty_results_do_not_schedule_play_or_selection(self):
        window = _window()
        window.on_youtube_search_completed([])
        window._send_video_command.assert_not_called()
        self.assertEqual(_Timer.callbacks, [])

    def test_mode_is_read_when_search_completes(self):
        window = _window(True, False)
        window.set_anime_op_mode_enabled(True)
        window.on_youtube_search_completed(_videos('4:00', '1:30'))
        self.assertEqual(window.pending_play_video_id, 'video-1')

    def test_autoplay_turned_off_during_search_prevents_play(self):
        window = _window()
        window.set_auto_play_enabled(False)
        window.on_youtube_search_completed(_videos('4:00', '1:30'))
        window._send_video_command.assert_not_called()

    def test_later_results_and_thumbnails_still_load(self):
        window = _window()
        videos = _videos('4:00', '1:30', '5:00', '2:00', '6:00', '1:32')
        window.on_youtube_search_completed(videos)
        window._load_thumbnails_async.assert_called_once_with(videos[:5])
        window._schedule_remaining_videos.assert_called_once_with(videos[5:])
        window._add_remaining_videos(videos[5:])
        self.assertEqual(window.left_pane.row, 1)
        self.assertEqual(len(window.left_pane.model.videos), 6)
        self.assertEqual(window.pending_play_video_id, 'video-1')

    def test_delayed_selection_does_not_override_user_selection(self):
        window = _window()
        window.on_youtube_search_completed(_videos('4:00', '1:30', '3:00'))
        window.left_pane.setCurrentIndex(window.left_pane.model.index(2, 0))
        _Timer.flush()
        self.assertEqual(window.left_pane.row, 2)
        self.assertFalse(window.left_pane.focused)

    def test_delayed_selection_ignores_replaced_results(self):
        window = _window()
        window.on_youtube_search_completed(_videos('4:00', '1:30'))
        replacement = [{'video_id': 'new-result', 'duration': '3:00'}]
        window.left_pane.set_search_results(replacement)
        _Timer.flush()
        self.assertEqual(window.left_pane.row, 0)
        self.assertFalse(window.left_pane.focused)

    def test_preload_ready_uses_selected_video(self):
        window = _window()
        window.on_youtube_search_completed(_videos('4:00', '1:30'))
        window._send_video_command.reset_mock()
        window._handle_player_feedback({'state': 'ready', 'videoId': 'video-1', 'playerId': 'B'})
        window._send_video_command.assert_called_once_with('PLAY', 'video-1', {})
        self.assertIsNone(window.pending_auto_play_video_id)

    def test_off_while_preloading_prevents_late_ready_play(self):
        window = _window()
        window.on_youtube_search_completed(_videos('4:00', '1:30'))
        window.set_auto_play_enabled(False)
        window._send_video_command.reset_mock()
        window._handle_player_feedback({'state': 'ready', 'videoId': 'video-1'})
        window._send_video_command.assert_not_called()

    def test_manual_play_still_works_when_autoplay_off(self):
        window = _window()
        window.on_youtube_search_completed(_videos('1:30'))
        window.play_current_video()
        self.assertIsNone(window.pending_auto_play_video_id)
        window.set_auto_play_enabled(False)
        window._send_video_command.reset_mock()
        window._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        window._send_video_command.assert_called_once_with('PLAY', 'video-0', {})

    def test_manual_preload_replaces_automatic_play_with_ready_only(self):
        window = _window()
        window.on_youtube_search_completed(_videos('1:30'))
        window.preload_current_video()
        window._send_video_command.reset_mock()
        window._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        window._send_video_command.assert_not_called()
        self.assertIsNone(window.pending_auto_play_video_id)

    def test_manual_double_click_cancels_the_automatic_pending_play(self):
        window = _window(auto_play_seek_seconds=10)
        window.on_youtube_search_completed(_videos('1:30'))
        index = types.SimpleNamespace(row=lambda: 0)
        window.on_youtube_double_click(index)
        self.assertIsNone(window.pending_play_video_id)
        self.assertIsNone(window.pending_auto_play_video_id)
        self.assertIsNone(window.pending_auto_seek_video_id)
        window._send_video_command.reset_mock()
        window._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        window._send_video_command.assert_not_called()

    def test_auto_seek_setting_is_preserved_for_op_video(self):
        window = _window(auto_play_seek_seconds=10)
        window.on_youtube_search_completed(_videos('4:00', '1:30'))
        self.assertEqual(window.pending_auto_seek_video_id, 'video-1')
        window._handle_player_feedback({'state': 'playing', 'videoId': 'video-1', 'playerId': 'B'})
        window.player_server.send_command.assert_called_once_with('FORWARD', '10', player_id='B')
        self.assertIsNone(window.pending_auto_seek_video_id)

    def test_error_clears_pending_automatic_operation(self):
        window = _window()
        window.on_youtube_search_completed(_videos('4:00', '1:30'))
        window._handle_player_feedback({'state': 'error', 'videoId': 'video-1'})
        self.assertIsNone(window.pending_play_video_id)
        self.assertIsNone(window.pending_auto_play_video_id)

    def test_missing_player_does_not_queue_a_play(self):
        window = _window()
        window.player_server = None
        window.on_youtube_search_completed(_videos('4:00', '1:30'))
        self.assertIsNone(window.pending_play_video_id)
        window._send_video_command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
