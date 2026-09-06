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
    def __init__(self, parent=None): self.timeout = Emitter(); self.running = False
    def setInterval(self, interval): self.interval = interval
    def start(self): self.running = True
    def stop(self): self.running = False


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

    def test_route_only_id_rejects_unrelated_apple_record(self):
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
            'url': 'https://www.shazam.com/ja-jp/song/1655784202/inochi-moyashite-koiseyo-otome-game-version',
        }
        with patch('requests.get', side_effect=request):
            self.assertEqual(
                resolver.resolve(result, 'ja-JP', 'JP'),
                ('Inochi Moyashite Koiseyo Otome Game Version', ''),
            )

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

    def test_existing_history_and_signal_contract(self):
        self.svc._handle_recognition_finished(9, 0, 0, '\u7d05\u84ee\u83ef', 'LiSA', '')
        history = self.svc.get_history()
        self.assertEqual(len(history[0]), 3)
        self.assertEqual(history[0][1:], ('\u7d05\u84ee\u83ef', 'LiSA'))
        self.assertEqual(self.svc.new_track_detected.calls, [(history[0],)])
        self.assertEqual(json.loads(self.svc._history_path.read_text())[0]['title'], '\u7d05\u84ee\u83ef')
        self.svc._handle_recognition_finished(9, 0, 0, '\u7d05\u84ee\u83ef', 'LiSA', '')
        self.assertEqual(len(self.svc.get_history()), 1)

    def test_stopped_generation_cannot_update_ui(self):
        self.svc._handle_recognition_finished(8, 0, 0, 'Old song', 'Old artist', '')
        self.assertEqual(self.svc.get_history(), [])
        self.svc.stop()
        self.svc._handle_recognition_finished(9, 0, 1, 'Song', 'Artist', '')
        self.assertEqual(self.svc.get_history(), [])

    def test_history_limit_is_still_50(self):
        for i in range(60):
            self.svc._handle_recognition_finished(9, 0, i, f'{i:06} Song', f'{i:06} Artist', '')
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

    def test_dispatch_uses_two_staggered_lanes(self):
        self.svc._audio_callback(np.zeros((96000, 1), np.int16), 96000, None, None)
        self.svc._recognize_tick()
        self.assertTrue(self.svc._lane_busy[0])
        self.assertEqual(self.svc._work_queues[0].qsize(), 1)
        self.assertEqual(self.svc._work_queues[1].qsize(), 0)
        # Global stagger prevents a burst on the same instant.
        self.svc._recognize_tick()
        self.assertEqual(self.svc._work_queues[1].qsize(), 0)
        # Once the stagger slot arrives, lane 2 can run while lane 1 is still busy.
        self.svc._next_lane_slot_at = 0.0
        self.svc._recognize_tick()
        self.assertTrue(all(self.svc._lane_busy))
        self.assertEqual(self.svc._work_queues[1].qsize(), 1)
        self.assertEqual(self.svc._next_request_at, 0.0)

    def test_parallel_completion_does_not_overwrite_newer_result(self):
        self.svc._handle_recognition_finished(9, 1, 4, 'New song', 'Artist', '')
        self.svc._handle_recognition_finished(9, 0, 3, 'Old song', 'Artist', '')
        self.assertEqual(self.svc.get_history()[0][1:], ('New song', 'Artist'))
        self.assertEqual(len(self.svc.get_history()), 1)

    def test_title_only_route_fallback_is_reflected_and_deduped(self):
        self.svc._handle_recognition_finished(9, 0, self.svc._request_sequence, 'campari na', '', '')
        self.assertEqual(self.svc.get_history()[0][1:], ('campari na', ''))
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)
        self.svc._handle_recognition_finished(9, 0, self.svc._request_sequence, 'campari na', '', '')
        self.assertEqual(len(self.svc.get_history()), 1)
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)

    def test_worker_uses_webview_result_without_shazamio(self):
        recognizer = Mock()
        recognizer.recognize.return_value = {'title': 'Song A', 'artist': 'Artist A', 'source': 'recognition-response'}
        self.svc._web_recognizers[0] = recognizer
        self.svc._metadata_resolver.resolve = Mock(return_value=('Song A', 'Artist A'))
        self.svc._ensure_worker_threads()
        self.svc._lane_busy[0] = True
        self.svc._work_queues[0].put((
            9, 0, 0, b'RIFF', 'ja-JP', 'JP', self.svc._cancel_current
        ))
        deadline = time.monotonic() + 2
        while not self.svc.get_history() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.svc.get_history()[0][1:], ('Song A', 'Artist A'))
        recognizer.recognize.assert_called_once()


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
        self.assertIn('TimeSpan.FromMilliseconds(1000)', source)

    def test_parallel_helpers_have_isolated_profiles_and_15s_deadline(self):
        program = (ROOT / 'native/ShazamWebViewBridge/Program.cs').read_text(encoding='utf-8')
        bridge = (ROOT / 'native/ShazamWebViewBridge/BridgeHost.cs').read_text(encoding='utf-8')
        client = (ROOT / 'app/services/shazam_webview_client.py').read_text(encoding='utf-8')
        service = (ROOT / 'app/services/shazam_service.py').read_text(encoding='utf-8')
        self.assertIn('--instance', program)
        self.assertIn('_instanceId', bridge)
        self.assertIn('TimeSpan.FromSeconds(15)', bridge)
        self.assertIn('"--instance", self._instance_id', client)
        self.assertIn('PARALLEL_RECOGNITION_LANES = 2', service)
        self.assertIn('LANE_STAGGER_SECONDS = 4.0', service)

    def test_no_shazamio_runtime_import(self):
        import ast
        for path in (ROOT / 'app').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse(any(name.startswith(('shazamio', 'aiohttp_retry')) for name in names), str(path))


if __name__ == '__main__':
    unittest.main(verbosity=2)
