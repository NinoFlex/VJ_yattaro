"""Defense-in-depth at the Python/native helper boundary; no network requests."""
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from tests.test_webview_integration import ShazamService


class StructuralSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = types.SimpleNamespace(get=lambda key, default=None: default)
        with patch('app.services.config_service.ConfigService', return_value=config), \
             patch.object(ShazamService, '_get_history_path', return_value=Path(self.tmp.name) / 'history.json'):
            self.svc = ShazamService()
        self.svc._active = True
        self.svc._generation = 1

    def tearDown(self):
        self.svc.shutdown()
        self.tmp.cleanup()

    def raw(self, source, title='Unlisted homepage caption'):
        return {'source': source, 'title': title, 'artist': 'Artist A, Artist B',
                'appleTrackId': '6811203635', 'shazamTrackId': '',
                'appleMusicUrl': 'https://music.apple.com/jp/album/x/6811203634?i=6811203635',
                'url': 'https://www.shazam.com/ja-jp'}

    def test_legacy_heuristic_sources_never_reach_metadata_resolver(self):
        for source in ['new-result-heading', 'result-region', 'track-heading',
                       'route-slug-artist', 'dialog-heading', '', 'unknown-source',
                       'new-result-heading+shazam-route']:
            with self.subTest(source=source), \
                 patch.object(self.svc._metadata_resolver, 'resolve') as lookup, \
                 patch.object(self.svc, '_stage_temporal_result') as stage:
                raw = self.raw(source)
                self.svc._resolve_or_publish_confirmed_track(0, 0, raw, raw['title'], raw['artist'])
                lookup.assert_not_called()
                stage.assert_called_once_with(0, 0)

    def test_repeated_legacy_heading_cannot_confirm(self):
        raw = self.raw('new-result-heading')
        with patch.object(self.svc._metadata_resolver, 'resolve') as lookup:
            for seq in range(2):
                self.svc._resolve_or_publish_confirmed_track(seq, seq, raw, raw['title'], raw['artist'])
            lookup.assert_not_called()
        self.assertEqual(self.svc.get_history(), [])

    def test_complete_primary_header_has_no_apple_lookup(self):
        raw = dict(self.raw('track-primary-header', 'Momo (feat. HATSUNE MIKU)'),
                   artist='KAIRUI & Sasuke Haraguchi', shazamTrackId='6802294256',
                   url='https://www.shazam.com/song/6802294256/momo-feat-hatsune-miku')
        with patch.object(self.svc._metadata_resolver, 'resolve') as lookup, \
             patch.object(self.svc, '_stage_temporal_result') as stage:
            self.svc._resolve_or_publish_confirmed_track(0, 0, raw, raw['title'], raw['artist'])
            lookup.assert_not_called()
            self.assertEqual(stage.call_args.args[2:4], (raw['title'], raw['artist']))

    def test_primary_header_still_requires_two_confirmations(self):
        raw = dict(self.raw('track-primary-header', 'Orange'), artist='Fixture Artist')
        with patch.object(self.svc._metadata_resolver, 'resolve') as lookup:
            self.svc._resolve_or_publish_confirmed_track(0, 0, raw, raw['title'], raw['artist'])
            self.assertEqual(self.svc.get_history(), [])
            self.svc._resolve_or_publish_confirmed_track(1, 1, raw, raw['title'], raw['artist'])
            lookup.assert_not_called()
        self.assertEqual(len(self.svc.get_history()), 1)
        self.assertEqual(self.svc.get_history()[0][1:], ('Orange', 'Fixture Artist'))


if __name__ == '__main__':
    unittest.main()
