"""One video's playback transaction; unrelated Shazam votes cannot mutate it."""
from dataclasses import dataclass
from enum import Enum


class PlaybackPhase(str, Enum):
    DEFERRED = "deferred"
    PRELOADING = "preloading"
    READY = "ready"
    PLAY_REQUESTED = "play_requested"
    PLAYING = "playing"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PlaybackAttempt:
    candidate_id: int
    video_id: str
    phase: PlaybackPhase = PlaybackPhase.PRELOADING
    preload_request_id: str | None = None
    play_request_id: str | None = None
    preload_retries: int = 0

    def matches_preload(self, video_id: str, request_id: str) -> bool:
        return (self.video_id == video_id and
                (not self.preload_request_id or self.preload_request_id == request_id) and
                self.phase in (PlaybackPhase.PRELOADING, PlaybackPhase.READY))

    def matches_play(self, video_id: str, request_id: str) -> bool:
        return (self.video_id == video_id and
                (not self.play_request_id or self.play_request_id == request_id) and
                self.phase in (PlaybackPhase.PLAY_REQUESTED, PlaybackPhase.PLAYING))

    def cue_requested(self, request_id: str | None) -> None:
        self.preload_request_id = request_id
        self.play_request_id = None
        self.phase = PlaybackPhase.PRELOADING

    def play_requested(self, request_id: str | None) -> None:
        self.play_request_id = request_id
        self.phase = PlaybackPhase.PLAY_REQUESTED

    def mark_ready(self, video_id: str, request_id: str) -> bool:
        if not self.matches_preload(video_id, request_id):
            return False
        self.phase = PlaybackPhase.READY
        return True

    def mark_playing(self, video_id: str, request_id: str) -> bool:
        if not self.matches_play(video_id, request_id):
            return False
        self.phase = PlaybackPhase.PLAYING
        return True

    def cancel(self) -> None:
        self.phase = PlaybackPhase.CANCELLED
