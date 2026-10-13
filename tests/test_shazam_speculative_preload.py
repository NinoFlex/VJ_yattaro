"""Offline two-vote Shazam speculative preload tests (no actual browser/Qt).

Includes lifecycle, ID-only metadata timing, a wrong first vote, playback races,
failover and stale asynchronous search/ready events.
"""
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from tests.test_webview_integration import ShazamService
from tests.test_latency_prefetch import ControlledPrefetch, raw_song
from tests.test_autoplay_selection import _methods, _window, WindowHarness, _complete, _videos

for name, function in _methods('MainWindow', [
    '_refresh_shazam_history_view', 'on_shazam_history_updated',
    'on_shazam_candidate_started', 'on_shazam_candidate_rejected',
    'on_shazam_candidate_confirmed', 'on_shazam_new_track_detected',
    '_play_confirmed_shazam_preview_if_ready', '_cue_shazam_preview_video',
]).items():
    setattr(WindowHarness, name, function)


class ServiceVoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 100.0
        self.clock = patch('time.monotonic', side_effect=lambda: self.now)
        self.clock.start()
        config = types.SimpleNamespace(get=lambda _k, default=None: default)
        with patch('app.services.config_service.ConfigService', return_value=config), \
             patch.object(ShazamService, '_get_history_path',
                          return_value=Path(self.tmp.name) / 'history.json'):
            self.svc = ShazamService()
        self.svc._active = True
        self.svc._generation = 1
        self.broker = ControlledPrefetch()
        self.svc._metadata_prefetch = self.broker
        self.a = raw_song(title='Song A', artist='Artist A')
        self.b = raw_song('22222222', title='Song B', artist='Artist B')
        self.c = raw_song('33333333', title='Song C', artist='Artist C')

    def tearDown(self):
        self.svc.shutdown()
        self.clock.stop()
        self.tmp.cleanup()

    def vote(self, sequence, song=None):
        raw = dict(song or self.a)
        self.svc._handle_confirmed_group_result(sequence % 4, sequence,
                                                 [sequence % 4],
                                                 [(raw.get('title', ''), raw.get('artist', ''), '')],
                                                 [raw])

    def no_match(self, sequence):
        raw = {'source': 'no-match', 'title': '', 'artist': ''}
        self.svc._handle_confirmed_group_result(sequence % 4, sequence, [sequence % 4],
                                                 [('', '', '')], [raw])

    def test_first_vote_is_tentative_only_second_vote_persists(self):
        self.vote(0, self.a)
        self.assertEqual(len(self.svc.candidate_started.calls), 1)
        self.assertEqual(self.svc.candidate_started.calls[0][0], 0)
        self.assertEqual(self.svc.get_history(), [])
        self.assertFalse((Path(self.tmp.name) / 'history.json').read_text().count('Song A'))
        self.vote(1, self.a)
        self.assertEqual(len(self.svc.candidate_confirmed.calls), 1)
        self.assertEqual([e[1] for e in self.svc.get_history()], ['Song A'])
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)

    def test_false_b_first_vote_removed_on_c(self):
        self.vote(0, self.a); self.vote(1, self.a)
        self.vote(2, self.b)
        self.assertEqual(self.svc.candidate_started.calls[-1][1][1], 'Song B')
        self.vote(3, self.c)
        self.assertIn((2, 'new-candidate'), self.svc.candidate_rejected.calls)
        self.assertEqual(self.svc.candidate_started.calls[-1][1][1], 'Song C')
        self.assertEqual([x[1] for x in self.svc.get_history()], ['Song A'])
        self.vote(4, self.c)
        self.assertEqual([x[1] for x in self.svc.get_history()], ['Song C', 'Song A'])

    def test_wrong_b_replaced_by_original_a_removes_preview_without_new_a(self):
        self.vote(0, self.a); self.vote(1, self.a)
        self.vote(2, self.b)
        self.assertEqual(self.svc.candidate_started.calls[-1][1][1], 'Song B')
        prior_starts = len(self.svc.candidate_started.calls)
        self.vote(3, self.a)
        self.assertIn((2, 'new-candidate'), self.svc.candidate_rejected.calls)
        self.assertEqual(len(self.svc.candidate_started.calls), prior_starts)
        self.assertEqual([x[1] for x in self.svc.get_history()], ['Song A'])
        self.vote(4, self.a)
        self.assertEqual([x[1] for x in self.svc.get_history()], ['Song A'])

    def test_one_two_no_matches_hold_preview_third_rejects(self):
        self.vote(0, self.b)
        self.no_match(1); self.no_match(2)
        self.assertEqual(len(self.svc.candidate_rejected.calls), 0)
        self.no_match(3)
        self.assertEqual(self.svc.candidate_rejected.calls[-1][0], 0)
        self.assertEqual(self.svc.get_history(), [])

    def test_candidate_expires_and_does_not_leave_history(self):
        self.vote(0, self.b)
        self.now = 116.0
        self.svc._expire_tentative_candidate()
        self.assertEqual(self.svc.candidate_rejected.calls[-1], (0, 'first-vote-expired'))
        self.assertEqual(self.svc.get_history(), [])

    def test_id_only_preview_waits_for_metadata_not_ui_noise(self):
        id_only = raw_song('22222222')
        self.vote(0, id_only)
        self.assertFalse(self.svc.candidate_started.calls)
        key = self.svc._metadata_key(('shazam-song', '22222222'))
        self.broker.finish(1, key, ('Song B', 'Artist B'))
        self.svc._handle_prefetch_finished(1, key, ('Song B', 'Artist B'), '')
        self.assertEqual(self.svc.candidate_started.calls[-1][1][1], 'Song B')
        self.assertEqual(self.svc.get_history(), [])
        self.vote(1, id_only)
        self.assertEqual(len(self.svc.candidate_confirmed.calls), 1)
        self.assertEqual(self.svc.get_history()[0][1], 'Song B')

    def test_trusted_title_only_is_available_on_first_vote(self):
        title_only = raw_song('22222222', title='Song B', artist='',
                              source='track-primary-header')
        self.vote(0, title_only)
        self.assertEqual(self.svc.candidate_started.calls[-1][1][1:], ('Song B', ''))
        self.assertEqual(self.svc.get_history(), [])
        self.vote(1, title_only)
        self.assertEqual(len(self.svc.candidate_confirmed.calls), 1)


class _Model:
    def __init__(self): self.data = []
    def update_data(self, data): self.data = list(data)
    def rowCount(self): return len(self.data)


class WindowVoteTests(unittest.TestCase):
    def window(self, autoplay=True):
        win = _window({'auto_play_top_result': autoplay})
        win.source_mode = 'shazam'
        win.shazam_service = Mock(get_history=Mock(return_value=[]))
        win.shazam_table_model = _Model()
        win.right_table = Mock()
        win.youtube_search_box = Mock(text=Mock(return_value=''))
        win.search_youtube = Mock()
        win._shazam_preview = None
        win._active_search_candidate_id = None
        win._youtube_results_owner_candidate_id = None
        win._youtube_list_generation = 0
        win._abandoned_preview_video_ids = set()
        win._pending_search_args = None
        return win

    def start(self, win):
        win.on_shazam_candidate_started(42, ('2026-10-13 10:00:00', 'Song B', 'Artist B'))
        self.assertIn('(仮)', win.shazam_table_model.data[0][0])
        self.assertEqual(win.search_youtube.call_args.kwargs['shazam_candidate_id'], 42)
        win._active_search_candidate_id = 42

    def test_preload_ready_before_second_vote_never_plays_early(self):
        win = self.window()
        self.start(win)
        _complete(win, _videos('3:00', '3:00'))
        self.assertEqual(win._send_video_command.call_args.args[0], 'PRELOAD')
        self.assertIsNone(win.pending_play_video_id)
        win._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        self.assertEqual([c.args[0] for c in win._send_video_command.call_args_list], ['PRELOAD'])
        win.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        self.assertEqual([c.args[0] for c in win._send_video_command.call_args_list], ['PRELOAD', 'PLAY'])
        win._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        self.assertEqual(len(win._send_video_command.call_args_list), 2)

    def test_second_vote_before_search_completion_waits_ready(self):
        win = self.window()
        self.start(win)
        win.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        self.assertEqual(len(win._send_video_command.call_args_list), 0)
        _complete(win, _videos('3:00', '3:00'))
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list], ['PRELOAD'])
        self.assertEqual(win.pending_play_video_id, 'video-0')
        win._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list], ['PRELOAD', 'PLAY'])

    def test_autoplay_off_keeps_early_preload_only(self):
        win = self.window(autoplay=False)
        self.start(win); _complete(win, _videos('3:00', '3:00'))
        win._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        win.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list], ['PRELOAD'])

    def test_disable_autoplay_after_preload_preserves_preload_retries(self):
        win = self.window()
        self.start(win); _complete(win, _videos('3:00', '3:00'))
        self.assertEqual(win._autoplay_active_video_id, 'video-0')
        win._preload_watch_video_id = 'video-0'
        win._preload_watch_video_data = _videos('3:00')[0]
        win.set_auto_play_enabled(False)
        self.assertEqual(win._autoplay_active_video_id, 'video-0')
        self.assertEqual(win._preload_watch_video_id, 'video-0')
        win._on_preload_retry_timeout()
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list],
                         ['PRELOAD', 'PRELOAD'])
        win._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        win.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list],
                         ['PRELOAD', 'PRELOAD'])

    def test_toggle_on_after_second_vote_off_does_not_retroactively_play(self):
        win = self.window()
        self.start(win); _complete(win, _videos('3:00', '3:00'))
        win.set_auto_play_enabled(False)
        win.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        win.set_auto_play_enabled(True)
        win._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list],
                         ['PRELOAD'])

    def test_switch_on_before_second_vote_enables_play(self):
        win = self.window(autoplay=False)
        self.start(win); _complete(win, _videos('3:00', '3:00'))
        win.set_auto_play_enabled(True)
        win.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        win._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list],
                         ['PRELOAD', 'PLAY'])

    def test_false_vote_is_removed_and_stale_ready_and_search_are_ignored(self):
        win = self.window()
        self.start(win); _complete(win, _videos('3:00', '3:00'))
        win.on_shazam_candidate_rejected(42, 'different-song')
        self.assertEqual(win.shazam_table_model.data, [])
        self.assertEqual(win.left_pane.model.rowCount(), 0)
        self.assertIsNone(win._shazam_preview)
        win._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        _complete(win, _videos('3:00', '3:00'))
        self.assertEqual(win.left_pane.model.rowCount(), 0)
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list], ['PRELOAD'])

    def test_tentative_fallback_is_still_preload_not_play(self):
        win = self.window()
        self.start(win); _complete(win, _videos('3:00', '3:00'))
        win._handle_player_feedback({'state': 'error', 'videoId': 'video-0'})
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list], ['PRELOAD', 'PRELOAD'])
        self.assertEqual(win._shazam_preview['video_id'], 'video-1')
        self.assertIsNone(win.pending_play_video_id)
        win._handle_player_feedback({'state': 'ready', 'videoId': 'video-1'})
        win.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list], ['PRELOAD', 'PRELOAD', 'PLAY'])

    def test_manual_play_after_first_vote_suppresses_automatic_play(self):
        win = self.window()
        self.start(win); _complete(win, _videos('3:00', '3:00'))
        win._cancel_autoplay_recovery('manual play')
        win._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        win.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list], ['PRELOAD'])

    def test_cancelled_queued_search_is_discarded(self):
        win = self.window()
        self.start(win)
        win._pending_search_args = ('Song B', 'Artist B', '', True, False, 'shazam', 42)
        win.on_shazam_candidate_rejected(42, 'wrong-first-vote')
        self.assertIsNone(win._pending_search_args)

    def test_two_preload_retries_fall_back_without_early_play(self):
        win = self.window()
        self.start(win); _complete(win, _videos('3:00', '3:00'))
        win._preload_watch_video_id = 'video-0'
        win._preload_watch_video_data = _videos('3:00')[0]
        win._on_preload_retry_timeout()
        win._on_preload_retry_timeout()
        win._on_preload_retry_timeout()
        self.assertEqual([x.args[0] for x in win._send_video_command.call_args_list],
                         ['PRELOAD', 'PRELOAD', 'PRELOAD', 'PRELOAD'])
        self.assertEqual(win._shazam_preview['video_id'], 'video-1')
        self.assertIsNone(win.pending_play_video_id)

    def test_commit_keeps_prepared_youtube_results_no_duplicate_search(self):
        win = self.window()
        self.start(win); _complete(win, _videos('3:00', '3:00'))
        win.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        confirmed_entry = ('2026-10-13 10:00:05', 'Song B', 'Artist B')
        win.shazam_service.get_history.return_value = [confirmed_entry]
        win.on_shazam_history_updated([confirmed_entry])
        self.assertEqual(len(win.shazam_table_model.data), 1)
        win.on_shazam_new_track_detected(confirmed_entry)
        self.assertEqual(win.search_youtube.call_count, 1)


if __name__ == '__main__':
    unittest.main()
