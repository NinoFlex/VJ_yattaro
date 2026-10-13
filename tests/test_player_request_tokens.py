"""Desktop/browser command IDs: reject stale feedback, retry the next ranked video."""
import unittest
from unittest.mock import Mock
from tests.test_autoplay_selection import _window, _videos, _complete, WindowHarness


class PlaybackRequestTokenTests(unittest.TestCase):
    def _real_command_window(self):
        w = _window({'auto_play_top_result': True})
        w._send_video_command = WindowHarness._send_video_command.__get__(w)
        w._current_track_info = {'title':'Song','artist':'Artist','comment':''}
        w._build_player_media_info = Mock(return_value={})
        # One ID per queued command, as emitted by the real command server.
        w.player_server.send_command.side_effect = [
            'cue-1', 'play-1', 'cue-2', 'play-2', 'cue-3', 'play-3'
        ]
        return w

    def test_other_player_with_identical_video_id_cannot_disarm_preload(self):
        w=self._real_command_window();_complete(w,_videos('3:00','3:00'))
        self.assertEqual(w._preload_watch_request_id,'cue-1')
        w._handle_player_feedback({'state':'ready','videoId':'video-0',
                                   'playerId':'A','requestId':'another-cue','isCurrent':True})
        self.assertEqual(w._preload_watch_video_id,'video-0')
        self.assertEqual(w.pending_play_video_id,'video-0')
        self.assertEqual(w.player_server.send_command.call_count,1)
        w._handle_player_feedback({'state':'ready','videoId':'video-0',
                                   'playerId':'B','requestId':'cue-1','isCurrent':False})
        self.assertIsNone(w._preload_watch_video_id)
        self.assertEqual(w.player_server.send_command.call_count,2)
        self.assertEqual(w._play_watch_request_id,'play-1')

    def test_old_playing_does_not_cancel_active_play_watch(self):
        w=self._real_command_window();_complete(w,_videos('3:00','3:00'))
        w._handle_player_feedback({'state':'ready','videoId':'video-0',
                                   'playerId':'B','requestId':'cue-1','isCurrent':False})
        w._handle_player_feedback({'state':'playing','videoId':'video-0',
                                   'playerId':'A','requestId':'old-play','isCurrent':True})
        self.assertEqual(w._play_watch_video_id,'video-0')
        self.assertEqual(w._autoplay_active_video_id,'video-0')
        w._handle_player_feedback({'state':'playing','videoId':'video-0',
                                   'playerId':'B','requestId':'play-1','isCurrent':True})
        self.assertIsNone(w._play_watch_video_id)
        self.assertIsNone(w._autoplay_active_video_id)

    def test_browser_play_ready_timeout_auto_cues_second(self):
        w=self._real_command_window();_complete(w,_videos('3:00','3:00'))
        w._handle_player_feedback({'state':'ready','videoId':'video-0',
                                   'playerId':'B','requestId':'cue-1','isCurrent':False})
        w._handle_player_feedback({'state':'error','videoId':'video-0','playerId':'B',
                                   'requestId':'play-1','errorCode':'play-ready-timeout'})
        self.assertEqual(w._autoplay_active_video_id,'video-1')
        self.assertEqual([c.args[0] for c in w.player_server.send_command.call_args_list],
                         ['PRELOAD','PLAY','PRELOAD'])
        self.assertEqual(w._preload_watch_request_id,'cue-2')

    def test_desktop_play_start_watchdog_also_falls_back(self):
        w=self._real_command_window();_complete(w,_videos('3:00','3:00'))
        w._handle_player_feedback({'state':'ready','videoId':'video-0',
                                   'playerId':'B','requestId':'cue-1','isCurrent':False})
        w._play_start_timer.start.assert_called_with(30000)
        w._on_play_start_timeout()
        self.assertEqual(w._autoplay_active_video_id,'video-1')
        self.assertEqual(w._preload_watch_request_id,'cue-2')
        self.assertIsNone(w._play_watch_video_id)
        self.assertEqual([c.args[0] for c in w.player_server.send_command.call_args_list],
                         ['PRELOAD','PLAY','PRELOAD'])

    def test_late_error_after_rank_two_started_must_not_fallback_again(self):
        w=self._real_command_window();_complete(w,_videos('3:00','3:00'))
        w._handle_player_feedback({'state':'ready','videoId':'video-0',
                                   'playerId':'B','requestId':'cue-1','isCurrent':False})
        w._on_play_start_timeout()
        w._handle_player_feedback({'state':'error','videoId':'video-0',
                                   'playerId':'B','requestId':'play-1','errorCode':'play-start-timeout'})
        self.assertEqual(w._autoplay_active_video_id,'video-1')
        self.assertEqual(w.player_server.send_command.call_count,3)


if __name__=='__main__':
    unittest.main()
