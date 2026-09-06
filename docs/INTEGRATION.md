# Shazam WebView2 integration: implementation notes

## Scope

This is a source-level replacement of the uploaded application's Shazam recognition
backend. It is not a replacement main window and does not launch ShazamWatch as a
separate user-facing app. `main.py`, `ui/`, and `web/` are byte-identical to the input
archive. Rekordbox, MIDI, hotkeys, YouTube search/player code, table models, and the
existing theme are untouched.

This uses **Microsoft Edge WebView2**, not WebKit. Recognition is executed by the
real Shazam.com page. This integration is website automation, not an Apple-supported
Windows recognition SDK. It does not use ShazamIO or call Shazam's private recognition
endpoints itself. Reading the page's own completed recognition responses is an
observer, not an additional recognition request. Site changes may require maintenance.
No accuracy comparison with ShazamIO or the official phone app was performed.

## Pipeline

```text
Existing settings: microphone index, 5..20-second window
  -> existing sounddevice/PortAudio stream and ring buffer
  -> existing mono PCM WAV snapshot
  -> Python recognition worker
  -> private child-process stdin/stdout (JSON lines, no local HTTP server added)
  -> self-contained .NET helper hosting Shazam.com in WebView2
  -> Web Audio MediaStream supplied to the page's getUserMedia call
  -> official Shazam page performs recognition
  -> response observer / song-route capture / scoped DOM result
  -> exact-ID iTunes Lookup for the selected country/language
  -> unchanged history and Qt signals
  -> unchanged app tables, YouTube search, and auto-play flow
```

Keeping the original microphone capture avoids trying to equate a PortAudio numeric
index with Chromium's origin-scoped deviceId. The selected microphone is the only
physical input. There is no Windows virtual audio driver, no global default-device
change, and no speaker loopback. The helper refuses native microphone/camera requests
as a fail-closed precaution. A missing input bridge is an error, not a fallback to a
possibly unrelated microphone.

The page receives the selected 5..20-second snapshot as a generated audio MediaStream.
The snapshot is repeated **only within that recognition request** because the website,
not the app, determines its listening duration. The graph connects to a
MediaStreamAudioDestinationNode, never to AudioContext.destination. The helper's page
is also muted. This does not send live, newly arriving audio into an already-running
request; the next request takes a new snapshot from the ring buffer.

The public ShazamService contract remains:

- `start()`, `stop()`, `reload_settings()`, `shutdown()`, `is_active()`;
- `list_input_devices()` and `get_history()`;
- `history_updated(list)`, `new_track_detected((timestamp, title, artist))`,
  `status_changed(str)`, `error_occurred(str)`.

History remains a maximum of 50 records in `shazam_history.json`. The previous
case-insensitive, six-character-prefix de-duplication is intentionally unchanged.
No new fields are required by the UI. Japanese text is passed directly as Unicode;
there is no `now_playing.txt` polling bridge.

## Scheduling and errors

The original three-second Qt timer remains as a readiness/watchdog timer. There is
no fixed 25-second web interval in v1.2. Only one recognition is in flight at a time;
when that attempt completes (match, no-match, or error), the service immediately
queues the newest available microphone snapshot. This preserves the existing UI while
implementing continuous recognition without overlapping WebView2 requests.

The helper still has a 75-second per-request total deadline and a 36-second result
listening deadline. A stop, microphone change, mode change, or shutdown sets a
cancellation event, invalidates the generation, stops the PortAudio stream, and
terminates the helper without publishing stale responses.

## Result correctness

Shazam recognition identity is authoritative. The helper keeps `shazamTrackId` and
`appleTrackId` as separate fields. A Shazam `/song/<id>` or `/track/<id>` route is
**never** reinterpreted as an Apple song ID. A real Apple ID is accepted only from
an Apple Music/iTunes song link (typically `?i=<trackId>`).

When a recognized Shazam route appears, the route is allowed to render for up to
about 1.8 seconds so the exact live page can expose its visible title, artist, and a
new Apple Music link. Visible result headings are preferred. JSON-LD is secondary and
its global Apple links are not attached because recommendation metadata can coexist
with the recognized result.

Japanese localization follows this order:

1. Exact Apple Music Track ID from the current Shazam result -> Apple en verification + JP lookup.
2. No exact ID -> Apple Search using the **live Shazam title + artist**, accepting only
   normalized exact title AND exact artist matches.
3. If needed, repeat the same strict match using the recognized route slug title + artist.
4. Static Shazam HTML is supplemental only when canonical/OG route identity matches and
   its title is compatible with the recognized route.
5. If Apple cannot be safely linked but the exact live `/ja-jp` result already contains
   Japanese text, keep that Japanese title. Only then fall back to trusted Shazam text
   or the route slug.

This intentionally rejects fuzzy/partial Apple candidates and stale/unrelated Shazam
metadata. Duplicate Apple catalog rows are accepted only when every exact candidate
resolves to the same localized title.

## Build and distribution

Run the root `build.cmd` on Windows. It creates/reuses `.venv` with 64-bit Python 3.12,
installs the application's existing dependencies minus ShazamIO, publishes the .NET
helper for `win-x64`, and freezes the original app using the updated PyInstaller spec.

The helper build reuses a usable installed SDK or the earlier private ShazamWatch SDK
at `%LOCALAPPDATA%\ShazamWatch\dotnet-sdk-8`. Otherwise it downloads Microsoft's official
.NET install script and installs a private SDK at
`%LOCALAPPDATA%\VJ_yattaro\dotnet-sdk-8`. Administrator privileges are not required for
this SDK bootstrap. Internet access is required for package downloads.

The .NET helper is self-contained; a separate .NET Runtime is not required on the
user's PC. The **Microsoft Edge WebView2 Runtime is still required**. If missing,
the app reports an initialization error. The helper does not silently install a
browser runtime on application startup. Runtime installation is handled by the user
or the application's distributor.

The entire published helper folder is bundled as data under
`dist\VJ_yattaro\_internal\shazam_webview`. Do not distribute only the main executable.
The build verifies the helper, CoreCLR, WebView2 managed wrappers/loader, and the
PortAudio DLL before copying the staged build to `dist\VJ_yattaro`.

Existing destination `config.json` and `shazam_history.json` are preserved. Build in
a new source folder or back up your existing installation first. The build does not
wipe the existing output directory; files from older builds may remain, but the
new spec explicitly excludes ShazamIO packages.

## Debugging

Application logging uses the existing `vj_yattaro.log` next to `VJ_yattaro.exe`
(or at the source root during a Python run). The existing logging ON/OFF setting
still applies. Helper stderr is forwarded into that logger with `ShazamWebView:`.
Recordings/base64 and full network response bodies are not logged or saved by this
integration. The website's own browser storage/caching remains controlled by WebView2.

The helper's dedicated browser profile is:

```text
%LOCALAPPDATA%\VJ_yattaro\ShazamWebView2\profiles\ja-jp
```

The final component varies with the selected language. It does not use or delete
the user's regular Edge/Chrome profile or the previous ShazamWatch profile.

To show the helper only for diagnostics, close the app, then run:

```powershell
$env:VJ_SHAZAM_DEBUG = "1"
.\dist\VJ_yattaro\VJ_yattaro.exe
```

Switch the app to Shazam mode as usual. Normal launches do not show a browser window.
If the site displays a consent screen, complete it manually in diagnostic mode;
the program does not accept cookie/privacy terms on the user's behalf. Close and
restart the app after changing the environment flag. To clear the flag for later
launches, use `Remove-Item Env:VJ_SHAZAM_DEBUG`.

## Verification boundaries

See `VERIFICATION.md`. Python behavior and JavaScript mock fixtures are tested;
Windows compilation, real WebView2 execution, the actual live Shazam site, and
real microphone recognition are **not verified in this build environment**.

## Primary references

- WebView2 hosting and deployment:
  https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
- WebView2 message bridge:
  https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.webmessagereceived
- Web Audio MediaStreamAudioDestinationNode:
  https://www.w3.org/TR/webaudio-1.0/#MediaStreamAudioDestinationNode
- Shazam official website recognition:
  https://support.apple.com/guide/shazam/identify-a-song-on-the-web-devfa7ba51e1/web
- iTunes Lookup:
  https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/LookupExamples.html
- Pinned WebView2 NuGet package:
  https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4191.47


### v1.2.1
Result-region UI labels such as `概要` are filtered before title ranking. Verified real Apple track links remain authoritative.


## v1.2.4 dual-lane latency mode

The integrated backend now keeps two independent WebView2 helper processes warm. Lane 2 starts four seconds after lane 1 when a fresh microphone snapshot is available. Each helper uses its own WebView2 user-data subfolder (`lane-1`, `lane-2`) and remains single-flight internally. Results carry a monotonically increasing request sequence; a late result from an older snapshot cannot replace a newer published track. The bridge safety deadline is 15 seconds per lane.


## v1.2.5 UI-label guard

Transient Shazam result scanning ignores footer/header/navigation containers and rejects labels such as `Shazam フッター`. The same title filter is duplicated in the native bridge and Python metadata resolver so a DOM-layout regression cannot directly reach the visible app title.


## v1.2.6 source-specific YouTube query templates

YouTube query formatting is now selected by the source mode that originated the search.
Rekordbox uses `youtube_search_template_rekordbox` (legacy default `%tracktitle% %comment%`) while Shazam uses `youtube_search_template_shazam` (default `%tracktitle% %artist%`). The old `youtube_search_template` key remains as a Rekordbox compatibility alias. Pending searches preserve their originating mode.
