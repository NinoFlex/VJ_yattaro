"""Regressions from 2026-10-13 23:44 'Primrose / Ceui' live log.

Recorded: official route ID 65621792, HTTP GET redirects to different
/song/1787023936/dtmf, artist blank in program. User's browser-saved first-
party page shows canonical /track/65621792/unavailable and artist Ceui.
"""
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

from app.services.itunes_metadata import ITunesMetadataResolver, _parse_shazam_primary_header

TITLE = '風のなかのプリムローズ'
ARTIST = 'Ceui'
TRACK_ID = '65621792'
ROUTE = f'https://www.shazam.com/ja-jp/track/{TRACK_ID}/{TITLE}'
LOCALIZED = f'https://www.shazam.com/ja-jp/track/{TRACK_ID}/unavailable'
CANONICAL = f'https://www.shazam.com/track/{TRACK_ID}/unavailable'
FIXTURE = Path(__file__).parent / 'fixtures' / 'shazam_structural' / 'primrose_primary_header.html'
WRONG = ('<html><head><link rel="canonical" '
         'href="https://www.shazam.com/song/1787023936/dtmf"></head>'
         '<body><div class="NewTrackPageHeader_trackContent__hash">'
         '<div class="NewTrackPageHeader_trackTitle__hash">DTMF</div>'
         '<span class="TrackPageArtistLink_artistNameText__hash">Other Artist</span>'
         '</div></body></html>')


def mock_reply(content):
    result = Mock(status_code=200)
    result.text = content
    result.json.return_value = {'results': []}
    result.raise_for_status.return_value = None
    return result


class ShazamPrimroseRecoveryTests(unittest.TestCase):
    def test_saved_primary_header_has_ceui(self):
        self.assertEqual(_parse_shazam_primary_header(FIXTURE.read_text(encoding='utf8')),
                         (TITLE, ARTIST))

    def test_unrelated_http_redirect_does_not_invent_artist(self):
        """Never use a known song/artist mapping when the page is unavailable."""
        requested = []
        def get(url, **kwargs):
            requested.append(url)
            return mock_reply('') if 'itunes.apple.com' in url else mock_reply(WRONG)
        with patch('requests.get', side_effect=get):
            self.assertEqual(ITunesMetadataResolver().resolve(
                {'url': ROUTE, 'shazamTrackId': TRACK_ID, 'source': 'shazam-route'},
                'ja-JP', 'JP'), (TITLE, ''))
        self.assertIn(LOCALIZED, requested)
        self.assertIn(CANONICAL, requested)

    def test_same_id_primary_header_recovers_ceui_via_general_path(self):
        """Artist comes from this response's primary header, not a local table."""
        requested = []
        good = FIXTURE.read_text(encoding='utf8')
        def get(url, **kwargs):
            requested.append(url)
            if 'itunes.apple.com' in url:
                return mock_reply('')
            return mock_reply(good if url == LOCALIZED else WRONG)
        with patch('requests.get', side_effect=get):
            self.assertEqual(ITunesMetadataResolver().resolve(
                {'url': ROUTE, 'shazamTrackId': TRACK_ID, 'source': 'shazam-route'},
                'ja-JP', 'JP'), (TITLE, ARTIST))
        self.assertIn(LOCALIZED, requested)

    def test_arbitrary_other_id_and_artist_follow_same_generic_recovery(self):
        """Demonstrate recovery without any ID or artist-specific runtime cases."""
        another_id = '987654321'
        another_title = 'Morning Horizon'
        another_artist = 'New Independent Band'
        another_page = (FIXTURE.read_text(encoding='utf8')
                        .replace(TRACK_ID, another_id)
                        .replace(TITLE, another_title)
                        .replace(ARTIST, another_artist))
        another_route = f'https://www.shazam.com/ja-jp/track/{another_id}/morning-horizon'
        exact_url = f'https://www.shazam.com/ja-jp/track/{another_id}/unavailable'
        requested = []
        def get(url, **kwargs):
            requested.append(url)
            if 'itunes.apple.com' in url:
                return mock_reply('')
            return mock_reply(another_page if url == exact_url else WRONG)
        with patch('requests.get', side_effect=get):
            self.assertEqual(ITunesMetadataResolver().resolve(
                {'url': another_route, 'shazamTrackId': another_id, 'source': 'shazam-route'},
                'ja-JP', 'JP'), (another_title, another_artist))
        self.assertIn(exact_url, requested)

    def test_exact_primary_page_recovers_unlisted_artist(self):
        requested = []
        good = (Path(__file__).parent / 'fixtures' / 'shazam_structural' /
                'ray_of_bullet_primary_header.html').read_text(encoding='utf8')
        ray_route = 'https://www.shazam.com/ja-jp/track/228936711/ray-of-bullet'
        ray_localized = 'https://www.shazam.com/ja-jp/track/228936711/unavailable'
        ray_canonical = 'https://www.shazam.com/track/228936711/unavailable'
        expected = _parse_shazam_primary_header(good)
        def get(url, **kwargs):
            requested.append(url)
            return mock_reply('') if 'itunes.apple.com' in url else mock_reply(good if url == ray_localized else WRONG)
        with patch('requests.get', side_effect=get):
            self.assertEqual(ITunesMetadataResolver().resolve(
                {'url': ray_route, 'shazamTrackId': '228936711', 'source': 'shazam-route'},
                'ja-JP', 'JP'), expected)
        self.assertNotIn(ray_canonical, requested)
        self.assertEqual(requested.count(ray_route), 1)

    def test_canonical_retry_succeeds_for_unlisted_artist(self):
        requested = []
        good = (Path(__file__).parent / 'fixtures' / 'shazam_structural' /
                'ray_of_bullet_primary_header.html').read_text(encoding='utf8')
        ray_route = 'https://www.shazam.com/ja-jp/track/228936711/ray-of-bullet'
        ray_canonical = 'https://www.shazam.com/track/228936711/unavailable'
        def get(url, **kwargs):
            requested.append(url)
            return mock_reply('') if 'itunes.apple.com' in url else mock_reply(good if url == ray_canonical else WRONG)
        with patch('requests.get', side_effect=get):
            self.assertEqual(ITunesMetadataResolver().resolve(
                {'url': ray_route, 'shazamTrackId': '228936711', 'source': 'shazam-route'},
                'ja-JP', 'JP'), _parse_shazam_primary_header(good))
        self.assertIn(ray_canonical, requested)
        self.assertIn(ray_route, requested)

    def test_wrong_route_id_never_receives_another_tracks_artist(self):
        def get(url, **kwargs):
            return mock_reply('') if 'itunes.apple.com' in url else mock_reply(WRONG)
        different = f'https://www.shazam.com/ja-jp/track/78999999/{TITLE}'
        with patch('requests.get', side_effect=get):
            self.assertEqual(ITunesMetadataResolver().resolve(
                {'url': different, 'shazamTrackId': '78999999', 'source': 'shazam-route'},
                'ja-JP', 'JP'), (TITLE, ''))

    def test_reported_id_must_match_real_route_id(self):
        def get(url, **kwargs):
            return mock_reply('') if 'itunes.apple.com' in url else mock_reply(WRONG)
        wrong_route = f'https://www.shazam.com/ja-jp/track/78999999/{TITLE}'
        with patch('requests.get', side_effect=get):
            result = ITunesMetadataResolver().resolve(
                {'url': wrong_route, 'shazamTrackId': TRACK_ID, 'source': 'shazam-route'},
                'ja-JP', 'JP')
        self.assertEqual(result, (TITLE, ''))

    def test_song_namespace_does_not_accept_unrelated_track_metadata(self):
        def get(url, **kwargs):
            return mock_reply('') if 'itunes.apple.com' in url else mock_reply(WRONG)
        song = f'https://www.shazam.com/ja-jp/song/{TRACK_ID}/{TITLE}'
        with patch('requests.get', side_effect=get):
            self.assertEqual(ITunesMetadataResolver().resolve(
                {'url': song, 'shazamTrackId': TRACK_ID, 'source': 'shazam-route'},
                'ja-JP', 'JP'), (TITLE, ''))

    def test_browser_complete_metadata_remains_authoritative(self):
        requested = []
        def get(url, **kwargs):
            requested.append(url)
            return mock_reply('') if 'itunes.apple.com' in url else mock_reply(WRONG)
        with patch('requests.get', side_effect=get):
            result = ITunesMetadataResolver().resolve({
                'url': ROUTE, 'shazamTrackId': TRACK_ID,
                'title': TITLE, 'artist': 'Another Verified Artist',
                'source': 'track-heading',
            }, 'ja-JP', 'JP')
        self.assertEqual(result, (TITLE, 'Another Verified Artist'))


if __name__ == '__main__':
    unittest.main()
