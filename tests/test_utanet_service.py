"""Network-free tests. HTML fixtures are synthetic, not downloaded pages."""
import html
import threading
import unittest
from unittest.mock import Mock
from urllib.parse import urlsplit, parse_qs

import requests
from app.services.utanet_service import (
    UtanetClient, SongCandidate, parse_search_results, parse_song_metadata,
    artist_prefix_matches, base_title, title_matches, safe_url, parse_tree,
)

TITLE = '\u30cf\u30ec\u306e\u65e5\u306b'
ARTIST = '\u6c50\u308c\u3044\u3089'
TIEUP = '\u85ab\u308b\u82b1\u306f\u51db\u3068\u54b2\u304f \u30a8\u30f3\u30c7\u30a3\u30f3\u30b0'
URL = 'https://www.uta-net.com/song/376215/'


def search_html(rows=None):
    rows = [(TITLE, ARTIST, '/song/376215/', '')] if rows is None else rows
    return '<html><body><table>' + ''.join(
        '<tr><td><a href="' + url + '"><span class="songlist-title">' + html.escape(title)
        + '</span><span class="songlist-lyrics">DO NOT COPY THIS LYRIC</span></a></td><td><a href="/artist/1/">'
        + html.escape(artist) + '</a></td><td class="tieup">' + html.escape(tieup) + '</td></tr>'
        for title, artist, url, tieup in rows) + '</table></body></html>'


def song_html(title=TITLE, artist=ARTIST, tieup=TIEUP, canonical=URL):
    return (f'<html><head><link rel="canonical" href="{canonical}"></head><body>'
            f'<h1>{html.escape(artist)} {html.escape(title)} \u6b4c\u8a5e</h1>'
            '<nav>\u30a8\u30f3\u30c7\u30a3\u30f3\u30b0</nav><section>'
            f'<h2>{html.escape(title)}</h2><h3><a href="/artist/1/">{html.escape(artist)}</a></h3>'
            f'<img src="ignored.jpg"><p>{tieup}</p>'
            '<p>\u4f5c\u8a5e\uff1a<a href="/lyricist/1/">Some Name</a><br>\u4f5c\u66f2\uff1aOther</p>'
            '<div id="kashi_area">LYRIC_SECRET<br>WRONG SHOW \u30a8\u30f3\u30c7\u30a3\u30f3\u30b0</div>'
            '<h4>RANKING</h4><p>WRONG PROGRAM \u4e3b\u984c\u6b4c</p></section></body></html>')


class FakeResponse:
    def __init__(self, content='', status=200, headers=None):
        self.content = content.encode('utf-8') if isinstance(content, str) else content
        self.status_code = status
        self.headers = {'Content-Type': 'text/html; charset=UTF-8', **(headers or {})}
        self.closed = False
    def iter_content(self, chunk_size):
        yield self.content
    def close(self):
        self.closed = True
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'HTTP {self.status_code}')


def client_for(*pages, clock=None):
    sess = Mock()
    sess.get.side_effect = [p if isinstance(p, (FakeResponse, Exception)) else FakeResponse(p) for p in pages]
    kwargs = {'clock': clock} if clock else {}
    return UtanetClient(session=sess, interval=0, **kwargs), sess


class MatchingTests(unittest.TestCase):
    def test_first_three_artist_characters(self):
        self.assertTrue(artist_prefix_matches(ARTIST, ARTIST + ' feat. Singer'))
    def test_wrong_prefix(self):
        self.assertFalse(artist_prefix_matches(ARTIST, '\u6c50\u308c\u304a\u3089'))
    def test_two_character_names_exact(self):
        self.assertTrue(artist_prefix_matches('Li', 'Li'))
        self.assertFalse(artist_prefix_matches('Li', 'Lia'))
    def test_empty_artist_rejected(self):
        self.assertFalse(artist_prefix_matches('', ''))
    def test_unicode_normalized(self):
        self.assertTrue(artist_prefix_matches('\uff2d\uff32. Big', 'Mr Big'))
    def test_spaces_and_case(self):
        self.assertTrue(artist_prefix_matches(' T H E NEUTRAL ', 'the neutral'))
    def test_tv_size_title(self):
        self.assertEqual(base_title(TITLE + ' (TV Size)'), TITLE)
    def test_instrumental_title(self):
        self.assertEqual(base_title(TITLE + '-Instrumental-'), TITLE)
    def test_bonus_track_title(self):
        self.assertEqual(base_title(TITLE + ' (Bonus Track)'), TITLE)
    def test_romanized_suffix(self):
        self.assertTrue(title_matches(TITLE + ' - Harenohini', TITLE))
    def test_normal_subtitle_preserved(self):
        self.assertFalse(title_matches('Song (Yesterday)', 'Song (Tomorrow)'))
    def test_four_characters_not_enough_for_title(self):
        self.assertFalse(title_matches('ABCDEFG', 'ABCD123'))
    def test_short_and_long_title(self):
        self.assertFalse(title_matches('Shine', 'Shine Shine'))
    def test_reject_untrusted_url(self):
        for path in ['http://www.uta-net.com/song/1/', 'https://www.uta-net.com.evil.test/song/1/', 'https://evil.test/song/1/', 'https://user@www.uta-net.com/song/1/']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_url(path)
    def test_relative_url(self):
        self.assertEqual(safe_url('/song/376215/'), URL)


class ParserTests(unittest.TestCase):
    def test_result_row_title_not_lyrics(self):
        self.assertEqual(parse_search_results(search_html()), [SongCandidate(TITLE, ARTIST, URL)])
    def test_artist_scoped_to_own_row(self):
        found = parse_search_results(search_html([(TITLE, 'WRONG', '/song/1/', ''), ('Other', ARTIST, '/song/2/', '')]))
        self.assertEqual([x.artist for x in found], ['WRONG', ARTIST])
    def test_search_empty(self):
        self.assertEqual(parse_search_results('<html><p>No results</p></html>'), [])
    def test_search_no_artist(self):
        self.assertEqual(parse_search_results('<p><a href="/song/1/">Song</a></p>'), [])
    def test_search_external_link(self):
        self.assertEqual(parse_search_results(search_html([(TITLE, ARTIST, 'https://evil.test/song/1/', '')])), [])
    def test_search_compact_card(self):
        found = parse_search_results('<div><a href="/song/2/">Title</a><a href="/artist/3/">Artist</a></div>')
        self.assertEqual(found[0].title, 'Title')
    def test_search_inline_tieup(self):
        self.assertEqual(parse_search_results(search_html([(TITLE, ARTIST, '/song/376215/', TIEUP)]))[0].tieup, TIEUP)
    def test_page_plain_text_tieup(self):
        self.assertEqual(parse_song_metadata(song_html(), TITLE, ARTIST, URL).tieup, TIEUP)
    def test_page_link_and_inline_role(self):
        tieup = '<a href="/tieup/1/">\u85ab\u308b\u82b1\u306f\u51db\u3068\u54b2\u304f</a> <span>\u30a8\u30f3\u30c7\u30a3\u30f3\u30b0</span>'
        self.assertEqual(parse_song_metadata(song_html(tieup=tieup), TITLE, ARTIST, URL).tieup, TIEUP)
    def test_page_role_after_break(self):
        self.assertEqual(parse_song_metadata(song_html(tieup=TIEUP.replace(' ', '<br>')), TITLE, ARTIST, URL).tieup, TIEUP)
    def test_page_no_tieup_not_lyrics(self):
        self.assertEqual(parse_song_metadata(song_html(tieup=''), TITLE, ARTIST, URL).tieup, '')
    def test_page_wrong_artist(self):
        self.assertIsNone(parse_song_metadata(song_html(artist='Wrong Artist'), TITLE, ARTIST, URL))
    def test_page_wrong_title(self):
        self.assertIsNone(parse_song_metadata(song_html(title='Other song'), TITLE, ARTIST, URL))
    def test_page_wrong_canonical(self):
        self.assertIsNone(parse_song_metadata(song_html(canonical='https://www.uta-net.com/song/123/'), TITLE, ARTIST, URL))
    def test_page_missing_heading_fails_closed(self):
        self.assertIsNone(parse_song_metadata('<p>' + TIEUP + '</p>', TITLE, ARTIST, URL))
    def test_page_missing_credits_fails_closed(self):
        self.assertIsNone(parse_song_metadata('<h2>'+TITLE+'</h2><h3>'+ARTIST+'</h3><p>'+TIEUP+'</p>', TITLE, ARTIST, URL))
    def test_multiple_tieups_and_duplicates(self):
        second = 'Other show \u4e3b\u984c\u6b4c'
        value = TIEUP + '<br>' + second + '<br>' + TIEUP
        self.assertEqual(parse_song_metadata(song_html(tieup=value), TITLE, ARTIST, URL).tieup, TIEUP+' / '+second)
    def test_parser_discards_lyrics_and_scripts(self):
        tree = parse_tree(song_html()+'<script>SECRET</script>')
        self.assertNotIn('LYRIC_SECRET', tree.text())
        self.assertNotIn('SECRET', tree.text())
    def test_title_html_entities(self):
        self.assertEqual(parse_search_results(search_html([('A & B', ARTIST, '/song/1/', '')]))[0].title, 'A & B')
    def test_complexity_limit(self):
        with self.assertRaises(ValueError):
            parse_tree('<div>'*130)


class HTTPClientTests(unittest.TestCase):
    def test_harenohini_query_artist_and_tieup(self):
        client, session = client_for(search_html(), song_html())
        result = client.lookup(TITLE, ARTIST)
        self.assertEqual((result.status, result.comment), ('found', TIEUP))
        params = parse_qs(urlsplit(session.get.call_args_list[0].args[0]).query)
        self.assertEqual(params['Aselect'], ['2'])
        self.assertEqual(params['Keyword'], [TITLE])
    def test_no_webview_and_tls_verification_not_disabled(self):
        client, session = client_for(search_html(), song_html())
        client.lookup(TITLE, ARTIST)
        for call in session.get.call_args_list:
            self.assertNotIn('verify', call.kwargs)
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertIn('timeout', call.kwargs)
    def test_wrong_artist_does_not_fetch_song(self):
        client, session = client_for(search_html([(TITLE, 'Wrong', '/song/1/', '')]))
        self.assertEqual(client.lookup(TITLE, ARTIST).comment, '')
        self.assertEqual(session.get.call_count, 1)
    def test_wrong_title_does_not_fetch_song(self):
        client, session = client_for(search_html([('Other Song', ARTIST, '/song/1/', '')]))
        self.assertEqual(client.lookup(TITLE, ARTIST).comment, '')
        self.assertEqual(session.get.call_count, 1)
    def test_inline_tieup_avoids_extra_request(self):
        client, session = client_for(search_html([(TITLE, ARTIST, '/song/376215/', TIEUP)]))
        self.assertEqual(client.lookup(TITLE, ARTIST).comment, TIEUP)
        self.assertEqual(session.get.call_count, 1)
    def test_success_cached(self):
        client, session = client_for(search_html(), song_html())
        client.lookup(TITLE, ARTIST)
        self.assertTrue(client.lookup(TITLE, ARTIST).cached)
        self.assertEqual(session.get.call_count, 2)
    def test_missing_artist_no_request(self):
        client, session = client_for()
        self.assertEqual(client.lookup(TITLE, '').status, 'missing_identity')
        session.get.assert_not_called()
    def test_missing_title_no_request(self):
        client, session = client_for()
        self.assertEqual(client.lookup('', ARTIST).status, 'missing_identity')
        session.get.assert_not_called()
    def test_cancelled_no_request(self):
        client, session = client_for()
        cancel = threading.Event(); cancel.set()
        self.assertEqual(client.lookup(TITLE, ARTIST, cancel=cancel).status, 'cancelled')
        session.get.assert_not_called()
    def test_network_failure_empty_not_not_found(self):
        client, session = client_for(requests.ConnectionError('DNS failure'))
        self.assertEqual(client.lookup(TITLE, ARTIST).status, 'error')
    def test_forbidden_cooldown_no_fallback(self):
        client, session = client_for(FakeResponse(status=403))
        self.assertEqual(client.lookup(TITLE, ARTIST).status, 'unavailable')
        self.assertEqual(client.lookup('Other', ARTIST).status, 'cooldown')
        self.assertEqual(session.get.call_count, 1)
    def test_rate_limit_cooldown(self):
        client, session = client_for(FakeResponse(status=429, headers={'Retry-After':'600'}))
        self.assertEqual(client.lookup(TITLE, ARTIST).status, 'unavailable')
        self.assertEqual(client.lookup('Other', ARTIST).status, 'cooldown')
    def test_offsite_redirect_rejected(self):
        client, session = client_for(FakeResponse(status=302, headers={'Location':'https://evil.test/song/1/'}))
        self.assertEqual(client.lookup(TITLE, ARTIST).status, 'error')
        self.assertEqual(session.get.call_count, 1)
    def test_non_html_rejected(self):
        client, _ = client_for(FakeResponse('{}', headers={'Content-Type':'application/json'}))
        self.assertEqual(client.lookup(TITLE, ARTIST).status, 'unavailable')
    def test_response_closed(self):
        response = FakeResponse(search_html())
        client, _ = client_for(response, song_html())
        client.lookup(TITLE, ARTIST)
        self.assertTrue(response.closed)
    def test_no_tieup_leaves_comment_empty(self):
        client, _ = client_for(search_html(), song_html(tieup=''))
        result = client.lookup(TITLE, ARTIST)
        self.assertEqual((result.status, result.comment), ('no_tieup', ''))
    def test_title_variant_query_is_bounded(self):
        client, session = client_for(search_html([]), search_html(), song_html())
        self.assertEqual(client.lookup(TITLE + ' (TV Size)', ARTIST).comment, TIEUP)
        self.assertEqual(session.get.call_count, 3)
    def test_exact_artist_wins_over_prefix_only(self):
        rows=[(TITLE, ARTIST+'Two', '/song/2/', 'Other \u4e3b\u984c\u6b4c'),(TITLE, ARTIST, '/song/376215/', TIEUP)]
        client,_=client_for(search_html(rows))
        self.assertEqual(client.lookup(TITLE,ARTIST).source_url,URL)
    def test_failed_verification_no_comment(self):
        client,_=client_for(search_html(),song_html(artist='Wrong'))
        self.assertEqual(client.lookup(TITLE,ARTIST).comment,'')


if __name__ == '__main__':
    unittest.main()
