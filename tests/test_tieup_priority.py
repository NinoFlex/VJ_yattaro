"""Two independent templates and a single winning automatic playback source.

Runs production MainWindow/mixin methods with Qt, HTTP and player transports
replaced. Does not claim a live Windows/WebView/YouTube integration test.
"""
from itertools import permutations
import ast
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from app.models.search_request import SearchPhase, SearchRequest
from app.models.track_candidate import TrackCandidate
from app.services.config_service import ConfigService
from app.services.utanet_service import LookupResult
from tests import test_tieup_enrichment as fixture
from tests.test_autoplay_selection import _complete, _methods
from tests import test_search_templates as templates
YouTubeSearchThread, youtube_module = templates.YouTubeSearchThread, templates.youtube_module
from tests import test_title_keyword_editor as editor

ROOT = Path(__file__).resolve().parents[1]
ENTRY = fixture.ENTRY


def videos(prefix):
    return [dict(video_id=prefix+'-1', title=prefix+' main', duration='1:35'),
            dict(video_id=prefix+'-2', title=prefix+' alternative', duration='1:38')]


class PriorityWindowTests(unittest.TestCase):
    def setUp(self):
        fixture.SearchLifecycleTests.setUp(self)
        self.w.title_bar.tieup_button = Mock()
        self.w.title_bar.set_tieup_checked = Mock()
        self.command_number = 0
        def send(command, video_id, _data=None, **kw):
            self.command_number += 1
            token = f'command-{self.command_number}'
            if command == 'PRELOAD':
                self.w.shazam_flow.register_cue_request(video_id, token)
                self.w._preload_watch_video_id = video_id
                self.w._preload_watch_request_id = token
            elif command == 'PLAY':
                self.w.shazam_flow.register_play_request(video_id, token)
                self.w._play_watch_video_id = video_id
                self.w._play_watch_request_id = token
            return True
        self.w._send_video_command = Mock(side_effect=send)
        fn = _methods('MainWindow', ['on_youtube_search_error'])['on_youtube_search_error']
        fn.__globals__['SearchPhase'] = SearchPhase
        self.w.on_youtube_search_error = fn.__get__(self.w)

    def tearDown(self):
        fixture.SearchLifecycleTests.tearDown(self)

    def start(self, entry=ENTRY, cid=10, confirm=False):
        self.w.on_shazam_candidate_started(cid, entry)
        c = self.w._shazam_preview
        worker = self.w.youtube_search_thread
        if confirm:
            self.w.on_shazam_candidate_confirmed(cid, entry)
        return c, worker

    def done(self, worker, prefix, ready=True):
        self.w.sender = lambda: worker
        _complete(self.w, videos(prefix))
        worker.running = False
        self.w._on_search_finished()
        self.w.sender = lambda: self.w.youtube_search_thread
        c = self.w._shazam_preview or self.w.shazam_flow.confirmed_pending
        if ready and c is not None and c.video_id:
            self.ready(c)

    def ready(self, c):
        self.w._handle_player_feedback(dict(state='ready', videoId=c.video_id,
            requestId=c.cue_request_id or '', playerId='B', isCurrent=False))

    def plays(self):
        return [call.args[1] for call in self.w._send_video_command.call_args_list
                if call.args[0] == 'PLAY']

    def test_initial_ready_and_confirmed_wait_for_tieup(self):
        c, old = self.start(confirm=True)
        self.done(old, 'initial')
        self.assertTrue(c.ready)
        self.assertEqual(c.tieup_state, 'pending')
        self.assertIsNone(self.w.pending_play_video_id)
        self.assertEqual(self.plays(), [])

    def test_positive_result_discards_ready_initial_and_plays_enriched_only(self):
        c, old = self.start(confirm=True)
        self.done(old, 'initial')
        fixture.deliver(self.w)
        self.assertFalse(c.ready)
        self.assertIsNone(c.video_id)
        self.assertEqual(self.plays(), [])
        self.done(self.w.youtube_search_thread, 'tieup')
        self.assertEqual(self.plays(), ['tieup-1'])
        self.ready(c)
        self.assertEqual(self.plays(), ['tieup-1'])

    def test_same_video_id_still_requires_new_request_ready(self):
        c, old = self.start(confirm=True)
        self.done(old, 'same')
        first_token = c.cue_request_id
        fixture.deliver(self.w)
        self.done(self.w.youtube_search_thread, 'same', ready=False)
        self.assertNotEqual(c.cue_request_id, first_token)
        self.w._handle_player_feedback(dict(state='ready', videoId='same-1', requestId=first_token))
        self.assertEqual(self.plays(), [])
        self.ready(c)
        self.assertEqual(self.plays(), ['same-1'])

    def test_first_vote_found_never_plays_until_second_vote(self):
        c, old = self.start()
        fixture.deliver(self.w)
        self.done(self.w.youtube_search_thread, 'tieup')
        self.assertEqual(self.plays(), [])
        self.w.on_shazam_candidate_confirmed(c.id,c.entry)
        self.assertEqual(self.plays(), ['tieup-1'])

    def test_initial_worker_ends_after_enriched_worker_does_not_change_ui_state(self):
        c, old = self.start(confirm=True)
        fixture.deliver(self.w)
        self.assertTrue(old.running)
        self.assertTrue(old.aborted)
        new = self.w.youtube_search_thread
        self.done(new, 'tieup')
        before = self.w._set_searching_state.call_count
        self.done(old, 'initial')
        self.assertEqual(self.w._set_searching_state.call_count, before)
        self.assertNotIn(old,self.w._retired_youtube_threads)
        self.assertEqual(self.w.left_pane.model.videos[0]['video_id'], 'tieup-1')
        self.assertEqual(self.plays(), ['tieup-1'])

    def test_old_error_during_new_query_does_not_clear_busy_or_display_error(self):
        c, old = self.start(confirm=True)
        fixture.deliver(self.w)
        new = self.w.youtube_search_thread
        self.w.sender = lambda:old
        self.w.on_youtube_search_error('obsolete network error')
        self.w._on_search_finished()
        self.assertIs(self.w.youtube_search_thread,new)
        self.assertTrue(new.running)
        self.w._set_youtube_search_error.assert_not_called()
        self.assertTrue(self.w.shazam_flow.active_search.valid)

    def test_empty_enriched_results_never_release_initial_play(self):
        c, old = self.start(confirm=True)
        self.done(old,'initial')
        fixture.deliver(self.w)
        _complete(self.w,[])
        self.assertEqual(self.plays(),[])
        self.assertEqual(self.w.left_pane.model.videos,[])
        self.assertFalse(c.tieup_allows_play)

    def test_failed_enriched_query_does_not_use_initial(self):
        c, old = self.start(confirm=True)
        self.done(old,'initial')
        fixture.deliver(self.w)
        self.w.on_youtube_search_error('new query failed')
        self.assertEqual(self.plays(),[])
        self.assertFalse(c.tieup_allows_play)
        self.w._set_youtube_search_error.assert_called_once_with('new query failed')

    def test_initial_api_error_does_not_stop_successful_tieup_query(self):
        c, old=self.start(confirm=True)
        self.w.on_youtube_search_error('initial query failed')
        old.running=False; self.w._on_search_finished()
        fixture.deliver(self.w)
        self.done(self.w.youtube_search_thread,'tieup')
        self.assertEqual(self.plays(),['tieup-1'])

    def test_manual_play_during_wait_preserves_manual_control(self):
        c, old=self.start(confirm=True)
        self.done(old,'initial')
        self.w._cancel_autoplay_recovery('manual play')
        job=self.w._tieup_worker.jobs[-1]
        fixture.deliver(self.w,job)
        self.assertEqual(self.plays(),[])
        self.assertIsNone(self.w.youtube_search_thread)
        self.assertEqual(c.track_info['comment'],fixture.TIEUP)

    def test_manual_play_during_enriched_query_rejects_its_late_result(self):
        c, old=self.start(confirm=True)
        fixture.deliver(self.w)
        new=self.w.youtube_search_thread
        self.w._cancel_autoplay_recovery('manual play')
        self.done(new,'tieup')
        self.assertEqual(self.plays(),[])
        self.assertFalse(self.w.left_pane.model.videos)

    def test_autoplay_off_never_plays_either_query(self):
        self.w.auto_play_top_result=False
        c, old=self.start(confirm=True)
        fixture.deliver(self.w)
        self.done(self.w.youtube_search_thread,'tieup')
        self.assertEqual(self.plays(),[])
        self.assertTrue(c.ready)

    def test_off_on_after_confirmation_does_not_reauthorize(self):
        c, old=self.start(confirm=True)
        self.w.set_auto_play_enabled(False)
        self.w.set_auto_play_enabled(True)
        fixture.deliver(self.w)
        self.done(self.w.youtube_search_thread,'tieup')
        self.assertEqual(self.plays(),[])

    def test_turning_tieup_off_releases_ready_initial_once(self):
        c, old=self.start(confirm=True)
        self.done(old,'initial')
        self.w.set_tieup_enabled(False)
        self.assertEqual(self.plays(),['initial-1'])
        fixture.deliver(self.w)
        self.assertEqual(self.plays(),['initial-1'])

    def test_disabled_mode_does_not_delay_initial_playback(self):
        self.w.tieup_enabled=False
        c, old=self.start(confirm=True)
        self.done(old,'initial')
        self.assertEqual(self.plays(),['initial-1'])
        self.assertFalse(self.w._tieup_worker.jobs)

    def test_missing_artist_resolves_without_deadlock(self):
        c, old=self.start(entry=(ENTRY[0],ENTRY[1],''),confirm=True)
        self.done(old,'initial')
        self.assertEqual(self.plays(),['initial-1'])
        self.assertEqual(c.tieup_status,'missing_identity')

    def test_same_cached_comment_selects_enriched_search_for_this_candidate(self):
        self.w.shazam_service.set_tieup_comment(ENTRY[1],ENTRY[2],fixture.TIEUP,fixture.URL)
        c, old=self.start(confirm=True)
        fixture.deliver(self.w,cached=True)
        self.done(self.w.youtube_search_thread,'tieup')
        self.assertEqual(self.plays(),['tieup-1'])

    def test_duplicate_positive_reply_does_not_create_second_query(self):
        c, old=self.start(confirm=True)
        job=self.w._tieup_worker.jobs[-1]
        fixture.deliver(self.w,job)
        self.done(self.w.youtube_search_thread,'tieup')
        fixture.deliver(self.w,job)
        self.assertEqual(len(self.workers),2)
        self.assertEqual(self.plays(),['tieup-1'])

    def test_rejected_false_vote_result_never_creates_enriched_search(self):
        c, old=self.start()
        job=self.w._tieup_worker.jobs[-1]
        self.w.on_shazam_candidate_rejected(c.id,'wrong song')
        fixture.deliver(self.w,job)
        self.done(old,'initial')
        self.assertEqual(len(self.workers),1)
        self.assertEqual(self.plays(),[])

    def test_new_confirmed_song_cannot_be_stolen_by_old_enrichment(self):
        c, old=self.start(confirm=True)
        job=self.w._tieup_worker.jobs[-1]
        self.start(entry=('', 'Other song', 'Other artist'),cid=11,confirm=True)
        before=len(self.workers)
        fixture.deliver(self.w,job)
        self.assertEqual(len(self.workers),before)
        self.assertEqual(self.w._shazam_preview.id,11)
        self.assertEqual(self.plays(),[])

    def test_protected_confirmed_song_enrichment_survives_new_first_vote(self):
        b, initial=self.start(confirm=True)
        bjob=self.w._tieup_worker.jobs[-1]
        c, _unused=self.start(entry=('', 'Other song', 'Other artist'),cid=11)
        fixture.deliver(self.w,bjob)
        self.assertTrue(self.w.shazam_flow.active_search.metadata_refresh)
        self.assertEqual(self.w.shazam_flow.active_search.candidate_id,b.id)
        self.assertEqual(self.w.shazam_flow.queued_search.candidate_id,c.id)
        worker=self.w.youtube_search_thread
        _complete(self.w,videos('tieup'))
        self.ready(b)
        self.assertEqual(self.plays(),['tieup-1'])
        self.assertFalse(c.confirmed)

    def test_settings_save_restarts_lookup_instead_of_leaving_pending_forever(self):
        c, old=self.start(confirm=True)
        self.done(old,'initial')
        job=self.w._tieup_worker.jobs[-1]
        self.w._invalidate_tieup_requests()
        self.w._resume_tieup_after_settings()
        fixture.deliver(self.w,job)
        self.assertEqual(self.plays(),[])
        fixture.deliver(self.w)
        self.done(self.w.youtube_search_thread,'tieup')
        self.assertEqual(self.plays(),['tieup-1'])

    def test_enriched_cue_fallback_stays_in_enriched_search(self):
        c, old=self.start(confirm=True)
        fixture.deliver(self.w)
        self.done(self.w.youtube_search_thread,'tieup',ready=False)
        self.w._handle_autoplay_failure('tieup-1','simulated preload failure')
        self.ready(c)
        self.assertEqual(self.plays(),['tieup-2'])
        self.assertTrue(c.cue_from_tieup)

    def test_initial_cue_fallback_while_pending_still_cannot_play(self):
        c, old=self.start(confirm=True)
        self.done(old,'initial',ready=False)
        self.w._handle_autoplay_failure('initial-1','simulated preload failure')
        self.ready(c)
        self.assertEqual(self.plays(),[])
        fixture.deliver(self.w,status='no_tieup',comment='')
        self.assertEqual(self.plays(),['initial-2'])

    def test_shazam_confirmed_publication_only_path_has_same_gate(self):
        self.w.shazam_service._last_publication_candidate_id=10
        self.w.on_shazam_new_track_detected(ENTRY)
        c=self.w._shazam_preview
        self.assertTrue(c.confirmed)
        old=self.w.youtube_search_thread
        self.done(old,'initial')
        self.assertEqual(self.plays(),[])
        fixture.deliver(self.w)
        self.done(self.w.youtube_search_thread,'tieup')
        self.assertEqual(self.plays(),['tieup-1'])


    def test_off_during_enriched_search_restarts_initial(self):
        c, old = self.start(confirm=True)
        fixture.deliver(self.w)
        enriched = self.w.youtube_search_thread
        self.w.set_tieup_enabled(False)
        initial = self.w.youtube_search_thread
        self.assertIsNot(initial, enriched)
        self.assertTrue(enriched.aborted)
        self.assertFalse(self.w.shazam_flow.active_search.metadata_refresh)
        self.done(enriched, 'obsolete')
        self.done(initial, 'initial')
        self.assertEqual(self.plays(), ['initial-1'])

    def test_settings_save_restarts_cancelled_enriched_query_with_new_template(self):
        c, old = self.start(confirm=True)
        fixture.deliver(self.w)
        obsolete = self.w.youtube_search_thread
        jobs = len(self.w._tieup_worker.jobs)
        self.w._invalidate_tieup_requests()
        self.w._resume_tieup_after_settings()
        replacement = self.w.youtube_search_thread
        self.assertIsNot(replacement, obsolete)
        self.assertTrue(obsolete.aborted)
        self.assertEqual(len(self.w._tieup_worker.jobs), jobs)
        self.assertTrue(self.w.shazam_flow.active_search.metadata_refresh)
        self.done(obsolete, 'obsolete')
        self.done(replacement, 'tieup')
        self.assertEqual(self.plays(), ['tieup-1'])

    def test_settings_save_discards_unplayed_enriched_cue_not_recognition(self):
        c, old = self.start()
        fixture.deliver(self.w)
        self.done(self.w.youtube_search_thread, 'before')
        previous_token = c.cue_request_id
        self.assertTrue(c.ready)
        self.w._invalidate_tieup_requests()
        self.w._resume_tieup_after_settings()
        self.assertFalse(c.ready)
        self.assertFalse(c.cue_from_tieup)
        self.w.on_shazam_candidate_confirmed(c.id,c.entry)
        self.w._handle_player_feedback(dict(state='ready',videoId='before-1',requestId=previous_token))
        self.assertEqual(self.plays(), [])
        self.done(self.w.youtube_search_thread,'after')
        self.assertEqual(self.plays(), ['after-1'])

    def test_settings_save_does_not_repeat_already_started_enriched(self):
        c, old = self.start(confirm=True)
        fixture.deliver(self.w)
        self.done(self.w.youtube_search_thread, 'tieup')
        self.w._invalidate_tieup_requests()
        self.w._resume_tieup_after_settings()
        self.assertIsNone(self.w.youtube_search_thread)
        self.assertEqual(self.plays(), ['tieup-1'])

    def test_settings_restart_does_not_resurrect_old_generation(self):
        c, old = self.start(confirm=True)
        self.done(old, 'initial')
        count = len(self.w._tieup_worker.jobs)
        self.w._invalidate_tieup_requests()
        self.w.shazam_service._generation += 2
        self.w._resume_tieup_after_settings()
        self.assertEqual(len(self.w._tieup_worker.jobs),count)
        self.assertEqual(self.plays(), [])


    def test_enrichment_preempts_superseded_previous_candidate_worker(self):
        b, obsolete = self.start(confirm=True)
        c, _unused = self.start(entry=('', 'Next', 'Artist'),cid=11,confirm=True)
        fixture.deliver(self.w)
        self.assertTrue(obsolete.aborted)
        self.assertEqual(self.w.shazam_flow.active_search.candidate_id,11)
        self.assertTrue(self.w.shazam_flow.active_search.metadata_refresh)
        self.done(self.w.youtube_search_thread,'tieup')
        self.assertEqual(self.plays(),['tieup-1'])

    def test_enriched_first_vote_waits_for_protected_owner_then_confirmation_preempts(self):
        b, obsolete = self.start(confirm=True)
        c, _unused = self.start(entry=('', 'Next', 'Artist'),cid=11)
        fixture.deliver(self.w)
        self.assertIs(self.w.youtube_search_thread,obsolete)
        self.assertTrue(self.w.shazam_flow.queued_search.metadata_refresh)
        self.assertFalse(getattr(obsolete,'aborted',False))
        self.w.on_shazam_candidate_confirmed(c.id,c.entry)
        self.assertTrue(obsolete.aborted)
        self.assertEqual(self.w.shazam_flow.active_search.candidate_id,11)
        self.assertTrue(self.w.shazam_flow.active_search.metadata_refresh)
        self.done(self.w.youtube_search_thread,'tieup')
        self.assertEqual(self.plays(),['tieup-1'])


    def test_enabling_after_initial_playing_selects_enriched_instead_of_list_only(self):
        self.w.tieup_enabled=False
        c, old=self.start(confirm=True)
        self.done(old,'initial')
        self.assertEqual(self.plays(),['initial-1'])
        c.playing_ack=True
        self.w.config_service.save_config({"utanet_permission_confirmed":True})
        self.w.set_tieup_enabled(True)
        fixture.deliver(self.w)
        self.assertFalse(c.play_sent)
        self.done(self.w.youtube_search_thread,'tieup')
        self.assertEqual(self.plays(),['initial-1','tieup-1'])
        self.ready(c)
        self.assertEqual(self.plays(),['initial-1','tieup-1'])


def permutation_test(order, found):
    def run(self):
        c, initial=self.start()
        for event in order:
            if event=='C': self.w.on_shazam_candidate_confirmed(c.id,c.entry)
            elif event=='I': self.done(initial,'initial')
            elif event=='T':
                fixture.deliver(self.w, status='found' if found else 'not_found',
                                comment=fixture.TIEUP if found else '')
            elif event=='E': self.done(self.w.youtube_search_thread,'tieup')
            if c.tieup_state=='pending': self.assertEqual(self.plays(),[])
        self.assertEqual(self.plays(),['tieup-1' if found else 'initial-1'])
    return run

for order in permutations('CITE'):
    if order.index('T') < order.index('E'):
        setattr(PriorityWindowTests,'test_found_order_'+''.join(order),permutation_test(order,True))
for order in permutations('CIT'):
    setattr(PriorityWindowTests,'test_empty_order_'+''.join(order),permutation_test(order,False))


def terminal_test(status):
    def run(self):
        c, old=self.start(confirm=True)
        self.done(old,'initial')
        fixture.deliver(self.w,status=status,comment='')
        self.assertEqual(c.tieup_state,'empty')
        self.assertEqual(c.tieup_status,status)
        self.assertEqual(self.plays(),['initial-1'])
    return run
for status in ['no_tieup','not_found','error','timeout','unavailable','cooldown','missing_identity','found']:
    setattr(PriorityWindowTests,'test_terminal_empty_'+status,terminal_test(status))


class IndependentTemplateTests(unittest.TestCase):
    def service(self, values=None):
        return templates.SearchTemplateTests()._service(values or {})
    def test_default_initial_and_enriched_templates(self):
        s=self.service()
        self.assertEqual(s.get_search_template('shazam'),'%tracktitle% %artist%')
        self.assertEqual(s.get_search_template('shazam_tieup'),'%tracktitle% %comment%')
    def test_custom_templates_are_independent(self):
        s=self.service({'youtube_search_template_shazam':'%artist% %tracktitle%',
                        'youtube_search_template_shazam_tieup':'%comment% %tracktitle%'})
        self.assertEqual(s.create_search_query_from_track('Song','Artist','Anime',source_mode='shazam'),'Artist Song')
        self.assertEqual(s.create_search_query_from_track('Song','Artist','Anime',source_mode='shazam_tieup'),'Anime Song')
    def test_enriched_artist_fallback_preserves_comment(self):
        s=self.service({'youtube_search_template_shazam_tieup':'%tracktitle% %comment% %artist%'})
        self.assertEqual(s.create_search_queries_from_track('Song','Artist','Anime',source_mode='shazam_tieup'),['Song Anime Artist','Song Anime'])
    def test_default_tieup_template_does_not_add_artist(self):
        self.assertEqual(self.service().create_search_queries_from_track('Song','Artist','Anime',source_mode='shazam_tieup'),['Song Anime'])
    def test_blank_comment_has_no_literal_placeholder(self):
        self.assertEqual(self.service().create_search_query_from_track('Song','Artist','',source_mode='shazam_tieup'),'Song')
    def test_rekordbox_template_unchanged(self):
        s=self.service({'youtube_search_template':'%comment% %artist%'})
        self.assertEqual(s.create_search_query_from_track('Song','Artist','Anime'),'Anime Artist')
    def test_legacy_config_gets_new_default_without_overwriting_initial(self):
        c=object.__new__(ConfigService); c._load_default_config()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json'
            path.write_text(json.dumps({'youtube_search_template_shazam':'%artist% %tracktitle%',
                                       'youtube_search_template_rekordbox':'custom'}))
            c._config_file=str(path);c.load_config()
            self.assertEqual(c.get('youtube_search_template_shazam'),'%artist% %tracktitle%')
            self.assertEqual(c.get('youtube_search_template_shazam_tieup'),'%tracktitle% %comment%')
            c.save_config({'youtube_search_template_shazam_tieup':'%comment% %tracktitle%'})
            self.assertEqual(json.loads(path.read_text())['youtube_search_template_shazam_tieup'],'%comment% %tracktitle%')
    def test_settings_save_both_fields_and_fallback_for_blanks(self):
        dialog,ns=editor.DialogTests().dialog()
        dialog.youtube_search_template_shazam_edit.setText('%artist% %tracktitle%')
        dialog.youtube_search_template_shazam_tieup_edit.setText('%comment% %tracktitle%')
        dialog.accept()
        saved=dialog.config_service.save_config.call_args.args[0]
        self.assertEqual(saved['youtube_search_template_shazam'],'%artist% %tracktitle%')
        self.assertEqual(saved['youtube_search_template_shazam_tieup'],'%comment% %tracktitle%')
        dialog.youtube_search_template_shazam_tieup_edit.setText(' ')
        dialog.accept()
        self.assertEqual(dialog.config_service.save_config.call_args.args[0]['youtube_search_template_shazam_tieup'],'%tracktitle% %comment%')


class CooperativeAbortTests(unittest.TestCase):
    def thread(self):
        t=YouTubeSearchThread(['a','b'],0,'query',fallback_queries=['fallback'])
        t.requestInterruption=Mock(); t.isRunning=Mock(return_value=True)
        t.quit=Mock();t.wait=Mock();t.terminate=Mock()
        t.search_completed=Mock();t.search_error=Mock()
        return t
    def test_abort_is_nonblocking_and_never_terminates(self):
        t=self.thread();t.abort()
        self.assertTrue(t._is_aborted);t.requestInterruption.assert_called_once()
        t.wait.assert_not_called();t.terminate.assert_not_called();t.quit.assert_not_called()
    def test_cancelled_result_is_not_emitted(self):
        t=self.thread()
        def lookup():t.abort();return videos('initial')
        t._search_with_query_fallbacks=lookup;t.run()
        t.search_completed.emit.assert_not_called()
    def test_cancelled_error_is_not_emitted(self):
        t=self.thread()
        def lookup():t.abort();raise RuntimeError('old')
        t._search_with_query_fallbacks=lookup;t.run()
        t.search_error.emit.assert_not_called()
    def test_cancelled_quota_response_cannot_rotate_shared_api_key(self):
        t=self.thread();t._switch_to_next_key=Mock()
        def lookup(*args):
            t.abort();raise youtube_module.YouTubeQuotaExceededError()
        t._search_youtube=lookup
        self.assertEqual(t._search_with_key_rotation(),[])
        t._switch_to_next_key.assert_not_called()
    def test_cancelled_empty_response_does_not_issue_fallback(self):
        t=self.thread()
        def lookup(*args):t.abort();return []
        t._search_with_key_rotation=Mock(side_effect=lookup)
        self.assertEqual(t._search_with_query_fallbacks(),[])
        self.assertEqual(t._search_with_key_rotation.call_count,1)
    def test_search_abort_before_details_skips_second_http(self):
        t=self.thread();t._filter_shorts=Mock()
        def lookup(*args,**kwargs):t.abort();return {'items':[{'id':{'videoId':'abc'}}]}
        t._request_json=lookup
        self.assertEqual(t._search_youtube(),[])
        t._filter_shorts.assert_not_called()

if __name__=='__main__': unittest.main()


class SettingsTabStructureTests(unittest.TestCase):
    def test_three_separate_fields_inside_resizable_scroll_area(self):
        tree=ast.parse((ROOT/'ui/dialogs/settings_dialog.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='SettingsDialog')
        fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_init_youtube_tab')
        forms=[]; scrolls=[]
        def form(*args):
            x=Mock(); forms.append(x); return x
        def scroll(*args):
            x=Mock(); scrolls.append(x); return x
        names=['QWidget','QHBoxLayout','QLineEdit','QPushButton','QTableWidget','QLabel']
        ns={name:Mock() for name in names}
        ns.update(QFormLayout=form,QScrollArea=scroll,QHeaderView=Mock(),QAbstractItemView=Mock())
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(ROOT/'ui/dialogs/settings_dialog.py'),'exec'),ns)
        win=Mock(); ns['_init_youtube_tab'](win)
        win.tabs.addTab.assert_called_once_with(scrolls[0],'YouTube')
        scrolls[0].setWidgetResizable.assert_called_once_with(True)
        rows=[call.args[1] for call in forms[0].addRow.call_args_list if len(call.args)>1]
        self.assertIn(win.youtube_search_template_rekordbox_edit,rows)
        self.assertIn(win.youtube_search_template_shazam_edit,rows)
        self.assertIn(win.youtube_search_template_shazam_tieup_edit,rows)

class LoggedLookupReplayTests(unittest.TestCase):
    """Replay observed lookup status/comment and vote order, not wall-clock I/O.

    Production candidate, selection, preemption, completion and player feedback
    methods run. External search results/READY are explicit deterministic doubles.
    The 16 lookup records contain 14 confirmed candidates and 2 rejected ones.
    """
    setUp = PriorityWindowTests.setUp
    tearDown = PriorityWindowTests.tearDown
    start = PriorityWindowTests.start
    done = PriorityWindowTests.done
    ready = PriorityWindowTests.ready
    plays = PriorityWindowTests.plays


def logged_case(case):
    def run(self):
        entry = (case['started_at'], case['title'], case['artist'])
        c, initial = self.start(entry=entry, cid=case['candidate_id'])
        job = self.w._tieup_worker.jobs[-1]
        events = [(case['result_at'], 'T')]
        if case.get('confirmed_at'):
            events.append((case['confirmed_at'], 'C'))
        if case.get('initial_result_at'):
            events.append((case['initial_result_at'], 'I'))
        if case.get('rejected_at'):
            events.append((case['rejected_at'], 'X'))
        for _, event in sorted(events):
            if event == 'C':
                self.w.on_shazam_candidate_confirmed(c.id, entry)
            elif event == 'I':
                self.done(initial, 'initial')
            elif event == 'X':
                self.w.on_shazam_candidate_rejected(c.id,case.get('rejected_reason','new-candidate'))
            else:
                fixture.deliver(self.w, job, status=case['status'],
                                comment=case['comment'], cached=case['cached'])
                if case['status'] == 'found' and case['comment']:
                    self.done(self.w.youtube_search_thread, 'tieup')
            if c.tieup_state == 'pending':
                self.assertEqual(self.plays(), [])
        expected = {'enriched':['tieup-1'], 'initial':['initial-1'], 'none':[]}[case['expected_play_source']]
        self.assertEqual(self.plays(), expected, case['title'])
        if case['expected_play_source'] == 'enriched':
            self.assertTrue(initial.aborted)
            self.assertEqual(self.w.left_pane.model.videos[0]['video_id'], 'tieup-1')
    return run

for _index, _case in enumerate(json.loads((ROOT/'tests/fixtures/tieup_priority_runtime.json').read_text())['cases']):
    setattr(LoggedLookupReplayTests, f'test_logged_lookup_{_index:02d}_seq_{_case["candidate_id"]}', logged_case(_case))
