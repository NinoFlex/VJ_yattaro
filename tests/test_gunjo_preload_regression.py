"""2026-10-13 '群青' field-log replay: preserve an early PRELOAD on late confirmation.

Offline deterministic service+UI tests. Reproduce seq133 / missing seq134 /
seq135+136 and the original one-time YouTube PRELOAD. No browser/network.
"""
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from tests.test_webview_integration import ShazamService
from tests.test_latency_prefetch import ControlledPrefetch, raw_song
from tests import test_shazam_speculative_preload as vote_tests
from tests.test_autoplay_selection import _complete, _videos, WindowHarness


class GunjoPreloadFieldLogTests(unittest.TestCase):
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
        self.svc._next_temporal_sequence = 133
        self.broker = ControlledPrefetch()
        self.svc._metadata_prefetch = self.broker
        self.song = raw_song('1618070130')
        self.other = raw_song('1769302045')
        self.window = vote_tests.WindowVoteTests().window()
        self.window.shazam_service = self.svc
        self.svc.candidate_started.connect(self.window.on_shazam_candidate_started)
        self.svc.candidate_rejected.connect(self.window.on_shazam_candidate_rejected)
        self.svc.candidate_confirmed.connect(self.window.on_shazam_candidate_confirmed)
        self.svc.history_updated.connect(self.window.on_shazam_history_updated)
        self.svc.new_track_detected.connect(self.window.on_shazam_new_track_detected)

    def tearDown(self):
        self.svc.shutdown()
        self.clock.stop()
        self.tmp.cleanup()

    def vote(self, seq, raw=None, *, receipt=None):
        evidence = dict(raw or self.song)
        if receipt is not None:
            evidence['_received_monotonic'] = float(receipt)
        self.svc._handle_confirmed_group_result(
            seq % 4, seq, [seq % 4],
            [(evidence.get('title', ''), evidence.get('artist', ''), '')],
            [evidence],
        )

    def first_vote_and_cue(self):
        self.vote(133, receipt=100.0)
        self.now = 101.0
        key = self.svc._metadata_key(('shazam-song', '1618070130'))
        self.broker.finish(1, key, ('群青', '神山羊'))
        self.svc._handle_prefetch_finished(1, key, ('群青', '神山羊'), '')
        self.assertEqual(self.window.search_youtube.call_count, 1)
        self.assertEqual(self.window._shazam_preview.id, 133)
        self.window._active_search_candidate_id = 133
        _complete(self.window, _videos('3:00', '3:00'))
        if hasattr(self.window._send_video_command, 'call_args_list'):
            commands = [c.args[0] for c in self.window._send_video_command.call_args_list]
        else:
            commands = [c.args[0] for c in self.window.player_server.send_command.call_args_list]
        self.assertEqual(commands, ['PRELOAD'])
        self.assertEqual(self.window.shazam_table_model.rowCount(), 1)

    def confirm_from_out_of_order_pair(self, *, receipt135=103.0, receipt136=105.0,
                                       callback_time=106.0):
        self.now = receipt135
        self.vote(135, receipt=receipt135)  # seq134 stuck in another lane
        self.now = callback_time
        self.vote(136, receipt=receipt136)  # pair 135/136 bypasses seq134

    def assert_one_search_one_cue(self):
        self.assertNotIn((133, 'older-observation-bypassed'),
                         self.svc.candidate_rejected.calls)
        self.assertEqual([r[0] for r in self.svc.candidate_started.calls], [133])
        self.assertEqual([r[0] for r in self.svc.candidate_confirmed.calls], [133])
        self.assertEqual(self.svc._last_publication_candidate_id, 133)
        self.assertEqual(self.window._shazam_preview.id, 133)
        self.assertEqual(self.window.search_youtube.call_count, 1)
        self.assertEqual(self.svc.get_history()[0][1:], ('群青', '神山羊'))

    def test_original_log_sequence_preserves_early_cue_after_5_seconds(self):
        self.first_vote_and_cue()
        self.confirm_from_out_of_order_pair(callback_time=106.0)
        self.assert_one_search_one_cue()
        self.assertEqual([c.args[0] for c in self.window._send_video_command.call_args_list],
                         ['PRELOAD'])  # still no PLAY until browser ready
        self.window._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        self.assertEqual([c.args[0] for c in self.window._send_video_command.call_args_list],
                         ['PRELOAD', 'PLAY'])

    def test_ready_before_second_vote_plays_without_duplicate_cue(self):
        self.first_vote_and_cue()
        self.window._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        self.assertEqual([c.args[0] for c in self.window._send_video_command.call_args_list],
                         ['PRELOAD'])
        self.confirm_from_out_of_order_pair(callback_time=106.0)
        self.assert_one_search_one_cue()
        self.assertEqual([c.args[0] for c in self.window._send_video_command.call_args_list],
                         ['PRELOAD', 'PLAY'])

    def test_slow_qt_event_drain_must_not_invalidate_a_valid_pair(self):
        # Actual recognitions at 100/103/105; the GUI finally processes seq136
        # at time 126 (the QTimer timeout was also blocked). This used to
        # cancel the already-cued song because newest receipt is >15s old.
        self.first_vote_and_cue()
        self.confirm_from_out_of_order_pair(callback_time=126.0)
        self.assert_one_search_one_cue()
        self.window._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        self.assertEqual([c.args[0] for c in self.window._send_video_command.call_args_list],
                         ['PRELOAD', 'PLAY'])

    def test_real_request_ids_survive_temporal_bypass_and_play(self):
        # Unlike the lightweight transport mocks above, use the actual desktop
        # command path so that the browser READY is tied to its original PRELOAD
        # request ID. Recreating the preview would invalidate that token.
        w = self.window
        w._send_video_command = WindowHarness._send_video_command.__get__(w)
        w._current_track_info = {'title': '群青', 'artist': '神山羊', 'comment': ''}
        w._build_player_media_info = Mock(return_value={})
        w.player_server.send_command.side_effect = ['gunjo-cue-1', 'gunjo-play-1']
        self.first_vote_and_cue()
        self.assertEqual(w._preload_watch_request_id, 'gunjo-cue-1')
        self.confirm_from_out_of_order_pair(callback_time=126.0)
        self.assert_one_search_one_cue()
        self.assertEqual(w.player_server.send_command.call_count, 1)
        w._handle_player_feedback({'state': 'ready', 'videoId': 'video-0',
                                   'playerId': 'B', 'isCurrent': False,
                                   'requestId': 'different-cue'})
        self.assertEqual(w.player_server.send_command.call_count, 1)
        self.assertEqual(w._preload_watch_request_id, 'gunjo-cue-1')
        w._handle_player_feedback({'state': 'ready', 'videoId': 'video-0',
                                   'playerId': 'B', 'isCurrent': False,
                                   'requestId': 'gunjo-cue-1'})
        self.assertEqual(w._play_watch_request_id, 'gunjo-play-1')
        self.assertEqual([c.args[0] for c in w.player_server.send_command.call_args_list],
                         ['PRELOAD', 'PLAY'])
        w._handle_player_feedback({'state': 'playing', 'videoId': 'video-0',
                                   'playerId': 'B', 'isCurrent': True,
                                   'requestId': 'gunjo-play-1'})
        self.assertTrue(w.shazam_flow.find_candidate(133).playing_ack)

    def test_unconfirmed_vote_not_kept_indefinitely_even_if_same_id(self):
        self.first_vote_and_cue()
        self.confirm_from_out_of_order_pair(receipt135=127.0, receipt136=129.0,
                                            callback_time=131.0)
        self.assertIn((133, 'older-observation-bypassed'),
                      self.svc.candidate_rejected.calls)
        self.assertEqual([r[0] for r in self.svc.candidate_started.calls], [133, 135])
        self.assertEqual([r[0] for r in self.svc.candidate_confirmed.calls], [135])
        self.assertEqual(self.window.search_youtube.call_count, 2)

    def test_different_confirmed_identity_replaces_old_preview(self):
        self.first_vote_and_cue()
        self.now = 103.0
        self.vote(135, self.other, receipt=103.0)
        self.now = 106.0
        self.vote(136, self.other, receipt=105.0)
        self.assertIn((133, 'older-observation-bypassed'),
                      self.svc.candidate_rejected.calls)
        self.assertFalse(self.svc.candidate_confirmed.calls)  # ID-only new title still loading
        self.assertNotEqual(self.window._shazam_preview.id if self.window._shazam_preview else -1, 133)

    def test_running_build_tag_is_present(self):
        from app.version import SOURCE_BUILD_TAG
        self.assertEqual(SOURCE_BUILD_TAG, '2026-10-13-shazam-track-artist-v1')


if __name__ == '__main__':
    unittest.main()
