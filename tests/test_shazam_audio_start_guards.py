"""Source contract checks for native orchestration (NOT a C# execution test).

Real DOM, trusted input, audio bridge and race tests live in audio_start_checks.py.
Windows/.NET compilation and end-to-end Shazam remain separate acceptance gates.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'native/ShazamWebViewBridge'


class NativeAudioStartContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (NATIVE / 'BridgeHost.cs').read_text(encoding='utf-8')
        cls.guard = (NATIVE / 'Scripts/audio_retry.js').read_text(encoding='utf-8')

    def method(self, start, end):
        return self.source.split(start, 1)[1].split(end, 1)[0]

    def test_first_click_clock_is_started_once_and_not_reset_on_retry(self):
        body = self.method('private async Task<Candidate?> RecognizeAsync', 'private string? AudioRetryStopReason')
        self.assertEqual(body.count('Stopwatch.StartNew()'), 2)  # page prep and initial click
        self.assertLess(body.index('var watch = Stopwatch.StartNew()'), body.index('await ClickAsync('))
        retry = self.method('private async Task<Button?> TryAudioRetryAsync', 'private async Task LogAudioRequestFailureAsync')
        for bad in ('watch.Restart(', 'watch.Reset(', 'Stopwatch.StartNew()'):
            self.assertNotIn(bad, retry)

    def test_existing_10_and_15_second_decision_limits_remain(self):
        self.assertIn('AudioRequestDeadline = TimeSpan.FromSeconds(10)', self.source)
        body = self.method('private async Task<Candidate?> RecognizeAsync', 'private string? AudioRetryStopReason')
        self.assertIn('while (watch.Elapsed < TimeSpan.FromSeconds(15))', body)
        self.assertLess(body.index('watch.Elapsed > AudioRequestDeadline'), body.index('await TryAudioRetryAsync'))

    def test_retry_is_one_decision_at_2500ms_not_a_retry_loop(self):
        self.assertIn('AudioRetryDelay = TimeSpan.FromMilliseconds(2500)', self.source)
        body = self.method('private async Task<Candidate?> RecognizeAsync', 'private string? AudioRetryStopReason')
        self.assertEqual(body.count('await TryAudioRetryAsync'), 1)
        self.assertIn('!audioRetryConsidered && !_audioStarted', body)
        self.assertLess(body.index('audioRetryConsidered = true;'), body.index('await TryAudioRetryAsync'))
        self.assertIn('if (_audioRetryAttempts != 0)', self.source)

    def test_retry_budget_does_not_consume_the_whole_audio_deadline(self):
        self.assertIn('AudioRequestDeadline - TimeSpan.FromSeconds(3)', self.source)
        self.assertIn('budget.CancelAfter(TimeSpan.FromSeconds(2))', self.source)
        self.assertIn('.WaitAsync(TimeSpan.FromMilliseconds(500), token)', self.source)

    def test_host_filters_progress_native_mic_and_stale_navigation(self):
        body = self.method('private string? AudioRetryStopReason', 'private async Task<JsonElement> ProbeAudioRetryAsync')
        for field in ('_cycle != id', '_currentRecognitionId != id', '_audioRequested', '_audioStarted',
                      '_audioError', '_best', '_definiteNoMatch', '_tagResponses', '_nativePermissionsDenied',
                      '_navigationInProgress', '_pageEpoch != pageEpoch', '_audioBridgeArmed', '_pcmDataArrived'):
            self.assertIn(field, body)

    def test_host_rechecks_after_each_asynchronous_probe(self):
        body = self.method('private async Task<Button?> TryAudioRetryAsync', 'private async Task ClickAudioRetryAsync')
        self.assertGreaterEqual(body.count('AudioRetryStopReason('), 3)
        self.assertIn('ProbeAudioRetryAsync("probe"', body)
        self.assertIn('ProbeAudioRetryAsync("arm"', body)
        self.assertIn('SameButton(firstButton.Value, button.Value)', body)
        self.assertIn('const result = probe(id, home, key);', self.guard)

    def test_navigation_stabilization_is_once_per_page(self):
        body = self.method('private async Task<Button> StabilizeHomeButtonAsync', 'private async Task NavigateHomeAsync')
        self.assertLess(body.index('_stableHomeEpoch == _pageEpoch'), body.index('Task.Delay('))
        self.assertIn('HomeNavigationSettle = TimeSpan.FromMilliseconds(400)', self.source)
        self.assertIn('HomeButtonSampleInterval = TimeSpan.FromMilliseconds(100)', self.source)
        self.assertIn('Stopwatch.GetElapsedTime(_homeCompletedAt) >= HomeNavigationSettle', body)
        self.assertIn('previousEpoch == _pageEpoch', body)
        self.assertIn('SameButton(previous.Value, current.Value)', body)
        self.assertIn('_stableHomeEpoch = _pageEpoch;', body)

    def test_both_full_and_spa_navigation_invalidate_stability(self):
        full = self.method('private void OnNavigationStarting', 'private void CaptureRoute')
        spa = self.method('core.SourceChanged +=', 'core.NavigationCompleted +=')
        for body in (full, spa):
            self.assertIn('_pageEpoch++;', body)
            self.assertIn('_stableHomeEpoch = -1;', body)
        self.assertIn('_navigationId == args.NavigationId', self.source)

    def test_button_probe_result_is_not_reused_after_navigation(self):
        body = self.method('private async Task<Button?> FindButtonOnceAsync', 'private async Task<Button> PrepareHomeAsync')
        self.assertIn('var epoch = _pageEpoch;', body)
        self.assertIn('epoch != _pageEpoch', body)
        self.assertIn('recognitionPageEpoch != _pageEpoch', self.source)

    def test_guard_injected_before_audio_observer_and_included_as_resource(self):
        self.assertLess(self.source.index('Script("audio_retry.js")'), self.source.index('Script("audio_bridge.js")'))
        self.assertIn('.Replace("__VJ_FIND_BUTTON__", Script("find_button.js")', self.source)
        self.assertIn('EmbeddedResource Include="Scripts\\*.js"', (NATIVE / 'ShazamWebViewBridge.csproj').read_text())

    def test_cdp_retry_uses_trusted_input_not_element_click(self):
        body = self.method('private async Task ClickAudioRetryAsync', 'private async Task LogAudioRequestFailureAsync')
        self.assertIn('Input.dispatchMouseEvent', body)
        self.assertNotIn('.click()', body)
        self.assertIn('token.ThrowIfCancellationRequested()', body)
        self.assertIn('window.__vjAudioRetry?.cancel(', body)
        self.assertIn('await Send("mouseReleased", CancellationToken.None)', body)

    def test_retry_guard_cleanup_is_request_scoped(self):
        body = self.method('private async Task HandleCommandAsync', 'private void StartIdleHomePreparation')
        self.assertIn('window.__vjAudioBridge?.stop();', body)
        self.assertIn('window.__vjAudioRetry?.finish(', body)
        self.assertIn('JsonSerializer.Serialize(id)', body)
        self.assertIn('guard && guard.id === id', self.guard)

    def test_async_getusermedia_entry_notifies_host_before_resume(self):
        js = (NATIVE / 'Scripts/audio_bridge.js').read_text(encoding='utf-8')
        body = js.split('const getAudio = async constraints =>', 1)[1]
        self.assertLess(body.index("report('audio-requested'"), body.index('await current.resume()'))
        self.assertIn('case "audio-requested":', self.source)

    def test_diagnostic_snapshot_has_parent_state_and_retry_outcome(self):
        js = (NATIVE / 'Scripts/audio_failure_snapshot.js').read_text(encoding='utf-8')
        for field in ('clickPointAncestors', 'ariaBusy', 'ariaDisabled', 'ariaPressed', 'getDiagnostics'):
            self.assertIn(field, js)
        for field in ('retryAttempts=', 'Audio retry skipped', 'Audio retry dispatch', 'Audio request started after retry'):
            self.assertIn(field, self.source)

    def test_probe_rejects_non_finite_coordinates(self):
        body = self.method('private static Button? ReadButton', 'private static bool SameButton')
        self.assertIn('double.IsFinite(px)', body)
        self.assertIn('double.IsFinite(py)', body)
        self.assertIn('JsonValueKind.Number', body)

    def test_capture_guard_is_fail_closed_and_bounded(self):
        for term in ('guard-expired', 'probe-failed', 'retry-already-delivered', 'stopImmediatePropagation',
                     'click-target-changed', 'audio-already-requested', 'idle-not-proven'):
            self.assertIn(term, self.guard)
        self.assertNotIn('fetch(', self.guard)
        self.assertNotIn('getUserMedia(', self.guard)


if __name__ == '__main__':
    unittest.main()
