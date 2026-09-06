# Verification report

## Executed successfully in this environment

- Python AST parsing of the application source and added Python modules/tests.
- 42 Python unittest checks (`tests/test_webview_integration.py` + `tests/test_search_templates.py`). Qt and HTTP are
  mocked: these test the service/history contract, cancellation generations,
  two-lane staggered scheduling, stale-result ordering, strict Apple localization, live Japanese fallback, wrong-song rejection, route-only Shazam fallback, title-only de-duplication, request IDs, and preservation.
- Node.js syntax checks of all five embedded JavaScript files.
- 16 JavaScript VM fixture checks (`tests/javascript_checks.cjs`). Web Audio and
  DOM APIs are mocked: these verify result filters, route ID distinctions, stale
  response rejection, callback wiring, origin guards, and audio-graph setup/cleanup.
- Hash comparison confirms unrelated UI/player source files match the input; `main.py` and the settings dialog are intentionally changed for v1.2.6.
- Project XML/manifest parsing and ZIP integrity checks.

## Not verified here

- .NET / C# compilation for Windows: no .NET SDK is available in this environment.
- Running the Windows EXE or Microsoft WebView2 Runtime.
- Real Qt GUI operation, actual sounddevice microphone capture, or live Shazam recognition.
- Live iTunes network responses and real-world recognition accuracy/latency.
- Windows PowerShell/build.cmd execution and PyInstaller Windows packaging.
- Real Chromium audio integration fixture. A browser fixture runner is supplied,
  but the Playwright Chromium executable is not installed in this environment;
  it was not used as evidence of a successful integration test.

This is a tested-at-the-logic-level source integration, not a Windows-validated release.
No prebuilt application executable is included.

## Repeat the offline checks

```text
python -m unittest discover -s tests -p "test_*.py" -v
node tests/javascript_checks.cjs
```

`tests/browser_checks.py` is an additional optional browser fixture, not part of
application startup or build requirements. It needs Playwright and its Chromium
browser. It intercepts all fixture requests locally, does not contact Shazam, and
uses a test-only localhost origin adaptation. Production scripts are unchanged.

## Windows acceptance checklist

1. Run build.cmd; check that native helper and Python app both publish successfully.
2. Start the normal app and check that no new visible browser/console appears.
3. Switch to Shazam mode and use the existing microphone selector and recording duration.
4. Play known music; check title AND artist in the existing table and UTF-8 history JSON.
5. Test Japanese metadata from a real Apple Music link, strict title+artist matching, and the live-Japanese fallback (for example `ふわりずむ`).
6. Check that the existing YouTube search/auto-play behavior receives the same tuple contract.
7. Switch back to Rekordbox, change microphone, and close the app during recognition;
   ensure no stale row is added and the helper terminates.
8. Test network failure, missing WebView2 Runtime, and a site consent screen.
9. Run with VJ_SHAZAM_DEBUG=1 only when inspecting the hidden site's behavior.


- v1.2.1: regression fixture verifies `概要 / Kia Mazzi` + Apple track 763630973 resolves to `Trilogy / Kia Mazzi`.

- v1.2.2: regression fixtures verify route-only Shazam ID `1655784202` can be used only after Apple EN title proves the same route slug, returning JP metadata; an unrelated Apple record with the same numeric ID is rejected.
- v1.2.2: recognition-response `matches: []` fixture emits an immediate no-match signal, and the v1.2.2 bridge safety deadline was 18 seconds instead of 36 seconds.
- v1.2.2: WebView2 prewarm is queued as soon as Shazam microphone capture starts.


## v1.2.3 fast-path checks

- 100 ms recognition readiness timer is asserted.
- C# source check verifies WebView2 `/tag/` response observation.
- C# source check verifies `_busy` is cleared before the result is sent to Python.
- JavaScript fixture verifies visible no-match text emits an immediate retry signal.
- All embedded JavaScript files pass `node --check`.


## v1.2.4 dual-lane checks

- Python fixture verifies two recognition lanes can be in flight concurrently with a global 4.0 second stagger.
- Python fixture verifies an older lane result cannot overwrite a newer published recognition.
- Source check verifies lane helpers receive distinct `--instance` IDs and C# stores each WebView2 profile under a separate folder.
- C# source check verifies the per-lane safety deadline is 15 seconds.
- Apple metadata resolver remains shared/serialized so parallel Shazam results reuse the same strict localization cache.
- v1.2.4 baseline remained covered; current full suite is Python 42/42 and JavaScript VM 16/16.


## v1.2.5 footer-label regression

- `Shazam フッター` and `Shazam Footer` are rejected as titles in JS/C#/Python.
- DOM headings nested under footer/contentinfo/navigation/header are ignored.
- The captured route slug remains available as the safe fallback title.


## v1.2.6 mode-specific YouTube search templates

- Added offline tests for distinct Rekordbox/Shazam template defaults and custom values.
- Shazam default is `%tracktitle% %artist%`; Rekordbox preserves the legacy `%tracktitle% %comment%` behavior.
- The pending-search queue carries the originating source mode, preventing a later mode toggle from changing the queued query template.
- `main.py` and `ui/dialogs/settings_dialog.py` are intentionally changed in this release; unrelated visible UI files remain hash-checked against the original application.

## v1.2.7 route-only romanized-title Japanese metadata regression

- Added a regression fixture for Shazam route-only ID `1565502610` with slug `14-heibei-ni-souvenir-original-karaoke`.
- Apple Lookup returns Japanese metadata while direct text proof fails; Apple Search with the exact Shazam route title must independently return the same Track ID before JP metadata is accepted.
- Expected result: `14平米にスーベニア (オリジナル・カラオケ)` and a non-empty Apple JP artist.
- Unrelated numeric-ID collisions remain rejected when route-title Apple Search does not return that ID.
- Python regression suite: 43/43 PASS in this environment.
- JavaScript mock/syntax checks remain unchanged and pass.
