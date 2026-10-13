"""Offline regressions for exact Shazam /track page artist recovery.

The primary-header fixture is a reduced reproduction of the user's saved
Ray Of Bullet Shazam page. Browser-generated CSS hash suffixes may change.
"""
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

from app.services.itunes_metadata import (
    ITunesMetadataResolver, _same_shazam_route, _parse_shazam_primary_header,
)

FIXTURE = Path(__file__).parent / 'fixtures' / 'shazam_structural' / 'ray_of_bullet_primary_header.html'
ROUTE = 'https://www.shazam.com/ja-jp/track/228936711/ray-of-bullet'
FALLBACK = 'https://www.shazam.com/ja-jp/track/228936711/unavailable'
ARTIST = '銃皇無尽のファフニー & リス・フレイア、物部深月(声優:沼倉 (声優:日高里菜)'


def page_reply(page):
    reply = Mock(status_code=200)
    reply.text = page
    reply.raise_for_status.return_value = None
    return reply


def apple_empty():
    reply = page_reply('')
    reply.json.return_value = {'results': []}
    return reply


class ShazamPrimaryPageArtistTests(unittest.TestCase):
    def test_fixture_contains_exact_header_fields_without_og_metadata(self):
        title, artist = _parse_shazam_primary_header(FIXTURE.read_text(encoding='utf-8'))
        self.assertEqual(title, 'Ray Of Bullet')
        self.assertEqual(artist, ARTIST)

    def test_track_and_song_ids_are_different_namespaces(self):
        self.assertFalse(_same_shazam_route(
            ROUTE, 'https://www.shazam.com/song/228936711/other-song'))
        self.assertTrue(_same_shazam_route(
            ROUTE, 'https://www.shazam.com/track/228936711/unavailable'))

    def test_same_id_saved_page_recovers_artist_without_wrong_redirect(self):
        requested = []
        wrong = ('<html><head><meta property="og:title" content="Dtmf - Bad Artist | Shazam">'
                 '<link rel="canonical" href="https://www.shazam.com/song/1787023936/dtmf">'
                 '</head></html>')
        good = FIXTURE.read_text(encoding='utf-8')

        def request(url, **kwargs):
            requested.append(url)
            if url.startswith('https://itunes.apple.com/'):
                return apple_empty()
            if url == ROUTE:
                return page_reply(wrong)
            if url == FALLBACK:
                return page_reply(good)
            raise AssertionError('Unexpected request: ' + url)

        result = {'url': ROUTE, 'shazamTrackId': '228936711', 'source': 'shazam-route'}
        resolver = ITunesMetadataResolver()
        with patch('requests.get', side_effect=request):
            self.assertEqual(resolver.resolve(result, 'ja-JP', 'JP'), ('Ray Of Bullet', ARTIST))
            before = len(requested)
            self.assertEqual(resolver.resolve(result, 'ja-JP', 'JP'), ('Ray Of Bullet', ARTIST))
            self.assertEqual(len(requested), before, 'identical candidate should use result cache')
        self.assertIn(FALLBACK, requested)
        self.assertNotIn(ROUTE, requested, 'do not visit known redirect-prone slug after exact page succeeds')

    def test_original_url_is_tried_if_id_bound_page_fails(self):
        source = FIXTURE.read_text(encoding='utf-8')
        requested = []

        def request(url, **kwargs):
            requested.append(url)
            if 'itunes.apple.com' in url:
                return apple_empty()
            if url == FALLBACK:
                return page_reply('<meta property="og:url" content="https://www.shazam.com/song/1787023936/dtmf">')
            if url == ROUTE:
                return page_reply(source)
            raise AssertionError(url)

        with patch('requests.get', side_effect=request):
            self.assertEqual(ITunesMetadataResolver().resolve(
                {'url': ROUTE, 'shazamTrackId': '228936711', 'source': 'shazam-route'},
                'ja-JP', 'JP'), ('Ray Of Bullet', ARTIST))
        self.assertIn(ROUTE, requested)
        self.assertLess(requested.index(FALLBACK), requested.index(ROUTE))

    def test_unrelated_id_never_supplies_artist_even_with_same_title(self):
        bad = FIXTURE.read_text(encoding='utf-8').replace('228936711', '99999999')
        requested = []

        def request(url, **kwargs):
            requested.append(url)
            return apple_empty() if 'itunes.apple.com' in url else page_reply(bad)

        with patch('requests.get', side_effect=request):
            result = ITunesMetadataResolver().resolve(
                {'url': ROUTE, 'shazamTrackId': '228936711', 'source': 'shazam-route'},
                'ja-JP', 'JP')
        self.assertEqual(result, ('Ray Of Bullet', ''))

    def test_primary_header_rejects_unrelated_song_cards_and_promo(self):
        good = FIXTURE.read_text(encoding='utf-8')
        changed = good.replace(
            'TrackPageArtistLink_artistNameText__aBao_',
            'TrackPageArtistLink_notAnArtistField__aBao_'
        ).replace('</body>', '<section><h2>Ray Of Bullet</h2><span>Wrong Artist</span>'
                       '<p>Apple Music に接続</p></section></body>')
        title, artist = _parse_shazam_primary_header(changed)
        self.assertEqual(title, 'Ray Of Bullet')
        self.assertEqual(artist, '')

    def test_multiple_primary_headers_are_rejected(self):
        good = FIXTURE.read_text(encoding='utf-8')
        start = good.index('<div class="NewTrackPageHeader_trackContent__')
        end = good.index('</body>', start)
        header = good[start:end]
        changed = good.replace('</body>', header.replace('Ray Of Bullet', 'Wrong Song') + '</body>')
        self.assertEqual(_parse_shazam_primary_header(changed), ('', ''))

    def test_css_module_hashes_may_change(self):
        changed = FIXTURE.read_text(encoding='utf-8').replace(
            'NewTrackPageHeader_trackTitle__YYbL7', 'NewTrackPageHeader_trackTitle__NewHash'
        ).replace(
            'TrackPageArtistLink_artistNameText__aBao_', 'TrackPageArtistLink_artistNameText__AnotherHash'
        )
        self.assertEqual(_parse_shazam_primary_header(changed), ('Ray Of Bullet', ARTIST))

    def test_conflicting_og_id_cannot_override_correct_canonical(self):
        page = FIXTURE.read_text(encoding='utf-8').replace(
            '</head>', '<meta property="og:url" '
            'content="https://www.shazam.com/track/99999999/other"></head>'
        )

        def request(url, **kwargs):
            return apple_empty() if 'itunes.apple.com' in url else page_reply(page)

        with patch('requests.get', side_effect=request):
            self.assertEqual(ITunesMetadataResolver().resolve(
                {'url': ROUTE, 'shazamTrackId': '228936711', 'source': 'shazam-route'},
                'ja-JP', 'JP'), ('Ray Of Bullet', ''))

    def test_stale_primary_header_title_is_rejected(self):
        page = FIXTURE.read_text(encoding='utf-8').replace('>Ray Of Bullet</div>', '>Other Song</div>')

        def request(url, **kwargs):
            return apple_empty() if 'itunes.apple.com' in url else page_reply(page)

        with patch('requests.get', side_effect=request):
            self.assertEqual(ITunesMetadataResolver().resolve(
                {'url': ROUTE, 'shazamTrackId': '228936711', 'source': 'shazam-route'},
                'ja-JP', 'JP'), ('Ray Of Bullet', ''))

    def test_promotional_artist_is_discarded(self):
        changed = FIXTURE.read_text(encoding='utf-8').replace(
            ARTIST.replace('&', '&amp;'), 'Apple Music に接続')
        title, artist = _parse_shazam_primary_header(changed)
        self.assertEqual(title, 'Ray Of Bullet')
        self.assertEqual(artist, '')


if __name__ == '__main__':
    unittest.main()
