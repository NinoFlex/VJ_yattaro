"""Field regression: a different song must not inherit the old preview's clock.

The production ShazamService runs with the repository's Qt/audio/network fakes.
Time and metadata completion are explicit; this is not a live recognition test.
"""
import unittest

from tests import test_shazam_speculative_preload as votes
from tests.test_latency_prefetch import raw_song


class PreviewExpiryTests(unittest.TestCase):
    setUp = votes.ServiceVoteTests.setUp
    tearDown = votes.ServiceVoteTests.tearDown
    vote = votes.ServiceVoteTests.vote
    no_match = votes.ServiceVoteTests.no_match

    TITLE = '\u3042\u306e\u65e5\u30bf\u30a4\u30e0\u30de\u30b7\u30f3'
    SONG_ID = '1536115707'

    def finish(self, song_id, title, artist='LONG SHOT PARTY'):
        key = self.svc._metadata_key(('shazam-song', song_id))
        self.broker.finish(1, key, (title, artist))

    def retained_old_then_unknown(self):
        # Same-title variants legitimately keep one early PRELOAD for up to 30s.
        self.vote(0, raw_song('1856773946', title='Ever be my love', artist='Old Artist'))
        self.now = 110.0
        self.vote(1, raw_song('1856773947', title='Ever be my love (live)', artist='Old Artist'))
        self.now = 124.0
        self.vote(2, raw_song(self.SONG_ID))
        self.assertEqual(self.svc._tentative_candidate['preview_received_at'], 100.0)
        self.assertIn('retained_preview', self.svc._tentative_candidate)

    def resolve_different(self):
        self.retained_old_then_unknown()
        self.now = 124.6
        self.finish(self.SONG_ID, self.TITLE)
        return self.svc._tentative_candidate

    def test_different_title_removes_old_preview_clock(self):
        candidate = self.resolve_different()
        self.assertNotIn('preview_received_at', candidate)
        self.assertNotIn('retained_preview', candidate)
        self.assertEqual(candidate['sequence'], 2)
        self.assertEqual(candidate['observation']['received_at'], 124.0)
        self.assertEqual(self.svc.candidate_rejected.calls, [(0, 'resolved-different-title')])

    def test_timer_rearmed_from_new_receipt_not_metadata_completion(self):
        self.resolve_different()
        # 124.0 + 15s - 124.6, plus the existing 100ms timer tolerance.
        self.assertAlmostEqual(self.svc._tentative_timer.interval, 14500, delta=2)
        self.assertTrue(self.svc._tentative_timer.running)

    def test_old_preview_expiry_does_not_remove_new_song(self):
        self.resolve_different()
        starts = list(self.svc.candidate_started.calls)
        self.now = 130.2
        self.svc._expire_tentative_candidate()
        self.assertIsNotNone(self.svc._tentative_candidate)
        self.assertEqual(self.svc._tentative_candidate['sequence'], 2)
        self.assertEqual(self.svc.candidate_started.calls, starts)
        self.assertEqual(self.svc.candidate_rejected.calls, [(0, 'resolved-different-title')])

    def test_new_song_still_expires_under_its_own_limit(self):
        self.resolve_different()
        self.now = 139.2
        self.svc._expire_tentative_candidate()
        self.assertIsNone(self.svc._tentative_candidate)
        self.assertEqual(self.svc.candidate_rejected.calls[-1], (2, 'first-vote-expired'))
        self.assertFalse(self.svc._tentative_timer.running)
        self.assertEqual(self.svc.get_history(), [])

    def test_second_vote_after_old_deadline_confirms_new_song_once(self):
        self.resolve_different()
        self.now = 130.2
        self.svc._expire_tentative_candidate()
        self.now = 131.0
        self.vote(3, raw_song(self.SONG_ID))
        self.assertEqual([x[0] for x in self.svc.candidate_started.calls], [0, 2])
        self.assertEqual([x[0] for x in self.svc.candidate_confirmed.calls], [2])
        self.assertEqual([x[1] for x in self.svc.get_history()], [self.TITLE])
        self.assertFalse(self.svc._tentative_timer.running)

    def test_expired_new_receipt_never_starts_a_search_when_metadata_arrives(self):
        self.vote(0, raw_song('1856773946', title='Ever be my love', artist='Old Artist'))
        self.now = 105.0
        self.vote(1, raw_song(self.SONG_ID))
        self.now = 121.0  # New first vote expired; old 30s preview is not expired.
        self.finish(self.SONG_ID, self.TITLE)
        self.assertEqual([x[0] for x in self.svc.candidate_started.calls], [0])
        self.assertIsNone(self.svc._tentative_candidate)
        self.assertFalse(self.svc._tentative_timer.running)
        self.assertEqual(self.svc.candidate_rejected.calls, [(0, 'resolved-different-title')])
        self.assertFalse(self.svc.candidate_confirmed.calls)
        self.assertEqual(self.svc.get_history(), [])

    def test_already_confirmed_new_song_with_late_metadata_is_not_expired(self):
        self.vote(0, raw_song('1856773946', title='Ever be my love', artist='Old Artist'))
        self.now = 110.0
        self.vote(1, raw_song(self.SONG_ID))
        self.now = 112.0
        self.vote(2, raw_song(self.SONG_ID))
        self.now = 114.0
        confirmed_candidate = self.svc._tentative_candidate
        self.finish(self.SONG_ID, self.TITLE)
        self.assertNotIn('preview_received_at', confirmed_candidate)
        self.assertEqual([x[0] for x in self.svc.candidate_started.calls], [0, 1])
        self.assertEqual([x[0] for x in self.svc.candidate_confirmed.calls], [1])
        self.assertEqual([x[1] for x in self.svc.get_history()], [self.TITLE])
        self.assertFalse(self.svc._tentative_timer.running)

    def test_actual_title_variant_keeps_original_lifetime_and_search_owner(self):
        self.retained_old_then_unknown()
        self.now = 124.6
        self.finish(self.SONG_ID, 'Ever be my love (radio)', 'Old Artist')
        candidate = self.svc._tentative_candidate
        self.assertEqual(candidate['preview_received_at'], 100.0)
        self.assertEqual(candidate['sequence'], 0)
        self.assertEqual([x[0] for x in self.svc.candidate_started.calls], [0])
        self.assertFalse(self.svc.candidate_confirmed.calls)  # IDs differ.
        self.now = 130.2
        self.svc._expire_tentative_candidate()
        self.assertEqual(self.svc.candidate_rejected.calls[-1], (0, 'title-variant-preview-expired'))

    def test_next_unknown_preview_inherits_new_song_receipt_not_earlier_song(self):
        self.resolve_different()
        self.now = 125.0
        self.vote(3, raw_song('99999999'))
        candidate = self.svc._tentative_candidate
        self.assertEqual(candidate['preview_received_at'], 124.0)
        self.assertEqual(candidate['retained_preview']['sequence'], 2)

    def test_stop_and_late_metadata_cannot_revive_replaced_song(self):
        self.retained_old_then_unknown()
        self.svc.stop()
        before = list(self.svc.candidate_started.calls)
        self.now = 126.0
        self.finish(self.SONG_ID, self.TITLE)
        self.assertEqual(self.svc.candidate_started.calls, before)
        self.assertIsNone(self.svc._tentative_candidate)
        self.assertEqual(self.svc.get_history(), [])


if __name__ == '__main__':
    unittest.main()
