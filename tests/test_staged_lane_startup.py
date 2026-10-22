"""Real-thread regression for bounded startup, failed lanes and generation races.

Qt, microphone and browser are fakes; the production gate and service workers
are executed. These are not Windows/WebView2 performance measurements.
"""
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

from app.services.shazam_lane_health import LaneHealth, LanePhase, LaneStartupGate, lane_failure_reason
from app.services.shazam_webview_client import WebViewRecognizer, RecognitionCancelled, RecognitionAborted
from app.utils.logger import Logger
from tests.test_webview_integration import ShazamService


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class StartupGateTests(unittest.TestCase):
    def test_sixteen_callers_never_exceed_two_initializers(self):
        gate = LaneStartupGate(2, 0)
        cancel = threading.Event()
        lock = threading.Lock()
        current, maximum, completed = 0, 0, []

        def run(index):
            nonlocal current, maximum
            if not gate.acquire(cancel):
                return
            try:
                with lock:
                    current += 1
                    maximum = max(maximum, current)
                time.sleep(0.015)
                with lock:
                    current -= 1
                    completed.append(index)
            finally:
                gate.release()

        threads = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(16)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(maximum, 2)
        self.assertEqual(sorted(completed), list(range(16)))
        self.assertEqual(gate.in_flight, 0)

    def test_admission_spaces_start_times_even_if_prewarm_is_instant(self):
        gate = LaneStartupGate(2, 0.03)
        times = []
        for _ in range(3):
            self.assertTrue(gate.acquire(threading.Event()))
            times.append(time.monotonic())
            gate.release()
        self.assertTrue(all(b-a >= 0.028 for a, b in zip(times, times[1:])))

    def test_cancelled_queued_head_does_not_block_next_lane(self):
        gate = LaneStartupGate(1, 0)
        self.assertTrue(gate.acquire(threading.Event()))
        first_cancel, second_cancel = threading.Event(), threading.Event()
        results = []
        def run(label, cancel):
            acquired = gate.acquire(cancel)
            results.append((label, acquired))
            if acquired: gate.release()
        first = threading.Thread(target=run, args=('first', first_cancel), daemon=True)
        second = threading.Thread(target=run, args=('second', second_cancel), daemon=True)
        first.start()
        self.assertTrue(wait_until(lambda: len(gate._waiting) == 1))
        second.start()
        self.assertTrue(wait_until(lambda: len(gate._waiting) == 2))
        first_cancel.set()
        first.join(1)
        self.assertIn(('first', False), results)
        gate.release()
        second.join(1)
        self.assertIn(('second', True), results)
        self.assertEqual(gate.in_flight, 0)

    def test_fifo_retry_goes_behind_already_waiting_lanes(self):
        gate = LaneStartupGate(1, 0)
        cancel = threading.Event()
        self.assertTrue(gate.acquire(cancel))
        order = []
        threads = []
        def run(label):
            if gate.acquire(cancel):
                order.append(label)
                gate.release()
        for index in range(3):
            t = threading.Thread(target=run, args=(index,), daemon=True)
            t.start(); threads.append(t)
            self.assertTrue(wait_until(lambda: len(gate._waiting) == index+1))
        gate.release()
        for t in threads: t.join(2)
        self.assertEqual(order, [0, 1, 2])

    def test_cancel_before_admission_consumes_no_slot(self):
        gate, cancel = LaneStartupGate(), threading.Event()
        cancel.set()
        self.assertFalse(gate.acquire(cancel))
        self.assertEqual(gate.in_flight, 0)
        self.assertFalse(gate._waiting)

    def test_allowlisted_failure_reasons_never_echo_message_contents(self):
        for error, expected in [
            (TimeoutError('slow'), 'timeout'),
            (RuntimeError('The operation has timed out.'), 'timeout'),
            (RuntimeError('Shazam helper protocol mismatch.'), 'protocol-mismatch'),
            (RuntimeError('Shazam helper is not prepared'), 'not-prepared'),
            (RuntimeError('Shazam helper exited'), 'helper-unavailable'),
            (RuntimeError('Shazam WebView2 helper was not found'), 'helper-missing'),
            (RuntimeError('Shazam requires Windows.'), 'platform'),
            (RuntimeError('The Shazam button did not request the supplied audio.'), 'audio-request'),
            (BrokenPipeError('a secret URL'), 'ipc'),
            (RuntimeError('SECRET\nPRIVATE'), 'other'),
        ]:
            self.assertEqual(lane_failure_reason(error), expected)

    def test_invalid_limits_and_double_release_are_rejected(self):
        for limit, spacing in [(0, 0), (2, -1)]:
            with self.assertRaises(ValueError): LaneStartupGate(limit, spacing)
        with self.assertRaises(RuntimeError): LaneStartupGate().release()


class BrowserProbe:
    def __init__(self):
        self.lock = threading.RLock()
        self.instances = []
        self.active = 0
        self.maximum = 0
        self.counts = {}
        self.starts = []
        self.recognitions = []
        self.before_prewarm = lambda instance, count, cancelled: cancelled.wait(0.005)
        self.on_recognize = lambda instance, cancelled: {'source': 'no-match', 'title': '', 'artist': ''}

    def factory(self, name):
        probe = self
        class Browser:
            def __init__(self):
                self.instance_id = name
                self.closed = False
                self.close_count = 0
                self.require_prewarm = False
            def prewarm(self, language, cancelled):
                with probe.lock:
                    probe.active += 1
                    probe.maximum = max(probe.maximum, probe.active)
                    count = probe.counts.get(name, 0) + 1
                    probe.counts[name] = count
                    probe.starts.append((name, count, time.monotonic(), cancelled))
                try:
                    probe.before_prewarm(self, count, cancelled)
                    self.closed = False
                finally:
                    with probe.lock: probe.active -= 1
            def recognize_live(self, rate, language, cancelled):
                with probe.lock: probe.recognitions.append(name)
                return probe.on_recognize(self, cancelled)
            def close(self):
                self.closed = True
                self.close_count += 1
            def feed_live_audio(self, *args): return False
        browser = Browser()
        self.instances.append(browser)
        return browser


class StagedLaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.values = {'shazam_lane_count': 4}
        self.probe = BrowserProbe()
        self.release_events = []
        config = types.SimpleNamespace(get=lambda key, default=None: self.values.get(key, default))
        self.patches = [
            patch('app.services.config_service.ConfigService', return_value=config),
            patch('app.services.shazam_webview_client.WebViewRecognizer', self.probe.factory),
            patch.object(ShazamService, '_get_history_path',
                         return_value=Path(self.tmp.name)/'history.json'),
        ]
        for p in self.patches: p.start()
        self.svc = ShazamService()
        self.svc._lane_startup_gate.min_start_interval = 0
        self.svc.LANE_RECYCLE_RETRY_DELAYS_SECONDS = (0.02, 0.04, 0.08, 0.1)

    def tearDown(self):
        self.svc.shutdown()
        for event in self.release_events: event.set()
        for thread in self.svc._worker_threads:
            if thread: thread.join(3)
        self.assertFalse(any(t and t.is_alive() for t in self.svc._worker_threads))
        self.assertEqual(self.svc._lane_startup_gate.in_flight, 0)
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def start(self, count=4):
        self.values['shazam_lane_count'] = count
        sd = types.SimpleNamespace(
            InputStream=lambda **kwargs: Mock(),
            query_devices=lambda *a, **kw: {'name': 'fake', 'hostapi': 0},
            query_hostapis=lambda: [{'name': 'fake'}],
        )
        with patch.dict(sys.modules, {'sounddevice': sd}), \
             patch.object(self.svc, '_check_shazam_runtime_dependencies'), \
             patch.object(self.svc, '_select_capture_format', return_value=(48000, 2)):
            self.assertTrue(self.svc.start())

    def ready(self, count=4):
        self.assertTrue(wait_until(lambda: all(e.is_set() for e in self.svc._lane_ready[:count])))

    def test_production_defaults_and_backoff_cap(self):
        self.assertEqual(ShazamService.LANE_INIT_MAX_CONCURRENT, 2)
        self.assertEqual(ShazamService.LANE_INIT_INTERVAL_SECONDS, 0.5)
        self.svc.LANE_RECYCLE_RETRY_DELAYS_SECONDS = ShazamService.LANE_RECYCLE_RETRY_DELAYS_SECONDS
        self.assertEqual([self.svc._lane_retry_delay(n) for n in [0,1,2,3,4,5,1000000]],
                         [0,2,5,10,20,30,30])

    def test_start_after_shutdown_cannot_launch_helpers(self):
        self.svc.shutdown()
        self.assertFalse(self.svc.start())
        self.assertFalse(self.probe.starts)

    def test_initial_sixteen_prewarm_calls_are_bounded(self):
        self.probe.before_prewarm = lambda instance, count, cancelled: cancelled.wait(0.015)
        self.start(16)
        self.ready(16)
        self.assertEqual(self.probe.maximum, 2)
        self.assertEqual(sum(self.probe.counts.values()), 16)
        self.assertTrue(all(r.require_prewarm for r in self.svc._web_recognizers))
        self.assertTrue(all(h.phase == LanePhase.READY for h in self.svc._lane_health))

    def test_recognition_can_start_before_other_lanes_finish_initialization(self):
        # OS thread start order is not lane-number order. Let the first
        # admitted lane complete; otherwise two blocked later-numbered lanes
        # can occupy both startup slots before lane-1 is ever admitted.
        first_admitted = []
        def prepare(instance, count, cancelled):
            with self.probe.lock:
                if not first_admitted:
                    first_admitted.append(instance.instance_id)
                    return
            cancelled.wait(10)
        self.probe.before_prewarm = prepare
        self.start(16)
        self.assertTrue(wait_until(lambda: any(e.is_set() for e in self.svc._lane_ready)))
        self.assertFalse(all(e.is_set() for e in self.svc._lane_ready))
        self.svc._recognize_tick()
        self.assertTrue(wait_until(lambda: first_admitted[0] in self.probe.recognitions))
        self.assertEqual(self.svc._request_sequence, 1)
        self.svc._recognize_tick()
        self.assertEqual(self.svc._request_sequence, 1)  # no catch-up burst
        self.assertLessEqual(self.probe.maximum, 2)

    def test_failed_prewarm_is_not_ready_or_a_recognition_gap(self):
        def prepare(instance, count, cancelled):
            if instance.instance_id == 'lane-1': raise TimeoutError('startup failed')
        self.probe.before_prewarm = prepare
        self.svc.LANE_RECYCLE_RETRY_DELAYS_SECONDS = (10,)
        self.start()
        self.assertTrue(wait_until(lambda: self.svc._lane_health[0].phase == LanePhase.RETRY_WAIT))
        self.assertTrue(wait_until(lambda: all(e.is_set() for e in self.svc._lane_ready[1:4])))
        self.assertFalse(self.svc._lane_ready[0].is_set())
        self.assertEqual(self.svc._recognition_finished.calls, [])
        self.svc._next_group_index = 0
        self.assertEqual(self.svc._find_available_group(), 1)
        self.svc._recognize_tick()
        self.assertTrue(wait_until(lambda: bool(self.probe.recognitions)))
        self.assertNotIn('lane-1', self.probe.recognitions)
        self.assertEqual(self.svc._request_sequence, 1)

    def test_all_startup_failures_do_not_consume_request_sequences(self):
        self.probe.before_prewarm = Mock(side_effect=TimeoutError('offline'))
        self.svc.LANE_RECYCLE_RETRY_DELAYS_SECONDS = (10,)
        self.start()
        self.assertTrue(wait_until(lambda: all(h.phase == LanePhase.RETRY_WAIT for h in self.svc._lane_health)))
        for _ in range(8): self.svc._recognize_tick()
        self.assertEqual(self.svc._request_sequence, 0)
        self.assertFalse(self.probe.recognitions)
        self.assertFalse(self.svc._recognition_finished.calls)
        self.assertEqual(self.svc._lane_startup_gate.in_flight, 0)
        summary = self.svc._lane_health_summary()
        self.assertIn('retryWait=4', summary)

    def test_failed_lane_retries_with_delay_then_recovers(self):
        def prepare(instance, count, cancelled):
            if instance.instance_id == 'lane-1' and count < 3:
                raise TimeoutError('startup failed')
        self.probe.before_prewarm = prepare
        self.start()
        self.ready()
        starts = [item[2] for item in self.probe.starts if item[0] == 'lane-1']
        self.assertEqual(len(starts), 3)
        self.assertGreaterEqual(starts[1]-starts[0], 0.019)
        self.assertGreaterEqual(starts[2]-starts[1], 0.039)
        self.assertEqual(self.svc._lane_recycle_count[0], 1)
        self.assertLessEqual(self.probe.maximum, 2)
        self.assertTrue(self.probe.instances[0].closed)

    def test_stop_interrupts_retry_wait_without_extra_initialization(self):
        self.probe.before_prewarm = Mock(side_effect=TimeoutError('offline'))
        self.svc.LANE_RECYCLE_RETRY_DELAYS_SECONDS = (30,)
        self.start()
        self.assertTrue(wait_until(lambda: len(self.probe.starts) == 4))
        before = len(self.probe.starts)
        started = time.monotonic()
        self.svc.stop()
        self.assertLess(time.monotonic()-started, 0.3)
        self.assertTrue(wait_until(lambda: self.svc._lane_startup_gate.in_flight == 0))
        self.assertEqual(len(self.probe.starts), before)
        self.assertFalse(any(e.is_set() for e in self.svc._lane_ready))
        self.assertTrue(all(h.phase == LanePhase.STOPPED for h in self.svc._lane_health))

    def test_stop_cancels_queued_and_in_progress_prewarm(self):
        self.probe.before_prewarm = lambda instance, count, cancelled: cancelled.wait(10)
        self.start(16)
        self.assertTrue(wait_until(lambda: self.probe.active == 2))
        self.svc.stop()
        self.assertTrue(wait_until(lambda: self.probe.active == 0))
        self.assertEqual(len(self.probe.starts), 2)
        self.assertFalse(any(e.is_set() for e in self.svc._lane_ready))

    def test_restart_retains_gate_and_ignores_late_old_ready(self):
        release = threading.Event(); self.release_events.append(release)
        old_cancel = []
        def prepare(instance, count, cancelled):
            if not old_cancel or cancelled is old_cancel[0]:
                if not old_cancel: old_cancel.append(cancelled)
                release.wait(3)  # deliberately ignores cancellation
            else:
                cancelled.wait(10)
        self.probe.before_prewarm = prepare
        self.start(16)
        self.assertTrue(wait_until(lambda: self.probe.active == 2))
        gate = self.svc._lane_startup_gate
        self.svc.stop()
        self.start(4)
        self.assertIs(gate, self.svc._lane_startup_gate)
        self.assertEqual(len(self.probe.starts), 2)
        release.set()
        self.assertTrue(wait_until(lambda: len(self.probe.starts) >= 4))
        self.assertLessEqual(self.probe.maximum, 2)
        self.assertFalse(any(e.is_set() for e in self.svc._lane_ready))
        self.assertTrue(all(h.phase == LanePhase.STOPPED for h in self.svc._lane_health[4:]))

    def test_reduced_lane_count_never_starts_parked_helpers_again(self):
        self.start(16); self.ready(16)
        self.svc.stop(); self.start(4); self.ready(4)
        self.assertEqual([self.probe.counts[f'lane-{i}'] for i in range(5, 17)], [1]*12)
        self.assertFalse(any(e.is_set() for e in self.svc._lane_ready[4:]))
        self.assertEqual(self.svc._lane_startup_gate.in_flight, 0)

    def test_first_transport_failure_quarantines_before_result_signal(self):
        self.start(); self.ready()
        self.svc.LANE_RECYCLE_RETRY_DELAYS_SECONDS = (10,)
        seen = []
        self.svc._recognition_finished.connect(
            lambda gen, lane, seq, title, artist, error:
            seen.append((lane, self.svc._lane_ready[lane].is_set(), error)))
        self.probe.on_recognize = Mock(side_effect=RuntimeError('helper exited unexpectedly'))
        self.svc._next_group_index = 0
        self.svc._recognize_tick()
        self.assertTrue(wait_until(lambda: bool(seen)))
        self.assertEqual(seen[0][:2], (0, False))
        self.assertIn('exited', seen[0][2])
        self.assertFalse(self.svc._lane_busy[0])
        self.assertEqual(self.svc._lane_health[0].phase, LanePhase.RETRY_WAIT)
        self.assertNotEqual(self.svc._find_available_group(), 0)
        self.assertEqual(self.svc._lane_startup_gate.in_flight, 0)

    def test_runtime_failure_recovery_shares_same_gate(self):
        self.start(); self.ready()
        failed_once = set()
        def recognize(instance, cancelled):
            if instance.instance_id not in failed_once:
                failed_once.add(instance.instance_id)
                raise RuntimeError('lost helper')
            return {'source': 'no-match'}
        self.probe.on_recognize = recognize
        self.probe.before_prewarm = lambda instance, count, cancelled: cancelled.wait(0.02)
        for lane in range(4):
            self.svc._lane_busy[lane] = True
            self.svc._work_queues[lane].put((self.svc._generation, lane, lane, lane,
                'ja-JP', 'JP', self.svc._cancel_current, 0., 48000))
        self.assertTrue(wait_until(lambda: all(v == 2 for v in self.probe.counts.values())))
        self.ready()
        self.assertLessEqual(self.probe.maximum, 2)
        self.assertTrue(all(v == 1 for v in self.svc._lane_recycle_count))

    def test_normal_no_match_does_not_rebuild_helper(self):
        self.start(); self.ready()
        self.svc._recognize_tick()
        self.assertTrue(wait_until(lambda: len(self.svc._recognition_finished.calls) == 1))
        self.assertEqual(sum(self.probe.counts.values()), 4)
        self.assertTrue(all(h.failures == 0 for h in self.svc._lane_health))
        self.assertTrue(all(v == 0 for v in self.svc._lane_recycle_count))

    def test_unexpected_cancellation_releases_busy_and_recovers(self):
        self.start(); self.ready()
        self.svc.LANE_RECYCLE_RETRY_DELAYS_SECONDS = (10,)
        self.probe.on_recognize = Mock(side_effect=RecognitionCancelled())
        self.svc._recognize_tick()
        self.assertTrue(wait_until(lambda: bool(self.svc._recognition_finished.calls)))
        self.assertFalse(self.svc._lane_busy[0])
        self.assertFalse(self.svc._lane_ready[0].is_set())
        self.assertEqual(self.svc._lane_health[0].phase, LanePhase.RETRY_WAIT)

    def test_intentional_abort_keeps_helper_and_finishes_request(self):
        self.start(); self.ready()
        self.probe.on_recognize = Mock(side_effect=RecognitionAborted('intentionally aborted'))
        self.svc._recognize_tick()
        self.assertTrue(wait_until(lambda: bool(self.svc._recognition_finished.calls)))
        self.assertTrue(self.svc._lane_ready[0].is_set())
        self.assertEqual(self.svc._lane_health[0].failures, 0)
        self.assertEqual(sum(self.probe.counts.values()), 4)

    def test_old_generation_error_cannot_quarantine_new_lane(self):
        self.start(); self.ready()
        old_gen, old_cancel = self.svc._generation, self.svc._cancel_current
        self.svc.stop(); self.start(); self.ready()
        self.assertFalse(self.svc._record_lane_health(
            0, 'late failure', generation=old_gen, cancelled=old_cancel))
        self.assertTrue(self.svc._lane_ready[0].is_set())
        self.assertEqual(self.svc._lane_health[0].failures, 0)

    def test_late_worker_failure_after_restart_does_not_modify_new_generation(self):
        release = threading.Event(); self.release_events.append(release)
        entered = threading.Event()
        def recognize(instance, cancelled):
            entered.set(); release.wait(3)
            raise RuntimeError('old recognition failed after restart')
        self.probe.on_recognize = recognize
        self.start(); self.ready()
        self.svc._recognize_tick()
        self.assertTrue(entered.wait(2))
        self.svc.stop(); self.start()
        release.set(); self.ready()
        self.assertTrue(all(h.failures == 0 for h in self.svc._lane_health))
        self.assertFalse(self.svc._recognition_finished.calls)
        self.assertFalse(self.svc._raw_lane_results)

    def test_retry_logs_are_compact_and_readiness_is_visible_at_info(self):
        def prepare(instance, count, cancelled):
            if instance.instance_id == 'lane-1' and count == 1:
                raise RuntimeError('private response body SECRET with\nmultiple lines')
        self.probe.before_prewarm = prepare
        with patch('builtins.print') as printed:
            self.start(); self.ready()
        lines = [str(c.args[0]) for c in printed.call_args_list if c.args]
        retries = [l for l in lines if 'Lane retry ' in l]
        ready = [l for l in lines if 'Lane ready ' in l]
        self.assertEqual(len(retries), 1)
        self.assertEqual(len(ready), 4)
        self.assertNotIn('SECRET', retries[0])
        self.assertNotIn('\n', retries[0])
        self.assertIn('error=RuntimeError', retries[0])
        self.assertTrue(all(not Logger._is_routine_info_line(line) for line in retries+ready))


class ManagedClientTests(unittest.TestCase):
    def test_cold_managed_client_does_not_spawn_inside_recognition(self):
        client = WebViewRecognizer('lane-1'); client.require_prewarm = True
        with patch.object(client, 'check_available') as check, \
             patch('subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'not prepared'):
                client.recognize_live(48000, 'ja-JP', threading.Event())
            check.assert_not_called(); spawn.assert_not_called()

    def test_dead_managed_client_does_not_respawn_outside_gate(self):
        client = WebViewRecognizer('lane-1'); client.require_prewarm = True
        client._process = Mock(); client._process.poll.return_value = 1
        client._language = 'ja-JP'; client._ready = True
        with patch.object(client, 'close'), patch('subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'not prepared'):
                client.recognize_live(48000, 'ja-JP', threading.Event())
            spawn.assert_not_called()

    def test_ready_managed_client_reuses_process_without_startup(self):
        client = WebViewRecognizer('lane-1')
        process, destination = Mock(), queue.Queue()
        process.poll.return_value = None
        client._process, client._queue = process, destination
        client._language, client._ready = 'ja-JP', True
        with patch('subprocess.Popen') as spawn:
            self.assertEqual(client._ensure_started('ja-JP', threading.Event(), allow_start=False),
                             (process, destination))
            spawn.assert_not_called()

    def test_cancelled_managed_work_remains_cancellation_not_failure(self):
        client = WebViewRecognizer('lane-1'); client.require_prewarm = True
        cancelled = threading.Event(); cancelled.set()
        with patch('subprocess.Popen') as spawn:
            with self.assertRaises(RecognitionCancelled):
                client.recognize_live(48000, 'ja-JP', cancelled)
            spawn.assert_not_called()

    def test_standalone_client_keeps_lazy_start_default(self):
        self.assertFalse(WebViewRecognizer().require_prewarm)


if __name__ == '__main__':
    unittest.main()
