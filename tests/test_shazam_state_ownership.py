"""Canonical B/C ownership under slow searches, duplicate videos and late events.

This module tests both the Qt-free state model and MainWindow's actual
command/feedback methods with an emulated HTTP command bridge.
"""
from dataclasses import replace
import unittest
from unittest.mock import Mock

from app.models.playback_state import PlaybackPhase
from app.models.search_request import SearchPhase, SearchRequest
from app.models.shazam_state import ShazamPlaybackState
from app.models.track_candidate import TrackCandidate
from tests.test_autoplay_selection import WindowHarness, _complete, _videos
from tests.test_shazam_speculative_preload import WindowVoteTests


class StateOwnershipTests(unittest.TestCase):
    def new_b(self):
        flow = ShazamPlaybackState()
        b = flow.begin_vote(42, ('', 'B title', 'B artist'))
        flow.supersede_with_confirmation(42)
        b.play_authorized = True
        attempt = flow.begin_preload(b, 'same-video')
        flow.register_cue_request('same-video', 'b-cue-1')
        attempt.preload_retries = 1
        flow.preserve_confirmed(b)
        return flow, b, attempt

    def test_first_c_vote_keeps_b_attempt_and_tokens(self):
        flow, b, attempt = self.new_b()
        before = replace(attempt)
        c = flow.begin_vote(43, ('', 'C title', 'C artist'))
        self.assertIsNone(flow.begin_preload(c, 'same-video'))
        flow.reject_vote(43)
        self.assertIs(flow.confirmed_awaiting_play(), b)
        self.assertIs(flow.active_attempt, attempt)
        self.assertEqual(attempt, before)
        self.assertEqual(attempt.candidate_id, 42)

    def test_late_c_search_can_be_cancelled_without_touching_b(self):
        flow, b, attempt = self.new_b()
        c = flow.begin_vote(43, ('', 'C title', 'C artist'))
        queued = SearchRequest('C title', 'C artist', candidate_id=43)
        flow.queue_search(queued)
        self.assertIs(c.search, queued)
        flow.reject_vote(43)
        self.assertEqual(queued.phase, SearchPhase.CANCELLED)
        self.assertIsNone(flow.queued_search)
        self.assertIs(flow.active_attempt, attempt)
        self.assertIs(flow.confirmed_awaiting_play(), b)

    def test_b_ready_requires_exact_b_cue_token_and_remains_owned(self):
        flow, b, attempt = self.new_b()
        flow.begin_vote(43, ('', 'C title', 'C artist'))
        self.assertIsNone(flow.accept_ready('same-video', 'c-cue'))
        self.assertIsNone(flow.accept_ready('other-video', 'b-cue-1'))
        self.assertEqual(attempt.phase, PlaybackPhase.PRELOADING)
        self.assertIs(flow.accept_ready('same-video', 'b-cue-1'), b)
        self.assertTrue(b.ready)
        self.assertEqual(attempt.phase, PlaybackPhase.READY)

    def test_cannot_play_tentative_even_if_youtube_id_matches_b(self):
        flow, b, attempt = self.new_b()
        c = flow.begin_vote(43, ('', 'C title', 'C artist'))
        flow.register_play_request('same-video', 'b-play')
        self.assertIsNone(flow.accept_playing('same-video', 'c-play'))
        self.assertIs(flow.accept_playing('same-video', 'b-play'), b)
        self.assertTrue(b.playing_ack)
        self.assertFalse(c.playing_ack)
        self.assertIsNone(flow.confirmed_awaiting_play())

    def test_first_c_vote_does_not_supersede_b_second_vote_does(self):
        flow, b, attempt = self.new_b()
        c = flow.begin_vote(43, ('', 'C title', 'C artist'))
        self.assertIs(flow.confirmed_awaiting_play(), b)
        flow.supersede_with_confirmation(43)
        self.assertTrue(c.confirmed)
        self.assertIsNone(flow.confirmed_pending)
        self.assertEqual(attempt.phase, PlaybackPhase.CANCELLED)
        new_attempt = flow.begin_preload(c, 'other-video')
        self.assertIs(flow.active_attempt, new_attempt)
        self.assertEqual(new_attempt.candidate_id, 43)

    def test_b_already_playing_is_not_cancelled_by_c_cue(self):
        flow, b, attempt = self.new_b()
        flow.register_play_request('same-video', 'b-play')
        self.assertIs(flow.accept_playing('same-video', 'b-play'), b)
        c = flow.begin_vote(43, ('', 'C title', 'C artist'))
        new_attempt = flow.begin_preload(c, 'another-video')
        self.assertIs(flow.active_attempt, new_attempt)
        self.assertEqual(attempt.phase, PlaybackPhase.PLAYING)


class MainWindowBToCIsolationTests(unittest.TestCase):
    def setUp(self):
        fixture = WindowVoteTests()
        self.w = fixture.window()
        self.w._send_video_command = WindowHarness._send_video_command.__get__(self.w)
        self.w._current_track_info = {'title': 'B', 'artist': 'Singer', 'comment': ''}
        self.w._build_player_media_info = Mock(return_value={})
        self.w.player_server.send_command.side_effect = ['b-cue', 'b-play', 'c-cue', 'c-play']
        self.w.on_shazam_candidate_started(42, ('', 'B', 'Singer'))
        self.w._active_search_candidate_id = 42
        _complete(self.w, [dict(video_id='same-video', title='B', duration='3:00'),
                           dict(video_id='fallback-b', title='B 2', duration='3:00')])
        self.w.on_shazam_candidate_confirmed(42, ('', 'B', 'Singer'))
        self.w.on_shazam_candidate_rejected(42, 'new-candidate')
        self.b = self.w._confirmed_shazam_pending
        self.attempt = self.w.shazam_flow.active_attempt
        self.assertEqual(self.w._preload_watch_request_id, 'b-cue')

    def c_search(self, video_id='c-video'):
        w = self.w
        w.on_shazam_candidate_started(43, ('', 'C', 'Singer'))
        w._active_search_candidate_id = 43
        _complete(w, [dict(video_id=video_id, title='C', duration='3:00'),
                      dict(video_id='c-fallback', title='C 2', duration='3:00')])

    def test_c_search_does_not_change_b_watchdog_even_if_same_video(self):
        self.c_search(video_id='same-video')
        self.assertEqual(self.w.player_server.send_command.call_count, 1)
        self.assertIs(self.w.shazam_flow.active_attempt, self.attempt)
        self.assertEqual(self.w._preload_watch_request_id, 'b-cue')
        self.assertEqual(self.w.pending_play_video_id, 'same-video')
        self.assertEqual(self.w._autoplay_active_video_id, 'same-video')
        self.assertEqual(self.w._shazam_preview.deferred_index, 0)
        self.assertEqual(self.w._shazam_preview.attempt, None)

    def test_c_rejection_preserves_b_request_and_only_b_can_play(self):
        self.c_search(video_id='same-video')
        self.w.on_shazam_candidate_rejected(43, 'wrong-first-vote')
        self.assertIsNone(self.w._shazam_preview)
        self.assertIs(self.w._confirmed_shazam_pending, self.b)
        self.assertEqual(self.w._preload_watch_request_id, 'b-cue')
        self.w._handle_player_feedback({'state':'ready', 'videoId':'same-video',
                                        'playerId':'B', 'requestId':'c-cue'})
        self.assertEqual(self.w.player_server.send_command.call_count, 1)
        self.w._handle_player_feedback({'state':'ready', 'videoId':'same-video',
                                        'playerId':'B', 'requestId':'b-cue'})
        self.assertEqual(self.w.player_server.send_command.call_count, 2)
        self.assertEqual(self.attempt.phase, PlaybackPhase.PLAY_REQUESTED)
        self.assertEqual(self.attempt.play_request_id, 'b-play')
        self.w._handle_player_feedback({'state':'playing', 'videoId':'same-video',
                                        'playerId':'B', 'requestId':'c-play', 'isCurrent':True})
        self.assertFalse(self.b.playing_ack)
        self.w._handle_player_feedback({'state':'playing', 'videoId':'same-video',
                                        'playerId':'B', 'requestId':'b-play', 'isCurrent':True})
        self.assertTrue(self.b.playing_ack)
        self.assertEqual(self.attempt.phase, PlaybackPhase.PLAYING)
        self.assertIsNone(self.w._confirmed_shazam_pending)

    def test_b_retry_during_c_first_vote_keeps_b_owner_and_new_request_id(self):
        self.w.player_server.send_command.side_effect = ['b-retry', 'b-play']
        self.c_search(video_id='same-video')
        self.w._on_preload_retry_timeout()
        self.assertEqual(self.w._preload_watch_request_id, 'b-retry')
        self.assertEqual(self.attempt.preload_retries, 1)
        self.assertEqual(self.attempt.preload_request_id, 'b-retry')
        self.w.on_shazam_candidate_rejected(43, 'wrong-first-vote')
        self.assertIs(self.w.shazam_flow.active_attempt, self.attempt)
        self.w._handle_player_feedback({'state': 'ready', 'videoId': 'same-video',
                                        'requestId': 'b-cue', 'playerId': 'B'})
        self.assertEqual(self.w.player_server.send_command.call_count, 2)
        self.w._handle_player_feedback({'state': 'ready', 'videoId': 'same-video',
                                        'requestId': 'b-retry', 'playerId': 'B'})
        self.assertEqual(self.w.player_server.send_command.call_count, 3)
        self.assertEqual(self.attempt.play_request_id, 'b-play')

    def test_b_playing_ack_releases_c_deferred_cue_without_rewriting_b(self):
        # B was READY and its PLAY is in progress when C first appears.
        self.w._handle_player_feedback({'state': 'ready', 'videoId': 'same-video',
                                        'requestId': 'b-cue', 'playerId': 'B'})
        self.assertEqual(self.attempt.phase, PlaybackPhase.PLAY_REQUESTED)
        self.c_search(video_id='c-video')
        self.assertEqual(self.w.player_server.send_command.call_count, 2)
        self.w._handle_player_feedback({'state': 'playing', 'videoId': 'same-video',
                                        'requestId': 'b-play', 'playerId': 'B',
                                        'isCurrent': True})
        self.assertEqual(self.attempt.phase, PlaybackPhase.PLAYING)
        self.assertTrue(self.b.playing_ack)
        self.assertIsNone(self.w._confirmed_shazam_pending)
        self.assertEqual(self.w.player_server.send_command.call_count, 3)
        self.assertEqual(self.w.shazam_flow.active_attempt.candidate_id, 43)
        self.assertEqual(self.w.shazam_flow.active_attempt.video_id, 'c-video')
        self.assertFalse(self.w._shazam_preview.confirmed)

    def test_c_second_vote_supersedes_b_but_first_vote_never_does(self):
        # B has only queued its cue; there has not yet been a B PLAY command.
        self.w.player_server.send_command.side_effect = ['c-cue', 'c-play']
        self.c_search(video_id='c-video')
        self.assertIs(self.w.shazam_flow.active_attempt, self.attempt)
        self.w.on_shazam_candidate_confirmed(43, ('', 'C', 'Singer'))
        self.assertEqual(self.w.player_server.send_command.call_count, 2)
        self.assertEqual(self.attempt.phase, PlaybackPhase.CANCELLED)
        self.assertEqual(self.w.shazam_flow.active_attempt.candidate_id, 43)
        self.assertEqual(self.w.shazam_flow.active_attempt.video_id, 'c-video')
        self.assertEqual(self.w._preload_watch_request_id, 'c-cue')


if __name__ == '__main__':
    unittest.main()
