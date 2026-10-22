"""Log parsing is local-only, including apostrophes and reused sequence IDs."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.check_utanet_from_log import extract_tracks, main


class LogToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def save(self, name, text):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        return path
    def vote(self, seq, title='Song', artist='Artist', second=1):
        return f"[2026-10-20 20:00:{second:02}.000] ShazamService: Tentative first vote seq={seq} title={title!r} artist={artist!r}; start YouTube preload, no PLAY\n"
    def confirm(self, seq, second=2):
        return f"[2026-10-20 20:00:{second:02}.000] UI: Shazam second vote confirmed firstSeq={seq} video=- ready=False autoPlay=True\n"
    def test_rotations_reverse_order(self):
        a=self.save('a.log',self.vote(3)); b=self.save('b.log',self.confirm(3))
        self.assertEqual(extract_tracks([b,a])[0]['title'],'Song')
    def test_apostrophes_in_artist(self):
        p=self.save('a.log',self.vote(1,'VOODOO KINGDOM',"SOUL'd OUT")+self.confirm(1))
        self.assertEqual(extract_tracks([p])[0]['artist'],"SOUL'd OUT")
    def test_apostrophes_and_quotes_in_title(self):
        title='It\'s "Time"'
        p=self.save('a.log',self.vote(1,title)+self.confirm(1))
        self.assertEqual(extract_tracks([p])[0]['title'],title)
    def test_unconfirmed_not_included(self):
        self.assertEqual(extract_tracks([self.save('a.log',self.vote(1))]),[])
    def test_missing_vote_not_invented(self):
        self.assertEqual(extract_tracks([self.save('a.log',self.confirm(1))]),[])
    def test_duplicate_files_and_confirmations(self):
        p=self.save('a.log',self.vote(1)+self.confirm(1)+self.confirm(1,3))
        self.assertEqual(len(extract_tracks([p,p])),1)
    def test_restart_reuses_seq(self):
        text=self.vote(1)+self.confirm(1)
        text+='[2026-10-20 20:00:03.000] ShazamService: Started (laneCount=6)\n'
        text+=self.vote(1,'Other',second=4)+self.confirm(1,5)
        self.assertEqual([x['title'] for x in extract_tracks([self.save('a.log',text)])],['Song','Other'])
    def test_metadata_may_finish_after_confirmation(self):
        p=self.save('a.log',self.confirm(1,1)+self.vote(1,second=2))
        self.assertEqual(len(extract_tracks([p])),1)
    def test_default_makes_no_network_requests(self):
        p=self.save('a.log',self.vote(1)+self.confirm(1)); out=self.root/'out.json'
        with patch('tools.check_utanet_from_log.UtanetClient') as client, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(p),'--out',str(out)]),0)
            client.assert_not_called()
        self.assertEqual(json.loads(out.read_text())['results'][0]['status'],'not_run')
    def test_live_requires_explicit_permission(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as raised:
            main(['log.txt','--live'])
        self.assertEqual(raised.exception.code,2)


if __name__=='__main__':
    unittest.main()
