import json
import math
import queue
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from app.services.config_service import SHAZAM_DEFAULT_LANES
from app.services.track_matching import deduplicate_history, is_same_track, shares_title_prefix
from app.services.title_keywords import CONFIG_KEY, normalize_keywords, strip_title_keywords, title_variant_matches
from app.services.itunes_metadata import clean_artist
from app.services.shazam_lane_health import (
    LaneHealth, LanePhase, LaneStartupGate, lane_failure_reason,
)
from app.services.shazam_metadata_prefetch import (
    MetadataPrefetcher, EarlyIdentityProbe, IdentityConflict, complete_metadata, track_identity,
)


class ShazamService(QObject):
    """Microphone -> live PCM fanout -> Shazam recognition service.

    One PortAudio input stream is captured continuously and fanned out to 4-16 independent
    WebView2 workers only while each lane is recognizing. New recognition starts are spaced
    by at least three seconds. If all workers are busy, the due slot is held instead of
    discarded and starts as soon as one worker becomes free. Each WebView therefore receives
    fresh microphone PCM continuously after its own start time instead of a looping WAV
    snapshot. A track needs two independent, time-separated recognitions. Matching
    Shazam IDs may bridge at most two completed neutral gaps within 15 seconds.
    A neutral gap is either a soft no-match or, at most once, the specific WebView audio
    transport failure where the Shazam button did not request the supplied audio. Other
    errors, rejected evidence, other songs and unknown intervening results break the chain.
    ID-less/Apple-only observations keep the consecutive-results rule. The
    existing title-prefix rule is retained only when neither observation has an ID.
    No ShazamIO library or private
    Shazam API is used.
    """

    history_updated = Signal(list)
    new_track_detected = Signal(tuple)
    # A candidate is a UI-only preview until two independent observations agree.
    # The first observation's request sequence is the stable preview token.
    candidate_started = Signal(int, tuple)
    candidate_rejected = Signal(int, str)
    candidate_confirmed = Signal(int, tuple)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    _recognition_finished = Signal(int, int, int, str, str, str)
    _metadata_finished = Signal(int, int, int, str, str, str)
    _prefetch_finished = Signal(int, object, object, str)

    SAMPLE_RATE = 16000
    CHANNELS = 1  # preferred minimum; stereo-capable endpoints are captured as 2ch and downmixed
    MAX_CAPTURE_CHANNELS = 2
    DTYPE = "int16"
    AGC_TARGET_RMS = 0.08
    AGC_MAX_GAIN = 32.0
    AGC_MIN_GAIN = 0.5
    AGC_NOISE_GATE_RMS = 0.00025
    RECOGNITION_INTERVAL_MS = 100
    RECOGNITION_GROUPS = SHAZAM_DEFAULT_LANES
    LANES_PER_GROUP = 1
    PARALLEL_RECOGNITION_LANES = RECOGNITION_GROUPS * LANES_PER_GROUP
    GROUP_STAGGER_SECONDS = 3.0
    LANE_STAGGER_SECONDS = 0.0
    HISTORY_LIMIT = 50
    # Measured using local monotonic result-receipt times, never DB completion
    # or queue-drain time. No-match events do not renew this window.
    SHAZAM_NO_MATCH_WINDOW_SECONDS = 15.0
    SHAZAM_NO_MATCH_MAX_GAPS = 2
    # Conservative extension: one known WebView audio-supply failure may be neutral,
    # but the total number of bridged gaps remains bounded by SHAZAM_NO_MATCH_MAX_GAPS.
    SHAZAM_TRANSPORT_ERROR_MAX_GAPS = 1
    SHAZAM_AUDIO_REQUEST_ERROR_MARKER = "the shazam button did not request the supplied audio"
    # A failed transport already closes its helper. Every such lane must be
    # re-prepared before scheduling, not only after two audio-supply errors.
    LANE_INIT_MAX_CONCURRENT = 2
    LANE_INIT_INTERVAL_SECONDS = 0.5
    LANE_RECYCLE_RETRY_DELAYS_SECONDS = (2.0, 5.0, 10.0, 20.0, 30.0)

    def __init__(self, parent=None):
        super().__init__(parent)
        from app.services.config_service import ConfigService

        self.config = ConfigService()
        from app.services.config_service import normalize_shazam_lane_count
        lane_count = normalize_shazam_lane_count(self.config.get("shazam_lane_count", SHAZAM_DEFAULT_LANES))
        # Class-level constants use the shared default; each instance overrides
        # the active worker count with a persisted 4..16 selection.
        self.RECOGNITION_GROUPS = lane_count
        self.PARALLEL_RECOGNITION_LANES = lane_count * self.LANES_PER_GROUP
        # Leave retired worker threads parked after a reduction. stop() closes
        # their WebView2 processes, and the dispatcher cannot select them.
        # Retaining their slots prevents races with late cancelled callbacks.
        self._pool_capacity = self.PARALLEL_RECOGNITION_LANES
        self._lane_state_lock = threading.RLock()
        # Do not replace this gate at restart: old cancelled workers must release
        # their slots before the new generation may consume them.
        self._lane_startup_gate = LaneStartupGate(
            self.LANE_INIT_MAX_CONCURRENT, self.LANE_INIT_INTERVAL_SECONDS)
        self._lane_health = [LaneHealth() for _ in range(self._pool_capacity)]
        self._capture_sample_rate = self.SAMPLE_RATE
        self._capture_channels = self.CHANNELS
        self._agc_gain = 1.0
        self._last_audio_level_log = 0.0

        self._stream = None
        self._active = False
        self._recognition_busy = False
        self._generation = 0
        self._last_track = None
        self._last_published_identity = None
        # Populated immediately before history/new_track Qt signals. UI must
        # associate a publication with THIS confirmed candidate, not a stale one.
        self._last_publication_candidate_id = None
        self._confirmed_search_receipt = None
        self._last_publication_search_reused = False

        from app.services.shazam_webview_client import WebViewRecognizer
        from app.services.itunes_metadata import ITunesMetadataResolver
        self._work_queues = [queue.Queue(maxsize=1) for _ in range(self.PARALLEL_RECOGNITION_LANES)]
        self._worker_threads = [None] * self.PARALLEL_RECOGNITION_LANES
        self._web_recognizers = [
            WebViewRecognizer(f"lane-{index + 1}") for index in range(self.PARALLEL_RECOGNITION_LANES)
        ]
        for recognizer in self._web_recognizers:
            recognizer.require_prewarm = True
        # Legacy ID-less results retain their original resolver path. ID-bearing
        # observations use bounded per-ID prefetch, independent of confirmation.
        self._metadata_resolver = ITunesMetadataResolver()
        self._metadata_lock = threading.Lock()
        self._metadata_prefetch = MetadataPrefetcher()
        self._pending_identity_confirmation = None
        self._newest_temporal_observation = None
        self._lane_busy = [False] * self._pool_capacity
        self._lane_ready = [threading.Event() for _ in range(self.PARALLEL_RECOGNITION_LANES)]
        self._lane_audio_request_failure_streak = [0] * self._pool_capacity
        self._lane_recycling = [False] * self._pool_capacity
        self._lane_recycle_count = [0] * self._pool_capacity
        self._group_busy = [False] * self._pool_capacity
        # _next_group_index is the round-robin search start for the shared worker pool.
        # _next_group_slot_at is the earliest time at which another recognition may start.
        self._next_group_index = 0
        self._next_group_slot_at = 0.0
        self._slot_wait_started_at = None
        self._slot_wait_logged = False
        self._request_sequence = 0
        self._pending_group_results = {}
        self._raw_lane_results = {}
        self._latest_published_sequence = -1
        self._temporal_results = {}
        self._next_temporal_sequence = 0
        self._last_temporal_observation = None
        self._tentative_candidate = None
        self._metadata_raw_results = {}
        self._cancel_current = threading.Event()
        self._shutting_down = False

        self._recognize_timer = QTimer(self)
        self._recognize_timer.setInterval(self.RECOGNITION_INTERVAL_MS)
        self._recognize_timer.timeout.connect(self._recognize_tick)
        self._recognition_finished.connect(self._handle_recognition_finished)
        self._metadata_finished.connect(self._handle_metadata_finished)
        self._prefetch_finished.connect(self._handle_prefetch_finished)
        self._tentative_timer = QTimer(self)
        self._tentative_timer.setSingleShot(True)
        self._tentative_timer.timeout.connect(self._expire_tentative_candidate)

        self._history_path = self._get_history_path()
        self._ensure_history_file()
        self._history_comments = {}
        self._history = self._load_history()
        self._recognition_settings = self._recognition_settings_key()

    @classmethod
    def list_input_devices(cls):
        """Return [(device_index, display_name), ...] for input-capable devices.

        PortAudio exposes the same Windows endpoint through several host APIs
        (MME/DirectSound/WASAPI/WDM-KS).  Query devices one-by-one from each
        host API so that one broken endpoint does not make the whole list fail,
        then de-duplicate Windows devices while preferring WASAPI.
        """
        try:
            import platform
            import sounddevice as sd

            try:
                hostapis = tuple(sd.query_hostapis())
            except Exception as e:
                return [], f"PortAudio host API query failed: {e}"

            default_input = -1
            try:
                default_input = int(sd.default.device[0])
            except Exception:
                pass

            candidates = []
            query_errors = []
            seen_ids = set()

            # query_hostapis() gives us device IDs without forcing a query of
            # every device.  Query each endpoint separately and skip only the
            # endpoint that fails.
            for hostapi_index, hostapi in enumerate(hostapis):
                hostapi_name = str(hostapi.get("name", f"Host API {hostapi_index}"))
                for device_index in hostapi.get("devices", []):
                    try:
                        device_index = int(device_index)
                    except (TypeError, ValueError):
                        continue
                    if device_index in seen_ids:
                        continue
                    seen_ids.add(device_index)

                    try:
                        device = sd.query_devices(device_index)
                        max_inputs = int(device.get("max_input_channels", 0))
                        if max_inputs <= 0:
                            continue
                        name = str(device.get("name", f"Device {device_index}")).strip()
                        if not name:
                            name = f"Device {device_index}"
                        candidates.append({
                            "index": device_index,
                            "name": name,
                            "hostapi": hostapi_name,
                            "default": device_index == default_input,
                        })
                    except Exception as e:
                        query_errors.append(f"#{device_index} [{hostapi_name}]: {e}")

            if platform.system() == "Windows":
                # Prefer user-facing Windows endpoints and avoid showing the
                # same microphone four times.  WDM-KS is kept as the final
                # fallback because it is a low-level API and often duplicates
                # WASAPI/MME devices.
                def host_priority(name):
                    name = name.lower()
                    if "wasapi" in name:
                        return 0
                    if "mme" in name:
                        return 1
                    if "directsound" in name:
                        return 2
                    if "asio" in name:
                        return 3
                    if "wdm" in name or "ks" in name:
                        return 4
                    return 5

                def normalize_name(name):
                    # PortAudio's MME device names can be truncated, therefore
                    # only de-duplicate exact normalized names.  Different names
                    # remain visible so a device is never hidden accidentally.
                    return " ".join(name.casefold().split())

                candidates.sort(key=lambda x: (host_priority(x["hostapi"]), x["index"]))
                unique = []
                seen_names = set()
                for item in candidates:
                    key = normalize_name(item["name"])
                    if key in seen_names:
                        continue
                    seen_names.add(key)
                    unique.append(item)
                candidates = unique
            else:
                candidates.sort(key=lambda x: x["index"])

            result = []
            for item in candidates:
                display = f'{item["name"]} [{item["hostapi"]}]'
                if item["default"]:
                    display += " (既定)"
                result.append((item["index"], display))

            if result:
                # Partial endpoint failures should not hide the usable list.
                # Keep the UI clean; details are still printed for diagnostics.
                if query_errors:
                    print("ShazamService: Some audio devices could not be queried:")
                    for error in query_errors:
                        print(f"  {error}")
                return result, ""

            detail = "入力可能なPortAudioデバイスが見つかりません。"
            if query_errors:
                detail += " " + " / ".join(query_errors[:3])
            try:
                pa_version = sd.get_portaudio_version()
                detail += f" / PortAudio: {pa_version}"
            except Exception:
                pass
            return [], detail
        except Exception as e:
            return [], f"sounddevice/PortAudio initialization failed: {e}"

    def _sync_lane_count(self):
        """Apply the persisted 4..16 count between stop() and start().

        Increase capacity on demand; park higher-index workers on decrease.
        Parked workers cannot dispatch or prewarm and have no live WebView2
        helper. Worker slots remain valid for cancelled/late callbacks.
        """
        from app.services.config_service import normalize_shazam_lane_count
        from app.services.shazam_webview_client import WebViewRecognizer

        count = normalize_shazam_lane_count(self.config.get("shazam_lane_count", SHAZAM_DEFAULT_LANES))
        required_capacity = count * self.LANES_PER_GROUP
        if required_capacity > self._pool_capacity:
            for index in range(self._pool_capacity, required_capacity):
                self._work_queues.append(queue.Queue(maxsize=1))
                self._worker_threads.append(None)
                recognizer = WebViewRecognizer(f"lane-{index + 1}")
                recognizer.require_prewarm = True
                self._web_recognizers.append(recognizer)
                self._lane_health.append(LaneHealth())
                self._lane_busy.append(False)
                self._lane_ready.append(threading.Event())
                self._lane_audio_request_failure_streak.append(0)
                self._lane_recycling.append(False)
                self._lane_recycle_count.append(0)
                self._group_busy.append(False)
            self._pool_capacity = required_capacity
        self.RECOGNITION_GROUPS = count
        self.PARALLEL_RECOGNITION_LANES = required_capacity

    def get_history(self):
        return list(self._history)

    def is_active(self):
        return self._active

    def start(self):
        if self._shutting_down:
            return False
        if self._active:
            return True
        self._recognition_settings = self._recognition_settings_key()

        try:
            import sounddevice as sd

            self._check_shazam_runtime_dependencies()

            device = self.config.get("shazam_input_device", None)
            if device in ("", -1):
                device = None
            elif device is not None:
                device = int(device)

            capture_rate, capture_channels = self._select_capture_format(sd, device)
            self._capture_sample_rate = int(capture_rate)
            self._capture_channels = int(capture_channels)
            self._agc_gain = 1.0
            self._last_audio_level_log = 0.0

            # Reload lane count after microphone validation; no new workers on
            # a failed Shazam start.
            self._sync_lane_count()
            self._ensure_worker_threads()
            with self._lane_state_lock:
                self._generation += 1
                self._cancel_current = threading.Event()
                self._lane_health = [LaneHealth() for _ in range(self._pool_capacity)]
            self._recognition_busy = False
            self._lane_busy = [False] * self._pool_capacity
            self._lane_audio_request_failure_streak = [0] * self._pool_capacity
            self._lane_recycling = [False] * self._pool_capacity
            self._lane_recycle_count = [0] * self._pool_capacity
            self._group_busy = [False] * self._pool_capacity
            self._next_group_index = 0
            self._next_group_slot_at = 0.0
            self._slot_wait_started_at = None
            self._slot_wait_logged = False
            self._request_sequence = 0
            self._pending_group_results.clear()
            self._raw_lane_results.clear()
            self._latest_published_sequence = -1
            self._last_publication_candidate_id = None
            self._confirmed_search_receipt = None
            self._last_publication_search_reused = False
            self._temporal_results.clear()
            self._reject_tentative_candidate("shazam-restarted")
            self._next_temporal_sequence = 0
            self._last_temporal_observation = None
            self._metadata_raw_results.clear()
            self._pending_identity_confirmation = None
            self._newest_temporal_observation = None
            self._metadata_prefetch.discard_cancelled()
            for ready in self._lane_ready:
                ready.clear()
            self._stream = sd.InputStream(
                device=device,
                samplerate=capture_rate,
                channels=self._capture_channels,
                dtype=self.DTYPE,
                callback=self._audio_callback,
                blocksize=0,
            )
            self._stream.start()
            with self._lane_state_lock:
                self._active = True
                for health in self._lane_health[:self.PARALLEL_RECOGNITION_LANES]:
                    health.phase = LanePhase.QUEUED
            # Queue all selected lanes, but the common startup gate admits only
            # two initializations at once. Recognition may use each successful lane
            # immediately, without waiting for the rest of the pool.
            language = str(self.config.get("shazam_language", "ja-JP") or "ja-JP")
            for lane_index, work_queue in enumerate(self._work_queues[:self.PARALLEL_RECOGNITION_LANES]):
                # A quick settings reload can leave last generation's request in a
                # single-slot queue. Drop it before enqueueing THIS generation's
                # prewarm; otherwise queue.Full incorrectly marked a cold lane ready.
                try:
                    while True:
                        work_queue.get_nowait()
                except queue.Empty:
                    pass
                work_queue.put_nowait(("prewarm", self._generation, language, self._cancel_current))
            self._recognize_timer.start()
            self.status_changed.emit("Shazam: live microphone capture started")
            try:
                info = sd.query_devices(device, "input") if device is not None else sd.query_devices(kind="input")
                device_name = str(info.get("name", "system-default"))
                hostapi_index = int(info.get("hostapi", -1))
                hostapis = sd.query_hostapis()
                hostapi_name = (str(hostapis[hostapi_index].get("name", ""))
                                if 0 <= hostapi_index < len(hostapis) else "")
            except Exception:
                device_name, hostapi_name = "unknown", ""
            print(
                f"ShazamService: Started (device={device}, name={device_name!r}, hostapi={hostapi_name!r}, "
                f"capture={capture_rate}Hz/{capture_channels}ch/int16->mono, input=live-stream-agc, "
                f"laneCount={self.PARALLEL_RECOGNITION_LANES}, "
                f"laneInterval={self.GROUP_STAGGER_SECONDS:.1f}s, metadata=identity-prefetch-v1, "
                f"confirmation=shazam-neutral-gap-v2 window={self.SHAZAM_NO_MATCH_WINDOW_SECONDS:.1f}s "
                f"maxNeutral={self.SHAZAM_NO_MATCH_MAX_GAPS} "
                f"maxTransportError={self.SHAZAM_TRANSPORT_ERROR_MAX_GAPS}, "
                f"publication=confirmed-neutral-v1, "
                f"laneStartup=staged-v1 initMax={self.LANE_INIT_MAX_CONCURRENT} "
                f"initSpacing={self.LANE_INIT_INTERVAL_SECONDS:.1f}s "
                f"laneRecovery=transport-backoff-v1)"
            )
            return True
        except Exception as e:
            # Also cancel queued/running prewarms if startup fails after capture.
            self.stop()
            message = f"Shazam microphone start failed: {e}"
            self.error_occurred.emit(message)
            self.status_changed.emit(message)
            print(f"ShazamService: {message}")
            return False

    def stop(self):
        # Invalidate under the same lock used when publishing READY/replacements.
        # Nothing from an old generation can mark a freshly restarted lane ready.
        with self._lane_state_lock:
            self._cancel_current.set()
            self._generation += 1
            self._active = False
            for ready, health in zip(self._lane_ready, self._lane_health):
                ready.clear()
                health.phase = LanePhase.STOPPED
            recognizers = list(self._web_recognizers)
        for pending in list(self._pending_group_results.values()):
            timer = pending.get("confirmation_timer")
            if timer is not None:
                timer.stop()
                if hasattr(timer, "deleteLater"):
                    timer.deleteLater()
        for recognizer in recognizers:
            recognizer.close()
        self._recognize_timer.stop()
        self._close_stream()
        self._recognition_busy = False
        self._lane_busy = [False] * self._pool_capacity
        self._lane_audio_request_failure_streak = [0] * self._pool_capacity
        self._lane_recycling = [False] * self._pool_capacity
        self._group_busy = [False] * self._pool_capacity
        self._slot_wait_started_at = None
        self._slot_wait_logged = False
        self._pending_group_results.clear()
        self._raw_lane_results.clear()
        self._temporal_results.clear()
        self._reject_tentative_candidate("shazam-stopped")
        self._last_temporal_observation = None
        self._metadata_raw_results.clear()
        self._pending_identity_confirmation = None
        self._newest_temporal_observation = None
        self._last_publication_candidate_id = None
        self._confirmed_search_receipt = None
        self._last_publication_search_reused = False
        self._metadata_prefetch.discard_cancelled()
        for ready in self._lane_ready:
            ready.clear()
        self.status_changed.emit("Shazam: stopped")
        print("ShazamService: Stopped")

    def _recognition_settings_key(self):
        from app.services.config_service import normalize_shazam_lane_count
        return (self.config.get("shazam_input_device"),
                normalize_shazam_lane_count(self.config.get("shazam_lane_count", SHAZAM_DEFAULT_LANES)),
                self.config.get("shazam_language", "ja-JP"),
                self.config.get("shazam_endpoint_country", "JP"))

    def reload_settings(self):
        """Keyword-only edits must not cancel cues or restart recognition."""
        settings = self._recognition_settings_key()
        changed = settings != self._recognition_settings
        self._recognition_settings = settings
        if self._active and changed:
            self.stop()
            self.start()

    def shutdown(self):
        self._shutting_down = True
        self.stop()
        self._metadata_prefetch.close()
        # Drop queued (not yet started) recordings and wake all idle workers.
        for work_queue in self._work_queues:
            try:
                while True:
                    work_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                work_queue.put_nowait(None)
            except queue.Full:
                pass

    def _ensure_worker_threads(self):
        for lane_index in range(self.PARALLEL_RECOGNITION_LANES):
            thread = self._worker_threads[lane_index]
            if thread is not None and thread.is_alive():
                continue
            # A lane owns one queue and one persistent WebView2 helper. Do not share
            # stdin/stdout between lanes; each helper stays strictly serial internally.
            if thread is not None:
                self._work_queues[lane_index] = queue.Queue(maxsize=1)
            thread = threading.Thread(
                target=self._worker_main,
                args=(lane_index,),
                name=f"ShazamRecognitionWorker-{lane_index + 1}",
                daemon=True,
            )
            self._worker_threads[lane_index] = thread
            thread.start()

    @classmethod
    def _is_lane_audio_request_failure(cls, error_text):
        return cls.SHAZAM_AUDIO_REQUEST_ERROR_MARKER in str(error_text or "").casefold()

    def _lane_run_current(self, lane_index, generation, cancelled):
        """Call under _lane_state_lock when publishing shared lifecycle state."""
        return (not cancelled.is_set() and not self._shutting_down
                and self._active and generation == self._generation
                and 0 <= lane_index < self.PARALLEL_RECOGNITION_LANES)

    def _lane_retry_delay(self, failure_count):
        delays = self.LANE_RECYCLE_RETRY_DELAYS_SECONDS
        if failure_count <= 0 or not delays:
            return 0.0
        return float(delays[min(failure_count - 1, len(delays) - 1)])

    def _lane_health_summary(self):
        with self._lane_state_lock:
            phases = [health.phase for health in
                      self._lane_health[:self.PARALLEL_RECOGNITION_LANES]]
            return (f"initializing={phases.count(LanePhase.INITIALIZING)} "
                    f"queued={phases.count(LanePhase.QUEUED)} "
                    f"retryWait={phases.count(LanePhase.RETRY_WAIT)}")

    def _record_lane_health(self, lane_index, error_text, *, generation=None,
                            cancelled=None, error_type="RuntimeError"):
        """Quarantine ALL failed transports before the result frees their busy slot.

        A normal no-match is a successful transport and does not restart a lane.
        This lifecycle policy does not relax or change track confirmation rules.
        """
        with self._lane_state_lock:
            generation = self._generation if generation is None else generation
            cancelled = self._cancel_current if cancelled is None else cancelled
            if not self._lane_run_current(lane_index, generation, cancelled):
                return False
            health = self._lane_health[lane_index]
            if not error_text:
                self._lane_audio_request_failure_streak[lane_index] = 0
                health.failures = 0
                health.last_error_type = ""
                health.last_failure_reason = ""
                health.phase = LanePhase.READY
                return False
            if self._is_lane_audio_request_failure(error_text):
                self._lane_audio_request_failure_streak[lane_index] += 1
            else:
                self._lane_audio_request_failure_streak[lane_index] = 0
            health.failures += 1
            health.last_error_type = error_type
            health.last_failure_reason = lane_failure_reason(error_text)
            health.phase = LanePhase.RETRY_WAIT
            self._lane_ready[lane_index].clear()
            self._lane_recycling[lane_index] = True
            delay = self._lane_retry_delay(health.failures)
            # No error body/URL/credentials here. The existing result log carries
            # its exception text. This is one compact extra lifecycle line.
            print(f"ShazamService: Lane retry lane={lane_index + 1} gen={generation} "
                  f"stage=recognize failures={health.failures} error={error_type} "
                  f"reason={health.last_failure_reason} retryIn={delay:.1f}s")
            return True

    def _prepare_lane_recognizer(self, lane_index, recognizer, generation,
                                 language, cancelled, *, replace=False, recovery=False):
        """Cancellable startup/recovery; only successful prewarm publishes READY.

        The initialization slot is released before every retry delay. Calls from
        initial startup and from failed recognitions use this same FIFO gate.
        Each lane has only one worker, so replacement/cleanup cannot overlap the
        next request on that lane. All publications are generation-guarded.
        """
        from app.services.shazam_webview_client import WebViewRecognizer, RecognitionCancelled

        current = recognizer
        while True:
            with self._lane_state_lock:
                if not self._lane_run_current(lane_index, generation, cancelled):
                    return current
                health = self._lane_health[lane_index]
                self._lane_ready[lane_index].clear()
                delay = self._lane_retry_delay(health.failures)
                health.phase = LanePhase.RETRY_WAIT if delay else LanePhase.QUEUED
            if delay and cancelled.wait(delay):
                return current
            with self._lane_state_lock:
                if not self._lane_run_current(lane_index, generation, cancelled):
                    return current
                health.phase = LanePhase.QUEUED
            if not self._lane_startup_gate.acquire(cancelled):
                return current
            started = time.monotonic()
            failure = None
            try:
                with self._lane_state_lock:
                    if not self._lane_run_current(lane_index, generation, cancelled):
                        return current
                    health.phase = LanePhase.INITIALIZING
                    health.attempts += 1
                    if replace:
                        current = WebViewRecognizer(f"lane-{lane_index + 1}")
                        current.require_prewarm = True
                        self._web_recognizers[lane_index] = current
                    current.require_prewarm = True
                current.prewarm(language, cancelled)
                with self._lane_state_lock:
                    if not self._lane_run_current(lane_index, generation, cancelled):
                        current.close()
                        return current
                    # Keep the failure count until one recognition completes: a
                    # helper that repeatedly starts then crashes must back off too.
                    health.phase = LanePhase.READY
                    self._lane_recycling[lane_index] = False
                    if recovery:
                        self._lane_recycle_count[lane_index] += 1
                    self._lane_ready[lane_index].set()
                    ready_count = sum(event.is_set() for event in
                                      self._lane_ready[:self.PARALLEL_RECOGNITION_LANES])
                    elapsed_ms = round((time.monotonic() - started) * 1000)
                    print(f"ShazamService: Lane ready lane={lane_index + 1} gen={generation} "
                          f"attempt={health.attempts} elapsedMs={elapsed_ms} "
                          f"poolReady={ready_count}/{self.PARALLEL_RECOGNITION_LANES} "
                          f"recovered={recovery}")
                return current
            except RecognitionCancelled:
                current.close()
                with self._lane_state_lock:
                    if not self._lane_run_current(lane_index, generation, cancelled):
                        return current
                # Unexpected cancellation with a live generation is a lost helper,
                # not a user stop. Recover rather than leave the lane stuck busy.
                failure = "RecognitionCancelled"
                reason = "helper-unavailable"
            except Exception as exc:
                current.close()
                failure = type(exc).__name__
                reason = lane_failure_reason(exc)
            finally:
                self._lane_startup_gate.release()
            with self._lane_state_lock:
                if not self._lane_run_current(lane_index, generation, cancelled):
                    return current
                health.failures += 1
                health.last_error_type = failure
                health.last_failure_reason = reason
                health.phase = LanePhase.RETRY_WAIT
                self._lane_recycling[lane_index] = True
                delay = self._lane_retry_delay(health.failures)
                print(f"ShazamService: Lane retry lane={lane_index + 1} gen={generation} "
                      f"stage=prewarm attempt={health.attempts} error={failure} reason={reason} "
                      f"retryIn={delay:.1f}s elapsedMs={round((time.monotonic() - started) * 1000)}")
            replace, recovery = True, True

    def _recycle_lane_recognizer(self, lane_index, recognizer, generation, language, cancelled):
        """Close a failed helper and rejoin bounded preparation, off the Qt thread."""
        try:
            recognizer.close()
        except Exception:
            pass
        return self._prepare_lane_recognizer(
            lane_index, recognizer, generation, language, cancelled,
            replace=True, recovery=True)

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"ShazamService: Audio status: {status}")
        if not self._active:
            return

        # Browser getUserMedia normally applies microphone processing such as automatic
        # gain control. Our app-supplied MediaStream bypasses that path, so raw PortAudio
        # levels can be 20-40 dB quieter than shazam.com sees from a real browser mic.
        # Capture up to stereo, downmix, then apply a conservative smoothed AGC before
        # fanning the same live PCM to all recognizing lanes.
        try:
            samples = indata.astype("float32", copy=False) / 32768.0
            if getattr(samples, "ndim", 1) > 1:
                mono = samples.mean(axis=1)
            else:
                mono = samples

            if len(mono):
                raw_peak = float(abs(mono).max())
                raw_rms = float(math.sqrt(float((mono * mono).mean())))
            else:
                raw_peak = raw_rms = 0.0

            if raw_rms >= self.AGC_NOISE_GATE_RMS:
                desired = self.AGC_TARGET_RMS / max(raw_rms, 1e-9)
                desired = max(self.AGC_MIN_GAIN, min(self.AGC_MAX_GAIN, desired))
                # Raise quiet audio reasonably quickly, reduce gain more gently to avoid
                # audible pumping between consecutive PortAudio blocks.
                alpha = 0.20 if desired > self._agc_gain else 0.06
                self._agc_gain += (desired - self._agc_gain) * alpha
            else:
                # Do not crank silence/noise to maximum gain. Slowly return toward unity.
                self._agc_gain += (1.0 - self._agc_gain) * 0.02

            processed = mono * self._agc_gain
            processed = processed.clip(-0.98, 0.98)
            if raw_rms < self.AGC_NOISE_GATE_RMS * 0.25:
                processed = processed * 0.0
            out_peak = float(abs(processed).max()) if len(processed) else 0.0
            out_rms = float(math.sqrt(float((processed * processed).mean()))) if len(processed) else 0.0
            pcm_bytes = (processed * 32767.0).astype("<i2").tobytes()

            now = time.monotonic()
            if now - self._last_audio_level_log >= 3.0:
                self._last_audio_level_log = now
                print(
                    "ShazamService: Audio level "
                    f"rawPeak={raw_peak:.4f} rawRms={raw_rms:.4f} "
                    f"agcGain={self._agc_gain:.2f} outPeak={out_peak:.4f} outRms={out_rms:.4f}"
                )
        except Exception as exc:
            # Keep audio callback failures observable without bringing PortAudio down.
            print(f"ShazamService: Audio preprocessing failed: {exc}")
            return

        for recognizer in self._web_recognizers[:self.PARALLEL_RECOGNITION_LANES]:
            try:
                recognizer.feed_live_audio(pcm_bytes, self._capture_sample_rate)
            except Exception:
                # Recognition workers own IPC failures. Never log/block from PortAudio's
                # real-time callback if one helper is being restarted.
                pass

    @staticmethod
    def _check_shazam_runtime_dependencies():
        from app.services.shazam_webview_client import WebViewRecognizer
        WebViewRecognizer.check_available()

    @classmethod
    def _select_capture_format(cls, sd, device):
        """Return (sample_rate, channels) accepted by the selected input endpoint.

        Windows endpoints are not guaranteed to accept mono even when they expose input.
        Prefer stereo when available so system/loopback-style sources cannot lose a signal
        that lives on only one side; the callback safely downmixes to mono for Shazam.
        """
        try:
            info = sd.query_devices(device, "input") if device is not None else sd.query_devices(kind="input")
        except Exception:
            info = {}
        try:
            max_inputs = max(1, int(info.get("max_input_channels", cls.CHANNELS) or cls.CHANNELS))
        except Exception:
            max_inputs = cls.CHANNELS
        channel_candidates = []
        if max_inputs >= 2:
            channel_candidates.append(min(cls.MAX_CAPTURE_CHANNELS, max_inputs))
        if 1 not in channel_candidates:
            channel_candidates.append(1)

        rates = [cls.SAMPLE_RATE]
        try:
            default_rate = int(round(float(info.get("default_samplerate", 0) or 0)))
            if default_rate > 0 and default_rate not in rates:
                rates.append(default_rate)
        except Exception:
            pass
        for fallback in (48000, 44100, 32000):
            if fallback not in rates:
                rates.append(fallback)

        errors = []
        for channels in channel_candidates:
            for rate in rates:
                try:
                    sd.check_input_settings(
                        device=device, channels=channels, dtype=cls.DTYPE, samplerate=rate,
                    )
                    return int(rate), int(channels)
                except Exception as e:
                    errors.append(f"{rate}Hz/{channels}ch: {e}")
        raise RuntimeError("No supported input format. " + " / ".join(errors))

    @classmethod
    def _select_capture_sample_rate(cls, sd, device):
        # Backward-compatible helper retained for tests/older callers.
        return cls._select_capture_format(sd, device)[0]

    @classmethod
    def _group_lanes(cls, group_index):
        start = int(group_index) * cls.LANES_PER_GROUP
        return tuple(range(start, start + cls.LANES_PER_GROUP))

    def _group_can_accept_work(self, group_index):
        if self._group_busy[group_index]:
            return False
        lanes = self._group_lanes(group_index)
        return all(
            not self._lane_busy[lane]
            and self._lane_ready[lane].is_set()
            and not self._work_queues[lane].full()
            for lane in lanes
        )

    def _find_available_group(self):
        """Return a ready idle worker, searching round-robin from the preferred lane."""
        for offset in range(self.RECOGNITION_GROUPS):
            group_index = (self._next_group_index + offset) % self.RECOGNITION_GROUPS
            if self._group_can_accept_work(group_index):
                return group_index
        return None

    def _recognize_tick(self):
        # The readiness timer runs every 100 ms. Recognition starts are globally spaced by
        # at least GROUP_STAGGER_SECONDS. Unlike the old fixed-lane schedule, a busy lane
        # does not cause a whole 12-second phase to be discarded: any free worker may take
        # the due slot, and if all workers are busy the slot remains pending until one frees.
        if not self._active:
            return

        now = time.monotonic()
        if self._next_group_slot_at <= 0:
            self._next_group_slot_at = now
        if now < self._next_group_slot_at:
            return

        group_index = self._find_available_group()
        if group_index is None:
            if self._slot_wait_started_at is None:
                self._slot_wait_started_at = now
            if not self._slot_wait_logged:
                busy = sum(1 for value in self._lane_busy[:self.PARALLEL_RECOGNITION_LANES] if value)
                ready = sum(1 for value in self._lane_ready[:self.PARALLEL_RECOGNITION_LANES] if value.is_set())
                print(
                    f"ShazamService: Recognition slot waiting seq={self._request_sequence} "
                    f"busy={busy}/{self.PARALLEL_RECOGNITION_LANES} "
                    f"ready={ready}/{self.PARALLEL_RECOGNITION_LANES} "
                    f"{self._lane_health_summary()}"
                )
                self._slot_wait_logged = True
            return

        # The chosen lane consumes future live PCM; no historical WAV snapshot is created.
        # Dispatch can start immediately after the helper is ready because page preparation
        # itself gives PortAudio time to deliver the first live blocks.
        generation = self._generation
        language = str(self.config.get("shazam_language", "ja-JP") or "ja-JP")
        country = str(self.config.get("shazam_endpoint_country", "JP") or "JP")
        lanes = self._group_lanes(group_index)
        request_sequence = self._request_sequence

        self._group_busy[group_index] = True
        for lane_index in lanes:
            self._lane_busy[lane_index] = True
            with self._lane_state_lock:
                self._lane_health[lane_index].phase = LanePhase.RECOGNIZING
        self._pending_group_results[request_sequence] = {
            "group_index": group_index,
            "lanes": lanes,
            "results": {},
        }

        try:
            for lane_offset, lane_index in enumerate(lanes):
                lane_start_delay = lane_offset * self.LANE_STAGGER_SECONDS
                self._work_queues[lane_index].put_nowait((
                    generation, group_index, lane_index, request_sequence,
                    language, country, self._cancel_current, lane_start_delay,
                    self._capture_sample_rate,
                ))
        except queue.Full:
            # A shutdown/restart race may make a queue unavailable after the readiness
            # check. Roll back without consuming the slot; the timer retries it shortly.
            self._pending_group_results.pop(request_sequence, None)
            self._group_busy[group_index] = False
            for lane_index in lanes:
                self._lane_busy[lane_index] = False
                with self._lane_state_lock:
                    self._lane_health[lane_index].phase = LanePhase.READY
            self._recognition_busy = any(self._group_busy) or any(self._lane_busy)
            return

        waited = 0.0
        if self._slot_wait_started_at is not None:
            waited = max(0.0, now - self._slot_wait_started_at)
        self._slot_wait_started_at = None
        self._slot_wait_logged = False
        self._request_sequence += 1
        self._recognition_busy = True
        # Fairness: start the next free-worker search after the worker just used.
        self._next_group_index = (group_index + 1) % self.RECOGNITION_GROUPS
        # Never catch up in a burst after saturation. Three seconds is measured from this
        # real dispatch, so consecutive live listening windows remain time-separated.
        self._next_group_slot_at = now + self.GROUP_STAGGER_SECONDS
        lane_text = "+".join(str(lane + 1) for lane in lanes)
        wait_text = f" waited={waited:.1f}s" if waited > 0.05 else ""
        print(
            f"ShazamService: Scheduled recognition group={group_index + 1} "
            f"lanes={lane_text} seq={request_sequence} "
            f"slotInterval={self.GROUP_STAGGER_SECONDS:.1f}s pool=shared{wait_text}"
        )

    def _worker_main(self, lane_index):
        from app.services.shazam_webview_client import RecognitionCancelled, RecognitionAborted

        work_queue = self._work_queues[lane_index]
        recognizer = self._web_recognizers[lane_index]
        try:
            while not self._shutting_down:
                item = work_queue.get()
                if item is None:
                    break
                if (isinstance(item, tuple) and len(item) == 4 and item[0] == "prewarm"):
                    _, generation, language, cancelled = item
                    if cancelled.is_set() or generation != self._generation:
                        continue
                    recognizer = self._prepare_lane_recognizer(
                        lane_index, recognizer, generation, language, cancelled)
                    continue

                (
                    generation, group_index, item_lane, request_sequence,
                    language, country, cancelled, lane_start_delay, capture_sample_rate,
                ) = item
                if cancelled.is_set() or generation != self._generation:
                    continue
                title, artist, error_text = "", "", ""
                raw_result = None
                recycle_lane = False
                try:
                    if lane_start_delay > 0 and cancelled.wait(lane_start_delay):
                        continue
                    if cancelled.is_set() or generation != self._generation:
                        continue
                    recognizer.identity_probe = EarlyIdentityProbe(
                        self._metadata_prefetch, generation, language, country,
                        cancelled, self._prefetch_finished.emit,
                    )
                    try:
                        result = recognizer.recognize_live(capture_sample_rate, language, cancelled)
                    finally:
                        recognizer.identity_probe = None
                    if cancelled.is_set() or generation != self._generation:
                        continue

                    # Do NOT run Apple/iTunes localization here. Keeping metadata work
                    # out of the lane worker releases this WebView immediately for the
                    # shared pool; localization happens after recognition returns.
                    raw_result = dict(result or {})
                    # Local-only timestamp: overwrite any remote field of this name.
                    # A delayed Qt callback must not make an old recognition fresh.
                    raw_result["_received_monotonic"] = time.monotonic()
                    title = str(raw_result.get("title") or "").strip()
                    artist = str(raw_result.get("artist") or "").strip()
                    with self._lane_state_lock:
                        if not self._lane_run_current(lane_index, generation, cancelled):
                            continue
                        self._raw_lane_results[(generation, item_lane, request_sequence)] = raw_result
                    print(
                        f"ShazamService: WebView2 raw group={group_index + 1} "
                        f"lane={lane_index + 1} seq={request_sequence} "
                        f"source={raw_result.get('source', '')} "
                        f"shazamTrackId={raw_result.get('shazamTrackId', '')} "
                        f"appleTrackId={raw_result.get('appleTrackId', '')} "
                        f"title={title!r} artist={artist!r}"
                    )
                    self._record_lane_health(lane_index, "", generation=generation, cancelled=cancelled)
                except RecognitionAborted as exc:
                    # Intentional request-scoped abort keeps the helper usable.
                    error_text = str(exc)
                    self._record_lane_health(lane_index, "", generation=generation, cancelled=cancelled)
                except RecognitionCancelled:
                    if cancelled.is_set() or generation != self._generation or self._shutting_down:
                        continue
                    error_text = "Shazam helper became unavailable during recognition."
                    recycle_lane = self._record_lane_health(
                        lane_index, error_text, generation=generation, cancelled=cancelled,
                        error_type="RecognitionCancelled")
                except Exception as exc:
                    error_text = str(exc) or type(exc).__name__
                    recycle_lane = self._record_lane_health(
                        lane_index, error_text, generation=generation, cancelled=cancelled,
                        error_type=type(exc).__name__)
                if (not cancelled.is_set() and not self._shutting_down
                        and generation == self._generation):
                    try:
                        self._recognition_finished.emit(
                            generation, item_lane, request_sequence, title, artist, error_text
                        )
                    except RuntimeError:
                        # The QObject may have been destroyed during application shutdown.
                        break
                if recycle_lane and not cancelled.is_set() and not self._shutting_down:
                    recognizer = self._recycle_lane_recognizer(
                        lane_index, recognizer, generation, language, cancelled
                    )
        finally:
            recognizer.close()

    @staticmethod
    def _raw_result_track_id(raw_result):
        if not isinstance(raw_result, dict):
            return ""
        for key in ("shazamTrackId", "appleTrackId"):
            value = str(raw_result.get(key) or "").strip()
            if re.fullmatch(r"[0-9]{6,20}", value):
                return value
        url = str(raw_result.get("url") or "")
        match = re.search(r"/(?:song|track)/([0-9]{6,20})(?:/|$)", url, re.I)
        return match.group(1) if match else ""

    @classmethod
    def _lane_result_is_usable(cls, result, raw_result=None):
        title, _artist, error = result
        if error:
            return False
        if str(title or "").strip():
            return True
        if not isinstance(raw_result, dict):
            return False
        if str(raw_result.get("title") or "").strip():
            return True
        return bool(cls._raw_result_track_id(raw_result))

    def _handle_recognition_finished(
        self, generation, lane_index, request_sequence, title, artist, error_text
    ):
        raw_key = (generation, lane_index, request_sequence)
        raw_result = self._raw_lane_results.pop(raw_key, None)

        # A late callback from before stop/restart must not release a lane/group that
        # is already processing a request in the new generation.
        if generation != self._generation or not self._active:
            return
        if 0 <= lane_index < len(self._lane_busy):
            self._lane_busy[lane_index] = False
        pending = self._pending_group_results.get(request_sequence)
        if pending is None or lane_index not in pending["lanes"]:
            # The group may already have been completed/cleared or belong to a cancelled
            # generation. Do not let an orphaned callback alter current recognition state.
            self._recognition_busy = any(self._group_busy) or any(self._lane_busy)
            print(
                f"ShazamService: Ignored orphaned lane result lane={lane_index + 1} "
                f"seq={request_sequence}"
            )
            return

        pending.setdefault("raw_results", {})[lane_index] = raw_result
        pending["results"][lane_index] = (
            str(title or "").strip(),
            str(artist or "").strip(),
            str(error_text or "").strip(),
        )
        self._finalize_pending_group(generation, request_sequence, pending)

    def _finalize_pending_group(
        self, generation, request_sequence, pending, missing_error=""
    ):
        if self._pending_group_results.get(request_sequence) is not pending:
            return

        timer = pending.get("confirmation_timer")
        if timer is not None:
            timer.stop()
            if hasattr(timer, "deleteLater"):
                timer.deleteLater()

        group_index = pending["group_index"]
        lanes = pending["lanes"]
        results = [
            pending["results"].get(lane, ("", "", missing_error))
            for lane in lanes
        ]
        raw_results = [
            pending.get("raw_results", {}).get(lane)
            for lane in lanes
        ]
        self._pending_group_results.pop(request_sequence, None)
        self._group_busy[group_index] = False
        self._recognition_busy = any(self._group_busy) or any(self._lane_busy)

        try:
            self._handle_confirmed_group_result(
                group_index, request_sequence, lanes, results, raw_results
            )
        finally:
            # A completed worker may satisfy a slot that was waiting because all active
            # workers were busy. The scheduler still enforces 3 seconds between dispatches.
            if self._active and generation == self._generation:
                self._recognize_tick()

    def _temporal_observations_match(self, previous, current):
        if (not previous or not current or previous.get("kind") or current.get("kind")):
            return False
        previous_id, current_id = previous.get("identity"), current.get("identity")
        if previous_id is not None or current_id is not None:
            # No cross-provider numeric-ID equality, and no name-based fallback
            # around two different known IDs. An ID-less result cannot certify an ID.
            return previous_id is not None and previous_id == current_id
        return is_same_track(self._display_track(previous["track"]), self._display_track(current["track"]))

    @staticmethod
    def _is_completed_no_match(observation):
        return isinstance(observation, dict) and observation.get("kind") == "no-match"

    @staticmethod
    def _is_transport_error_gap(observation):
        return isinstance(observation, dict) and observation.get("kind") == "transport-error"

    @classmethod
    def _is_neutral_gap(cls, observation):
        return cls._is_completed_no_match(observation) or cls._is_transport_error_gap(observation)

    @staticmethod
    def _shazam_gap_identity(raw_result, identity):
        """Keep Shazam evidence, even when metadata uses an explicit Apple ID.

        Route namespaces stay separate. No alias between /song, /track and an
        untyped ID is inferred. track_identity() must have checked conflicts first.
        """
        if identity is not None and identity[0] in (
                "shazam-song", "shazam-track", "shazam-untyped"):
            return identity
        if isinstance(raw_result, dict):
            value = str(raw_result.get("shazamTrackId") or "").strip()
            if re.fullmatch(r"[0-9]{6,20}", value):
                return ("shazam-untyped", value)
        return None

    @staticmethod
    def _received_time(raw_result, now):
        """Worker receipt time when present; direct/local calls use their arrival time."""
        if not isinstance(raw_result, dict) or "_received_monotonic" not in raw_result:
            return now
        value = raw_result["_received_monotonic"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        return value if math.isfinite(value) and 0 <= value <= now else None

    def _gap_candidate_fresh(self, observation, now=None):
        if not observation or not observation.get("shazam_identity"):
            return False
        received = observation.get("received_at")
        if received is None:
            return False
        age = (time.monotonic() if now is None else now) - received
        return 0 <= age <= self.SHAZAM_NO_MATCH_WINDOW_SECONDS

    def _gap_pair_matches(self, previous, current, gap_observations=None):
        """Match two Shazam observations across a bounded set of neutral gaps.

        Normal in-order draining records gap counters on the first observation. Look-ahead
        bypass passes the actual intervening observations explicitly. In both cases the
        total gap budget is two and the audio-transport subtype may occur at most once.
        """
        if not self._temporal_observations_match(previous, current):
            return False
        if (not previous.get("shazam_identity") or
                previous["shazam_identity"] != current.get("shazam_identity")):
            return False
        gaps = current["sequence"] - previous["sequence"] - 1
        if not 1 <= gaps <= self.SHAZAM_NO_MATCH_MAX_GAPS:
            return False

        if gap_observations is None:
            if previous.get("_neutral_gap_total", 0) != gaps:
                return False
            transport_errors = previous.get("_neutral_transport_errors", 0)
            no_matches = previous.get("_neutral_no_matches", 0)
        else:
            gap_observations = list(gap_observations)
            if len(gap_observations) != gaps or not all(
                    self._is_neutral_gap(item) for item in gap_observations):
                return False
            transport_errors = sum(self._is_transport_error_gap(item) for item in gap_observations)
            no_matches = sum(self._is_completed_no_match(item) for item in gap_observations)

        if transport_errors > self.SHAZAM_TRANSPORT_ERROR_MAX_GAPS:
            return False
        if no_matches > self.SHAZAM_NO_MATCH_MAX_GAPS:
            return False
        if transport_errors + no_matches != gaps:
            return False

        now = time.monotonic()
        # Check BOTH receipts against now: reordering/backlog cannot refresh them.
        return (self._gap_candidate_fresh(previous, now) and
                self._gap_candidate_fresh(current, now))

    @staticmethod
    def _plain_no_match_result(result, raw_result):
        """Only a completed empty recognition is soft; all failures are barriers."""
        title, artist, error = result
        if title or artist or error:
            return False
        if raw_result is None:
            return True
        if not isinstance(raw_result, dict):
            return False
        source = str(raw_result.get("source") or "").casefold()
        return (source in ("", "no-match") and not any(
            raw_result.get(field) for field in (
                "title", "artist", "shazamTrackId", "appleTrackId", "error")))

    @classmethod
    def _neutral_audio_request_error(cls, result, raw_result):
        """Recognize only the known WebView audio-supply failure as a neutral gap.

        Do not hide track evidence behind an error: if the same callback carries a title,
        artist or track ID it remains a hard barrier and is handled by the normal safety path.
        """
        title, artist, error = result
        normalized = str(error or "").casefold()
        if (title or artist or
                cls.SHAZAM_AUDIO_REQUEST_ERROR_MARKER not in normalized):
            return False
        if raw_result is None:
            return True
        if not isinstance(raw_result, dict):
            return False
        return not any(raw_result.get(field) for field in (
            "title", "artist", "shazamTrackId", "appleTrackId", "url"))

    def _metadata_key(self, identity):
        language = str(self.config.get("shazam_language", "ja-JP") or "ja-JP")
        country = str(self.config.get("shazam_endpoint_country", "JP") or "JP")
        return (language.casefold(), country.upper(), *identity)

    def _pending_publication_safe(self, pending, extra=None):
        """Check *observed* later outcomes before publishing a confirmed ID.

        A confirmed pair already supplied both independent votes. A later, completed
        no-match or the narrowly recognized audio-supply error does not retract those
        votes. Preserve the existing conservative limits (two neutral outcomes, at
        most one transport error, and 15 seconds from the confirmed result).

        A different song, an invalid result, or an unrelated failure remains a hard
        barrier. Include buffered out-of-order observations as well as neutrals that
        were already consumed by the in-order drain while metadata was in flight.
        """
        newer = dict(pending.get("_publication_neutrals", {}))
        newer.update((seq, observation)
                     for seq, observation in self._temporal_results.items()
                     if seq > pending["sequence"])
        newest = self._newest_temporal_observation
        if newest is not None and newest[0] > pending["sequence"]:
            newer[newest[0]] = newest[1]
        if extra is not None and extra[0] > pending["sequence"]:
            newer[extra[0]] = extra[1]

        neutral_count = 0
        transport_count = 0
        for sequence, observation in sorted(newer.items()):
            if self._temporal_observations_match(pending, observation):
                continue
            if not self._is_neutral_gap(observation):
                return False, f"different-or-invalid-result seq={sequence}"
            neutral_count += 1
            transport_count += int(self._is_transport_error_gap(observation))
            if (neutral_count > self.SHAZAM_NO_MATCH_MAX_GAPS or
                    transport_count > self.SHAZAM_TRANSPORT_ERROR_MAX_GAPS):
                return False, "neutral-gap-limit"

        if neutral_count and not self._gap_candidate_fresh(pending):
            return False, "neutral-gap-expired"
        return True, ""

    def _stage_temporal_result(
        self, group_index, request_sequence, title="", artist="", raw_result=None,
        *, identity_pending=False, no_match=False, transport_error=False,
    ):
        if (request_sequence < self._next_temporal_sequence or
                request_sequence in self._temporal_results):
            return
        title = str(title or "").strip()
        artist = clean_artist(artist)
        observation = None
        now = time.monotonic()
        received_at = self._received_time(raw_result, now)
        try:
            identity = track_identity(raw_result)
        except IdentityConflict:
            identity, title, no_match, transport_error = None, "", False, False
        if title or (identity_pending and identity is not None):
            observation = {
                "group_index": group_index,
                "sequence": request_sequence,
                "track": (title, artist),
                "identity": identity,
                "metadata_key": self._metadata_key(identity) if identity else None,
                "shazam_identity": self._shazam_gap_identity(raw_result, identity),
                "received_at": received_at,
            }
        elif no_match:
            observation = {"kind": "no-match", "sequence": request_sequence,
                           "received_at": received_at}
        elif transport_error:
            observation = {"kind": "transport-error", "sequence": request_sequence,
                           "received_at": received_at}
        newest = self._newest_temporal_observation
        if newest is None or request_sequence > newest[0]:
            self._newest_temporal_observation = (request_sequence, observation)
        pending = self._pending_identity_confirmation
        if pending and request_sequence > pending["sequence"]:
            # Do not confuse a later *neutral* outcome with a different song.
            # Remember it even after the temporal queue drains so that a late
            # metadata callback still observes the bounded safety budget.
            if self._is_neutral_gap(observation):
                pending.setdefault("_publication_neutrals", {})[request_sequence] = observation
            safe, reason = self._pending_publication_safe(
                pending, (request_sequence, observation)
            )
            if not safe:
                print(f"ShazamService: Metadata candidate superseded seq={pending['sequence']} "
                      f"by seq={request_sequence} reason={reason}")
                self._pending_identity_confirmation = None
        self._temporal_results[request_sequence] = observation
        self._drain_temporal_results()

    def _find_temporal_bypass_pair(self):
        """Skip a stuck older head only for an independently confirmed newer pair.

        A non-consecutive Shazam pair is allowed only when EVERY intervening request
        has completed with an allowed neutral gap: a soft no-match, or at most one
        known WebView audio-supply failure. Missing/pending results, other errors and
        rejected metadata never count as neutral.
        """
        head = self._next_temporal_sequence
        if head in self._temporal_results:
            return None
        for sequence in sorted(key for key in self._temporal_results if key > head):
            first = self._temporal_results[sequence]
            if not first or self._is_neutral_gap(first):
                continue
            neutral = []
            for distance in range(1, self.SHAZAM_NO_MATCH_MAX_GAPS + 2):
                next_sequence = sequence + distance
                if next_sequence not in self._temporal_results:
                    break  # Do not guess the outcome of a running request.
                second = self._temporal_results[next_sequence]
                if self._is_neutral_gap(second):
                    neutral.append(second)
                    if (sum(self._is_transport_error_gap(item) for item in neutral) >
                            self.SHAZAM_TRANSPORT_ERROR_MAX_GAPS):
                        break
                    continue
                if second and (
                    distance == 1 and self._temporal_observations_match(first, second)
                    or distance > 1 and self._gap_pair_matches(first, second, neutral)
                ):
                    return sequence, next_sequence
                break  # A different song or a hard failure is a barrier.
        return None

    def _bypass_stuck_temporal_head(self):
        pair = self._find_temporal_bypass_pair()
        if pair is None:
            return False

        first_sequence, second_sequence = pair
        old_head = self._next_temporal_sequence
        # Results older than the confirmed look-ahead pair are superseded. A later callback
        # for any of these request sequences is ignored by _stage_temporal_result().
        for sequence in list(self._temporal_results):
            if old_head <= sequence < first_sequence:
                self._temporal_results.pop(sequence, None)

        first = self._temporal_results[first_sequence]
        title, artist = first["track"]
        print(
            f"ShazamService: Temporal head bypass seq={old_head}..{first_sequence - 1} "
            f"using matched lookahead seq={first_sequence}/{second_sequence} "
            f"{artist} - {title}"
        )
        self._next_temporal_sequence = first_sequence
        # A blocked earlier window may have *already* shown this same track as
        # tentative. Retain that logical first vote and its UI/search ownership,
        # while using the new independent pair to satisfy temporal confirmation.
        # Reject only if the newer pair is a genuinely different song.
        candidate = self._tentative_candidate
        retain, retain_reason = self._can_retain_tentative_for_bypass(
            candidate, first, self._temporal_results[second_sequence]
        )
        if retain:
            first["_retain_existing_tentative"] = True
            print(f"ShazamService: Retaining tentative firstSeq={candidate['sequence']} "
                  f"for matching lookahead {first_sequence}/{second_sequence} "
                  f"reason={retain_reason}; keep existing YouTube PRELOAD/request ID")
        else:
            if candidate:
                print(f"ShazamService: Cannot retain tentative firstSeq={candidate['sequence']} "
                      f"for lookahead {first_sequence}/{second_sequence} "
                      f"reason={retain_reason}")
            self._reject_tentative_candidate("older-observation-bypassed")
        # Do not compare the pair with an observation before the stuck head.
        self._last_temporal_observation = None
        self._pending_identity_confirmation = None
        return True

    def _can_retain_tentative_for_bypass(self, candidate, first, second):
        """Keep the *search/PRELOAD owner* of a verified same-song lookahead pair.

        The lookahead pair has already passed the independent-vote and neutral-gap
        rules in _find_temporal_bypass_pair().  The first, older vote is NOT
        counted as an additional confirmation.  It is only the identity of an
        already displayed/queued preview that can be reused after a stuck
        recognition lane or a delayed Qt event loop.

        Checking the newest receipt against *the current wall/monotonic time*
        incorrectly discards a valid early PRELOAD when callbacks are drained
        late: by then its original 'ready' request ID may have arrived, and
        issuing a replacement PRELOAD makes that correct acknowledgement stale.
        Bound reuse to the existing 30s tentative display lifetime and require
        the two newer observations to match the same song.  A different song
        always invalidates the old preview.
        """
        if not candidate:
            return False, "no-existing-preview"
        old = candidate["observation"]
        if not self._same_presentation_observations(old, first):
            return False, "different-first-identity"
        if not self._temporal_observations_match(first, second):
            return False, "lookahead-pair-mismatch"
        if candidate["confirmed"]:
            return True, "already-confirmed-same-identity"
        received_at = old.get("received_at")
        if received_at is None:
            return False, "first-vote-without-receipt-time"
        age = time.monotonic() - received_at
        max_age = self.SHAZAM_NO_MATCH_WINDOW_SECONDS * 2
        if age < 0 or age > max_age:
            return False, f"first-vote-too-old age={age:.1f}s limit={max_age:.1f}s"
        return True, f"verified-same-identity age={age:.1f}s"

    def _latest_matching_tentative_time(self, candidate):
        """Newest matching evidence, including results blocked behind older windows.

        This extends the *display* timeout only when another independent
        recognition of the SAME identity is already available; it never treats
        skipped or uncompleted requests as successful confirmation votes.
        """
        observation = candidate["observation"]
        latest = observation.get("received_at") or time.monotonic()
        buffered = list(self._temporal_results.values())
        newest = self._newest_temporal_observation
        if newest is not None:
            buffered.append(newest[1])
        for later in buffered:
            if (later is not None and later.get("sequence", -1) > observation["sequence"]
                    and self._temporal_observations_match(observation, later)):
                latest = max(latest, later.get("received_at", latest))
        return latest

    def _reject_tentative_candidate(self, reason):
        """Discard a first-vote UI preview; never touch committed history.json."""
        candidate = self._tentative_candidate
        self._tentative_candidate = None
        self._tentative_timer.stop()
        if candidate is None:
            return
        print(f"ShazamService: Tentative rejected firstSeq={candidate['sequence']} reason={reason}")
        if candidate["emitted"]:
            self.candidate_rejected.emit(candidate["sequence"], str(reason))
        elif candidate.get("retained_preview"):
            self.candidate_rejected.emit(candidate["retained_preview"]["sequence"], str(reason))

    def _display_track(self, track):
        if not track:
            return None
        return (strip_title_keywords(track[0], self.config.get(CONFIG_KEY, [])), track[1])

    def _same_presentation(self, previous, current, previous_id=None, current_id=None):
        # Verified IDs control confirmation votes, not UI/search deduplication.
        # Even across different known IDs, the first four displayed title
        # characters are enough to reuse the same search/PRELOAD/history row.
        if previous_id is not None and current_id is not None:
            if previous_id == current_id:
                return True
            if shares_title_prefix(self._display_track(previous), self._display_track(current)):
                return True
            return title_variant_matches(previous, current, self.config.get(CONFIG_KEY, []))
        return is_same_track(self._display_track(previous), self._display_track(current))

    def _observation_track(self, observation):
        track = observation.get("track", ("", ""))
        if not track[0] and observation.get("metadata_key"):
            track = self._metadata_prefetch.get(observation["metadata_key"]) or track
        return track

    def _same_presentation_observations(self, previous, current):
        if not previous or not current or previous.get("kind") or current.get("kind"):
            return False
        return self._same_presentation(self._observation_track(previous), self._observation_track(current),
                                       previous.get("identity"), current.get("identity"))

    def _receipt_matches(self, identity, track):
        receipt = self._confirmed_search_receipt
        if receipt is None or receipt[0] != self._generation:
            return False
        raw_track = receipt[4] if len(receipt) > 4 else receipt[3][1:3]
        return self._same_presentation(raw_track, track, receipt[2], identity)

    def _emit_tentative_if_ready(self, *, allow_confirmed=False):
        """Resolve ID-only metadata, even if the second vote beat the lookup."""
        candidate = self._tentative_candidate
        if not candidate or candidate["emitted"] or (candidate["confirmed"] and not allow_confirmed):
            return False
        observation = candidate["observation"]
        title, artist = observation["track"]
        if not title and observation.get("metadata_key"):
            metadata = self._metadata_prefetch.get(observation["metadata_key"])
            if metadata:
                title, artist = metadata
        title = str(title or "").strip()
        artist = clean_artist(artist)
        retained = candidate.get("retained_preview")
        if retained and title:
            candidate.pop("retained_preview", None)
            old = retained["observation"]
            if self._same_presentation(self._observation_track(old), (title, artist),
                                       old.get("identity"), observation.get("identity")):
                candidate["sequence"] = retained["sequence"]
                candidate["entry"] = retained["entry"]
                candidate["keywords"] = retained.get("keywords", [])
                candidate["emitted"] = True
                candidate["presentation_retained"] = True
                print(f"ShazamService: Resolved title variant retained firstSeq={candidate['sequence']} "
                      f"seq={observation['sequence']}; no repeat search or PRELOAD")
                return True  # a completed second vote may now authorize the old cue
            # The retained clock belongs to the OLD UI preview, not this song.
            # A genuinely different title starts with its own observation's
            # receipt time, never the metadata-completion time. Otherwise it can
            # expire early (or live too long) on the previous song's 30s limit.
            candidate.pop("preview_received_at", None)
            self.candidate_rejected.emit(retained["sequence"], "resolved-different-title")
            print(f"ShazamService: Different title detached preview clock "
                  f"oldFirstSeq={retained['sequence']} newFirstSeq={candidate['sequence']}")
            if candidate["confirmed"]:
                self._tentative_timer.stop()
            else:
                # Re-arm through the normal receipt/evidence-based expiry rules.
                # If this new first vote is already stale, do not briefly launch
                # a YouTube search before the timer rejects it.
                self._expire_tentative_candidate()
                if self._tentative_candidate is not candidate:
                    return False
        last_identity = self._last_published_identity
        this_identity = observation.get("identity")
        same_as_published = self._same_presentation(
            self._last_track, (title, artist), last_identity, this_identity)
        same_as_confirmed_search = self._receipt_matches(this_identity, (title, artist))
        if not title or same_as_published or same_as_confirmed_search:
            return False
        title = strip_title_keywords(title, candidate.get("keywords", self.config.get(CONFIG_KEY, [])))
        entry = (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), title, artist)
        candidate["entry"] = entry
        candidate["emitted"] = True
        print(f"ShazamService: Tentative first vote seq={candidate['sequence']} "
              f"title={title!r} artist={artist!r}; start YouTube preload, no PLAY")
        self.candidate_started.emit(candidate["sequence"], entry)
        return True

    def _start_tentative_candidate(self, observation):
        previous = self._tentative_candidate
        retained = (previous if previous and previous["emitted"] and not previous["confirmed"]
                    else previous.get("retained_preview") if previous else None)
        valid = bool(observation and not observation.get("kind"))
        unknown_title = bool(valid and not self._observation_track(observation)[0]
                             and observation.get("metadata_key"))
        same_preview = bool(retained and valid and
            self._same_presentation_observations(retained["observation"], observation))
        if retained and (unknown_title or same_preview):
            # Wait for exact-ID metadata before deciding whether this is merely
            # a title variant. The retained UI row is STILL tentative: no PLAY.
            # Unknown results never count as an extra recognition vote.
            candidate = {"sequence": observation["sequence"], "observation": observation,
                "emitted": False, "confirmed": False, "entry": None,
                "keywords": normalize_keywords(self.config.get(CONFIG_KEY, [])),
                "retained_preview": retained}
            if same_preview:
                candidate.update(sequence=retained["sequence"], entry=retained["entry"],
                    keywords=retained.get("keywords", []), emitted=True,
                    presentation_retained=True)
                candidate.pop("retained_preview", None)
                print(f"ShazamService: Tentative title variant retained firstSeq={candidate['sequence']} "
                      f"seq={observation['sequence']}; no repeat search or PRELOAD")
            self._tentative_candidate = candidate
            # The original cue cannot be held forever by unresolved replacements.
            received = retained.get("preview_received_at", retained["observation"].get("received_at"))
            candidate["preview_received_at"] = received
            age = max(0.0, time.monotonic() - received) if received is not None else 0.0
            remaining = max(1, int((self.SHAZAM_NO_MATCH_WINDOW_SECONDS * 2 - age) * 1000) + 100)
            self._tentative_timer.setInterval(remaining)
            self._tentative_timer.start()
            return
        self._reject_tentative_candidate("new-candidate")
        if not valid:
            return
        self._tentative_candidate = {
            "sequence": observation["sequence"], "observation": observation,
            "emitted": False, "confirmed": False, "entry": None,
            "keywords": normalize_keywords(self.config.get(CONFIG_KEY, [])),
        }
        received_at = observation.get("received_at")
        age = max(0.0, time.monotonic() - received_at) if received_at is not None else 0.0
        remaining_ms = max(1, int((self.SHAZAM_NO_MATCH_WINDOW_SECONDS - age) * 1000) + 100)
        self._tentative_timer.setInterval(remaining_ms)
        self._tentative_timer.start()
        self._emit_tentative_if_ready()

    def _expire_tentative_candidate(self):
        candidate = self._tentative_candidate
        if not candidate or (candidate["confirmed"] and not candidate.get("retained_preview")):
            return
        original_receipt = candidate.get("preview_received_at")
        if original_receipt is not None:
            remaining = self.SHAZAM_NO_MATCH_WINDOW_SECONDS * 2 - (time.monotonic() - original_receipt)
            if remaining <= 0:
                self._reject_tentative_candidate("title-variant-preview-expired")
            else:
                self._tentative_timer.setInterval(max(1, int(remaining * 1000) + 100))
                self._tentative_timer.start()
            return
        received_at = candidate["observation"].get("received_at")
        if received_at is not None:
            # A later matching recognition blocked by an unfinished lower seq
            # is evidence that the candidate is still current. Keep the UI
            # tentative for up to 30s total, but NEVER count that observation
            # as a second vote until the temporal drain validates it.
            evidence_at = min(
                self._latest_matching_tentative_time(candidate),
                received_at + self.SHAZAM_NO_MATCH_WINDOW_SECONDS,
            )
            remaining = self.SHAZAM_NO_MATCH_WINDOW_SECONDS - (time.monotonic() - evidence_at)
            if remaining > 0:
                self._tentative_timer.setInterval(max(1, int(remaining * 1000) + 100))
                self._tentative_timer.start()
                return
        self._reject_tentative_candidate("first-vote-expired")

    def _remember_confirmed_search(self, candidate):
        """Remember the already-searched two-vote owner, not uncommitted history.

        Late metadata still obeys ALL publication barriers. This small receipt
        only prevents the same song being searched/played again after a single
        false candidate interrupted history publication. A distinct confirmed
        song (even without metadata yet) invalidates reuse immediately.
        """
        identity = candidate["observation"].get("identity")
        receipt = self._confirmed_search_receipt
        if not identity:
            self._confirmed_search_receipt = None
        elif candidate["emitted"] and candidate.get("entry"):
            self._confirmed_search_receipt = (
                self._generation, candidate["sequence"], identity, tuple(candidate["entry"]),
                self._observation_track(candidate["observation"]),
            )
        elif receipt is not None and receipt[2] != identity:
            if self._receipt_matches(identity, self._observation_track(candidate["observation"])):
                # Keep the original search owner, but follow the latest confirmed
                # variant for adjacent matching. Do not seed a cross-ID cache.
                self._confirmed_search_receipt = (self._generation, receipt[1], identity,
                    receipt[3], self._observation_track(candidate["observation"]))
            else:
                self._confirmed_search_receipt = None

    def _confirm_tentative_candidate(self, first, second):
        candidate = self._tentative_candidate
        if not candidate or candidate["observation"]["sequence"] != first["sequence"]:
            return
        already_confirmed = candidate["confirmed"]
        candidate["confirmed"] = True
        candidate["confirmed_by"] = second["sequence"]
        if not candidate.get("retained_preview"):
            self._tentative_timer.stop()
        self._remember_confirmed_search(candidate)
        if candidate["emitted"] and not already_confirmed:
            print(f"ShazamService: Tentative confirmed firstSeq={candidate['sequence']} "
                  f"temporalSeq={first['sequence']} secondSeq={second['sequence']}; "
                  "playback now permitted")
            self.candidate_confirmed.emit(candidate["sequence"], candidate["entry"])

    def _process_temporal_observation(self, sequence, observation):
        if observation is None:
            self._reject_tentative_candidate("invalid-or-transport-error")
            self._last_temporal_observation = None
            self._pending_identity_confirmation = None
            return
        if self._is_neutral_gap(observation):
            previous = self._last_temporal_observation
            pending = self._pending_identity_confirmation
            if pending and sequence > pending["sequence"]:
                pending.setdefault("_publication_neutrals", {})[sequence] = observation
            gaps = sequence - previous["sequence"] if previous else 0
            no_matches = (previous.get("_neutral_no_matches", 0) if previous else 0)
            transport_errors = (previous.get("_neutral_transport_errors", 0) if previous else 0)
            no_matches += int(self._is_completed_no_match(observation))
            transport_errors += int(self._is_transport_error_gap(observation))
            if (previous and 1 <= gaps <= self.SHAZAM_NO_MATCH_MAX_GAPS and
                    transport_errors <= self.SHAZAM_TRANSPORT_ERROR_MAX_GAPS and
                    no_matches <= self.SHAZAM_NO_MATCH_MAX_GAPS and
                    no_matches + transport_errors == gaps and
                    self._gap_candidate_fresh(previous)):
                previous["_neutral_gap_total"] = gaps
                previous["_neutral_no_matches"] = no_matches
                previous["_neutral_transport_errors"] = transport_errors
                if pending:
                    safe, reason = self._pending_publication_safe(pending)
                    if previous is not pending or not safe:
                        print(f"ShazamService: Metadata candidate superseded seq={pending['sequence']} "
                              f"by neutral seq={sequence} reason={reason or 'changed-candidate'}")
                        self._pending_identity_confirmation = None
                age = time.monotonic() - previous["received_at"]
                print(f"ShazamService: Candidate held across neutral gap seq={sequence} "
                      f"kind={observation['kind']} prevSeq={previous['sequence']} "
                      f"identity={previous['shazam_identity']} gapCount={gaps} "
                      f"noMatches={no_matches} transportErrors={transport_errors} age={age:.3f}s")
            else:
                if previous:
                    print(f"ShazamService: Candidate gap reset seq={sequence} "
                          f"kind={observation['kind']} prevSeq={previous['sequence']} "
                          f"gapCount={gaps} noMatches={no_matches} "
                          f"transportErrors={transport_errors} "
                          "reason=expired-or-limit-or-no-shazam-id")
                self._reject_tentative_candidate("neutral-gap-expired-or-limit")
                self._last_temporal_observation = None
                self._pending_identity_confirmation = None
            return

        previous = self._last_temporal_observation
        self._last_temporal_observation = observation
        title, artist = observation["track"]
        if previous is None:
            self._pending_identity_confirmation = None
            candidate = self._tentative_candidate
            if (observation.pop("_retain_existing_tentative", False) and
                    candidate and
                    self._same_presentation_observations(candidate["observation"], observation)):
                # The same first vote keeps its original UI/candidate ID. Only
                # its temporal reference advances to the valid lookahead pair.
                candidate["observation"] = observation
                print(f"ShazamService: Tentative continued firstSeq={candidate['sequence']} "
                      f"lookaheadSeq={sequence}; no UI removal or repeat search")
                return
            self._start_tentative_candidate(observation)
            print(
                f"ShazamService: Candidate pending seq={sequence} "
                f"title={title!r} artist={artist!r}"
            )
            return

        adjacent_match = (sequence == previous["sequence"] + 1 and
                          self._temporal_observations_match(previous, observation))
        gap_match = self._gap_pair_matches(previous, observation)
        if not adjacent_match and not gap_match:
            self._pending_identity_confirmation = None
            self._start_tentative_candidate(observation)
            print(
                f"ShazamService: Candidate changed/rejected prevSeq={previous['sequence']} "
                f"seq={sequence} title={title!r}"
            )
            return

        if gap_match:
            observation["gap_confirmed"] = True
            elapsed = abs(observation["received_at"] - previous["received_at"])
            print(f"ShazamService: Temporal neutral-gap confirmation prevSeq={previous['sequence']} "
                  f"seq={sequence} identity={observation['shazam_identity']} "
                  f"gapCount={sequence - previous['sequence'] - 1} "
                  f"noMatches={previous.get('_neutral_no_matches', 0)} "
                  f"transportErrors={previous.get('_neutral_transport_errors', 0)} "
                  f"elapsed={elapsed:.3f}s window={self.SHAZAM_NO_MATCH_WINDOW_SECONDS:.1f}s "
                  f"{artist} - {title}")
        else:
            print(
                f"ShazamService: Temporal confirmation prevSeq={previous['sequence']} "
                f"seq={sequence} {artist} - {title}"
            )
        self._confirm_tentative_candidate(previous, observation)
        if observation.get("identity") is not None:
            # A buffered future result may already have arrived before this pair
            # completed. _try_publish_identity_confirmation validates those too.
            observation["_publication_neutrals"] = {}
            self._pending_identity_confirmation = observation
            print(f"ShazamService: Identity confirmed seq={sequence} "
                  f"identity={observation['identity']}; awaiting shared metadata only")
            self._try_publish_identity_confirmation()
        else:
            self._pending_identity_confirmation = None
            self._publish_confirmed_track(
                observation["group_index"], sequence, title, artist
            )

    def _try_publish_identity_confirmation(self):
        pending = self._pending_identity_confirmation
        if not pending or not self._active:
            return
        if pending.get("gap_confirmed") and not self._gap_candidate_fresh(pending):
            print(f"ShazamService: Gap-confirmed metadata expired seq={pending['sequence']}")
            self._pending_identity_confirmation = None
            return
        # Out-of-order callbacks may reveal a genuine different song before the
        # queue drains. Keep that barrier, but do not treat a bounded neutral
        # outcome as contradicting two already confirmed Shazam ID observations.
        safe, reason = self._pending_publication_safe(pending)
        if not safe:
            print(f"ShazamService: Metadata publication blocked seq={pending['sequence']} "
                  f"reason={reason}")
            self._pending_identity_confirmation = None
            return
        if not self._temporal_observations_match(pending, self._last_temporal_observation):
            self._pending_identity_confirmation = None
            return
        metadata = self._metadata_prefetch.get(pending["metadata_key"])
        if metadata is None:
            return
        self._pending_identity_confirmation = None
        print(f"ShazamService: Publishing prefetched metadata seq={pending['sequence']} "
              f"identity={pending['identity']}")
        self._publish_confirmed_track(
            pending["group_index"], pending["sequence"], metadata[0], metadata[1],
            identity=pending.get("identity"),
        )

    def _handle_prefetch_finished(self, generation, key, metadata, error_text):
        # This slot is invoked by a Qt signal; workers never touch history or UI.
        if generation != self._generation or not self._active or self._shutting_down:
            return
        print(f"ShazamService: Metadata prefetch completed identity={key[2:]} "
              f"usable={metadata is not None} error={error_text!r}")
        preview = self._tentative_candidate
        pending = self._pending_identity_confirmation
        if preview and preview["observation"].get("metadata_key") == key:
            # A rapid ID-only second vote may confirm before title/artist are
            # fetched. Still emit the first-vote UI/search event followed by
            # confirmation in that order, rather than silently skipping search.
            # But a *superseded* confirmation must never revive an old video.
            safe_late_vote = (not preview["confirmed"] or
                              pending is not None and pending.get("metadata_key") == key
                              and self._pending_publication_safe(pending)[0])
            newly_visible = (self._emit_tentative_if_ready(allow_confirmed=True)
                             if safe_late_vote else False)
            if newly_visible and preview["confirmed"]:
                self._tentative_timer.stop()
                self._remember_confirmed_search(preview)
                print(f"ShazamService: Late ID-only metadata completed two-vote "
                      f"candidate firstSeq={preview['sequence']}; enable PLAY")
                self.candidate_confirmed.emit(preview["sequence"], preview["entry"])
        if pending and pending["metadata_key"] == key:
            self._try_publish_identity_confirmation()

    def _drain_temporal_results(self):
        while True:
            made_progress = False
            while self._next_temporal_sequence in self._temporal_results:
                sequence = self._next_temporal_sequence
                observation = self._temporal_results.pop(sequence)
                self._next_temporal_sequence += 1
                self._process_temporal_observation(sequence, observation)
                made_progress = True

            # A newer pair may bypass an older stuck head, but any gap INSIDE
            # that pair must consist entirely of completed allowed neutral results.
            if self._bypass_stuck_temporal_head():
                made_progress = True
                continue

            if not made_progress:
                break
            # We drained everything currently contiguous and there is no safe bypass.
            break

    def _resolve_or_publish_confirmed_track(
        self, group_index, request_sequence, raw_result, fallback_title, fallback_artist
    ):
        if (request_sequence < self._next_temporal_sequence or
                request_sequence in self._temporal_results):
            return  # Old/duplicate callbacks must not fetch or revise metadata.
        fallback_title = str(fallback_title or "").strip()
        source = str(raw_result.get("source") or "") if isinstance(raw_result, dict) else ""
        fallback_artist = clean_artist(fallback_artist, source)
        if not isinstance(raw_result, dict):
            # Unit-test/direct-call compatibility and any legacy path that already carries
            # finalized metadata: publish immediately without creating another worker.
            if fallback_title:
                self._stage_temporal_result(
                    group_index, request_sequence, fallback_title, fallback_artist, raw_result
                )
            return

        # Enforce the same structural contract at the Python boundary. In
        # particular, legacy helpers must not send homepage text to Apple lookup
        # and turn a chart's unrelated Apple link into a trusted track identity.
        evidence_source = source.split("+", 1)[0].casefold()
        allowed_sources = {
            "track-primary-header", "jsonld", "recognition-response",
            "recognition-network", "shazam-route", "route-only",
        }
        if evidence_source not in allowed_sources:
            print(
                f"ShazamService: Rejected non-structural source={source!r} "
                f"seq={request_sequence}; rebuild the WebView helper if using an old binary"
            )
            self._stage_temporal_result(group_index, request_sequence)
            return

        # The WebView is already localized to ja-JP. Strong request-scoped sources that
        # contain both title and artist need no Apple round trip at all. This is the common
        # fast path for JSON-LD / recognition-response results and removes the ~0.5-1 s
        # Apple lookup/search that used to sit on the critical path. Incomplete/route-only
        # results still use the existing resolver below.
        from app.services.itunes_metadata import clean_metadata, is_generic_ui_title
        raw_title = clean_metadata(raw_result.get("title"))
        raw_artist = clean_artist(raw_result.get("artist"), source)
        if raw_result.get("artist") and not raw_artist:
            print(
                f"ShazamService: Rejected untrusted/UI artist seq={request_sequence} "
                f"source={source} artist={raw_result.get('artist')!r}"
            )
        # Preserve identity and title, but never send rejected UI text into lookup,
        # caches, or the exception/timeout fallback.
        raw_result = dict(raw_result, artist=raw_artist)
        source = str(raw_result.get("source") or "").split("+", 1)[0].casefold()
        trusted_complete_sources = {
            "jsonld", "recognition-response", "recognition-network",
            "track-primary-header",
        }
        try:
            identity = track_identity(raw_result)
        except IdentityConflict as exc:
            print(f"ShazamService: Rejected conflicting identity seq={request_sequence}: {exc}")
            self._stage_temporal_result(group_index, request_sequence)
            return
        live_complete = (
            complete_metadata(raw_title, raw_artist)
            if source in trusted_complete_sources else None
        )
        if identity is not None:
            key = self._metadata_key(identity)
            if live_complete:
                self._metadata_prefetch.seed(key, *live_complete)
                print(f"ShazamService: Using complete live Shazam metadata without Apple lookup "
                      f"group={group_index + 1} seq={request_sequence} source={source} "
                      f"title={raw_title!r} artist={raw_artist!r}")
            else:
                state = self._metadata_prefetch.request(
                    self._generation, key, raw_result,
                    str(self.config.get("shazam_language", "ja-JP") or "ja-JP"),
                    str(self.config.get("shazam_endpoint_country", "JP") or "JP"),
                    self._cancel_current, self._prefetch_finished.emit,
                )
                print(f"ShazamService: Metadata prefetch {state} seq={request_sequence} "
                      f"identity={identity}")
            metadata = self._metadata_prefetch.get(key) or ("", "")
            # This stages the identity immediately, even while its HTTP is pending.
            # Only a matching second observation plus usable metadata may publish.
            self._stage_temporal_result(
                group_index, request_sequence,
                metadata[0] or (raw_title if source in trusted_complete_sources else ""),
                metadata[1] or (raw_artist if source in trusted_complete_sources else ""),
                raw_result,
                identity_pending=True,
            )
            return
        if live_complete:
            print(f"ShazamService: Using complete live Shazam metadata without Apple lookup "
                  f"group={group_index + 1} seq={request_sequence} source={source} "
                  f"title={raw_title!r} artist={raw_artist!r}")
            self._stage_temporal_result(
                group_index, request_sequence, raw_title, raw_artist, raw_result
            )
            return

        self._metadata_raw_results[request_sequence] = raw_result
        generation = self._generation
        cancelled = self._cancel_current
        language = str(self.config.get("shazam_language", "ja-JP") or "ja-JP")
        country = str(self.config.get("shazam_endpoint_country", "JP") or "JP")

        def resolve_once():
            title, artist = fallback_title, fallback_artist
            error_text = ""
            try:
                if cancelled.is_set() or generation != self._generation:
                    return
                with self._metadata_lock:
                    resolved_title, resolved_artist = self._metadata_resolver.resolve(
                        raw_result, language, country, cancelled
                    )
                title = str(resolved_title or title).strip()
                artist = clean_artist(resolved_artist) or fallback_artist
            except Exception as exc:
                error_text = str(exc)
            if cancelled.is_set() or self._shutting_down:
                return
            try:
                self._metadata_finished.emit(
                    generation, group_index, request_sequence, title, artist, error_text
                )
            except RuntimeError:
                pass

        threading.Thread(
            target=resolve_once,
            name=f"ShazamMetadata-{request_sequence}",
            daemon=True,
        ).start()

    def _handle_metadata_finished(
        self, generation, group_index, request_sequence, title, artist, error_text
    ):
        if generation != self._generation or not self._active:
            return
        title = str(title or "").strip()
        artist = clean_artist(artist)
        if error_text:
            print(
                f"ShazamService: Deferred metadata resolution failed seq={request_sequence}: "
                f"{error_text}; using Shazam fallback"
            )
        raw_result = self._metadata_raw_results.pop(request_sequence, None)
        if not title:
            print(
                f"ShazamService: Confirmed identity had no usable title after metadata "
                f"resolution group={group_index + 1} seq={request_sequence}"
            )
            self._stage_temporal_result(group_index, request_sequence, raw_result=raw_result)
            return
        print(
            f"ShazamService: Deferred metadata resolved group={group_index + 1} "
            f"seq={request_sequence} title={title!r} artist={artist!r}"
        )
        self._stage_temporal_result(
            group_index, request_sequence, title, artist, raw_result
        )

    def _handle_confirmed_group_result(
        self, group_index, request_sequence, lanes, results, raw_results=None
    ):
        raw_results = list(raw_results or [None] * len(results))
        if len(raw_results) < len(results):
            raw_results.extend([None] * (len(results) - len(raw_results)))

        # Each slot listens independently. Confirmation still requires two real
        # recognitions; only bounded, completed neutral gaps may be bridged.
        result = results[0] if results else ("", "", "")
        raw_result = raw_results[0] if raw_results else None
        if not self._lane_result_is_usable(result, raw_result):
            title, artist, error = result
            if error:
                print(
                    f"ShazamService: Recognition failed lane={lanes[0] + 1 if lanes else '?'} "
                    f"seq={request_sequence}: {error}"
                )
            else:
                print(f"ShazamService: No match seq={request_sequence}")
            self._stage_temporal_result(
                group_index, request_sequence, raw_result=raw_result,
                no_match=self._plain_no_match_result(result, raw_result),
                transport_error=self._neutral_audio_request_error(result, raw_result),
            )
            return

        title, artist, _error = result
        self._resolve_or_publish_confirmed_track(
            group_index, request_sequence, raw_result, title, artist
        )

    def _publish_confirmed_track(self, group_index, request_sequence, title, artist,
                                 *, identity=None):
        # Groups can finish out of order. Never allow an older listening window to
        # overwrite a newer confirmed track that has already been published.
        if request_sequence < self._latest_published_sequence:
            print(
                f"ShazamService: Ignored stale confirmed group={group_index + 1} "
                f"seq={request_sequence} latest={self._latest_published_sequence} "
                f"title={title!r}"
            )
            return
        self._latest_published_sequence = request_sequence

        artist = clean_artist(artist)
        title = str(title or "").strip()
        track_key = (title, artist)
        previous_track = self._last_track
        previous_identity = self._last_published_identity
        # Compare successive confirmed results, including suppressed variants.
        self._last_track = track_key
        self._last_published_identity = identity
        same_track = self._same_presentation(previous_track, track_key, previous_identity, identity)
        owner = self._tentative_candidate
        associated_owner = bool(owner and owner.get("confirmed") and (
            identity is not None and owner["observation"].get("identity") == identity
            or identity is None and is_same_track(owner["observation"]["track"], track_key)))
        keywords = owner.get("keywords", []) if associated_owner else self.config.get(CONFIG_KEY, [])
        title = strip_title_keywords(title, keywords)
        if associated_owner and owner.get("presentation_retained") and owner.get("entry"):
            title = owner["entry"][1]
        if same_track:
            self.status_changed.emit(f"Shazam: {artist} - {title}")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = (timestamp, title, artist)
        # The first live confirmed result after application startup still needs to
        # trigger playback, even if it matches the restored history head.
        if (
            previous_track is None
            and self._history
            and is_same_track(self._history[0][1:], self._display_track(track_key))
        ):
            self._history[0] = entry
        else:
            self._history.insert(0, entry)
        del self._history[self.HISTORY_LIMIT:]
        self._save_history()

        # Correlate the committed history entry to its *own* two-vote candidate.
        # The previous confirmed UI candidate can still be stored when a new
        # ID-only song is published; it must never suppress the new song search.
        candidate = self._tentative_candidate
        candidate_identity = candidate["observation"].get("identity") if candidate else None
        associated = bool(candidate and candidate["confirmed"] and (
            identity is not None and candidate_identity == identity
            or identity is None and is_same_track(candidate["observation"]["track"], track_key)
        ))
        receipt = self._confirmed_search_receipt
        reused = bool(receipt is not None and identity is not None and
                      self._receipt_matches(identity, track_key) and
                      (not candidate or not candidate["emitted"]))
        self._last_publication_search_reused = reused
        self._last_publication_candidate_id = (
            receipt[1] if reused else candidate["sequence"] if associated else None
        )
        self.history_updated.emit(list(self._history))
        self.new_track_detected.emit(entry)
        candidate = self._tentative_candidate
        if (candidate and candidate.get("confirmed") and
                candidate["sequence"] <= request_sequence and
                candidate["sequence"] == self._last_publication_candidate_id):
            self._tentative_timer.stop()
            self._tentative_candidate = None
        self.status_changed.emit(f"Shazam: {artist} - {title}")
        lane_text = "+".join(str(lane + 1) for lane in self._group_lanes(group_index))
        print(
            f"ShazamService: Confirmed group={group_index + 1} lanes={lane_text} "
            f"seq={request_sequence} {artist} - {title}"
        )

    def _close_stream(self):
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    @staticmethod
    def _get_base_dir():
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parents[2]

    def _get_history_path(self):
        return self._get_base_dir() / "shazam_history.json"

    def _ensure_history_file(self):
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._history_path.exists():
                with open(self._history_path, "w", encoding="utf-8") as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
                    f.write("\n")
        except Exception as e:
            print(f"ShazamService: Failed to create history file: {e}")

    @staticmethod
    def _clean_field(value):
        return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())

    def _load_history(self):
        entries = []
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, list):
                raise ValueError("history root must be a JSON array")

            for item in data[:self.HISTORY_LIMIT]:
                if not isinstance(item, dict):
                    continue
                timestamp = self._clean_field(item.get("timestamp", ""))
                title = self._clean_field(item.get("title", ""))
                artist = self._clean_field(item.get("artist", ""))
                # Title-only recognitions are valid during live detection;
                # retain them on restart as well.
                if not timestamp or not title:
                    continue
                entries.append((timestamp, title, artist))
                comment = self._clean_field(item.get("comment", ""))
                if comment:
                    from app.services.utanet_service import track_key
                    self._history_comments[track_key(title, artist)] = (comment, str(item.get("tieup_url", "") or ""))
        except Exception as e:
            print(f"ShazamService: Failed to load history: {e}")
        # Compact in memory only. The existing file is not rewritten merely
        # by opening the app; the next new recognition saves the compact view.
        return deduplicate_history(entries)[:self.HISTORY_LIMIT]

    def _save_history(self):
        try:
            payload = []
            for timestamp, title, artist in self._history[:self.HISTORY_LIMIT]:
                item = {
                    "timestamp": self._clean_field(timestamp),
                    "title": self._clean_field(title),
                    "artist": self._clean_field(artist),
                }
                from app.services.utanet_service import track_key
                metadata = getattr(self, "_history_comments", {}).get(track_key(title, artist))
                if metadata and metadata[0]:
                    item["comment"] = metadata[0]
                    if metadata[1]:
                        item["tieup_url"] = metadata[1]
                payload.append(item)

            with open(self._history_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
        except Exception as e:
            print(f"ShazamService: Failed to save history: {e}")

    def get_tieup_comment(self, title, artist):
        from app.services.utanet_service import track_key
        return getattr(self, "_history_comments", {}).get(track_key(title, artist), ("", ""))[0]

    def set_tieup_comment(self, title, artist, comment, source_url=""):
        """Main-thread metadata update; NEVER publish a new track or first vote."""
        from app.services.utanet_service import track_key, clean_text
        comment = clean_text(comment)[:600]
        if not comment:
            return False
        if not hasattr(self, "_history_comments"):
            self._history_comments = {}
        key = track_key(title, artist)
        old = self._history_comments.get(key, ("", ""))
        if old[0] == comment:
            return False
        self._history_comments[key] = (comment, str(source_url or ""))
        if len(self._history_comments) > 512:
            preserved = {track_key(row[1], row[2]) for row in self._history}
            for obsolete in list(self._history_comments):
                if obsolete not in preserved and obsolete != key:
                    del self._history_comments[obsolete]
                    if len(self._history_comments) <= 256:
                        break
        if any(track_key(row[1], row[2]) == key for row in self._history):
            self._save_history()
        return True
