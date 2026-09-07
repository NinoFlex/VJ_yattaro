# v1.2.9 - Shazam /song route ID localization fix

## Problem
Some route-only recognitions such as `.../song/6793804469/star` and
`.../song/6776126341/hikari` still produced a romanized title and blank artist.
The Apple exact-ID lookup already returned localized metadata, but the resolver
rejected it because the Japanese title did not text-match the English route slug.

## Fix
- Treat current Shazam `/song/<id>` route IDs as Apple song IDs.
- Accept the exact Apple Lookup record for `/song/<id>` when it is a real song
  record containing both title and artist.
- Keep the previous independent text/search proof for legacy `/track/<id>`
  routes, whose numeric IDs are Shazam-specific.
- Preserve all existing fallbacks when Apple Lookup has no usable record.

## Regression tests
- `/song/6793804469/star` -> localized exact-ID metadata is accepted.
- Legacy `/track/<id>` with an unrelated Apple numeric collision is rejected.
- Existing route-only, JSON-LD, public-page and exact-title/artist tests remain green.

## Applying
If you run the packaged EXE, rebuild with `build.cmd` because this change is in
Python code bundled by PyInstaller. If you run from source, replacing
`app/services/itunes_metadata.py` is sufficient; the included test update is for
verification only.
