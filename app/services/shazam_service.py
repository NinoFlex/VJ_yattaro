import io
import json
import queue
import re
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from app.services.track_matching import deduplicate_history, is_same_track


class ShazamService(QObject):
    """Microphone -> fixed ring buffer -> Shazam recognition service.

    Audio capture and the public Qt/history contract are unchanged. Recognition uses four
    independent WebView2 workers in a shared pool. New recognition starts are spaced by at
    least three seconds. If all workers are busy, the due slot is held instead of discarded;
    a fresh audio snapshot is submitted as soon as any worker becomes free, and the next
    start is scheduled three seconds after that actual dispatch. A track is published only
    after two consecutive time-separated recognition results match using the existing
    title-prefix rule. No ShazamIO library or private Shazam API is used.
    """

    history_updated = Signal(list)
    new_track_detected = Signal(tuple)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    _recognition_finished = Signal(int, int, int, str, str, str)
    _metadata_finished = Signal(int, int, int, str, str, str)

    SAMPLE_RATE = 16000
    CHANNELS = 1
    DTYPE = "int16"
    MIN_RECORDING_SECONDS = 5
    MAX_RECORDING_SECONDS = 20
    DEFAULT_RECORDING_SECONDS = 6
    RECOGNITION_INTERVAL_MS = 100
    RECOGNITION_GROUPS = 4
    LANES_PER_GROUP = 1
    PARALLEL_RECOGNITION_LANES = RECOGNITION_GROUPS * LANES_PER_GROUP
    GROUP_STAGGER_SECONDS = 3.0
    LANE_STAGGER_SECONDS = 0.0
    HISTORY_LIMIT = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        from app.services.config_service import ConfigService

        self.config = ConfigService()
        self._capture_sample_rate = self.SAMPLE_RATE
        self._recording_seconds = self._get_recording_seconds()
        self._ring = np.zeros(
            self._capture_sample_rate * self._recording_seconds,
            dtype=np.int16,
        )
        self._ring_lock = threading.Lock()
        self._write_pos = 0
        self._samples_available = 0

        self._stream = None
        self._active = False
        self._recognition_busy = False
        self._generation = 0
        self._last_track = None

        from app.services.shazam_webview_client import WebViewRecognizer
        from app.services.itunes_metadata import ITunesMetadataResolver
        self._work_queues = [queue.Queue(maxsize=1) for _ in range(self.PARALLEL_RECOGNITION_LANES)]
        self._worker_threads = [None] * self.PARALLEL_RECOGNITION_LANES
        self._web_recognizers = [
            WebViewRecognizer(f"lane-{index + 1}") for index in range(self.PARALLEL_RECOGNITION_LANES)
        ]
        # Metadata localization is deferred until one lane has produced a usable
        # recognition result. The lock protects the shared resolver cache when staggered
        # lanes finish close together.
        self._metadata_resolver = ITunesMetadataResolver()
        self._metadata_lock = threading.Lock()
        self._lane_busy = [False] * self.PARALLEL_RECOGNITION_LANES
        self._lane_ready = [threading.Event() for _ in range(self.PARALLEL_RECOGNITION_LANES)]
        self._group_busy = [False] * self.RECOGNITION_GROUPS
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
        self._metadata_raw_results = {}
        self._cancel_current = threading.Event()
        self._shutting_down = False

        self._recognize_timer = QTimer(self)
        self._recognize_timer.setInterval(self.RECOGNITION_INTERVAL_MS)
        self._recognize_timer.timeout.connect(self._recognize_tick)
        self._recognition_finished.connect(self._handle_recognition_finished)
        self._metadata_finished.connect(self._handle_metadata_finished)

        self._history_path = self._get_history_path()
        self._ensure_history_file()
        self._history = self._load_history()

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

    def get_history(self):
        return list(self._history)

    def is_active(self):
        return self._active

    def start(self):
        if self._active:
            return True

        try:
            import sounddevice as sd

            self._check_shazam_runtime_dependencies()

            device = self.config.get("shazam_input_device", None)
            if device in ("", -1):
                device = None
            elif device is not None:
                device = int(device)

            capture_rate = self._select_capture_sample_rate(sd, device)
            self._recording_seconds = self._get_recording_seconds()
            self._configure_capture_buffer(capture_rate)

            # Only load/start the Shazam worker after the selected microphone has
            # passed validation. This keeps a failed Shazam start as lightweight as possible.
            self._ensure_worker_threads()
            self._generation += 1
            self._cancel_current = threading.Event()
            self._recognition_busy = False
            self._lane_busy = [False] * self.PARALLEL_RECOGNITION_LANES
            self._group_busy = [False] * self.RECOGNITION_GROUPS
            self._next_group_index = 0
            self._next_group_slot_at = 0.0
            self._slot_wait_started_at = None
            self._slot_wait_logged = False
            self._request_sequence = 0
            self._pending_group_results.clear()
            self._raw_lane_results.clear()
            self._latest_published_sequence = -1
            self._temporal_results.clear()
            self._next_temporal_sequence = 0
            self._last_temporal_observation = None
            self._metadata_raw_results.clear()
            for ready in self._lane_ready:
                ready.clear()
            self._stream = sd.InputStream(
                device=device,
                samplerate=capture_rate,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                callback=self._audio_callback,
                blocksize=0,
            )
            self._stream.start()
            self._active = True
            # Prewarm all four hidden WebView2 helpers immediately. Once ready, they form
            # a shared worker pool; recognition dispatches are spaced by at least 3 seconds.
            language = str(self.config.get("shazam_language", "ja-JP") or "ja-JP")
            for lane_index, work_queue in enumerate(self._work_queues):
                try:
                    work_queue.put_nowait(("prewarm", self._generation, language, self._cancel_current))
                except queue.Full:
                    # Prewarm is optional. Do not permanently block a lane if an old
                    # cancelled item is still leaving the queue during a quick restart.
                    self._lane_ready[lane_index].set()
            self._recognize_timer.start()
            self.status_changed.emit("Shazam: microphone capture started")
            print(
                f"ShazamService: Started (device={device}, "
                f"capture={capture_rate}Hz/mono/int16, shazam={self.SAMPLE_RATE}Hz, "
                f"recording={self._recording_seconds}s)"
            )
            return True
        except Exception as e:
            self._active = False
            self._close_stream()
            message = f"Shazam microphone start failed: {e}"
            self.error_occurred.emit(message)
            self.status_changed.emit(message)
            print(f"ShazamService: {message}")
            return False

    def stop(self):
        self._cancel_current.set()
        for pending in list(self._pending_group_results.values()):
            timer = pending.get("confirmation_timer")
            if timer is not None:
                timer.stop()
                if hasattr(timer, "deleteLater"):
                    timer.deleteLater()
        for recognizer in self._web_recognizers:
            recognizer.close()
        self._generation += 1
        self._active = False
        self._recognize_timer.stop()
        self._close_stream()
        self._recognition_busy = False
        self._lane_busy = [False] * self.PARALLEL_RECOGNITION_LANES
        self._group_busy = [False] * self.RECOGNITION_GROUPS
        self._slot_wait_started_at = None
        self._slot_wait_logged = False
        self._pending_group_results.clear()
        self._raw_lane_results.clear()
        self._temporal_results.clear()
        self._last_temporal_observation = None
        self._metadata_raw_results.clear()
        for ready in self._lane_ready:
            ready.clear()
        self.status_changed.emit("Shazam: stopped")
        print("ShazamService: Stopped")

    def reload_settings(self):
        """Apply microphone and Shazam locale settings while Shazam mode is active."""
        was_active = self._active
        if was_active:
            self.stop()
            self.start()

    def shutdown(self):
        self._shutting_down = True
        self.stop()
        # Drop queued (not yet started) recordings and wake both idle workers.
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

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"ShazamService: Audio status: {status}")
        if not self._active:
            return

        samples = np.asarray(indata[:, 0], dtype=np.int16)
        count = len(samples)
        ring_size = len(self._ring)

        with self._ring_lock:
            if count >= ring_size:
                self._ring[:] = samples[-ring_size:]
                self._write_pos = 0
                self._samples_available = ring_size
                return

            first = min(count, ring_size - self._write_pos)
            self._ring[self._write_pos:self._write_pos + first] = samples[:first]
            remaining = count - first
            if remaining:
                self._ring[:remaining] = samples[first:]
            self._write_pos = (self._write_pos + count) % ring_size
            self._samples_available = min(ring_size, self._samples_available + count)

    @staticmethod
    def _check_shazam_runtime_dependencies():
        from app.services.shazam_webview_client import WebViewRecognizer
        WebViewRecognizer.check_available()

    @classmethod
    def _select_capture_sample_rate(cls, sd, device):
        """Pick a sample rate accepted by the selected PortAudio input device.

        16 kHz is preferred to keep the capture buffer small. Some Windows host APIs
        (especially WDM-KS/WASAPI endpoints) only accept their native 44.1/48 kHz
        rate, so fall back to the device default and resample only the configured
        recognition snapshot.
        """
        rates = [cls.SAMPLE_RATE]
        try:
            info = sd.query_devices(device, "input") if device is not None else sd.query_devices(kind="input")
            default_rate = int(round(float(info.get("default_samplerate", 0) or 0)))
            if default_rate > 0 and default_rate not in rates:
                rates.append(default_rate)
        except Exception:
            pass

        for fallback in (48000, 44100, 32000):
            if fallback not in rates:
                rates.append(fallback)

        errors = []
        for rate in rates:
            try:
                sd.check_input_settings(
                    device=device,
                    channels=cls.CHANNELS,
                    dtype=cls.DTYPE,
                    samplerate=rate,
                )
                return int(rate)
            except Exception as e:
                errors.append(f"{rate}Hz: {e}")

        raise RuntimeError("No supported input sample rate. " + " / ".join(errors))

    def _get_recording_seconds(self):
        """Return the configured Shazam recording duration clamped to 5..20 seconds."""
        try:
            seconds = int(self.config.get(
                "shazam_recording_seconds",
                self.DEFAULT_RECORDING_SECONDS,
            ))
        except (TypeError, ValueError):
            seconds = self.DEFAULT_RECORDING_SECONDS
        return max(self.MIN_RECORDING_SECONDS, min(self.MAX_RECORDING_SECONDS, seconds))

    def _configure_capture_buffer(self, sample_rate):
        self._capture_sample_rate = int(sample_rate)
        with self._ring_lock:
            self._ring = np.zeros(
                self._capture_sample_rate * self._recording_seconds,
                dtype=np.int16,
            )
            self._write_pos = 0
            self._samples_available = 0

    def _reset_ring(self):
        with self._ring_lock:
            self._ring.fill(0)
            self._write_pos = 0
            self._samples_available = 0

    def _snapshot_latest(self, seconds):
        sample_count = int(self._capture_sample_rate * seconds)
        with self._ring_lock:
            if self._samples_available < sample_count:
                return None

            ring_size = len(self._ring)
            start = (self._write_pos - sample_count) % ring_size
            if start < self._write_pos:
                return self._ring[start:self._write_pos].copy()

            return np.concatenate((self._ring[start:], self._ring[:self._write_pos])).copy()

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
                busy = sum(1 for value in self._lane_busy if value)
                ready = sum(1 for value in self._lane_ready if value.is_set())
                print(
                    f"ShazamService: Recognition slot waiting seq={self._request_sequence} "
                    f"busy={busy}/{self.PARALLEL_RECOGNITION_LANES} "
                    f"ready={ready}/{self.PARALLEL_RECOGNITION_LANES}"
                )
                self._slot_wait_logged = True
            return

        recording_seconds = self._recording_seconds
        samples = self._snapshot_latest(recording_seconds)
        if samples is None:
            return

        # Take the snapshot only when a worker is actually available. This avoids queueing
        # stale audio while preserving the intended time separation between confirmations.
        samples = self._resample_to_shazam_rate(samples, self._capture_sample_rate)
        audio_bytes = self._pcm_to_wav_bytes(samples)
        generation = self._generation
        language = str(self.config.get("shazam_language", "ja-JP") or "ja-JP")
        country = str(self.config.get("shazam_endpoint_country", "JP") or "JP")
        lanes = self._group_lanes(group_index)
        request_sequence = self._request_sequence

        self._group_busy[group_index] = True
        for lane_index in lanes:
            self._lane_busy[lane_index] = True
        self._pending_group_results[request_sequence] = {
            "group_index": group_index,
            "lanes": lanes,
            "results": {},
        }

        try:
            for lane_offset, lane_index in enumerate(lanes):
                lane_start_delay = lane_offset * self.LANE_STAGGER_SECONDS
                self._work_queues[lane_index].put_nowait((
                    generation, group_index, lane_index, request_sequence, audio_bytes,
                    language, country, self._cancel_current, lane_start_delay,
                ))
        except queue.Full:
            # A shutdown/restart race may make a queue unavailable after the readiness
            # check. Roll back without consuming the slot; the timer retries it shortly.
            self._pending_group_results.pop(request_sequence, None)
            self._group_busy[group_index] = False
            for lane_index in lanes:
                self._lane_busy[lane_index] = False
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
        # real dispatch, so consecutive audio snapshots remain time-separated.
        self._next_group_slot_at = now + self.GROUP_STAGGER_SECONDS
        lane_text = "+".join(str(lane + 1) for lane in lanes)
        wait_text = f" waited={waited:.1f}s" if waited > 0.05 else ""
        print(
            f"ShazamService: Scheduled recognition group={group_index + 1} "
            f"lanes={lane_text} seq={request_sequence} "
            f"slotInterval={self.GROUP_STAGGER_SECONDS:.1f}s pool=shared{wait_text}"
        )

    def _worker_main(self, lane_index):
        from app.services.shazam_webview_client import RecognitionCancelled

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
                    try:
                        recognizer.prewarm(language, cancelled)
                        print(f"ShazamService: WebView2 lane {lane_index + 1} prewarm ready")
                    except RecognitionCancelled:
                        pass
                    except Exception as exc:
                        # Prewarm is an optimization only. The real recognition request
                        # will retry startup and surface an error if it still cannot run.
                        print(f"ShazamService: WebView2 lane {lane_index + 1} prewarm failed: {exc}")
                    finally:
                        if not cancelled.is_set() and generation == self._generation:
                            self._lane_ready[lane_index].set()
                    continue

                (
                    generation, group_index, item_lane, request_sequence, audio_bytes,
                    language, country, cancelled, lane_start_delay,
                ) = item
                if cancelled.is_set() or generation != self._generation:
                    continue
                title, artist, error_text = "", "", ""
                raw_result = None
                try:
                    if lane_start_delay > 0 and cancelled.wait(lane_start_delay):
                        continue
                    if cancelled.is_set() or generation != self._generation:
                        continue
                    result = recognizer.recognize(audio_bytes, language, cancelled)
                    if cancelled.is_set():
                        continue

                    # Do NOT run Apple/iTunes localization here. Keeping metadata work
                    # out of the lane worker releases this WebView immediately for its
                    # next fixed slot; localization happens after recognition returns.
                    raw_result = dict(result or {})
                    title = str(raw_result.get("title") or "").strip()
                    artist = str(raw_result.get("artist") or "").strip()
                    self._raw_lane_results[(generation, item_lane, request_sequence)] = raw_result
                    print(
                        f"ShazamService: WebView2 raw group={group_index + 1} "
                        f"lane={lane_index + 1} seq={request_sequence} "
                        f"source={raw_result.get('source', '')} "
                        f"shazamTrackId={raw_result.get('shazamTrackId', '')} "
                        f"appleTrackId={raw_result.get('appleTrackId', '')} "
                        f"title={title!r} artist={artist!r}"
                    )
                except RecognitionCancelled:
                    continue
                except Exception as exc:
                    error_text = str(exc)
                if not cancelled.is_set() and not self._shutting_down:
                    try:
                        self._recognition_finished.emit(
                            generation, item_lane, request_sequence, title, artist, error_text
                        )
                    except RuntimeError:
                        # The QObject may have been destroyed during application shutdown.
                        break
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
            # A completed worker may satisfy a slot that was waiting because all four
            # workers were busy. The scheduler still enforces 3 seconds between dispatches.
            if self._active and generation == self._generation:
                self._recognize_tick()

    @staticmethod
    def _temporal_observations_match(previous, current):
        if not previous or not current:
            return False
        # Keep the existing title-prefix same-track rule unchanged. Two independent,
        # time-separated observations are the only confirmation guard used here.
        return is_same_track(previous["track"], current["track"])

    def _stage_temporal_result(
        self, group_index, request_sequence, title="", artist="", raw_result=None
    ):
        if request_sequence < self._next_temporal_sequence:
            return
        title = str(title or "").strip()
        artist = str(artist or "").strip()
        observation = None
        if title:
            observation = {
                "group_index": group_index,
                "sequence": request_sequence,
                "track": (title, artist),
            }
        self._temporal_results[request_sequence] = observation
        self._drain_temporal_results()

    def _drain_temporal_results(self):
        while self._next_temporal_sequence in self._temporal_results:
            sequence = self._next_temporal_sequence
            observation = self._temporal_results.pop(sequence)
            self._next_temporal_sequence += 1

            if observation is None:
                self._last_temporal_observation = None
                continue

            previous = self._last_temporal_observation
            self._last_temporal_observation = observation
            title, artist = observation["track"]
            if previous is None:
                print(
                    f"ShazamService: Candidate pending seq={sequence} "
                    f"title={title!r} artist={artist!r}"
                )
                continue

            if not self._temporal_observations_match(previous, observation):
                print(
                    f"ShazamService: Candidate changed/rejected prevSeq={previous['sequence']} "
                    f"seq={sequence} title={title!r}"
                )
                continue

            print(
                f"ShazamService: Temporal confirmation prevSeq={previous['sequence']} "
                f"seq={sequence} {artist} - {title}"
            )
            self._publish_confirmed_track(
                observation["group_index"], sequence, title, artist
            )

    def _resolve_or_publish_confirmed_track(
        self, group_index, request_sequence, raw_result, fallback_title, fallback_artist
    ):
        fallback_title = str(fallback_title or "").strip()
        fallback_artist = str(fallback_artist or "").strip()
        if not isinstance(raw_result, dict):
            # Unit-test/direct-call compatibility and any legacy path that already carries
            # finalized metadata: publish immediately without creating another worker.
            if fallback_title:
                self._stage_temporal_result(
                    group_index, request_sequence, fallback_title, fallback_artist, raw_result
                )
            return

        # The WebView is already localized to ja-JP. Strong request-scoped sources that
        # contain both title and artist need no Apple round trip at all. This is the common
        # fast path for JSON-LD / recognition-response results and removes the ~0.5-1 s
        # Apple lookup/search that used to sit on the critical path. Incomplete/route-only
        # results still use the existing resolver below.
        from app.services.itunes_metadata import clean_metadata, is_generic_ui_title
        raw_title = clean_metadata(raw_result.get("title"))
        raw_artist = clean_metadata(raw_result.get("artist"))
        source = str(raw_result.get("source") or "").split("+", 1)[0].casefold()
        trusted_complete_sources = {
            "jsonld", "recognition-response", "recognition-network",
            "track-heading", "track-heading-nearby", "route-slug-dom",
        }
        if (
            raw_title and raw_artist
            and not is_generic_ui_title(raw_title)
            and source in trusted_complete_sources
        ):
            title = fallback_title or raw_title
            artist = fallback_artist or raw_artist
            print(
                f"ShazamService: Using complete live Shazam metadata without Apple lookup "
                f"group={group_index + 1} seq={request_sequence} "
                f"source={source} title={title!r} artist={artist!r}"
            )
            self._stage_temporal_result(
                group_index, request_sequence, title, artist, raw_result
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
                artist = str(resolved_artist or artist).strip()
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
        artist = str(artist or "").strip()
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

        # New architecture: one independently timed recognition per slot. Accuracy is
        # obtained from two consecutive slots, not by replaying one snapshot to a peer.
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
            self._stage_temporal_result(group_index, request_sequence, raw_result=raw_result)
            return

        title, artist, _error = result
        self._resolve_or_publish_confirmed_track(
            group_index, request_sequence, raw_result, title, artist
        )

    def _publish_confirmed_track(self, group_index, request_sequence, title, artist):
        # Groups can finish out of order. Never allow an older audio snapshot to
        # overwrite a newer confirmed track that has already been published.
        if request_sequence < self._latest_published_sequence:
            print(
                f"ShazamService: Ignored stale confirmed group={group_index + 1} "
                f"seq={request_sequence} latest={self._latest_published_sequence} "
                f"title={title!r}"
            )
            return
        self._latest_published_sequence = request_sequence

        artist = str(artist or "").strip()
        title = str(title or "").strip()
        track_key = (title, artist)
        previous_track = self._last_track
        # Compare successive confirmed results, including suppressed variants.
        self._last_track = track_key
        if is_same_track(previous_track, track_key):
            self.status_changed.emit(f"Shazam: {artist} - {title}")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = (timestamp, title, artist)
        # The first live confirmed result after application startup still needs to
        # trigger playback, even if it matches the restored history head.
        if (
            previous_track is None
            and self._history
            and is_same_track(self._history[0][1:], track_key)
        ):
            self._history[0] = entry
        else:
            self._history.insert(0, entry)
        del self._history[self.HISTORY_LIMIT:]
        self._save_history()

        self.history_updated.emit(list(self._history))
        self.new_track_detected.emit(entry)
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

    @classmethod
    def _resample_to_shazam_rate(cls, samples, source_rate):
        source_rate = int(source_rate)
        if source_rate == cls.SAMPLE_RATE:
            return samples.astype(np.int16, copy=False)
        if len(samples) == 0:
            return samples.astype(np.int16, copy=False)

        # Fast path for exact integer ratios such as the common 48 kHz -> 16 kHz case.
        if source_rate % cls.SAMPLE_RATE == 0:
            step = source_rate // cls.SAMPLE_RATE
            return samples[::step].astype(np.int16, copy=False)

        # Generic lightweight linear resampling for 44.1 kHz and other native rates.
        target_len = max(1, int(round(len(samples) * cls.SAMPLE_RATE / source_rate)))
        source_pos = np.arange(len(samples), dtype=np.float64)
        target_pos = np.linspace(0, len(samples) - 1, target_len, dtype=np.float64)
        converted = np.interp(target_pos, source_pos, samples.astype(np.float64, copy=False))
        return np.clip(converted, -32768, 32767).astype(np.int16)

    @classmethod
    def _pcm_to_wav_bytes(cls, samples):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(cls.CHANNELS)
            wav.setsampwidth(np.dtype(np.int16).itemsize)
            wav.setframerate(cls.SAMPLE_RATE)
            wav.writeframes(samples.astype(np.int16, copy=False).tobytes())
        return buffer.getvalue()

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
        except Exception as e:
            print(f"ShazamService: Failed to load history: {e}")
        # Compact in memory only. The existing file is not rewritten merely
        # by opening the app; the next new recognition saves the compact view.
        return deduplicate_history(entries)[:self.HISTORY_LIMIT]

    def _save_history(self):
        try:
            payload = []
            for timestamp, title, artist in self._history[:self.HISTORY_LIMIT]:
                payload.append({
                    "timestamp": self._clean_field(timestamp),
                    "title": self._clean_field(title),
                    "artist": self._clean_field(artist),
                })

            with open(self._history_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
        except Exception as e:
            print(f"ShazamService: Failed to save history: {e}")
