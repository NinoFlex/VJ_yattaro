"""Literal filtering, settings persistence and real service/UI event regressions.

Qt, audio and external services are mocked. No song-specific lookup tables are
used by the implementation: names below are fixtures only.
"""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from app.services.title_keywords import (
    CONFIG_KEY, MAX_KEYWORDS, normalize_keywords, strip_title_keywords,
    filter_history_rows, title_variant_matches,
)
from app.services.config_service import ConfigService
from tests import test_shazam_speculative_preload as votes
from tests.test_latency_prefetch import raw_song
from tests import test_search_templates as search_tests
from tests.test_autoplay_selection import _methods

WORDS = ['(Insturumental)', '-Insturumental-', '\uff08insturumental\uff09', '\uff08TV\u30b5\u30a4\u30ba\uff09']


class FilteringTests(unittest.TestCase):
    def test_all_four_user_examples(self):
        for word in WORDS:
            with self.subTest(word=word):
                self.assertEqual(strip_title_keywords('\u66f2\u540d'+word, WORDS), '\u66f2\u540d')
    def test_case_insensitive(self):
        self.assertEqual(strip_title_keywords('Song (INSTRUMENTAL)', ['(Instrumental)']), 'Song')
    def test_bracket_width_is_literal(self):
        self.assertEqual(strip_title_keywords('Song\uff08Instrumental\uff09', ['(Instrumental)']), 'Song\uff08Instrumental\uff09')
    def test_not_a_regex(self):
        self.assertEqual(strip_title_keywords('abc .* end', ['.*']), 'abc end')
        self.assertEqual(strip_title_keywords('abc song end', ['.*']), 'abc song end')
    def test_prefix_middle_suffix_all_occurrences(self):
        self.assertEqual(strip_title_keywords('(x)First(x) Second(x)', ['(x)']), 'First Second')
    def test_longest_match_order_independent(self):
        for words in (['x','(x)'], ['(x)','x']):
            self.assertEqual(strip_title_keywords('Title (x)', words), 'Title')
    def test_empty_or_invalid_rules_preserve_exact_original(self):
        for rules in ([], None, {}, 'Song', [1, None]):
            self.assertEqual(strip_title_keywords('  Song  Name  ', rules), '  Song  Name  ')
    def test_nonmatching_rules_do_not_rewrite_whitespace(self):
        self.assertEqual(strip_title_keywords(' A  B ', ['(x)']), ' A  B ')
    def test_whitespace_collapse_does_not_break_idempotence(self):
        result=strip_title_keywords('Fooxx a  b', ['xx', 'a b'])
        self.assertEqual(result,'Foo')
        self.assertEqual(strip_title_keywords(result, ['xx', 'a b']),result)
    def test_empty_result_retains_original(self):
        self.assertEqual(strip_title_keywords('(x)', ['(x)']), '(x)')
    def test_no_unregistered_spelling_correction(self):
        self.assertEqual(strip_title_keywords('Song (Instrumental)', ['(Insturumental)']), 'Song (Instrumental)')
    def test_idempotent_after_adjacent_removal(self):
        s='aXb Real  Title'; words=['X','ab']
        result=strip_title_keywords(s,words)
        self.assertEqual(result,'Real Title')
        self.assertEqual(strip_title_keywords(result,words),result)
    def test_repeated_removal_and_empty_fallback_are_idempotent(self):
        for title in ['aXb','(x)Title(x)','A (x) B', 'Title']:
            for words in [['X','ab'],['(x)'],[]]:
                r=strip_title_keywords(title,words)
                self.assertEqual(strip_title_keywords(r,words),r)
    def test_normalize_limits_to_50_unique_strings(self):
        result=normalize_keywords([None,'',' ','foo','FOO','bar\nline']+[f'({i})' for i in range(80)])
        self.assertEqual(len(result),50)
        self.assertEqual(result[0],'foo')
        self.assertEqual(result[-1],'(48)')
    def test_normalize_is_copy(self):
        x=['a','b']; out=normalize_keywords(x);out.append('c');self.assertEqual(x,['a','b'])
    def test_rows_preserve_artist_comment_timestamp_and_input(self):
        rows=[('Song(x)','Artist(x)','Comment(x)','id')]
        self.assertEqual(filter_history_rows(rows,['(x)']), [('Song','Artist(x)','Comment(x)','id')])
        self.assertEqual(rows,[('Song(x)','Artist(x)','Comment(x)','id')])
        self.assertEqual(filter_history_rows([('time','Song(x)','Artist')],['(x)'],title_index=1), [('time','Song','Artist')])
    def test_prefix_variant_both_directions(self):
        for first,second in [('Short','Short\uff08Insturumental\uff09'), ('Full Title','Full Title -Live-')]:
            self.assertTrue(title_variant_matches((first,'A'),(second,'A')))
            self.assertTrue(title_variant_matches((second,'A'),(first,'A')))
    def test_common_fragment_of_distinct_known_titles_is_not_a_variant(self):
        self.assertFalse(title_variant_matches(('Song A','Artist'),('Song B','Artist')))
        self.assertFalse(title_variant_matches(('Blue','Artist'),('Bluebird','Artist')))
    def test_different_known_artists_are_not_unconditionally_merged(self):
        self.assertFalse(title_variant_matches(('Song','A'),('Song (x)','B')))
    def test_keyword_removal_can_equate_two_nonprefix_versions(self):
        self.assertTrue(title_variant_matches(('Title'+WORDS[0],'A'),('Title'+WORDS[3],'A'),WORDS))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.config=object.__new__(ConfigService)
        self.config._config_file=str(Path(self.tmp.name)/'config.json')
        self.config._load_default_config()
    def tearDown(self): self.tmp.cleanup()
    def test_default_is_empty(self): self.assertEqual(self.config.get(CONFIG_KEY),[])
    def test_save_reload_50_keywords_and_other_settings(self):
        original={'keys':['private-local-key'],'active_index':0}
        self.assertTrue(self.config.save_config({CONFIG_KEY:WORDS, 'youtube_api_keys':original,'shazam_lane_count':8}))
        self.config._load_default_config();self.config.load_config()
        self.assertEqual(self.config.get(CONFIG_KEY),WORDS)
        self.assertEqual(self.config.get('youtube_api_keys'),original)
        self.assertEqual(self.config.get('shazam_lane_count'),8)
        self.assertIn('\uff08TV\u30b5\u30a4\u30ba\uff09',Path(self.config._config_file).read_text())
    def test_older_json_without_key_keeps_settings(self):
        Path(self.config._config_file).write_text(json.dumps({'shazam_lane_count':8,'youtube_search_template_rekordbox':'%tracktitle%'}))
        self.config.load_config()
        self.assertEqual(self.config.get(CONFIG_KEY),[])
        self.assertEqual(self.config.get('shazam_lane_count'),8)
    def test_manually_edited_json_is_bounded_and_sanitized(self):
        self.config.save_config({CONFIG_KEY:[f'word{i}' for i in range(55)]+['']})
        self.assertEqual(len(self.config.get(CONFIG_KEY)),50)
    def test_malformed_value_is_not_used_as_regex(self):
        self.config.save_config({CONFIG_KEY:'.*'})
        self.assertEqual(self.config.get(CONFIG_KEY),[])
    def test_save_error_does_not_activate_unsaved_rules(self):
        self.config.save_config({CONFIG_KEY:['old']})
        with patch('builtins.open',side_effect=PermissionError('read-only')):
            self.assertFalse(self.config.save_config({CONFIG_KEY:['new']}))
        self.assertEqual(self.config.get(CONFIG_KEY),['old'])


class ServiceTests(unittest.TestCase):
    setUp = votes.ServiceVoteTests.setUp
    tearDown = votes.ServiceVoteTests.tearDown
    vote = votes.ServiceVoteTests.vote
    no_match = votes.ServiceVoteTests.no_match

    def configure(self,words=WORDS):
        values={CONFIG_KEY:list(words)}
        self.svc.config=types.SimpleNamespace(get=lambda k,d=None:values.get(k,d))
        return values
    def track(self,title,number='55555555',artist='Fixture Artist'):
        return raw_song(number,title=title,artist=artist)
    def starts(self):return [x[1][1] for x in self.svc.candidate_started.calls]
    def test_tentative_confirmed_history_share_clean_title(self):
        self.configure();raw=self.track('My Title'+WORDS[0]);self.vote(0,raw)
        self.assertEqual(self.starts(),['My Title'])
        self.assertEqual(self.svc._tentative_candidate['observation']['track'][0],raw['title'])
        self.vote(1,raw)
        self.assertEqual(self.svc.candidate_confirmed.calls[0][1][1],'My Title')
        self.assertEqual(self.svc.get_history()[0][1],'My Title')
        saved=json.loads((Path(self.tmp.name)/'history.json').read_text())
        self.assertEqual(saved[0]['title'],'My Title')
        self.assertEqual(raw['title'],'My Title'+WORDS[0])
    def test_evidence_and_broker_cache_are_not_filtered(self):
        self.configure();raw=self.track('My Title'+WORDS[3]);self.vote(0,raw)
        cached=self.broker.get(self.svc._metadata_key(('shazam-song','55555555')))
        self.assertEqual(cached,(raw['title'],raw['artist']))
    def test_same_id_title_variant_never_researches(self):
        self.configure();self.vote(0,self.track('My Title'));self.vote(1,self.track('My Title'+WORDS[0]))
        self.vote(2,self.track('My Title'+WORDS[3]));self.vote(3,self.track('My Title'))
        self.assertEqual(self.starts(),['My Title'])
        self.assertEqual(len(self.svc.new_track_detected.calls),1)
    def test_different_id_suffix_keeps_tentative_owner_not_identity_vote(self):
        self.configure();self.vote(0,self.track('My Title'+WORDS[0]))
        self.vote(1,self.track('My Title'+WORDS[3],'66666666'))
        self.assertEqual(self.starts(),['My Title'])
        self.assertEqual(self.svc.candidate_rejected.calls,[])
        self.assertEqual(self.svc.candidate_confirmed.calls,[],'different IDs are NOT two matching votes')
        self.vote(2,self.track('My Title'+WORDS[3],'66666666'))
        self.assertEqual(len(self.svc.candidate_confirmed.calls),1)
        self.assertEqual(self.svc.candidate_confirmed.calls[0][0],0)
        self.assertEqual(len(self.svc.get_history()),1)
    def test_prefix_variant_unconfigured_no_duplicate_preview(self):
        self.configure([]);self.vote(0,self.track('My Title'))
        self.vote(1,self.track('My Title (Instrumental)','66666666'))
        self.vote(2,self.track('My Title (Instrumental)','66666666'))
        self.assertEqual(self.starts(),['My Title'])
        self.assertEqual(len(self.svc.candidate_confirmed.calls),1)
    def test_confirmed_prefix_variant_is_not_researched(self):
        self.configure();self.vote(0,self.track('My Title'));self.vote(1,self.track('My Title'))
        self.vote(2,self.track('My Title'+WORDS[0],'66666666'));self.vote(3,self.track('My Title'+WORDS[0],'66666666'))
        self.assertEqual(self.starts(),['My Title']);self.assertEqual(len(self.svc.get_history()),1)
        self.assertEqual(len(self.svc.new_track_detected.calls),1)
    def test_real_different_song_still_replaces_and_can_return(self):
        self.configure()
        for seq,track in enumerate([self.track('My Title'),self.track('My Title'),
                                   self.track('Other Tune','66666666'),self.track('Other Tune','66666666'),
                                   self.track('My Title'+WORDS[0]),self.track('My Title'+WORDS[0])]):self.vote(seq,track)
        self.assertEqual(self.starts(),['My Title','Other Tune','My Title'])
        self.assertEqual(len(self.svc.get_history()),3)
    def test_equal_title_reuses_presentation_but_not_an_identity_vote(self):
        self.configure([]);self.vote(0,self.track('My Title'))
        self.vote(1,self.track('My Title','66666666'))
        self.assertEqual(len(self.svc.candidate_started.calls),1)
        self.assertEqual(len(self.svc.candidate_confirmed.calls),0)
        self.vote(2,self.track('My Title','66666666'))
        self.assertEqual(len(self.svc.candidate_confirmed.calls),1)
        self.assertEqual(len(self.svc.get_history()),1)
    def test_different_artist_same_four_title_chars_reuses_preview(self):
        self.configure();self.vote(0,self.track('My Title'))
        self.vote(1,self.track('My Title'+WORDS[0],'66666666','Other Artist'))
        self.assertEqual(self.starts(),['My Title'])
        self.assertFalse(self.svc.candidate_rejected.calls)
        self.assertFalse(self.svc.candidate_confirmed.calls, 'different IDs must not confirm each other')
        self.vote(2,self.track('My Title'+WORDS[0],'66666666','Other Artist'))
        self.assertEqual(len(self.svc.candidate_confirmed.calls),1)
        self.assertEqual(len(self.svc.get_history()),1)

    def test_gedo_different_shazam_ids_and_artists_reuse_preview_and_history(self):
        self.configure([])
        a=self.track('GEDO (feat.鏡音レン)','1691477743','daraku')
        b=self.track('GEDO','1694678766','daraku & 鏡音レン')
        self.vote(0,a)
        self.vote(1,b)
        self.assertEqual(self.starts(),['GEDO (feat.鏡音レン)'])
        self.assertFalse(self.svc.candidate_rejected.calls)
        self.assertFalse(self.svc.candidate_confirmed.calls,'cross-ID votes cannot confirm each other')
        self.vote(2,b)
        self.assertEqual(len(self.svc.candidate_confirmed.calls),1)
        self.assertEqual(len(self.svc.new_track_detected.calls),1)
        self.assertEqual(len(self.svc.get_history()),1)
        self.vote(3,a)
        self.vote(4,a)
        self.assertEqual(self.starts(),['GEDO (feat.鏡音レン)'])
        self.assertEqual(len(self.svc.new_track_detected.calls),1)
        self.assertEqual(len(self.svc.get_history()),1)

    def test_rinkaidaiba_different_shazam_ids_and_artists_are_one_presentation(self):
        self.configure([])
        cover=self.track('臨界ダイバー','818320401','メガテラ・ゼロ')
        original=self.track('臨界ダイバー','1543504171','うみろ')
        self.vote(0,cover)
        self.vote(1,original)
        self.assertEqual(self.starts(),['臨界ダイバー'])
        self.assertFalse(self.svc.candidate_confirmed.calls)
        self.vote(2,original)
        self.assertEqual(len(self.svc.candidate_confirmed.calls),1)
        self.assertEqual(len(self.svc.get_history()),1)
        self.vote(3,cover)
        self.vote(4,cover)
        self.assertEqual(len(self.svc.candidate_started.calls),1)
        self.assertEqual(len(self.svc.new_track_detected.calls),1)
        self.assertEqual(len(self.svc.get_history()),1)

    def test_different_known_ids_without_four_character_prefix_still_research(self):
        self.configure([])
        a=self.track('GEDO','1691477743','daraku')
        b=self.track('Other Tune','1694678766','daraku')
        self.vote(0,a)
        self.vote(1,b)
        self.assertEqual(self.starts(),['GEDO','Other Tune'])
        self.assertTrue(self.svc.candidate_rejected.calls)
        self.assertFalse(self.svc.candidate_confirmed.calls)
        self.vote(2,b)
        self.assertEqual(len(self.svc.candidate_confirmed.calls),1)
    def test_empty_after_removal_does_not_make_no_match(self):
        self.configure(['My Title']);self.vote(0,self.track('My Title'));self.vote(1,self.track('My Title'))
        self.assertEqual(self.starts(),['My Title']);self.assertEqual(len(self.svc.get_history()),1)
    def test_id_only_delayed_metadata_gets_same_filter(self):
        self.configure();raw=raw_song('55555555');self.vote(0,raw);self.vote(1,raw)
        self.assertEqual(self.starts(),[])
        key=self.svc._metadata_key(('shazam-song','55555555'))
        self.broker.finish(1,key,('My Title'+WORDS[3],'Fixture Artist'))
        self.svc._handle_prefetch_finished(1,key,('My Title'+WORDS[3],'Fixture Artist'),'')
        self.assertEqual(self.starts(),['My Title']);self.assertEqual(len(self.svc.candidate_confirmed.calls),1)
        self.assertEqual(self.svc.get_history()[0][1],'My Title')
    def test_late_repeated_metadata_does_not_research(self):
        self.configure();raw=self.track('My Title'+WORDS[0]);self.vote(0,raw);self.vote(1,raw)
        key=self.svc._metadata_key(('shazam-song','55555555'))
        for _ in range(5):self.svc._handle_prefetch_finished(1,key,(raw['title'],raw['artist']),'')
        self.assertEqual(len(self.svc.candidate_started.calls),1)
    def test_out_of_order_matching_variant_pair_preserves_preload(self):
        self.configure();self.vote(0,self.track('My Title'))
        self.vote(3,self.track('My Title'+WORDS[3],'66666666'))
        self.vote(2,self.track('My Title'+WORDS[3],'66666666'))
        self.assertEqual(self.starts(),['My Title'])
        self.assertEqual(self.svc.candidate_rejected.calls,[])
        self.assertEqual(self.svc.candidate_confirmed.calls[0][0],0)
    def test_expired_preview_not_reused(self):
        self.configure();self.vote(0,self.track('My Title'));self.now+=40
        self.svc._expire_tentative_candidate()
        self.vote(1,self.track('My Title'+WORDS[3],'66666666'))
        self.assertEqual(len(self.svc.candidate_started.calls),2)
    def test_keyword_only_setting_does_not_restart(self):
        values=self.configure([]);self.svc._recognition_settings=self.svc._recognition_settings_key()
        values[CONFIG_KEY]=WORDS
        with patch.object(self.svc,'stop') as stop,patch.object(self.svc,'start') as start:
            self.svc.reload_settings();stop.assert_not_called();start.assert_not_called()
    def test_lane_setting_still_restarts(self):
        values=self.configure([]);self.svc._recognition_settings=self.svc._recognition_settings_key();values['shazam_lane_count']=8
        with patch.object(self.svc,'stop') as stop,patch.object(self.svc,'start') as start:
            self.svc.reload_settings();stop.assert_called_once();start.assert_called_once()
    def test_prefetch_early_hint_cannot_start_search(self):
        self.configure();self.broker.seed(self.svc._metadata_key(('shazam-song','55555555')),'My Title'+WORDS[0],'Fixture Artist')
        self.assertEqual(self.starts(),[])
    def test_all_settings_are_generic_not_song_specific(self):
        self.configure(['ZZ_REMOVE'])
        self.vote(0,self.track('ArbitraryZZ_REMOVEName'));self.vote(1,self.track('ArbitraryZZ_REMOVEName'))
        self.assertEqual(self.svc.get_history()[0][1],'ArbitraryName')
    def test_real_service_window_preserves_one_search_preload_play(self):
        self.configure();w=votes.WindowVoteTests().window();w.shazam_service=self.svc
        def search(*args,**kw):w._active_search_candidate_id=kw.get('shazam_candidate_id')
        w.search_youtube.side_effect=search
        for signal,slot in [(self.svc.candidate_started,w.on_shazam_candidate_started),
                            (self.svc.candidate_confirmed,w.on_shazam_candidate_confirmed),
                            (self.svc.candidate_rejected,w.on_shazam_candidate_rejected),
                            (self.svc.history_updated,w.on_shazam_history_updated),
                            (self.svc.new_track_detected,w.on_shazam_new_track_detected)]:signal.connect(slot)
        self.vote(0,self.track('My Title'+WORDS[0]));original_id=w._shazam_preview.id
        votes._complete(w,votes._videos('3:00','3:00'))
        w._handle_player_feedback({'state':'ready','videoId':'video-0'})
        self.vote(1,self.track('My Title'+WORDS[3],'66666666'))
        self.assertEqual(w._shazam_preview.id,original_id)
        self.assertEqual([c.args[0] for c in w._send_video_command.call_args_list],['PRELOAD'])
        self.vote(2,self.track('My Title'+WORDS[3],'66666666'))
        self.assertEqual(w.search_youtube.call_count,1)
        self.assertEqual(w.search_youtube.call_args.args[:2],('My Title','Fixture Artist'))
        self.assertEqual([c.args[0] for c in w._send_video_command.call_args_list],['PRELOAD','PLAY'])
        self.assertEqual(w.shazam_table_model.data[0][1],'My Title')


    def test_gedo_cross_id_window_one_search_preload_and_play(self):
        self.configure([])
        w=votes.WindowVoteTests().window();w.shazam_service=self.svc
        def search(*args,**kwargs):
            w._active_search_candidate_id=kwargs.get('shazam_candidate_id')
        w.search_youtube.side_effect=search
        for signal,slot in [(self.svc.candidate_started,w.on_shazam_candidate_started),
                            (self.svc.candidate_confirmed,w.on_shazam_candidate_confirmed),
                            (self.svc.candidate_rejected,w.on_shazam_candidate_rejected),
                            (self.svc.history_updated,w.on_shazam_history_updated),
                            (self.svc.new_track_detected,w.on_shazam_new_track_detected)]:
            signal.connect(slot)
        a=self.track('GEDO (feat.鏡音レン)','1691477743','daraku')
        b=self.track('GEDO','1694678766','daraku & 鏡音レン')
        self.vote(0,a)
        first_preview_id=w._shazam_preview.id
        votes._complete(w,votes._videos('3:00','3:00'))
        w._handle_player_feedback({'state':'ready','videoId':'video-0'})
        self.vote(1,b)
        self.assertEqual(w._shazam_preview.id,first_preview_id)
        self.assertEqual(w.search_youtube.call_count,1)
        self.assertEqual([c.args[0] for c in w._send_video_command.call_args_list],['PRELOAD'])
        self.assertFalse(self.svc.candidate_confirmed.calls)
        self.vote(2,b)
        self.assertEqual([c.args[0] for c in w._send_video_command.call_args_list],['PRELOAD','PLAY'])
        self.vote(3,a);self.vote(4,a)
        self.assertEqual(w.search_youtube.call_count,1)
        self.assertEqual([c.args[0] for c in w._send_video_command.call_args_list],['PRELOAD','PLAY'])
        self.assertEqual(len(self.svc.get_history()),1)

    def finish_metadata(self, number, title, artist='Fixture Artist'):
        key=self.svc._metadata_key(('shazam-song',number))
        self.broker.finish(1,key,(title,artist))
        self.svc._handle_prefetch_finished(1,key,(title,artist),'')
    def test_delayed_variant_metadata_preserves_old_preview_before_second_vote(self):
        self.configure();self.vote(0,self.track('My Title'))
        self.vote(1,raw_song('66666666'))
        self.assertEqual(self.svc.candidate_rejected.calls,[])
        self.finish_metadata('66666666','My Title'+WORDS[0])
        self.assertEqual(self.starts(),['My Title'])
        self.assertFalse(self.svc.candidate_confirmed.calls)
        self.vote(2,raw_song('66666666'))
        self.assertEqual(self.svc.candidate_confirmed.calls[0][0],0)
        self.assertEqual(self.svc.get_history()[0][1],'My Title')
    def test_delayed_variant_metadata_after_two_votes_emits_one_confirmation(self):
        self.configure();self.vote(0,self.track('My Title'))
        self.vote(1,raw_song('66666666'));self.vote(2,raw_song('66666666'))
        self.assertEqual(self.starts(),['My Title'])
        self.assertFalse(self.svc.candidate_confirmed.calls)
        self.finish_metadata('66666666','My Title'+WORDS[3])
        self.assertEqual(self.starts(),['My Title'])
        self.assertEqual(self.svc.candidate_confirmed.calls[0][0],0)
        self.assertEqual(len(self.svc.get_history()),1)
        self.assertFalse(self.svc._tentative_timer.running)
    def test_delayed_real_other_song_replaces_old_preview_only_once(self):
        self.configure();self.vote(0,self.track('My Title'))
        self.vote(1,raw_song('66666666'));self.vote(2,raw_song('66666666'))
        self.finish_metadata('66666666','Other Tune'+WORDS[3])
        self.assertEqual(self.starts(),['My Title','Other Tune'])
        self.assertEqual(self.svc.candidate_rejected.calls,[(0,'resolved-different-title')])
        self.assertEqual(self.svc.candidate_confirmed.calls[0][0],1)
        self.assertEqual(self.svc.get_history()[0][1],'Other Tune')
    def test_delayed_unknown_metadata_expires_even_after_identity_confirmation(self):
        self.configure();self.vote(0,self.track('My Title'))
        self.vote(1,raw_song('66666666'));self.vote(2,raw_song('66666666'))
        self.now+=31;self.svc._expire_tentative_candidate()
        self.assertEqual(self.svc.candidate_rejected.calls[0][0],0)
        self.assertFalse(self.svc.candidate_confirmed.calls)
    def test_delayed_old_metadata_cannot_revive_after_distinct_song(self):
        self.configure();self.vote(0,self.track('My Title'));self.vote(1,raw_song('66666666'))
        self.vote(2,self.track('Other Tune','77777777'));self.vote(3,self.track('Other Tune','77777777'))
        before=len(self.svc.candidate_started.calls)
        self.finish_metadata('66666666','My Title'+WORDS[0])
        self.assertEqual(len(self.svc.candidate_started.calls),before)
        self.assertEqual(self.svc.get_history()[0][1],'Other Tune')
    def test_stop_removes_displayed_preview_when_replacement_unresolved(self):
        self.configure();self.vote(0,self.track('My Title'));self.vote(1,raw_song('66666666'))
        self.svc.stop()
        self.assertEqual(self.svc.candidate_rejected.calls[-1][0],0)
    def test_keyword_edit_keeps_inflight_search_title_snapshot(self):
        values=self.configure([]);raw=self.track('My Title'+WORDS[0]);self.vote(0,raw)
        values[CONFIG_KEY]=WORDS;self.svc.reload_settings();self.vote(1,raw)
        self.assertEqual(self.svc.get_history()[0][1],raw['title'])
        self.assertEqual(len(self.svc.candidate_started.calls),1)
    def test_idless_legacy_four_character_prefix_rule_is_retained(self):
        self.configure([])
        self.svc._stage_temporal_result(0,0,'ABCD Original','First artist')
        self.svc._stage_temporal_result(1,1,'ABCD Cover','Other artist')
        self.assertEqual(len(self.svc.get_history()),1)
    def test_idless_matching_applies_removal_before_comparison(self):
        self.configure(['(x)'])
        self.svc._stage_temporal_result(0,0,'(x)My Title','Artist')
        self.svc._stage_temporal_result(1,1,'My Title','Artist')
        self.assertEqual(self.svc.get_history()[0][1],'My Title')


class MainWindowTests(unittest.TestCase):
    def test_rekordbox_list_and_autosearch_use_clean_title(self):
        method=_methods('MainWindow',['on_history_updated'])['on_history_updated']
        rows=[]
        model=types.SimpleNamespace(_data=[],update_data=lambda r:rows.append(r) or True,rowCount=lambda:len(rows[-1]))
        w=types.SimpleNamespace(source_mode='rekordbox',config_service=types.SimpleNamespace(get=lambda k,d=None:WORDS),
            right_table=Mock(selectionModel=Mock(return_value=None)),rekordbox_table_model=model,
            youtube_search_box=Mock(text=Mock(return_value='')),search_youtube=Mock())
        method(w,[('Previous','A','')]);method(w,[('New Song'+WORDS[0],'B','Comment'+WORDS[0])])
        self.assertEqual(rows[-1],[('New Song','B','Comment'+WORDS[0])])
        w.search_youtube.assert_called_once_with('New Song','B','Comment'+WORDS[0],from_list=True)
        method(w,[('New Song'+WORDS[3],'B','Comment'+WORDS[0])])
        self.assertEqual(w.search_youtube.call_count,1)
    def test_shazam_and_rekordbox_list_search_filter_only_title(self):
        harness=search_tests.SearchFallbackWiringTests()
        for source in ['shazam','rekordbox']:
            w,service,thread=harness._run_main('A Tune'+WORDS[0],'Artist',True,{CONFIG_KEY:WORDS},source)
            self.assertEqual(w._current_track_info['title'],'A Tune')
            self.assertEqual(w._current_track_info['artist'],'Artist')
            thread.start.assert_called_once()
    def test_manual_query_not_filtered(self):
        w,service,thread=search_tests.SearchFallbackWiringTests()._run_main('A Tune'+WORDS[0],'',False,{CONFIG_KEY:WORDS})
        query=service.search_videos.call_args.args[0]
        self.assertIn('Insturumental',query)


if __name__=='__main__':unittest.main()
