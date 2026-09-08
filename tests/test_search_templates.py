import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[1]


class _DummyQObject:
    pass


class _DummyQThread:
    pass


class _DummySignal:
    def __init__(self, *args, **kwargs):
        pass


class _DummyQt:
    KeepAspectRatio = 0
    SmoothTransformation = 0


class _DummyPixmap:
    pass


qtcore = types.ModuleType('PySide6.QtCore')
qtcore.QObject = _DummyQObject
qtcore.Signal = _DummySignal
qtcore.QThread = _DummyQThread
qtcore.Qt = _DummyQt
qtgui = types.ModuleType('PySide6.QtGui')
qtgui.QPixmap = _DummyPixmap
pyside = types.ModuleType('PySide6')
pyside.QtCore = qtcore
pyside.QtGui = qtgui

with patch.dict(sys.modules, {
    'PySide6': pyside,
    'PySide6.QtCore': qtcore,
    'PySide6.QtGui': qtgui,
}):
    spec = importlib.util.spec_from_file_location(
        '_youtube_service_under_test', ROOT / 'app/services/youtube_service.py'
    )
    youtube_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(youtube_module)

YouTubeService = youtube_module.YouTubeService
YouTubeSearchThread = youtube_module.YouTubeSearchThread


class _Config:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)


class SearchTemplateTests(unittest.TestCase):
    def _service(self, values):
        service = object.__new__(YouTubeService)
        service.config_service = _Config(values)
        return service

    def test_default_templates_are_mode_specific(self):
        service = self._service({})
        self.assertEqual(service.get_search_template('rekordbox'), '%tracktitle% %comment%')
        self.assertEqual(service.get_search_template('shazam'), '%tracktitle% %artist%')

    def test_rekordbox_and_shazam_build_different_queries(self):
        service = self._service({
            'youtube_search_template_rekordbox': '%tracktitle% %comment%',
            'youtube_search_template_shazam': '%tracktitle% %artist%',
        })
        self.assertEqual(
            service.create_search_query_from_track(
                'Song A', 'Artist A', 'DJ edit', source_mode='rekordbox'
            ),
            'Song A DJ edit',
        )
        self.assertEqual(
            service.create_search_query_from_track(
                'Song A', 'Artist A', 'DJ edit', source_mode='shazam'
            ),
            'Song A Artist A',
        )

    def test_legacy_template_remains_rekordbox_fallback(self):
        service = self._service({'youtube_search_template': '%artist% %tracktitle%'})
        self.assertEqual(service.get_search_template('rekordbox'), '%artist% %tracktitle%')
        self.assertEqual(service.get_search_template('shazam'), '%tracktitle% %artist%')

    def test_custom_templates_do_not_cross_modes(self):
        service = self._service({
            'youtube_search_template_rekordbox': 'RB %tracktitle%',
            'youtube_search_template_shazam': 'SZ %artist% %tracktitle%',
        })
        self.assertEqual(
            service.create_search_query_from_track('Tune', 'Singer', source_mode='rekordbox'),
            'RB Tune',
        )
        self.assertEqual(
            service.create_search_query_from_track('Tune', 'Singer', source_mode='shazam'),
            'SZ Singer Tune',
        )

    def test_settings_and_queue_keep_separate_mode_keys(self):
        settings = (ROOT / 'ui/dialogs/settings_dialog.py').read_text(encoding='utf-8')
        main = (ROOT / 'main.py').read_text(encoding='utf-8')
        config = (ROOT / 'app/services/config_service.py').read_text(encoding='utf-8')
        self.assertIn('youtube_search_template_rekordbox_edit', settings)
        self.assertIn('youtube_search_template_shazam_edit', settings)
        self.assertIn('"youtube_search_template_shazam": "%tracktitle% %artist%"', config)
        self.assertIn('allow_auto_play, search_source_mode', main)
        self.assertIn('source_mode=search_source_mode', main)


class YouTubeSearchRequestTests(unittest.TestCase):
    def test_search_explicitly_disables_youtube_safe_search_filter(self):
        thread = YouTubeSearchThread(['test-key'], 0, '脱げばいいってモンじゃない loves. 初音ミク デッドボールP')

        with patch.object(thread, '_request_json', return_value={'items': []}) as request_json:
            self.assertEqual(thread._search_youtube(), [])

        _, params, _ = request_json.call_args.args
        self.assertEqual(params['safeSearch'], 'none')
        self.assertEqual(
            params['q'],
            '脱げばいいってモンじゃない loves. 初音ミク デッドボールP',
        )


class SearchFallbackQueryTests(unittest.TestCase):
    _service = SearchTemplateTests._service
    TITLE = '\u8131\u3052\u3070\u3044\u3044\u3063\u3066\u30e2\u30f3\u3058\u3083\u306a\u3044! (loves. \u521d\u97f3\u30df\u30af)'
    ARTIST = '\u30c7\u30c3\u30c9\u30dc\u30fc\u30ebP'
    CORE = '\u8131\u3052\u3070\u3044\u3044\u3063\u3066\u30e2\u30f3\u3058\u3083\u306a\u3044'

    def _queries(self, title=TITLE, artist=ARTIST, comment='', **kwargs):
        return self._service({}).create_search_queries_from_track(
            title, artist, comment, source_mode='shazam', **kwargs
        )

    def test_reported_shazam_track_keeps_original_first_then_title_then_core(self):
        full_title = f'{self.CORE} loves. \u521d\u97f3\u30df\u30af'
        self.assertEqual(self._queries(), [
            f'{full_title} {self.ARTIST}', full_title, self.CORE,
        ])
        self.assertNotIn(self.ARTIST, self._queries())

    def test_reported_pasted_combined_query_has_same_fallbacks(self):
        self.assertEqual(
            self._queries(f'{self.TITLE} {self.ARTIST}', '', allow_inline_artist=True),
            self._queries(),
        )

    def test_title_only_deduplicates_first_fallback(self):
        self.assertEqual(self._queries(self.TITLE, ''), [
            f'{self.CORE} loves. \u521d\u97f3\u30df\u30af', self.CORE,
        ])

    def test_artist_only_manual_query_does_not_drop_any_words(self):
        self.assertEqual(
            self._queries(self.ARTIST, '', allow_inline_artist=True), [self.ARTIST]
        )
        self.assertEqual(
            self._queries('Earth Wind and Fire', '', allow_inline_artist=True),
            ['Earth Wind and Fire'],
        )

    def test_normal_track_uses_at_most_two_distinct_queries(self):
        self.assertEqual(self._queries('Song A', 'Artist A'), ['Song A Artist A', 'Song A'])

    def test_ordinary_parentheses_and_versions_are_not_removed(self):
        for title in ('Song (Live)', 'Song (Remix)', '(I Love You)', 'A (lovesong)'):
            with self.subTest(title=title):
                self.assertEqual(self._queries(title, ''), [
                    YouTubeService.sanitize_search_query(title)
                ])

    def test_guest_markers_and_fullwidth_parentheses(self):
        for title in (
            'Song (feat. Singer)', 'Song (featuring Singer)', 'Song (ft Singer)',
            'Song (LOVES. Singer)', 'Song (feat.Singer)',
            'Song\uff08loves. Singer\uff09',
        ):
            with self.subTest(title=title):
                self.assertEqual(self._queries(title, '')[-1], 'Song')

    def test_structured_track_retains_words_following_guest_credit(self):
        self.assertEqual(
            self._queries('Song (feat. Singer) Part 2', 'Band')[-1], 'Song Part 2'
        )

    def test_free_text_keeps_version_suffix_after_guest_credit(self):
        for suffix in ('(Live)', '[Remix]', 'Live', 'Remix', 'Part 2'):
            with self.subTest(suffix=suffix):
                title = f'Song (feat. Singer) {suffix}'
                self.assertEqual(
                    self._queries(title, '', allow_inline_artist=True)[-1],
                    YouTubeService.sanitize_search_query(f'Song {suffix}'),
                )

    def test_metadata_only_prefix_does_not_fallback_to_artist_only(self):
        self.assertEqual(
            self._queries('(feat. Singer) Band', '', allow_inline_artist=True),
            ['feat. Singer Band'],
        )

    def test_fixed_template_words_and_comments_are_retained(self):
        service = self._service({
            'youtube_search_template_shazam': '%artist% %tracktitle% %comment% MV'
        })
        self.assertEqual(service.create_search_queries_from_track(
            'Song (feat. Singer)', 'Band', 'Live', source_mode='shazam'
        ), ['Band Song feat. Singer Live MV', 'Song feat. Singer Live MV', 'Song Live MV'])

    def test_artist_or_comment_only_template_has_no_title_fallback(self):
        for template, expected in (('%artist%', ['Band']), ('%comment%', ['Live'])):
            with self.subTest(template=template):
                service = self._service({'youtube_search_template_shazam': template})
                self.assertEqual(service.create_search_queries_from_track(
                    self.TITLE, 'Band', 'Live', source_mode='shazam'
                ), expected)

    def test_rekordbox_comment_is_not_silently_removed(self):
        service = self._service({})
        self.assertEqual(service.create_search_queries_from_track(
            'Song (feat. Singer)', 'Band', 'DJ edit', source_mode='rekordbox'
        ), ['Song feat. Singer DJ edit', 'Song DJ edit'])

    def test_primary_query_matches_legacy_method_for_each_source(self):
        service = self._service({})
        for mode in ('rekordbox', 'shazam'):
            with self.subTest(mode=mode):
                self.assertEqual(service.create_search_queries_from_track(
                    self.TITLE, self.ARTIST, 'Live', source_mode=mode
                )[0], service.create_search_query_from_track(
                    self.TITLE, self.ARTIST, 'Live', source_mode=mode
                ))

    def test_query_limit_deduplicates_truncated_candidates(self):
        self.assertEqual(self._queries('x' * 150, 'Band'), ['x' * 100])

    def test_empty_and_metadata_only_input_never_yields_an_empty_query(self):
        self.assertEqual(self._queries('', ''), [])
        self.assertEqual(self._queries('! ()', ''), [])
        self.assertEqual(self._queries('', 'Band'), ['Band'])
        self.assertEqual(self._queries('(feat. Singer)', ''), ['feat. Singer'])


class YouTubeFallbackExecutionTests(unittest.TestCase):
    @staticmethod
    def _thread(queries=None, keys=None):
        queries = queries or ['original artist', 'original', 'core']
        thread = YouTubeSearchThread(
            keys or ['test-key'], 0, queries[0], fallback_queries=queries[1:]
        )
        thread.search_completed = Mock()
        thread.search_error = Mock()
        thread.api_key_switched = Mock()
        return thread

    @staticmethod
    def _item(video_id='video-id'):
        return {
            'id': {'videoId': video_id},
            'snippet': {'title': 'Found video', 'thumbnails': {}},
        }

    def test_first_query_success_does_not_make_extra_requests(self):
        thread = self._thread()
        result = [{'video_id': 'first'}]
        with patch.object(thread, '_search_with_key_rotation', return_value=result) as search:
            thread.run()
        search.assert_called_once_with('original artist')
        thread.search_completed.emit.assert_called_once_with(result)
        thread.search_error.emit.assert_not_called()

    def test_empty_first_result_uses_title_query_and_stops_on_success(self):
        thread = self._thread()
        result = [{'video_id': 'title-only'}]
        with patch.object(thread, '_search_with_key_rotation', side_effect=[[], result]) as search:
            thread.run()
        self.assertEqual(search.call_args_list, [call('original artist'), call('original')])
        thread.search_completed.emit.assert_called_once_with(result)

    def test_all_queries_empty_emits_only_one_completion(self):
        thread = self._thread()
        with patch.object(thread, '_search_with_key_rotation', return_value=[]) as search:
            thread.run()
        self.assertEqual(search.call_count, 3)
        thread.search_completed.emit.assert_called_once_with([])
        thread.search_error.emit.assert_not_called()

    def test_transport_or_api_error_does_not_trigger_broader_queries(self):
        for exc in (RuntimeError('HTTP 403 forbidden'), TimeoutError('network timeout')):
            with self.subTest(error=str(exc)):
                thread = self._thread()
                with patch.object(thread, '_search_with_key_rotation', side_effect=exc) as search:
                    thread.run()
                search.assert_called_once_with('original artist')
                thread.search_completed.emit.assert_not_called()
                thread.search_error.emit.assert_called_once_with(str(exc))

    def test_duplicate_and_empty_queries_are_omitted_and_count_is_bounded(self):
        thread = self._thread([' first ', '', 'first', ' second ', 'third', 'fourth'])
        self.assertEqual(thread.queries, ['first', 'second', 'third'])

    def test_no_network_for_empty_query(self):
        thread = self._thread([''])
        with patch.object(thread, '_request_json') as request:
            thread.run()
        request.assert_not_called()
        thread.search_completed.emit.assert_called_once_with([])

    def test_cancelled_before_start_never_requests_or_emits(self):
        thread = self._thread()
        thread._is_aborted = True
        with patch.object(thread, '_request_json') as request:
            thread.run()
        request.assert_not_called()
        thread.search_completed.emit.assert_not_called()
        thread.search_error.emit.assert_not_called()

    def test_cancel_after_empty_response_does_not_start_next_query(self):
        thread = self._thread()
        def cancelled_search(query):
            thread._is_aborted = True
            return []
        with patch.object(thread, '_search_with_key_rotation', side_effect=cancelled_search) as search:
            thread.run()
        self.assertEqual(search.call_count, 1)
        thread.search_completed.emit.assert_not_called()

    def test_cancel_during_search_skips_detail_request_and_completion(self):
        thread = self._thread()
        def cancelled_response(*args, **kwargs):
            thread._is_aborted = True
            return {'items': [self._item()]}
        with patch.object(thread, '_request_json', side_effect=cancelled_response) as request:
            thread.run()
        self.assertEqual(request.call_count, 1)
        thread.search_completed.emit.assert_not_called()

    def test_real_query_pipeline_relaxes_on_empty_search_response(self):
        thread = self._thread()
        responses = [
            {'items': []},
            {'items': [self._item()]},
            {'items': [{'id': 'video-id', 'contentDetails': {'duration': 'PT4M15S'}}]},
        ]
        with patch.object(thread, '_request_json', side_effect=responses) as request:
            thread.run()
        search_calls = [c for c in request.call_args_list if c.args[0].endswith('/search')]
        self.assertEqual([c.args[1]['q'] for c in search_calls], ['original artist', 'original'])
        self.assertTrue(all(c.args[1]['safeSearch'] == 'none' for c in search_calls))
        self.assertEqual(request.call_count, 3)
        result = thread.search_completed.emit.call_args.args[0]
        self.assertEqual(result[0]['duration'], '4:15')
        thread.search_error.emit.assert_not_called()

    def test_all_results_shorter_than_60_seconds_also_trigger_fallback(self):
        thread = self._thread()
        responses = [
            {'items': [self._item('short')]},
            {'items': [{'id': 'short', 'contentDetails': {'duration': 'PT59S'}}]},
            {'items': [self._item('long')]},
            {'items': [{'id': 'long', 'contentDetails': {'duration': 'PT3M'}}]},
        ]
        with patch.object(thread, '_request_json', side_effect=responses) as request:
            thread.run()
        self.assertEqual(request.call_count, 4)
        result = thread.search_completed.emit.call_args.args[0]
        self.assertEqual([v['video_id'] for v in result], ['long'])

    def test_key_rotation_retries_the_same_fallback_query(self):
        thread = self._thread(keys=['key-one', 'key-two'])
        calls = []
        def search(query=None):
            calls.append((query, thread.api_key))
            if query == 'original artist':
                return []
            if thread.api_key == 'key-one':
                raise youtube_module.YouTubeQuotaExceededError()
            return [{'video_id': 'found'}]
        with patch.object(thread, '_search_youtube', side_effect=search):
            thread.run()
        self.assertEqual(calls, [
            ('original artist', 'key-one'), ('original', 'key-one'), ('original', 'key-two')
        ])
        thread.api_key_switched.emit.assert_called_once_with(2, 2)
        thread.search_completed.emit.assert_called_once_with([{'video_id': 'found'}])

    def test_all_keys_exhausted_reports_error_without_trying_next_query(self):
        thread = self._thread(keys=['key-one', 'key-two'])
        with patch.object(thread, '_search_youtube', side_effect=youtube_module.YouTubeQuotaExceededError()) as search:
            thread.run()
        self.assertEqual(search.call_args_list, [call('original artist'), call('original artist')])
        thread.search_completed.emit.assert_not_called()
        thread.search_error.emit.assert_called_once()

    def test_missing_api_keys_is_an_error_not_a_query_fallback(self):
        thread = self._thread()
        thread.api_keys = []
        with patch.object(thread, '_search_youtube') as search:
            thread.run()
        search.assert_not_called()
        thread.search_error.emit.assert_called_once_with('YouTube API key not configured')


class SearchFallbackWiringTests(unittest.TestCase):
    @staticmethod
    def _main_search_method():
        # Execute the actual UI entry point without importing the Windows GUI.
        import ast
        tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == 'search_youtube')
        module = ast.Module(body=[method], type_ignores=[])
        namespace = {}
        exec(compile(module, 'main.py', 'exec'), namespace)
        return namespace['search_youtube']

    def _run_main(self, title, artist, from_list, values=None):
        service = object.__new__(YouTubeService)
        service.config_service = _Config(values or {})
        service.is_configured = Mock(return_value=True)
        thread = Mock()
        service.search_videos = Mock(return_value=thread)
        fake_youtube = types.ModuleType('app.services.youtube_service')
        fake_youtube.YouTubeService = lambda: service
        fake_logger = types.ModuleType('app.utils.logger')
        fake_logger.info, fake_logger.error = Mock(), Mock()
        window = types.SimpleNamespace(
            source_mode='shazam', youtube_search_thread=None,
            _set_searching_state=Mock(), _set_youtube_search_error=Mock(),
            _refresh_connection_statuses=Mock(),
            on_youtube_search_completed=Mock(), on_youtube_search_error=Mock(),
            _on_search_finished=Mock(),
        )
        with patch.dict(sys.modules, {
            'app.services.youtube_service': fake_youtube, 'app.utils.logger': fake_logger,
        }):
            self._main_search_method()(window, title, artist, '', from_list=from_list,
                                       allow_auto_play=from_list)
        return window, service, thread

    def test_automatic_track_passes_fallbacks_and_preserves_metadata(self):
        title, artist = SearchFallbackQueryTests.TITLE, SearchFallbackQueryTests.ARTIST
        window, service, thread = self._run_main(title, artist, True)
        self.assertEqual(service.search_videos.call_args.kwargs['fallback_queries'], [
            f'{SearchFallbackQueryTests.CORE} loves. \u521d\u97f3\u30df\u30af',
            SearchFallbackQueryTests.CORE,
        ])
        self.assertEqual(window._current_track_info, {'title': title, 'artist': artist, 'comment': ''})
        self.assertTrue(window._active_search_allow_auto_play)
        thread.start.assert_called_once()

    def test_manual_combined_input_uses_same_fallbacks_without_auto_play(self):
        title, artist = SearchFallbackQueryTests.TITLE, SearchFallbackQueryTests.ARTIST
        window, service, thread = self._run_main(f'{title} {artist}', '', False)
        self.assertEqual(service.search_videos.call_args.kwargs['fallback_queries'], [
            f'{SearchFallbackQueryTests.CORE} loves. \u521d\u97f3\u30df\u30af',
            SearchFallbackQueryTests.CORE,
        ])
        self.assertFalse(window._active_search_allow_auto_play)
        self.assertFalse(hasattr(window, '_current_track_info'))
        thread.start.assert_called_once()

    def test_empty_sanitized_input_reports_error_without_starting_thread(self):
        window, service, thread = self._run_main('! ()', '', False)
        service.search_videos.assert_not_called()
        thread.start.assert_not_called()
        window._set_youtube_search_error.assert_called_once()

    def test_service_passes_fallback_queries_to_worker(self):
        service = object.__new__(YouTubeService)
        service.api_key_store = Mock()
        service.api_key_store.load.return_value = (['test-key'], 0)
        worker = Mock()
        callback = Mock()
        with patch.object(youtube_module, 'YouTubeSearchThread', return_value=worker) as cls, \
                patch.object(youtube_module, '_retain_qthread') as retain:
            result = service.search_videos('Song Band', callback, fallback_queries=['Song'])
        cls.assert_called_once_with(['test-key'], 0, 'Song Band',
                                    api_key_store=service.api_key_store, fallback_queries=['Song'])
        retain.assert_called_once_with(worker)
        worker.search_completed.connect.assert_called_once_with(callback)
        self.assertIs(result, worker)


if __name__ == '__main__':
    unittest.main()
