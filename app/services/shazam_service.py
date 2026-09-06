import io
import json
import queue
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal


class ShazamService(QObject):
    """Microphone -> fixed ring buffer -> Shazam recognition service.

    Audio capture and the public Qt/history contract are unchanged. Two staggered
    recognition lanes send fresh WAV snapshots to independent private WebView2 helpers
    running the official Shazam website. This avoids a single no-match blocking the next
    attempt for ~18 seconds while keeping each helper strictly one-request-at-a-time.
    No ShazamIO library or private Shazam API is used.
    """

    history_updated = Signal(list)
    new_track_detected = Signal(tuple)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    _recognition_finished = Signal(int, int, int, str, str, str)

    SAMPLE_RATE = 16000
    CHANNELS = 1
    DTYPE = "int16"
    MIN_RECORDING_SECONDS = 5
    MAX_RECORDING_SECONDS = 20
    DEFAULT_RECORDING_SECONDS = 6
    RECOGNITION_INTERVAL_MS = 100
    PARALLEL_RECOGNITION_LANES = 2
    LANE_STAGGER_SECONDS = 4.0
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
        # Both lanes share one resolver/cache. Serialize only the short Apple metadata
        # phase so simultaneous Shazam matches do not duplicate Apple Search/Lookup calls.
        self._metadata_resolver = ITunesMetadataResolver()
        self._metadata_lock = threading.Lock()
        self._lane_busy = [False] * self.PARALLEL_RECOGNITION_LANES
        self._next_lane_index = 0
        self._next_lane_slot_at = 0.0
        self._request_sequence = 0
        self._latest_published_sequence = -1
        self._cancel_current = threading.Event()
        self._shutting_down = False
        self._next_request_at = 0.0

        self._recognize_timer = QTimer(self)
        self._recognize_timer.setInterval(self.RECOGNITION_INTERVAL_MS)
        self._recognize_timer.timeout.connect(self._recognize_tick)
        self._recognition_finished.connect(self._handle_recognition_finished)

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
            self._next_request_at = 0.0
            self._recognition_busy = False
            self._lane_busy = [False] * self.PARALLEL_RECOGNITION_LANES
            self._next_lane_index = 0
            self._next_lane_slot_at = 0.0
            self._request_sequence = 0
            self._latest_published_sequence = -1
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
            # Prewarm both hidden WebView2 helpers immediately. Their isolated profiles
            # allow a second recognition to run while the first Shazam attempt is still
            # pending, which removes the 18 s no-match + 14 s next-match serial penalty.
            language = str(self.config.get("shazam_language", "ja-JP") or "ja-JP")
            for lane_index, work_queue in enumerate(self._work_queues):
                try:
                    work_queue.put_nowait(("prewarm", self._generation, language, self._cancel_current))
                except queue.Full:
                    pass
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
        for recognizer in self._web_recognizers:
            recognizer.close()
        self._generation += 1
        self._active = False
        self._recognize_timer.stop()
        self._close_stream()
        self._recognition_busy = False
        self._lane_busy = [False] * self.PARALLEL_RECOGNITION_LANES
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

    def _recognize_tick(self):
        if not self._active:
            return

        now = time.monotonic()
        if now < self._next_lane_slot_at:
            return

        recording_seconds = self._recording_seconds
        samples = self._snapshot_latest(recording_seconds)
        if samples is None:
            return

        # Build one fresh snapshot, then give it to whichever staggered lane is free.
        # A second lane may therefore start while the first Shazam website attempt is
        # still pending, instead of waiting 15-18 seconds for a no-match deadline.
        samples = self._resample_to_shazam_rate(samples, self._capture_sample_rate)
        audio_bytes = self._pcm_to_wav_bytes(samples)
        generation = self._generation
        language = str(self.config.get("shazam_language", "ja-JP") or "ja-JP")
        country = str(self.config.get("shazam_endpoint_country", "JP") or "JP")

        for offset in range(self.PARALLEL_RECOGNITION_LANES):
            lane_index = (self._next_lane_index + offset) % self.PARALLEL_RECOGNITION_LANES
            if self._lane_busy[lane_index]:
                continue
            request_sequence = self._request_sequence
            try:
                self._work_queues[lane_index].put_nowait((
                    generation, lane_index, request_sequence, audio_bytes,
                    language, country, self._cancel_current,
                ))
            except queue.Full:
                # The lane may still be finishing its prewarm command. Try the other
                # lane now; the 100 ms timer will retry if both queues are occupied.
                continue

            self._request_sequence += 1
            self._lane_busy[lane_index] = True
            self._recognition_busy = any(self._lane_busy)
            self._next_lane_index = (lane_index + 1) % self.PARALLEL_RECOGNITION_LANES
            self._next_lane_slot_at = now + self.LANE_STAGGER_SECONDS
            self._next_request_at = 0.0
            print(
                f"ShazamService: Scheduled recognition lane={lane_index + 1} "
                f"seq={request_sequence} stagger={self.LANE_STAGGER_SECONDS:.1f}s"
            )
            return

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
                    continue

                generation, item_lane, request_sequence, audio_bytes, language, country, cancelled = item
                if cancelled.is_set() or generation != self._generation:
                    continue
                title, artist, error_text = "", "", ""
                try:
                    result = recognizer.recognize(audio_bytes, language, cancelled)
                    if cancelled.is_set():
                        continue
                    # Shared cache + lock prevents two staggered lanes from issuing the
                    # same Apple Search/Lookup requests at the same time.
                    with self._metadata_lock:
                        title, artist = self._metadata_resolver.resolve(
                            result, language, country, cancelled
                        )
                    if title or artist:
                        print(
                            f"ShazamService: WebView2 lane={lane_index + 1} seq={request_sequence} result "
                            f"source={result.get('source', '')} "
                            f"appleTrackId={result.get('appleTrackId', '')} "
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

    def _handle_recognition_finished(
        self, generation, lane_index, request_sequence, title, artist, error_text
    ):
        if 0 <= lane_index < len(self._lane_busy):
            self._lane_busy[lane_index] = False
        self._recognition_busy = any(self._lane_busy)

        if generation != self._generation:
            return
        if not self._active:
            return

        try:
            if error_text:
                message = (
                    f"Shazam recognition lane {lane_index + 1} failed: {error_text}"
                )
                # One lane failing is recoverable while the other lane is still racing.
                # Keep the user-visible error channel for the case where both are idle.
                if not self._recognition_busy:
                    self.error_occurred.emit(message)
                    self.status_changed.emit(message)
                print(f"ShazamService: {message}")
                return

            if not title:
                if not self._recognition_busy:
                    self.status_changed.emit("Shazam: no match")
                return

            # Parallel lanes can finish out of order. Never allow an older audio snapshot
            # to overwrite a newer recognized track that has already been published.
            if request_sequence < self._latest_published_sequence:
                print(
                    f"ShazamService: Ignored stale lane result lane={lane_index + 1} "
                    f"seq={request_sequence} latest={self._latest_published_sequence} "
                    f"title={title!r}"
                )
                return
            self._latest_published_sequence = request_sequence

            # Artist can be temporarily unavailable when the live Shazam route exposes
            # title identity before artist metadata. Keep the valid title instead of
            # dropping a recognition; YouTube search accepts an empty artist.
            artist = str(artist or "").strip()
            track_key = (title.casefold(), artist.casefold())
            if self._is_same_track(self._last_track, track_key):
                self.status_changed.emit(f"Shazam: {artist} - {title}")
                return

            self._last_track = track_key
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = (timestamp, title, artist)
            self._history.insert(0, entry)
            self._history = self._history[:self.HISTORY_LIMIT]
            self._save_history()

            self.history_updated.emit(list(self._history))
            self.new_track_detected.emit(entry)
            self.status_changed.emit(f"Shazam: {artist} - {title}")
            print(
                f"ShazamService: Recognized lane={lane_index + 1} seq={request_sequence} "
                f"{artist} - {title}"
            )
        finally:
            # Keep both lanes filled on a staggered cadence. The global slot protects
            # Shazam.com from simultaneous bursts while still overlapping slow attempts.
            if self._active and generation == self._generation:
                self._recognize_tick()

    @staticmethod
    def _track_field_matches(previous_value, current_value):
        """Return True when two Shazam fields are close enough for de-duplication.

        Compare case-insensitively.  A field contributes its first six characters;
        when it is shorter than six characters, the whole field is used.  The
        comparison is symmetric so a short/base title also matches a longer
        variant regardless of which one Shazam reports first.
        """
        previous = str(previous_value or "").strip().casefold()
        current = str(current_value or "").strip().casefold()
        if not previous or not current:
            return False

        previous_prefix = previous[: min(6, len(previous))]
        current_prefix = current[: min(6, len(current))]
        return previous_prefix in current or current_prefix in previous

    @classmethod
    def _is_same_track(cls, previous_track, current_track):
        if not previous_track or not current_track:
            return False

        previous_title, previous_artist = previous_track
        current_title, current_artist = current_track
        if not cls._track_field_matches(previous_title, current_title):
            return False
        # A title-only fallback should not add the same song to history every cycle.
        if not str(previous_artist or "").strip() or not str(current_artist or "").strip():
            return True
        return cls._track_field_matches(previous_artist, current_artist)

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
                if not timestamp or not title or not artist:
                    continue
                entries.append((timestamp, title, artist))
        except Exception as e:
            print(f"ShazamService: Failed to load history: {e}")
        return entries[:self.HISTORY_LIMIT]

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
