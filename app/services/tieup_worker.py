"""Qt-free bounded worker. UI polls results; worker never calls GUI objects."""
from collections import deque
from dataclasses import dataclass, field
import queue
import threading
import uuid

from app.services.utanet_service import LookupResult, UtanetClient


@dataclass
class TieupJob:
    title: str
    artist: str
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancel: threading.Event = field(default_factory=threading.Event)


class TieupWorker:
    def __init__(self, client=None, max_pending=8):
        self.client = client or UtanetClient()
        self.max_pending = max(1, int(max_pending))
        self._condition = threading.Condition()
        self._queue = deque()
        self._results = queue.Queue(maxsize=32)
        self._active = None
        self._closed = False
        self._thread = None

    def submit(self, job):
        with self._condition:
            if self._closed:
                return False
            while len(self._queue) >= self.max_pending:
                old = self._queue.popleft()
                old.cancel.set()
                self._put(old.token, LookupResult(old.title, old.artist, status="cancelled"))
            self._queue.append(job)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="UtanetTieup", daemon=True)
                self._thread.start()
            self._condition.notify()
            return True

    def _put(self, token, result):
        try:
            self._results.put_nowait((token, result))
        except queue.Full:
            # The GUI can drain between Full and get_nowait().
            try:
                self._results.get_nowait()
            except queue.Empty:
                pass
            try:
                self._results.put_nowait((token, result))
            except queue.Full:
                pass  # Bounded optional metadata; never block a worker.

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._queue)
                if self._closed:
                    return
                job = self._queue.popleft()
                self._active = job
            try:
                result = self.client.lookup(job.title, job.artist, cancel=job.cancel)
            except Exception as exc:
                result = LookupResult(job.title, job.artist, status="error", detail=type(exc).__name__)
            with self._condition:
                self._active = None
                if not self._closed:
                    self._put(job.token, result)

    def drain(self):
        result = []
        for _ in range(32):
            try:
                result.append(self._results.get_nowait())
            except queue.Empty:
                break
        return result

    def cancel_all(self):
        with self._condition:
            if self._active is not None:
                self._active.cancel.set()
            for job in self._queue:
                job.cancel.set()
            self._queue.clear()
        self.drain()

    def shutdown(self):
        self.cancel_all()
        with self._condition:
            self._closed = True
            self._condition.notify_all()
