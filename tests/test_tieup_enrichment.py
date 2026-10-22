"""Real orchestration and UI methods; Qt/network replaced, not a GUI E2E test."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from app.models.shazam_state import ShazamStateMixin
from app.models.search_request import SearchRequest, SearchPhase
from app.models.tieup_enrichment import TieupEnrichmentMixin
from app.services.utanet_service import LookupResult, track_key
from tests.test_utanet_service import TITLE, ARTIST, TIEUP, URL
from tests.test_autoplay_selection import _methods, _videos, _complete, WindowHarness, _Config, _Button
from tests import test_shazam_search_queue_priority as queue_fixture
from tests import test_shazam_speculative_preload as vote_fixture
from tests.test_webview_integration import ShazamService, Emitter, QTimer
from tests import test_search_templates as template_fixture

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ('2026-10-20 20:00:00', TITLE, ARTIST)


class FakeBroker:
    def __init__(self):
        self.jobs = []
        self.output = []
        self.cancel_all = Mock()
    def submit(self, job):
        self.jobs.append(job)
        return True
    def drain(self):
        result, self.output = self.output, []
        return result


class MemoryHistory:
    def __init__(self):
        self._generation = 3
        self.rows = []
        self.comments = {}
        self.saved = []
    def get_history(self): return list(self.rows)
    def get_tieup_comment(self, title, artist): return self.comments.get(track_key(title,artist), '')
    def set_tieup_comment(self, title, artist, comment, url):
        self.comments[track_key(title,artist)] = comment
        self.saved.append((title,artist,comment,url))
        return True


class EnrichmentHarness(TieupEnrichmentMixin, ShazamStateMixin):
    pass


class TieupWindow(TieupEnrichmentMixin, WindowHarness):
    pass


def install(win):
    win._tieup_epoch = 0
    win._tieup_interaction_epoch = 0
    win._tieup_contexts = {}
    win._tieup_requested_candidates = set()
    win._tieup_worker = FakeBroker()
    win._tieup_timer = QTimer()
    win.tieup_enabled = True
    win.source_mode = 'shazam'
    win.shazam_service = MemoryHistory()
    win._current_track_info = {}
    win.shazam_table_model = Mock()
    win.title_bar = Mock()
    win.config_service = _Config({'utanet_permission_confirmed': True})
    return win


def deliver(win, job=None, **overrides):
    job = job or win._tieup_worker.jobs[-1]
    result = LookupResult(job.title, job.artist, comment=TIEUP, source_url=URL,
                          matched_title=TITLE, matched_artist=ARTIST, status='found')
    from dataclasses import replace
    result = replace(result, **overrides)
    win._on_tieup_result(job.token, result)


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.w = install(EnrichmentHarness())
        self.w._sync_pending_search_args = Mock()
        self.w.search_youtube = Mock()
        self.c = self.w.shazam_flow.begin_vote(10, ENTRY)
    def request(self):
        self.w._request_tieup(ENTRY, self.c)
        return self.w._tieup_worker.jobs[-1]
    def test_disabled_no_job(self):
        self.w.tieup_enabled = False
        self.w._request_tieup(ENTRY, self.c)
        self.assertFalse(self.w._tieup_worker.jobs)
    def test_rekordbox_no_job(self):
        self.w.source_mode = 'rekordbox'
        self.w._request_tieup(ENTRY, self.c)
        self.assertFalse(self.w._tieup_worker.jobs)
    def test_missing_artist_no_job(self):
        self.w._request_tieup((ENTRY[0], TITLE, ''), self.c)
        self.assertFalse(self.w._tieup_worker.jobs)
    def test_first_vote_can_fetch_but_not_publish(self):
        self.request(); deliver(self.w)
        self.assertEqual(self.c.track_info['comment'], TIEUP)
        self.assertFalse(self.c.confirmed)
        self.assertFalse(self.c.play_sent)
        self.assertEqual(self.w.shazam_service.get_history(), [])
    def test_found_updates_and_refreshes_once(self):
        job=self.request(); deliver(self.w, job)
        self.assertEqual(self.w.shazam_service.get_tieup_comment(TITLE,ARTIST),TIEUP)
        self.w.shazam_table_model.refresh_comments.assert_called_once()
        self.w.search_youtube.assert_called_once_with(TITLE,ARTIST,TIEUP,from_list=True,
            allow_auto_play=False,source_mode='shazam',shazam_candidate_id=10,_metadata_refresh=True)
        deliver(self.w,job)
        self.assertEqual(self.w.search_youtube.call_count,1)
    def test_empty_response_leaves_everything_alone(self):
        self.request(); deliver(self.w,status='no_tieup',comment='')
        self.assertFalse(self.w.shazam_service.saved)
        self.w.search_youtube.assert_not_called()
    def test_timeout_leaves_everything_alone(self):
        self.request(); deliver(self.w,status='timeout',comment='')
        self.assertFalse(self.w.shazam_service.saved)
        self.w.search_youtube.assert_not_called()
    def test_mismatched_response_rejected(self):
        self.request(); deliver(self.w,title='Wrong song')
        self.assertFalse(self.w.shazam_service.saved)
    def test_cached_comment_selects_enriched_query_for_new_candidate(self):
        self.w.shazam_service.comments[track_key(TITLE,ARTIST)]=TIEUP
        self.request(); deliver(self.w)
        self.w.search_youtube.assert_called_once()
        self.assertEqual(self.c.tieup_state, 'found')
    def test_request_coalesces_repeated_confirmation(self):
        self.request(); self.request()
        self.assertEqual(len(self.w._tieup_worker.jobs),1)
    def test_rejected_uncommitted_vote_is_ignored(self):
        self.request(); self.w.shazam_flow.reject_vote(10); deliver(self.w)
        self.assertFalse(self.w.shazam_service.saved)
        self.w.search_youtube.assert_not_called()
    def test_retired_history_item_comment_only(self):
        self.c.confirmed=self.c.committed=True
        self.w.shazam_service.rows=[ENTRY]
        self.request()
        self.w.shazam_flow.begin_vote(11,('', 'New song','Other'))
        deliver(self.w)
        self.assertEqual(len(self.w.shazam_service.saved),1)
        self.w.search_youtube.assert_not_called()
    def test_reused_sequence_number_cannot_take_ownership(self):
        self.request()
        self.w.shazam_service._generation+=1
        self.w.shazam_flow.begin_vote(10,ENTRY)
        deliver(self.w)
        self.assertFalse(self.w.shazam_service.saved)
        self.w.search_youtube.assert_not_called()
    def test_stale_generation_preserves_row_but_not_search(self):
        self.request(); self.w.shazam_service.rows=[ENTRY]
        self.w.shazam_service._generation+=1
        deliver(self.w)
        self.assertEqual(len(self.w.shazam_service.saved),1)
        self.w.search_youtube.assert_not_called()
    def test_off_during_fetch_discards_late_result(self):
        job=self.request(); self.w.set_tieup_enabled(False); deliver(self.w,job)
        self.assertFalse(self.w.shazam_service.saved)
        self.w.search_youtube.assert_not_called()
        self.w._tieup_worker.cancel_all.assert_called_once()
    def test_source_change_cancels_contexts(self):
        job=self.request(); self.w._invalidate_tieup_requests(); deliver(self.w,job)
        self.assertFalse(self.w.shazam_service.saved)
    def test_manual_action_saves_comment_no_search(self):
        self.request(); self.w._tieup_manual_action(); deliver(self.w)
        self.assertEqual(len(self.w.shazam_service.saved),1)
        self.w.search_youtube.assert_not_called()
    def test_manual_override_saves_comment_no_search(self):
        self.request(); self.c.manual_override=True; deliver(self.w)
        self.assertEqual(len(self.w.shazam_service.saved),1)
        self.w.search_youtube.assert_not_called()
    def test_current_track_metadata_updated(self):
        self.w._current_track_info={'title':TITLE,'artist':ARTIST,'comment':''}
        self.request(); deliver(self.w)
        self.assertEqual(self.w._current_track_info['comment'],TIEUP)
    def test_other_current_track_metadata_not_overwritten(self):
        self.w._current_track_info={'title':'Other','artist':'Other','comment':'Manual'}
        self.request(); deliver(self.w)
        self.assertEqual(self.w._current_track_info['comment'],'Manual')
    def test_no_candidate_legacy_path_matches_exact_top_row(self):
        self.w._shazam_preview=None; self.w.shazam_service.rows=[ENTRY]
        self.w._request_tieup(ENTRY)
        deliver(self.w)
        self.assertIsNone(self.w.search_youtube.call_args.kwargs['shazam_candidate_id'])
    def test_poll_stops_timer_after_completion(self):
        job=self.request()
        self.w._tieup_worker.output=[(job.token,LookupResult(TITLE,ARTIST,status='not_found'))]
        self.w._poll_tieup_results()
        self.assertFalse(self.w._tieup_timer.running)
    def test_no_permission_decline_does_not_enable(self):
        self.w.tieup_enabled=False
        self.w.config_service=_Config({})
        box=types.SimpleNamespace(Yes=1,No=2,question=Mock(return_value=2))
        module=types.ModuleType('PySide6.QtWidgets'); module.QMessageBox=box
        with patch.dict(sys.modules,{'PySide6.QtWidgets':module}):
            self.w.set_tieup_enabled(True)
        self.assertFalse(self.w.tieup_enabled)
        self.assertFalse(self.w._tieup_worker.jobs)
    def test_permission_enabled_persists_toggle_and_fetches_current(self):
        self.w.tieup_enabled=False
        self.w.set_tieup_enabled(True)
        self.assertTrue(self.w.config_service.get('utanet_tieup_enabled'))
        self.assertEqual(len(self.w._tieup_worker.jobs),1)
    def test_manual_action_cancels_both_automatic_search_stages(self):
        original=SearchRequest(TITLE,ARTIST,candidate_id=10)
        enrich=SearchRequest(TITLE,ARTIST,candidate_id=10,metadata_refresh=True)
        self.w.shazam_flow.active_search=original
        self.w.shazam_flow.queued_search=enrich
        self.w._tieup_manual_action()
        self.assertFalse(original.valid); self.assertFalse(enrich.valid)


class SearchLifecycleTests(unittest.TestCase):
    def setUp(self):
        queue_fixture.UIQueuedSearchTests.setUp(self)
        w=self.w; w.__class__=TieupWindow
        # Retain queue fixture config and model; add optional metadata services.
        w._tieup_epoch=w._tieup_interaction_epoch=0
        w._tieup_contexts={}; w._tieup_requested_candidates=set()
        w._tieup_worker=FakeBroker(); w._tieup_timer=QTimer(); w.tieup_enabled=True
        w.shazam_service=MemoryHistory()
        w.shazam_table_model.refresh_comments=Mock()
        w.youtube_search_thread.running=False
        w.youtube_search_thread=None
        w._set_youtube_search_error=Mock()
    def tearDown(self): queue_fixture.UIQueuedSearchTests.tearDown(self)
    def start(self, confirmed=False):
        self.w.on_shazam_candidate_started(10,ENTRY)
        if confirmed: self.w.on_shazam_candidate_confirmed(10,ENTRY)
        return self.w._shazam_preview
    def finish(self):
        self.w.youtube_search_thread.running=False
        self.w._on_search_finished()
    def test_first_vote_starts_http_beside_youtube(self):
        c=self.start()
        self.assertEqual(len(self.workers),1)
        self.assertEqual(len(self.w._tieup_worker.jobs),1)
        self.assertTrue(self.w.youtube_search_thread.running)
        self.assertFalse(c.confirmed)
    def test_cached_comment_does_not_leak_into_initial_template(self):
        self.w.shazam_service.comments[track_key(TITLE,ARTIST)]=TIEUP
        self.start()
        self.assertEqual(self.svc.create_search_queries_from_track.call_args.args[2], '')
    def test_refresh_preempts_with_comment_and_epoch(self):
        self.start(); old=self.w.youtube_search_thread; deliver(self.w)
        request=self.w.shazam_flow.active_search
        self.assertEqual(request.comment,TIEUP)
        self.assertTrue(request.metadata_refresh)
        self.assertEqual(request.enrichment_epoch,0)
        self.assertEqual(len(self.workers),2)
        self.assertTrue(old.running); self.assertTrue(old.aborted)
        self.assertIn(old,self.w._retired_youtube_threads)
    def test_original_result_discarded_after_authoritative_refresh(self):
        c=self.start(); original=self.w.youtube_search_thread; deliver(self.w)
        self.w.sender=lambda:original
        _complete(self.w,_videos('3:00'))
        self.assertIsNone(c.video_id)
        self.w._send_video_command.assert_not_called()
    def test_refresh_keeps_template_and_candidate(self):
        c=self.start(); deliver(self.w)
        request=self.w.shazam_flow.active_search
        self.assertTrue(request.metadata_refresh)
        self.assertEqual(request.candidate_id,c.id)
        self.assertEqual(self.svc.create_search_queries_from_track.call_args.args[2],TIEUP)
        self.assertEqual(self.svc.create_search_queries_from_track.call_args.kwargs['source_mode'],'shazam_tieup')
    def test_refresh_before_two_votes_can_only_preload(self):
        self.start(); deliver(self.w)
        _complete(self.w,_videos('3:00'))
        commands=[x.args[0] for x in self.w._send_video_command.call_args_list]
        self.assertEqual(commands,['PRELOAD'])
    def test_enabling_tieup_after_initial_play_can_replace_it_once(self):
        self.w.tieup_enabled=False
        c=self.start(True); _complete(self.w,_videos('3:00'))
        self.finish(); c.playing_ack=True; c.play_sent=True
        self.w.tieup_enabled=True; self.w._request_tieup(ENTRY,c)
        self.w._send_video_command.reset_mock()
        deliver(self.w); _complete(self.w,_videos('2:00'))
        self.assertEqual(self.w._send_video_command.call_args.args[0],'PRELOAD')
        self.assertFalse(c.playing_ack)
        self.assertTrue(c.cue_from_tieup)
    def test_existing_initial_play_intent_does_not_block_enriched_cue(self):
        c=self.start(True); _complete(self.w,_videos('3:00')); self.finish()
        c.play_sent=True  # Compatibility with an ON toggle / old in-flight cue.
        self.w._send_video_command.reset_mock()
        deliver(self.w); _complete(self.w,_videos('2:00'))
        self.assertEqual(self.w._send_video_command.call_args.args[0],'PRELOAD')
        self.assertFalse(c.play_sent)
    def test_refresh_empty_does_not_fall_back_to_initial_cue(self):
        c=self.start(True); _complete(self.w,_videos('3:00')); self.finish()
        deliver(self.w); self.w._send_video_command.reset_mock(); _complete(self.w,[])
        self.assertEqual(self.w.left_pane.model.videos,[])
        self.w._send_video_command.assert_not_called()
        self.assertIsNone(c.video_id)
        self.assertFalse(c.tieup_allows_play)
    def test_manual_epoch_blocks_late_enrichment_search(self):
        self.start(); deliver(self.w)
        self.w._tieup_manual_action(); _complete(self.w,_videos('3:00'))
        self.assertFalse(self.w.left_pane.model.videos)
        self.w._send_video_command.assert_not_called()
    def test_off_blocks_late_enrichment_search(self):
        self.start(); deliver(self.w)
        self.w._invalidate_tieup_requests(); _complete(self.w,_videos('3:00'))
        self.assertFalse(self.w.left_pane.model.videos)
    def test_next_first_vote_preserves_confirmed_enriched_search(self):
        self.start(True); deliver(self.w)
        old=self.w.shazam_flow.active_search
        self.w.on_shazam_candidate_started(11,('', 'Other song','Other Artist'))
        self.assertTrue(old.valid)
        self.assertEqual(self.w.shazam_flow.queued_search.candidate_id,11)
    def test_late_original_worker_callback_does_not_overwrite_refresh(self):
        self.start(); original=self.w.youtube_search_thread
        deliver(self.w)
        self.w.sender=lambda:original
        _complete(self.w,_videos('3:00'))
        self.assertFalse(self.w.left_pane.model.videos)
    def test_disabled_feature_never_submits_worker_job(self):
        self.w.tieup_enabled=False
        self.start()
        self.assertEqual(len(self.workers),1)
        self.assertFalse(self.w._tieup_worker.jobs)


class HistoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'history.json'
        self.path.write_text('[]')
        self.svc=object.__new__(ShazamService)
        self.svc._history_path=self.path
        self.svc._history=[]; self.svc._history_comments={}
    def tearDown(self): self.tmp.cleanup()
    def test_first_vote_annotation_not_written_or_published(self):
        self.svc.set_tieup_comment(TITLE,ARTIST,TIEUP,URL)
        self.assertEqual(json.loads(self.path.read_text()),[])
        self.assertEqual(self.svc.get_history(),[])
    def test_commit_retains_existing_comment(self):
        self.svc.set_tieup_comment(TITLE,ARTIST,TIEUP,URL)
        self.svc._history=[ENTRY]; self.svc._save_history()
        saved=json.loads(self.path.read_text())
        self.assertEqual(saved[0]['comment'],TIEUP)
        self.assertEqual(saved[0]['tieup_url'],URL)
    def test_late_comment_updates_committed_history_without_new_row(self):
        self.svc._history=[ENTRY]; self.svc._save_history()
        self.svc.set_tieup_comment(TITLE,ARTIST,TIEUP,URL)
        self.assertEqual(len(json.loads(self.path.read_text())),1)
        self.assertEqual(self.svc.get_history(),[ENTRY])
    def test_restart_loads_comment_and_legacy_raw_tuple(self):
        self.svc._history=[ENTRY]; self.svc.set_tieup_comment(TITLE,ARTIST,TIEUP,URL)
        self.svc._history_comments={}
        rows=self.svc._load_history()
        self.assertEqual(rows,[ENTRY])
        self.assertEqual(self.svc.get_tieup_comment(TITLE,ARTIST),TIEUP)
    def test_old_history_without_comment_loads(self):
        self.path.write_text(json.dumps([dict(zip(('timestamp','title','artist'),ENTRY))]))
        self.assertEqual(self.svc._load_history(),[ENTRY])
        self.assertEqual(self.svc.get_tieup_comment(TITLE,ARTIST),'')
    def test_artist_mismatch_not_given_comment(self):
        self.svc.set_tieup_comment(TITLE,ARTIST,TIEUP,URL)
        self.assertEqual(self.svc.get_tieup_comment(TITLE,'Other artist'),'')
    def test_no_comment_does_not_erase_existing(self):
        self.svc.set_tieup_comment(TITLE,ARTIST,TIEUP,URL)
        self.assertFalse(self.svc.set_tieup_comment(TITLE,ARTIST,'',URL))
        self.assertEqual(self.svc.get_tieup_comment(TITLE,ARTIST),TIEUP)
    def test_comments_memory_is_bounded(self):
        for i in range(530): self.svc.set_tieup_comment(str(i),'Artist','Info',URL)
        self.assertLessEqual(len(self.svc._history_comments),512)


class TemplateTests(unittest.TestCase):
    def service(self, template):
        return template_fixture.SearchTemplateTests()._service({'youtube_search_template_shazam':template})
    def test_empty_comment_variable_is_empty(self):
        svc=self.service('%tracktitle% %artist% %comment%')
        query=svc.create_search_query_from_track(TITLE,ARTIST,None,source_mode='shazam')
        self.assertEqual(query,TITLE+' '+ARTIST)
    def test_comment_uses_configured_position(self):
        svc=self.service('%comment% %tracktitle%')
        query=svc.create_search_query_from_track(TITLE,ARTIST,TIEUP,source_mode='shazam')
        self.assertEqual(query,TIEUP+' '+TITLE)
    def test_template_without_comment_does_not_force_keywords(self):
        svc=self.service('%tracktitle% %artist%')
        self.assertEqual(svc.create_search_query_from_track(TITLE,ARTIST,TIEUP,source_mode='shazam'),TITLE+' '+ARTIST)
    def test_comment_only_empty_generates_no_query(self):
        svc=self.service('%comment%')
        self.assertEqual(svc.create_search_queries_from_track(TITLE,ARTIST,'',source_mode='shazam'),[])


class DisplayTests(unittest.TestCase):
    def model(self):
        class Index:
            def __init__(self,row=-1,col=-1):self.r,self.c=row,col
            def row(self):return self.r
            def column(self):return self.c
            def isValid(self):return self.r>=0 and self.c>=0
        class AbstractModel:
            def __init__(self):self.dataChanged=Emitter();self.resets=0
            def index(self,r,c):return Index(r,c)
            def beginResetModel(self):self.resets+=1
            def endResetModel(self):pass
        qt=types.SimpleNamespace(DisplayRole=0,ToolTipRole=3,Horizontal=1)
        core=types.ModuleType('PySide6.QtCore');core.Qt=qt;core.QModelIndex=Index;core.QAbstractTableModel=AbstractModel
        widgets=types.ModuleType('PySide6.QtWidgets');widgets.QTableView=object;widgets.QHeaderView=object
        with patch.dict(sys.modules,{'PySide6.QtCore':core,'PySide6.QtWidgets':widgets}):
            spec=importlib.util.spec_from_file_location('_right_model_test',ROOT/'ui/widgets/right_table_view.py')
            base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
            with patch.dict(sys.modules,{'ui.widgets.right_table_view':base}):
                spec=importlib.util.spec_from_file_location('_shazam_model_test',ROOT/'ui/widgets/shazam_history_model.py')
                mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        self.comments={}
        return mod.ShazamHistoryModel([ENTRY],lambda t,a:self.comments.get(track_key(t,a),''))
    def test_visible_order_is_title_artist_comment(self):
        m=self.model()
        self.assertEqual([m.data(m.index(0,c)) for c in range(3)],[TITLE,ARTIST,''])
        self.assertEqual(m._data,[ENTRY])
    def test_headers(self):
        m=self.model()
        self.assertEqual([m.headerData(i,1) for i in range(3)],['\u66f2\u540d','\u30a2\u30fc\u30c6\u30a3\u30b9\u30c8\u540d','\u30b3\u30e1\u30f3\u30c8'])
    def test_timestamp_is_tooltip_only(self):
        m=self.model()
        self.assertIn(ENTRY[0],m.data(m.index(0,0),3))
        self.assertNotIn(ENTRY[0],m.data(m.index(0,0)))
    def test_comment_refresh_does_not_reset_selection(self):
        m=self.model(); self.comments[track_key(TITLE,ARTIST)]=TIEUP
        m.refresh_comments()
        self.assertEqual(m.resets,0)
        self.assertEqual(len(m.dataChanged.calls),1)
        self.assertEqual(m.data(m.index(0,2)),TIEUP)
    def test_search_fields_read_comment_not_timestamp(self):
        m=self.model(); self.comments[track_key(TITLE,ARTIST)]=TIEUP
        method=_methods('MainWindow',['_get_track_search_fields'])['_get_track_search_fields']
        w=types.SimpleNamespace(source_mode='shazam',table_model=m)
        self.assertEqual(method(w,0),(TITLE,ARTIST,TIEUP))
    def test_button_updates_without_recursive_signal(self):
        method=_methods('TitleBar',['set_tieup_checked'])['set_tieup_checked']
        button=_Button();button.handler=Mock()
        bar=types.SimpleNamespace(tieup_button=button)
        method(bar,True,enabled=True)
        self.assertTrue(button.checked);self.assertTrue(button.enabled)
        self.assertIn('ON',button.text)
        button.handler.assert_not_called()

if __name__=='__main__':unittest.main()
