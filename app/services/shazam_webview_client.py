"""Private stdio IPC for the official Shazam website hosted in WebView2.

No Shazam private API requests are made here. The helper runs the website and
feeds it the recording supplied by the existing PortAudio capture service.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid


class RecognitionCancelled(Exception):
    pass


class RecognitionAborted(Exception):
    """One request was intentionally aborted while the helper remains reusable."""
    pass


def helper_path() -> Path:
    """Support source runs and PyInstaller onedir/onefile layouts."""
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
        if getattr(sys, "_MEIPASS", None):
            roots.append(Path(sys._MEIPASS))
    roots.append(Path(__file__).resolve().parents[2])
    for root in roots:
        for relative in (
            "shazam_webview/ShazamWebViewBridge.exe",
            "native/ShazamWebViewBridge/publish/ShazamWebViewBridge.exe",
        ):
            path = root / relative
            if path.is_file():
                return path
    raise RuntimeError(
        "Shazam WebView2 helper was not found. Run build.cmd or "
        "native\\ShazamWebViewBridge\\build.cmd first."
    )


def normalize_language(value: str) -> str:
    value = str(value or "ja-JP").strip()
    if value.lower() == "jp-jp":
        value = "ja-JP"
    return value if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value) else "ja-JP"


class WebViewRecognizer:
    """One helper, one in-flight request; stop() can abort from the Qt thread.

    All stdout/stderr pipes are drained by daemon readers so neither process
    blocks on a full pipe. A stopped generation never starts a new helper.
    """
    PROTOCOL = 2
    MAX_REPLY_CHARS = 1024 * 1024
    LIVE_AUDIO_QUEUE_MAX = 32
    LIVE_AUDIO_BATCH_SECONDS = 0.20
    LIVE_AUDIO_FLUSH_SECONDS = 0.08

    def __init__(self, instance_id: str = "main"):
        instance_id = re.sub(r"[^A-Za-z0-9_-]", "", str(instance_id or "main"))[:32] or "main"
        self._instance_id = instance_id
        self._lock = threading.Lock()
        self._stdin_lock = threading.Lock()
        self._process = None
        self._queue = None
        self._language = None
        self._ready = False
        self._active_request_id = None
        self._live_audio_queue = None
        self._live_sample_rate = None

    @staticmethod
    def check_available():
        if sys.platform != "win32":
            raise RuntimeError("Shazam.com + WebView2 recognition requires Windows.")
        return helper_path()

    @staticmethod
    def _read_stdout(process, destination):
        try:
            while True:
                line = process.stdout.readline(WebViewRecognizer.MAX_REPLY_CHARS + 1)
                if not line:
                    break
                if len(line) > WebViewRecognizer.MAX_REPLY_CHARS:
                    destination.put({"type": "fatal", "error": "Oversized helper response"})
                    break
                try:
                    payload = json.loads(line)
                except (ValueError, TypeError):
                    print("ShazamWebView: ignored non-JSON stdout")
                    continue
                if isinstance(payload, dict):
                    destination.put(payload)
        except (OSError, ValueError) as exc:
            destination.put({"type": "fatal", "error": str(exc)})
        finally:
            destination.put({"type": "eof"})

    @staticmethod
    def _read_stderr(process):
        try:
            for line in process.stderr:
                # The helper never logs audio/base64, tokens, or response bodies.
                print("ShazamWebView: " + line.rstrip()[:2000])
        except (OSError, ValueError):
            pass

    @staticmethod
    def _next_message(destination, deadline, cancelled):
        while time.monotonic() < deadline:
            if cancelled.is_set():
                raise RecognitionCancelled()
            try:
                return destination.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
        raise TimeoutError("Shazam WebView2 did not respond before the timeout.")

    def _ensure_started(self, language, cancelled):
        language = normalize_language(language)
        if cancelled.is_set():
            raise RecognitionCancelled()
        with self._lock:
            reusable = (
                self._process is not None and self._process.poll() is None
                and self._language == language
            )
        if not reusable:
            self.close()
            executable = self.check_available()
            args = [str(executable), "--language", language, "--instance", self._instance_id]
            if os.environ.get("VJ_SHAZAM_DEBUG", "").lower() in ("1", "true", "yes"):
                args.append("--debug")
            with self._lock:
                if cancelled.is_set():
                    raise RecognitionCancelled()
                process = subprocess.Popen(
                    args,
                    cwd=str(executable.parent),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                destination = queue.Queue()
                self._process, self._queue = process, destination
                self._language, self._ready = language, False
                threading.Thread(target=self._read_stdout, args=(process, destination),
                                 name="ShazamWebViewStdout", daemon=True).start()
                threading.Thread(target=self._read_stderr, args=(process,),
                                 name="ShazamWebViewStderr", daemon=True).start()
        with self._lock:
            process, destination, ready = self._process, self._queue, self._ready
        if process is None or destination is None or cancelled.is_set():
            raise RecognitionCancelled()
        if not ready:
            deadline = time.monotonic() + 65
            while True:
                message = self._next_message(destination, deadline, cancelled)
                kind = message.get("type")
                if kind == "ready":
                    if message.get("protocol") != self.PROTOCOL:
                        raise RuntimeError("Shazam helper protocol mismatch. Rebuild both components.")
                    with self._lock:
                        if self._process is process:
                            self._ready = True
                    break
                if kind in ("fatal", "eof"):
                    raise RuntimeError(message.get("error") or "Shazam helper closed during startup.")
        return process, destination

    def prewarm(self, language: str, cancelled: threading.Event) -> None:
        """Start WebView2 while live microphone capture is becoming ready."""
        self._ensure_started(language, cancelled)

    def _write_request(self, process, payload) -> None:
        with self._stdin_lock:
            if process.poll() is not None:
                raise RecognitionCancelled()
            process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            process.stdin.flush()

    def _send_live_audio_batch(self, process, request_id: str, pcm_bytes: bytes) -> bool:
        if not pcm_bytes:
            return True
        try:
            self._write_request(process, {
                "type": "audio",
                "id": request_id,
                "pcm16Base64": base64.b64encode(pcm_bytes).decode("ascii"),
            })
            return True
        except (RecognitionCancelled, OSError, ValueError):
            return False

    def _live_audio_feeder(
        self, process, request_id: str, audio_queue: queue.Queue, sample_rate: int,
        cancelled: threading.Event,
    ) -> None:
        # PortAudio's callback must never perform IPC/base64 work. It only enqueues the
        # immutable PCM bytes; this daemon batches ~200 ms and feeds WebView2 separately.
        target_bytes = max(640, int(sample_rate * 2 * self.LIVE_AUDIO_BATCH_SECONDS))
        pending = bytearray()
        last_flush = time.monotonic()
        while not cancelled.is_set():
            with self._lock:
                active = (
                    self._process is process
                    and self._active_request_id == request_id
                    and self._live_audio_queue is audio_queue
                )
            if not active or process.poll() is not None:
                break
            try:
                chunk = audio_queue.get(timeout=0.04)
            except queue.Empty:
                chunk = None
            if chunk is False:  # request-scoped sentinel
                break
            if chunk:
                pending.extend(chunk)

            now = time.monotonic()
            while len(pending) >= target_bytes:
                batch = bytes(pending[:target_bytes])
                del pending[:target_bytes]
                if not self._send_live_audio_batch(process, request_id, batch):
                    return
                last_flush = now
            if pending and now - last_flush >= self.LIVE_AUDIO_FLUSH_SECONDS:
                batch = bytes(pending)
                pending.clear()
                if not self._send_live_audio_batch(process, request_id, batch):
                    return
                last_flush = now

    def feed_live_audio(self, pcm_bytes: bytes, sample_rate: int) -> bool:
        """Queue captured mono int16 PCM for this lane's active recognition.

        This method is deliberately non-blocking because it is called from the PortAudio
        callback. If the IPC feeder is briefly behind, discard the oldest queued block so
        Shazam continues receiving current audio rather than delayed audio.
        """
        if not pcm_bytes:
            return False
        with self._lock:
            audio_queue = self._live_audio_queue
            active = self._active_request_id is not None
            expected_rate = self._live_sample_rate
        if not active or audio_queue is None or int(sample_rate) != expected_rate:
            return False
        payload = bytes(pcm_bytes)
        try:
            audio_queue.put_nowait(payload)
            return True
        except queue.Full:
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                audio_queue.put_nowait(payload)
                return True
            except queue.Full:
                return False

    def recognize_live(self, sample_rate: int, language: str, cancelled: threading.Event) -> dict:
        if cancelled.is_set():
            raise RecognitionCancelled()
        sample_rate = int(sample_rate)
        if not 8000 <= sample_rate <= 96000:
            raise ValueError("Expected an 8..96 kHz live PCM sample rate.")
        request_id = None
        audio_queue = None
        try:
            process, destination = self._ensure_started(language, cancelled)
            request_id = uuid.uuid4().hex
            audio_queue = queue.Queue(maxsize=self.LIVE_AUDIO_QUEUE_MAX)
            if cancelled.is_set() or process.poll() is not None:
                raise RecognitionCancelled()
            self._write_request(process, {
                "type": "recognize-live",
                "id": request_id,
                "sampleRate": sample_rate,
            })
            with self._lock:
                if self._process is process:
                    self._active_request_id = request_id
                    self._live_audio_queue = audio_queue
                    self._live_sample_rate = sample_rate
            threading.Thread(
                target=self._live_audio_feeder,
                args=(process, request_id, audio_queue, sample_rate, cancelled),
                name=f"ShazamLiveAudio-{self._instance_id}",
                daemon=True,
            ).start()

            deadline = time.monotonic() + 85
            while True:
                message = self._next_message(destination, deadline, cancelled)
                kind = message.get("type")
                if kind in ("fatal", "eof"):
                    raise RuntimeError(message.get("error") or "Shazam helper exited unexpectedly.")
                if kind == "result" and message.get("id") == request_id:
                    if message.get("error"):
                        error = str(message["error"])
                        if "shazam recognition cancelled" in error.casefold() or \
                           "cancelled after peer confirmation timeout" in error.casefold():
                            raise RecognitionAborted(error)
                        raise RuntimeError(error)
                    return message
        except (RecognitionCancelled, RecognitionAborted):
            raise
        except Exception:
            self.close()
            raise
        finally:
            if request_id:
                with self._lock:
                    if self._active_request_id == request_id:
                        self._active_request_id = None
                        q = self._live_audio_queue
                        self._live_audio_queue = None
                        self._live_sample_rate = None
                    else:
                        q = None
                if q is not None:
                    try:
                        q.put_nowait(False)
                    except queue.Full:
                        pass

    def cancel_current(self) -> bool:
        """Ask the live helper to abort its current recognition without killing WebView2.

        The helper keeps its WebView/profile warm. Cancellation remains request-scoped so
        a stopped/restarted Shazam mode can release one lane without killing unrelated lanes.
        """
        with self._lock:
            process = self._process
            request_id = self._active_request_id
        if process is None or not request_id or process.poll() is not None:
            return False
        try:
            with self._stdin_lock:
                if process.poll() is not None:
                    return False
                request = {"type": "cancel", "id": request_id}
                process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                process.stdin.flush()
            return True
        except (OSError, ValueError):
            self.close()
            return False

    def close(self):
        """Non-blocking for the GUI: terminate now, reap/close pipes on a daemon."""
        with self._lock:
            process, self._process = self._process, None
            self._queue = None
            self._language, self._ready = None, False
            self._active_request_id = None
            audio_queue = self._live_audio_queue
            self._live_audio_queue = None
            self._live_sample_rate = None
        if audio_queue is not None:
            try:
                audio_queue.put_nowait(False)
            except queue.Full:
                pass
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
        except OSError:
            pass

        def reap():
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    try:
                        if stream:
                            stream.close()
                    except (OSError, ValueError):
                        pass
        threading.Thread(target=reap, name="ShazamWebViewReaper", daemon=True).start()
