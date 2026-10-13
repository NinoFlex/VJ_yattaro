"""A YouTube lookup belongs to one Shazam first-vote or manual operation."""
from dataclasses import dataclass
from enum import Enum


class SearchPhase(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class SearchRequest:
    title: str
    artist: str
    comment: str = ""
    candidate_id: int | None = None
    source_mode: str = "rekordbox"
    allow_auto_play: bool = False
    phase: SearchPhase = SearchPhase.QUEUED
    # Retain the original search invocation when an in-flight worker delays it.
    from_list: bool = False
    # A two-vote result must never be evicted by a later one-vote result.
    confirmed: bool = False

    @property
    def valid(self) -> bool:
        return self.phase not in (SearchPhase.CANCELLED, SearchPhase.FAILED)

    def cancel(self) -> None:
        self.phase = SearchPhase.CANCELLED
