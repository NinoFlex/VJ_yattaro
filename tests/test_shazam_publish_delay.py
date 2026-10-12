"""Regression tests: a verified two-ID pair survives bounded later neutral results.

Based on the 2026-10-08 live log, where seq 288's transport error arrived
before seq 286's successful result and silently vetoed a valid seq 285/286 pair.
Qt, mic, WebView2 and HTTP are offline test doubles; no actual Shazam requests.
"""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from tests.test_webview_integration import ShazamService
from tests.test_latency_prefetch import ControlledPrefetch, raw_song
from app.services.shazam_metadata_prefetch import track_identity

TRANSPORT_ERROR = (
    'The Shazam button did not request the supplied audio. '
    'Use VJ_SHAZAM_DEBUG=1 to inspect the page/consent dialog.'
)


class PublicationBarrierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 100.0
        self.clock = patch('time.monotonic', side_effect=lambda: self.now)
        self.clock.start()
        config = types.SimpleNamespace(get=lambda _k, default=None: default)
        with patch('app.services.config_service.ConfigService', return_value=config), \
             patch.object(ShazamService, '_get_history_path',
                          return_value=Path(self.tmp.name) / 'history.json'):
            self.svc = ShazamService()
        self.svc._active = True
        self.svc._generation = 1
        self.broker = ControlledPrefetch()
        self.svc._metadata_prefetch = self.broker
        self.a = raw_song('6818212029')
        self.b = raw_song('22222222')
        self.key_a = self.svc._metadata_key(track_identity(self.a))
        self.key_b = self.svc._metadata_key(track_identity(self.b))
        self.published = []
        self.svc.new_track_detected.connect(lambda entry: self.published.append((self.now, entry)))

    def tearDown(self):
        self.svc.shutdown()
        self.clock.stop()
        self.tmp.cleanup()

    def song(self, seq, song=None, at=None):
        if at is not None:
            self.now = at
        raw = dict(self.a if song is None else song)
        self.svc._handle_confirmed_group_result(seq % 4, seq, [seq % 4],
            [(raw.get('title', ''), raw.get('artist', ''), '')], [raw])

    def gap(self, seq, kind='no-match', at=None):
        if at is not None:
            self.now = at
        error = TRANSPORT_ERROR if kind == 'transport-error' else ('navigation failed' if kind == 'hard-error' else '')
        self.svc._handle_confirmed_group_result(seq % 4, seq, [seq % 4],
            [('', '', error)], [{'source': 'no-match', 'title': '', 'artist': ''}])

    def resolved(self, at=None):
        if at is not None:
            self.now = at
        self.broker.finish(1, self.key_a, ('超次元エンカウント', 'にじさんじ'))

    def titles(self):
        return [item[1] for item in self.svc.get_history()]

    def test_live_oct8_receipt_order_confirms_at_286_not_289(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' /
                              'shazam_publish_delay_20261008.json').read_text(encoding='utf-8'))
        self.svc._next_temporal_sequence = 285
        self.a = raw_song(fixture['shazam_track_id'])
        self.key_a = self.svc._metadata_key(track_identity(self.a))
        for event in fixture['events']:
            at = 100.0 + event['elapsed_seconds']
            if event['type'] == 'metadata':
                self.resolved(at)
            elif event['type'] == 'transport_error':
                self.gap(event['sequence'], kind='transport-error', at=at)
            else:
                self.song(event['sequence'], at=at)
            if event.get('sequence') == fixture['expected_confirm_sequence']:
                self.assertEqual(self.svc._latest_published_sequence, 286)
                self.assertEqual(self.titles(), [fixture['title']])
        self.assertEqual(len(self.published), 1)
        self.assertAlmostEqual(self.published[0][0], 102.129, places=3)
        self.assertEqual(self.svc._latest_published_sequence, 289)

    def test_out_of_order_no_match_is_not_a_publication_veto(self):
        self.song(0)
        self.resolved()
        self.gap(2, at=101)
        self.song(1, at=103)
        self.assertEqual(self.titles(), ['超次元エンカウント'])
        self.assertEqual(len(self.published), 1)

    def test_out_of_order_transport_error_is_not_a_publication_veto(self):
        self.song(0)
        self.resolved()
        self.gap(2, kind='transport-error', at=101)
        self.song(1, at=103)
        self.assertEqual(self.titles(), ['超次元エンカウント'])
        self.assertEqual(self.svc._latest_published_sequence, 1)

    def test_two_out_of_order_neutral_results_still_allow_pair(self):
        self.song(0)
        self.resolved()
        self.gap(3, at=101)
        self.gap(2, kind='transport-error', at=102)
        self.song(1, at=103)
        self.assertEqual(self.titles(), ['超次元エンカウント'])

    def test_three_out_of_order_neutral_results_veto_old_pair(self):
        self.song(0)
        self.resolved()
        self.gap(4, at=101)
        self.gap(3, at=102)
        self.gap(2, at=103)
        self.song(1, at=104)
        self.assertEqual(self.titles(), [])
        self.assertIsNone(self.svc._pending_identity_confirmation)

    def test_two_out_of_order_transport_errors_veto_old_pair(self):
        self.song(0)
        self.resolved()
        self.gap(3, kind='transport-error', at=101)
        self.gap(2, kind='transport-error', at=102)
        self.song(1, at=103)
        self.assertEqual(self.titles(), [])

    def test_out_of_order_different_song_remains_hard_barrier(self):
        self.song(0)
        self.resolved()
        self.song(2, self.b, at=101)
        self.song(1, at=103)
        self.assertEqual(self.titles(), [])

    def test_out_of_order_other_error_remains_hard_barrier(self):
        self.song(0)
        self.resolved()
        self.gap(2, kind='hard-error', at=101)
        self.song(1, at=103)
        self.assertEqual(self.titles(), [])

    def test_queued_same_identity_does_not_veto_pair(self):
        self.song(0)
        self.resolved()
        self.song(2, at=101)
        self.song(1, at=103)
        self.assertEqual(self.titles(), ['超次元エンカウント'])
        self.assertEqual(len(self.published), 1)

    def test_delayed_metadata_survives_processed_transport_gap(self):
        self.song(0)
        self.song(1, at=102)
        self.gap(2, kind='transport-error', at=105)
        self.assertEqual(self.titles(), [])
        self.assertIsNotNone(self.svc._pending_identity_confirmation)
        self.resolved(at=106)
        self.assertEqual(self.titles(), ['超次元エンカウント'])
        self.assertEqual(len(self.published), 1)

    def test_delayed_metadata_survives_processed_no_match(self):
        self.song(0)
        self.song(1, at=102)
        self.gap(2, at=105)
        self.resolved(at=106)
        self.assertEqual(self.titles(), ['超次元エンカウント'])

    def test_delayed_metadata_survives_two_neutrals(self):
        self.song(0)
        self.song(1, at=102)
        self.gap(2, at=105)
        self.gap(3, kind='transport-error', at=107)
        self.resolved(at=108)
        self.assertEqual(self.titles(), ['超次元エンカウント'])

    def test_delayed_metadata_never_publishes_after_third_neutral(self):
        self.song(0)
        self.song(1, at=102)
        self.gap(2, at=105)
        self.gap(3, kind='transport-error', at=107)
        self.gap(4, at=109)
        self.resolved(at=110)
        self.assertEqual(self.titles(), [])

    def test_delayed_metadata_never_publishes_after_second_transport_error(self):
        self.song(0)
        self.song(1, at=102)
        self.gap(2, kind='transport-error', at=105)
        self.gap(3, kind='transport-error', at=107)
        self.resolved(at=108)
        self.assertEqual(self.titles(), [])

    def test_delayed_metadata_never_publishes_after_neutral_window_expires(self):
        self.song(0)
        self.song(1, at=102)
        self.gap(2, at=105)
        self.resolved(at=119)
        self.assertEqual(self.titles(), [])

    def test_delayed_metadata_never_publishes_across_new_song(self):
        self.song(0)
        self.song(1, at=102)
        self.gap(2, at=104)
        self.song(3, self.b, at=106)
        self.resolved(at=108)
        self.assertEqual(self.titles(), [])

    def test_delayed_metadata_never_publishes_across_hard_error(self):
        self.song(0)
        self.song(1, at=102)
        self.gap(2, kind='hard-error', at=105)
        self.resolved(at=106)
        self.assertEqual(self.titles(), [])

    def test_successful_publication_not_repeated_by_later_same_song(self):
        self.song(0)
        self.resolved()
        self.gap(2, kind='transport-error', at=101)
        self.song(1, at=103)
        self.song(3, at=105)
        self.assertEqual(self.titles(), ['超次元エンカウント'])
        self.assertEqual(len(self.published), 1)

    def test_not_published_while_only_one_recognition_exists(self):
        self.song(0)
        self.resolved()
        self.gap(2, at=101)
        self.assertEqual(self.titles(), [])


if __name__ == '__main__':
    unittest.main()
