"""User-configured, literal title filtering; never changes recognition evidence.

No artist names, track IDs, suffix dictionaries, or regular expressions supplied
by the user are interpreted here. The empty default leaves existing titles alone.
"""
from functools import lru_cache
import re
import unicodedata

CONFIG_KEY = "track_title_remove_keywords"
MAX_KEYWORDS = 50


def normalize_keywords(value) -> list[str]:
    """Sanitize JSON/UI values without treating a string as a list of letters."""
    if not isinstance(value, (list, tuple)):
        return []
    result, seen = [], set()
    for item in value:
        if not isinstance(item, str) or "\n" in item or "\r" in item:
            continue
        word = item.strip()
        key = word.casefold()
        if not word or key in seen:
            continue
        seen.add(key)
        result.append(word)
        if len(result) == MAX_KEYWORDS:
            break
    return result


@lru_cache(maxsize=64)
def _pattern(words: tuple[str, ...]):
    # Longest match wins; a short keyword cannot leave half a longer suffix.
    return re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)),
                      re.IGNORECASE)


def strip_title_keywords(title, keywords=()) -> str:
    """Remove literal substrings, case-insensitively, and trim leftover spaces.

    Bracket width and punctuation remain literal. A completely removed title
    falls back to its original value: never turn a real recognition into an
    empty search/row. Reduction to a fixed point makes repeated UI entry points
    idempotent, including keywords exposed by removing another substring.
    """
    original = str(title or "")
    words = tuple(normalize_keywords(keywords))
    if not original or not words:
        return original
    pattern = _pattern(words)
    result = original
    while True:
        changed = pattern.sub("", result)
        if changed == result:
            break
        result = re.sub(r"[ \t\u3000]+", " ", changed).strip()
        if not result:
            return original
    return result


def filter_history_rows(rows, keywords=(), *, title_index=0) -> list[tuple]:
    """Copy rows; preserve timestamps, artists, comments and all extra fields."""
    result = []
    for row in rows or []:
        item = list(row)
        if len(item) > title_index:
            item[title_index] = strip_title_keywords(item[title_index], keywords)
        result.append(tuple(item))
    return result


def _comparison_title(value) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def title_variant_matches(previous, current, keywords=()) -> bool:
    """Presentation reuse for different *verified* IDs, NOT an identity proof.

    Additional suffix/artist compatibility check for presentation reuse.
    Cross-ID four-leading-character matching is performed separately by
    shazam_service via track_matching.shares_title_prefix, even when artists
    differ. This helper preserves the earlier full-base-title/suffix rule for
    cases that do not meet the four-character threshold. Neither rule turns
    two different verified IDs into two matching recognition votes.
    """
    if not previous or not current:
        return False
    a = _comparison_title(strip_title_keywords(previous[0], keywords))
    b = _comparison_title(strip_title_keywords(current[0], keywords))
    if not a or not b:
        return False
    # A different artist with the same short/common title is not a suffix change.
    aa, ab = _comparison_title(previous[1]), _comparison_title(current[1])
    if aa and ab and aa != ab:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    if not longer.startswith(shorter):
        return False
    tail = longer[len(shorter):]
    return bool(tail) and (tail[0].isspace() or tail[0] in "([{-~: /\u2010\u2011\u2012\u2013\u2014\u3010\u300c\u300e")
