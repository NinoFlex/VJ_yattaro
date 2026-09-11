"""Pure automatic-video selection, using durations already returned by search."""

from collections.abc import Mapping, Sequence


def _duration_seconds(value: object) -> int | None:
    """Read M:SS (the search format) or H:MM:SS; ignore unknown durations."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or any(
        not part.isascii() or not part.isdigit() for part in parts
    ):
        return None
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    if numbers[-1] >= 60 or (len(numbers) == 3 and numbers[-2] >= 60):
        return None
    seconds = 0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def select_auto_play_index(
    videos: Sequence[Mapping[str, object]], anime_op_mode: bool = False
) -> int | None:
    """Prefer the first 90-105 second video among the top four, else rank one.

    Search ranking is not modified. Empty results have no selection. A missing
    duration or video ID cannot qualify a result for anime OP preference.
    """
    if not videos:
        return None
    if anime_op_mode:
        for index, video in enumerate(videos[:4]):
            seconds = _duration_seconds(video.get("duration"))
            if video.get("video_id") and seconds is not None and 90 <= seconds <= 105:
                return index
    return 0
