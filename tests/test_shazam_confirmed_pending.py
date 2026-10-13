"""Races between B's confirmed delayed PLAY and C's speculative YouTube search."""
import unittest
from tests.test_shazam_speculative_preload import WindowVoteTests
from tests.test_autoplay_selection import _complete, _videos


class ConfirmedPlaybackSurvivesFirstVoteTests(unittest.TestCase):
    def setUp(self):
        self.fix = WindowVoteTests()
        self.win = self.fix.window()
        self.fix.start(self.win)
        _complete(self.win, _videos('3:00', '3:00'))
        self.win.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))

    def _c_first_vote(self):
        w = self.win
        w.on_shazam_candidate_rejected(42, 'new-candidate')
        self.assertEqual(w._confirmed_shazam_pending['id'], 42)
        self.assertEqual(w.pending_play_video_id, 'video-0')
        w.on_shazam_candidate_started(43, ('', 'Song C', 'Artist C'))
        w._active_search_candidate_id = 43
        return w

    def test_c_wrong_first_vote_does_not_cancel_confirmed_b(self):
        w = self._c_first_vote()
        w.on_shazam_candidate_rejected(43, 'first-vote-expired')
        self.assertEqual(w.pending_play_video_id, 'video-0')
        self.assertEqual(w._confirmed_shazam_pending['id'], 42)
        w._handle_player_feedback({'state': 'ready', 'videoId': 'video-0', 'playerId': 'B'})
        self.assertEqual([x.args[0] for x in w._send_video_command.call_args_list],
                         ['PRELOAD', 'PLAY'])
        w._handle_player_feedback({'state': 'playing', 'videoId': 'video-0',
                                   'isCurrent': True, 'playerId': 'B'})
        self.assertIsNone(w._confirmed_shazam_pending)

    def test_c_search_completes_first_b_keeps_cued_slot(self):
        w = self._c_first_vote()
        self.assertEqual(w._autoplay_active_video_id, 'video-0')
        _complete(w, _videos('3:00', '3:00'))
        self.assertEqual([x.args[0] for x in w._send_video_command.call_args_list], ['PRELOAD'])
        self.assertEqual(w._shazam_preview['deferred_index'], 0)
        w._handle_player_feedback({'state': 'ready', 'videoId': 'video-0', 'playerId': 'B'})
        self.assertEqual([x.args[0] for x in w._send_video_command.call_args_list],
                         ['PRELOAD', 'PLAY'])
        w._handle_player_feedback({'state': 'playing', 'videoId': 'video-0',
                                   'isCurrent': True, 'playerId': 'B'})
        self.assertEqual([x.args[0] for x in w._send_video_command.call_args_list],
                         ['PRELOAD', 'PLAY', 'PRELOAD'])
        self.assertFalse(w._shazam_preview['confirmed'])
        self.assertFalse(w._shazam_preview['ready'])

    def test_c_second_vote_can_supersede_b(self):
        w = self._c_first_vote()
        _complete(w, _videos('3:00', '3:00'))
        w.on_shazam_candidate_confirmed(43, ('', 'Song C', 'Artist C'))
        self.assertIsNone(w._confirmed_shazam_pending)
        self.assertEqual([x.args[0] for x in w._send_video_command.call_args_list],
                         ['PRELOAD', 'PRELOAD'])
        w._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        self.assertIn('PLAY', [x.args[0] for x in w._send_video_command.call_args_list])

    def test_b_search_completes_after_c1_b_still_gets_results(self):
        w = self.fix.window()
        self.fix.start(w)
        w.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        w.on_shazam_candidate_rejected(42, 'new-candidate')
        w.on_shazam_candidate_started(43, ('', 'Song C', 'Artist C'))
        # The C search is queued while B's worker is still finishing.
        w._pending_search_args = ('Song C', 'Artist C', '', True, False, 'shazam', 43)
        w._active_search_candidate_id = 42
        _complete(w, _videos('3:00','3:00'))
        self.assertEqual([x.args[0] for x in w._send_video_command.call_args_list], ['PRELOAD'])
        self.assertEqual(w._confirmed_shazam_pending['video_id'], 'video-0')
        self.assertEqual(w.pending_play_video_id, 'video-0')

    def test_stale_c_result_not_resurrected_after_c_rejection(self):
        w = self._c_first_vote()
        w.on_shazam_candidate_rejected(43, 'wrong-first-vote')
        _complete(w, _videos('3:00','3:00'))
        self.assertEqual([x.args[0] for x in w._send_video_command.call_args_list], ['PRELOAD'])
        w._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        self.assertEqual(w._send_video_command.call_args.args[0], 'PLAY')

    def test_c_preload_deferred_when_b_waiting_for_playing_ack(self):
        w = self.win
        w._handle_player_feedback({'state': 'ready', 'videoId': 'video-0'})
        self.assertEqual(w._send_video_command.call_args.args[0], 'PLAY')
        w.on_shazam_candidate_rejected(42, 'new-candidate')
        w.on_shazam_candidate_started(43, ('', 'Song C', 'Artist C'))
        w._active_search_candidate_id = 43
        _complete(w, _videos('3:00','3:00'))
        self.assertEqual([x.args[0] for x in w._send_video_command.call_args_list],
                         ['PRELOAD', 'PLAY'])
        w._handle_player_feedback({'state': 'playing', 'videoId': 'video-0',
                                   'isCurrent': True, 'playerId': 'B'})
        self.assertEqual(w._send_video_command.call_args.args[0], 'PRELOAD')


if __name__ == '__main__':
    unittest.main()
