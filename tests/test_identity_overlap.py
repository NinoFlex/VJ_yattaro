"""Non-voting identity prefetch/ACK, cancellation and lookup regression tests.
Qt, HTTP and WebView2 are fakes; real broker threads and IPC loop are exercised.
"""
import io
import json
from pathlib import Path
import queue
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch
from app.services.shazam_metadata_prefetch import EarlyIdentityProbe, MetadataPrefetcher, track_identity
from app.services.shazam_webview_client import WebViewRecognizer, RecognitionCancelled
from app.services.itunes_metadata import ITunesMetadataResolver
from tests.test_latency_prefetch import ControlledPrefetch, raw_song
from tests import test_shazam_speculative_preload as vote_tests

RAW = raw_song('765432198', kind='track')
KEY = ('ja-jp','JP','shazam-track','765432198')

class EarlyProbeTests(unittest.TestCase):
    def setUp(self):
        self.b = ControlledPrefetch(); self.cancel = threading.Event(); self.cb = Mock()
        self.p = EarlyIdentityProbe(self.b,1,'ja-JP','JP',self.cancel,self.cb)
    def test_first_hint_starts_one_lookup_no_vote_callback(self):
        self.assertFalse(self.p(RAW)); self.assertEqual(len(self.b.requests),1); self.cb.assert_not_called()
    def test_repeated_hint_only_polls_shared_cache(self):
        for _ in range(20): self.assertFalse(self.p(RAW))
        self.assertEqual(len(self.b.requests),1)
    def test_complete_cache_needs_no_lookup(self):
        self.b.seed(KEY,'Verified Song','Verified Artist')
        self.assertTrue(self.p(RAW)); self.assertFalse(self.b.requests)
    def test_title_only_cache_does_not_cut_short_artist_grace(self):
        self.b.cache[KEY]=('Verified Song','')
        self.assertFalse(self.p(RAW)); self.assertFalse(self.b.requests)
    def test_lookup_completion_allows_early_release(self):
        self.assertFalse(self.p(RAW)); self.b.finish(1,KEY,('Title','Artist'))
        self.assertTrue(self.p(RAW)); self.assertEqual(len(self.b.requests),1)
    def test_multiple_lanes_share_one_lookup(self):
        other=EarlyIdentityProbe(self.b,1,'ja-JP','JP',self.cancel,self.cb)
        self.p(RAW); other(RAW); self.assertEqual(len(self.b.requests),1)
        self.b.finish(1,KEY,('Title','Artist'))
        self.assertTrue(self.p(RAW)); self.assertTrue(other(RAW))
    def test_stop_cannot_release_even_cached_identity(self):
        self.b.seed(KEY,'Title','Artist'); self.cancel.set()
        self.assertFalse(self.p(RAW)); self.assertFalse(self.b.requests)
    def test_conflicting_identity_no_lookup(self):
        self.assertFalse(self.p(dict(RAW, shazamTrackId='999999999'))); self.assertFalse(self.b.requests)
    def test_non_track_hint_cannot_change_song_fast_path(self):
        for raw in [dict(RAW,url=RAW['url'].replace('/track/','/song/')),
                    dict(RAW,url='https://www.shazam.com/'),
                    dict(RAW,url=RAW['url'].replace('www.shazam.com','evil.invalid')),
                    dict(RAW,url=RAW['url'].replace('https:','http:')),
                    dict(RAW,source='homepage'),dict(RAW,url='https://www.shazam.com:bad/track/765432198/x')]:
            self.assertFalse(self.p(raw))
        self.assertFalse(self.b.requests)
    def test_hint_text_is_not_trusted_or_seeded(self):
        self.p(dict(RAW,title='WRONG',artist='WRONG'))
        self.assertNotIn('artist',self.b.requests[0][2]); self.assertNotIn('title',self.b.requests[0][2])
    def test_cache_localization_scope_remains_separate(self):
        self.b.seed(KEY,'Title','Artist')
        other=EarlyIdentityProbe(self.b,1,'en-US','US',self.cancel,self.cb)
        self.assertFalse(other(RAW)); self.assertEqual(len(self.b.requests),1)
    def test_hint_budget_bounded_per_cycle(self):
        for i in range(8): self.p(raw_song(str(900000000+i),kind='track'))
        self.assertEqual(len(self.b.requests),2)

class IdentityIpcTests(unittest.TestCase):
    def setUp(self):
        self.r=WebViewRecognizer('test'); self.cancel=threading.Event(); self.q=queue.Queue()
        self.process=types.SimpleNamespace(poll=lambda:None, stdin=io.StringIO())
        self.r._process=self.process; self.r._ready=True
        self.r._ensure_started=Mock(return_value=(self.process,self.q))
        self.r._live_audio_feeder=Mock()
        self.id='a'*32
        self.hint=dict(RAW,type='identity',id=self.id)
        self.result=dict(RAW,type='result',id=self.id,title='Returned Title',error='')
    def run_client(self,probe,messages):
        self.r.identity_probe=probe
        for m in messages:self.q.put(m)
        with patch('app.services.shazam_webview_client.uuid.uuid4',return_value=types.SimpleNamespace(hex=self.id)):
            result=self.r.recognize_live(16000,'ja-JP',self.cancel)
        sent=[json.loads(x) for x in self.process.stdin.getvalue().splitlines()]
        return result,sent
    def test_hint_is_not_final_result_or_second_vote(self):
        probe=Mock(return_value=True)
        result,sent=self.run_client(probe,[self.hint,self.result])
        self.assertEqual(result,self.result)
        self.assertEqual([x['type'] for x in sent],['recognize-live','metadata-ready'])
    def test_repeated_hint_only_one_ack(self):
        result,sent=self.run_client(Mock(return_value=True),[self.hint,self.hint,self.result])
        self.assertEqual(sum(x['type']=='metadata-ready' for x in sent),1)
    def test_other_request_hint_ignored(self):
        probe=Mock(return_value=True)
        _,sent=self.run_client(probe,[dict(self.hint,id='b'*32),self.result])
        probe.assert_not_called(); self.assertEqual(len(sent),1)
    def test_older_helper_without_hint_still_works(self):
        probe=Mock(return_value=True)
        result,sent=self.run_client(probe,[self.result])
        probe.assert_not_called(); self.assertEqual(result,self.result)
    def test_probe_error_does_not_kill_normal_recognition(self):
        _,sent=self.run_client(Mock(side_effect=ValueError('bad')),[self.hint,self.result])
        self.assertEqual(len(sent),1)
    def test_title_only_or_pending_metadata_sends_no_ack(self):
        _,sent=self.run_client(Mock(return_value=False),[self.hint,self.result]);self.assertEqual(len(sent),1)
    def test_completion_is_detected_without_more_helper_messages(self):
        calls=[]
        def probe(raw):
            calls.append(raw)
            if len(calls)>=3:
                self.q.put(self.result)
                return True
            return False
        result,sent=self.run_client(probe,[self.hint])
        self.assertEqual(result,self.result);self.assertEqual(sent[-1]['type'],'metadata-ready')
    def test_cancel_while_waiting_never_acks(self):
        def probe(raw):self.cancel.set();return False
        self.r.identity_probe=probe;self.q.put(self.hint)
        with patch('app.services.shazam_webview_client.uuid.uuid4',return_value=types.SimpleNamespace(hex=self.id)):
            with self.assertRaises(RecognitionCancelled):self.r.recognize_live(16000,'ja-JP',self.cancel)
        self.assertNotIn('metadata-ready',self.process.stdin.getvalue())

    def test_malformed_identity_hint_cannot_break_final_result(self):
        probe=Mock(return_value=True)
        bad=[dict(self.hint,shazamTrackId=[]),dict(self.hint,url=[]),
             dict(self.hint,shazamTrackId='not-an-id'),dict(self.hint,url='x'*5000)]
        result,sent=self.run_client(probe,bad+[self.result])
        self.assertEqual(result,self.result);self.assertEqual(len(sent),1)
        probe.assert_not_called()
    def test_cancel_during_successful_probe_cannot_send_ack(self):
        def probe(raw):self.cancel.set();return True
        self.r.identity_probe=probe;self.q.put(self.hint)
        with patch('app.services.shazam_webview_client.uuid.uuid4',return_value=types.SimpleNamespace(hex=self.id)):
            with self.assertRaises(RecognitionCancelled):self.r.recognize_live(16000,'ja-JP',self.cancel)
        self.assertNotIn('metadata-ready',self.process.stdin.getvalue())

class CompletedSearchReceiptTests(vote_tests.ServiceVoteTests):
    # Inherit just the fixture, not its existing tests (loader below strips them).
    def _use_distinct_third_track(self):
        # Song B and Song C share 'Song', so the new four-character policy
        # intentionally reuses their presentation. Use an actually distinct
        # prefix for tests exercising replacement/search-reuse barriers.
        self.c = raw_song('33333333', title='Other C', artist='Artist C')
    def test_field_log_b0_c2_b1_then_false_c_and_b_does_not_research(self):
        self._use_distinct_third_track()
        self.vote(0,self.b);self.vote(2,self.c);self.vote(1,self.b)
        self.assertFalse(self.svc.get_history(), 'keep future-result publication barrier')
        self.assertEqual(len(self.svc.candidate_confirmed.calls),1)
        self.no_match(3);self.no_match(4);self.no_match(5)
        self.vote(6,self.b);self.vote(7,self.b)
        starts=[args[1][1] for args in self.svc.candidate_started.calls]
        self.assertEqual(starts,['Song B','Other C'])
        self.assertEqual(len(self.svc.candidate_confirmed.calls),1,'no second PLAY permission')
        self.assertEqual(self.svc.get_history()[0][1],'Song B')
        self.assertTrue(self.svc._last_publication_search_reused)
        self.assertEqual(self.svc._last_publication_candidate_id,0)
    def test_distinct_confirmed_song_allows_legitimate_return_search(self):
        self._use_distinct_third_track()
        self.vote(0,self.b);self.vote(2,self.c);self.vote(1,self.b);self.vote(3,self.c)
        self.vote(4,self.b);self.vote(5,self.b)
        self.assertEqual([x[1][1] for x in self.svc.candidate_started.calls],['Song B','Other C','Song B'])
        self.assertFalse(self.svc._last_publication_search_reused)
    def test_same_title_other_id_reuses_display_not_identity_confirmation(self):
        # Title keyword/prefix feature: presentation is reused, but a different
        # ID's first vote cannot count as another exact-ID confirmation.
        self.vote(0,self.b);self.vote(1,self.b)
        self.vote(2,dict(self.c,title='Song B',artist='Artist B'))
        self.assertEqual(len(self.svc.candidate_started.calls),1)
        self.assertEqual(len(self.svc.candidate_confirmed.calls),1)
        self.assertEqual(len(self.svc.new_track_detected.calls),1)
    def test_early_hint_is_not_a_vote_and_does_not_search(self):
        probe=EarlyIdentityProbe(self.broker,1,'ja-JP','JP',self.svc._cancel_current,self.svc._prefetch_finished.emit)
        self.assertFalse(probe(RAW));self.broker.finish(1,KEY,('Title','Artist'))
        self.assertFalse(self.svc.candidate_started.calls);self.assertFalse(self.svc.candidate_confirmed.calls)
        self.assertTrue(probe(RAW));self.assertEqual(self.svc._next_temporal_sequence,0)
        self.vote(0,RAW);self.assertEqual(len(self.svc.candidate_started.calls),1)
        self.assertFalse(self.svc.candidate_confirmed.calls)
        self.vote(1,RAW);self.assertEqual(len(self.svc.candidate_confirmed.calls),1)
        self.assertEqual(len(self.broker.requests),1)
    def test_repeated_completion_does_not_trigger_additional_search(self):
        self.vote(0,self.b);self.vote(1,self.b)
        key=self.svc._metadata_key(track_identity(self.b))
        for _ in range(8):self.svc._handle_prefetch_finished(1,key,('Song B','Artist B'),'')
        self.assertEqual(len(self.svc.candidate_started.calls),1)
        self.assertEqual(len(self.svc.new_track_detected.calls),1)

    def test_field_log_service_ui_preload_play_end_to_end_no_duplicate(self):
        self._use_distinct_third_track()
        w=vote_tests.WindowVoteTests().window()
        w.shazam_service=self.svc
        def search(*args,**kwargs):
            w._active_search_candidate_id=kwargs.get('shazam_candidate_id')
        w.search_youtube.side_effect=search
        self.svc.candidate_started.connect(w.on_shazam_candidate_started)
        self.svc.candidate_confirmed.connect(w.on_shazam_candidate_confirmed)
        self.svc.candidate_rejected.connect(w.on_shazam_candidate_rejected)
        self.svc.history_updated.connect(w.on_shazam_history_updated)
        self.svc.new_track_detected.connect(w.on_shazam_new_track_detected)
        self.vote(0,self.b)
        videos=vote_tests._videos('3:00','3:00')
        vote_tests._complete(w,videos)
        w._handle_player_feedback({'state':'ready','videoId':'video-0'})
        self.vote(2,self.c);self.vote(1,self.b)
        self.assertFalse(self.svc.get_history())
        w._handle_player_feedback({'state':'playing','videoId':'video-0'})
        for v in videos:v['video_id']='c-'+v['video_id']
        vote_tests._complete(w,videos)
        self.no_match(3);self.no_match(4);self.no_match(5)
        commands_before=w._send_video_command.call_count
        self.vote(6,self.b);self.vote(7,self.b)
        self.assertEqual(w.search_youtube.call_count,2, 'only first B and tentative C')
        self.assertEqual(w._send_video_command.call_count,commands_before)
        play=[c for c in w._send_video_command.call_args_list if c.args[0]=='PLAY']
        self.assertEqual(len(play),1)
        self.assertEqual(self.svc.get_history()[0][1],'Song B')
    def test_receipt_is_invalid_after_run_generation_changes(self):
        self._use_distinct_third_track()
        self.vote(0,self.b);self.vote(2,self.c);self.vote(1,self.b)
        self.no_match(3);self.no_match(4);self.no_match(5)
        self.svc._generation+=1
        self.vote(6,self.b);self.vote(7,self.b)
        self.assertEqual([x[1][1] for x in self.svc.candidate_started.calls],['Song B','Other C','Song B'])

# Avoid counting inherited baseline tests twice.
for _name in dir(vote_tests.ServiceVoteTests):
    if _name.startswith('test_'):
        setattr(CompletedSearchReceiptTests,_name,None)

class ReceiptUiTests(unittest.TestCase):
    def test_receipt_updates_history_without_search_preload_or_play(self):
        w=vote_tests.WindowVoteTests().window()
        w.shazam_service._last_publication_search_reused=True
        w.shazam_service._last_publication_candidate_id=42
        w.on_shazam_new_track_detected(('', 'Existing Song', 'Artist'))
        w.search_youtube.assert_not_called();w._send_video_command.assert_not_called()
    def test_absent_receipt_does_not_suppress_new_song(self):
        w=vote_tests.WindowVoteTests().window();w.shazam_service._last_publication_search_reused=False
        w.on_shazam_new_track_detected(('', 'Brand New Song', 'Artist'))
        self.assertTrue(w.search_youtube.called)

class LookupOrderingTests(unittest.TestCase):
    def setUp(self):
        self.r=ITunesMetadataResolver();self.url=RAW['url']
        self.local='https://www.shazam.com/ja-jp/track/765432198/unavailable'
        self.canonical='https://www.shazam.com/track/765432198/unavailable'
    def page(self,url=None,artist='Artist'):
        return ('<link rel="canonical" href="'+(url or self.canonical)+'">'
                '<div class="NewTrackPageHeader_trackContent__T">'
                '<div class="NewTrackPageHeader_trackTitle__T">Fixture</div>'
                '<span class="TrackPageArtistLink_artistNameText__T">'+artist+'</span></div>')
    def reply(self,txt):return types.SimpleNamespace(text=txt,raise_for_status=lambda:None)
    def test_working_original_needs_only_one_request(self):
        with patch('requests.get',return_value=self.reply(self.page())) as get:
            v=self.r._shazam_page_metadata(self.url)
            self.assertEqual(v[:2],('Fixture','Artist'));self.assertEqual(get.call_count,1)
            self.assertEqual(get.call_args.args[0],self.url)
    def test_recovery_urls_retained_on_bad_original(self):
        calls=[]
        def get(url,**kw):
            calls.append(url);return self.reply(self.page() if url==self.canonical else self.page(url='https://www.shazam.com/song/999999999/other'))
        with patch('requests.get',side_effect=get):v=self.r._shazam_page_metadata(self.url)
        self.assertEqual(v[:2],('Fixture','Artist'));self.assertEqual(calls,[self.url,self.local,self.canonical])
    def test_exact_id_alias_reuses_complete_proof(self):
        with patch('requests.get',return_value=self.reply(self.page())) as get:
            self.r._shazam_page_metadata(self.url);self.r._shazam_page_metadata(self.canonical)
            self.assertEqual(get.call_count,1)
    def test_failed_alias_cache_does_not_hide_later_success(self):
        with patch('requests.get',return_value=self.reply('')):self.r._shazam_page_metadata(self.url)
        with patch('requests.get',return_value=self.reply(self.page())):self.r._shazam_page_metadata(self.canonical)
        with patch('requests.get') as get:
            self.assertEqual(self.r._shazam_page_metadata(self.url)[:2],('Fixture','Artist'));get.assert_not_called()
    def test_title_only_is_not_shared_as_complete(self):
        with patch('requests.get',return_value=self.reply(self.page(artist=''))):self.r._shazam_page_metadata(self.url)
        self.assertFalse(self.r._page_identity_cache)
    def test_namespaces_remain_separate(self):
        with patch('requests.get',return_value=self.reply(self.page())):self.r._shazam_page_metadata(self.url)
        with patch('requests.get',return_value=self.reply('')) as get:self.r._shazam_page_metadata(self.url.replace('/track/','/song/'))
        self.assertTrue(get.called)
    def test_cancelled_lookup_does_not_query_or_return_cache(self):
        e=threading.Event();e.set()
        with patch('requests.get') as get:
            self.assertEqual(self.r._shazam_page_metadata(self.url,e),('','','',False));get.assert_not_called()

    def test_different_locale_does_not_reuse_alias_text(self):
        with patch('requests.get',return_value=self.reply(self.page())):
            self.r._shazam_page_metadata(self.url)
        with patch('requests.get',return_value=self.reply(self.page(artist='English Artist'))) as get:
            value=self.r._shazam_page_metadata(self.url.replace('/ja-jp/','/en-us/'))
        self.assertTrue(get.called);self.assertEqual(value[1],'English Artist')

class NativeIdentityOverlapContractTests(unittest.TestCase):
    """C# source guards only; NOT a native Windows execution/compilation test."""
    @classmethod
    def setUpClass(cls):
        cls.source=(Path(__file__).resolve().parents[1]/'native/ShazamWebViewBridge/BridgeHost.cs').read_text()
        cls.capture=cls.source.split('private async Task<Candidate?> CaptureRouteEvidenceAsync(',1)[1].split('private bool IsHomeUrl(',1)[0]
        cls.ack=cls.source.split('if (type == "metadata-ready")',1)[1].split('if (type == "audio")',1)[0]
    def test_identity_notification_once_only_for_verified_legacy_route(self):
        c=self.capture
        self.assertIn('bool identityHintSent = false;',c)
        self.assertIn('IsLegacyTrackRoute(routeCandidate.Url) && !identityHintSent',c)
        self.assertLess(c.index('_audioStarted && !_definiteNoMatch'),c.index('type = "identity"'))
        self.assertLess(c.index('deadline.Elapsed >= VerifiedRouteIdentityGrace'),c.index('type = "identity"'))
    def test_ack_bound_to_cycle_and_committed_typed_id(self):
        for guard in ('_busy && _cycle == id && _currentRecognitionId == id',
                      'hintId == trackId','currentId == hintId',
                      'SameShazamRouteKind(_webView.CoreWebView2.Source, url)'):
            self.assertIn(guard,self.ack)
        self.assertNotIn('Accept(',self.ack);self.assertNotIn('_best =',self.ack)
    def test_no_metadata_ready_keeps_existing_long_grace(self):
        self.assertIn('deadline.Elapsed >= identityGrace || metadataReady',self.capture)
        self.assertIn('VerifiedLegacyTrackIdentityGrace = TimeSpan.FromMilliseconds(3000)',self.source)
        self.assertIn('SameShazamRouteKind(_metadataReadyRoute, routeCandidate.Url)',self.capture)
    def test_ack_reset_on_new_request_and_finally(self):
        self.assertEqual(self.source.count('_metadataReadyRoute = "";'),3)
    def test_hint_does_not_send_a_result_or_accept_metadata_text(self):
        hint=self.capture.split('Program.Send(new { type = "identity"',1)[1].split('});',1)[0]
        self.assertIn('id = _cycle',hint);self.assertNotIn('title',hint.lower());self.assertNotIn('artist',hint.lower())

class SupersededLookupTests(unittest.TestCase):
    def test_complete_live_metadata_stops_further_http_fallbacks(self):
        entered=threading.Event();release=threading.Event();done=threading.Event();calls=[]
        def resolve(raw,lang,country,cancel):
            calls.append('first');entered.set();release.wait(2)
            if not cancel.is_set():calls.append('unnecessary-second')
            return ('Old','Old Artist')
        b=MetadataPrefetcher(resolver_factory=lambda:types.SimpleNamespace(resolve=resolve))
        cancel=threading.Event()
        try:
            b.request(1,KEY,RAW,'ja-JP','JP',cancel,lambda *args:done.set())
            self.assertTrue(entered.wait(1));b.seed(KEY,'Live','Live Artist');release.set()
            self.assertTrue(done.wait(1));self.assertEqual(calls,['first']);self.assertEqual(b.get(KEY),('Live','Live Artist'))
        finally:release.set();cancel.set();b.close()

if __name__=='__main__':unittest.main()
