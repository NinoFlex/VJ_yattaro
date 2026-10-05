"""Offline artist-field regressions: Qt, HTTP and worker scheduling are mocked."""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from tests.test_webview_integration import ShazamService, service_module
from app.services.itunes_metadata import ITunesMetadataResolver, clean_artist

PROMO = 'Apple Music \u306b\u63a5\u7d9a\u3057\u3066\u3001Shazam \u5185\u3067\u66f2\u5168\u4f53\u3092\u30d5\u30eb\u518d\u751f\u3057\u307e\u3057\u3087\u3046\u3002 \u306b\u63a5\u7d9a'
KIT = '\u958b\u767a\u8005\u5411\u3051ShazamKit'
URL = 'https://www.shazam.com/ja-jp/track/40820351/orange'


class ArtistValidationTests(unittest.TestCase):
    def test_exact_log_pollution_is_rejected(self):
        for value in (PROMO, KIT, 'AppleMusic\u3067\u8074\u304f', 'Apple Music',
                      'Listen on Apple Music', 'Connect to Apple Music',
                      'ShazamKit for developers', 'Get the app'):
            with self.subTest(value=value):
                self.assertEqual(clean_artist(value), '')

    def test_normalization_rejects_promotional_variants(self):
        for value in (PROMO.replace(' ', '\u00a0'), PROMO.replace('Apple', 'APPLE'),
                      PROMO.replace('Music', 'Mu\u200bsic'),
                      PROMO.replace('Apple', '\uff21\uff50\uff50\uff4c\uff45')):
            with self.subTest(value=value):
                self.assertEqual(clean_artist(value), '')

    def test_legitimate_artist_names_are_preserved(self):
        for value in ('Live', '311', '!!!', 'Lefties Soul Connection', 'Apple',
                      'The Music', 'SMAP', 'Earth, Wind & Fire', '\u798f\u5c71 \u96c5\u6cbb'):
            with self.subTest(value=value):
                self.assertEqual(clean_artist(value), value)

    def test_only_old_proximity_provenance_is_rejected(self):
        for source in ('route-slug-dom', 'track-heading-nearby', 'route-slug-dom+apple-track-id'):
            with self.subTest(source=source):
                self.assertEqual(clean_artist('Unlisted arbitrary text', source), '')
        for source in ('track-heading', 'jsonld', 'recognition-response', 'route-slug-artist', ''):
            with self.subTest(source=source):
                self.assertEqual(clean_artist('Artist', source), 'Artist')

    def test_metadata_cleanup_still_applies(self):
        self.assertEqual(clean_artist(' A &amp; B '), 'A & B')
        for value in ('x' * 501, 'Page not found', None):
            self.assertEqual(clean_artist(value), '')


class ResolverArtistTests(unittest.TestCase):
    def setUp(self):
        self.resolver = ITunesMetadataResolver()
        self.raw = {'title': 'Orange', 'artist': PROMO, 'url': URL,
                    'shazamTrackId': '40820351', 'source': 'jsonld'}

    def test_page_recovers_artist_even_when_title_was_already_available(self):
        with patch.object(self.resolver, '_resolve_route_id_candidate', return_value=None), \
             patch.object(self.resolver, '_shazam_page_metadata',
                          return_value=('Orange', 'Fixture Artist', URL, True)) as page:
            self.assertEqual(self.resolver.resolve(self.raw, 'ja-JP', 'JP'), ('Orange', 'Fixture Artist'))
            page.assert_called_once()

    def test_missing_page_keeps_title_but_not_pollution(self):
        with patch.object(self.resolver, '_resolve_route_id_candidate', return_value=None), \
             patch.object(self.resolver, '_shazam_page_metadata', return_value=('', '', '', False)):
            self.assertEqual(self.resolver.resolve(self.raw, 'ja-JP', 'JP'), ('Orange', ''))

    def test_different_page_id_cannot_supply_an_artist(self):
        with patch.object(self.resolver, '_resolve_route_id_candidate', return_value=None), \
             patch.object(self.resolver, '_shazam_page_metadata',
                          return_value=('Orange', 'Wrong Artist', URL.replace('40820351', '99999999'), False)):
            self.assertEqual(self.resolver.resolve(self.raw, 'ja-JP', 'JP'), ('Orange', ''))

    def test_real_page_parser_checks_identity_and_artist(self):
        for page_id, performer, expected in (
            ('40820351', 'Fixture Artist', 'Fixture Artist'),
            ('99999999', 'Wrong Artist', ''),
            ('40820351', PROMO, ''),
        ):
            with self.subTest(page_id=page_id, performer=performer):
                resolver = ITunesMetadataResolver()
                response = Mock()
                response.text = (f'<meta property="og:title" content="Orange - {performer} | Shazam">'
                                 f'<meta property="og:url" content="{URL.replace("40820351", page_id)}">')
                with patch('requests.get', return_value=response), \
                     patch.object(resolver, '_resolve_route_id_candidate', return_value=None):
                    self.assertEqual(resolver.resolve(self.raw, 'ja-JP', 'JP'), ('Orange', expected))

    def test_cache_never_retains_a_promotional_artist(self):
        key = ('fixture',)
        self.assertEqual(self.resolver._cache(key, ('Orange', PROMO)), ('Orange', ''))
        self.assertEqual(self.resolver._result_cache[key][1], ('Orange', ''))

    def test_direct_response_without_route_is_sanitized(self):
        with patch('requests.get') as get:
            result = self.resolver.resolve({'title': 'Orange', 'artist': KIT,
                                            'source': 'recognition-response'}, 'ja-JP', 'JP')
            self.assertEqual(result, ('Orange', ''))
            get.assert_not_called()


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class ServiceArtistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history_path = Path(self.tmp.name) / 'history.json'
        config = types.SimpleNamespace(get=lambda key, default=None: default)
        with patch('app.services.config_service.ConfigService', return_value=config), \
             patch.object(ShazamService, '_get_history_path', return_value=self.history_path):
            self.svc = ShazamService()
        self.svc._active = True
        self.svc._generation = 1
        self.raw = {'title': 'Orange', 'artist': PROMO, 'url': URL,
                    'shazamTrackId': '40820351', 'source': 'jsonld'}

    def tearDown(self):
        self.svc.shutdown()
        self.tmp.cleanup()

    def resolve(self, raw=None, artist=None, seq=0):
        raw = dict(self.raw if raw is None else raw)
        with patch.object(service_module.threading, 'Thread', ImmediateThread):
            self.svc._resolve_or_publish_confirmed_track(
                0, seq, raw, 'Orange', raw['artist'] if artist is None else artist)

    def test_legacy_source_does_not_take_the_complete_fast_path(self):
        with patch.object(self.svc._metadata_resolver, 'resolve', return_value=('Orange', 'Fixture Artist')) as resolve, \
             patch.object(self.svc, '_stage_temporal_result') as stage:
            self.resolve(dict(self.raw, source='route-slug-dom'))
            resolve.assert_not_called()
            self.assertEqual(stage.call_args.args, (0, 0))

    def test_complete_trusted_result_keeps_no_lookup_fast_path(self):
        with patch.object(self.svc._metadata_resolver, 'resolve') as resolve, \
             patch.object(self.svc, '_stage_temporal_result') as stage:
            self.resolve(dict(self.raw, artist='Fixture Artist', source='track-primary-header'))
            resolve.assert_not_called()
            self.assertEqual(stage.call_args.args[2:4], ('Orange', 'Fixture Artist'))

    def test_pollution_is_rejected_even_for_trusted_source(self):
        with patch.object(self.svc._metadata_resolver, 'resolve', return_value=('Orange', '')) as resolve, \
             patch.object(self.svc, '_stage_temporal_result') as stage:
            self.resolve(dict(self.raw, source='track-primary-header'))
            resolve.assert_called_once()
            self.assertEqual(stage.call_args.args[2:4], ('Orange', ''))

    def test_empty_resolution_does_not_restore_rejected_fallback(self):
        with patch.object(self.svc._metadata_resolver, 'resolve', return_value=('', '')), \
             patch.object(self.svc, '_stage_temporal_result') as stage:
            self.resolve()
            self.assertEqual(stage.call_args.args[2:4], ('Orange', ''))

    def test_exception_does_not_restore_rejected_fallback(self):
        with patch.object(self.svc._metadata_resolver, 'resolve', side_effect=OSError('offline')), \
             patch.object(self.svc, '_stage_temporal_result') as stage:
            self.resolve()
            self.assertEqual(stage.call_args.args[2:4], ('Orange', ''))

    def test_bad_resolver_value_is_also_rejected(self):
        with patch.object(self.svc._metadata_resolver, 'resolve', return_value=('Orange', KIT)), \
             patch.object(self.svc, '_stage_temporal_result') as stage:
            self.resolve()
            self.assertEqual(stage.call_args.args[2:4], ('Orange', ''))

    def test_exception_preserves_independently_safe_fallback(self):
        with patch.object(self.svc._metadata_resolver, 'resolve', side_effect=OSError('offline')), \
             patch.object(self.svc, '_stage_temporal_result') as stage:
            self.resolve(dict(self.raw, artist='Fixture Artist', source='shazam-route'))
            self.assertEqual(stage.call_args.args[2:4], ('Orange', 'Fixture Artist'))

    def test_two_confirmations_save_blank_artist_and_emit_no_pollution(self):
        with patch.object(self.svc._metadata_resolver, 'resolve', return_value=('Orange', '')):
            self.resolve(seq=0)
            self.resolve(seq=1)
        self.assertEqual(self.svc.get_history()[0][1:], ('Orange', ''))
        self.assertEqual(self.svc.new_track_detected.calls[-1][0][1:], ('Orange', ''))
        persisted = self.history_path.read_text(encoding='utf-8')
        self.assertNotIn('Apple', persisted)
        self.assertEqual(len(json.loads(persisted)), 1)

    def test_final_publish_guard_also_sanitizes_artist(self):
        self.svc._publish_confirmed_track(0, 0, 'Orange', KIT)
        self.assertEqual(self.svc.get_history()[0][1:], ('Orange', ''))


if __name__ == '__main__':
    unittest.main()
