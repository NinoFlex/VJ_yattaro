"""Qt-free admission control and lifecycle state for managed WebView2 lanes.

The gate is shared by every lane and retained across stop/start generations.
It limits *initialization*, not the user's configured recognition concurrency.
"""
from collections import deque
from dataclasses import dataclass
from enum import Enum
import threading
import time


class LanePhase(str, Enum):
    STOPPED = "stopped"
    QUEUED = "queued"
    INITIALIZING = "initializing"
    READY = "ready"
    RECOGNIZING = "recognizing"
    RETRY_WAIT = "retry_wait"


@dataclass
class LaneHealth:
    # Accessed under ShazamService._lane_state_lock.
    phase: LanePhase = LanePhase.STOPPED
    failures: int = 0
    attempts: int = 0
    last_error_type: str = ""
    last_failure_reason: str = ""


class LaneStartupGate:
    """FIFO, cancellable initialization admission with a small start spacing.

    A retry must rejoin the queue, so a failing lane cannot monopolize startup.
    Call release() in a finally block for every successful acquire().
    """
    def __init__(self, limit: int = 2, min_start_interval: float = 0.5):
        if limit < 1 or min_start_interval < 0:
            raise ValueError("Invalid WebView2 initialization limits")
        self.limit = int(limit)
        self.min_start_interval = float(min_start_interval)
        self._condition = threading.Condition()
        self._waiting = deque()
        self._in_flight = 0
        self._next_start_at = 0.0

    def acquire(self, cancelled: threading.Event) -> bool:
        ticket = object()
        with self._condition:
            self._waiting.append(ticket)
            try:
                while not cancelled.is_set():
                    now = time.monotonic()
                    if (self._waiting[0] is ticket
                            and self._in_flight < self.limit
                            and now >= self._next_start_at):
                        self._waiting.popleft()
                        self._in_flight += 1
                        self._next_start_at = now + self.min_start_interval
                        self._condition.notify_all()
                        return True
                    # Event.wait cannot be combined with a Condition. Poll cancellation
                    # at most every 100 ms, without holding up the GUI thread.
                    delay = max(0.001, self._next_start_at - now)
                    self._condition.wait(min(0.1, delay) if delay > 0.001 else 0.1)
                return False
            finally:
                if ticket in self._waiting:
                    self._waiting.remove(ticket)
                    self._condition.notify_all()

    def release(self) -> None:
        with self._condition:
            if self._in_flight <= 0:
                raise RuntimeError("WebView2 initialization slot released twice")
            self._in_flight -= 1
            self._condition.notify_all()

    @property
    def in_flight(self) -> int:
        with self._condition:
            return self._in_flight


def lane_failure_reason(error) -> str:
    """Allowlisted diagnosis only; never echo exception bodies or credentials."""
    message = str(error or "").casefold()
    if "did not request the supplied audio" in message:
        return "audio-request"
    if isinstance(error, TimeoutError) or "timed out" in message or "timeout" in message:
        return "timeout"
    if "protocol mismatch" in message:
        return "protocol-mismatch"
    if "helper was not found" in message:
        return "helper-missing"
    if "requires windows" in message:
        return "platform"
    if "not prepared" in message:
        return "not-prepared"
    if any(word in message for word in ("exited", "closed", "unavailable")):
        return "helper-unavailable"
    if isinstance(error, (BrokenPipeError, ConnectionError)):
        return "ipc"
    return "other"
