"""Source-contract regressions for the WebView2 route-ID-only fast return.

The Windows C# bridge cannot be executed in the Linux test environment. These
checks assert the exact safety guards remain in BridgeHost.cs; the app's Python
and JavaScript behavior is checked by their separate runtime test suites.
"""
from pathlib import Path
import re
import unittest

SRC = Path(__file__).resolve().parents[1] / 'native' / 'ShazamWebViewBridge' / 'BridgeHost.cs'


class RouteIdentityFastReturnContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SRC.read_text(encoding='utf-8')
        cls.capture = cls.source.split('private async Task<Candidate?> CaptureRouteEvidenceAsync(', 1)[1].split(
            'private bool IsHomeUrl(', 1)[0]
        cls.fast_path = cls.capture.split('// Fast ID-only path:', 1)[1].split('await Task.Delay(75, token);', 1)[0]

    def test_grace_is_900ms_while_original_fallback_stays_4s(self):
        self.assertIn('RouteEvidenceWindow = TimeSpan.FromSeconds(4)', self.source)
        self.assertIn('VerifiedRouteIdentityGrace = TimeSpan.FromMilliseconds(900)', self.source)
        self.assertIn('while (deadline.Elapsed < RouteEvidenceWindow)', self.capture)

    def test_fast_return_needs_this_requests_live_audio(self):
        self.assertIn('_audioStarted && !_definiteNoMatch', self.fast_path)

    def test_fast_return_needs_full_grace(self):
        self.assertIn('deadline.Elapsed >= VerifiedRouteIdentityGrace', self.fast_path)

    def test_fast_return_reads_committed_live_url_not_navigation_event(self):
        self.assertIn('TryParseShazamRoute(_webView.CoreWebView2.Source, out var liveRouteId)', self.fast_path)
        self.assertNotIn('args.Uri', self.fast_path)

    def test_fast_return_requires_id_equal_original_and_latest_candidate(self):
        self.assertIn('string.Equals(liveRouteId, routeCandidate.ShazamTrackId, StringComparison.Ordinal)', self.fast_path)
        self.assertIn('liveRouteBest is not null', self.fast_path)
        self.assertIn('string.Equals(liveRouteBest.ShazamTrackId, liveRouteId, StringComparison.Ordinal)', self.fast_path)

    def test_fast_return_rejects_song_track_namespace_change(self):
        self.assertIn('SameShazamRouteKind(_webView.CoreWebView2.Source, routeCandidate.Url)', self.fast_path)
        helper = self.source.split('private static bool SameShazamRouteKind(', 1)[1].split(
            'private void OnWebMessage(', 1)[0]
        self.assertIn('(song|track)', helper)
        self.assertIn('candidateKind.Length > 0', helper)
        self.assertIn('string.Equals(Kind(currentUrl), candidateKind, StringComparison.Ordinal)', helper)

    def test_fast_return_keeps_strongest_available_metadata(self):
        self.assertIn('var result = Better(best, liveRouteBest);', self.fast_path)
        self.assertLess(self.capture.index('if (!string.IsNullOrWhiteSpace(candidate.AppleTrackId)'),
                        self.capture.index('// Fast ID-only path:'))

    def test_default_path_still_collects_dom_until_complete_or_timeout(self):
        self.assertIn('ExecuteScriptAsync(script)', self.capture)
        self.assertIn('await Task.Delay(75, token);', self.capture)
        self.assertIn('return best;', self.capture)
        self.assertNotIn('RouteEvidenceWindow = TimeSpan.FromMilliseconds(900)', self.source)

    def test_path_is_exact_shazam_song_track_not_hompage_or_third_party(self):
        parse = self.source.split('private static bool TryParseShazamRoute(', 1)[1].split(
            'private void OnWebMessage(', 1)[0]
        self.assertIn('!IsShazam(url)', parse)
        self.assertIn('(?:song|track)', parse)
        self.assertIn('\\d{6,20}', parse)
        self.assertIn('if (!m.Success) return false', parse)
        self.assertIn('uri.AbsolutePath', parse)

    def test_production_observability_marks_fast_path(self):
        self.assertIn('Verified route ID-only early return id=', self.fast_path)
        self.assertIn('elapsedMs=', self.fast_path)

    def test_generic_network_result_and_html_result_fast_paths_remain(self):
        self.assertIn('Network/result-observer evidence can arrive', self.capture)
        self.assertIn('(!string.IsNullOrWhiteSpace(liveBest.Title)', self.capture)
        self.assertIn('(!string.IsNullOrWhiteSpace(candidate.Title)', self.capture)


if __name__ == '__main__':
    unittest.main()
