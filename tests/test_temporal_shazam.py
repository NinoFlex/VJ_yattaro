from pathlib import Path
import queue
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

from tests.test_webview_integration import ShazamService


class TemporalConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {
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


    def test_capture_format_prefers_stereo_when_endpoint_supports_it(self):
        class FakeSD:
            def query_devices(self, device=None, kind=None):
                return {'max_input_channels': 2, 'default_samplerate': 48000}
            def check_input_settings(self, **kwargs):
                if kwargs['channels'] != 2 or kwargs['samplerate'] != 48000:
                    raise RuntimeError('unsupported')
        rate, channels = self.svc._select_capture_format(FakeSD(), 35)
        self.assertEqual((rate, channels), (48000, 2))

    def test_live_audio_agc_raises_quiet_signal_before_fanout(self):
        class Sink:
            def __init__(self):
                self.payload = None
            def feed_live_audio(self, pcm, sample_rate):
                self.payload = (pcm, sample_rate)
            def close(self):
                pass
        sinks = [Sink() for _ in self.svc._web_recognizers]
        self.svc._web_recognizers = sinks
        self.svc._capture_sample_rate = 48000
        self.svc._active = True
        # About -46 dBFS RMS, similar to the failing real-device log. Repeated blocks
        # let the smoothed AGC converge without any discontinuous one-block jump.
        block = np.full((480, 2), 160, dtype=np.int16)
        for _ in range(30):
            self.svc._audio_callback(block, len(block), None, None)
        pcm, sample_rate = sinks[0].payload
        out = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
        raw = block[:, 0].astype(np.float32) / 32768.0
        self.assertEqual(sample_rate, 48000)
        self.assertGreater(float(np.sqrt(np.mean(out * out))), float(np.sqrt(np.mean(raw * raw))) * 4.0)

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

    def test_two_matching_later_sequences_bypass_stuck_head(self):
        # seq=0 is still outstanding. Two independent later listening windows already
        # agree, so they may confirm without waiting for the slow head request.
        self.svc._stage_temporal_result(1, 1, 'ABCD song', 'A')
        self.assertEqual(self.svc.get_history(), [])
        self.svc._stage_temporal_result(2, 2, 'ABCD song live', 'B')
        self.assertEqual(self.svc.get_history()[0][1:], ('ABCD song live', 'B'))
        self.assertEqual(self.svc._next_temporal_sequence, 3)

    def test_single_later_sequence_never_bypasses_stuck_head(self):
        self.svc._stage_temporal_result(1, 1, 'ABCD song', 'A')
        self.assertEqual(self.svc.get_history(), [])
        self.assertEqual(self.svc._next_temporal_sequence, 0)
        self.assertIn(1, self.svc._temporal_results)

    def test_mismatching_later_sequences_do_not_bypass_stuck_head(self):
        self.svc._stage_temporal_result(1, 1, 'ABCD song', 'A')
        self.svc._stage_temporal_result(2, 2, 'WXYZ song', 'B')
        self.assertEqual(self.svc.get_history(), [])
        self.assertEqual(self.svc._next_temporal_sequence, 0)

    def test_late_result_for_bypassed_head_is_ignored(self):
        self.svc._stage_temporal_result(1, 1, 'ABCD song', 'A')
        self.svc._stage_temporal_result(2, 2, 'ABCD song live', 'B')
        before = list(self.svc.get_history())
        self.svc._stage_temporal_result(0, 0, 'OLD track', 'Old')
        self.assertEqual(self.svc.get_history(), before)
        self.assertEqual(self.svc._next_temporal_sequence, 3)

    def test_matching_pair_can_bypass_intervening_completed_noise(self):
        # seq=0 is stuck, seq=1 is a one-off false result, seq=2/3 agree. The matched
        # pair is sufficient evidence; seq=1 is superseded rather than blocking it.
        self.svc._stage_temporal_result(1, 1, 'Noise Track', 'X')
        self.svc._stage_temporal_result(2, 2, 'After Run', 'シーズ')
        self.svc._stage_temporal_result(3, 3, 'After Run', 'シーズ')
        self.assertEqual(self.svc.get_history()[0][1:], ('After Run', 'シーズ'))
        self.assertEqual(self.svc._next_temporal_sequence, 4)

    def test_busy_preferred_lane_borrows_another_free_lane(self):
        self._ready_all_lanes()
        self.svc._lane_busy[0] = True
        self.svc._group_busy[0] = True
        self.svc._next_group_index = 0
        self.svc._next_group_slot_at = 99.0
        with patch('time.monotonic', return_value=100.0):
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
        with patch('time.monotonic', return_value=101.5):
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
        with patch('time.monotonic', return_value=100.0):
            self.svc._recognize_tick()
        self.assertEqual(self.svc._request_sequence, 1)

        # Even if another lane is free, 100 ms later must not dispatch a second request.
        with patch('time.monotonic', return_value=100.1):
            self.svc._recognize_tick()
        self.assertEqual(self.svc._request_sequence, 1)
        self._drain_work_queues()

    def test_dispatch_contains_live_sample_rate_not_wav_snapshot(self):
        self._ready_all_lanes()
        self.svc._capture_sample_rate = 48000
        self.svc._next_group_slot_at = 0.0
        with patch('time.monotonic', return_value=100.0):
            self.svc._recognize_tick()
        item = self.svc._work_queues[0].get_nowait()
        self.assertEqual(item[0:4], (1, 0, 0, 0))
        self.assertEqual(item[-1], 48000)
        self.assertNotIn(b'RIFF', item)

    def test_audio_callback_fans_live_pcm_to_all_active_recognizers(self):
        recognizers = []
        for _ in range(4):
            fake = types.SimpleNamespace(feed_live_audio=Mock(return_value=True), close=Mock())
            recognizers.append(fake)
        self.svc._web_recognizers = recognizers
        self.svc._capture_sample_rate = 48000
        samples = np.array([[1], [-2], [32767], [-32768]], dtype=np.int16)
        self.svc._audio_callback(samples, 4, None, None)
        payloads = []
        for fake in recognizers:
            fake.feed_live_audio.assert_called_once()
            pcm, sample_rate = fake.feed_live_audio.call_args.args
            self.assertEqual(sample_rate, 48000)
            self.assertEqual(len(pcm), 8)
            payloads.append(pcm)
        self.assertTrue(all(pcm == payloads[0] for pcm in payloads))



if __name__ == '__main__':
    unittest.main(verbosity=2)
