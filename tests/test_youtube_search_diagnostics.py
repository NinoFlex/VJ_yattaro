"""HTTP/JSON trace regression for rare zero-result YouTube searches.

No real YouTube request.  Test diagnostic boundaries and verify no API-key/body
leakage.  Successful searches should keep their existing log line count.
"""
import io
import json
import requests
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, Mock
from tests.test_search_templates import youtube_module, YouTubeSearchThread


TEST_KEY = 'AIzaSecretNeverExpose1234567890'


def response(status=200, body=None, content_type='application/json', url=None,
             headers=None, history=None):
    reply = requests.Response()
    reply.status_code = status
    reply.url = url or (
        'https://www.googleapis.com/youtube/v3/search?part=snippet&key=' + TEST_KEY
    )
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False).encode('utf-8')
    reply._content = body if body is not None else b'{}'
    reply._content_consumed = True
    reply.headers['Content-Type'] = content_type
    reply.headers['Server'] = 'ESF'
    for key, value in (headers or {}).items():
        reply.headers[key] = value
    reply.history = history or []
    return reply


class YouTubeHttpDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.thread = YouTubeSearchThread([TEST_KEY], 0, 'Naked Story GARNET CROW',
                                          fallback_queries=['Naked Story'])
        self.thread._query_attempt = 1

    @staticmethod
    def output(callback):
        out = io.StringIO()
        with redirect_stdout(out):
            result = callback()
        return result, out.getvalue()

    def assert_no_secret(self, text):
        self.assertNotIn(TEST_KEY, text)
        self.assertNotIn('key=', text)
        self.assertNotIn('AIza', text)
        self.assertNotIn('body content secrets', text)

    def test_valid_zero_has_one_detailed_line_no_duplicate_zero(self):
        reply = response(body={
            'kind': 'youtube#searchListResponse',
            'pageInfo': {'totalResults': 0, 'resultsPerPage': 20},
            'items': [],
        })
        with patch.object(youtube_module.requests, 'get', return_value=reply):
            found, output = self.output(lambda: self.thread._search_youtube())
        self.assertEqual(found, [])
        self.assertEqual(output.count('\n'), 1)
        for fragment in ('API returned 0 item(s)', 'issue=api-empty', 'http=200',
                         'ct=application/json', 'pageTotal=0', 'items=0',
                         'keyIndex=1', 'attempt=1/2', 'server=ESF',
                         'sha12=', 'redirects=0', 'host=www.googleapis.com'):
            self.assertIn(fragment, output, fragment)
        self.assert_no_secret(output)

    def test_proxy_html_http200_is_not_misdiagnosed_as_real_zero(self):
        reply = response(body=b'<!DOCTYPE html><html>body content secrets ' +
                        TEST_KEY.encode('ascii') + b'</html>',
                        content_type='text/html; charset=UTF-8',
                        headers={'Via': 'internal-proxy'})
        with patch.object(youtube_module.requests, 'get', return_value=reply):
            found, output = self.output(lambda: self.thread._search_youtube())
        self.assertEqual(found, [])  # Preserve previous fallback behavior.
        self.assertEqual(output.count('\n'), 1)
        for fragment in ('issue=invalid-json', 'http=200', 'ct=text/html',
                         'json=error', 'html=1', 'proxyHdr=1', 'sha12='):
            self.assertIn(fragment, output)
        self.assert_no_secret(output)

    def test_empty_json_object_is_classified_as_missing_items(self):
        with patch.object(youtube_module.requests, 'get', return_value=response(body={})):
            result, output = self.output(lambda: self.thread._search_youtube())
        self.assertEqual(result, [])
        self.assertIn('issue=missing-items', output)
        self.assertIn('items=-', output)
        self.assert_no_secret(output)

    def test_error_object_in_http200_does_not_disappear_without_trace(self):
        payload = {'error': {'code': 200, 'message': 'Secret must not be logged',
                             'errors': [{'reason': 'unexpectedInternalError'}]}}
        with patch.object(youtube_module.requests, 'get', return_value=response(body=payload)):
            result, output = self.output(lambda: self.thread._search_youtube())
        self.assertEqual(result, [])
        self.assertIn('issue=api-json-error', output)
        self.assertIn('errorReason=unexpectedInternalError', output)
        self.assertNotIn('Secret must not be logged', output)
        self.assert_no_secret(output)

    def test_nonjson_content_type_with_parseable_json_is_suspicious(self):
        with patch.object(youtube_module.requests, 'get', return_value=response(
            body={'items': []}, content_type='text/plain')):
            result, output = self.output(lambda: self.thread._search_youtube())
        self.assertEqual(result, [])
        self.assertIn('issue=unexpected-content-type', output)
        self.assertIn('ct=text/plain', output)

    def test_empty_items_with_positive_total_is_flagged(self):
        with patch.object(youtube_module.requests, 'get', return_value=response(
            body={'items': [], 'pageInfo': {'totalResults': 100}})):
            result, output = self.output(lambda: self.thread._search_youtube())
        self.assertEqual(result, [])
        self.assertIn('issue=empty-with-positive-total', output)
        self.assertIn('pageTotal=100', output)

    def test_proxy_redirect_records_count_and_host_not_url(self):
        reply = response(body={'items': []}, url='https://corporate.example/block?key=' + TEST_KEY,
                         history=[object()], headers={'X-Cache': 'HIT'})
        with patch.object(youtube_module.requests, 'get', return_value=reply):
            _, output = self.output(lambda: self.thread._search_youtube())
        self.assertIn('redirects=1', output)
        self.assertIn('host=corporate.example', output)
        self.assertIn('proxyHdr=1', output)
        self.assert_no_secret(output)

    def test_normal_success_does_not_add_diagnostic_line(self):
        payload = {
            'items': [{'id': {'videoId': 'works'},
                       'snippet': {'title': 'Naked Story Official',
                                   'thumbnails': {}}}],
            'pageInfo': {'totalResults': 100},
        }
        with patch.object(youtube_module.requests, 'get', return_value=response(body=payload)), \
             patch.object(self.thread, '_filter_shorts', side_effect=lambda videos, _: videos):
            found, output = self.output(lambda: self.thread._search_youtube())
        self.assertEqual([v['video_id'] for v in found], ['works'])
        self.assertEqual(output.count('\n'), 2)
        self.assertNotIn('API diagnostic', output)
        self.assertNotIn('pageTotal=', output)
        self.assert_no_secret(output)

    def test_quota_keeps_rotation_and_reports_key_index_only(self):
        self.thread.api_keys = [TEST_KEY, TEST_KEY + 'B']
        self.thread.active_index = 0
        self.thread.api_key = TEST_KEY
        self.thread.api_key_switched = Mock()
        quota = response(status=403, body={
            'error': {'code': 403, 'message': 'Quota exceeded',
                      'errors': [{'reason': 'quotaExceeded'}]}})
        good = response(body={'items': []})
        with patch.object(youtube_module.requests, 'get', side_effect=[quota, good]):
            result, output = self.output(
                lambda: self.thread._search_with_key_rotation('Naked Story'))
        self.assertEqual(result, [])
        self.assertIn('quota=1', output)
        self.assertIn('keyIndex=1', output)
        self.assertIn('Switched active API key to #2', output)
        self.thread.api_key_switched.emit.assert_called_once_with(2, 2)
        self.assert_no_secret(output)

    def test_transport_proxy_error_masks_key_bearing_exception(self):
        message = ('HTTPSConnectionPool url=' +
                   'https://www.googleapis.com/youtube/v3/search?key=' + TEST_KEY)
        with patch.object(youtube_module.requests, 'get',
                          side_effect=requests.exceptions.ProxyError(message)):
            out = io.StringIO()
            with redirect_stdout(out):
                with self.assertRaisesRegex(RuntimeError,
                                            'YouTube API transport error: ProxyError'):
                    self.thread._search_youtube()
        self.assertIn('issue=transport', out.getvalue())
        self.assertIn('exception=ProxyError', out.getvalue())
        self.assert_no_secret(out.getvalue())

    def test_shorts_only_log_one_reasoned_zero_line(self):
        search = response(body={'items': [
            {'id': {'videoId': 'short1'}, 'snippet': {'title': 'Music', 'thumbnails': {}}},
            {'id': {'videoId': 'short2'}, 'snippet': {'title': 'Music 2', 'thumbnails': {}}},
        ]})
        details = response(
            body={'items': [
                {'id': 'short1', 'contentDetails': {'duration': 'PT20S'}},
                {'id': 'short2', 'contentDetails': {'duration': 'PT59S'}},
            ]}, url='https://www.googleapis.com/youtube/v3/videos?key=' + TEST_KEY,
        )
        with patch.object(youtube_module.requests, 'get', side_effect=[search, details]):
            result, output = self.output(lambda: self.thread._search_youtube())
        self.assertEqual(result, [])
        self.assertEqual(output.count('\n'), 2)
        self.assertIn('0 usable video(s)', output)
        self.assertIn('apiItems=2', output)
        self.assertIn('videoIds=2', output)
        self.assertIn('detailsItems=2', output)
        self.assertIn('under60s=2', output)
        self.assertIn('missingDuration=0', output)
        self.assert_no_secret(output)

    def test_cover_only_log_reason_not_two_generic_zeros(self):
        items = {'items': [{'id': {'videoId': 'cover'},
                            'snippet': {'title': '歌ってみた', 'thumbnails': {}}}]}
        with patch.object(youtube_module.requests, 'get', return_value=response(body=items)) as get:
            result, output = self.output(lambda: self.thread._search_youtube())
        self.assertEqual(result, [])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(output.count('\n'), 2)
        self.assertIn('coverExcluded=1', output)
        self.assertIn('videoIds=0', output)
        self.assert_no_secret(output)

    def test_nonquota_http_error_has_one_safe_diagnostic(self):
        bad = response(status=403, body={
            'error': {'code': 403, 'message': 'sensitive ' + TEST_KEY,
                      'errors': [{'reason': 'accessNotConfigured'}]}
        })
        with patch.object(youtube_module.requests, 'get', return_value=bad):
            out = io.StringIO()
            with redirect_stdout(out):
                with self.assertRaisesRegex(Exception,
                                            'YouTube API error HTTP 403'):
                    self.thread._search_youtube()
        log = out.getvalue()
        self.assertEqual(log.count('API diagnostic'), 1)
        self.assertIn('issue=api-json-error', log)
        self.assertIn('http=403', log)
        self.assertIn('errorReason=accessNotConfigured', log)
        self.assertIn('quota=0', log)
        self.assert_no_secret(log)

    def test_missing_video_id_is_diagnosed(self):
        source = response(body={'items': [{'id': {}, 'snippet': {'title': 'Bad'}}]})
        with patch.object(youtube_module.requests, 'get', return_value=source) as get:
            found, log = self.output(lambda: self.thread._search_youtube())
        self.assertEqual(found, [])
        self.assertEqual(get.call_count, 1)
        self.assertIn('missingVideoId=1', log)
        self.assertIn('detailsItems=-', log)
        self.assert_no_secret(log)

    def test_nonjson_details_log_anomaly_but_preserve_fallback_videos(self):
        primary = response(body={'items': [
            {'id': {'videoId': 'video1'}, 'snippet': {'title': 'Real Music',
                                                      'thumbnails': {}}}]})
        details = response(body=b'<html>Bad proxy page secret ' + TEST_KEY.encode() + b'</html>',
                           content_type='text/html',
                           url='https://www.googleapis.com/youtube/v3/videos?key=' + TEST_KEY)
        with patch.object(youtube_module.requests, 'get', side_effect=[primary, details]):
            found, log = self.output(lambda: self.thread._search_youtube())
        self.assertEqual([x['video_id'] for x in found], ['video1'])
        self.assertEqual(log.count('API diagnostic'), 1)
        self.assertIn('stage=videos', log)
        self.assertIn('issue=invalid-json', log)
        self.assertIn('1 usable video(s)', log)
        self.assert_no_secret(log)

    def test_wrong_json_top_level_is_recorded_before_existing_error(self):
        bad = response(body=['not', 'a', 'search'])
        with patch.object(youtube_module.requests, 'get', return_value=bad):
            out = io.StringIO()
            with redirect_stdout(out):
                with self.assertRaises(AttributeError):
                    self.thread._search_youtube()
        self.assertEqual(out.getvalue().count('API diagnostic'), 1)
        self.assertIn('issue=invalid-json-type', out.getvalue())
        self.assert_no_secret(out.getvalue())

    def test_concurrent_threads_have_separate_correlated_ids(self):
        other = YouTubeSearchThread([TEST_KEY], 0, 'another query')
        self.assertNotEqual(self.thread.search_id, other.search_id)
        self.assertEqual(len(self.thread.search_id), 6)
        out = io.StringIO()
        with redirect_stdout(out), patch.object(self.thread, '_search_with_key_rotation', return_value=[]):
            self.thread._search_with_query_fallbacks()
        log = out.getvalue()
        self.assertIn('sid=' + self.thread.search_id, log)
        self.assertEqual(log.count('query '), 2)


if __name__ == '__main__':
    unittest.main()
