"""Consecutive Shazam track matching, independent of Qt and recognition I/O."""
from collections.abc import Iterable

Track = tuple[str, str]
HistoryEntry = tuple[str, str, str]
TITLE_PREFIX_LENGTH = 4
LEGACY_PREFIX_LENGTH = 6


def _normalize(value: str | None) -> str:
    return str(value or "").strip().casefold()


def _field_matches(previous: str, current: str) -> bool:
    """Preserve the existing six-character substring rule for normalized fields."""
    return bool(previous and current) and (
        previous[:LEGACY_PREFIX_LENGTH] in current
        or current[:LEGACY_PREFIX_LENGTH] in previous
    )


def is_same_track(previous: Track | None, current: Track | None) -> bool:
    """Compare adjacent results, not every occurrence of a track in the history.

    Four matching leading title characters are sufficient, even when artists
    differ (e.g. an original recording and a cover). Both titles must have at
    least four characters. Otherwise retain the previous title/artist rule,
    including recognition results for which the artist is temporarily absent.
    This intentionally also groups distinct songs that share those four letters.
    """
    if not previous or not current:
        return False

    previous_title, current_title = _normalize(previous[0]), _normalize(current[0])
    if not previous_title or not current_title:
        return False
    if (
        len(previous_title) >= TITLE_PREFIX_LENGTH
        and len(current_title) >= TITLE_PREFIX_LENGTH
        and previous_title[:TITLE_PREFIX_LENGTH] == current_title[:TITLE_PREFIX_LENGTH]
    ):
        return True
    if not _field_matches(previous_title, current_title):
        return False

    previous_artist, current_artist = _normalize(previous[1]), _normalize(current[1])
    return (
        not previous_artist
        or not current_artist
        or _field_matches(previous_artist, current_artist)
    )


def deduplicate_history(entries: Iterable[HistoryEntry]) -> list[HistoryEntry]:
    """Keep the newest row of each adjacent run in a newest-first history.

    Always compare the original neighboring rows. The legacy fuzzy comparison
    is not transitive, so comparing only with the last retained row is different.
    No sorting or global de-duplication is performed.
    """
    result: list[HistoryEntry] = []
    previous: Track | None = None
    for entry in entries:
        current = (entry[1], entry[2])
        if not is_same_track(previous, current):
            result.append(entry)
        previous = current
    return result
