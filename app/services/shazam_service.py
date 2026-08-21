import asyncio
import io
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
    RING_SECONDS = 8
    RECOGNITION_SECONDS = 6
    RECOGNITION_INTERVAL_MS = 3000
    HISTORY_LIMIT = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        from app.services.config_service import ConfigService

        self.config = ConfigService()
        self._ring = np.zeros(self.SAMPLE_RATE * self.RING_SECONDS, dtype=np.int16)
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
        """Return [(device_index, display_name), ...] for input-capable devices."""
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            result = []
            for index, device in enumerate(devices):
                if int(device.get("max_input_channels", 0)) > 0:
                    name = str(device.get("name", f"Device {index}"))
                    hostapi_index = int(device.get("hostapi", -1))
                    hostapi_name = ""
                    try:
                        hostapis = sd.query_hostapis()
                        if 0 <= hostapi_index < len(hostapis):
                            hostapi_name = str(hostapis[hostapi_index].get("name", ""))
                    except Exception:
                        pass
                    display = f"{index}: {name}"
                    if hostapi_name:
                        display += f" [{hostapi_name}]"
                    result.append((index, display))
            return result, ""
        except Exception as e:
            return [], str(e)

    def get_history(self):
        return list(self._history)

    def is_active(self):
        return self._active

    def start(self):
        if self._active:
            return True

        try:
            import sounddevice as sd

            device = self.config.get("shazam_input_device", None)
            if device in ("", -1):
                device = None
            elif device is not None:
                device = int(device)

            sd.check_input_settings(
                device=device,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                samplerate=self.SAMPLE_RATE,
            )

            # Only load/start the Shazam worker after the selected microphone has
            # passed validation. This keeps a failed Shazam start as lightweight as possible.
            self._ensure_worker_thread()
            self._reset_ring()
            self._generation += 1
            self._recognition_busy = False
            self._stream = sd.InputStream(
                device=device,
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                callback=self._audio_callback,
                blocksize=0,
            )
            self._stream.start()
            self._active = True
            self._recognize_timer.start()
            self.status_changed.emit("Shazam: microphone capture started")
            print(f"ShazamService: Started (device={device}, 16kHz/mono/int16)")
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
        """Apply microphone selection immediately when Shazam mode is active."""
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

    def _reset_ring(self):
        with self._ring_lock:
            self._ring.fill(0)
            self._write_pos = 0
            self._samples_available = 0

    def _snapshot_latest(self, seconds):
        sample_count = int(self.SAMPLE_RATE * seconds)
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

        samples = self._snapshot_latest(self.RECOGNITION_SECONDS)
        if samples is None:
            return

        audio_bytes = self._pcm_to_wav_bytes(samples)
        generation = self._generation
        try:
            self._work_queue.put_nowait((generation, audio_bytes))
            self._recognition_busy = True
        except queue.Full:
            # Never accumulate recognition work. The next timer tick uses the newest audio.
            return

    def _worker_main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        shazam = None
        shazam_error = ""

        try:
            from aiohttp_retry import ExponentialRetry
            from shazamio import HTTPClient, Shazam

            # Keep retries bounded. A long 429/5xx retry chain would hurt realtime behavior.
            shazam = Shazam(
                http_client=HTTPClient(
                    retry_options=ExponentialRetry(
                        attempts=3,
                        max_timeout=5,
                        statuses={429, 500, 502, 503, 504},
                    )
                ),
                segment_duration_seconds=self.RECOGNITION_SECONDS,
            )
        except Exception as e:
            shazam_error = f"Shazam initialization failed: {e}"

        while True:
            item = self._work_queue.get()
            if item is None:
                break

            generation, audio_bytes = item
            title = ""
            artist = ""
            error_text = shazam_error

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
        return self._get_base_dir() / "shazam_history.log"

    def _ensure_history_file(self):
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history_path.touch(exist_ok=True)
        except Exception as e:
            print(f"ShazamService: Failed to create history file: {e}")

    @staticmethod
    def _clean_field(value):
        return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())

    def _load_history(self):
        entries = []
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-self.HISTORY_LIMIT:]

            for raw_line in reversed(lines):
                line = raw_line.rstrip("\r\n")
                if not line:
                    continue
                parts = line.split(" | ", 2)
                if len(parts) != 3:
                    continue
                timestamp, artist, title = parts
                entries.append((timestamp, title, artist))
        except Exception as e:
            print(f"ShazamService: Failed to load history: {e}")
        return entries[:self.HISTORY_LIMIT]

    def _save_history(self):
        try:
            with open(self._history_path, "w", encoding="utf-8", newline="\n") as f:
                # File is chronological so the latest recognition is visible at the tail.
                for timestamp, title, artist in reversed(self._history[:self.HISTORY_LIMIT]):
                    safe_artist = self._clean_field(artist)
                    safe_title = self._clean_field(title)
                    f.write(f"{timestamp} | {safe_artist} | {safe_title}\n")
        except Exception as e:
            print(f"ShazamService: Failed to save history: {e}")
