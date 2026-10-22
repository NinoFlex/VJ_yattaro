"""Real log strings with synthetic HTTP fixtures, NOT live site verification."""
import contextlib
import html
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from app.services.utanet_service import base_title
from tests.test_utanet_service import client_for, search_html, song_html
from tests.test_tieup_enrichment import EnrichmentHarness, install

DATA=Path(__file__).resolve().parent/'fixtures'/'utanet'
TRACKS=json.loads((DATA/'log_confirmed_tracks.json').read_text(encoding='utf-8'))


class RealLogInputFixtureTests(unittest.TestCase):
    def test_exact_89_confirmed_inputs_with_apostrophe_artist(self):
        self.assertEqual(len(TRACKS),89)
        self.assertEqual(next(row for row in TRACKS if row['seq']==645)['artist'],"SOUL'd OUT")
    def check_flow(self,row,title,artist,comment,url):
        client,_=client_for(search_html([(title,artist,url,'')]),
                            song_html(title,artist,html.escape(comment),url))
        result=client.lookup(row['title'],row['artist'])
        self.assertEqual(result.status,'found')
        self.assertEqual(result.comment,comment)
        win=install(EnrichmentHarness())
        win._sync_pending_search_args=Mock();win.search_youtube=Mock()
        entry=(row.get('time','2026-10-20 20:00:00'),row['title'],row['artist'])
        candidate=win.shazam_flow.begin_vote(row.get('seq',1),entry)
        with contextlib.redirect_stdout(io.StringIO()):
            win._request_tieup(entry,candidate)
            win._on_tieup_result(win._tieup_worker.jobs[-1].token,result)
        self.assertEqual(candidate.track_info['comment'],comment)
        self.assertFalse(candidate.play_sent)
        self.assertFalse(win.shazam_service.get_history())
        win.search_youtube.assert_called_once_with(row['title'],row['artist'],comment,
            from_list=True,allow_auto_play=False,source_mode='shazam',
            shazam_candidate_id=candidate.id,_metadata_refresh=True)
    def test_89_inputs_through_real_parser_comment_update_and_requery(self):
        for row in TRACKS:
            with self.subTest(seq=row['seq'],title=row['title']):
                self.check_flow(row,base_title(row['title']),row['artist'],
                    'SYNTHETIC TEST ONLY \u30a8\u30f3\u30c7\u30a3\u30f3\u30b0',
                    'https://www.uta-net.com/song/'+str(9000000+row['seq'])+'/')
    def test_89_inputs_reject_wrong_artist_and_keep_missing_comment_blank(self):
        for row in TRACKS:
            with self.subTest(seq=row['seq']):
                client,_=client_for(search_html([]),search_html([]))
                self.assertEqual(client.lookup(row['title'],row['artist']).comment,'')
                fixture=search_html([(base_title(row['title']),'000 Wrong Artist','/song/1/','WRONG \u4e3b\u984c\u6b4c')])
                client,_=client_for(fixture,fixture)
                self.assertEqual(client.lookup(row['title'],row['artist']).comment,'')
    def test_5_public_metadata_values_in_reconstructed_html_not_actual_site_dom(self):
        cases=json.loads((DATA/'public_metadata_examples.json').read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(title=case['title']):
                row={'title':case['input_title'],'artist':case['input_artist'],'seq':case.get('seq',1)}
                self.check_flow(row,case['title'],case['artist'],case['comment'],case['url'])


if __name__=='__main__':unittest.main()
