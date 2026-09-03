"""Local HTTP bridge between the desktop controller and the browser player."""

import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, Signal


class FeedbackSignals(QObject):
    feedback_received = Signal(dict)


feedback_signals = FeedbackSignals()


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class PlayerCommandHandler(BaseHTTPRequestHandler):
    command_queue = []
    pending_commands = {}
    queue_lock = threading.Lock()
    state_callback = None
    web_root = None
    session_id = ""
    _last_poll_time = 0.0
    _last_feedback_time = 0.0

    MAX_QUEUE_SIZE = 64
    COMMAND_TTL_SECONDS = {
        "PRELOAD": 20.0,
        "PLAY": 12.0,
        "PAUSE_PLAYER": 4.0,
        "RESUME_PLAYER": 4.0,
        "SELECT_PLAYER": 4.0,
        "REWIND": 4.0,
        "FORWARD": 4.0,
        "REQUEST_PLAYER_STATE": 5.0,
        "SET_CONFIG": 30.0,
    }

    def log_message(self, format, *args):
        pass

    @classmethod
    def reset_state(cls, session_id):
        with cls.queue_lock:
            cls.command_queue.clear()
            cls.pending_commands.clear()
            cls.session_id = str(session_id or "")
            cls._last_poll_time = 0.0
            cls._last_feedback_time = 0.0

    @classmethod
    def _purge_expired_locked(cls, now=None):
        now = time.time() if now is None else now
        cls.command_queue[:] = [
            cmd for cmd in cls.command_queue if float(cmd.get("_expiresAt", now + 1)) > now
        ]
        expired_pending = [
            command_id
            for command_id, meta in cls.pending_commands.items()
            if float(meta.get("expiresAt", now + 1)) <= now
        ]
        for command_id in expired_pending:
            cls.pending_commands.pop(command_id, None)

    @classmethod
    def _coalesce_key(cls, command):
        cmd = str(command.get("cmd", "") or "").upper()
        player = str(command.get("playerId", "") or "").upper()
        if cmd in ("SELECT_PLAYER", "REQUEST_PLAYER_STATE", "SET_CONFIG"):
            return (cmd, "")
        if cmd in ("PAUSE_PLAYER", "RESUME_PLAYER"):
            return ("PLAYER_TRANSPORT", player)
        return None

    @classmethod
    def enqueue_command(cls, command):
        now = time.time()
        command = dict(command or {})
        cmd = str(command.get("cmd", "") or "").upper()
        command["cmd"] = cmd
        command.setdefault("commandId", uuid.uuid4().hex)
        command.setdefault("timestamp", now)
        command["sessionId"] = cls.session_id
        ttl = cls.COMMAND_TTL_SECONDS.get(cmd, 8.0)
        command["_expiresAt"] = now + ttl

        dropped = None
        with cls.queue_lock:
            cls._purge_expired_locked(now)

            # A new deck selection supersedes queued transport actions for the other deck.
            # This preserves the intended SELECT -> RESUME order on rapid A/B clicks.
            if cmd == "SELECT_PLAYER":
                selected_player = str(command.get("playerId", "") or "").upper()
                if selected_player in ("A", "B"):
                    cls.command_queue[:] = [
                        existing
                        for existing in cls.command_queue
                        if not (
                            str(existing.get("cmd", "") or "").upper()
                            in ("PAUSE_PLAYER", "RESUME_PLAYER")
                            and str(existing.get("playerId", "") or "").upper()
                            != selected_player
                        )
                    ]

            coalesce_key = cls._coalesce_key(command)
            replaced = False
            if coalesce_key is not None:
                for index, existing in enumerate(cls.command_queue):
                    if cls._coalesce_key(existing) == coalesce_key:
                        # Replace in place so coalescing does not reorder related commands.
                        cls.command_queue[index] = command
                        replaced = True
                        break

            if not replaced:
                while len(cls.command_queue) >= cls.MAX_QUEUE_SIZE:
                    dropped = cls.command_queue.pop(0)
                cls.command_queue.append(command)

        if dropped:
            print(
                "PlayerHttpServer: Command queue full; dropped oldest command "
                f"{dropped.get('cmd')} ({dropped.get('commandId')})"
            )
        return command["commandId"]

    @classmethod
    def acknowledge_command(cls, command_id, status="accepted"):
        if not command_id:
            return
        with cls.queue_lock:
            meta = cls.pending_commands.pop(str(command_id), None)
        if meta is not None:
            print(
                f"PlayerHttpServer: ACK {command_id} status={status} "
                f"cmd={meta.get('cmd', '')}"
            )

    @classmethod
    def _public_command(cls, command):
        return {key: value for key, value in command.items() if not key.startswith("_")}

    @classmethod
    def _session_matches(cls, supplied):
        return bool(cls.session_id) and str(supplied or "") == cls.session_id

    def _send_json(self, status_code, data):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/poll":
            self.handle_poll(parsed_path)
        elif parsed_path.path == "/status":
            self.handle_status()
        elif (
            parsed_path.path == "/"
            or parsed_path.path.startswith("/web/")
            or parsed_path.path.endswith(".html")
            or parsed_path.path.endswith(".js")
            or parsed_path.path.endswith(".css")
        ):
            self.handle_static(parsed_path.path)
        else:
            self.send_error(404, "Not Found")

    def handle_static(self, request_path: str):
        try:
            if not self.web_root:
                self.send_error(500, "Web root not configured")
                return
            if request_path == "/":
                self.send_response(302)
                self.send_header("Location", "/player.html")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return

            rel = request_path.lstrip("/")
            if rel.startswith("web/"):
                rel = rel[len("web/") :]
            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                self.send_error(400, "Invalid path")
                return

            file_path = (Path(self.web_root) / rel_path).resolve()
            web_root_resolved = Path(self.web_root).resolve()
            if web_root_resolved not in file_path.parents and file_path != web_root_resolved:
                self.send_error(400, "Invalid path")
                return
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404, "Not Found")
                return

            ext = file_path.suffix.lower()
            content_type = "application/octet-stream"
            if ext == ".html":
                content_type = "text/html; charset=utf-8"
            elif ext == ".js":
                content_type = "application/javascript; charset=utf-8"
            elif ext == ".css":
                content_type = "text/css; charset=utf-8"
            elif ext == ".png":
                content_type = "image/png"
            elif ext == ".ico":
                content_type = "image/x-icon"

            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            print(f"PlayerCommandHandler: Error in static handler: {exc}")
            self.send_error(500, "Internal Server Error")

    def handle_poll(self, parsed_path):
        try:
            query = parse_qs(parsed_path.query or "")
            supplied_session = (query.get("sessionId") or [""])[0]
            if not self._session_matches(supplied_session):
                self._send_json(
                    200,
                    {
                        "cmd": "",
                        "videoId": "",
                        "sessionMismatch": True,
                    },
                )
                return

            now = time.time()
            PlayerCommandHandler._last_poll_time = now
            with self.queue_lock:
                self._purge_expired_locked(now)
                if self.command_queue:
                    command = self.command_queue.pop(0)
                    command_id = str(command.get("commandId", "") or "")
                    if command_id:
                        self.pending_commands[command_id] = {
                            "cmd": command.get("cmd", ""),
                            "expiresAt": now + 10.0,
                        }
                    response_data = self._public_command(command)
                else:
                    response_data = {"cmd": "", "videoId": ""}

            self._send_json(200, response_data)
        except Exception as exc:
            print(f"PlayerCommandHandler: Error in poll: {exc}")
            self.send_error(500, "Internal Server Error")

    def handle_status(self):
        try:
            now = time.time()
            with self.queue_lock:
                self._purge_expired_locked(now)
                queue_size = len(self.command_queue)
                pending_acks = len(self.pending_commands)
            self._send_json(
                200,
                {
                    "status": "running",
                    "queue_size": queue_size,
                    "pending_acks": pending_acks,
                    "session_id": self.session_id,
                    "last_poll_age_s": (
                        max(0.0, now - self._last_poll_time)
                        if self._last_poll_time
                        else None
                    ),
                    "timestamp": now,
                },
            )
        except Exception as exc:
            print(f"PlayerCommandHandler: Error in status: {exc}")
            self.send_error(500, "Internal Server Error")

    def do_POST(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/command":
            self.handle_command()
        elif parsed_path.path == "/feedback":
            self.handle_feedback()
        else:
            self.send_error(404, "Not Found")

    def _read_json_body(self):
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length <= 0 or content_length > 1024 * 1024:
            raise ValueError("invalid Content-Length")
        post_data = self.rfile.read(content_length)
        return json.loads(post_data.decode("utf-8"))

    def handle_command(self):
        try:
            command_data = self._read_json_body()
            if not self._session_matches(command_data.get("sessionId", "")):
                self._send_json(403, {"status": "session_mismatch"})
                return
            command_id = self.enqueue_command(command_data)
            self._send_json(
                200,
                {
                    "status": "success",
                    "message": "Command received",
                    "commandId": command_id,
                },
            )
        except Exception as exc:
            print(f"PlayerCommandHandler: Error in command: {exc}")
            self.send_error(500, "Internal Server Error")

    def handle_feedback(self):
        try:
            feedback_data = self._read_json_body()
            supplied_session = feedback_data.get("sessionId", "")
            if not self._session_matches(supplied_session):
                self._send_json(409, {"status": "session_mismatch"})
                return

            PlayerCommandHandler._last_feedback_time = time.time()
            state = str(feedback_data.get("state", "") or "")
            if state == "command_ack":
                self.acknowledge_command(
                    feedback_data.get("commandId"),
                    feedback_data.get("commandStatus", "accepted"),
                )
            else:
                # Qt signals safely marshal the update back to the GUI thread.
                feedback_signals.feedback_received.emit(feedback_data)
                if self.state_callback:
                    self.state_callback(feedback_data)

            self._send_json(200, {"status": "success"})
        except Exception as exc:
            print(f"PlayerCommandHandler: Error in feedback: {exc}")
            self.send_error(500, "Internal Server Error")


class PlayerHttpServer:
    def __init__(self, host="localhost", port=8080):
        self.host = host
        self.port = port
        self.server = None
        self.server_thread = None
        self.is_running = False
        self.session_id = ""

    def start(self):
        if self.is_running:
            return
        try:
            if getattr(sys, "frozen", False):
                web_dir = (Path(sys.executable).parent / "web").resolve()
            else:
                services_dir = Path(__file__).resolve().parent
                web_dir = (services_dir.parent.parent / "web").resolve()

            self.session_id = uuid.uuid4().hex
            PlayerCommandHandler.web_root = str(web_dir)
            PlayerCommandHandler.reset_state(self.session_id)
            self.server = ReusableThreadingHTTPServer(
                (self.host, self.port), PlayerCommandHandler
            )
            self.is_running = True
            self.server_thread = threading.Thread(
                target=self._run_server,
                name="VJPlayerHTTP",
                daemon=True,
            )
            self.server_thread.start()
            print(
                f"PlayerHttpServer: Server started on http://{self.host}:{self.port} "
                f"session={self.session_id[:8]}"
            )
        except Exception as exc:
            print(f"PlayerHttpServer: Failed to start server: {exc}")
            self.is_running = False

    def _run_server(self):
        try:
            self.server.serve_forever(poll_interval=0.2)
        except Exception as exc:
            if self.is_running:
                print(f"PlayerHttpServer: Server error: {exc}")

    def stop(self):
        if not self.is_running:
            return
        self.is_running = False
        try:
            if self.server:
                shutdown_thread = threading.Thread(
                    target=self._shutdown_server,
                    name="VJPlayerHTTPShutdown",
                    daemon=True,
                )
                shutdown_thread.start()
                shutdown_thread.join(timeout=2.0)
                if shutdown_thread.is_alive():
                    try:
                        self.server.server_close()
                    except Exception:
                        pass
            PlayerCommandHandler.reset_state("")
            print("PlayerHttpServer: Server stopped")
        except Exception as exc:
            print(f"PlayerHttpServer: Error stopping server: {exc}")

    def _shutdown_server(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception as exc:
            print(f"PlayerHttpServer: Error in shutdown thread: {exc}")

    def set_state_callback(self, callback):
        PlayerCommandHandler.state_callback = callback

    def send_command(
        self,
        cmd,
        video_id="",
        track_info=None,
        player_id=None,
        media_info=None,
        value=None,
    ):
        try:
            command = {
                "cmd": cmd,
                "videoId": video_id,
                "timestamp": time.time(),
            }
            if track_info:
                command["trackInfo"] = track_info
            if media_info:
                command["mediaInfo"] = media_info
            normalized_player = str(player_id or "").upper()
            if normalized_player in ("A", "B"):
                command["playerId"] = normalized_player
            if value is not None:
                command["value"] = value

            command_id = PlayerCommandHandler.enqueue_command(command)
            target = f" player={normalized_player}" if normalized_player else ""
            print(
                f"PlayerHttpServer: Queued command {cmd} id={command_id[:8]} "
                f"video={video_id}{target}"
            )
            return command_id
        except Exception as exc:
            print(f"PlayerHttpServer: Error sending command: {exc}")
            return None

    def clear_queue(self):
        with PlayerCommandHandler.queue_lock:
            PlayerCommandHandler.command_queue.clear()
            PlayerCommandHandler.pending_commands.clear()

    def get_queue_size(self):
        with PlayerCommandHandler.queue_lock:
            PlayerCommandHandler._purge_expired_locked()
            return len(PlayerCommandHandler.command_queue)


player_server = None


def get_player_server():
    global player_server
    if player_server is None:
        player_server = PlayerHttpServer()
    return player_server


def start_player_server(host="localhost", port=8080):
    server = get_player_server()
    server.host = host
    server.port = port
    server.start()
    return server


def stop_player_server():
    global player_server
    if player_server:
        player_server.stop()
        player_server = None
