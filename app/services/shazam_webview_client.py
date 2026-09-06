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
    PROTOCOL = 1
    MAX_REPLY_CHARS = 1024 * 1024

    def __init__(self, instance_id: str = "main"):
        instance_id = re.sub(r"[^A-Za-z0-9_-]", "", str(instance_id or "main"))[:32] or "main"
        self._instance_id = instance_id
        self._lock = threading.Lock()
        self._process = None
        self._queue = None
        self._language = None
        self._ready = False

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
        """Start WebView2 while the microphone ring buffer is still filling."""
        self._ensure_started(language, cancelled)

    def recognize(self, audio_bytes: bytes, language: str, cancelled: threading.Event) -> dict:
        if cancelled.is_set():
            raise RecognitionCancelled()
        if not (44 <= len(audio_bytes) <= 1500000) or audio_bytes[:4] != b"RIFF":
            raise ValueError("Expected a bounded PCM WAV recording.")
        try:
            process, destination = self._ensure_started(language, cancelled)
            request_id = uuid.uuid4().hex
            request = {
                "type": "recognize", "id": request_id,
                "wavBase64": base64.b64encode(audio_bytes).decode("ascii"),
            }
            if cancelled.is_set() or process.poll() is not None:
                raise RecognitionCancelled()
            # Only the recognition worker writes stdin; close() does not write it.
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 85
            while True:
                message = self._next_message(destination, deadline, cancelled)
                kind = message.get("type")
                if kind in ("fatal", "eof"):
                    raise RuntimeError(message.get("error") or "Shazam helper exited unexpectedly.")
                if kind == "result" and message.get("id") == request_id:
                    if message.get("error"):
                        raise RuntimeError(str(message["error"]))
                    return message
        except RecognitionCancelled:
            raise
        except Exception:
            self.close()
            raise

    def close(self):
        """Non-blocking for the GUI: terminate now, reap/close pipes on a daemon."""
        with self._lock:
            process, self._process = self._process, None
            self._queue = None
            self._language, self._ready = None, False
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
