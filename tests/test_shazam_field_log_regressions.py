"""Regressions reproduced from 2026-10-13 field log.

Includes same-title temporal-bypass ownership, overdue recognition backlog,
ID-only confirmation before metadata, stale preview incorrectly suppressing a
new publication, and immediate tentative-to-confirmed display updates.
"""
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from tests.test_webview_integration import ShazamService
from tests.test_latency_prefetch import ControlledPrefetch, raw_song
from tests import test_shazam_speculative_preload as legacy_vote_tests


class ServiceLogRegressions(unittest.TestCase):
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

    def tearDown(self):
        self.svc.shutdown()
        self.clock.stop()
        self.tmp.cleanup()

    def vote(self, seq, raw):
        self.svc._handle_confirmed_group_result(
            seq % 4, seq, [seq % 4],
            [(raw.get('title', ''), raw.get('artist', ''), '')], [raw]
        )

    def test_id_only_second_vote_before_metadata_still_starts_search_and_confirms(self):
        events = []
        self.svc.candidate_started.connect(lambda *args: events.append(('start', args[0])))
        self.svc.candidate_confirmed.connect(lambda *args: events.append(('confirm', args[0])))
        raw = raw_song('6785720953')
        self.vote(0, raw)
        self.vote(1, raw)
        self.assertEqual(events, [])
        key = self.svc._metadata_key(('shazam-song', '6785720953'))
        self.broker.finish(1, key, ('月に願う', 'sorato'))
        self.svc._handle_prefetch_finished(1, key, ('月に願う', 'sorato'), '')
        self.assertEqual(events, [('start', 0), ('confirm', 0)])
        self.assertEqual(self.svc._last_publication_candidate_id, 0)
        self.assertEqual(self.svc.get_history()[0][1:], ('月に願う', 'sorato'))

    def test_id_only_signals_reach_ui_and_search_once_with_stale_old_preview(self):
        # Replay the service->MainWindow Qt signal order from the field log.
        h = legacy_vote_tests.WindowVoteTests()
        w = h.window()
        h.start(w)  # prior song already has a confirmed preview
        w.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        w._shazam_preview.committed = True
        w._shazam_preview.playing_ack = True
        w.search_youtube.reset_mock()
        w.shazam_service = self.svc
        self.svc.candidate_started.connect(w.on_shazam_candidate_started)
        self.svc.candidate_confirmed.connect(w.on_shazam_candidate_confirmed)
        self.svc.history_updated.connect(w.on_shazam_history_updated)
        self.svc.new_track_detected.connect(w.on_shazam_new_track_detected)
        raw = raw_song('6785720953')
        self.vote(0, raw)
        self.vote(1, raw)
        key = self.svc._metadata_key(('shazam-song', '6785720953'))
        self.broker.finish(1, key, ('月に願う', 'sorato'))
        self.svc._handle_prefetch_finished(1, key, ('月に願う', 'sorato'), '')
        self.assertEqual(w.search_youtube.call_count, 1)
        self.assertEqual(w.search_youtube.call_args.args[:2], ('月に願う', 'sorato'))
        self.assertTrue(w._shazam_preview.committed)
        self.assertEqual(w._shazam_preview.id, 0)
        self.assertEqual(w.shazam_table_model.rowCount(), 1)

    def test_out_of_order_same_identity_preserves_tentative_id_and_search(self):
        raw = raw_song('431556820', title='Os-宇宙人', artist='エリオをかまってちゃん')
        self.vote(0, raw)
        self.assertEqual(self.svc.candidate_started.calls[0][0], 0)
        self.vote(2, raw)  # 1 is still running
        self.vote(3, raw)  # 2/3 form independently verified lookahead pair
        self.assertEqual([e[0] for e in self.svc.candidate_started.calls], [0])
        self.assertNotIn((0, 'older-observation-bypassed'), self.svc.candidate_rejected.calls)
        self.assertEqual([e[0] for e in self.svc.candidate_confirmed.calls], [0])
        self.assertEqual(self.svc.get_history()[0][1], 'Os-宇宙人')
        self.assertEqual(self.svc._last_publication_candidate_id, 0)

    def test_confirmed_id_only_with_stuck_head_keeps_one_vote_owner(self):
        # Identity was already confirmed, but metadata is still loading when
        # a separate 3/4 matching pair bypasses stuck seq2.
        raw = raw_song('6785720953')
        self.vote(0, raw)
        self.vote(1, raw)
        self.vote(3, raw)
        self.vote(4, raw)
        self.assertFalse(self.svc.candidate_rejected.calls)
        self.assertEqual(self.svc._tentative_candidate["sequence"], 0)
        key = self.svc._metadata_key(('shazam-song', '6785720953'))
        self.broker.finish(1, key, ('月に願う', 'sorato'))
        self.svc._handle_prefetch_finished(1, key, ('月に願う', 'sorato'), '')
        self.assertEqual([e[0] for e in self.svc.candidate_started.calls], [0])
        self.assertEqual([e[0] for e in self.svc.candidate_confirmed.calls], [0])
        self.assertEqual(self.svc.get_history()[0][1], '月に願う')

    def test_stuck_head_with_new_matching_evidence_extends_display_not_confirmation(self):
        # Exact relative sequence shape of 21:05:34-21:05:51 field log.
        raw = raw_song('1533362031', title='Rumbling hearts', artist='栗林みな実')
        self.svc._next_temporal_sequence = 8
        self.vote(8, raw)
        self.now = 106.0
        self.vote(10, raw)  # seq9 never finished
        self.now = 110.0
        self.vote(12, raw)  # seq11 also unfinished
        self.now = 116.0
        self.svc._expire_tentative_candidate()
        self.assertEqual(self.svc.get_history(), [])
        self.assertFalse(self.svc.candidate_rejected.calls)
        self.now = 117.0
        self.vote(13, raw)  # 12/13 now form a safe matching lookahead
        self.assertEqual([e[0] for e in self.svc.candidate_started.calls], [8])
        self.assertEqual([e[0] for e in self.svc.candidate_confirmed.calls], [8])
        self.assertEqual(self.svc.get_history()[0][1], 'Rumbling hearts')

    def test_stale_id_only_confirmation_does_not_start_playback_after_barrier(self):
        raw = raw_song('6785720953')
        other = raw_song('9999000', title='Different', artist='Other')
        self.vote(0, raw)
        self.vote(1, raw)
        # Newer different music makes the first pending publication unsafe.
        self.vote(2, other)
        key = self.svc._metadata_key(('shazam-song', '6785720953'))
        self.broker.finish(1, key, ('月に願う', 'sorato'))
        self.svc._handle_prefetch_finished(1, key, ('月に願う', 'sorato'), '')
        self.assertFalse(self.svc.candidate_confirmed.calls)
        self.assertEqual(self.svc.get_history(), [])

    def test_different_lookahead_pair_still_rejects_old_vote(self):
        old = raw_song('111111', title='Old Track', artist='Old')
        fresh = raw_song('222222', title='New Track', artist='New')
        self.vote(0, old)
        self.vote(2, fresh)
        self.vote(3, fresh)
        self.assertIn((0, 'older-observation-bypassed'), self.svc.candidate_rejected.calls)
        self.assertEqual([e[0] for e in self.svc.candidate_started.calls], [0, 2])
        self.assertEqual([e[0] for e in self.svc.candidate_confirmed.calls], [2])
        self.assertEqual(self.svc.get_history()[0][1], 'New Track')

    def test_single_first_vote_still_expires_without_later_matching_evidence(self):
        raw = raw_song('1000100', title='Single', artist='Solo')
        self.vote(0, raw)
        self.now = 116.0
        self.svc._expire_tentative_candidate()
        self.assertEqual(self.svc.candidate_rejected.calls[-1], (0, 'first-vote-expired'))


class UiLogRegressions(unittest.TestCase):
    def setUp(self):
        h = legacy_vote_tests.WindowVoteTests()
        self.win = h.window()
        h.start(self.win)

    def test_old_confirmed_candidate_cannot_claim_new_id_only_history_and_search(self):
        w = self.win
        w.on_shazam_candidate_confirmed(42, ('', 'euphoric field (English)', 'Elisa'))
        old = ('2026-10-13 21:10:02', 'Song B', 'Artist B')
        # The fixture candidate is Song B, commit the old song normally.
        w.shazam_service._last_publication_candidate_id = 42
        w.on_shazam_history_updated([old])
        w.on_shazam_new_track_detected(old)
        self.assertEqual(w.search_youtube.call_count, 1)
        # ID-only new song publishes with NO matching tentative owner.
        fresh = ('2026-10-13 21:10:20', '月に願う', 'sorato')
        w.shazam_service._last_publication_candidate_id = None
        w.on_shazam_history_updated([fresh, old])
        w.on_shazam_new_track_detected(fresh)
        self.assertEqual(w.search_youtube.call_count, 2)
        self.assertEqual(w.search_youtube.call_args.args[:2], ('月に願う', 'sorato'))

    def test_same_candidate_still_skips_duplicate_search_by_publisher_id(self):
        w = self.win
        w.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        entry = ('2026-10-13 21:11:00', 'Song B', 'Artist B')
        w.shazam_service._last_publication_candidate_id = 42
        w.on_shazam_history_updated([entry])
        w.on_shazam_new_track_detected(entry)
        self.assertEqual(w.search_youtube.call_count, 1)

    def test_fast_confirmation_immediately_updates_from_tentative_to_confirmed(self):
        w = self.win
        self.assertIn('(仮)', w.shazam_table_model.data[0][0])
        w.on_shazam_candidate_confirmed(42, ('', 'Song B', 'Artist B'))
        self.assertIn('(確定・情報補完中)', w.shazam_table_model.data[0][0])
        self.assertNotIn('(仮)', w.shazam_table_model.data[0][0])
        entry = ('2026-10-13 21:11:00', 'Song B', 'Artist B')
        w.shazam_service._last_publication_candidate_id = 42
        w.on_shazam_history_updated([entry])
        self.assertEqual(w.shazam_table_model.data, [entry])
        self.assertEqual(w.shazam_table_model.rowCount(), 1)


if __name__ == '__main__':
    unittest.main()
