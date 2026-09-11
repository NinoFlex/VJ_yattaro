"""Offline regression tests; these mock Qt and HTTP, not a Windows live recognition test."""
import base64
import hashlib
import importlib.util
import io
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
import wave

import numpy as np

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
    def test_invalid_audio_does_not_launch_helper(self):
        client = WebViewRecognizer()
        with self.assertRaises(ValueError): client.recognize(b'bad', 'ja-JP', threading.Event())

    def test_cancelled_work_never_launches_helper(self):
        cancel = threading.Event(); cancel.set()
        client = WebViewRecognizer()
        with patch.object(client, '_ensure_started') as start:
            with self.assertRaises(RecognitionCancelled): client.recognize(b'RIFF' + b'\0' * 44, 'ja-JP', cancel)
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

    def test_request_id_and_utf8_result(self):
        client = WebViewRecognizer()
        destination = queue.Queue()
        class Input:
            def write(self, line):
                req = json.loads(line)
                self.request = req
                destination.put({'type': 'result', 'id': 'stale', 'title': 'Ignore'})
                destination.put({'type': 'result', 'id': req['id'], 'title': '\u7d05\u84ee\u83ef', 'artist': 'LiSA'})
            def flush(self): pass
        process = types.SimpleNamespace(stdin=Input(), poll=lambda: None)
        with patch.object(client, '_ensure_started', return_value=(process, destination)):
            wav = b'RIFF' + b'\0' * 44
            result = client.recognize(wav, 'ja-JP', threading.Event())
            self.assertEqual(result['title'], '\u7d05\u84ee\u83ef')
            self.assertEqual(base64.b64decode(process.stdin.request['wavBase64']), wav)

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


@unittest.skip("legacy 2+2 recognition architecture; replaced by tests/test_temporal_shazam.py")
class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {'shazam_recording_seconds': 6, 'shazam_language': 'ja-JP', 'shazam_endpoint_country': 'JP'}
        fake_config = types.SimpleNamespace(get=lambda k, default=None: self.config.get(k, default))
        with patch('app.services.config_service.ConfigService', return_value=fake_config), \
             patch.object(ShazamService, '_get_history_path', return_value=Path(self.tmp.name) / 'shazam_history.json'):
            self.svc = ShazamService()
        self.svc._active = True
        self.svc._generation = 9

    def tearDown(self):
        self.svc.shutdown()
        for thread in self.svc._worker_threads:
            if thread:
                thread.join(2)
        self.tmp.cleanup()

    def _seed_pair(self, seq, group=0):
        lanes = self.svc._group_lanes(group)
        self.svc._group_busy[group] = True
        for lane in lanes:
            self.svc._lane_busy[lane] = True
        self.svc._recognition_busy = True
        self.svc._pending_group_results[seq] = {
            'group_index': group,
            'lanes': lanes,
            'results': {},
        }
        return lanes

    def _complete_pair(self, seq, left, right=None, group=0, left_error='', right_error=''):
        if right is None:
            right = left
        lanes = self._seed_pair(seq, group)
        self.svc._handle_recognition_finished(9, lanes[0], seq, left[0], left[1], left_error)
        self.svc._handle_recognition_finished(9, lanes[1], seq, right[0], right[1], right_error)

    def test_existing_history_and_signal_contract_requires_pair_confirmation(self):
        lanes = self._seed_pair(0)
        self.svc._handle_recognition_finished(9, lanes[0], 0, '\u7d05\u84ee\u83ef', 'LiSA', '')
        self.assertEqual(self.svc.get_history(), [])
        self.assertEqual(self.svc.new_track_detected.calls, [])
        self.svc._handle_recognition_finished(9, lanes[1], 0, '\u7d05\u84ee\u83ef', 'LiSA', '')
        history = self.svc.get_history()
        self.assertEqual(len(history[0]), 3)
        self.assertEqual(history[0][1:], ('\u7d05\u84ee\u83ef', 'LiSA'))
        self.assertEqual(self.svc.new_track_detected.calls, [(history[0],)])
        self.assertEqual(json.loads(self.svc._history_path.read_text())[0]['title'], '\u7d05\u84ee\u83ef')
        self._complete_pair(1, ('\u7d05\u84ee\u83ef', 'LiSA'), group=1)
        self.assertEqual(len(self.svc.get_history()), 1)

    def test_stopped_generation_cannot_update_ui(self):
        lanes = self._seed_pair(0)
        self.svc._handle_recognition_finished(8, lanes[0], 0, 'Old song', 'Old artist', '')
        self.assertEqual(self.svc.get_history(), [])
        self.svc.stop()
        self.svc._handle_recognition_finished(9, lanes[1], 0, 'Song', 'Artist', '')
        self.assertEqual(self.svc.get_history(), [])

    def test_history_limit_is_still_50(self):
        for i in range(60):
            self._complete_pair(i, (f'{i:04} Song', f'{i:04} Artist'), group=i % 2)
        self.assertEqual(len(self.svc.get_history()), 50)
        self.assertEqual(len(json.loads(self.svc._history_path.read_text())), 50)

    def test_ring_and_recording_duration_are_preserved(self):
        self.svc._audio_callback(np.arange(100000, dtype=np.int16).reshape(-1, 1), 100000, None, None)
        samples = self.svc._snapshot_latest(6)
        self.assertEqual(len(samples), 96000)
        wav = self.svc._pcm_to_wav_bytes(samples)
        with wave.open(io.BytesIO(wav)) as w:
            self.assertEqual((w.getnchannels(), w.getframerate(), w.getnframes()), (1, 16000, 96000))

    def test_low_latency_readiness_timer_is_100ms(self):
        self.assertEqual(self.svc.RECOGNITION_INTERVAL_MS, 100)
        self.assertEqual(self.svc._recognize_timer.interval, 100)

    def test_dispatch_uses_two_staggered_two_lane_groups(self):
        for ready in self.svc._lane_ready:
            ready.set()
        self.svc._audio_callback(np.zeros((96000, 1), np.int16), 96000, None, None)
        self.svc._recognize_tick()
        self.assertEqual(self.svc._lane_busy, [True, True, False, False])
        self.assertEqual([q.qsize() for q in self.svc._work_queues], [1, 1, 0, 0])

        first = self.svc._work_queues[0].queue[0]
        second = self.svc._work_queues[1].queue[0]
        self.assertEqual(first[3], second[3])  # same request sequence
        self.assertIs(first[4], second[4])     # exact same immutable WAV bytes object
        self.assertEqual(first[8], 0.0)        # lane A starts immediately
        self.assertEqual(second[8], 1.0)       # lane B starts one second later

        # The second pair retains the old 4 second stagger instead of bursting now.
        self.svc._recognize_tick()
        self.assertEqual([q.qsize() for q in self.svc._work_queues], [1, 1, 0, 0])
        self.svc._next_group_slot_at = 0.0
        self.svc._recognize_tick()
        self.assertEqual(self.svc._lane_busy, [True, True, True, True])
        self.assertEqual([q.qsize() for q in self.svc._work_queues], [1, 1, 1, 1])
        third = self.svc._work_queues[2].queue[0]
        fourth = self.svc._work_queues[3].queue[0]
        self.assertEqual(third[3], fourth[3])
        self.assertNotEqual(first[3], third[3])
        self.assertEqual(third[8], 0.0)
        self.assertEqual(fourth[8], 1.0)

    def test_pair_workers_start_recognition_one_second_apart(self):
        starts = []
        starts_lock = threading.Lock()
        recognizers = []
        for index in range(4):
            recognizer = Mock()
            recognizer.close = Mock()
            recognizer.prewarm = Mock()
            if index < 2:
                def recognize(_audio, _language, _cancelled, lane=index):
                    with starts_lock:
                        starts.append((lane, time.monotonic()))
                    return {'title': 'Song A', 'artist': 'Artist A', 'source': 'recognition-response'}
                recognizer.recognize.side_effect = recognize
            recognizers.append(recognizer)
        self.svc._web_recognizers = recognizers
        self.svc._metadata_resolver.resolve = Mock(return_value=('Song A', 'Artist A'))
        self.svc.LANE_STAGGER_SECONDS = 0.1
        self.svc._ensure_worker_threads()
        self.svc._lane_ready[0].set()
        self.svc._lane_ready[1].set()
        self.svc._audio_callback(np.zeros((96000, 1), np.int16), 96000, None, None)
        self.svc._recognize_tick()

        deadline = time.monotonic() + 2
        while not self.svc.get_history() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.svc.get_history()[0][1:], ('Song A', 'Artist A'))
        self.assertEqual(len(starts), 2)
        starts_by_lane = dict(starts)
        self.assertGreaterEqual(starts_by_lane[1] - starts_by_lane[0], 0.08)
        self.assertLess(starts_by_lane[1] - starts_by_lane[0], 0.5)

    def test_default_pair_lane_stagger_is_one_second(self):
        self.assertEqual(ShazamService.LANE_STAGGER_SECONDS, 1.0)

    def test_double_check_rejects_fuzzy_title_match(self):
        # History de-duplication intentionally treats these as the same by 4-char prefix,
        # but double-check confirmation must be stricter to improve recognition accuracy.
        self._complete_pair(0, ('ABCD original', 'Alpha'), ('ABCD cover', 'Alpha'))
        self.assertEqual(self.svc.get_history(), [])
        self.assertEqual(self.svc.new_track_detected.calls, [])

    def test_double_check_accepts_artist_mismatch_using_lane_a_artist(self):
        self._complete_pair(0, ('Exact Song', 'Alpha'), ('Exact Song', 'Omega'))
        self.assertEqual(self.svc.get_history()[0][1:], ('Exact Song', 'Alpha'))

    def test_double_check_accepts_only_lane_a_recognition(self):
        self._complete_pair(0, ('Exact Song', 'Alpha'), ('', ''))
        self.assertEqual(self.svc.get_history()[0][1:], ('Exact Song', 'Alpha'))

    def test_single_success_waits_at_most_four_seconds_for_partner(self):
        lanes = self._seed_pair(0)
        self.svc._handle_recognition_finished(9, lanes[0], 0, 'Exact Song', 'Alpha', '')
        self.assertEqual(self.svc.get_history(), [])
        pending = self.svc._pending_group_results[0]
        timer = pending['confirmation_timer']
        self.assertEqual(timer.interval, 4000)
        self.assertTrue(timer.running)

        # Simulate the four-second deadline without sleeping in the test.
        timer.fire()
        self.assertEqual(self.svc.get_history()[0][1:], ('Exact Song', 'Alpha'))
        self.assertNotIn(0, self.svc._pending_group_results)

    def test_partner_within_confirmation_window_is_still_double_checked(self):
        lanes = self._seed_pair(0)
        self.svc._handle_recognition_finished(9, lanes[0], 0, 'Exact Song', 'Alpha', '')
        timer = self.svc._pending_group_results[0]['confirmation_timer']
        self.svc._handle_recognition_finished(9, lanes[1], 0, 'Different Song', 'Alpha', '')
        self.assertFalse(timer.running)
        self.assertEqual(self.svc.get_history(), [])

    def test_late_partner_after_confirmation_timeout_is_ignored(self):
        lanes = self._seed_pair(0)
        self.svc._handle_recognition_finished(9, lanes[0], 0, 'Exact Song', 'Alpha', '')
        self.svc._pending_group_results[0]['confirmation_timer'].fire()
        self.svc._handle_recognition_finished(9, lanes[1], 0, 'Different Song', 'Omega', '')
        self.assertEqual(self.svc.get_history()[0][1:], ('Exact Song', 'Alpha'))
        self.assertEqual(len(self.svc.get_history()), 1)

    def test_peer_callback_after_deadline_cannot_veto_before_timer_event_runs(self):
        lanes = self._seed_pair(0)
        self.svc._handle_recognition_finished(9, lanes[0], 0, 'Exact Song', 'Alpha', '')
        self.svc._pending_group_results[0]['confirmation_deadline'] = 0.0
        self.svc._handle_recognition_finished(9, lanes[1], 0, 'Different Song', 'Omega', '')
        self.assertEqual(self.svc.get_history()[0][1:], ('Exact Song', 'Alpha'))
        self.assertEqual(len(self.svc.get_history()), 1)

    def test_double_check_accepts_only_lane_b_recognition(self):
        self._complete_pair(0, ('', ''), ('Exact Song', 'Beta'))
        self.assertEqual(self.svc.get_history()[0][1:], ('Exact Song', 'Beta'))

    def test_double_check_accepts_other_lane_when_lane_a_errors(self):
        self._complete_pair(0, ('', ''), ('Exact Song', 'Beta'), left_error='temporary failure')
        self.assertEqual(self.svc.get_history()[0][1:], ('Exact Song', 'Beta'))

    def test_double_check_accepts_other_lane_when_lane_b_errors(self):
        self._complete_pair(0, ('Exact Song', 'Alpha'), ('', ''), right_error='temporary failure')
        self.assertEqual(self.svc.get_history()[0][1:], ('Exact Song', 'Alpha'))

    def test_double_check_accepts_missing_artist_on_one_lane(self):
        self._complete_pair(0, ('Exact Song', ''), (' exact   song ', 'Alpha'))
        self.assertEqual(self.svc.get_history()[0][1:], ('Exact Song', 'Alpha'))

    def test_double_check_rejects_when_neither_lane_recognizes(self):
        self._complete_pair(0, ('', ''), ('', ''))
        self.assertEqual(self.svc.get_history(), [])

    def test_double_check_rejects_when_both_lanes_error(self):
        self._complete_pair(0, ('', ''), ('', ''), left_error='left failure', right_error='right failure')
        self.assertEqual(self.svc.get_history(), [])

    def test_group_completion_does_not_overwrite_newer_confirmed_result(self):
        self._complete_pair(4, ('New song', 'Artist'), group=1)
        self._complete_pair(3, ('Old song', 'Artist'), group=0)
        self.assertEqual(self.svc.get_history()[0][1:], ('New song', 'Artist'))
        self.assertEqual(len(self.svc.get_history()), 1)

    def test_title_only_route_fallback_is_reflected_and_deduped(self):
        self._complete_pair(0, ('campari na', ''))
        self.assertEqual(self.svc.get_history()[0][1:], ('campari na', ''))
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)
        self._complete_pair(1, ('campari na', ''), group=1)
        self.assertEqual(len(self.svc.get_history()), 1)
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)

    def test_worker_uses_webview_results_without_shazamio(self):
        recognizers = []
        for _ in range(4):
            recognizer = Mock()
            recognizer.close = Mock()
            recognizer.prewarm = Mock()
            recognizer.recognize.return_value = {
                'title': 'Song A', 'artist': 'Artist A', 'source': 'recognition-response'
            }
            recognizers.append(recognizer)
        self.svc._web_recognizers = recognizers
        self.svc._metadata_resolver.resolve = Mock(return_value=('Song A', 'Artist A'))
        self.svc._ensure_worker_threads()
        self.svc._lane_ready[0].set()
        self.svc._lane_ready[1].set()
        self.svc._audio_callback(np.zeros((96000, 1), np.int16), 96000, None, None)
        self.svc._recognize_tick()
        deadline = time.monotonic() + 2
        while not self.svc.get_history() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.svc.get_history()[0][1:], ('Song A', 'Artist A'))
        recognizers[0].recognize.assert_called_once()
        recognizers[1].recognize.assert_called_once()
        # Complete request-scoped Shazam metadata is already localized and should not
        # pay for an Apple lookup after pair confirmation.
        self.svc._metadata_resolver.resolve.assert_not_called()

    def test_timeout_cancels_slow_peer_instead_of_waiting_for_15_second_helper_deadline(self):
        lanes = self._seed_pair(0)
        peer = Mock()
        peer.cancel_current.return_value = True
        self.svc._web_recognizers[lanes[1]] = peer
        self.svc._handle_recognition_finished(9, lanes[0], 0, 'Exact Song', 'Alpha', '')
        self.svc._pending_group_results[0]['confirmation_timer'].fire()
        peer.cancel_current.assert_called_once_with()
        self.assertEqual(self.svc.get_history()[0][1:], ('Exact Song', 'Alpha'))

    def test_same_route_only_identity_is_confirmed_before_metadata_and_resolved_once(self):
        lanes = self._seed_pair(0)
        raw_a = {
            'title': '', 'artist': '', 'source': 'shazam-route',
            'shazamTrackId': '1234567890',
            'url': 'https://www.shazam.com/ja-jp/song/1234567890/example-song',
        }
        raw_b = dict(raw_a)
        self.svc._metadata_resolver.resolve = Mock(return_value=('日本語曲名', '日本語アーティスト'))
        self.svc._raw_lane_results[(9, lanes[0], 0)] = raw_a
        self.svc._raw_lane_results[(9, lanes[1], 0)] = raw_b
        self.svc._handle_recognition_finished(9, lanes[0], 0, '', '', '')
        self.assertIn('confirmation_timer', self.svc._pending_group_results[0])
        self.svc._handle_recognition_finished(9, lanes[1], 0, '', '', '')
        deadline = time.monotonic() + 2
        while not self.svc.get_history() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.svc.get_history()[0][1:], ('日本語曲名', '日本語アーティスト'))
        self.assertEqual(self.svc._metadata_resolver.resolve.call_count, 1)

    def test_different_route_only_ids_are_rejected_without_metadata_network_work(self):
        lanes = self._seed_pair(0)
        self.svc._metadata_resolver.resolve = Mock(return_value=('Should not', 'Run'))
        self.svc._raw_lane_results[(9, lanes[0], 0)] = {
            'shazamTrackId': '1234567890', 'source': 'shazam-route'}
        self.svc._raw_lane_results[(9, lanes[1], 0)] = {
            'shazamTrackId': '9876543210', 'source': 'shazam-route'}
        self.svc._handle_recognition_finished(9, lanes[0], 0, '', '', '')
        self.svc._handle_recognition_finished(9, lanes[1], 0, '', '', '')
        self.assertEqual(self.svc.get_history(), [])
        self.svc._metadata_resolver.resolve.assert_not_called()

    def test_alternating_original_cover_emits_only_one_new_track(self):
        original = ('\u7089\u5fc3\u878d\u89e3 (feat. \u93e1\u97f3\u30ea\u30f3)', 'iroha(sasaki)')
        cover = ('\u7089\u5fc3\u878d\u89e3(\u30ab\u30d0\u30fc)feat. \u30ea\u30c4\u30ab', 'iroha')
        with patch.object(self.svc, '_save_history', wraps=self.svc._save_history) as save:
            for seq, track in enumerate((original, cover, original, cover)):
                self._complete_pair(seq, track, group=seq % 2)
            self.assertEqual(save.call_count, 1)
        self.assertEqual(len(self.svc.get_history()), 1)
        self.assertEqual(self.svc.get_history()[0][1:], original)
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)
        self.assertEqual(len(self.svc.history_updated.calls), 1)
        self.assertEqual(self.svc._latest_published_sequence, 3)

    def test_nonconsecutive_same_track_is_emitted_again(self):
        tracks = (('ABCD original', 'Alpha'),
                  ('Different song', 'Elsewhere'),
                  ('ABCD cover', 'Omega'))
        for seq, track in enumerate(tracks):
            self._complete_pair(seq, track, group=seq % 2)
        self.assertEqual(len(self.svc.get_history()), 3)
        self.assertEqual(len(self.svc.new_track_detected.calls), 3)

    def test_four_character_history_rule_ignores_artist_changes_after_confirmation(self):
        self._complete_pair(0, ('ABCD original', 'Alpha'))
        self._complete_pair(1, ('ABCD cover', 'Omega'), group=1)
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)

    def test_only_three_matching_characters_emits_new_track(self):
        self._complete_pair(0, ('ABCD first', 'Alpha'))
        self._complete_pair(1, ('ABCE second', 'Alpha'), group=1)
        self.assertEqual(len(self.svc.new_track_detected.calls), 2)

    def test_suppressed_detection_remains_the_previous_result(self):
        for seq, title in enumerate(('ABCDEFGH', 'BCDEFGHI', 'CDEFGHIJ')):
            self._complete_pair(seq, (title, 'Alpha'), group=seq % 2)
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)
        self.assertEqual(self.svc._last_track, ('CDEFGHIJ', 'Alpha'))

    def test_new_run_is_not_merged_into_older_history_head(self):
        for seq, title in enumerate(('ABCDEFGH', 'BCDEFGHI', 'CDEFGHIJ', 'ABCDEF reprise')):
            self._complete_pair(seq, (title, 'Alpha'), group=seq % 2)
        self.assertEqual(len(self.svc.new_track_detected.calls), 2)
        self.assertEqual(len(self.svc.get_history()), 2)

    def test_no_match_or_error_does_not_reset_previous_track(self):
        self._complete_pair(0, ('ABCD original', 'Alpha'))
        self._complete_pair(1, ('', ''), group=1)
        self._complete_pair(2, ('', ''), group=0, left_error='temporary failure')
        self._complete_pair(3, ('ABCD cover', 'Omega'), group=1)
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)

    def test_late_confirmed_result_after_suppressed_cover_is_still_stale(self):
        self._complete_pair(0, ('ABCD original', 'Alpha'))
        self._complete_pair(4, ('ABCD cover', 'Omega'), group=1)
        self._complete_pair(3, ('Different song', 'Elsewhere'), group=0)
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)
        self.assertEqual(self.svc._last_track, ('ABCD cover', 'Omega'))

    def test_old_generation_cannot_release_current_pair(self):
        self.svc._lane_busy = [True, True, True, True]
        self.svc._group_busy = [True, True]
        self.svc._recognition_busy = True
        self.svc._handle_recognition_finished(8, 0, 99, 'Stale song', 'Alpha', '')
        self.assertEqual(self.svc._lane_busy, [True, True, True, True])
        self.assertEqual(self.svc._group_busy, [True, True])
        self.assertTrue(self.svc._recognition_busy)
        self.assertIsNone(self.svc._last_track)

    def test_busy_groups_skip_audio_copy_resampling_and_wav_encoding(self):
        self.svc._lane_busy = [True, True, True, True]
        self.svc._group_busy = [True, True]
        with patch.object(self.svc, '_snapshot_latest') as snapshot, \
             patch.object(self.svc, '_resample_to_shazam_rate') as resample, \
             patch.object(self.svc, '_pcm_to_wav_bytes') as encode:
            self.svc._recognize_tick()
        snapshot.assert_not_called()
        resample.assert_not_called()
        encode.assert_not_called()

    def _write_history(self, rows):
        payload = [dict(zip(('timestamp', 'title', 'artist'), row)) for row in rows]
        self.svc._history_path.write_text(json.dumps(payload), encoding='utf-8')

    def test_loading_compacts_adjacent_covers_without_rewriting_file(self):
        rows = [('3', 'ABCD cover', 'Omega'), ('2', 'ABCD original', 'Alpha'),
                ('1', 'Different song', 'Elsewhere')]
        self._write_history(rows)
        before = self.svc._history_path.read_bytes()
        self.assertEqual(self.svc._load_history(), [rows[0], rows[2]])
        self.assertEqual(self.svc._history_path.read_bytes(), before)

    def test_loading_preserves_nonconsecutive_repeats(self):
        rows = [('3', 'ABCD cover', 'Omega'), ('2', 'Different song', 'Elsewhere'),
                ('1', 'ABCD original', 'Alpha')]
        self._write_history(rows)
        self.assertEqual(self.svc._load_history(), rows)

    def test_loading_preserves_title_only_recognition(self):
        rows = [('1', 'ABC', '')]
        self._write_history(rows)
        self.assertEqual(self.svc._load_history(), rows)

    def test_first_live_confirmed_result_after_restart_updates_head_and_triggers_search(self):
        self.svc._history = [('1', 'ABCD original', 'Alpha')]
        self._complete_pair(0, ('ABCD cover', 'Omega'))
        self.assertEqual(len(self.svc.get_history()), 1)
        self.assertEqual(self.svc.get_history()[0][1:], ('ABCD cover', 'Omega'))
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)
        self._complete_pair(1, ('ABCD original', 'Alpha'), group=1)
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)

    def test_next_new_track_saves_compacted_history(self):
        self._write_history([('2', 'ABCD cover', 'Omega'), ('1', 'ABCD original', 'Alpha')])
        self.svc._history = self.svc._load_history()
        self._complete_pair(0, ('Different song', 'Elsewhere'))
        saved = json.loads(self.svc._history_path.read_text())
        self.assertEqual([row['title'] for row in saved], ['Different song', 'ABCD cover'])


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

    def test_no_shazamio_runtime_import(self):
        import ast
        for path in (ROOT / 'app').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse(any(name.startswith(('shazamio', 'aiohttp_retry')) for name in names), str(path))


if __name__ == '__main__':
    unittest.main(verbosity=2)
