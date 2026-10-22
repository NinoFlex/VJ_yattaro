"""Production desktop flow: same-video replacement keeps strict feedback tokens.

The companion Node suite executes player.js's real feedback serializer. These
checks execute MainWindow's real handlers with mocked Qt, searches and transport.
"""
import unittest
from unittest.mock import Mock

from tests import test_shazam_speculative_preload as votes


class RepreloadFeedbackFlowTests(unittest.TestCase):
    def replaced_preview(self):
        w = votes.WindowVoteTests().window()
        w._send_video_command = votes.WindowHarness._send_video_command.__get__(w)
        w._current_track_info = {'title': 'New song', 'artist': 'Artist', 'comment': ''}
        w._build_player_media_info = Mock(return_value={})
        w.player_server.send_command.side_effect = ['old-cue', 'new-cue', 'new-play']
        w.on_shazam_candidate_started(59, ('', 'First candidate', 'Artist'))
        w._active_search_candidate_id = 59
        votes._complete(w, votes._videos('3:00', '3:00'))
        w.on_shazam_candidate_rejected(59, 'first-vote-expired')
        w.on_shazam_candidate_started(63, ('', 'New song', 'Artist'))
        w._active_search_candidate_id = 63
        votes._complete(w, votes._videos('3:00', '3:00'))
        return w

    def commands(self, w):
        return [c.args[0] for c in w.player_server.send_command.call_args_list]

    def ready(self, w, request):
        w._handle_player_feedback({'state': 'ready', 'videoId': 'video-0',
                                   'playerId': 'B', 'requestId': request, 'isCurrent': False})

    def test_confirmed_replacement_waits_for_new_token_then_plays_once(self):
        w = self.replaced_preview()
        w.on_shazam_candidate_confirmed(63, ('', 'New song', 'Artist'))
        self.ready(w, 'old-cue')
        self.assertEqual(self.commands(w), ['PRELOAD', 'PRELOAD'])
        self.ready(w, 'new-cue')
        self.assertEqual(self.commands(w), ['PRELOAD', 'PRELOAD', 'PLAY'])
        self.assertEqual(w._play_watch_request_id, 'new-play')
        self.ready(w, 'new-cue')
        self.ready(w, 'old-cue')
        self.assertEqual(self.commands(w), ['PRELOAD', 'PRELOAD', 'PLAY'])

    def test_replacement_ready_before_confirmation_cannot_play_early(self):
        w = self.replaced_preview()
        self.ready(w, 'new-cue')
        self.assertEqual(self.commands(w), ['PRELOAD', 'PRELOAD'])
        w.on_shazam_candidate_confirmed(63, ('', 'New song', 'Artist'))
        self.assertEqual(self.commands(w), ['PRELOAD', 'PRELOAD', 'PLAY'])
        self.assertEqual(w._play_watch_request_id, 'new-play')


if __name__ == '__main__':
    unittest.main()
