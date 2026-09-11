"""Offline anime OP tests: real selection/UI methods, with Qt and I/O mocked."""

import ast
from copy import deepcopy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.autoplay_selection import select_auto_play_index
from app.services.config_service import ConfigService

TREE = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))


def _methods(class_name, names):
    cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in methods} == set(names)
    namespace = {}
    exec(compile(ast.Module(body=methods, type_ignores=[]), 'main.py', 'exec'), namespace)
    return {name: namespace[name] for name in names}


TitleHarness = type('TitleHarness', (), _methods('TitleBar', [
    '_on_autoplay_toggled', '_on_anime_op_toggled',
    'set_auto_play_checked', 'set_anime_op_checked',
]))
WindowHarness = type('WindowHarness', (), _methods('MainWindow', [
    '_restore_auto_play_settings', 'set_auto_play_enabled', 'set_anime_op_mode_enabled',
    '_auto_play_video', 'on_youtube_search_completed', '_select_search_video',
    '_add_remaining_videos', '_handle_player_feedback',
]))


class _Button:
    def __init__(self):
        self.checked = False
        self.enabled = True
        self.blocked = False
        self.text = ''
        self.tooltip = ''
        self.handler = None

    def blockSignals(self, blocked):
        previous, self.blocked = self.blocked, blocked
        return previous

    def isChecked(self):
        return self.checked

    def setChecked(self, checked):
        changed = checked != self.checked
        self.checked = checked
        if changed and not self.blocked and self.handler:
            self.handler(checked)

    def setEnabled(self, enabled):
        self.enabled = enabled

    def setText(self, text):
        self.text = text

    def setToolTip(self, tooltip):
        self.tooltip = tooltip

    def click(self):
        if self.enabled:
            self.setChecked(not self.checked)


class _Config:
    def __init__(self, values):
        self.values = dict(values)
        self.saves = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def save_config(self, values):
        self.values.update(values)
        self.saves.append(dict(values))
        return True


class _Index:
    def __init__(self, row):
        self._row = row

    def row(self):
        return self._row

    def isValid(self):
        return self._row >= 0


class _Model:
    def __init__(self):
        self.videos = []

    def rowCount(self):
        return len(self.videos)

    def get_video_at(self, row):
        return self.videos[row] if 0 <= row < len(self.videos) else None

    def index(self, row, column):
        return _Index(row if 0 <= row < len(self.videos) else -1)

    def set_videos(self, videos):
        self.videos = list(videos)


class _Pane:
    def __init__(self):
        self.model = _Model()
        self.selected_row = -1
        self.setFocus = Mock()

    def set_search_results(self, videos):
        self.model.set_videos(videos)
        self.selected_row = 0 if videos else -1

    def setCurrentIndex(self, index):
        self.selected_row = index.row()

    def get_selected_video(self):
        return self.model.get_video_at(self.selected_row)

    def clear_results(self):
        self.set_search_results([])


def _videos(*durations):
    return [dict(video_id=f'video-{i}', title=f'Title {i}', duration=d)
            for i, d in enumerate(durations)]


def _window(values=None):
    window = WindowHarness()
    window.config_service = _Config({
        'bring_to_front_on_search': False, 'bring_to_front_on_hotkey': False,
        **(values or {}),
    })
    bar = window.title_bar = TitleHarness()
    bar._main_window = window
    bar.autoplay_button, bar.anime_op_button = _Button(), _Button()
    bar.autoplay_button.handler = bar._on_autoplay_toggled
    bar.anime_op_button.handler = bar._on_anime_op_toggled
    window._restore_auto_play_settings()
    window.left_pane = _Pane()
    window.player_server = Mock()
    window._send_video_command = Mock(return_value=True)
    for name in ['_update_youtube_video_state', '_refresh_connection_statuses',
                 '_schedule_remaining_videos', '_load_thumbnails_async',
                 '_update_player_control_panel']:
        setattr(window, name, Mock())
    window.pending_auto_seek_video_id = None
    window.pending_play_video_id = None
    window.preloaded_video_id = None
    window.last_clicked_video_id = None
    window._active_search_allow_auto_play = True
    window._find_video_data = lambda vid: next(
        (v for v in window.left_pane.model.videos if v['video_id'] == vid), None
    )
    return window


def _complete(window, videos):
    logger = types.ModuleType('app.utils.logger')
    logger.info, logger.debug = Mock(), Mock()
    with patch.dict(sys.modules, {'app.utils.logger': logger}):
        window.on_youtube_search_completed(videos)


class SelectionTests(unittest.TestCase):
    def test_empty_results_have_no_selection(self):
        self.assertIsNone(select_auto_play_index([], True))
        self.assertIsNone(select_auto_play_index([]))

    def test_disabled_mode_keeps_rank_one(self):
        self.assertEqual(select_auto_play_index(_videos('4:00', '1:30'), False), 0)

    def test_lower_boundary_inclusive(self):
        self.assertEqual(select_auto_play_index(_videos('4:00', '1:30'), True), 1)

    def test_upper_boundary_inclusive(self):
        self.assertEqual(select_auto_play_index(_videos('4:00', '1:45'), True), 1)

    def test_each_of_top_four_can_win(self):
        for rank in range(4):
            with self.subTest(rank=rank):
                videos = _videos('4:00', '4:00', '4:00', '4:00', '4:00')
                videos[rank]['duration'] = '1:32'
                self.assertEqual(select_auto_play_index(videos, True), rank)

    def test_fifth_result_does_not_qualify(self):
        self.assertEqual(select_auto_play_index(
            _videos('4:00', '3:00', '1:29', '1:46', '1:30'), True), 0)

    def test_higher_rank_wins_not_shortest_or_closest_to_ninety(self):
        self.assertEqual(select_auto_play_index(_videos('4:00', '1:45', '1:30'), True), 1)

    def test_fewer_than_four_results(self):
        self.assertEqual(select_auto_play_index(_videos('1:34'), True), 0)
        self.assertEqual(select_auto_play_index(_videos('4:00', '1:31'), True), 1)

    def test_every_second_around_the_window(self):
        for seconds in range(60, 121):
            with self.subTest(seconds=seconds):
                duration = f'{seconds // 60}:{seconds % 60:02d}'
                expected = 1 if 90 <= seconds <= 105 else 0
                self.assertEqual(select_auto_play_index(_videos('4:00', duration), True), expected)

    def test_unknown_or_invalid_duration_is_not_preferred(self):
        for duration in [None, '', '--:--', 'LIVE', 90, 'PT1M30S', 'nan', '1:99',
                         '-1:30', '0:90', '1:30.0', '1:30:00', '1:00:90', '1:90:00']:
            with self.subTest(duration=duration):
                self.assertEqual(select_auto_play_index(_videos('4:00', duration), True), 0)

    def test_unknown_duration_does_not_hide_next_match(self):
        self.assertEqual(select_auto_play_index(_videos('4:00', None, '1:40'), True), 2)

    def test_missing_duration_does_not_raise(self):
        videos = _videos('4:00', '1:30')
        del videos[1]['duration']
        self.assertEqual(select_auto_play_index(videos, True), 0)

    def test_missing_video_id_is_not_preferred(self):
        videos = _videos('4:00', '1:30', '1:45')
        del videos[1]['video_id']
        self.assertEqual(select_auto_play_index(videos, True), 2)

    def test_hour_format_padding_and_whitespace(self):
        for duration in ['0:01:30', '01:45', ' 1:32 ']:
            with self.subTest(duration=duration):
                self.assertEqual(select_auto_play_index(_videos('4:00', duration), True), 1)

    def test_long_video_cannot_be_mistaken_for_short_video(self):
        self.assertEqual(select_auto_play_index(_videos('4:00', '61:30'), True), 0)

    def test_no_mutation_or_reordering(self):
        videos = _videos('4:00', '1:30', '5:00')
        original = deepcopy(videos)
        select_auto_play_index(videos, True)
        self.assertEqual(videos, original)

    def test_never_inspects_results_outside_top_four(self):
        ignored = Mock()
        ignored.get.side_effect = AssertionError('Rank five must not be inspected')
        self.assertEqual(select_auto_play_index([*_videos('4:00', '3:00', '2:00', '5:00'), ignored], True), 0)


class ModeStateTests(unittest.TestCase):
    def test_config_default_is_off(self):
        config = object.__new__(ConfigService)
        config._load_default_config()
        self.assertIs(config.get('anime_op_mode'), False)

    def test_first_run_is_off_and_disabled(self):
        window = _window()
        self.assertFalse(window.auto_play_top_result)
        self.assertFalse(window.anime_op_mode)
        self.assertFalse(window.title_bar.anime_op_button.enabled)
        self.assertTrue(window.title_bar.anime_op_button.text.endswith(' OFF'))
        self.assertEqual(window.config_service.saves, [])

    def test_autoplay_on_enables_mode_button_but_does_not_turn_mode_on(self):
        window = _window()
        window.title_bar.autoplay_button.click()
        self.assertTrue(window.auto_play_top_result)
        self.assertTrue(window.title_bar.anime_op_button.enabled)
        self.assertFalse(window.anime_op_mode)
        self.assertEqual(len(window.config_service.saves), 1)

    def test_disabled_button_cannot_be_clicked(self):
        window = _window()
        window.title_bar.anime_op_button.click()
        self.assertFalse(window.anime_op_mode)
        self.assertEqual(window.config_service.saves, [])

    def test_mode_click_saves_once_without_starting_playback(self):
        window = _window({'auto_play_top_result': True})
        window.title_bar.anime_op_button.click()
        self.assertTrue(window.anime_op_mode)
        self.assertEqual(window.config_service.saves, [{'anime_op_mode': True}])
        window._send_video_command.assert_not_called()

    def test_autoplay_off_clears_mode_ui_and_saved_state(self):
        window = _window({'auto_play_top_result': True, 'anime_op_mode': True})
        window.pending_auto_seek_video_id = 'pending'
        window.title_bar.autoplay_button.click()
        self.assertFalse(window.anime_op_mode)
        self.assertFalse(window.title_bar.anime_op_button.enabled)
        self.assertFalse(window.title_bar.anime_op_button.checked)
        self.assertEqual(window.config_service.saves, [
            {'auto_play_top_result': False, 'anime_op_mode': False}])
        self.assertIsNone(window.pending_auto_seek_video_id)

    def test_reenabling_autoplay_does_not_restore_cleared_mode(self):
        window = _window({'auto_play_top_result': True, 'anime_op_mode': True})
        window.title_bar.autoplay_button.click()
        window.title_bar.autoplay_button.click()
        self.assertTrue(window.auto_play_top_result)
        self.assertTrue(window.title_bar.anime_op_button.enabled)
        self.assertFalse(window.anime_op_mode)
        reopened = _window(window.config_service.values)
        self.assertFalse(reopened.anime_op_mode)

    def test_both_on_are_restored_without_unnecessary_write(self):
        window = _window({'auto_play_top_result': True, 'anime_op_mode': True})
        self.assertTrue(window.anime_op_mode)
        self.assertTrue(window.title_bar.anime_op_button.checked)
        self.assertTrue(window.title_bar.anime_op_button.enabled)
        self.assertEqual(window.config_service.saves, [])

    def test_invalid_saved_off_on_combination_is_repaired(self):
        window = _window({'auto_play_top_result': False, 'anime_op_mode': True})
        self.assertFalse(window.anime_op_mode)
        self.assertFalse(window.title_bar.anime_op_button.checked)
        self.assertFalse(window.title_bar.anime_op_button.enabled)
        self.assertEqual(window.config_service.saves, [{'anime_op_mode': False}])

    def test_programmatic_enable_is_rejected_while_autoplay_off(self):
        window = _window()
        window.set_anime_op_mode_enabled(True)
        self.assertFalse(window.anime_op_mode)
        self.assertFalse(window.config_service.values['anime_op_mode'])

    def test_disabling_mode_does_not_disable_autoplay(self):
        window = _window({'auto_play_top_result': True, 'anime_op_mode': True})
        window.title_bar.anime_op_button.click()
        self.assertTrue(window.auto_play_top_result)
        self.assertFalse(window.anime_op_mode)
        self.assertTrue(window.title_bar.anime_op_button.enabled)

    def test_ui_sync_preserves_preexisting_signal_blocks(self):
        window = _window()
        bar = window.title_bar
        bar.autoplay_button.blockSignals(True)
        bar.anime_op_button.blockSignals(True)
        bar.set_auto_play_checked(True)
        bar.set_anime_op_checked(True)
        self.assertTrue(bar.autoplay_button.blocked)
        self.assertTrue(bar.anime_op_button.blocked)
        self.assertEqual(window.config_service.saves, [])

    def test_ui_sync_cannot_check_mode_with_autoplay_off(self):
        window = _window()
        window.title_bar.set_anime_op_checked(True)
        self.assertFalse(window.title_bar.anime_op_button.checked)
        self.assertFalse(window.title_bar.anime_op_button.enabled)

    def test_layout_places_mode_immediately_after_autoplay(self):
        cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == 'TitleBar')
        init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
        widgets = [n.value.args[0].attr for n in init.body
                   if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                   and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'addWidget'
                   and n.value.args and isinstance(n.value.args[0], ast.Attribute)]
        self.assertEqual(widgets[widgets.index('autoplay_button') + 1], 'anime_op_button')


class PlaybackWiringTests(unittest.TestCase):
    def _enabled_window(self, **values):
        return _window({'auto_play_top_result': True, 'anime_op_mode': True, **values})

    def test_fourth_result_is_queued_and_selected(self):
        window = self._enabled_window()
        videos = _videos('4:00', '3:00', '2:00', '1:30', '1:31')
        _complete(window, videos)
        window._send_video_command.assert_called_once_with('PRELOAD', 'video-3', videos[3])
        self.assertEqual(window.pending_play_video_id, 'video-3')
        self.assertEqual(window.left_pane.selected_row, 3)

    def test_mode_off_preserves_previous_rank_one_behavior(self):
        window = self._enabled_window(anime_op_mode=False)
        videos = _videos('4:00', '1:30')
        _complete(window, videos)
        window._send_video_command.assert_called_once_with('PRELOAD', 'video-0', videos[0])
        self.assertEqual(window.left_pane.selected_row, 0)

    def test_autoplay_off_never_plays_even_with_stale_mode_flag(self):
        window = _window()
        window.anime_op_mode = True
        _complete(window, _videos('4:00', '1:30'))
        window._send_video_command.assert_not_called()
        self.assertEqual(window.left_pane.selected_row, 0)

    def test_manual_search_does_not_play_or_prefer_short_video(self):
        window = self._enabled_window()
        window._active_search_allow_auto_play = False
        _complete(window, _videos('4:00', '1:30'))
        window._send_video_command.assert_not_called()
        self.assertEqual(window.left_pane.selected_row, 0)

    def test_no_matching_length_plays_rank_one(self):
        window = self._enabled_window()
        videos = _videos('4:00', '1:29', '1:46')
        _complete(window, videos)
        window._send_video_command.assert_called_once_with('PRELOAD', 'video-0', videos[0])

    def test_fifth_match_is_not_used_by_actual_callback(self):
        window = self._enabled_window()
        videos = _videos('4:00', '3:00', '2:00', '5:00', '1:30')
        _complete(window, videos)
        window._send_video_command.assert_called_once_with('PRELOAD', 'video-0', videos[0])

    def test_empty_results_clear_list_without_playback(self):
        window = self._enabled_window()
        window.left_pane.set_search_results(_videos('4:00'))
        _complete(window, [])
        self.assertEqual(window.left_pane.model.rowCount(), 0)
        window._send_video_command.assert_not_called()

    def test_result_order_and_background_append_are_preserved(self):
        window = self._enabled_window()
        videos = _videos('4:00', '3:00', '2:00', '1:30', '5:00', '1:40', '2:00')
        original = deepcopy(videos)
        _complete(window, videos)
        self.assertEqual([v['video_id'] for v in window.left_pane.model.videos],
                         [v['video_id'] for v in videos[:5]])
        window._schedule_remaining_videos.assert_called_once_with(videos[5:])
        window._add_remaining_videos(videos[5:])
        self.assertEqual([v['video_id'] for v in window.left_pane.model.videos],
                         [v['video_id'] for v in videos])
        self.assertEqual(window.left_pane.selected_row, 3)
        self.assertEqual(videos, original)
        self.assertEqual(window._send_video_command.call_count, 1)

    def test_ready_feedback_plays_the_preferred_video(self):
        window = self._enabled_window()
        videos = _videos('4:00', '1:30')
        _complete(window, videos)
        window._handle_player_feedback({'state': 'ready', 'videoId': 'video-1'})
        self.assertEqual([c.args[:2] for c in window._send_video_command.call_args_list],
                         [('PRELOAD', 'video-1'), ('PLAY', 'video-1')])
        self.assertIsNone(window.pending_play_video_id)

    def test_existing_initial_seek_tracks_the_preferred_video(self):
        window = self._enabled_window(auto_play_seek_seconds=10)
        _complete(window, _videos('4:00', '1:30'))
        self.assertEqual(window.pending_auto_seek_video_id, 'video-1')

    def test_turning_autoplay_off_before_results_arrive_prevents_playback(self):
        window = self._enabled_window()
        window.set_auto_play_enabled(False)
        _complete(window, _videos('4:00', '1:30'))
        window._send_video_command.assert_not_called()
        self.assertFalse(window.anime_op_mode)

    def test_turning_mode_off_before_results_arrive_uses_rank_one(self):
        window = self._enabled_window()
        window.set_anime_op_mode_enabled(False)
        videos = _videos('4:00', '1:30')
        _complete(window, videos)
        window._send_video_command.assert_called_once_with('PRELOAD', 'video-0', videos[0])

    def test_missing_player_server_does_not_send_commands(self):
        window = self._enabled_window()
        window.player_server = None
        _complete(window, _videos('4:00', '1:30'))
        window._send_video_command.assert_not_called()
        self.assertIsNone(window.pending_play_video_id)

    def test_all_unknown_durations_fall_back_without_failure(self):
        window = self._enabled_window()
        videos = _videos('', '', '')
        _complete(window, videos)
        window._send_video_command.assert_called_once_with('PRELOAD', 'video-0', videos[0])


if __name__ == '__main__':
    unittest.main()
