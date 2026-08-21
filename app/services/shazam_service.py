import asyncio
import io
import json
import queue
import sys
import threading
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal


class ShazamService(QObject):
    """Microphone -> fixed ring buffer -> Shazam recognition service.

    Audio capture stays in the main Python process. Shazam network recognition runs on
    one daemon worker thread so the Qt UI and the PortAudio callback are never blocked.
    """

    history_updated = Signal(list)
    new_track_detected = Signal(tuple)
    status_changed = Signal(str)
    error_occurred = Signal(str)
    _recognition_finished = Signal(int, str, str, str)

    SAMPLE_RATE = 16000
    CHANNELS = 1
    DTYPE = "int16"
    MIN_RECORDING_SECONDS = 5
    MAX_RECORDING_SECONDS = 20
    DEFAULT_RECORDING_SECONDS = 6
    RECOGNITION_INTERVAL_MS = 3000
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

        self._work_queue = queue.Queue(maxsize=1)
        self._worker_thread = None

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
            self._ensure_worker_thread()
            self._generation += 1
            self._recognition_busy = False
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
        if not self._active and self._stream is None:
            return

        self._generation += 1
        self._active = False
        self._recognize_timer.stop()
        self._close_stream()
        self._recognition_busy = False
        self.status_changed.emit("Shazam: stopped")
        print("ShazamService: Stopped")

    def reload_settings(self):
        """Apply microphone and Shazam locale settings while Shazam mode is active."""
        was_active = self._active
        if was_active:
            self.stop()
            self.start()

    def shutdown(self):
        self.stop()
        if self._worker_thread is not None and self._worker_thread.is_alive():
            try:
                self._work_queue.put_nowait(None)
            except queue.Full:
                # The worker is daemonized. If a request is in flight it may finish during
                # process teardown, so do not block the GUI waiting for it.
                pass

    def _ensure_worker_thread(self):
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._work_queue = queue.Queue(maxsize=1)
        self._worker_thread = threading.Thread(
            target=self._worker_main,
            name="ShazamRecognitionWorker",
            daemon=True,
        )
        self._worker_thread.start()

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
        """Fail immediately with a readable error when the frozen build is incomplete."""
        required = ("aiohttp_retry", "shazamio", "shazamio_core")
        missing = []
        for module_name in required:
            try:
                __import__(module_name)
            except ModuleNotFoundError as e:
                missing.append(e.name or module_name)
            except Exception as e:
                raise RuntimeError(f"Failed to load {module_name}: {e}") from e
        if missing:
            names = ", ".join(dict.fromkeys(missing))
            raise RuntimeError(f"Missing Shazam runtime dependency: {names}")

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
        if not self._active or self._recognition_busy:
            return

        recording_seconds = self._recording_seconds
        samples = self._snapshot_latest(recording_seconds)
        if samples is None:
            return

        samples = self._resample_to_shazam_rate(samples, self._capture_sample_rate)
        audio_bytes = self._pcm_to_wav_bytes(samples)
        generation = self._generation
        try:
            self._work_queue.put_nowait((generation, audio_bytes, recording_seconds))
            self._recognition_busy = True
        except queue.Full:
            # Never accumulate recognition work. The next timer tick uses the newest audio.
            return

    def _worker_main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        shazam = None
        shazam_profile = None
        runtime_error = ""

        try:
            from aiohttp_retry import ExponentialRetry
            from shazamio import HTTPClient, Shazam
        except Exception as e:
            runtime_error = f"Shazam initialization failed: {e}"

        while True:
            item = self._work_queue.get()
            if item is None:
                break

            generation, audio_bytes, recording_seconds = item
            title = ""
            artist = ""
            error_text = runtime_error

            if not runtime_error:
                language = str(self.config.get("shazam_language", "ja-JP") or "ja-JP").strip()
                # Backward compatibility with older builds that used the invalid
                # Japanese tag "jp-JP". BCP 47 uses "ja" for Japanese.
                if language.lower() == "jp-jp":
                    language = "ja-JP"
                endpoint_country = str(
                    self.config.get("shazam_endpoint_country", "JP") or "JP"
                ).strip().upper()
                profile = (language, endpoint_country, recording_seconds)

                if shazam is None or profile != shazam_profile:
                    try:
                        # Keep retries bounded. A long 429/5xx retry chain would hurt realtime behavior.
                        shazam = Shazam(
                            language=language,
                            endpoint_country=endpoint_country,
                            http_client=HTTPClient(
                                retry_options=ExponentialRetry(
                                    attempts=3,
                                    max_timeout=5,
                                    statuses={429, 500, 502, 503, 504},
                                )
                            ),
                            segment_duration_seconds=recording_seconds,
                        )
                        shazam_profile = profile
                        error_text = ""
                        print(
                            "ShazamService: Recognition profile -> "
                            f"language={language}, country={endpoint_country}, "
                            f"recording={recording_seconds}s"
                        )
                    except Exception as e:
                        shazam = None
                        shazam_profile = None
                        error_text = f"Shazam initialization failed: {e}"

                if shazam is not None:
                    try:
                        result = loop.run_until_complete(shazam.recognize(audio_bytes))
                        track = result.get("track") if isinstance(result, dict) else None
                        if isinstance(track, dict):
                            title = str(track.get("title") or "").strip()
                            artist = str(track.get("subtitle") or "").strip()
                    except Exception as e:
                        error_text = str(e)

            self._recognition_finished.emit(generation, title, artist, error_text)

        try:
            loop.stop()
            loop.close()
        except Exception:
            pass

    def _handle_recognition_finished(self, generation, title, artist, error_text):
        if generation != self._generation:
            return

        self._recognition_busy = False
        if not self._active:
            return

        if error_text:
            message = f"Shazam recognition failed: {error_text}"
            self.error_occurred.emit(message)
            self.status_changed.emit(message)
            print(f"ShazamService: {message}")
            return

        if not title or not artist:
            self.status_changed.emit("Shazam: no match")
            return

        track_key = (title.casefold(), artist.casefold())
        if track_key == self._last_track:
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
        print(f"ShazamService: Recognized {artist} - {title}")

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
