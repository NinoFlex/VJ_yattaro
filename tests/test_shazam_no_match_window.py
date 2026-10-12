"""Bounded soft no-match confirmation. Qt/recognition/HTTP are offline doubles.

Use the actual service entry points, not only the matching predicate. The clock
is monotonic and controllable; no live Shazam queries or microphone are used.
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


class NoMatchWindowTests(unittest.TestCase):
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
        self.a = raw_song(title='Song A', artist='Artist A')
        self.b = raw_song('22222222', title='Song B', artist='Artist B')

    def tearDown(self):
        self.svc.shutdown()
        self.clock.stop()
        self.tmp.cleanup()

    def observe(self, seq, raw=None, at=None, error=''):
        if at is not None:
            self.now = at
        raw = dict(self.a if raw is None else raw)
        result = (raw.get('title', ''), raw.get('artist', ''), error)
        self.svc._handle_confirmed_group_result(seq % 4, seq, [seq % 4], [result], [raw])

    def no_match(self, seq, at=None):
        self.observe(seq, {'source': 'no-match', 'title': '', 'artist': ''}, at)

    def transport_error(self, seq, at=None, error=TRANSPORT_ERROR, raw=None):
        if raw is None:
            raw = {'source': 'no-match', 'title': '', 'artist': ''}
        self.observe(seq, raw, at, error=error)

    def titles(self):
        return [row[1] for row in self.svc.get_history()]

    def key(self, raw):
        return self.svc._metadata_key(track_identity(raw))

    def finish(self, raw, metadata=('Song A', 'Artist A'), at=None):
        if at is not None:
            self.now = at
        self.broker.finish(1, self.key(raw), metadata)

    def test_one_no_match_then_same_shazam_id_confirms(self):
        self.observe(0); self.no_match(1, 103); self.observe(2, at=106)
        self.assertEqual(self.titles(), ['Song A'])
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)

    def test_two_no_matches_then_same_shazam_id_confirms(self):
        self.observe(0); self.no_match(1, 104); self.no_match(2, 108)
        self.assertEqual(self.titles(), [])
        self.observe(3, at=112)
        self.assertEqual(self.titles(), ['Song A'])

    def test_one_transport_error_then_same_shazam_id_confirms(self):
        self.observe(0); self.transport_error(1, 103); self.observe(2, at=106)
        self.assertEqual(self.titles(), ['Song A'])

    def test_transport_error_and_no_match_can_share_two_gap_budget(self):
        self.observe(0); self.transport_error(1, 103); self.no_match(2, 106)
        self.observe(3, at=109)
        self.assertEqual(self.titles(), ['Song A'])

    def test_no_match_then_transport_error_can_share_two_gap_budget(self):
        self.observe(0); self.no_match(1, 103); self.transport_error(2, 106)
        self.observe(3, at=109)
        self.assertEqual(self.titles(), ['Song A'])

    def test_two_transport_errors_break_chain(self):
        self.observe(0); self.transport_error(1, 102); self.transport_error(2, 104)
        self.observe(3, at=106); self.assertEqual(self.titles(), [])
        self.observe(4, at=108); self.assertEqual(self.titles(), ['Song A'])

    def test_transport_error_does_not_extend_window(self):
        self.observe(0); self.transport_error(1, 114); self.observe(2, at=116)
        self.assertEqual(self.titles(), [])

    def test_unrelated_recognition_error_remains_hard_barrier(self):
        self.observe(0)
        self.transport_error(1, 103, error='navigation failed')
        self.observe(2, at=106)
        self.assertEqual(self.titles(), [])

    def test_transport_error_with_track_evidence_is_hard_barrier(self):
        self.observe(0)
        self.transport_error(1, 103, raw=self.a)
        self.observe(2, at=106)
        self.assertEqual(self.titles(), [])

    def test_different_song_after_transport_error_breaks_chain(self):
        self.observe(0); self.transport_error(1, 103); self.observe(2, self.b, at=106)
        self.observe(3, at=109)
        self.assertEqual(self.titles(), [])

    def test_lookahead_pair_with_transport_error_bypasses_older_head(self):
        self.observe(1); self.transport_error(2, 103); self.observe(3, at=106)
        self.assertEqual(self.titles(), ['Song A'])
        self.assertEqual(self.svc._next_temporal_sequence, 4)

    def test_lookahead_pair_with_two_transport_errors_does_not_bypass(self):
        self.observe(1); self.transport_error(2, 102); self.transport_error(3, 104)
        self.observe(4, at=106)
        self.assertEqual(self.titles(), [])

    def test_three_no_matches_break_chain(self):
        self.observe(0); self.no_match(1, 102); self.no_match(2, 104); self.no_match(3, 106)
        self.observe(4, at=108); self.assertEqual(self.titles(), [])
        self.observe(5, at=110); self.assertEqual(self.titles(), ['Song A'])

    def test_exact_15_second_boundary_is_allowed(self):
        self.observe(0); self.no_match(1, 107); self.observe(2, at=115)
        self.assertEqual(self.titles(), ['Song A'])

    def test_over_15_second_boundary_requires_another_recognition(self):
        self.observe(0); self.no_match(1, 107); self.observe(2, at=115.001)
        self.assertEqual(self.titles(), [])
        self.observe(3, at=117); self.assertEqual(self.titles(), ['Song A'])

    def test_no_match_does_not_extend_window(self):
        self.observe(0); self.no_match(1, 111); self.no_match(2, 114)
        self.observe(3, at=116); self.assertEqual(self.titles(), [])

    def test_expired_candidate_is_cleared_on_no_match(self):
        self.observe(0); self.no_match(1, 116)
        self.assertIsNone(self.svc._last_temporal_observation)
        self.observe(2, at=117); self.assertEqual(self.titles(), [])

    def test_worker_timestamp_not_ui_callback_time_controls_age(self):
        self.observe(0, dict(self.a, _received_monotonic=90.0))
        self.no_match(1, 103); self.observe(2, at=106)
        self.assertEqual(self.titles(), [])

    def test_queue_drain_does_not_refresh_receipt_time(self):
        self.observe(1, at=100)
        self.no_match(2, 103)
        # seq 0 is still unresolved. Return it too late to use the seq 1 anchor.
        self.observe(0, self.b, at=116)
        self.observe(3, at=117)
        self.assertEqual(self.titles(), [])

    def test_out_of_order_completions_use_age_of_both_results(self):
        self.observe(2, at=100)
        self.no_match(1, 104)
        self.observe(0, at=114)
        self.assertEqual(self.titles(), ['Song A'])

    def test_expired_newer_sequence_returned_early_cannot_confirm(self):
        self.observe(2, at=100)
        self.no_match(1, 104)
        self.observe(0, at=116)
        self.assertEqual(self.titles(), [])

    def test_invalid_worker_timestamps_disable_gap(self):
        for bad in (True, '100', float('nan'), float('inf'), -1.0, 101.0):
            with self.subTest(value=bad):
                self.svc._next_temporal_sequence = 0
                self.svc._last_temporal_observation = None
                self.svc._newest_temporal_observation = None
                self.now = 100
                self.observe(0, dict(self.a, _received_monotonic=bad))
                self.no_match(1, 103); self.observe(2, at=106)
                self.assertEqual(self.titles(), [])

    def test_wall_clock_not_used_by_gap_policy(self):
        self.observe(0)
        with patch('time.time', side_effect=AssertionError('Must not use wall clock')):
            self.no_match(1, 103); self.observe(2, at=106)
        self.assertEqual(self.titles(), ['Song A'])

    def test_no_match_without_prior_success_does_not_count(self):
        self.no_match(0); self.no_match(1, 103); self.observe(2, at=106)
        self.assertEqual(self.titles(), [])

    def test_different_shazam_id_breaks_chain(self):
        self.observe(0); self.no_match(1, 103); self.observe(2, self.b, at=106)
        self.no_match(3, 108); self.observe(4, at=110)
        self.assertEqual(self.titles(), [])

    def test_same_name_different_ids_do_not_confirm(self):
        other = raw_song('22222222', title='Song A', artist='Artist A')
        self.observe(0); self.no_match(1, 103); self.observe(2, other, at=106)
        self.assertEqual(self.titles(), [])

    def test_same_id_different_localized_names_still_confirms(self):
        self.observe(0); self.no_match(1, 103)
        self.observe(2, raw_song(title='Localized Song A', artist='Artist A'), at=106)
        self.assertEqual(self.titles(), ['Localized Song A'])

    def test_song_and_track_namespaces_are_not_equated(self):
        self.observe(0); self.no_match(1, 103)
        self.observe(2, raw_song(kind='track', title='Song A', artist='Artist A'), at=106)
        self.assertEqual(self.titles(), [])

    def test_typed_and_untyped_shazam_ids_are_not_equated(self):
        untyped = dict(self.a); untyped.pop('url')
        self.observe(0); self.no_match(1, 103); self.observe(2, untyped, at=106)
        self.assertEqual(self.titles(), [])

    def test_two_matching_untyped_shazam_ids_can_bridge(self):
        untyped = dict(self.a); untyped.pop('url')
        self.observe(0, untyped); self.no_match(1, 103); self.observe(2, untyped, at=106)
        self.assertEqual(self.titles(), ['Song A'])

    def test_shazam_evidence_retained_when_metadata_uses_apple_id(self):
        value = dict(self.a, appleTrackId='99999999'); value.pop('url')
        self.observe(0, value); self.no_match(1, 103); self.observe(2, value, at=106)
        self.assertEqual(self.titles(), ['Song A'])

    def test_different_shazam_ids_with_same_apple_id_cannot_bridge(self):
        value = dict(self.a, appleTrackId='99999999'); value.pop('url')
        other = dict(value, shazamTrackId='22222222')
        self.observe(0, value); self.no_match(1, 103); self.observe(2, other, at=106)
        self.assertEqual(self.titles(), [])

    def test_apple_only_id_requires_consecutive_results(self):
        value = {'source': 'recognition-response', 'title': 'Song A',
                 'artist': 'Artist A', 'appleTrackId': '99999999'}
        self.observe(0, value); self.no_match(1, 103); self.observe(2, value, at=106)
        self.assertEqual(self.titles(), [])
        self.observe(3, value, at=109); self.assertEqual(self.titles(), ['Song A'])

    def test_idless_name_requires_consecutive_results(self):
        value = {'source': 'recognition-response', 'title': 'Song A', 'artist': 'Artist A'}
        self.observe(0, value); self.no_match(1, 103); self.observe(2, value, at=106)
        self.assertEqual(self.titles(), [])
        self.observe(3, value, at=109); self.assertEqual(self.titles(), ['Song A'])

    def test_idless_result_between_known_ids_breaks_chain(self):
        value = {'source': 'recognition-response', 'title': 'Song A', 'artist': 'Artist A'}
        self.observe(0); self.observe(1, value, at=103); self.observe(2, at=106)
        self.assertEqual(self.titles(), [])

    def test_apple_only_result_between_shazam_results_breaks_chain(self):
        value = {'source': 'recognition-response', 'title': 'Song A', 'artist': 'Artist A',
                 'appleTrackId': '11111111'}
        self.observe(0); self.observe(1, value, at=103); self.observe(2, at=106)
        self.assertEqual(self.titles(), [])

    def test_no_match_with_error_is_a_hard_barrier(self):
        self.observe(0)
        self.observe(1, {'source': 'no-match'}, at=103, error='audio request timed out')
        self.observe(2, at=106); self.assertEqual(self.titles(), [])

    def test_error_field_in_empty_result_is_a_hard_barrier(self):
        self.observe(0)
        self.observe(1, {'source': 'no-match', 'error': 'network failure'}, at=103)
        self.observe(2, at=106); self.assertEqual(self.titles(), [])

    def test_untrusted_source_is_not_a_soft_no_match(self):
        self.observe(0)
        self.observe(1, dict(self.a, source='new-result-heading'), at=103)
        self.observe(2, at=106); self.assertEqual(self.titles(), [])

    def test_empty_untrusted_source_is_not_a_soft_no_match(self):
        self.observe(0)
        self.observe(1, {'source': 'new-result-heading'}, at=103)
        self.observe(2, at=106); self.assertEqual(self.titles(), [])

    def test_conflicting_identity_is_a_hard_barrier(self):
        self.observe(0)
        self.observe(1, dict(self.a, shazamTrackId='22222222'), at=103)
        self.observe(2, at=106); self.assertEqual(self.titles(), [])

    def test_invalid_id_with_no_title_is_not_a_soft_no_match(self):
        self.observe(0)
        self.observe(1, {'source': 'no-match', 'shazamTrackId': 'not-an-id'}, at=103)
        self.observe(2, at=106); self.assertEqual(self.titles(), [])

    def test_artist_without_title_is_not_a_soft_no_match(self):
        self.observe(0)
        self.observe(1, {'source': 'no-match', 'artist': 'Unknown artist'}, at=103)
        self.observe(2, at=106); self.assertEqual(self.titles(), [])

    def test_duplicate_first_result_never_supplies_second_vote(self):
        self.observe(0); self.no_match(1, 103); self.observe(0, at=106)
        self.assertEqual(self.titles(), [])

    def test_duplicate_no_match_does_not_consume_gap_budget(self):
        self.observe(0); self.no_match(1, 102); self.no_match(1, 103)
        self.no_match(2, 104); self.observe(3, at=106)
        self.assertEqual(self.titles(), ['Song A'])

    def test_running_intermediate_request_cannot_be_skipped(self):
        self.observe(0); self.observe(2, at=106)
        self.assertEqual(self.titles(), [])
        self.assertEqual(self.svc._next_temporal_sequence, 1)
        self.no_match(1, 108); self.assertEqual(self.titles(), ['Song A'])

    def test_late_intermediate_different_song_blocks_gap(self):
        self.observe(0); self.observe(2, at=106)
        self.observe(1, self.b, at=108)
        self.assertEqual(self.titles(), [])

    def test_lookahead_pair_with_completed_no_match_bypasses_older_head(self):
        self.observe(1); self.no_match(2, 103); self.observe(3, at=106)
        self.assertEqual(self.titles(), ['Song A'])
        self.assertEqual(self.svc._next_temporal_sequence, 4)
        self.observe(0, self.b, at=108); self.assertEqual(self.titles(), ['Song A'])

    def test_lookahead_pair_with_two_no_matches_bypasses_older_head(self):
        self.observe(1); self.no_match(2, 103); self.no_match(3, 105)
        self.observe(4, at=108); self.assertEqual(self.titles(), ['Song A'])

    def test_lookahead_missing_inner_result_never_bypasses(self):
        self.observe(1); self.observe(3, at=106)
        self.assertEqual(self.titles(), [])
        self.assertEqual(self.svc._next_temporal_sequence, 0)

    def test_lookahead_pair_with_error_does_not_bypass(self):
        self.observe(1)
        self.observe(2, {'source': 'no-match'}, at=103, error='failure')
        self.observe(3, at=106); self.assertEqual(self.titles(), [])

    def test_lookahead_pair_expired_does_not_bypass(self):
        self.observe(1); self.no_match(2, 103); self.observe(3, at=116)
        self.assertEqual(self.titles(), [])

    def test_receipt_reordering_does_not_extend_lookahead_window(self):
        self.observe(3, at=100); self.no_match(2, 103); self.observe(1, at=116)
        self.assertEqual(self.titles(), [])

    def test_metadata_completed_during_gap_is_not_a_second_recognition(self):
        incomplete = raw_song()
        self.observe(0, incomplete); self.no_match(1, 103)
        self.finish(incomplete, at=104); self.assertEqual(self.titles(), [])
        self.observe(2, incomplete, at=106)
        self.assertEqual(self.titles(), ['Song A'])
        self.assertEqual(len(self.broker.requests), 1)

    def test_gap_confirmation_waits_for_inflight_metadata(self):
        incomplete = raw_song()
        self.observe(0, incomplete); self.no_match(1, 103)
        self.observe(2, incomplete, at=106); self.assertEqual(self.titles(), [])
        self.finish(incomplete, at=108); self.assertEqual(self.titles(), ['Song A'])
        self.assertEqual(len(self.broker.requests), 1)

    def test_title_only_metadata_fallback_still_works(self):
        incomplete = raw_song(title='Song A')
        self.observe(0, incomplete); self.no_match(1, 103)
        self.observe(2, incomplete, at=106)
        self.finish(incomplete, metadata=('Song A', ''), at=108)
        self.assertEqual(self.svc.get_history()[0][1:], ('Song A', ''))

    def test_metadata_failure_does_not_publish_placeholder(self):
        incomplete = raw_song()
        self.observe(0, incomplete); self.no_match(1, 103); self.observe(2, incomplete, at=106)
        self.finish(incomplete, metadata=None, at=108); self.assertEqual(self.titles(), [])

    def test_second_live_result_can_complete_metadata_after_gap(self):
        incomplete = raw_song()
        self.observe(0, incomplete); self.no_match(1, 103); self.observe(2, at=106)
        self.assertEqual(self.titles(), ['Song A'])

    def test_new_no_match_preserves_confirmed_gap_publication(self):
        incomplete = raw_song()
        self.observe(0, incomplete); self.no_match(1, 103); self.observe(2, incomplete, at=106)
        self.no_match(3, 108); self.finish(incomplete, at=109)
        self.assertEqual(self.titles(), ['Song A'])
        self.assertEqual(self.svc._latest_published_sequence, 2)
        self.observe(4, incomplete, at=111)
        self.assertEqual(self.titles(), ['Song A'])
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)

    def test_new_song_cancels_pending_gap_publication(self):
        incomplete = raw_song()
        self.observe(0, incomplete); self.no_match(1, 103); self.observe(2, incomplete, at=106)
        self.observe(3, self.b, at=108); self.finish(incomplete, at=109)
        self.assertEqual(self.titles(), [])

    def test_out_of_order_new_song_cancels_pending_gap_publication(self):
        incomplete = raw_song()
        self.observe(0, incomplete); self.no_match(1, 103); self.observe(2, incomplete, at=106)
        self.observe(4, self.b, at=108); self.finish(incomplete, at=109)
        self.assertEqual(self.titles(), [])

    def test_gap_publication_expires_if_metadata_arrives_too_late(self):
        incomplete = raw_song()
        self.observe(0, incomplete); self.no_match(1, 103); self.observe(2, incomplete, at=106)
        self.finish(incomplete, at=122); self.assertEqual(self.titles(), [])
        self.assertIsNone(self.svc._pending_identity_confirmation)

    def test_stop_drops_held_candidate_and_late_metadata(self):
        incomplete = raw_song()
        self.observe(0, incomplete); self.no_match(1, 103)
        self.svc.stop()
        self.finish(incomplete, at=104)
        self.assertIsNone(self.svc._last_temporal_observation)
        self.assertEqual(self.titles(), [])

    def test_consecutive_success_after_gap_does_not_duplicate_history(self):
        self.observe(0); self.no_match(1, 103); self.observe(2, at=106); self.observe(3, at=109)
        self.assertEqual(self.titles(), ['Song A'])
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)

    def test_invalidated_metadata_never_counts_as_soft_no_match(self):
        self.observe(0)
        # The resolver failed to obtain a title; this is NOT a completed empty recognition.
        self.svc._metadata_raw_results[1] = {'source': 'recognition-response'}
        self.svc._handle_metadata_finished(1, 1, 1, '', '', 'lookup failed')
        self.observe(2, at=106); self.assertEqual(self.titles(), [])

    def test_q_evolution_log_order_confirms_at_sequence_74(self):
        fixture = Path(__file__).parent / 'fixtures' / 'shazam_q_gap.json'
        data = json.loads(fixture.read_text(encoding='utf-8'))
        self.svc._next_temporal_sequence = 71
        published_at = []
        self.svc.new_track_detected.connect(lambda _track: published_at.append(self.now))
        for event in data['events']:
            self.observe(event['sequence'], event['raw'], at=100 + event['elapsed_seconds'])
            if event['sequence'] == 74:
                self.assertEqual(self.svc._latest_published_sequence, 74)
                self.assertEqual(self.titles(), [data['title']])
        self.assertEqual(len(published_at), 1)
        self.assertAlmostEqual(published_at[0] - 100, 11.962, places=3)

    def test_new_messages_are_not_hidden_at_info_level(self):
        from app.utils.logger import Logger
        for line in (
            'ShazamService: Candidate held across neutral gap seq=72',
            'ShazamService: Temporal neutral-gap confirmation prevSeq=71 seq=74',
            'ShazamService: Candidate gap reset seq=72',
            'ShazamService: Started (confirmation=shazam-neutral-gap-v2)',
        ):
            with self.subTest(line=line):
                self.assertFalse(Logger._is_routine_info_line(line))


if __name__ == '__main__':
    unittest.main()
