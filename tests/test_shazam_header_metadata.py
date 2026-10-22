"""Service-boundary regression for primary header results with no Apple ID.

JavaScript extraction itself is exercised against real DOM by
header_identity_checks.py; here Qt and metadata HTTP are mocked.
"""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from tests.test_webview_integration import ShazamService

FIXTURES = Path(__file__).parent / 'fixtures/shazam_no_jsonld'


class CanonicalHeaderPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = types.SimpleNamespace(get=lambda key, default=None: default)
        with patch('app.services.config_service.ConfigService', return_value=cfg), \
             patch.object(ShazamService, '_get_history_path', return_value=Path(self.tmp.name) / 'history.json'):
            self.service = ShazamService()
        self.service._active = True
        self.service._generation = 1

    def tearDown(self):
        self.service.shutdown()
        self.tmp.cleanup()

    def raw(self, label):
        meta = json.loads((FIXTURES / (label + '.json')).read_text(encoding='utf-8'))
        return dict(title=meta['title'], artist=meta['artist'], url=meta['url'],
                    shazamTrackId=meta['canonical'].split('/track/')[1].split('/')[0],
                    appleTrackId='', appleMusicUrl='', source='track-primary-header')

    def verify_two_votes(self, raw):
        with patch.object(self.service._metadata_resolver, 'resolve') as resolve, \
             patch.object(self.service._metadata_prefetch, 'request') as prefetch:
            self.service._resolve_or_publish_confirmed_track(0, 0, raw, raw['title'], raw['artist'])
            self.assertEqual(self.service.get_history(), [])
            self.assertEqual(len(self.service.candidate_started.calls), 1)
            self.assertEqual(self.service.candidate_started.calls[0][1][1:], (raw['title'], raw['artist']))
            self.service._resolve_or_publish_confirmed_track(1, 1, raw, raw['title'], raw['artist'])
            self.assertEqual(len(self.service.get_history()), 1)
            self.assertEqual(self.service.get_history()[0][1:], (raw['title'], raw['artist']))
            self.assertEqual(len(self.service.candidate_confirmed.calls), 1)
            resolve.assert_not_called()
            prefetch.assert_not_called()

    def test_sentimental_artist_reaches_preview_and_confirmed_history(self):
        self.verify_two_votes(self.raw('sentimental'))

    def test_flyaway_retains_artist_as_displayed_in_saved_page(self):
        self.verify_two_votes(self.raw('flyaway'))

    def test_arbitrary_track_uses_same_path_without_a_special_table(self):
        self.verify_two_votes(dict(title='Arbitrary New Track', artist='Arbitrary Artist',
                                   url='https://www.shazam.com/track/987654321/arbitrary-new-track',
                                   shazamTrackId='987654321', appleTrackId='', appleMusicUrl='',
                                   source='track-primary-header'))

    def test_single_vote_never_allows_play_or_history(self):
        raw = self.raw('sentimental')
        with patch.object(self.service._metadata_prefetch, 'request') as prefetch:
            self.service._resolve_or_publish_confirmed_track(0, 0, raw, raw['title'], raw['artist'])
            self.assertEqual(self.service.get_history(), [])
            self.assertFalse(self.service.candidate_confirmed.calls)
            self.assertFalse(self.service.new_track_detected.calls)
            self.assertEqual(len(self.service.candidate_started.calls), 1)
            prefetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
