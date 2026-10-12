"""Offline latency regressions: real broker threads, deterministic service events.

The service tests mock Qt and HTTP. These are NOT live Shazam/WebView2 benchmarks.
"""
from pathlib import Path
import queue
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from app.services.shazam_metadata_prefetch import (
    MetadataPrefetcher, IdentityConflict, track_identity, usable_metadata,
)
from tests.test_webview_integration import ShazamService


def raw_song(number='11111111', title='', artist='', kind='song', source=None):
    return {'source': source or ('track-primary-header' if title and artist else 'shazam-route'),
            'title': title, 'artist': artist, 'shazamTrackId': number,
            'url': f'https://www.shazam.com/ja-jp/{kind}/{number}/fixture'}


class IdentityTests(unittest.TestCase):
    def test_route_identity_contains_provider_and_route_kind(self):
        self.assertEqual(track_identity(raw_song()), ('shazam-song', '11111111'))
        self.assertEqual(track_identity(raw_song(kind='track')), ('shazam-track', '11111111'))

    def test_same_digits_across_providers_are_not_equal(self):
        self.assertNotEqual(track_identity(raw_song()), track_identity({'appleTrackId': '11111111'}))

    def test_same_digits_song_and_track_are_not_equal(self):
        self.assertNotEqual(track_identity(raw_song()), track_identity(raw_song(kind='track')))

    def test_route_and_explicit_id_disagreement_is_rejected(self):
        value = raw_song(); value['shazamTrackId'] = '22222222'
        with self.assertRaises(IdentityConflict): track_identity(value)

    def test_apple_url_disagreement_is_rejected(self):
        with self.assertRaises(IdentityConflict):
            track_identity({'appleTrackId': '11111111',
                            'appleMusicUrl': 'https://music.apple.com/jp/album/x/12345678?i=22222222'})

    def test_album_only_url_is_not_a_track_identity(self):
        self.assertIsNone(track_identity({'appleMusicUrl': 'https://music.apple.com/jp/album/x/12345678'}))

    def test_untyped_shazam_id_does_not_equal_known_route(self):
        self.assertNotEqual(track_identity({'shazamTrackId': '11111111'}), track_identity(raw_song()))

    def test_bad_or_missing_id_never_becomes_title_guess(self):
        self.assertIsNone(track_identity({'title': 'Song', 'shazamTrackId': 'abc'}))
        self.assertIsNone(track_identity({'url': 'https://www.shazam.com.evil.test/song/11111111/x'}))

    def test_valid_apple_link_is_typed(self):
        self.assertEqual(track_identity({'appleMusicUrl':
            'https://music.apple.com/jp/album/x/12345678?i=11111111'}), ('apple-song', '11111111'))

    def test_leading_namespace_not_inferred_from_localized_title(self):
        self.assertEqual(track_identity(raw_song(title='Localized')), track_identity(raw_song(title='Different')))


class ControlledPrefetch:
    """No threads: deliver completions explicitly in the service event order."""
    def __init__(self):
        self.cache = {}; self.pending = {}; self.requests = []; self.closed = False
    def get(self, key): return self.cache.get(key)
    def seed(self, key, title, artist):
        self.cache[key] = (title, artist); return self.cache[key]
    def request(self, generation, key, raw, language, country, cancelled, callback):
        if key in self.cache: return 'cache'
        if (generation, key) in self.pending: return 'joined'
        self.pending[(generation, key)] = callback
        self.requests.append((generation, key, dict(raw)))
        return 'started'
    def finish(self, generation, key, metadata=('Fixture Song', 'Fixture Artist')):
        callback = self.pending.pop((generation, key))
        if metadata: self.cache[key] = metadata
        callback(generation, key, metadata, '' if metadata else 'unavailable')
    def discard_cancelled(self): pass
    def close(self): self.closed = True


class ServicePrefetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {'shazam_language': 'ja-JP', 'shazam_endpoint_country': 'JP'}
        config = types.SimpleNamespace(get=lambda k, default=None: self.config.get(k, default))
        with patch('app.services.config_service.ConfigService', return_value=config), \
             patch.object(ShazamService, '_get_history_path', return_value=Path(self.tmp.name) / 'history.json'):
            self.svc = ShazamService()
        self.svc._active = True; self.svc._generation = 1
        self.broker = ControlledPrefetch(); self.svc._metadata_prefetch = self.broker
        self.a = raw_song(); self.b = raw_song('22222222')
        self.key_a = self.svc._metadata_key(track_identity(self.a))
        self.key_b = self.svc._metadata_key(track_identity(self.b))
    def tearDown(self):
        self.svc.shutdown(); self.tmp.cleanup()
    def observe(self, seq, raw=None):
        raw = self.a if raw is None else raw
        self.svc._resolve_or_publish_confirmed_track(seq % 4, seq, raw, raw.get('title', ''), raw.get('artist', ''))
    def finish_a(self): self.broker.finish(1, self.key_a)
    def titles(self): return [x[1] for x in self.svc.get_history()]

    def test_first_id_starts_fetch_without_waiting_for_title(self):
        self.observe(0)
        self.assertEqual(len(self.broker.requests), 1)
        self.assertEqual(self.svc._next_temporal_sequence, 1)
        self.assertEqual(self.svc._last_temporal_observation['identity'], ('shazam-song', '11111111'))
        self.assertEqual(self.titles(), [])

    def test_metadata_first_does_not_publish_until_second_recognition(self):
        self.observe(0); self.finish_a()
        self.assertEqual(self.titles(), [])
        self.observe(1)
        self.assertEqual(self.titles(), ['Fixture Song'])
        self.assertEqual(len(self.broker.requests), 1)

    def test_confirmation_first_waits_only_for_shared_fetch(self):
        self.observe(0); self.observe(1)
        self.assertEqual(self.svc._pending_identity_confirmation['sequence'], 1)
        self.assertEqual(len(self.broker.requests), 1)
        self.assertEqual(self.titles(), [])
        self.finish_a()
        self.assertEqual(self.titles(), ['Fixture Song'])

    def test_same_id_confirms_despite_different_display_strings(self):
        self.observe(0, raw_song(title='English Name', artist='Artist'))
        self.observe(1, raw_song(title='Localized Name', artist='Artist'))
        self.assertEqual(self.titles(), ['Localized Name'])
        self.assertEqual(self.broker.requests, [])

    def test_different_ids_with_identical_names_do_not_confirm(self):
        self.observe(0, raw_song(title='Same title', artist='Artist'))
        self.observe(1, raw_song('22222222', title='Same title', artist='Artist'))
        self.assertEqual(self.titles(), [])

    def test_different_id_breaks_delayed_candidate_before_old_db_returns(self):
        self.observe(0); self.observe(1); self.observe(2, self.b); self.finish_a()
        self.assertEqual(self.titles(), [])
        self.observe(3, self.b); self.broker.finish(1, self.key_b, ('Song B', 'Artist B'))
        self.assertEqual(self.titles(), ['Song B'])

    def test_no_match_breaks_delayed_candidate(self):
        self.observe(0); self.observe(1)
        self.svc._stage_temporal_result(2, 2)
        self.finish_a(); self.assertEqual(self.titles(), [])
        self.observe(3); self.assertEqual(self.titles(), [])
        self.observe(4); self.assertEqual(self.titles(), ['Fixture Song'])

    def test_out_of_order_new_song_blocks_old_metadata(self):
        self.observe(0); self.observe(1)
        self.observe(3, self.b)  # seq=2 still running.
        self.finish_a(); self.assertEqual(self.titles(), [])

    def test_two_later_same_ids_bypass_slow_head_before_db_finishes(self):
        self.observe(1); self.observe(2)
        self.assertEqual(self.svc._next_temporal_sequence, 3)
        self.finish_a(); self.assertEqual(self.titles(), ['Fixture Song'])
        self.observe(0, self.b)
        self.assertEqual(self.titles(), ['Fixture Song'])
        self.assertEqual(len(self.broker.requests), 1)

    def test_completed_single_id_never_bypasses_missing_predecessor(self):
        self.observe(1); self.finish_a()
        self.assertEqual(self.titles(), [])
        self.assertEqual(self.svc._next_temporal_sequence, 0)

    def test_out_of_order_pair_is_confirmed_once(self):
        self.observe(1); self.finish_a(); self.observe(0)
        self.assertEqual(self.titles(), ['Fixture Song'])
        self.assertEqual(len(self.svc.new_track_detected.calls), 1)

    def test_a_unknown_a_requires_another_a(self):
        self.observe(0); self.finish_a()
        self.svc._stage_temporal_result(1, 1)
        self.observe(2); self.assertEqual(self.titles(), [])
        self.observe(3); self.assertEqual(self.titles(), ['Fixture Song'])

    def test_cached_metadata_not_counted_as_second_observation(self):
        self.broker.seed(self.key_a, 'Song A', 'Artist A')
        self.observe(0)
        for _ in range(3):
            self.svc._handle_prefetch_finished(1, self.key_a, ('Song A', 'Artist A'), '')
        self.assertEqual(self.titles(), [])
        self.observe(1); self.assertEqual(self.titles(), ['Song A'])

    def test_duplicate_recognition_sequence_not_counted_twice(self):
        self.observe(0); self.finish_a(); self.observe(0)
        self.assertEqual(self.titles(), [])

    def test_complete_second_result_satisfies_shared_info_immediately(self):
        self.observe(0)
        self.observe(1, raw_song(title='New Complete Name', artist='Full Artist A & B'))
        self.assertEqual(self.titles(), ['New Complete Name'])
        # An older HTTP callback cannot overwrite history.
        self.finish_a(); self.assertEqual(self.titles(), ['New Complete Name'])

    def test_late_completion_from_stopped_generation_does_not_publish(self):
        self.observe(0); self.observe(1); self.svc.stop(); self.finish_a()
        self.assertEqual(self.titles(), [])

    def test_old_generation_callback_does_not_release_new_candidate(self):
        self.observe(0); self.observe(1)
        self.svc._generation = 2
        self.svc._handle_prefetch_finished(1, self.key_a, ('Old Song', 'Old Artist'), '')
        self.assertEqual(self.titles(), [])

    def test_language_and_country_are_part_of_cache_key(self):
        self.config['shazam_language'] = 'en-US'
        self.assertNotEqual(self.key_a, self.svc._metadata_key(track_identity(self.a)))
        self.config['shazam_language'] = 'ja-JP'; self.config['shazam_endpoint_country'] = 'US'
        self.assertNotEqual(self.key_a, self.svc._metadata_key(track_identity(self.a)))

    def test_incomplete_lookup_does_not_add_empty_placeholder(self):
        self.observe(0); self.observe(1); self.broker.finish(1, self.key_a, None)
        self.assertEqual(self.titles(), [])

    def test_conflicting_fields_never_start_lookup(self):
        value = dict(self.a, shazamTrackId='22222222')
        self.observe(0, value); self.observe(1, value)
        self.assertEqual(self.broker.requests, []); self.assertEqual(self.titles(), [])

    def test_known_identity_does_not_match_idless_title(self):
        self.observe(0, raw_song(title='Same title', artist='Artist'))
        self.observe(1, {'source': 'recognition-response', 'title': 'Same title', 'artist': 'Artist'})
        self.assertEqual(self.titles(), [])

    def test_neither_id_keeps_original_title_rule(self):
        value = {'source': 'recognition-response', 'title': 'Same title', 'artist': 'Artist'}
        self.observe(0, value); self.observe(1, value)
        self.assertEqual(self.titles(), ['Same title'])


class BrokerThreadTests(unittest.TestCase):
    def setUp(self):
        self.cancel = threading.Event(); self.callbacks = queue.Queue(); self.brokers = []
        self.key = ('ja-jp', 'JP', 'shazam-song', '11111111')
    def tearDown(self):
        self.cancel.set()
        for broker in self.brokers: broker.close()
        for broker in self.brokers:
            for worker in broker._workers: worker.join(2)
    def broker(self, resolve, clock=time.monotonic):
        factory = lambda: types.SimpleNamespace(resolve=resolve)
        b = MetadataPrefetcher(factory, clock); self.brokers.append(b); return b
    def request(self, b, key=None, raw=None, generation=1, cancelled=None):
        return b.request(generation, key or self.key, raw or raw_song(), 'ja-JP', 'JP',
                         cancelled or self.cancel,
                         lambda *args: self.callbacks.put(args))
    def completion(self): return self.callbacks.get(timeout=2)

    def test_same_id_inflight_is_one_real_lookup(self):
        entered, release = threading.Event(), threading.Event(); calls = []
        def resolve(*args):
            calls.append(args); entered.set(); release.wait(2); return ('Song', 'Artist')
        b = self.broker(resolve)
        try:
            self.assertEqual(self.request(b), 'started'); self.assertTrue(entered.wait(1))
            self.assertEqual(self.request(b), 'joined'); self.assertEqual(len(calls), 1)
        finally: release.set()
        self.assertEqual(self.completion()[2], ('Song', 'Artist'))
        self.assertEqual(self.request(b), 'cache'); self.assertEqual(len(calls), 1)

    def test_different_ids_lookup_in_parallel_without_global_resolver_lock(self):
        barrier = threading.Barrier(2); entered = []
        def resolve(raw, *args):
            entered.append(raw['shazamTrackId']); barrier.wait(2); return (raw['shazamTrackId'], 'Artist')
        b = self.broker(resolve); key_b = (*self.key[:-1], '22222222')
        self.request(b); self.request(b, key_b, raw_song('22222222'))
        results = [self.completion(), self.completion()]
        self.assertEqual(len(entered), 2)
        self.assertTrue(all(r[2] and not r[3] for r in results))

    def test_live_metadata_seed_beats_older_running_lookup(self):
        entered, release = threading.Event(), threading.Event()
        def resolve(*args): entered.set(); release.wait(2); return ('Old Name', 'Old Artist')
        b = self.broker(resolve)
        try:
            self.request(b); self.assertTrue(entered.wait(1))
            b.seed(self.key, 'Live Name', 'Full Artist A & B')
        finally: release.set()
        self.assertEqual(self.completion()[2], ('Live Name', 'Full Artist A & B'))
        self.assertEqual(b.get(self.key), ('Live Name', 'Full Artist A & B'))

    def test_cancelled_generation_cannot_write_cache_or_callback(self):
        entered, release = threading.Event(), threading.Event()
        def resolve(*args): entered.set(); release.wait(2); return ('Stale Song', 'Artist')
        b = self.broker(resolve)
        try:
            self.request(b); self.assertTrue(entered.wait(1)); self.cancel.set()
        finally: release.set()
        # Wait until the inflight work leaves its finally block.
        for _ in range(100):
            with b._lock: done = not b._inflight
            if done: break
            time.sleep(0.005)
        self.assertIsNone(b.get(self.key)); self.assertTrue(self.callbacks.empty())

    def test_negative_result_is_briefly_cached_then_retried(self):
        now = [100.0]; calls = []
        def resolve(*args): calls.append(1); return ('', '')
        b = self.broker(resolve, lambda: now[0])
        self.request(b); self.assertIsNone(self.completion()[2])
        self.assertEqual(self.request(b), 'retry-later')
        now[0] += 6
        self.assertEqual(self.request(b), 'started'); self.completion()
        self.assertEqual(len(calls), 2)

    def test_success_cache_expires(self):
        now = [100.0]; b = self.broker(lambda *a: ('Song', 'Artist'), lambda: now[0])
        self.request(b); self.completion(); self.assertIsNotNone(b.get(self.key))
        now[0] += 301; self.assertIsNone(b.get(self.key))

    def test_cache_is_bounded(self):
        b = self.broker(lambda *a: ('Song', 'Artist'))
        for i in range(200): b.seed(('lang', 'country', 'ns', str(i)), 'Song', 'Artist')
        self.assertEqual(len(b._cache), 128)

    def test_exception_preserves_only_safe_existing_text(self):
        def resolve(*args): raise OSError('offline')
        b = self.broker(resolve)
        self.request(b, raw=raw_song(title='Safe Song'))
        self.assertEqual(self.completion()[2], ('Safe Song', ''))

    def test_lookup_can_keep_existing_title_only_fallback(self):
        b = self.broker(lambda *a: ('Safe Song', ''))
        self.request(b); self.assertEqual(self.completion()[2], ('Safe Song', ''))

    def test_workers_keep_private_resolvers(self):
        barrier = threading.Barrier(2); created = []
        class Resolver:
            def __init__(self): created.append(self)
            def resolve(self, *args): barrier.wait(2); return ('Song', 'Artist')
        b = MetadataPrefetcher(Resolver); self.brokers.append(b)
        self.request(b); self.request(b, (*self.key[:-1], '22222222'), raw_song('22222222'))
        self.completion(); self.completion(); self.assertEqual(len(created), 2)

    def test_close_rejects_new_requests(self):
        b = self.broker(lambda *a: ('Song', 'Artist')); b.close()
        self.assertEqual(self.request(b), 'closed')

    def test_two_workers_and_bounded_queue_reject_overflow(self):
        entered = threading.Barrier(3); release = threading.Event()
        def resolve(*args): entered.wait(2); release.wait(2); return ('Song', 'Artist')
        b = self.broker(resolve)
        try:
            self.request(b); self.request(b, (*self.key[:-1], '22222222'), raw_song('22222222'))
            entered.wait(2)
            for i in range(b.QUEUE_LIMIT):
                self.assertEqual(self.request(b, (*self.key[:-1], str(30000000+i))), 'started')
            self.assertEqual(self.request(b, (*self.key[:-1], '44444444')), 'queue-full')
        finally:
            self.cancel.set(); release.set()


class RealServiceWiringTests(unittest.TestCase):
    """Real worker threads; completion delivery marshalled through a test UI queue."""
    observe = ServicePrefetchTests.observe
    titles = ServicePrefetchTests.titles

    def setUp(self):
        ServicePrefetchTests.setUp(self)
        self.release = threading.Event()
        self.entered = threading.Event()
        self.ui_events = queue.Queue()
        self.calls = []
        def resolve(raw, language, country, cancelled):
            self.calls.append(raw['shazamTrackId'])
            self.entered.set(); self.release.wait(2)
            return ('Worker Song', 'Worker Artist')
        self.real = MetadataPrefetcher(lambda: types.SimpleNamespace(resolve=resolve))
        self.svc._metadata_prefetch = self.real
        self.svc._prefetch_finished.emit = lambda *args: self.ui_events.put(args)

    def tearDown(self):
        self.release.set(); self.svc.shutdown()
        for worker in self.real._workers: worker.join(2)
        self.tmp.cleanup()

    def deliver(self):
        args = self.ui_events.get(timeout=2)
        self.svc._handle_prefetch_finished(*args)

    def test_real_thread_prefetch_finishes_before_second_observation(self):
        self.observe(0); self.assertTrue(self.entered.wait(1))
        self.release.set(); self.deliver()
        self.assertEqual(self.titles(), [])
        self.observe(1)
        self.assertEqual(self.titles(), ['Worker Song'])
        self.assertEqual(len(self.calls), 1)

    def test_real_thread_does_not_block_two_id_confirmation(self):
        self.observe(0); self.assertTrue(self.entered.wait(1)); self.observe(1)
        self.assertEqual(self.svc._next_temporal_sequence, 2)
        self.assertEqual(self.svc._pending_identity_confirmation['sequence'], 1)
        self.assertEqual(self.titles(), [])
        self.release.set(); self.deliver()
        self.assertEqual(self.titles(), ['Worker Song'])
        self.assertEqual(len(self.calls), 1)

    def test_real_worker_cannot_change_history_before_ui_event_delivery(self):
        self.observe(0); self.assertTrue(self.entered.wait(1)); self.observe(1)
        self.release.set()
        args = self.ui_events.get(timeout=2)
        self.assertEqual(self.titles(), [])
        self.svc._handle_prefetch_finished(*args)
        self.assertEqual(self.titles(), ['Worker Song'])

    def test_real_completion_after_candidate_change_does_not_publish(self):
        self.observe(0); self.assertTrue(self.entered.wait(1)); self.observe(1)
        self.svc._stage_temporal_result(2, 2)
        self.release.set(); self.deliver()
        self.assertEqual(self.titles(), [])

    def test_queued_completion_after_stop_cannot_publish(self):
        self.observe(0); self.assertTrue(self.entered.wait(1)); self.observe(1)
        self.release.set()
        args = self.ui_events.get(timeout=2)
        self.svc.stop(); self.svc._handle_prefetch_finished(*args)
        self.assertEqual(self.titles(), [])


class NativePreparationSourceTests(unittest.TestCase):
    """Static contracts, not an execution of .NET/WebView2."""
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parents[1] /
                      'native/ShazamWebViewBridge/BridgeHost.cs').read_text(encoding='utf-8')
    def between(self, a, b): return self.source.split(a, 1)[1].split(b, 1)[0]

    def test_prepare_is_scheduled_after_result_not_before(self):
        body = self.between('private async Task HandleCommandAsync', 'private void StartIdleHomePreparation')
        self.assertLess(body.index('_cycle = null;'), body.index('type = "result"'))
        self.assertLess(body.index('_busy = false;'), body.index('type = "result"'))
        self.assertLess(body.index('type = "result"'), body.index('StartIdleHomePreparation();'))

    def test_new_recognition_joins_warmup_before_page_preparation_and_arm(self):
        body = self.between('private async Task<Candidate?> RecognizeAsync', 'private async Task<HashSet<string>>')
        self.assertLess(body.index('JoinIdleHomePreparationAsync(token)'), body.index('PrepareHomeAsync(token)'))
        self.assertLess(body.index('PrepareHomeAsync(token)'), body.index('_cycle = id;'))
        self.assertIn('Recognition page ready', body)

    def test_idle_preparation_does_not_start_audio_or_recognition(self):
        body = self.between('private async Task PrepareIdleHomeAsync', 'private async Task JoinIdleHomePreparationAsync')
        self.assertNotIn('startLive', body); self.assertNotIn('ClickAsync', body)
        self.assertNotIn('Task.Run', body); self.assertNotIn('_cycle =', body)
        self.assertIn('CreateLinkedTokenSource(_lifetime.Token)', body)

    def test_cancellation_keeps_running_preparation_available_to_next_request(self):
        body = self.between('private async Task JoinIdleHomePreparationAsync', 'private async Task<Candidate?> RecognizeAsync')
        self.assertIn('await preparation.WaitAsync(token)', body)
        self.assertIn('preparation.IsCompleted && ReferenceEquals', body)

    def test_home_check_requires_bridges_not_just_button(self):
        body = self.between('private async Task<Button?> FindButtonOnceAsync', 'private async Task<Button> PrepareHomeAsync')
        for field in ['__vjAudioBridge?.startLive', '__vjResults?.arm', '__vjTrackMetadata?.readPrimaryTrack']:
            self.assertIn(field, body)
        self.assertGreaterEqual(body.count('IsHomeUrl('), 2)

    def test_navigation_id_guard_and_timeout_cleanup_remain(self):
        body = self.between('private async Task NavigateHomeAsync', 'private readonly record struct Button')
        self.assertIn('navigationId == args.NavigationId', body)
        self.assertIn('core.Stop()', body)
        self.assertIn('core.NavigationCompleted -= OnCompleted', body)

    def test_never_introduces_ui_blocking_wait_or_result(self):
        body = self.between('private void StartIdleHomePreparation', 'private async Task<Candidate?> RecognizeAsync')
        self.assertNotIn('.Result', body); self.assertNotIn('.Wait()', body)


if __name__ == '__main__':
    unittest.main()
