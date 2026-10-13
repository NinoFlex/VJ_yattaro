"""First-vote candidate, confirmed ownership, and its search/physical cue.

The dict-style methods are a temporary interface for legacy MainWindow paths
and AST-based tests. The stored fields are typed, not an unstructured dict.
"""
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass, field, fields
from typing import Any

from app.models.playback_state import PlaybackAttempt
from app.models.search_request import SearchRequest


@dataclass
class TrackCandidate(MutableMapping[str, Any]):
    id: int
    entry: tuple[str, str, str]
    confirmed: bool = False
    committed: bool = False
    ready: bool = False
    video_id: str | None = None
    manual_override: bool = False
    play_authorized: bool = False
    track_info: dict[str, str] = field(default_factory=dict)
    playing_ack: bool = False
    play_sent: bool = False
    cue_request_id: str | None = None
    deferred_videos: list[dict] | None = None
    deferred_index: int | None = None
    search: SearchRequest | None = None
    attempt: PlaybackAttempt | None = None

    @classmethod
    def from_vote(cls, candidate_id: int, entry: tuple[str, str, str]) -> "TrackCandidate":
        vote = tuple(entry[:3])
        return cls(id=int(candidate_id), entry=vote,
                   track_info={"title": vote[1], "artist": vote[2], "comment": ""})

    # Compatibility only. New code uses field/property access and the session API.
    def __getitem__(self, key: str) -> Any:
        if key not in self._field_names():
            raise KeyError(key)
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self._field_names():
            raise KeyError(key)
        setattr(self, key, value)

    def __delitem__(self, key: str) -> None:
        if key not in self._field_names():
            raise KeyError(key)
        # Only transient fields may be popped by legacy callers.
        if key not in ("deferred_videos", "deferred_index"):
            raise KeyError(key)
        setattr(self, key, None)

    def __iter__(self) -> Iterator[str]:
        return iter(self._field_names())

    def __len__(self) -> int:
        return len(self._field_names())

    @classmethod
    def _field_names(cls) -> frozenset[str]:
        return frozenset(item.name for item in fields(cls))

    def pop(self, key: str, default: Any = None) -> Any:
        value = getattr(self, key, default)
        if key in ("deferred_videos", "deferred_index"):
            setattr(self, key, None)
        elif key not in self._field_names():
            return default
        else:
            raise KeyError(f"Non-transient candidate field cannot be popped: {key}")
        return value
