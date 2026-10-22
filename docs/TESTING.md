# テスト手順・検証結果

Release: `2026-10-22-stable-v2`

Base: `2026-10-21-stable-v1`

## Results for this source revision

| Suite | Result | Scope |
|---|---:|---|
| Previous stable-v1 Python suite | 991 passed | Carried forward unchanged |
| Python full regression after changes | 1006 passed | Added 15 tie-up three-stage search tests |
| Node JavaScript mock checks | 60 passed | 29 Shazam + 5 recovery + 8 race + 18 same-video re-PRELOAD |
| Offline Chromium | 169 passed | 40 artist + 60 header + 22 plain artist + 9 readiness + 38 audio-start |
| Separate `tests/browser_checks.py` | Blocked | `ERR_BLOCKED_BY_ADMINISTRATOR` at localhost navigation; excluded from passed totals |
| Actual Qt render / Windows EXE / real services | Not run | PySide6 unavailable; no Windows execution environment |

The stable-v1 comment/status tests remain in the full suite. The 15 new tests
exercise the actual query generator, sequential worker on simulated HTTP result
sets, query cancellation and API errors, and the MainWindow search entry point.
They verify the reported `Dolphin☆jet` query ordering but cannot prove that
YouTube currently returns videos for any of those queries. The Qt interfaces
are mocked, so they do not prove physical screen repaint or audio accuracy. The 89 historical log-derived Uta-Net fixture inputs remain synthetic
HTTP tests, NOT 89 new live requests or a measured hit rate.

The release verification was performed on Linux, Python 3.13.5, Node.js 22.16.0
and the container Chromium. The Windows build scripts still require 64-bit
Python 3.12; dependency versions are unchanged. Python tests can create local
logs/caches, which are excluded from the delivered archive.

## Python regression

From the source root, with the test dependencies already available:

```powershell
python -m unittest discover -s tests -p "test_*.py"
python -m unittest discover -s tests -p "test_shazam_comment_status.py"
python tools/verify_source.py
```

The suite uses the standard unittest runner; there is no requirement to install
pytest. Existing tests additionally use packages such as numpy and requests.
The source-integrity check reads the delivered SHA256SUMS.txt and ignores extra
local settings/history/build files. It never modifies files.

## Node checks

```powershell
node tests/javascript_checks.cjs
node tests/player_recovery_checks.cjs
node tests/player_race_checks.cjs
node tests/player_repreload_checks.cjs
```

## Optional offline browser checks

These require Playwright and a Chromium executable in the development environment.
They are not required to build or run the application. Replace the path below with
your installed Chromium path:

```powershell
python tests/artist_metadata_checks.py --chromium "C:\path\to\chromium.exe"
python tests/header_identity_checks.py --chromium "C:\path\to\chromium.exe"
python tests/plain_artist_checks.py --chromium "C:\path\to\chromium.exe"
python tests/idle_readiness_checks.py --chromium "C:\path\to\chromium.exe"
python tests/audio_start_checks.py --chromium "C:\path\to\chromium.exe"
```

`tests/browser_checks.py` is a separate retained suite. Run it where localhost
navigation is permitted. A blocked navigation is not a successful test.

## Windows acceptance checklist

- Start the new executable and check `UI: Source build: 2026-10-22-stable-v2`.
- Enable tie-up enrichment with a suitable test song; where first/second queries
  return no usable results, confirm the logs show `query 1/3`, `2/3`, `3/3` in
  the order enriched → normal Shazam → artist-free Shazam. Where the first query
  succeeds, confirm there are no follow-up requests.
- Confirm only the current candidate's new search results may auto-PLAY after
  two votes and player readiness, even if the old initial search had a PRELOAD.
- On the first vote, verify that the comment column displays the Japanese
  tentative label, with no added label in title or artist.
- When a tie-up arrives before confirmation, verify label + comment together.
- After the history commit, verify comment-only or blank. Fast two-vote cases
  may transition immediately; no artificial display delay was introduced.
- Select/search a tentative row: the generated query must not contain the label.
- With tie-up ON, verify initial PLAY stays held until the outcome; found selects
  the enriched result only, while empty/error uses the initial result.
- Verify a confirmed entry remains in history when tie-up lookup fails.
- Verify same-video PRELOAD reuse and manual controls still operate normally.

A field run of the base version is not a field run of the new display revision.

## Uta-Net diagnostic tool (explicit, optional network test)

The client and permission checks are unchanged. Default mode only parses input.
Use live mode only when operator permission has already been obtained:

```powershell
python tools/check_utanet_from_log.py --tracks tests/fixtures/utanet/log_confirmed_tracks.json --limit 5 --out utanet_check.json
python tools/check_utanet_from_log.py --tracks tests/fixtures/utanet/log_confirmed_tracks.json --limit 5 --live --permission-confirmed --out utanet_live_check.json
```

The permission flag does not obtain permission. This release preparation did
not execute the live command or change any actual Uta-Net/YouTube data.
