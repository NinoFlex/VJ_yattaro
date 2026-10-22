"""Bounded, ID-keyed metadata prefetch; does not make recognition decisions.

Workers have private ITunesMetadataResolver instances. Mutable resolver caches are
not shared across workers, and no cache lock is held across HTTP. Only the small
completed/in-flight maps below are shared. Callbacks must marshal back to Qt.
"""
from collections import OrderedDict
from dataclasses import dataclass
import queue
import re
import threading
import time
from urllib.parse import urlparse

from app.services.itunes_metadata import (
    ITunesMetadataResolver, apple_id_from_url, clean_artist, clean_metadata,
    is_generic_ui_title,
)


class IdentityConflict(ValueError):
    """Fields from one recognition disagree; never attach metadata to this ID."""


def track_identity(raw):
    """Return (namespace, ID), or None. Equal digits across namespaces != a match.

    A /song route and a /track route stay distinct. The URL, when present, must
    agree with the helper's explicit ID. No title/slug guessing or cross-provider
    aliasing is used. The caller must enforce the structural source allowlist.
    """
    if not isinstance(raw, dict):
        return None

    def identifier(value):
        value = str(value or '').strip()
        return value if re.fullmatch(r'[0-9]{6,20}', value) else ''

    shazam_id = identifier(raw.get('shazamTrackId'))
    apple_id = identifier(raw.get('appleTrackId'))
    apple_url_id = apple_id_from_url(raw.get('appleMusicUrl', ''))
    if apple_id and apple_url_id and apple_id != apple_url_id:
        raise IdentityConflict('Apple URL and explicit track ID disagree')
    apple_id = apple_url_id or apple_id
    try:
        url = urlparse(str(raw.get('url') or ''))
        match = None
        if url.scheme == 'https' and url.hostname in ('www.shazam.com', 'shazam.com'):
            match = re.fullmatch(
                r'/(?:[a-z]{2}(?:-[a-z]{2})?/)?(song|track)/([0-9]{6,20})(?:/.*)?',
                url.path, re.I,
            )
        if match:
            route_kind, route_id = match.groups()
            if shazam_id and shazam_id != route_id:
                raise IdentityConflict('Shazam route and explicit track ID disagree')
            return ('shazam-' + route_kind.casefold(), route_id)
    except ValueError as exc:
        if isinstance(exc, IdentityConflict):
            raise
    if apple_id:
        return ('apple-song', apple_id)
    if shazam_id:
        # Without a route the /song versus /track namespace is unknown. Do not
        # silently equate this ID with either route kind (or with an Apple ID).
        return ('shazam-untyped', shazam_id)
    return None


def usable_metadata(title, artist):
    """Preserve the existing safe title-only fallback after lookup finishes."""
    title, artist = clean_metadata(title), clean_artist(artist)
    return (title, artist) if title and not is_generic_ui_title(title) else None


def complete_metadata(title, artist):
    title, artist = clean_metadata(title), clean_artist(artist)
    if title and artist and not is_generic_ui_title(title):
        return (title, artist)
    return None


@dataclass(frozen=True)
class _Job:
    generation: int
    key: tuple
    raw: dict
    language: str
    country: str
    cancelled: object
    callback: object
    superseded: object


class _LookupCancellation:
    """Cooperative HTTP boundary cancellation, including live-DOM upgrades."""
    def __init__(self, cancelled, superseded):
        self.cancelled, self.superseded = cancelled, superseded

    def is_set(self):
        return self.cancelled.is_set() or self.superseded.is_set()


class MetadataPrefetcher:
    """Two daemon workers, a bounded queue, single-flight per generation/key.

    Cache keys include language/country and the typed track identity. Successful
    data lives for five minutes (128 keys max). Failures retry after five seconds.
    A recognition result with complete primary metadata may seed/upgrade the cache
    while an older HTTP call is still running; that call cannot overwrite it.
    """
    WORKERS = 2
    QUEUE_LIMIT = 8
    CACHE_LIMIT = 128
    SUCCESS_TTL = 300.0
    FAILURE_TTL = 5.0

    def __init__(self, resolver_factory=ITunesMetadataResolver, clock=time.monotonic):
        self._factory = resolver_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._cache = OrderedDict()
        self._inflight = set()
        self._lookup_cancels = {}
        self._queue = queue.Queue(maxsize=self.QUEUE_LIMIT)
        self._workers = []
        self._closed = False
        self._retry_after = 0.0

    def _cached_locked(self, key):
        value = self._cache.get(key)
        if value is None:
            return None
        expires, metadata, error = value
        if expires <= self._clock():
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return metadata, error

    def _put_locked(self, key, metadata, error=''):
        ttl = self.SUCCESS_TTL if metadata else self.FAILURE_TTL
        self._cache[key] = (self._clock() + ttl, metadata, error)
        self._cache.move_to_end(key)
        while len(self._cache) > self.CACHE_LIMIT:
            self._cache.popitem(last=False)

    def get(self, key):
        with self._lock:
            cached = self._cached_locked(key)
            return cached[0] if cached else None

    def seed(self, key, title, artist):
        metadata = complete_metadata(title, artist)
        if metadata:
            with self._lock:
                if not self._closed:
                    self._put_locked(key, metadata)
                    for (_, lookup_key), event in self._lookup_cancels.items():
                        if lookup_key == key:
                            event.set()
        return metadata

    def request(self, generation, key, raw, language, country, cancelled, callback):
        """Return cache/joined/started/retry-later/queue-full/closed.

        Never call the callback synchronously, including cache hits. The Qt owner
        checks get() before/after staging the recognition; worker completions use
        the callback only to send an event to the Qt owner thread.
        """
        with self._lock:
            if self._closed or cancelled.is_set():
                return 'closed'
            cached = self._cached_locked(key)
            if cached:
                return 'cache' if cached[0] else 'retry-later'
            ticket = (generation, key)
            if ticket in self._inflight:
                return 'joined'
            if self._clock() < self._retry_after:
                return 'retry-later'
            # Lazy workers avoid any startup work for complete live metadata.
            if not self._workers:
                for index in range(self.WORKERS):
                    worker = threading.Thread(target=self._worker, daemon=True,
                                              name=f'ShazamPrefetch-{index + 1}')
                    self._workers.append(worker)
                    worker.start()
            superseded = threading.Event()
            job = _Job(generation, key, dict(raw), language, country, cancelled, callback, superseded)
            self._inflight.add(ticket)
            self._lookup_cancels[ticket] = superseded
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                self._inflight.discard(ticket)
                self._lookup_cancels.pop(ticket, None)
                return 'queue-full'
            return 'started'

    def discard_cancelled(self):
        """Drain cancelled queued jobs on stop/restart; active HTTP is cooperative."""
        with self._lock:
            retained = []
            while True:
                try:
                    job = self._queue.get_nowait()
                except queue.Empty:
                    break
                if job.cancelled.is_set():
                    self._inflight.discard((job.generation, job.key))
                    self._lookup_cancels.pop((job.generation, job.key), None)
                else:
                    retained.append(job)
            for job in retained:
                self._queue.put_nowait(job)

    def close(self):
        with self._lock:
            self._closed = True
            for event in self._lookup_cancels.values():
                event.set()
        # No join on the UI thread. Workers use bounded/cooperative network calls
        # and daemon threads; stopped-generation callbacks are ignored by Qt.
        self.discard_cancelled()

    def _worker(self):
        resolver = None
        while True:
            with self._lock:
                if self._closed:
                    return
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            metadata, error = None, ''
            try:
                if job.cancelled.is_set():
                    continue
                with self._lock:
                    cached = self._cached_locked(job.key)
                    retry_after = self._retry_after
                if cached:
                    metadata, error = cached
                elif self._clock() < retry_after:
                    error = 'metadata provider is backing off'
                else:
                    if resolver is None:
                        resolver = self._factory()
                    values = resolver.resolve(job.raw, job.language, job.country,
                                              _LookupCancellation(job.cancelled, job.superseded))
                    metadata = usable_metadata(
                        values[0] or job.raw.get('title'),
                        clean_artist(values[1]) or job.raw.get('artist'),
                    )
                    if metadata is None:
                        error = 'usable title not available'
                if job.cancelled.is_set():
                    continue
            except Exception as exc:
                error = str(exc)
                metadata = usable_metadata(job.raw.get('title'), job.raw.get('artist'))
            finally:
                with self._lock:
                    # Share the existing resolver's 429 backoff across workers.
                    if resolver is not None:
                        self._retry_after = max(self._retry_after, getattr(resolver, '_retry_after', 0.0))
                    self._inflight.discard((job.generation, job.key))
                    self._lookup_cancels.pop((job.generation, job.key), None)
                    # New live primary metadata beats an older lookup result.
                    current = self._cached_locked(job.key)
                    if current and current[0]:
                        metadata, error = current
                    elif not self._closed and not job.cancelled.is_set():
                        self._put_locked(job.key, metadata, error)
                if not job.cancelled.is_set():
                    try:
                        job.callback(job.generation, job.key, metadata, error)
                    except RuntimeError:
                        pass  # QObject was destroyed during application shutdown.


class EarlyIdentityProbe:
    """One live helper cycle's non-voting, bounded ID-prefetch callback.

    Invoked by the lane worker, not Qt. get()/request() are thread-safe; the
    callback supplied by ShazamService only emits a queued Qt signal. A true
    return permits the helper to shorten its *metadata* grace; the normal final
    result is still the only recognition vote. No song-specific mapping exists.
    """
    MAX_IDENTITIES = 2

    def __init__(self, broker, generation, language, country, cancelled, callback):
        self.broker = broker
        self.generation = generation
        self.language = language
        self.country = country
        self.cancelled = cancelled
        self.callback = callback
        self._requested = set()

    def __call__(self, raw):
        if self.cancelled.is_set() or not isinstance(raw, dict):
            return False
        # The helper's identity notification carries no trustworthy metadata;
        # discard any text in the IPC hint before consulting the resolver.
        url = str(raw.get('url') or '')
        if raw.get('source') != 'shazam-route':
            return False
        identity_raw = {'url': url, 'shazamTrackId': raw.get('shazamTrackId'),
                        'source': 'shazam-route'}
        try:
            parsed = urlparse(url)
            port = parsed.port
            identity = track_identity(identity_raw)
        except (IdentityConflict, ValueError):
            return False
        if (parsed.scheme != 'https' or parsed.hostname not in ('www.shazam.com', 'shazam.com')
                or parsed.username or parsed.password or port not in (None, 443)
                or not identity or identity[0] != 'shazam-track'):
            return False
        key = (self.language.casefold(), self.country.upper(), *identity)
        metadata = self.broker.get(key)
        if metadata and complete_metadata(*metadata):
            return True
        if key not in self._requested and len(self._requested) < self.MAX_IDENTITIES:
            self._requested.add(key)
            state = self.broker.request(
                self.generation, key, identity_raw, self.language, self.country,
                self.cancelled, self.callback,
            )
            # One line only when new HTTP work was actually submitted, not for
            # each 200ms cache check or another lane joining the same lookup.
            if state == 'started':
                print(f"ShazamService: Early metadata prefetch gen={self.generation} identity={identity}")
        metadata = self.broker.get(key)
        return bool(metadata and complete_metadata(*metadata))
