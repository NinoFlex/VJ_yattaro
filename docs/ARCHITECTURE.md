# Source layout and maintenance boundaries

Release: `2026-10-22-stable-v2`.

| Path | Responsibility |
|---|---|
| `main.py` | Qt application, source switching, candidate display and playback orchestration |
| `app/models/` | Candidate ownership, search transactions, tie-up selection, playback state |
| `app/services/` | Shazam, Uta-Net HTTPS, YouTube search, configuration, history, hotkeys and local player server |
| `ui/` | Widgets, dialogs, theme and table-model presentation |
| `native/ShazamWebViewBridge/` | C# helper and embedded scripts for official Shazam WebView2 recognition |
| `web/` | Browser player HTML / CSS / JavaScript |
| `assets/` | Required application icons |
| `tools/` | Windows build scripts, log-based Uta-Net check and source integrity checker |
| `tests/` | Python, Node and offline browser regressions with required fixtures |
| `docs/` | Current usage, limitations, test procedure and this source map |
| `SHA256SUMS.txt` | Complete source inventory and checksums, excluding the manifest itself |

## Display/data separation

The existing recognition tuple remains `(timestamp, title, artist)`. A preview
in `MainWindow._refresh_shazam_history_view()` uses an exact state suffix on its
timestamp. `ShazamHistoryModel` now renders that suffix in the third visible
column alongside the actual tie-up comment. Only `(tentative)` and the existing
confirmed-metadata-pending label are recognized (their UI text is Japanese).

`comment_for_row()` remains undecorated. Manual search, automatic query inputs,
`track_info`, tie-up cache and persisted history therefore do not receive the
presentation label. The model does not mutate the source tuple or add timers.
`refresh_comments()` updates the comment cells without resetting row selection.

## Runtime changes in stable-v2

`app/services/youtube_service.py` builds a three-stage `shazam_tieup` query plan:
first the enriched template, next the independent Shazam template, then its
artist-free query. The same `YouTubeSearchThread` executes these in order, after
an empty or filtered-empty response. It stops on the first usable set of videos.
The cap of three requests (excluding quota-key retries) and deduplication are
unchanged. The normal Shazam/Rekordbox builders and the GUI orchestration stay
unchanged. This does not resurrect any aborted initial search worker or the
initial player's PRELOAD/PLAY cue; the active SearchRequest still has
`metadata_refresh=True` and the usual two-vote and readiness gates.

Relative to stable-v1, only the query planner, source-build tag, tests and
release documentation are changed. The state machines, other network clients,
C#, JavaScript, dependency pins, icons, specification and build scripts are
unchanged. The integrity checker is not invoked by application startup.

Confirmation, tie-up selection and browser readiness remain separate gates.
Different known Shazam IDs cannot certify each other as the two required votes.
The four-character presentation de-duplication rule remains separate. Request IDs
continue to protect same-video re-PRELOAD and late worker / browser callbacks.

## Package cleanup

The release does not contain the old per-fix reports or accumulated historical
README/TESTING sections. They remain available in the original base archive;
current behavior is documented here and in USAGE.md, not mixed with superseded
behavior. Generated verification results and environment-specific network probes
were removed. Required Uta-Net regression inputs were relocated from
`docs/verification/` to `tests/fixtures/utanet/` and their test references updated.
The synthetic HTML/JSON fixtures are test inputs, not user configuration or a
promise of live-service coverage. No original full user logs are bundled.

No dependency versions, authorization behavior or license grants were changed.
The archive has one top-level `VJ_yattaro_stable/` directory. Build and run from
that directory; executable distribution is still the whole `dist/VJ_yattaro/`.
