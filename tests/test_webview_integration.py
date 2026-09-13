"""Offline regression tests; these mock Qt and HTTP, not a Windows live recognition test."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch, Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.itunes_metadata import ITunesMetadataResolver, clean_metadata, apple_id_from_url, is_generic_ui_title
from app.services.shazam_webview_client import WebViewRecognizer, RecognitionCancelled, normalize_language


class Emitter:
    def __init__(self): self.calls, self.handlers = [], []
    def connect(self, handler): self.handlers.append(handler)
    def emit(self, *args):
        self.calls.append(args)
        for handler in self.handlers: handler(*args)


class Signal:
    def __init__(self, *args): pass
    def __set_name__(self, owner, name): self.name = '_testsignal_' + name
    def __get__(self, obj, owner):
        if obj is None: return self
        if not hasattr(obj, self.name): setattr(obj, self.name, Emitter())
        return getattr(obj, self.name)


class QObject:
    def __init__(self, parent=None): pass


class QTimer:
    def __init__(self, parent=None):
        self.timeout = Emitter(); self.running = False; self.single_shot = False
    def setInterval(self, interval): self.interval = interval
    def setSingleShot(self, single_shot): self.single_shot = bool(single_shot)
    def start(self): self.running = True
    def stop(self): self.running = False
    def fire(self):
        if not self.running:
            return
        if self.single_shot:
            self.running = False
        self.timeout.emit()


qt = types.ModuleType('PySide6.QtCore')
qt.QObject, qt.QTimer, qt.Signal = QObject, QTimer, Signal
fake_pyside = types.ModuleType('PySide6')
fake_pyside.QtCore = qt
with patch.dict(sys.modules, {'PySide6': fake_pyside, 'PySide6.QtCore': qt}):
    spec = importlib.util.spec_from_file_location('_service_under_test', ROOT / 'app/services/shazam_service.py')
    service_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service_module)
ShazamService = service_module.ShazamService


class MetadataTests(unittest.TestCase):
    @staticmethod
    def _json_response(payload, status=200):
        response = Mock(status_code=status)
        response.json.return_value = payload
        response.raise_for_status = Mock()
        response.text = ''
        return response

    def test_localization_is_exact_apple_link_and_cached(self):
        resolver = ITunesMetadataResolver()
        en = self._json_response({'results': [
            {'kind': 'song', 'trackId': 1825279997, 'trackName': 'Sunfaded', 'artistName': 'Reol'}]})
        jp = self._json_response({'results': [
            {'kind': 'song', 'trackId': 1825279997, 'trackName': 'サンフェーデッド', 'artistName': 'Reol'}]})

        def request(url, *args, **kwargs):
            return jp if kwargs.get('params', {}).get('lang') == 'ja_jp' else en

        result = {
            'title': 'Sunfaded', 'artist': 'Reol', 'source': 'recognition-response',
            'appleTrackId': '1825279997',
            'appleMusicUrl': 'https://music.apple.com/jp/album/x/123?i=1825279997',
        }
        with patch('requests.get', side_effect=request) as get:
            expected = ('サンフェーデッド', 'Reol')
            self.assertEqual(resolver.resolve(result, 'ja-JP', 'JP'), expected)
            self.assertEqual(resolver.resolve(result, 'ja-JP', 'JP'), expected)
            self.assertEqual(get.call_count, 2)  # en verification + ja localization, then cache

    def test_no_identity_source_does_not_search_fuzzily(self):
        with patch('requests.get') as get:
            self.assertEqual(
                ITunesMetadataResolver().resolve({'title': 'Song', 'artist': 'Artist'}, 'ja-JP', 'JP'),
                ('Song', 'Artist'),
            )
            get.assert_not_called()

    def test_not_available_keeps_original(self):
        response = self._json_response({'results': []})
        with patch('requests.get', return_value=response):
            result = ITunesMetadataResolver().resolve(
                {'title': 'Original', 'artist': 'A', 'appleTrackId': '1825279997'}, 'ja-JP', 'JP')
            self.assertEqual(result, ('Original', 'A'))

    def test_bad_lookup_kind_is_not_used(self):
        response = self._json_response({'results': [
            {'kind': 'album', 'trackId': 1825279997, 'trackName': 'Album', 'artistName': 'A'}]})
        with patch('requests.get', return_value=response):
            self.assertEqual(
                ITunesMetadataResolver().resolve({'appleTrackId': '1825279997'}, 'ja-JP', 'JP'),
                ('', ''),
            )

    def test_error_text_is_rejected(self):
        for value in ('Page not found', '要求されたページは見つかりませんでした。',
                      'ユーザーが今Shazamで見つけている曲'):
            self.assertEqual(clean_metadata(value), '')

    def test_apple_url_parsing_does_not_confuse_album_or_shazam_ids(self):
        self.assertEqual(apple_id_from_url('https://music.apple.com/jp/album/test/1234567?i=1825279997'), '1825279997')
        self.assertEqual(apple_id_from_url('https://music.apple.com/jp/song/test/1825279997'), '1825279997')
        for value in ('https://music.apple.com/jp/album/test/1234567',
                      'https://www.shazam.com/track/1234567/test',
                      'https://music.apple.com.evil.test/jp/song/x/1825279997'):
            self.assertEqual(apple_id_from_url(value), '')

    def test_metadata_network_error_does_not_lose_original(self):
        with patch('requests.get', side_effect=OSError('offline')):
            self.assertEqual(
                ITunesMetadataResolver().resolve(
                    {'title': 'Song', 'artist': 'A', 'appleTrackId': '1234567'}, 'ja-JP', 'JP'),
                ('Song', 'A'),
            )

    def test_rate_limit_backs_off(self):
        response = self._json_response({}, status=429)
        resolver = ITunesMetadataResolver()
        with patch('requests.get', return_value=response) as get:
            resolver.resolve({'appleTrackId': '1234567'}, 'en-US', 'US')
            resolver.resolve({'appleTrackId': '7654321'}, 'en-US', 'US')
            self.assertEqual(get.call_count, 1)

    def test_live_japanese_title_survives_wrong_apple_and_wrong_static_shazam_metadata(self):
        search = self._json_response({'results': [
            {'kind': 'song', 'trackId': 9999999, 'trackName': 'Loser',
             'artistName': '宮尾美也 (CV.桐谷蝶々)'}]})
        page = self._json_response({})
        page.text = (
            '<html><head>'
            '<meta property="og:title" content="Movin&#39; To The Sun - Other Artist | Shazam">'
            '<meta property="og:url" content="https://www.shazam.com/song/6768755477/movin-to-the-sun">'
            '</head></html>'
        )

        def request(url, *args, **kwargs):
            return search if 'itunes.apple.com' in url else page

        result = {
            'title': 'ふわりずむ', 'artist': '宮尾美也 (CV.桐谷蝶々)', 'source': 'jsonld',
            'shazamTrackId': '1720337354',
            'url': 'https://www.shazam.com/ja-jp/song/1720337354/fuwa-rhythm',
        }
        with patch('requests.get', side_effect=request):
            self.assertEqual(
                ITunesMetadataResolver().resolve(result, 'ja-JP', 'JP'),
                ('ふわりずむ', '宮尾美也 (CV.桐谷蝶々)'),
            )

    def test_strict_apple_search_requires_exact_title_and_artist_and_localizes(self):
        search_jp = self._json_response({'results': [
            {'kind': 'song', 'trackId': 1720337354, 'trackName': 'ふわりずむ',
             'artistName': '宮尾美也 (CV.桐谷蝶々)'},
            {'kind': 'song', 'trackId': 7777777, 'trackName': 'ふわりずむ', 'artistName': 'Wrong Artist'},
        ]})
        search_en = self._json_response({'results': []})
        lookup_jp = self._json_response({'results': [
            {'kind': 'song', 'trackId': 1720337354, 'trackName': 'ふわりずむ',
             'artistName': '宮尾美也 (CV.桐谷蝶々)'}]})

        def request(url, *args, **kwargs):
            params = kwargs.get('params', {})
            if url.endswith('/search'):
                return search_jp if params.get('lang') == 'ja_jp' else search_en
            if url.endswith('/lookup'):
                return lookup_jp
            raise AssertionError(url)

        result = {
            'title': 'ふわりずむ', 'artist': '宮尾美也 (CV.桐谷蝶々)', 'source': 'track-heading',
            'shazamTrackId': '1720337354',
            'url': 'https://www.shazam.com/ja-jp/song/1720337354/fuwa-rhythm',
        }
        with patch('requests.get', side_effect=request):
            self.assertEqual(
                ITunesMetadataResolver().resolve(result, 'ja-JP', 'JP'),
                ('ふわりずむ', '宮尾美也 (CV.桐谷蝶々)'),
            )


    def test_overview_ui_label_cannot_override_verified_apple_track(self):
        resolver = ITunesMetadataResolver()
        en = self._json_response({'results': [
            {'kind': 'song', 'trackId': 763630973, 'trackName': 'Trilogy', 'artistName': 'Kia Mazzi'}]})
        jp = self._json_response({'results': [
            {'kind': 'song', 'trackId': 763630973, 'trackName': 'Trilogy', 'artistName': 'Kia Mazzi'}]})
        def request(url, *args, **kwargs):
            return jp if kwargs.get('params', {}).get('lang') == 'ja_jp' else en
        result = {
            'title': '概要', 'artist': 'Kia Mazzi', 'source': 'result-region',
            'shazamTrackId': '763630973', 'appleTrackId': '763630973',
            'appleMusicUrl': 'https://music.apple.com/jp/album/trilogy/763630962?i=763630973',
            'url': 'https://www.shazam.com/ja-jp/song/763630973/trilogy',
        }
        with patch('requests.get', side_effect=request):
            self.assertEqual(resolver.resolve(result, 'ja-JP', 'JP'), ('Trilogy', 'Kia Mazzi'))

    def test_global_chart_heading_is_recognized_as_shazam_ui_label(self):
        self.assertTrue(is_generic_ui_title('世界トップ200チャート'))
        self.assertTrue(is_generic_ui_title('Global Top 200 Chart'))
        self.assertFalse(is_generic_ui_title('Top 200 feat. Someone'))

    def test_chart_heading_uses_verified_apple_song_title(self):
        resolver = ITunesMetadataResolver()
        en = self._json_response({'results': [
            {'kind': 'song', 'trackId': 6778877440, 'trackName': "Movin' To The Sun",
             'artistName': 'HUGEL, Imael Angel & Ultra Nate'}]})
        jp = self._json_response({'results': [
            {'kind': 'song', 'trackId': 6778877440, 'trackName': "Movin' To The Sun",
             'artistName': 'HUGEL, Imael Angel & Ultra Nate'}]})
        def request(url, *args, **kwargs):
            return jp if kwargs.get('params', {}).get('lang') == 'ja_jp' else en
        result = {
            'title': '世界トップ200チャート',
            'artist': 'HUGEL, Imael Angel & Ultra Nate',
            'source': 'new-result-heading',
            'appleTrackId': '6778877440',
            'appleMusicUrl': 'https://music.apple.com/jp/album/movin-to-the-sun/6778877191?i=6778877440',
        }
        with patch('requests.get', side_effect=request):
            self.assertEqual(
                resolver.resolve(result, 'ja-JP', 'JP'),
                ("Movin' To The Sun", 'HUGEL, Imael Angel & Ultra Nate'),
            )

    def test_overview_is_recognized_as_shazam_ui_label(self):
        self.assertTrue(is_generic_ui_title('概要'))
        self.assertTrue(is_generic_ui_title('Overview'))
        self.assertFalse(is_generic_ui_title('概要 feat. Someone'))

    def test_footer_is_recognized_as_shazam_ui_label(self):
        self.assertTrue(is_generic_ui_title('Shazam フッター'))
        self.assertTrue(is_generic_ui_title('Shazam Footer'))
        self.assertTrue(is_generic_ui_title('footer'))
        self.assertFalse(is_generic_ui_title('Footer feat. Someone'))

    def test_footer_ui_label_falls_back_to_exact_shazam_route_title(self):
        resolver = ITunesMetadataResolver()
        result = {
            'title': 'Shazam フッター', 'artist': 'Metizone', 'source': 'result-region',
            'shazamTrackId': '810532382',
            'url': 'https://www.shazam.com/ja-jp/song/810532382/modular-theorem',
        }
        # No Apple/search/public-page enrichment is required to prevent the UI label.
        with patch('requests.get', side_effect=RuntimeError('offline fixture')):
            self.assertEqual(resolver.resolve(result, 'ja-JP', 'JP'), ('Modular Theorem', 'Metizone'))

    def test_route_only_id_can_localize_when_apple_en_title_proves_same_slug(self):
        resolver = ITunesMetadataResolver()
        en = self._json_response({'results': [
            {'kind': 'song', 'trackId': 1655784202,
             'trackName': 'Inochi Moyashite Koiseyo Otome (Game Version)',
             'artistName': 'Kaede Takagaki & Others'}]})
        jp = self._json_response({'results': [
            {'kind': 'song', 'trackId': 1655784202,
             'trackName': '命燃やして恋せよ乙女 (GAME VERSION)',
             'artistName': '高垣楓ほか'}]})

        def request(url, *args, **kwargs):
            self.assertTrue(url.endswith('/lookup'))
            return jp if kwargs.get('params', {}).get('lang') == 'ja_jp' else en

        result = {
            'shazamTrackId': '1655784202', 'source': 'shazam-route',
            'url': 'https://www.shazam.com/ja-jp/song/1655784202/inochi-moyashite-koiseyo-otome-game-version',
        }
        with patch('requests.get', side_effect=request):
            self.assertEqual(
                resolver.resolve(result, 'ja-JP', 'JP'),
                ('命燃やして恋せよ乙女 (GAME VERSION)', '高垣楓ほか'),
            )


    def test_route_only_romanized_slug_can_use_apple_search_to_prove_same_japanese_track(self):
        resolver = ITunesMetadataResolver()
        en = self._json_response({'results': [
            {'kind': 'song', 'trackId': 1565502610,
             'trackName': '14平米にスーベニア (オリジナル・カラオケ)',
             'artistName': 'Apple JP Artist'}]})
        jp = self._json_response({'results': [
            {'kind': 'song', 'trackId': 1565502610,
             'trackName': '14平米にスーベニア (オリジナル・カラオケ)',
             'artistName': 'Apple JP Artist'}]})
        search = self._json_response({'results': [
            {'kind': 'song', 'trackId': 1565502610,
             'trackName': '14平米にスーベニア (オリジナル・カラオケ)',
             'artistName': 'Apple JP Artist'}]})

        def request(url, *args, **kwargs):
            if url.endswith('/search'):
                self.assertEqual(kwargs.get('params', {}).get('term'), '14 Heibei Ni Souvenir Original Karaoke')
                return search
            self.assertTrue(url.endswith('/lookup'))
            return jp if kwargs.get('params', {}).get('lang') == 'ja_jp' else en

        result = {
            'shazamTrackId': '1565502610', 'source': 'shazam-route',
            'url': 'https://www.shazam.com/ja-jp/song/1565502610/14-heibei-ni-souvenir-original-karaoke',
        }
        with patch('requests.get', side_effect=request):
            self.assertEqual(
                resolver.resolve(result, 'ja-JP', 'JP'),
                ('14平米にスーベニア (オリジナル・カラオケ)', 'Apple JP Artist'),
            )

    def test_legacy_track_route_rejects_unrelated_apple_record(self):
        resolver = ITunesMetadataResolver()
        wrong = self._json_response({'results': [
            {'kind': 'song', 'trackId': 1655784202, 'trackName': 'Completely Different', 'artistName': 'Wrong'}]})
        search = self._json_response({'results': [
            {'kind': 'song', 'trackId': 999999999, 'trackName': 'Inochi Moyashite Koiseyo Otome', 'artistName': 'Someone Else'}]})
        page = self._json_response({})
        page.text = ''
        def request(url, *args, **kwargs):
            if url.endswith('/search'):
                return search
            return wrong if 'itunes.apple.com' in url else page
        result = {
            'shazamTrackId': '1655784202', 'source': 'shazam-route',
            'url': 'https://www.shazam.com/ja-jp/track/1655784202/inochi-moyashite-koiseyo-otome-game-version',
        }
        with patch('requests.get', side_effect=request):
            self.assertEqual(
                resolver.resolve(result, 'ja-JP', 'JP'),
                ('Inochi Moyashite Koiseyo Otome Game Version', ''),
            )

    def test_route_only_song_id_accepts_exact_apple_localization_when_slug_is_translation(self):
        resolver = ITunesMetadataResolver()
        en = self._json_response({'results': [
            {'kind': 'song', 'trackId': 6793804469, 'trackName': '星', 'artistName': '女王蜂'}]})
        jp = self._json_response({'results': [
            {'kind': 'song', 'trackId': 6793804469, 'trackName': '星', 'artistName': '女王蜂'}]})

        def request(url, *args, **kwargs):
            self.assertTrue(url.endswith('/lookup'))
            return jp if kwargs.get('params', {}).get('lang') == 'ja_jp' else en

        result = {
            'shazamTrackId': '6793804469', 'source': 'shazam-route',
            'url': 'https://www.shazam.com/ja-jp/song/6793804469/star',
        }
        with patch('requests.get', side_effect=request) as get:
            self.assertEqual(
                resolver.resolve(result, 'ja-JP', 'JP'),
                ('星', '女王蜂'),
            )
            self.assertEqual(get.call_count, 2)

    def test_route_slug_is_last_resort_instead_of_dropping_recognition(self):
        result = {
            'shazamTrackId': '1729756018',
            'url': 'https://www.shazam.com/ja-jp/song/1729756018/campari-na',
        }
        with patch('requests.get', side_effect=OSError('offline')):
            self.assertEqual(
                ITunesMetadataResolver().resolve(result, 'ja-JP', 'JP'),
                ('Campari Na', ''),
            )

    def test_route_only_exact_public_page_recovers_localized_title_and_artist(self):
        empty = self._json_response({'results': []})
        page = self._json_response({})
        page.text = (
            '<html><head>'
            '<meta property="og:title" content="光 - 櫻井優衣 | Shazam">'
            '<meta property="og:url" content="https://www.shazam.com/ja-jp/song/6776126341/hikari">'
            '</head></html>'
        )

        def request(url, *args, **kwargs):
            return empty if 'itunes.apple.com' in url else page

        result = {
            'shazamTrackId': '6776126341', 'source': 'shazam-route',
            'url': 'https://www.shazam.com/ja-jp/song/6776126341/hikari',
        }
        with patch('requests.get', side_effect=request):
            self.assertEqual(
                ITunesMetadataResolver().resolve(result, 'ja-JP', 'JP'),
                ('光', '櫻井優衣'),
            )

    def test_route_only_public_page_rejects_different_shazam_id(self):
        empty = self._json_response({'results': []})
        page = self._json_response({})
        page.text = (
            '<html><head>'
            '<meta property="og:title" content="Wrong Song - Wrong Artist | Shazam">'
            '<meta property="og:url" content="https://www.shazam.com/ja-jp/song/9999999999/wrong-song">'
            '</head></html>'
        )

        def request(url, *args, **kwargs):
            return empty if 'itunes.apple.com' in url else page

        result = {
            'shazamTrackId': '6776126341', 'source': 'shazam-route',
            'url': 'https://www.shazam.com/ja-jp/song/6776126341/hikari',
        }
        with patch('requests.get', side_effect=request):
            self.assertEqual(
                ITunesMetadataResolver().resolve(result, 'ja-JP', 'JP'),
                ('Hikari', ''),
            )


class ClientTests(unittest.TestCase):
    def test_invalid_sample_rate_does_not_launch_helper(self):
        client = WebViewRecognizer()
        with patch.object(client, '_ensure_started') as start:
            with self.assertRaises(ValueError):
                client.recognize_live(1000, 'ja-JP', threading.Event())
            start.assert_not_called()

    def test_cancelled_work_never_launches_helper(self):
        cancel = threading.Event(); cancel.set()
        client = WebViewRecognizer()
        with patch.object(client, '_ensure_started') as start:
            with self.assertRaises(RecognitionCancelled):
                client.recognize_live(48000, 'ja-JP', cancel)
            start.assert_not_called()

    def test_timeout_is_bounded(self):
        with self.assertRaises(TimeoutError): WebViewRecognizer._next_message(queue.Queue(), time.monotonic() - 1, threading.Event())

    def test_language_is_validated(self):
        self.assertEqual(normalize_language('jp-JP'), 'ja-JP')
        self.assertEqual(normalize_language('en-GB'), 'en-GB')
        self.assertEqual(normalize_language('../profile'), 'ja-JP')

    def test_lane_instance_id_is_sanitized(self):
        client = WebViewRecognizer('../lane two!')
        self.assertEqual(client._instance_id, 'lanetwo')

    def test_live_request_id_and_utf8_result(self):
        client = WebViewRecognizer()
        destination = queue.Queue()
        writes = []
        class Input:
            def write(self, line):
                req = json.loads(line)
                writes.append(req)
                if req['type'] == 'recognize-live':
                    destination.put({'type': 'result', 'id': 'stale', 'title': 'Ignore'})
                    destination.put({'type': 'result', 'id': req['id'], 'title': '\u7d05\u84ee\u83ef', 'artist': 'LiSA'})
            def flush(self): pass
        process = types.SimpleNamespace(stdin=Input(), poll=lambda: None)
        with patch.object(client, '_ensure_started', return_value=(process, destination)):
            result = client.recognize_live(48000, 'ja-JP', threading.Event())
            self.assertEqual(result['title'], '\u7d05\u84ee\u83ef')
            self.assertEqual(writes[0]['type'], 'recognize-live')
            self.assertEqual(writes[0]['sampleRate'], 48000)
            self.assertNotIn('wavBase64', writes[0])

    def test_feed_live_audio_is_nonblocking_and_request_scoped(self):
        client = WebViewRecognizer()
        q = queue.Queue(maxsize=2)
        client._active_request_id = 'a' * 32
        client._live_audio_queue = q
        client._live_sample_rate = 48000
        self.assertTrue(client.feed_live_audio(b'\x01\x00' * 32, 48000))
        self.assertEqual(q.get_nowait(), b'\x01\x00' * 32)
        self.assertFalse(client.feed_live_audio(b'\x02\x00', 44100))
        client._active_request_id = None
        self.assertFalse(client.feed_live_audio(b'\x02\x00', 48000))

    def test_live_audio_batch_uses_request_scoped_audio_command(self):
        client = WebViewRecognizer()
        writes = []
        class Input:
            def write(self, line): writes.append(json.loads(line))
            def flush(self): pass
        process = types.SimpleNamespace(stdin=Input(), poll=lambda: None)
        self.assertTrue(client._send_live_audio_batch(process, 'a' * 32, b'\x01\x00\x02\x00'))
        self.assertEqual(writes[0]['type'], 'audio')
        self.assertEqual(writes[0]['id'], 'a' * 32)
        self.assertEqual(base64.b64decode(writes[0]['pcm16Base64']), b'\x01\x00\x02\x00')

    def test_active_live_recognition_feeds_pcm_while_waiting_for_result(self):
        client = WebViewRecognizer()
        destination = queue.Queue()
        writes = []
        recognize_sent = threading.Event()
        audio_sent = threading.Event()
        class Input:
            def write(self, line):
                req = json.loads(line)
                writes.append(req)
                if req['type'] == 'recognize-live':
                    recognize_sent.set()
                elif req['type'] == 'audio':
                    audio_sent.set()
            def flush(self): pass
        process = types.SimpleNamespace(stdin=Input(), poll=lambda: None)
        client._process = process
        holder = {}
        def run():
            with patch.object(client, '_ensure_started', return_value=(process, destination)):
                holder['result'] = client.recognize_live(16000, 'ja-JP', threading.Event())
        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(recognize_sent.wait(1))
        deadline = time.monotonic() + 1
        while client._active_request_id is None and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertIsNotNone(client._active_request_id)
        self.assertTrue(client.feed_live_audio(b'\x10\x00' * 3200, 16000))
        self.assertTrue(audio_sent.wait(1))
        request_id = next(item['id'] for item in writes if item['type'] == 'recognize-live')
        destination.put({'type': 'result', 'id': request_id, 'title': 'Song', 'artist': 'Artist'})
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(holder['result']['title'], 'Song')
        audio = next(item for item in writes if item['type'] == 'audio')
        self.assertEqual(audio['id'], request_id)
        self.assertEqual(base64.b64decode(audio['pcm16Base64']), b'\x10\x00' * 3200)

    def test_cancel_current_sends_request_scoped_cancel_without_closing_helper(self):
        client = WebViewRecognizer()
        writes = []
        class Input:
            def write(self, line): writes.append(json.loads(line))
            def flush(self): pass
        process = types.SimpleNamespace(stdin=Input(), poll=lambda: None)
        client._process = process
        client._active_request_id = 'a' * 32
        self.assertTrue(client.cancel_current())
        self.assertEqual(writes, [{'type': 'cancel', 'id': 'a' * 32}])
        self.assertIs(client._process, process)


class PreservationTests(unittest.TestCase):
    def test_unrelated_visible_ui_files_are_byte_identical(self):
        hashes = json.loads((ROOT / 'tests/original_ui.sha256.json').read_text())
        intentionally_changed = {"main.py", "ui/dialogs/settings_dialog.py"}
        for relative, expected in hashes.items():
            if relative in intentionally_changed:
                continue
            with self.subTest(path=relative):
                self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), expected)

    def test_settings_exposes_separate_rekordbox_and_shazam_search_templates(self):
        source = (ROOT / 'ui/dialogs/settings_dialog.py').read_text(encoding='utf-8')
        self.assertIn('Rekordbox検索テンプレート:', source)
        self.assertIn('Shazam検索テンプレート:', source)
        self.assertIn('%tracktitle% %artist%', source)

    def test_fast_bridge_observes_official_tag_response_and_clears_busy_before_reply(self):
        source = (ROOT / 'native/ShazamWebViewBridge/BridgeHost.cs').read_text(encoding='utf-8')
        self.assertIn('WebResourceResponseReceived += OnWebResourceResponseReceived', source)
        self.assertIn('uri.AbsolutePath.Contains("/tag/"', source)
        method = source[source.index('private async Task HandleCommandAsync'):source.index('private async Task<Candidate?> RecognizeAsync')]
        self.assertLess(method.index('_busy = false;'), method.rindex('Program.Send(new { type = "result"'))
        self.assertIn('TimeSpan.FromMilliseconds(250)', source)
        self.assertIn('TimeSpan.FromMilliseconds(650)', source)
        self.assertIn('RouteEvidenceWindow = TimeSpan.FromSeconds(4)', source)

    def test_four_parallel_helpers_have_isolated_profiles_and_15s_deadline(self):
        program = (ROOT / 'native/ShazamWebViewBridge/Program.cs').read_text(encoding='utf-8')
        bridge = (ROOT / 'native/ShazamWebViewBridge/BridgeHost.cs').read_text(encoding='utf-8')
        client = (ROOT / 'app/services/shazam_webview_client.py').read_text(encoding='utf-8')
        service = (ROOT / 'app/services/shazam_service.py').read_text(encoding='utf-8')
        self.assertIn('--instance', program)
        self.assertIn('_instanceId', bridge)
        self.assertIn('TimeSpan.FromSeconds(15)', bridge)
        self.assertIn('"--instance", self._instance_id', client)
        self.assertIn('RECOGNITION_GROUPS = 4', service)
        self.assertIn('LANES_PER_GROUP = 1', service)
        self.assertIn('PARALLEL_RECOGNITION_LANES = RECOGNITION_GROUPS * LANES_PER_GROUP', service)
        self.assertIn('GROUP_STAGGER_SECONDS = 3.0', service)
        self.assertIn('LANE_STAGGER_SECONDS = 0.0', service)
        self.assertIn('type == "cancel"', bridge)
        self.assertIn('cancel_current', client)
        self.assertIn('Deferred metadata resolved', service)
        self.assertNotIn('threading.Barrier(self.LANES_PER_GROUP)', service)
        self.assertIn('pool=shared', service)
        self.assertIn('Recognition slot waiting', service)
        self.assertIn('def _find_available_group', service)
        self.assertIn('recognize_live', service)
        self.assertIn('feed_live_audio', service)
        self.assertIn('"type": "recognize-live"', client)
        self.assertIn('"type": "audio"', client)
        self.assertIn('protocol = 2', bridge)
        audio_bridge = (ROOT / 'native/ShazamWebViewBridge/Scripts/audio_bridge.js').read_text(encoding='utf-8')
        self.assertIn('startLive(sampleRate, id)', audio_bridge)
        self.assertIn('type = "audio-chunk"', bridge)
        self.assertNotIn('source.loop = true', audio_bridge)
        self.assertIn('live-pcm-ring-fresh-track', audio_bridge)
        self.assertIn('const mediaDestination = current.createMediaStreamDestination()', audio_bridge)
        self.assertNotIn('__vjAudioBridge.load(', bridge)

    def test_no_shazamio_runtime_import(self):
        import ast
        for path in (ROOT / 'app').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse(any(name.startswith(('shazamio', 'aiohttp_retry')) for name in names), str(path))


if __name__ == '__main__':
    unittest.main(verbosity=2)
