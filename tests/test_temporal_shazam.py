from pathlib import Path
import queue
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

from tests.test_webview_integration import ShazamService


class TemporalConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {
            'shazam_recording_seconds': 6,
            'shazam_language': 'ja-JP',
            'shazam_endpoint_country': 'JP',
        }
        fake_config = types.SimpleNamespace(get=lambda k, default=None: self.config.get(k, default))
        with patch('app.services.config_service.ConfigService', return_value=fake_config), \
             patch.object(ShazamService, '_get_history_path', return_value=Path(self.tmp.name) / 'history.json'):
            self.svc = ShazamService()
        self.svc._active = True
        self.svc._generation = 1

    def tearDown(self):
        self.svc.shutdown()
        self.tmp.cleanup()

    def _ready_all_lanes(self):
        for ready in self.svc._lane_ready:
            ready.set()

    def _drain_work_queues(self):
        for work_queue in self.svc._work_queues:
            try:
                while True:
                    work_queue.get_nowait()
            except queue.Empty:
                pass

    def test_four_lanes_and_three_second_slot_constants(self):
        self.assertEqual(self.svc.RECOGNITION_GROUPS, 4)
        self.assertEqual(self.svc.LANES_PER_GROUP, 1)
        self.assertEqual(self.svc.PARALLEL_RECOGNITION_LANES, 4)
        self.assertEqual(self.svc.GROUP_STAGGER_SECONDS, 3.0)
        self.assertEqual(self.svc._group_lanes(0), (0,))
        self.assertEqual(self.svc._group_lanes(3), (3,))

    def test_single_result_is_only_pending(self):
        self.svc._stage_temporal_result(0, 0, '白い雪のプリンセスは', 'のぼる↑')
        self.assertEqual(self.svc.get_history(), [])

    def test_two_consecutive_prefix_matches_publish(self):
        self.svc._stage_temporal_result(0, 0, '白い雪のプリンセスは', 'のぼる↑')
        self.svc._stage_temporal_result(1, 1, '白い雪のプリンセスは (feat. 初音ミク)', '別artist')
        self.assertEqual(self.svc.get_history()[0][1], '白い雪のプリンセスは (feat. 初音ミク)')

    def test_different_title_does_not_publish(self):
        self.svc._stage_temporal_result(0, 0, 'ABCD song', 'A')
        self.svc._stage_temporal_result(1, 1, 'WXYZ song', 'B')
        self.assertEqual(self.svc.get_history(), [])

    def test_a_x_a_does_not_publish_single_x(self):
        self.svc._stage_temporal_result(0, 0, 'After Run', 'シーズ')
        self.svc._stage_temporal_result(1, 1, 'After Run', 'シーズ')
        self.svc._stage_temporal_result(2, 2, 'Shape of My Mind', 'Neon Dreams')
        self.svc._stage_temporal_result(3, 3, 'After Run', 'シーズ')
        self.assertEqual(len(self.svc.get_history()), 1)
        self.assertEqual(self.svc.get_history()[0][1:], ('After Run', 'シーズ'))

    def test_out_of_order_completion_is_processed_by_sequence(self):
        self.svc._stage_temporal_result(1, 1, 'ABCD song', 'A')
        self.assertEqual(self.svc.get_history(), [])
        self.svc._stage_temporal_result(0, 0, 'ABCD song', 'A')
        self.assertEqual(self.svc.get_history()[0][1:], ('ABCD song', 'A'))

    def test_busy_preferred_lane_borrows_another_free_lane(self):
        self._ready_all_lanes()
        self.svc._lane_busy[0] = True
        self.svc._group_busy[0] = True
        self.svc._next_group_index = 0
        self.svc._next_group_slot_at = 99.0
        samples = np.zeros(16000 * 6, dtype=np.int16)
        with patch('time.monotonic', return_value=100.0), \
             patch.object(self.svc, '_snapshot_latest', return_value=samples), \
             patch.object(self.svc, '_resample_to_shazam_rate', return_value=samples), \
             patch.object(self.svc, '_pcm_to_wav_bytes', return_value=b'wav'):
            self.svc._recognize_tick()

        self.assertEqual(self.svc._request_sequence, 1)
        self.assertTrue(self.svc._group_busy[1])
        self.assertFalse(self.svc._work_queues[1].empty())
        self.assertEqual(self.svc._next_group_index, 2)
        self.assertEqual(self.svc._next_group_slot_at, 103.0)
        self._drain_work_queues()

    def test_all_busy_keeps_due_slot_pending_until_worker_frees(self):
        self._ready_all_lanes()
        self.svc._lane_busy = [True] * 4
        self.svc._group_busy = [True] * 4
        self.svc._next_group_index = 0
        self.svc._next_group_slot_at = 99.0

        with patch('time.monotonic', return_value=100.0):
            self.svc._recognize_tick()
        self.assertEqual(self.svc._request_sequence, 0)
        self.assertEqual(self.svc._next_group_slot_at, 99.0)
        self.assertEqual(self.svc._slot_wait_started_at, 100.0)

        # Lane 3 becomes free shortly afterward. The pending slot must be dispatched there
        # immediately with fresh audio instead of waiting for lane 1's next 12-second phase.
        self.svc._lane_busy[2] = False
        self.svc._group_busy[2] = False
        samples = np.zeros(16000 * 6, dtype=np.int16)
        with patch('time.monotonic', return_value=101.5), \
             patch.object(self.svc, '_snapshot_latest', return_value=samples), \
             patch.object(self.svc, '_resample_to_shazam_rate', return_value=samples), \
             patch.object(self.svc, '_pcm_to_wav_bytes', return_value=b'wav'):
            self.svc._recognize_tick()

        self.assertEqual(self.svc._request_sequence, 1)
        self.assertTrue(self.svc._group_busy[2])
        self.assertFalse(self.svc._work_queues[2].empty())
        self.assertEqual(self.svc._next_group_slot_at, 104.5)
        self.assertIsNone(self.svc._slot_wait_started_at)
        self._drain_work_queues()

    def test_no_catchup_burst_after_busy_wait(self):
        self._ready_all_lanes()
        self.svc._next_group_slot_at = 99.0
        samples = np.zeros(16000 * 6, dtype=np.int16)
        with patch('time.monotonic', return_value=100.0), \
             patch.object(self.svc, '_snapshot_latest', return_value=samples), \
             patch.object(self.svc, '_resample_to_shazam_rate', return_value=samples), \
             patch.object(self.svc, '_pcm_to_wav_bytes', return_value=b'wav'):
            self.svc._recognize_tick()
        self.assertEqual(self.svc._request_sequence, 1)

        # Even if another lane is free, 100 ms later must not dispatch a second request.
        with patch('time.monotonic', return_value=100.1), \
             patch.object(self.svc, '_snapshot_latest', return_value=samples):
            self.svc._recognize_tick()
        self.assertEqual(self.svc._request_sequence, 1)
        self._drain_work_queues()


if __name__ == '__main__':
    unittest.main(verbosity=2)
