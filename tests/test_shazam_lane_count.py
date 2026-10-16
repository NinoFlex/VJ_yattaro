"""Configurable 4-16 WebView2 lanes: offline lifecycle and scheduler regression."""
from pathlib import Path
import queue
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

from app.services.config_service import (
    SHAZAM_MAX_LANES, SHAZAM_MIN_LANES, normalize_shazam_lane_count,
)
from tests.test_webview_integration import ShazamService


class FakeRecognizer:
    def __init__(self, instance_id):
        self.instance_id = instance_id
        self.closed = False
        self.prewarm_count = 0
        self.close_count = 0
        self.audio_count = 0

    def prewarm(self, language, cancelled):
        self.prewarm_count += 1
        self.closed = False

    def close(self):
        self.close_count += 1
        self.closed = True

    def feed_live_audio(self, pcm, rate):
        self.audio_count += 1

    def recognize_live(self, *args):
        return {'title': '', 'artist': ''}


class FakeStream:
    def __init__(self, **kwargs):
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        pass


class LaneCountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.values = {'shazam_lane_count': 4}
        self.fake_config = types.SimpleNamespace(
            get=lambda k, default=None: self.values.get(k, default)
        )
        self.config_patch = patch('app.services.config_service.ConfigService', return_value=self.fake_config)
        self.recognizer_patch = patch('app.services.shazam_webview_client.WebViewRecognizer', FakeRecognizer)
        self.history_patch = patch.object(
            ShazamService, '_get_history_path', return_value=Path(self.tmp.name) / 'history.json'
        )
        for patcher in (self.config_patch, self.recognizer_patch, self.history_patch):
            patcher.start()
        self.svc = ShazamService()

    def tearDown(self):
        self.svc.shutdown()
        for patcher in (self.history_patch, self.recognizer_patch, self.config_patch):
            patcher.stop()
        self.tmp.cleanup()

    def _start(self):
        fake_sd = types.SimpleNamespace(
            InputStream=FakeStream,
            query_devices=lambda *args, **kwargs: {'name': 'fake mic', 'hostapi': 0},
            query_hostapis=lambda: [{'name': 'fake'}],
        )
        with patch.dict(sys.modules, {'sounddevice': fake_sd}), \
             patch.object(self.svc, '_check_shazam_runtime_dependencies'), \
             patch.object(self.svc, '_select_capture_format', return_value=(48000, 2)):
            self.assertTrue(self.svc.start())
        return self.svc._stream

    def test_normalization_bounds_and_corrupt_persisted_values(self):
        self.assertEqual((SHAZAM_MIN_LANES, SHAZAM_MAX_LANES), (4, 16))
        for value, expected in [
            (None, 4), ('invalid', 4), (-2, 4), (3, 4), ('4', 4),
            (8, 8), ('16', 16), (999, 16), (float('inf'), 4),
        ]:
            with self.subTest(value=value):
                self.assertEqual(normalize_shazam_lane_count(value), expected)

    def test_new_install_keeps_default_four_lanes(self):
        self.assertEqual(self.svc.RECOGNITION_GROUPS, 4)
        self.assertEqual(self.svc.PARALLEL_RECOGNITION_LANES, 4)
        self.assertEqual(self.svc._pool_capacity, 4)
        self.assertEqual(len(self.svc._web_recognizers), 4)
        self.assertEqual([r.instance_id for r in self.svc._web_recognizers],
                         ['lane-1', 'lane-2', 'lane-3', 'lane-4'])

    def test_rapid_restart_drops_previous_generation_prewarm(self):
        # Keep the test worker threads intentionally unstarted, so queue slots
        # remain occupied at stop/start and reproduce the original race.
        with patch.object(self.svc, '_ensure_worker_threads'):
            self._start()
            self.assertEqual(self.svc._work_queues[0].qsize(), 1)
            self.assertFalse(self.svc._lane_ready[0].is_set())
            old_generation = self.svc._generation
            self.svc.stop()
            self._start()
            self.assertEqual(self.svc._work_queues[0].qsize(), 1)
            request = self.svc._work_queues[0].get_nowait()
            self.assertEqual(request[0], 'prewarm')
            self.assertEqual(request[1], self.svc._generation)
            self.assertGreater(self.svc._generation, old_generation)
            self.assertFalse(self.svc._lane_ready[0].is_set())

    def test_16_lanes_created_and_prewarms_only_selected_count(self):
        self.values['shazam_lane_count'] = 16
        self._start()
        self.assertEqual(self.svc.RECOGNITION_GROUPS, 16)
        self.assertEqual(self.svc.PARALLEL_RECOGNITION_LANES, 16)
        self.assertEqual(self.svc._pool_capacity, 16)
        self.assertEqual(len(self.svc._worker_threads), 16)
        self.assertEqual(len(self.svc._web_recognizers), 16)
        self.assertEqual(len(self.svc._lane_ready), 16)
        self.assertEqual(len(self.svc._group_busy), 16)
        for lane_index, ready in enumerate(self.svc._lane_ready):
            self.assertTrue(ready.wait(3.0), f'lane {lane_index + 1} never became ready')
        self.assertEqual([r.instance_id for r in self.svc._web_recognizers],
                         [f'lane-{i}' for i in range(1, 17)])
        self.assertTrue(all(r.prewarm_count == 1 for r in self.svc._web_recognizers))

    def test_16_to_4_and_back_to_8_reuses_slots_without_extra_helpers(self):
        self.values['shazam_lane_count'] = 16
        self._start()
        self.assertTrue(all(ready.wait(3) for ready in self.svc._lane_ready))
        initial_workers = list(self.svc._worker_threads)
        initial_recognizers = list(self.svc._web_recognizers)
        self.values['shazam_lane_count'] = 4
        with patch.dict(sys.modules, {'sounddevice': types.SimpleNamespace(
                InputStream=FakeStream,
                query_devices=lambda *args, **kwargs: {'name': 'fake mic', 'hostapi': 0},
                query_hostapis=lambda: [{'name': 'fake'}])}), \
             patch.object(self.svc, '_check_shazam_runtime_dependencies'), \
             patch.object(self.svc, '_select_capture_format', return_value=(48000, 2)):
            self.svc.reload_settings()
        self.assertEqual(self.svc.RECOGNITION_GROUPS, 4)
        self.assertEqual(self.svc.PARALLEL_RECOGNITION_LANES, 4)
        self.assertEqual(self.svc._pool_capacity, 16)
        self.assertTrue(all(ready.wait(3) for ready in self.svc._lane_ready[:4]))
        self.assertFalse(any(ready.is_set() for ready in self.svc._lane_ready[4:]))
        self.assertTrue(all(r.closed for r in self.svc._web_recognizers[4:]))
        self.assertEqual(self.svc._web_recognizers, initial_recognizers)
        self.assertEqual(self.svc._worker_threads, initial_workers)
        self.svc._next_group_index = 3
        self.assertEqual(self.svc._find_available_group(), 3)
        self.values['shazam_lane_count'] = 8
        self.svc.stop()
        self._start()
        self.assertEqual(self.svc.PARALLEL_RECOGNITION_LANES, 8)
        self.assertTrue(all(ready.wait(3) for ready in self.svc._lane_ready[:8]))
        self.assertFalse(any(ready.is_set() for ready in self.svc._lane_ready[8:]))
        self.assertEqual(self.svc._web_recognizers, initial_recognizers)
        self.assertEqual(self.svc._worker_threads, initial_workers)

    def test_16_lanes_round_robin_uses_lane_16_with_three_second_spacing(self):
        self.values['shazam_lane_count'] = 16
        self.svc._sync_lane_count()
        self.svc._generation = 1
        self.svc._active = True
        for ready in self.svc._lane_ready:
            ready.set()
        self.svc._group_busy[:15] = [True] * 15
        self.svc._next_group_index = 12
        self.svc._next_group_slot_at = 99.0
        with patch('time.monotonic', return_value=100.0):
            self.svc._recognize_tick()
        self.assertEqual(self.svc._request_sequence, 1)
        self.assertTrue(self.svc._group_busy[15])
        req = self.svc._work_queues[15].get_nowait()
        self.assertEqual(req[1:4], (15, 15, 0))
        self.assertEqual(self.svc._next_group_index, 0)
        with patch('time.monotonic', return_value=100.1):
            self.svc._recognize_tick()
        self.assertEqual(self.svc._request_sequence, 1)
        self.assertEqual(self.svc._next_group_slot_at, 103.0)

    def test_more_than_sixteen_saved_cannot_allocate_more_than_sixteen(self):
        self.values['shazam_lane_count'] = 999
        self.svc._sync_lane_count()
        self.assertEqual(self.svc._pool_capacity, 16)
        self.assertEqual(self.svc.PARALLEL_RECOGNITION_LANES, 16)

    def test_sixteen_pcm_fanout_then_shrink_to_four(self):
        self.values['shazam_lane_count'] = 16
        self.svc._sync_lane_count()
        self.svc._active = True
        self.svc._capture_sample_rate = 48000
        data = np.array([[1], [400], [-300]], dtype=np.int16)
        self.svc._audio_callback(data, len(data), None, None)
        self.assertEqual([r.audio_count for r in self.svc._web_recognizers], [1] * 16)
        self.values['shazam_lane_count'] = 4
        self.svc._sync_lane_count()
        self.svc._audio_callback(data, len(data), None, None)
        self.assertEqual([r.audio_count for r in self.svc._web_recognizers], [2] * 4 + [1] * 12)

    def test_settings_ui_reads_and_persists_lane_count(self):
        source = (Path(__file__).resolve().parents[1] /
                  'ui/dialogs/settings_dialog.py').read_text(encoding='utf-8')
        self.assertIn('self.shazam_lane_count_spin.setRange(4, 16)', source)
        self.assertIn('"WebView2レーン数:"', source)
        self.assertIn('self.shazam_lane_count_spin.setValue(', source)
        self.assertIn('"shazam_lane_count": shazam_lane_count', source)
        self.assertIn('shazam_lane_count = int(self.shazam_lane_count_spin.value())', source)


if __name__ == '__main__':
    unittest.main()
