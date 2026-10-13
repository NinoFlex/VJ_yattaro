"""Qt-independent ownership rules for speculative Shazam -> YouTube playback.

An initial C vote *cannot* touch the confirmed B transaction. Only a second
vote (explicit confirmation), manual override, or B's own terminal event can
supersede B. Browser feedback is correlated to a candidate-specific attempt
and its command ID, not just a video ID which might repeat across A/B.
"""
from dataclasses import dataclass

from app.models.playback_state import PlaybackAttempt, PlaybackPhase
from app.models.search_request import SearchRequest, SearchPhase
from app.models.track_candidate import TrackCandidate


@dataclass
class ShazamPlaybackState:
    tentative: TrackCandidate | None = None
    confirmed_pending: TrackCandidate | None = None
    active_attempt: PlaybackAttempt | None = None
    active_search: SearchRequest | None = None
    queued_search: SearchRequest | None = None
    # Most recent *unconfirmed* lookup, behind a protected confirmed lookup.
    speculative_search: SearchRequest | None = None
    # A confirmed search can outlive its UI preview even with autoplay OFF.
    confirmed_search_owner: TrackCandidate | None = None

    def find_candidate(self, candidate_id: int) -> TrackCandidate | None:
        for candidate in (self.confirmed_pending, self.tentative,
                          self.confirmed_search_owner):
            if candidate is not None and candidate.id == candidate_id:
                return candidate
        return None

    def confirmed_awaiting_play(self) -> TrackCandidate | None:
        candidate = self.confirmed_pending
        return candidate if (candidate is not None and candidate.confirmed and
                             not candidate.playing_ack) else None

    def begin_vote(self, candidate_id: int, entry: tuple[str, str, str]) -> TrackCandidate:
        """Create C separately, leaving confirmed B and its cue untouched."""
        candidate = TrackCandidate.from_vote(candidate_id, entry)
        self.tentative = candidate
        return candidate

    def preserve_confirmed(self, candidate: TrackCandidate) -> None:
        if not candidate.confirmed or candidate.playing_ack:
            raise ValueError("Only confirmed, not-yet-playing tracks may be protected")
        self.confirmed_pending = candidate
        if self.tentative is candidate:
            self.tentative = None

    def supersede_with_confirmation(self, candidate_id: int) -> TrackCandidate | None:
        """A genuine two-vote C may supersede an unplayed B, never a first vote."""
        candidate = self.tentative
        if candidate is None or candidate.id != candidate_id:
            return None
        old = self.confirmed_pending
        if old is not None and old.id != candidate_id:
            if old.attempt:
                old.attempt.cancel()
            self.confirmed_pending = None
        candidate.confirmed = True
        # Confirmation also transfers SEARCH priority, independently of PLAY.
        # Mark a running B search cancelled so its delayed callback cannot
        # publish a result for the now-confirmed C track.
        for request in (self.active_search, self.queued_search,
                        self.speculative_search):
            if request is None or request.candidate_id is None:
                continue
            if request.candidate_id == candidate_id:
                request.confirmed = True
            elif request.confirmed and request.valid:
                request.cancel()
        if self.queued_search is not None and not self.queued_search.valid:
            self.queued_search = None
        if self.speculative_search is not None and not self.speculative_search.valid:
            self.speculative_search = None
        self.promote_speculative_search()
        # Keep the owner available to accept results even if the caller later
        # removes the preview row (e.g. autoplay disabled / another first vote).
        self.confirmed_search_owner = candidate
        return candidate

    def can_cue(self, candidate_id: int) -> bool:
        protected = self.confirmed_awaiting_play()
        return protected is None or protected.id == candidate_id

    def register_search(self, request: SearchRequest) -> None:
        self.active_search = request
        request.phase = SearchPhase.RUNNING
        if request.candidate_id is not None:
            candidate = self.find_candidate(request.candidate_id)
            if candidate is not None:
                candidate.search = request

    def queue_search(self, request: SearchRequest) -> None:
        """Protect queued confirmed B while staging tentative C separately.

        A manual search (candidate_id=None) and a *confirmed* C request are
        explicit overrides. Otherwise, only the newest first vote is staged.
        """
        incumbent = self.queued_search
        if (incumbent is not None and incumbent.valid and incumbent.confirmed
                and incumbent.candidate_id is not None
                and request.candidate_id is not None and not request.confirmed):
            if self.speculative_search is not None:
                self.speculative_search.cancel()
            self.speculative_search = request
        else:
            if incumbent is not None:
                incumbent.cancel()
            self.queued_search = request
            if request.confirmed or request.candidate_id is None:
                if self.speculative_search is not None:
                    self.speculative_search.cancel()
                self.speculative_search = None
        if request.candidate_id is not None:
            candidate = self.find_candidate(request.candidate_id)
            if candidate is not None:
                candidate.search = request

    def promote_speculative_search(self) -> None:
        """Expose C only when no protected B remains ahead of it."""
        if self.queued_search is not None and self.queued_search.valid:
            return
        self.queued_search = None
        request = self.speculative_search
        self.speculative_search = None
        if request is not None and request.valid:
            self.queued_search = request

    def pop_queued_search(self) -> SearchRequest | None:
        """Dispatch B first, then move tentative C into the next slot."""
        self.promote_speculative_search()
        request = self.queued_search
        self.queued_search = None
        self.promote_speculative_search()
        return request if request is not None and request.valid else None

    def reject_vote(self, candidate_id: int) -> TrackCandidate | None:
        candidate = self.tentative
        if candidate is None or candidate.id != candidate_id:
            return None
        self.tentative = None
        if candidate.search:
            candidate.search.cancel()
        if self.queued_search and self.queued_search.candidate_id == candidate_id:
            self.queued_search.cancel()
            self.queued_search = None
        if self.speculative_search and self.speculative_search.candidate_id == candidate_id:
            self.speculative_search.cancel()
            self.speculative_search = None
        self.promote_speculative_search()
        # The other candidate B owns the physical PRELOAD/PLAY slot.
        if (candidate.attempt is not None and
                self.active_attempt is candidate.attempt):
            candidate.attempt.cancel()
            self.active_attempt = None
        return candidate

    def begin_preload(self, candidate: TrackCandidate, video_id: str) -> PlaybackAttempt | None:
        """Reject a speculative C physical cue while B still owns nextPlayer."""
        if not self.can_cue(candidate.id):
            return None
        previous = self.active_attempt
        if previous is not None and previous is not candidate.attempt:
            # Starting a new *cue* never invalidates an already-playing song.
            if previous.phase != PlaybackPhase.PLAYING:
                previous.cancel()
        attempt = PlaybackAttempt(candidate_id=candidate.id, video_id=video_id)
        candidate.attempt = attempt
        self.active_attempt = attempt
        candidate.video_id = video_id
        candidate.ready = False
        candidate.play_sent = False
        return attempt

    def register_cue_request(self, video_id: str, request_id: str) -> None:
        attempt = self.active_attempt
        if not attempt or attempt.video_id != video_id:
            return
        candidate = self.find_candidate(attempt.candidate_id)
        if candidate is None or candidate.attempt is not attempt:
            return
        attempt.cue_requested(request_id)
        candidate.cue_request_id = request_id

    def register_play_request(self, video_id: str, request_id: str) -> None:
        attempt = self.active_attempt
        if not attempt or attempt.video_id != video_id:
            return
        candidate = self.find_candidate(attempt.candidate_id)
        if candidate is None or candidate.attempt is not attempt:
            return
        if not candidate.confirmed or not candidate.play_authorized:
            return
        attempt.play_requested(request_id)

    def accept_ready(self, video_id: str, request_id: str) -> TrackCandidate | None:
        attempt = self.active_attempt
        if attempt is None or not attempt.matches_preload(video_id, request_id):
            return None
        candidate = self.find_candidate(attempt.candidate_id)
        if candidate is None or candidate.attempt is not attempt:
            return None
        if attempt.mark_ready(video_id, request_id):
            candidate.ready = True
            return candidate
        return None

    def accept_playing(self, video_id: str, request_id: str) -> TrackCandidate | None:
        attempt = self.active_attempt
        if attempt is None or not attempt.matches_play(video_id, request_id):
            return None
        candidate = self.find_candidate(attempt.candidate_id)
        if candidate is None or candidate.attempt is not attempt or not candidate.confirmed:
            return None
        if attempt.mark_playing(video_id, request_id):
            candidate.playing_ack = True
            if self.confirmed_pending is candidate:
                self.confirmed_pending = None
            return candidate
        return None

    def close_attempt(self, candidate_id: int) -> None:
        attempt = self.active_attempt
        if attempt is not None and attempt.candidate_id == candidate_id:
            attempt.cancel()
            self.active_attempt = None


class ShazamStateMixin:
    """Legacy property names are single-source adapters, never shadow copies.

    MainWindow and the Qt-free method-extraction test harness both use this.
    """
    @property
    def shazam_flow(self) -> ShazamPlaybackState:
        if not hasattr(self, "_shazam_flow_state"):
            self._shazam_flow_state = ShazamPlaybackState()
        return self._shazam_flow_state

    @property
    def _shazam_preview(self) -> TrackCandidate | None:
        return self.shazam_flow.tentative

    @_shazam_preview.setter
    def _shazam_preview(self, candidate: TrackCandidate | None) -> None:
        self.shazam_flow.tentative = candidate

    @property
    def _confirmed_shazam_pending(self) -> TrackCandidate | None:
        return self.shazam_flow.confirmed_pending

    @_confirmed_shazam_pending.setter
    def _confirmed_shazam_pending(self, candidate: TrackCandidate | None) -> None:
        self.shazam_flow.confirmed_pending = candidate
