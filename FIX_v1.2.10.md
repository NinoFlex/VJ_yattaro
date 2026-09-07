# v1.2.10 - YouTube safe-search music lookup fix

## Problem

Some correctly recognized tracks, including `脱げばいいってモンじゃない! (loves. 初音ミク)` by `デッドボールP`, produced zero YouTube results even though the Shazam/Apple metadata was correct.

The YouTube Data API `search.list` request did not specify `safeSearch`, so the API used its default filtering behavior. This can remove otherwise valid music videos from search results based on title/content classification.

## Fix

- Explicitly send `safeSearch=none` for YouTube video searches.
- Keep the existing query sanitization, API-key rotation, and 60-second Shorts filter unchanged.
- Add a regression test using the affected Japanese track query to ensure `safeSearch=none` remains present.

## Build

Run `build.cmd` on Windows after applying the patch. The Python source is bundled into the EXE during the build, so an existing EXE must be rebuilt.
