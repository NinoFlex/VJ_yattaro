"""MainWindow tie-up orchestration. No worker thread can mutate GUI/history.

Tie-up is optional metadata, never evidence for two-vote recognition. A job is
correlated to the actual candidate object, source generation and manual-action
epoch, not just seq (which is reused when Shazam restarts).
"""
from dataclasses import dataclass

from app.services.utanet_service import LookupResult, clean_text, track_key
from app.services.tieup_worker import TieupJob, TieupWorker


@dataclass
class TieupContext:
    entry: tuple
    candidate: object
    source_epoch: int
    recognition_generation: int
    interaction_epoch: int


class TieupEnrichmentMixin:
    def _init_tieup_enrichment(self):
        from PySide6.QtCore import QTimer
        self._tieup_epoch = 0
        self._tieup_interaction_epoch = 0
        self._tieup_contexts = {}
        self._tieup_requested_candidates = set()
        self._retired_youtube_threads = set()
        self._tieup_worker = TieupWorker()
        self._tieup_timer = QTimer(self)
        self._tieup_timer.setInterval(100)
        self._tieup_timer.timeout.connect(self._poll_tieup_results)
        self.tieup_enabled = bool(self.config_service.get("utanet_tieup_enabled", False)
                                  and self.config_service.get("utanet_permission_confirmed", False))
        self.title_bar.set_tieup_checked(self.tieup_enabled,
                                        enabled=self.source_mode == "shazam")

    def set_tieup_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled and not self.config_service.get("utanet_permission_confirmed", False):
            from PySide6.QtWidgets import QMessageBox
            text = ("\u6b4c\u30cd\u30c3\u30c8\u306e\u898f\u7d04\u306f\u7121\u65ad\u30b9\u30af\u30ec\u30a4\u30d4\u30f3\u30b0\u3092\u7981\u6b62\u3057\u3066\u3044\u307e\u3059\u3002\n"
                    "\u3053\u306e\u6a5f\u80fd\u306f\u3001\u904b\u55b6\u8005\u304b\u3089\u81ea\u52d5\u53d6\u5f97\u306e\u8a31\u8afe\u3092\u5f97\u3066\u3044\u308b\u5834\u5408\u306b\u5229\u7528\u3057\u3066\u304f\u3060\u3055\u3044\u3002\n"
                    "https://www.uta-net.com/info/kiyaku.html\n\n"
                    "\u8a31\u8afe\u53d6\u5f97\u6e08\u307f\u3068\u3057\u3066\u6709\u52b9\u306b\u3057\u307e\u3059\u304b\uff1f\n"
                    "\uff08\u300c\u306f\u3044\u300d\u306f\u8a31\u8afe\u81ea\u4f53\u3092\u53d6\u5f97\u3059\u308b\u64cd\u4f5c\u3067\u306f\u3042\u308a\u307e\u305b\u3093\uff09")
            answer = QMessageBox.question(self, "\u30bf\u30a4\u30a2\u30c3\u30d7\u88dc\u5b8c", text,
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                self.title_bar.set_tieup_checked(False, enabled=self.source_mode == "shazam")
                return
            if not self.config_service.save_config({"utanet_permission_confirmed": True}):
                self.title_bar.set_tieup_checked(False, enabled=self.source_mode == "shazam")
                return
        self.tieup_enabled = enabled
        self.config_service.save_config({"utanet_tieup_enabled": enabled})
        self.title_bar.set_tieup_checked(enabled, enabled=self.source_mode == "shazam")
        if not enabled:
            candidates = self._tieup_candidates()
            self._invalidate_tieup_requests()
            for candidate in candidates:
                candidate.tieup_state = "off"
                if candidate.manual_override or candidate.play_sent or candidate.playing_ack:
                    continue
                # An initial cue may already be ready. Turning OFF is explicit
                # permission to use it, not a fresh recognition confirmation.
                self._try_tieup_play(candidate)
                if not candidate.video_id and candidate.search and candidate.search.metadata_refresh:
                    self._preempt_initial_youtube_search(candidate.id, include_enriched=True)
                    self.search_youtube(candidate.entry[1], candidate.entry[2], "",
                        from_list=True, allow_auto_play=False, source_mode="shazam",
                        shazam_candidate_id=candidate.id)
        else:
            preview = getattr(self, "_shazam_preview", None)
            if preview is not None and self.source_mode == "shazam":
                self._request_tieup(preview.entry, preview)
        print(f"UtanetTieup: mode={'ON' if enabled else 'OFF'} transport=https")

    def _invalidate_tieup_requests(self):
        self._tieup_epoch = getattr(self, "_tieup_epoch", 0) + 1
        self._tieup_contexts.clear()
        self._tieup_requested_candidates.clear()
        self._tieup_worker.cancel_all()
        self._tieup_timer.stop()
        for request in (self.shazam_flow.active_search, self.shazam_flow.queued_search,
                        self.shazam_flow.speculative_search):
            if request is not None and request.metadata_refresh:
                request.cancel()
        self.shazam_flow.promote_speculative_search()
        self._sync_pending_search_args()

    def _tieup_manual_action(self):
        self._tieup_interaction_epoch = getattr(self, "_tieup_interaction_epoch", 0) + 1
        # Finishing a fetch may still annotate its history row. It may not
        # replace a user's manual search/selection or send PRELOAD/PLAY.
        for candidate in self._tieup_candidates():
            candidate.manual_override = True
        for request in (self.shazam_flow.active_search, self.shazam_flow.queued_search,
                        self.shazam_flow.speculative_search):
            if request is not None and (request.metadata_refresh or request.candidate_id is not None):
                request.cancel()
        self.shazam_flow.promote_speculative_search()
        self._sync_pending_search_args()

    def _tieup_candidates(self):
        result = []
        for candidate in (self.shazam_flow.tentative, self.shazam_flow.confirmed_pending,
                          self.shazam_flow.confirmed_search_owner):
            if candidate is not None and not any(c is candidate for c in result):
                result.append(candidate)
        return result

    def _prepare_tieup_candidate(self, candidate):
        candidate.tieup_generation = getattr(self.shazam_service, "_generation", 0)
        candidate.tieup_state = "pending" if self.tieup_enabled else "off"
        candidate.tieup_status = "pending" if self.tieup_enabled else "disabled"
        if candidate.tieup_state == "pending" and candidate.video_id:
            if getattr(self, "pending_play_video_id", None) == candidate.video_id:
                self.pending_play_video_id = None
            if getattr(self, "pending_auto_seek_video_id", None) == candidate.video_id:
                self.pending_auto_seek_video_id = None

    def _try_tieup_play(self, candidate):
        # This never supplies two-vote authorization itself.
        play = getattr(self, "_play_confirmed_shazam_preview_if_ready", None)
        if play is not None:
            play(candidate)

    def _preempt_initial_youtube_search(self, candidate_id, *, include_enriched=False):
        """Detach an obsolete worker immediately, without wait()/terminate().

        The HTTP request already on the wire may finish, but its signals cannot
        replace the new worker's results, busy state, error or pending timer.
        YouTubeService's registry and this set retain QThread lifetime.
        """
        flow = self.shazam_flow
        for attr in ("queued_search", "speculative_search"):
            request = getattr(flow, attr)
            if request is not None and request.candidate_id == candidate_id:
                request.cancel()
                setattr(flow, attr, None)
        active = flow.active_search
        superseded = (active is not None and active.candidate_id is not None
                      and (not active.valid or flow.find_candidate(active.candidate_id) is None))
        if (active is not None and (superseded or
                (active.candidate_id == candidate_id and (include_enriched or not active.metadata_refresh)))):
            active.cancel()
            worker = getattr(self, "youtube_search_thread", None)
            if worker is not None:
                if not hasattr(self, "_retired_youtube_threads"):
                    self._retired_youtube_threads = set()
                self._retired_youtube_threads.add(worker)
                worker.abort()
            self.youtube_search_thread = None
            self._active_search_candidate_id = None
            flow.active_search = None
            timer = getattr(self, "_pending_search_timer", None)
            if timer is not None:
                timer.stop()
            # Invalidate batched rows/thumbnails belonging to the old query.
            self._youtube_list_generation = getattr(self, "_youtube_list_generation", 0) + 1
            manager = getattr(self, "_thumbnail_manager", None)
            if manager is not None:
                manager.reset()
            print(f"UtanetTieup: preempted YouTube search firstSeq={candidate_id}; "
                  "old worker aborted, enrichment starts without waiting")
        flow.promote_speculative_search()
        self._sync_pending_search_args()

    def _prioritize_confirmed_tieup_search(self, candidate):
        """A newly confirmed winner need not await a superseded song's HTTP."""
        from app.models.search_request import SearchPhase
        request = candidate.search
        active = self.shazam_flow.active_search
        if (candidate.tieup_state != "found" or candidate.manual_override or
                request is None or not request.valid or not request.metadata_refresh or
                request.phase != SearchPhase.QUEUED):
            return
        if active is not None and active.valid and active.candidate_id != candidate.id:
            return  # Never override a still-valid manual/protected owner.
        self._preempt_initial_youtube_search(candidate.id)
        self.search_youtube(request.title, request.artist, request.comment,
            from_list=True, allow_auto_play=False, source_mode="shazam",
            shazam_candidate_id=candidate.id, _metadata_refresh=True)

    def _reset_initial_tieup_cue(self, candidate, *, force=False):
        """Invalidate initial cue ownership, not the physically playing song."""
        if candidate.cue_from_tieup and not force:
            return  # Duplicate metadata must never replay a winning query.
        attempt = candidate.attempt
        if attempt is not None and self.shazam_flow.active_attempt is attempt:
            self.shazam_flow.close_attempt(candidate.id)
            self._cancel_autoplay_recovery("tieup supersedes initial cue")
        elif attempt is not None:
            attempt.cancel()
        video_id = candidate.video_id
        if video_id:
            getattr(self, "_abandoned_preview_video_ids", set()).add(video_id)
            for attr in ("pending_play_video_id", "pending_auto_seek_video_id", "preloaded_video_id"):
                if getattr(self, attr, None) == video_id:
                    setattr(self, attr, None)
            if getattr(self, "_preload_watch_video_id", None) == video_id:
                self._clear_preload_watch()
        candidate.attempt = None
        candidate.video_id = None
        candidate.ready = candidate.play_sent = candidate.playing_ack = False
        candidate.cue_request_id = None
        candidate.deferred_videos = candidate.deferred_index = None
        candidate.deferred_from_tieup = False
        candidate.cue_from_tieup = False

    def _request_tieup(self, entry, candidate=None):
        # A protected, confirmed B's enriched lookup is essential, not optional.
        # A new tentative C must not cancel it. Obsolete owners are disposable.
        for pending in (self.shazam_flow.active_search, self.shazam_flow.queued_search,
                        self.shazam_flow.speculative_search):
            if pending is not None and pending.metadata_refresh:
                owner = self.shazam_flow.find_candidate(pending.candidate_id)
                if owner is None or (owner is not candidate and not owner.confirmed):
                    pending.cancel()
        self.shazam_flow.promote_speculative_search()
        self._sync_pending_search_args()
        if not getattr(self, "tieup_enabled", False) or self.source_mode != "shazam":
            return
        if len(entry) < 3:
            return
        request_key = (getattr(self.shazam_service, "_generation", 0),
                       candidate.id if candidate is not None else tuple(entry))
        if request_key in self._tieup_requested_candidates:
            return
        if candidate is not None:
            self._prepare_tieup_candidate(candidate)
        if not entry[1] or not entry[2]:
            if candidate is not None:
                candidate.tieup_state, candidate.tieup_status = "empty", "missing_identity"
                self._try_tieup_play(candidate)
            return
        if len(self._tieup_requested_candidates) >= 256:
            self._tieup_requested_candidates.clear()
        self._tieup_requested_candidates.add(request_key)
        job = TieupJob(str(entry[1]), str(entry[2]))
        context = TieupContext(tuple(entry), candidate, self._tieup_epoch,
            getattr(self.shazam_service, "_generation", 0), self._tieup_interaction_epoch)
        while len(self._tieup_contexts) >= 64:
            self._tieup_contexts.pop(next(iter(self._tieup_contexts)))
        self._tieup_contexts[job.token] = context
        if not self._tieup_worker.submit(job):
            self._on_tieup_result(job.token, LookupResult(job.title, job.artist,
                                                       status="unavailable", detail="worker closed"))
            return
        self._tieup_timer.start()
        print(f"UtanetTieup: search scheduled token={job.token[:8]} title={job.title!r} artist={job.artist!r}")

    def _poll_tieup_results(self):
        for token, result in self._tieup_worker.drain():
            self._on_tieup_result(token, result)
        if not self._tieup_contexts:
            self._tieup_timer.stop()

    def _on_tieup_result(self, token, result):
        context = self._tieup_contexts.pop(token, None)
        if context is None or not self.tieup_enabled or context.source_epoch != self._tieup_epoch:
            return
        if track_key(result.title, result.artist) != track_key(context.entry[1], context.entry[2]):
            print(f"UtanetTieup: discarded mismatched response token={token[:8]}")
            return
        print(f"UtanetTieup: result token={token[:8]} status={result.status} cached={result.cached} "
              f"title={result.title!r} comment={result.comment!r} detail={result.detail!r}")
        candidate = context.candidate
        generation_matches = context.recognition_generation == getattr(self.shazam_service, "_generation", 0)
        candidate_owned = (candidate is not None and generation_matches and
                           self.shazam_flow.find_candidate(candidate.id) is candidate)
        history = self.shazam_service.get_history()
        row_present = any(tuple(row) == context.entry for row in history)
        if not candidate_owned and not row_present and not (candidate is not None and candidate.committed):
            print(f"UtanetTieup: obsolete candidate token={token[:8]}; no row or search update")
            return
        # A protected confirmed candidate remains live until another TWO-vote
        # result supersedes it; it must not be lost to a mere next first vote.
        is_current = candidate_owned and (self._shazam_preview is candidate or
                                         self.shazam_flow.confirmed_pending is candidate)
        if candidate is None:
            is_current = (generation_matches and bool(history) and tuple(history[0]) == context.entry
                          and self._shazam_preview is None)
        may_select = (is_current and self.source_mode == "shazam" and
                      context.interaction_epoch == self._tieup_interaction_epoch and
                      not (candidate is not None and candidate.manual_override))
        comment = clean_text(result.comment) if result.status == "found" else ""
        if not comment:
            if candidate_owned:
                # A terminal failure is logged distinctly, but no usable comment
                # exists: release the initial cue instead of waiting forever.
                candidate.tieup_state, candidate.tieup_status = "empty", result.status
                print(f"UtanetTieup: initial playback selected firstSeq={candidate.id} "
                      f"reason={result.status}; still requires confirmation and ready")
                if may_select:
                    self._try_tieup_play(candidate)
            return
        old_comment = self.shazam_service.get_tieup_comment(result.title, result.artist)
        if old_comment != comment:
            self.shazam_service.set_tieup_comment(result.title, result.artist, comment, result.source_url)
        if candidate_owned:
            candidate.track_info["comment"] = comment
            candidate.tieup_state, candidate.tieup_status = "found", result.status
        model = getattr(self, "shazam_table_model", None)
        if model is not None and hasattr(model, "refresh_comments"):
            model.refresh_comments()
        current_info = getattr(self, "_current_track_info", {})
        if track_key(current_info.get("title"), current_info.get("artist")) == track_key(result.title, result.artist):
            self._current_track_info = {**current_info, "comment": comment}
        if not may_select:
            print(f"UtanetTieup: comment saved only token={token[:8]}; newer/manual owner retained")
            return
        if candidate_owned:
            if candidate.cue_from_tieup:
                return
            self._reset_initial_tieup_cue(candidate)
        print(f"UtanetTieup: enriched playback selected token={token[:8]}; initial PLAY suppressed")
        self.search_youtube(result.title, result.artist, comment, from_list=True,
            allow_auto_play=False, source_mode="shazam",
            shazam_candidate_id=candidate.id if candidate is not None else None,
            _metadata_refresh=True)

    def _resume_tieup_after_settings(self):
        """Resume invalidated work without replaying an already playing owner.

        A successful lookup is still valid when just the query template changes.
        Rebuild that query/cue instead of leaving a cancelled search marked ready.
        A device/lane restart changes recognition generation: old owners may not
        be resurrected under a reused sequence number.
        """
        if not self.tieup_enabled or self.source_mode != "shazam":
            return
        generation = getattr(self.shazam_service, "_generation", 0)
        candidates = sorted(self._tieup_candidates(), key=lambda c: not c.confirmed)
        for candidate in candidates:
            if (candidate.manual_override or candidate.play_sent or candidate.playing_ack
                    or candidate.tieup_generation not in (None, generation)):
                continue
            comment = clean_text(candidate.track_info.get("comment", ""))
            if candidate.tieup_state == "found" and comment:
                self._reset_initial_tieup_cue(candidate, force=True)
                self._preempt_initial_youtube_search(candidate.id, include_enriched=True)
                self._tieup_requested_candidates.add((generation, candidate.id))
                print(f"UtanetTieup: settings resume enriched query firstSeq={candidate.id}")
                self.search_youtube(candidate.entry[1], candidate.entry[2], comment,
                    from_list=True, allow_auto_play=False, source_mode="shazam",
                    shazam_candidate_id=candidate.id, _metadata_refresh=True)
            else:
                self._request_tieup(candidate.entry, candidate)
