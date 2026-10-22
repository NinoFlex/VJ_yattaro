"""Extract confirmed log tracks; optionally check them with the production HTTP client.

Default: no network. --live additionally requires --permission-confirmed.
No browser, lyric export, YouTube requests, or application history writes.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.utanet_service import UtanetClient, lookup_titles

_QUOTED = r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')'''
_VOTE = re.compile(r"Tentative first vote seq=(\d+) title=(" + _QUOTED + r") artist=(" + _QUOTED + r"); start")
_CONFIRM = re.compile(r"UI: Shazam second vote confirmed firstSeq=(\d+)")
_TIME = re.compile(r"^\[([0-9-]{10} [0-9:.]{12})\]")


def extract_tracks(paths):
    """Join first-vote metadata and confirmations across rotations / restarts."""
    events = []
    for path in map(Path, paths):
        for number, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
            timestamp = _TIME.match(line)
            if timestamp:
                events.append((timestamp.group(1), path.name, number, line))
    events.sort(key=lambda row: (row[0], row[1], row[2]))
    session, votes, confirmations, seen = 0, {}, {}, set()
    for timestamp, filename, number, line in events:
        if line in seen:
            continue
        seen.add(line)
        if "ShazamService: Started (" in line:
            session += 1
        match = _VOTE.search(line)
        if match:
            seq = int(match.group(1))
            try:
                title, artist = ast.literal_eval(match.group(2)), ast.literal_eval(match.group(3))
            except (ValueError, SyntaxError):
                continue
            if isinstance(title, str) and isinstance(artist, str) and title and artist:
                votes[(session, seq)] = dict(session=session, seq=seq, title=title, artist=artist,
                    log=filename, line=number, time=timestamp)
        match = _CONFIRM.search(line)
        if match:
            confirmations.setdefault((session, int(match.group(1))), timestamp)
    return [{**votes[key], "confirmed_at": confirmations[key]}
            for key in confirmations if key in votes]


def load_tracks(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("track input must be a JSON list")
    return [row for row in data if isinstance(row, dict) and
            isinstance(row.get("title"), str) and isinstance(row.get("artist"), str)
            and row["title"] and row["artist"]]


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="*", help="rotated log files, in any order")
    parser.add_argument("--tracks", help="previously extracted JSON track list")
    parser.add_argument("--out", default="utanet_check.json")
    parser.add_argument("--limit", type=int, default=0, help="0 = all extracted tracks")
    parser.add_argument("--live", action="store_true", help="use the real HTTPS client")
    parser.add_argument("--permission-confirmed", action="store_true", help="confirm existing site-operator permission")
    args = parser.parse_args(argv)
    if bool(args.logs) == bool(args.tracks):
        parser.error("supply either log paths or --tracks, not both")
    if args.live and not args.permission_confirmed:
        parser.error("--live requires --permission-confirmed (this flag does not obtain permission)")
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    try:
        tracks = extract_tracks(args.logs) if args.logs else load_tracks(args.tracks)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.limit:
        tracks = tracks[:args.limit]
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "mode": "live_https" if args.live else "extract_only_no_network",
              "total": len(tracks), "completed": 0, "found": 0, "results": [],
              "note": "Transport errors are not evidence that a song has no tie-up."}
    client = UtanetClient() if args.live else None
    failures = 0
    interrupted = False
    try:
        for row in tracks:
            item = {**row, "queries": lookup_titles(row["title"]), "transport_performed": False}
            if client is None:
                item.update(status="not_run", comment="", source_url="")
            elif failures >= 3:
                item.update(status="not_run_after_transport_errors", comment="", source_url="")
            else:
                from dataclasses import asdict
                result = client.lookup(row["title"], row["artist"])
                item.update(asdict(result), transport_performed=not result.cached and result.status != "cooldown")
                failures = failures + 1 if result.status in {"error", "unavailable", "cooldown", "timeout"} else 0
                if result.status in {"unavailable", "cooldown"}:
                    failures = 3  # Do not keep querying an access-limited site.
            report["results"].append(item)
            report["completed"] += 1
            report["found"] += int(item["status"] == "found")
            write_report(args.out, report)
    except KeyboardInterrupt:
        interrupted = True
        report["interrupted"] = True
    finally:
        if client is not None:
            client.session.close()
        write_report(args.out, report)
    print(f"{report['mode']}: {report['completed']}/{len(tracks)}, found={report['found']}; {args.out}")
    return 130 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
