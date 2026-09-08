"""Autoplay selection from existing search metadata; no Qt or network dependency."""

from collections.abc import Mapping, Sequence

ANIME_OP_CANDIDATE_COUNT = 4
ANIME_OP_MIN_SECONDS = 90
ANIME_OP_MAX_SECONDS = 105


def _duration_seconds(value: object) -> int | None:
    """Read the service's m:ss format; also accept h:mm:ss. Unknown stays unknown."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or any(not p.isascii() or not p.isdigit() for p in parts):
        return None
    numbers = [int(p) for p in parts]
    if any(n >= 60 for n in numbers[1:]):
        return None
    total = 0
    for n in numbers:
        total = total * 60 + n
    return total


def select_auto_play_index(
    videos: Sequence[Mapping[str, object]], anime_op_mode: bool = False
) -> int | None:
    """Prefer the first 90-105s video among the top four; otherwise use rank one."""
    if not videos:
        return None
    if anime_op_mode:
        for row, video in enumerate(videos[:ANIME_OP_CANDIDATE_COUNT]):
            seconds = _duration_seconds(video.get("duration"))
            if (video.get("video_id") and seconds is not None
                    and ANIME_OP_MIN_SECONDS <= seconds <= ANIME_OP_MAX_SECONDS):
                return row
    return 0
