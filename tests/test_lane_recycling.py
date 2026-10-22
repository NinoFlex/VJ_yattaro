"""Offline lane-health and single-lane WebView2 recycling regression tests."""
from pathlib import Path
import queue
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

from tests.test_webview_integration import ShazamService
from app.services.shazam_webview_client import RecognitionCancelled


AUDIO_ERROR = (
    "The Shazam button did not request the supplied audio. "
    "Use VJ_SHAZAM_DEBUG=1 to inspect the page/consent dialog."
)


class LaneRecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = types.SimpleNamespace(get=lambda _k, default=None: default)
        with patch('app.services.config_service.ConfigService', return_value=config), \
             patch.object(ShazamService, '_get_history_path',
                          return_value=Path(self.tmp.name) / 'history.json'):
            self.svc = ShazamService()
        self.svc._lane_startup_gate.min_start_interval = 0.0
        self.svc.LANE_RECYCLE_RETRY_DELAYS_SECONDS = (0.0, 0.0)
        self.svc._active = True
        self.svc._generation = 1
        for ready in self.svc._lane_ready:
            ready.set()

    def tearDown(self):
        self.svc.shutdown()
        self.tmp.cleanup()

    def test_first_audio_failure_quarantines_closed_helper(self):
        self.assertTrue(self.svc._record_lane_health(1, AUDIO_ERROR))
        self.assertEqual(self.svc._lane_audio_request_failure_streak[1], 1)
        self.assertFalse(self.svc._lane_ready[1].is_set())
        self.assertTrue(self.svc._lane_recycling[1])

    def test_second_consecutive_audio_failure_quarantines_only_that_lane(self):
        self.svc._record_lane_health(1, AUDIO_ERROR)
        self.assertTrue(self.svc._record_lane_health(1, AUDIO_ERROR))
        self.assertFalse(self.svc._lane_ready[1].is_set())
        self.assertTrue(self.svc._lane_recycling[1])
        for lane in (0, 2, 3):
            self.assertTrue(self.svc._lane_ready[lane].is_set())
            self.assertFalse(self.svc._lane_recycling[lane])

    def test_scheduler_skips_quarantined_lane_and_uses_next_healthy_lane(self):
        self.svc._next_group_index = 1
        self.svc._record_lane_health(1, AUDIO_ERROR)
        self.svc._record_lane_health(1, AUDIO_ERROR)
        self.assertEqual(self.svc._find_available_group(), 2)

    def test_non_audio_error_breaks_audio_failure_streak(self):
        self.svc._record_lane_health(0, AUDIO_ERROR)
        self.assertTrue(self.svc._record_lane_health(0, 'navigation failed'))
        self.assertEqual(self.svc._lane_audio_request_failure_streak[0], 0)
        self.assertTrue(self.svc._record_lane_health(0, AUDIO_ERROR))

    def test_success_or_no_match_breaks_audio_failure_streak(self):
        self.svc._record_lane_health(2, AUDIO_ERROR)
        self.assertFalse(self.svc._record_lane_health(2, ''))
        self.assertEqual(self.svc._lane_audio_request_failure_streak[2], 0)
        self.assertTrue(self.svc._record_lane_health(2, AUDIO_ERROR))

    def test_recycle_replaces_and_prewarms_only_one_recognizer(self):
        created = []
        service = self.svc

        class FakeRecognizer:
            def __init__(self, instance_id):
                self.instance_id = instance_id
                self.prewarm_calls = 0
                self.closed = False
                created.append(self)
            def prewarm(self, language, cancelled):
                self.prewarm_calls += 1
                self.language = language
            def close(self):
                self.closed = True

        old = Mock()
        self.svc._lane_ready[2].clear()
        self.svc._lane_recycling[2] = True
        self.svc._lane_audio_request_failure_streak[2] = 2
        untouched = list(self.svc._web_recognizers)
        with patch('app.services.shazam_webview_client.WebViewRecognizer', FakeRecognizer):
            result = self.svc._recycle_lane_recognizer(
                2, old, 1, 'ja-JP', threading.Event())

        old.close.assert_called_once()
        self.assertIs(result, created[0])
        self.assertIs(self.svc._web_recognizers[2], created[0])
        self.assertTrue(self.svc._lane_ready[2].is_set())
        self.assertFalse(self.svc._lane_recycling[2])
        # Starting a helper alone is not proof that recognition is healthy.
        self.assertEqual(self.svc._lane_audio_request_failure_streak[2], 2)
        self.svc._record_lane_health(2, '')
        self.assertEqual(self.svc._lane_audio_request_failure_streak[2], 0)
        self.assertEqual(self.svc._lane_recycle_count[2], 1)
        for lane in (0, 1, 3):
            self.assertIs(self.svc._web_recognizers[lane], untouched[lane])

    def test_recycle_retries_prewarm_without_releasing_lane_early(self):
        created = []
        service = self.svc

        class FakeRecognizer:
            def __init__(self, instance_id):
                self.index = len(created)
                self.closed = False
                created.append(self)
            def prewarm(self, language, cancelled):
                if self.index == 0:
                    self.assert_not_ready = not service._lane_ready[0].is_set()
                    raise RuntimeError('startup failed once')
            def close(self):
                self.closed = True

        self.svc._lane_ready[0].clear()
        self.svc._lane_recycling[0] = True
        old = Mock()
        with patch('app.services.shazam_webview_client.WebViewRecognizer', FakeRecognizer), \
             patch.object(self.svc, 'LANE_RECYCLE_RETRY_DELAYS_SECONDS', (0.0, 0.0)):
            result = self.svc._recycle_lane_recognizer(
                0, old, 1, 'ja-JP', threading.Event())

        self.assertEqual(len(created), 2)
        self.assertTrue(created[0].closed)
        self.assertTrue(created[0].assert_not_ready)
        self.assertIs(result, created[1])
        self.assertTrue(self.svc._lane_ready[0].is_set())
        self.assertEqual(self.svc._lane_recycle_count[0], 1)

    def test_cancelled_recycle_never_marks_lane_ready(self):
        class FakeRecognizer:
            def __init__(self, instance_id): pass
            def prewarm(self, language, cancelled):
                cancelled.set()
                raise RecognitionCancelled()
            def close(self): pass

        self.svc._lane_ready[3].clear()
        self.svc._lane_recycling[3] = True
        with patch('app.services.shazam_webview_client.WebViewRecognizer', FakeRecognizer):
            self.svc._recycle_lane_recognizer(
                3, Mock(), 1, 'ja-JP', threading.Event())
        self.assertFalse(self.svc._lane_ready[3].is_set())
        self.assertEqual(self.svc._lane_recycle_count[3], 0)

    def test_stale_generation_recycle_never_marks_lane_ready(self):
        self.svc._lane_ready[1].clear()
        self.svc._lane_recycling[1] = True
        old = Mock()
        result = self.svc._recycle_lane_recognizer(
            1, old, 0, 'ja-JP', threading.Event())
        self.assertIs(result, old)
        self.assertFalse(self.svc._lane_ready[1].is_set())
        self.assertEqual(self.svc._lane_recycle_count[1], 0)

    def test_worker_triggers_recycle_on_each_closed_helper(self):
        class FailingRecognizer:
            def recognize_live(self, *args, **kwargs):
                raise RuntimeError(AUDIO_ERROR)
            def close(self): pass

        lane = 0
        work_queue = queue.Queue()
        self.svc._work_queues[lane] = work_queue
        self.svc._web_recognizers[lane] = FailingRecognizer()
        # Isolate the worker plumbing from Qt result aggregation for this test.
        self.svc._recognition_finished.handlers = []
        replacement = FailingRecognizer()
        recycle = Mock(return_value=replacement)
        self.svc._recycle_lane_recognizer = recycle
        cancelled = threading.Event()

        worker = threading.Thread(target=self.svc._worker_main, args=(lane,), daemon=True)
        worker.start()
        for seq in (0, 1):
            work_queue.put((1, lane, lane, seq, 'ja-JP', 'JP', cancelled, 0.0, 16000))
        work_queue.put(None)
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(recycle.call_count, 2)
        self.assertFalse(self.svc._lane_ready[lane].is_set())
        self.assertEqual(self.svc._lane_audio_request_failure_streak[lane], 2)

    def test_success_breaks_streak_but_each_transport_error_recovers(self):
        class AlternatingRecognizer:
            def __init__(self): self.calls = 0
            def recognize_live(self, *args, **kwargs):
                self.calls += 1
                if self.calls in (1, 3):
                    raise RuntimeError(AUDIO_ERROR)
                return {'source': 'no-match', 'title': '', 'artist': ''}
            def close(self): pass

        lane = 0
        work_queue = queue.Queue()
        self.svc._work_queues[lane] = work_queue
        self.svc._web_recognizers[lane] = AlternatingRecognizer()
        self.svc._recognition_finished.handlers = []
        self.svc._recycle_lane_recognizer = Mock(return_value=self.svc._web_recognizers[lane])
        cancelled = threading.Event()

        worker = threading.Thread(target=self.svc._worker_main, args=(lane,), daemon=True)
        worker.start()
        for seq in (0, 1, 2):
            work_queue.put((1, lane, lane, seq, 'ja-JP', 'JP', cancelled, 0.0, 16000))
        work_queue.put(None)
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(self.svc._recycle_lane_recognizer.call_count, 2)
        self.assertEqual(self.svc._lane_audio_request_failure_streak[lane], 1)


if __name__ == '__main__':
    unittest.main()
