"""Comment-column state labels must never become search/history metadata.

Runs the actual table model and MainWindow methods with Qt signal doubles.
No network, native helper, or Windows GUI is used by this module.
"""
import types
import unittest

from tests import test_tieup_enrichment as fixture
from tests.test_autoplay_selection import _methods
from app.models.shazam_state import ShazamStateMixin
from app.services.utanet_service import track_key

TITLE = fixture.TITLE
ARTIST = fixture.ARTIST
COMMENT = fixture.TIEUP
STAMP = '2026-10-20 22:47:40'
TENTATIVE = '(\u4eee)'
CONFIRMED_PENDING = '(\u78ba\u5b9a\u30fb\u60c5\u5831\u88dc\u5b8c\u4e2d)'


class CommentStatusTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.DisplayTests()
        self.model = self.fixture.model()

    def row(self, status='', comment=''):
        stamp = STAMP + (' ' + status if status else '')
        self.model.update_data([(stamp, TITLE, ARTIST)])
        self.fixture.comments[track_key(TITLE, ARTIST)] = comment
        return self.model.data(self.model.index(0, 2))

    def test_tentative_is_visible_in_comment_column(self):
        self.assertEqual(self.row(TENTATIVE), TENTATIVE)

    def test_tentative_and_tieup_are_both_visible(self):
        self.assertEqual(self.row(TENTATIVE, COMMENT), TENTATIVE + ' ' + COMMENT)

    def test_confirmed_pending_is_not_shown_as_tentative(self):
        self.assertEqual(self.row(CONFIRMED_PENDING), CONFIRMED_PENDING)

    def test_confirmed_pending_preserves_tieup(self):
        self.assertEqual(self.row(CONFIRMED_PENDING, COMMENT), CONFIRMED_PENDING + ' ' + COMMENT)

    def test_raw_empty_comment_has_no_label(self):
        self.row(TENTATIVE)
        self.assertEqual(self.model.comment_for_row(0), '')

    def test_raw_comment_has_no_label(self):
        self.row(TENTATIVE, COMMENT)
        self.assertEqual(self.model.comment_for_row(0), COMMENT)

    def test_title_and_artist_unchanged(self):
        self.row(TENTATIVE, COMMENT)
        self.assertEqual([self.model.data(self.model.index(0, c)) for c in (0, 1)], [TITLE, ARTIST])

    def test_commit_removes_label_but_preserves_comment(self):
        self.row(TENTATIVE, COMMENT)
        self.model.update_data([(STAMP, TITLE, ARTIST)])
        self.assertEqual(self.model.data(self.model.index(0, 2)), COMMENT)

    def test_commit_without_comment_is_blank(self):
        self.row(TENTATIVE)
        self.model.update_data([(STAMP, TITLE, ARTIST)])
        self.assertEqual(self.model.data(self.model.index(0, 2)), '')

    def test_comment_refresh_keeps_label_and_selection(self):
        self.row(TENTATIVE)
        resets = self.model.resets
        self.fixture.comments[track_key(TITLE, ARTIST)] = COMMENT
        self.model.refresh_comments()
        self.assertEqual(self.model.resets, resets)
        self.assertEqual(len(self.model.dataChanged.calls), 1)
        self.assertEqual(self.model.data(self.model.index(0, 2)), TENTATIVE + ' ' + COMMENT)

    def test_comment_after_commit_does_not_restore_label(self):
        self.row(TENTATIVE)
        self.model.update_data([(STAMP, TITLE, ARTIST)])
        self.fixture.comments[track_key(TITLE, ARTIST)] = COMMENT
        self.model.refresh_comments()
        self.assertEqual(self.model.data(self.model.index(0, 2)), COMMENT)

    def test_display_does_not_mutate_metadata(self):
        self.row(TENTATIVE, COMMENT)
        stored = list(self.model._data)
        comments = dict(self.fixture.comments)
        for c in range(3):
            self.model.data(self.model.index(0, c))
            self.model.data(self.model.index(0, c), 3)
        self.assertEqual(self.model._data, stored)
        self.assertEqual(self.fixture.comments, comments)

    def test_search_on_tentative_uses_raw_tieup(self):
        self.row(TENTATIVE, COMMENT)
        method = _methods('MainWindow', ['_get_track_search_fields'])['_get_track_search_fields']
        win = types.SimpleNamespace(source_mode='shazam', table_model=self.model)
        self.assertEqual(method(win, 0), (TITLE, ARTIST, COMMENT))

    def test_search_on_empty_tentative_does_not_search_label(self):
        self.row(TENTATIVE)
        method = _methods('MainWindow', ['_get_track_search_fields'])['_get_track_search_fields']
        win = types.SimpleNamespace(source_mode='shazam', table_model=self.model)
        self.assertEqual(method(win, 0), (TITLE, ARTIST, ''))

    def test_tooltip_keeps_timestamp_and_status(self):
        self.row(TENTATIVE, COMMENT)
        tip = self.model.data(self.model.index(0, 2), 3)
        self.assertIn(STAMP + ' ' + TENTATIVE, tip)
        self.assertIn(COMMENT, tip)

    def test_unknown_status_is_not_interpreted(self):
        self.assertEqual(self.row('(arbitrary)', COMMENT), COMMENT)

    def test_status_must_be_a_complete_timestamp_suffix(self):
        self.row('', COMMENT)
        self.model.update_data([(TENTATIVE + ' ' + STAMP, TITLE, ARTIST)])
        self.assertEqual(self.model.data(self.model.index(0, 2)), COMMENT)

    def test_missing_timestamp_is_accepted(self):
        self.row('', COMMENT)
        self.model.update_data([(None, TITLE, ARTIST)])
        self.assertEqual(self.model.data(self.model.index(0, 2)), COMMENT)

    def test_real_comment_containing_label_is_not_stripped(self):
        actual = 'Working title ' + TENTATIVE
        self.assertEqual(self.row('', actual), actual)
        self.assertEqual(self.model.comment_for_row(0), actual)

    def test_invalid_index_does_not_raise(self):
        self.row(TENTATIVE)
        self.assertIsNone(self.model.data(self.model.index(-1, -1)))
        self.assertIsNone(self.model.data(self.model.index(999, 2)))
        self.assertEqual(self.model.data(self.model.index(0, 3)), '')

    def test_short_row_does_not_raise(self):
        self.model.update_data([(STAMP,)])
        self.assertEqual(self.model.data(self.model.index(0, 2)), '')
        self.assertEqual(self.model.comment_for_row(0), '')


class MainWindowStatusTransitionsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.DisplayTests()
        self.model = self.fixture.model()
        cls = type('StatusWindow', (ShazamStateMixin,), _methods(
            'MainWindow', ['_refresh_shazam_history_view', '_get_track_search_fields']))
        self.win = cls()
        self.win.shazam_table_model = self.model
        self.win.table_model = self.model
        self.win.source_mode = 'shazam'
        self.selected = []
        self.win.right_table = types.SimpleNamespace(selectRow=self.selected.append)
        self.candidate = self.win.shazam_flow.begin_vote(56, (STAMP, TITLE, ARTIST))
        self.fixture.comments[track_key(TITLE, ARTIST)] = COMMENT

    def display(self):
        return self.model.data(self.model.index(0, 2))

    def test_first_vote_only_adds_display_label(self):
        self.win._refresh_shazam_history_view([])
        self.assertEqual(self.display(), TENTATIVE + ' ' + COMMENT)
        self.assertFalse(self.candidate.confirmed)
        self.assertFalse(self.candidate.committed)
        self.assertFalse(self.candidate.play_sent)
        self.assertEqual(self.candidate.entry, (STAMP, TITLE, ARTIST))

    def test_two_votes_replace_label_without_changing_confirmation(self):
        self.candidate.confirmed = True
        self.win._refresh_shazam_history_view([])
        self.assertEqual(self.display(), CONFIRMED_PENDING + ' ' + COMMENT)
        self.assertFalse(self.candidate.committed)
        self.assertFalse(self.candidate.play_sent)

    def test_history_commit_clears_label(self):
        self.candidate.confirmed = True
        self.candidate.committed = True
        history = [(STAMP, TITLE, ARTIST)]
        self.win._refresh_shazam_history_view(history)
        self.assertEqual(self.display(), COMMENT)
        self.assertEqual(self.model._data, history)

    def test_failed_lookup_does_not_clear_tentative_row(self):
        self.fixture.comments.clear()
        self.win._refresh_shazam_history_view([])
        self.model.refresh_comments()
        self.assertEqual(self.model.rowCount(), 1)
        self.assertEqual(self.display(), TENTATIVE)
        self.assertEqual(self.win._get_track_search_fields(0), (TITLE, ARTIST, ''))

    def test_lookup_success_before_confirmation_keeps_label(self):
        self.fixture.comments.clear()
        self.win._refresh_shazam_history_view([])
        self.fixture.comments[track_key(TITLE, ARTIST)] = COMMENT
        self.model.refresh_comments()
        self.assertEqual(self.display(), TENTATIVE + ' ' + COMMENT)
        self.assertFalse(self.candidate.confirmed)
        self.assertFalse(self.candidate.play_sent)

    def test_rejection_does_not_leave_label_on_previous_history(self):
        history = [('2026-10-20 22:00:00', 'Previous song', 'Previous artist')]
        self.win._refresh_shazam_history_view(history)
        self.win._shazam_preview = None
        self.win._refresh_shazam_history_view(history)
        self.assertEqual(self.model.rowCount(), 1)
        self.assertEqual(self.display(), '')
        self.assertEqual(self.win._get_track_search_fields(0), ('Previous song', 'Previous artist', ''))


if __name__ == '__main__':
    unittest.main()
