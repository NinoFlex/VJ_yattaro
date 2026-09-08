"""Pure matching rules; no Qt, audio device or network is required."""
import unittest

from app.services.track_matching import deduplicate_history, is_same_track

ORIGINAL = ('\u7089\u5fc3\u878d\u89e3 (feat. \u93e1\u97f3\u30ea\u30f3)', 'iroha(sasaki)')
COVER = ('\u7089\u5fc3\u878d\u89e3(\u30ab\u30d0\u30fc)feat. \u30ea\u30c4\u30ab', 'iroha')


class TrackMatchingTests(unittest.TestCase):
    def test_original_and_cover_match_in_both_directions(self):
        self.assertTrue(is_same_track(ORIGINAL, COVER))
        self.assertTrue(is_same_track(COVER, ORIGINAL))

    def test_four_characters_are_enough_even_with_unrelated_artists(self):
        self.assertTrue(is_same_track(('ABCD original', 'Alpha'), ('ABCD cover', 'Omega')))

    def test_exactly_four_character_base_title_matches(self):
        self.assertTrue(is_same_track(('ABCD', 'Alpha'), ('ABCD cover', 'Omega')))

    def test_only_three_shared_characters_are_not_enough(self):
        self.assertFalse(is_same_track(('ABCD first', 'Alpha'), ('ABCE second', 'Alpha')))

    def test_new_rule_is_prefix_not_substring(self):
        self.assertFalse(is_same_track(('Intro: ABCD', 'Alpha'), ('ABCD cover', 'Omega')))

    def test_short_title_does_not_bypass_the_artist_rule(self):
        self.assertFalse(is_same_track(('ABC', 'Alpha'), ('ABC version', 'Omega')))
        self.assertFalse(is_same_track(('ABC', 'Alpha'), ('ABC', 'Omega')))

    def test_short_titles_keep_legacy_matching(self):
        self.assertTrue(is_same_track(('ABC', 'Alpha'), ('ABC version', 'Alpha')))

    def test_whitespace_and_case_are_normalized(self):
        self.assertTrue(is_same_track(('  aBcD original  ', 'Alpha'), ('ABCD cover', 'Omega')))

    def test_internal_spaces_count_as_characters(self):
        self.assertTrue(is_same_track(('ABC original', 'Alpha'), ('ABC cover', 'Omega')))
        self.assertFalse(is_same_track(('A B C', 'Alpha'), ('ABCD', 'Omega')))

    def test_legacy_six_character_substring_rule_is_retained(self):
        self.assertTrue(is_same_track(('abcdef original', 'Performer'), ('Intro abcdef remix', 'Performer')))

    def test_missing_artist_keeps_title_only_fallback(self):
        self.assertTrue(is_same_track(('ABC', ''), ('ABC', 'Alpha')))
        self.assertTrue(is_same_track(('ABC', 'Alpha'), ('ABC', '')))

    def test_empty_or_missing_title_never_matches(self):
        for previous, current in ((None, ORIGINAL), (ORIGINAL, None),
                                  (('', 'Alpha'), ('', 'Alpha')),
                                  (('   ', ''), ('ABCD', ''))):
            with self.subTest(previous=previous, current=current):
                self.assertFalse(is_same_track(previous, current))


class HistoryMatchingTests(unittest.TestCase):
    def test_cover_run_keeps_only_the_newest_row(self):
        rows = [(str(i), *track) for i, track in enumerate((COVER, ORIGINAL, COVER, ORIGINAL))]
        self.assertEqual(deduplicate_history(rows), [rows[0]])

    def test_nonconsecutive_repeat_is_retained(self):
        rows = [('3', *ORIGINAL), ('2', 'Other song', 'Elsewhere'), ('1', *COVER)]
        self.assertEqual(deduplicate_history(rows), rows)

    def test_input_is_not_mutated_and_order_is_preserved(self):
        rows = [('2', *COVER), ('1', *ORIGINAL)]
        saved = list(rows)
        self.assertEqual(deduplicate_history(rows), rows[:1])
        self.assertEqual(rows, saved)

    def test_compares_actual_neighbors_in_a_fuzzy_chain(self):
        # A~B and B~C, but A!~C under the retained substring heuristic.
        rows = [('3', 'ABCDEFGH', 'Alpha'), ('2', 'BCDEFGHI', 'Alpha'),
                ('1', 'CDEFGHIJ', 'Alpha')]
        self.assertEqual(deduplicate_history(rows), rows[:1])

    def test_empty_history(self):
        self.assertEqual(deduplicate_history([]), [])


if __name__ == '__main__':
    unittest.main()
