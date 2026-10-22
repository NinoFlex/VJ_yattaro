"""Queue races: a confirmed B lookup cannot be evicted by tentative C.

These tests use the actual main.py queue/slot methods without creating Qt,
network requests, or a browser. The fake QThread permits deliberately late
finished/results signals and the single-slot timer is advanced manually.
"""
import sys
import types
import unittest
from unittest.mock import Mock, patch

from app.models.search_request import SearchPhase, SearchRequest
from app.models.shazam_state import ShazamPlaybackState
from tests.test_autoplay_selection import _methods, _videos, _complete, WindowHarness
from tests.test_shazam_speculative_preload import WindowVoteTests

for name, func in _methods('MainWindow', [
    'search_youtube', '_on_search_finished', '_execute_pending_search',
]).items():
    func.__globals__['SearchPhase'] = SearchPhase
    setattr(WindowHarness, name, func)

# Existing tests extract MainWindow callbacks by AST without module imports.
WindowHarness.on_youtube_search_completed.__globals__['SearchPhase'] = SearchPhase


class ModelSearchQueueTests(unittest.TestCase):
    def confirmed_b_with_queued_search(self):
        flow = ShazamPlaybackState()
        b = flow.begin_vote(42, ('', 'Song B', 'Artist B'))
        request_b = SearchRequest('Song B', 'Artist B', candidate_id=42,
                                  from_list=True, source_mode='shazam')
        flow.queue_search(request_b)
        flow.supersede_with_confirmation(42)
        flow.preserve_confirmed(b)
        return flow, b, request_b

    def first_c(self, flow, candidate_id=43):
        c = flow.begin_vote(candidate_id, ('', 'Song C', 'Artist C'))
        request_c = SearchRequest('Song C', 'Artist C', candidate_id=candidate_id,
                                  from_list=True, source_mode='shazam')
        flow.queue_search(request_c)
        return c, request_c

    def test_first_vote_does_not_evict_queued_confirmed_b(self):
        flow, b, request_b = self.confirmed_b_with_queued_search()
        c, request_c = self.first_c(flow)
        self.assertIs(flow.queued_search, request_b)
        self.assertEqual(request_b.phase, SearchPhase.QUEUED)
        self.assertTrue(request_b.confirmed)
        self.assertIs(flow.speculative_search, request_c)
        self.assertIs(c.search, request_c)
        self.assertIs(flow.confirmed_awaiting_play(), b)

    def test_rejected_c_leaves_b_exactly_untouched(self):
        flow, _, request_b = self.confirmed_b_with_queued_search()
        _, request_c = self.first_c(flow)
        flow.reject_vote(43)
        self.assertEqual(request_c.phase, SearchPhase.CANCELLED)
        self.assertIs(flow.queued_search, request_b)
        self.assertEqual(request_b.phase, SearchPhase.QUEUED)
        self.assertIsNone(flow.speculative_search)

    def test_confirmed_c_replaces_unstarted_b_only_at_second_vote(self):
        flow, _, request_b = self.confirmed_b_with_queued_search()
        c, request_c = self.first_c(flow)
        flow.supersede_with_confirmation(c.id)
        self.assertIs(flow.queued_search, request_c)
        self.assertTrue(request_c.confirmed)
        self.assertIsNone(flow.speculative_search)
        self.assertEqual(request_b.phase, SearchPhase.CANCELLED)
        self.assertIsNone(flow.confirmed_awaiting_play())

    def test_dispatch_order_b_then_c(self):
        flow, _, request_b = self.confirmed_b_with_queued_search()
        _, request_c = self.first_c(flow)
        self.assertIs(flow.pop_queued_search(), request_b)
        self.assertIs(flow.queued_search, request_c)
        self.assertEqual(request_b.phase, SearchPhase.QUEUED)
        self.assertIs(flow.pop_queued_search(), request_c)
        self.assertIsNone(flow.queued_search)

    def test_last_speculative_vote_wins_without_cancelling_b(self):
        flow, _, request_b = self.confirmed_b_with_queued_search()
        _, request_c = self.first_c(flow)
        flow.reject_vote(43)
        d = flow.begin_vote(44, ('', 'Song D', 'Artist D'))
        request_d = SearchRequest('Song D', 'Artist D', candidate_id=d.id)
        flow.queue_search(request_d)
        self.assertIs(flow.queued_search, request_b)
        self.assertIs(flow.speculative_search, request_d)
        self.assertEqual(request_c.phase, SearchPhase.CANCELLED)

    def test_running_b_is_cancelled_by_confirmed_c_but_not_first_vote(self):
        flow = ShazamPlaybackState()
        b = flow.begin_vote(42, ('', 'B', 'Artist'))
        flow.supersede_with_confirmation(42)
        request_b = SearchRequest('B', 'Artist', candidate_id=42, confirmed=True)
        flow.register_search(request_b)
        flow.preserve_confirmed(b)
        c, request_c = self.first_c(flow)
        self.assertEqual(request_b.phase, SearchPhase.RUNNING)
        flow.supersede_with_confirmation(c.id)
        self.assertEqual(request_b.phase, SearchPhase.CANCELLED)
        self.assertIs(flow.queued_search, request_c)
        self.assertTrue(request_c.confirmed)

    def test_manual_explicit_search_keeps_previous_latest_wins_behavior(self):
        flow, _, request_b = self.confirmed_b_with_queued_search()
        _, request_c = self.first_c(flow)
        manual = SearchRequest('Manual title', '', candidate_id=None)
        flow.queue_search(manual)
        self.assertIs(flow.queued_search, manual)
        self.assertEqual(request_b.phase, SearchPhase.CANCELLED)
        self.assertEqual(request_c.phase, SearchPhase.CANCELLED)


class FakeWorker:
    def __init__(self, title):
        self.title = title
        self.running = False
        self.search_error = Mock()
        self.finished = Mock()

    def isRunning(self):
        return self.running

    def start(self):
        self.running = True

    def abort(self):
        # A synchronous HTTP request can remain running after cancellation.
        self.aborted = True


class UIQueuedSearchTests(unittest.TestCase):
    def setUp(self):
        self.w = WindowVoteTests().window()
        # WindowVoteTests replaces search_youtube with Mock to test votes;
        # restore the real method here to exercise the search queue itself.
        self.w.search_youtube = WindowHarness.search_youtube.__get__(self.w)
        self.w.youtube_search_thread = FakeWorker('A')
        self.w.youtube_search_thread.start()
        self.w._pending_search_timer = Mock()
        self.w._set_searching_state = Mock()
        self.w._set_youtube_search_error = Mock()
        self.w.on_youtube_search_error = Mock()
        self.w._youtube_search_error_message = ''
        self.w._current_track_info = {}
        self.w.sender = lambda: self.w.youtube_search_thread
        self.svc = Mock()
        self.svc.is_configured.return_value = True
        self.svc.create_search_queries_from_track.side_effect = (
            lambda title, *_args, **_kwargs: [title])
        self.workers = []

        def new_worker(query, _callback, **_kwargs):
            worker = FakeWorker(query)
            self.workers.append(worker)
            return worker

        self.svc.search_videos.side_effect = new_worker
        youtube_module = types.ModuleType('app.services.youtube_service')
        youtube_module.YouTubeService = lambda: self.svc
        self.module_patch = patch.dict(sys.modules,
                                       {'app.services.youtube_service': youtube_module})
        self.module_patch.start()

    def tearDown(self):
        self.module_patch.stop()

    def vote_b_then_c(self):
        w = self.w
        w.on_shazam_candidate_started(42, ('', 'Song B', 'Artist B'))
        b_request = w.shazam_flow.queued_search
        w.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        w.on_shazam_candidate_rejected(42, 'new-candidate')
        w.on_shazam_candidate_started(43, ('', 'Song C', 'Artist C'))
        c_request = w.shazam_flow.speculative_search
        return b_request, c_request

    def finish_current(self):
        w = self.w
        w.youtube_search_thread.running = False
        w._on_search_finished()

    def test_pending_b_stays_in_ui_tuple_until_c_confirmed(self):
        b_request, c_request = self.vote_b_then_c()
        self.assertIs(self.w.shazam_flow.queued_search, b_request)
        self.assertEqual(self.w._pending_search_args[0], 'Song B')
        self.assertIs(self.w.shazam_flow.speculative_search, c_request)
        self.assertEqual(self.w.shazam_flow.confirmed_pending.id, 42)

    def test_after_slow_a_finishes_b_runs_before_c_and_preloads(self):
        b_request, c_request = self.vote_b_then_c()
        self.finish_current()
        self.w._pending_search_timer.start.assert_called_with(1000)
        self.w._execute_pending_search()
        self.assertEqual([w.title for w in self.workers], ['Song B'])
        self.assertIs(self.w.shazam_flow.active_search, b_request)
        self.assertIs(self.w.shazam_flow.queued_search, c_request)
        self.assertEqual(self.w._pending_search_args[0], 'Song C')
        self.assertEqual(b_request.phase, SearchPhase.RUNNING)
        _complete(self.w, _videos('3:00', '3:00'))
        self.assertEqual(self.w._confirmed_shazam_pending.video_id, 'video-0')
        self.assertEqual(self.w._send_video_command.call_args.args[0], 'PRELOAD')
        self.finish_current()
        self.w._execute_pending_search()
        self.assertEqual([w.title for w in self.workers], ['Song B', 'Song C'])
        self.assertIs(self.w.shazam_flow.active_search, c_request)

    def test_reject_c_before_a_finishes_never_loses_b(self):
        b_request, c_request = self.vote_b_then_c()
        self.w.on_shazam_candidate_rejected(43, 'wrong-vote')
        self.assertIs(self.w.shazam_flow.queued_search, b_request)
        self.assertEqual(c_request.phase, SearchPhase.CANCELLED)
        self.finish_current()
        self.w._execute_pending_search()
        self.assertEqual([x.title for x in self.workers], ['Song B'])

    def test_confirm_c_before_a_finishes_supersedes_queued_b(self):
        b_request, c_request = self.vote_b_then_c()
        self.w.on_shazam_candidate_confirmed(43, ('', 'Song C', 'Artist C'))
        self.assertEqual(b_request.phase, SearchPhase.CANCELLED)
        self.assertIs(self.w.shazam_flow.queued_search, c_request)
        self.assertEqual(self.w._pending_search_args[0], 'Song C')
        self.finish_current()
        self.w._execute_pending_search()
        self.assertEqual([x.title for x in self.workers], ['Song C'])

    def test_confirm_c_after_b_started_cancels_late_b_result(self):
        b_request, c_request = self.vote_b_then_c()
        self.finish_current()
        self.w._execute_pending_search()
        self.w.on_shazam_candidate_confirmed(43, ('', 'Song C', 'Artist C'))
        self.assertEqual(b_request.phase, SearchPhase.CANCELLED)
        _complete(self.w, _videos('3:00', '3:00'))
        self.assertEqual(len(self.w._send_video_command.call_args_list), 0)
        self.finish_current()
        self.w._execute_pending_search()
        self.assertEqual([x.title for x in self.workers], ['Song B', 'Song C'])

    def test_finished_worker_without_finished_signal_does_not_allow_c_overtake(self):
        w = self.w
        w.on_shazam_candidate_started(42, ('', 'Song B', 'Artist B'))
        w.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        w.on_shazam_candidate_rejected(42, 'new-candidate')
        w.youtube_search_thread.running = False  # finished signal not yet delivered
        w.on_shazam_candidate_started(43, ('', 'Song C', 'Artist C'))
        self.assertEqual([x.title for x in self.workers], [])
        self.assertEqual(w._pending_search_args[0], 'Song B')
        self.assertEqual(w.shazam_flow.speculative_search.candidate_id, 43)
        w._on_search_finished()
        w._execute_pending_search()
        self.assertEqual([x.title for x in self.workers], ['Song B'])

    def test_autoplay_off_still_preserves_confirmed_b_search(self):
        # Search priority is based on *confirmation*, not PLAY permission.
        w = self.w
        w.auto_play_top_result = False
        b_request, c_request = self.vote_b_then_c()
        self.assertIsNone(w._confirmed_shazam_awaiting_play())
        self.assertIs(w.shazam_flow.queued_search, b_request)
        self.finish_current()
        w._execute_pending_search()
        self.assertEqual([x.title for x in self.workers], ['Song B'])
        _complete(w, _videos('3:00', '3:00'))
        self.assertEqual(w._send_video_command.call_args.args[0], 'PRELOAD')
        self.assertNotIn('PLAY', [x.args[0] for x in w._send_video_command.call_args_list])

    def test_late_b_result_from_previous_worker_cannot_look_like_c_result(self):
        b_request, c_request = self.vote_b_then_c()
        self.finish_current()
        self.w._execute_pending_search()       # B worker
        b_worker = self.w.youtube_search_thread
        self.w.on_shazam_candidate_confirmed(43, ('', 'Song C', 'Artist C'))
        self.finish_current()
        self.w._execute_pending_search()       # C worker
        c_worker = self.w.youtube_search_thread
        self.assertIsNot(b_worker, c_worker)
        self.assertIs(self.w.shazam_flow.active_search, c_request)

        # Qt can deliver an already-posted signal after the next worker starts.
        self.w.sender = lambda: b_worker
        _complete(self.w, [dict(video_id='stale-b', title='B', duration='3:00')])
        self.assertEqual(self.w._send_video_command.call_count, 0)
        self.assertIsNone(self.w._youtube_results_owner_candidate_id)
        self.w._on_search_finished()           # likewise stale finished
        self.assertIs(self.w.youtube_search_thread, c_worker)

        self.w.sender = lambda: c_worker
        _complete(self.w, [dict(video_id='fresh-c', title='C', duration='3:00')])
        self.assertEqual(self.w._youtube_results_owner_candidate_id, 43)
        self.assertEqual(self.w._send_video_command.call_args.args[0], 'PRELOAD')

    def test_false_c_after_b_dispatch_does_not_interrupt_b(self):
        b_request, c_request = self.vote_b_then_c()
        self.finish_current()
        self.w._execute_pending_search()       # B running, C next
        self.w.on_shazam_candidate_rejected(43, 'wrong-first-vote')
        self.assertEqual(c_request.phase, SearchPhase.CANCELLED)
        self.assertIsNone(self.w.shazam_flow.queued_search)
        self.assertEqual(b_request.phase, SearchPhase.RUNNING)
        _complete(self.w, _videos('3:00', '3:00'))
        self.assertEqual(self.w._confirmed_shazam_pending.video_id, 'video-0')
        self.assertEqual(self.w._send_video_command.call_args.args[0], 'PRELOAD')
        self.finish_current()
        self.w._execute_pending_search()
        self.assertEqual([x.title for x in self.workers], ['Song B'])

    def test_explicit_manual_search_still_supersedes_active_confirmed_b(self):
        b_request, c_request = self.vote_b_then_c()
        self.finish_current()
        self.w._execute_pending_search()    # B worker running
        self.w.search_youtube('Manual Song', '', '', from_list=False,
                              allow_auto_play=False, shazam_candidate_id=None)
        self.assertEqual(self.w._pending_search_args[0], 'Manual Song')
        self.assertEqual(c_request.phase, SearchPhase.CANCELLED)
        _complete(self.w, _videos('3:00', '3:00'))
        # B is two-vote confirmed but an explicit manual request takes priority.
        self.assertEqual(len(self.w._send_video_command.call_args_list), 0)

    def test_obsolete_queued_b_does_not_strand_valid_c(self):
        b_request, c_request = self.vote_b_then_c()
        self.w._confirmed_shazam_pending = None
        self.w.shazam_flow.confirmed_search_owner = None
        self.finish_current()
        self.w._execute_pending_search()
        self.assertEqual(b_request.phase, SearchPhase.CANCELLED)
        self.assertEqual(self.w._pending_search_args[0], 'Song C')
        self.w._pending_search_timer.start.assert_called_with(1000)
        self.w._execute_pending_search()
        self.assertEqual([x.title for x in self.workers], ['Song C'])


if __name__ == '__main__':
    unittest.main()
