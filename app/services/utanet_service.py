"""Bounded Uta-Net metadata lookup over HTTPS; no browser and no lyric storage.

Site access is opt-in in the GUI. Callers must have permission to use the site.
HTTP and parsing are independent of Qt so the same client can be checked from
logs or tested with fixtures. A failed/ambiguous lookup never supplies a comment.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
import re
import threading
import time
import unicodedata
from urllib.parse import urlencode, urljoin, urlsplit

import requests

ORIGIN = "https://www.uta-net.com"
TERMS_URL = ORIGIN + "/info/kiyaku.html"
MAX_BYTES = 2 * 1024 * 1024
_ROLE = re.compile(
    r"(?:\u30aa\u30fc\u30d7\u30cb\u30f3\u30b0(?:\u30c6\u30fc\u30de)?|"
    r"\u30a8\u30f3\u30c7\u30a3\u30f3\u30b0(?:\u30c6\u30fc\u30de)?|"
    r"\u4e3b\u984c\u6b4c|\u633f\u5165\u6b4c|\u30a4\u30e1\u30fc\u30b8\u30bd\u30f3\u30b0|"
    r"\u30c6\u30fc\u30de\u30bd\u30f3\u30b0|\u30ad\u30e3\u30f3\u30da\u30fc\u30f3\u30bd\u30f3\u30b0|"
    r"CM\u30bd\u30f3\u30b0|\u5fdc\u63f4\u30bd\u30f3\u30b0|\u5287\u4e2d\u6b4c)(?:\s*\([^\n()]{1,35}\))?\s*$",
    re.IGNORECASE,
)
_CREDITS = re.compile(r"\u4f5c\u8a5e\s*[:\uff1a]|\u4f5c\u66f2\s*[:\uff1a]|\u7de8\u66f2\s*[:\uff1a]|\u767a\u58f2\u65e5\s*[:\uff1a]")
_VERSION = re.compile(
    r"(?:[\s\-~]*(?:\((?:TV[\s\-]*(?:size|edit|ver\.?|version)|TV\u30b5\u30a4\u30ba|"
    r"instrumental|off[\s\-]*vocal|\u30a4\u30f3\u30b9\u30c8(?:\u30eb\u30e1\u30f3\u30bf\u30eb)?)\)|"
    r"TV[\s\-]*(?:size|edit|ver\.?|version)|TV\u30b5\u30a4\u30ba|instrumental|off[\s\-]*vocal))\s*$",
    re.IGNORECASE,
)


def clean_text(value: object) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


def comparison_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def artist_prefix_matches(observed: str, found: str) -> bool:
    """Three normalized characters, or exact equality for shorter names."""
    a, b = comparison_text(observed), comparison_text(found)
    return bool(a and b and a[:3] == b[:3])


def base_title(value: str) -> str:
    text = clean_text(unicodedata.normalize("NFKC", str(value or "")))
    while True:
        previous = text
        text = _VERSION.sub("", text.rstrip(" -~")).strip()
        text = re.sub(r"\s*\(bonus\s+track\)\s*$", "", text, flags=re.I).strip()
        # Drop an explicitly labelled release/tie-up suffix, never a normal subtitle.
        text = re.sub(r"\([^()]*?(?:\u30a2\u30cb\u30e1|\u30c6\u30ec\u30d3|\u6620\u753b)[^()]*?(?:\u4e3b\u984c\u6b4c|\u30c6\u30fc\u30de)[^()]*\)$", "", text).strip()
        if previous == text:
            break
    # Japanese title followed by its Latin-script label, e.g. Harenohini.
    parts = re.split(r"\s+[-\u2013\u2014]\s+", text, maxsplit=1)
    if len(parts) == 2 and re.search(r"[\u3040-\u30ff\u3400-\u9fff]", parts[0]) and re.fullmatch(r"[A-Za-z][A-Za-z0-9 '\-]*", parts[1]):
        text = parts[0]
    return text


def lookup_titles(title: str) -> list[str]:
    original, base = clean_text(title), base_title(title)
    return list(dict.fromkeys(t for t in (original, base) if t))[:2]


def title_matches(observed: str, found: str) -> bool:
    a, b = comparison_text(base_title(observed)), comparison_text(base_title(found))
    return bool(a and a == b)


def track_key(title: str, artist: str) -> tuple[str, str]:
    # Exact normalized identity, NOT the four-character presentation heuristic.
    return comparison_text(title), comparison_text(artist)


def safe_url(url: str, base: str = ORIGIN) -> str:
    absolute = urljoin(base, url)
    p = urlsplit(absolute)
    if p.scheme != "https" or p.hostname not in ("www.uta-net.com", "uta-net.com") or p.username or p.password or p.port not in (None, 443):
        raise ValueError("off-site or non-HTTPS URL")
    return absolute


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: Node | None = field(default=None, repr=False)
    order: int = 0

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self, blocks: bool = False) -> str:
        result = []
        for child in self.children:
            if isinstance(child, str):
                result.append(child)
            elif child.tag == "br":
                result.append("\n" if blocks else " ")
            else:
                value = child.text(blocks)
                block = child.tag in {"p", "div", "h1", "h2", "h3", "h4", "li", "td", "tr", "section", "dt", "dd"}
                result.append(("\n" + value + "\n") if blocks and block else value)
        return "".join(result)


class MetadataHTMLParser(HTMLParser):
    """Small bounded tree parser. Script/style/lyrics are discarded in memory."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    SKIP = {"script", "style", "noscript", "nav", "footer", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.count = 0
        self.skipped = []

    def handle_starttag(self, tag, attrs):
        attrs = dict((k, v or "") for k, v in attrs)
        if self.skipped:
            if tag not in self.VOID:
                self.skipped.append(tag)
            return
        ident = (attrs.get("id", "") + " " + attrs.get("class", "") + " " + attrs.get("itemprop", "")).lower()
        if tag in self.SKIP or any(x in ident.split() for x in ("kashi_area", "kashi", "lyrics", "lyricbody", "hiragana", "songlist-lyrics", "songlist-lyric", "songlist-kashi")):
            if tag not in self.VOID:
                self.skipped = [tag]
            return
        self.count += 1
        if self.count > 30000 or len(self.stack) > 120:
            raise ValueError("HTML complexity limit")
        node = Node(tag, attrs, parent=self.stack[-1], order=self.count)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skipped:
            if tag in self.skipped:
                idx = len(self.skipped) - 1 - self.skipped[::-1].index(tag)
                del self.skipped[idx:]
            return
        for idx in range(len(self.stack) - 1, 0, -1):
            if self.stack[idx].tag == tag:
                del self.stack[idx:]
                break

    def handle_data(self, data):
        if not self.skipped:
            self.stack[-1].children.append(data)


def parse_tree(html: str) -> Node:
    if len(html) > MAX_BYTES:
        raise ValueError("HTML size limit")
    parser = MetadataHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.root


def _tieup_lines(text: str) -> list[str]:
    values = []
    for raw in text.splitlines():
        value = clean_text(raw)
        value = re.sub(r"^(?:\u30bf\u30a4\u30a2\u30c3\u30d7|\u4f5c\u54c1\u540d)\s*[:\uff1a]?\s*", "", value)
        match = _ROLE.search(value)
        if match and value[:match.start()].strip() and len(value) <= 200 and not _CREDITS.search(value):
            value = value[:match.start()].rstrip() + " " + value[match.start():].strip()
            if value not in values:
                values.append(value)
    return values


@dataclass(frozen=True)
class SongCandidate:
    title: str
    artist: str
    url: str
    tieup: str = ""


@dataclass(frozen=True)
class LookupResult:
    title: str
    artist: str
    comment: str = ""
    source_url: str = ""
    matched_title: str = ""
    matched_artist: str = ""
    status: str = "not_found"
    detail: str = ""
    cached: bool = False


def parse_search_results(html: str) -> list[SongCandidate]:
    root = parse_tree(html)
    result, seen = [], set()
    for link in root.walk():
        if link.tag != "a" or not re.search(r"(?:^|/)song/\d+/?(?:[?#]|$)", link.attrs.get("href", "")):
            continue
        try:
            url = safe_url(link.attrs["href"])
        except ValueError:
            continue
        title_node = next((n for n in link.walk() if n is not link and (
            "songlist-title" in n.attrs.get("class", "").split()
            or n.attrs.get("itemprop") == "name")), None)
        title = clean_text(title_node.text() if title_node else link.text())
        if not title or url in seen:
            continue
        group = link.parent
        # A result may be a table row or a compact list/card. Never search the
        # whole page for an artist: that could match a different song's entry.
        while group is not None and group.tag not in ("tr", "li"):
            if group.tag in ("body", "html", "document", "table", "tbody", "ul"):
                group = None
                break
            links = [n for n in group.walk() if n.tag == "a" and re.search(r"/artist/\d+", n.attrs.get("href", ""))]
            songs = {n.attrs.get("href", "") for n in group.walk() if n.tag == "a" and re.search(r"/song/\d+", n.attrs.get("href", ""))}
            if links and len(songs) == 1:
                break
            group = group.parent
        if group is None:
            continue
        artists = list(dict.fromkeys(clean_text(n.text()) for n in group.walk() if n.tag == "a" and re.search(r"/artist/\d+", n.attrs.get("href", "")) and clean_text(n.text())))
        if len(artists) != 1:
            continue
        tieups = []
        for n in group.walk():
            label = (n.attrs.get("class", "") + " " + n.attrs.get("data-label", "") + " " + n.attrs.get("id", "")).lower()
            if "tieup" in label or "tie-up" in label or "\u30bf\u30a4\u30a2\u30c3\u30d7" in label:
                tieups.extend(_tieup_lines(n.text(blocks=True)))
        result.append(SongCandidate(title, artists[0], url, " / ".join(dict.fromkeys(tieups))))
        seen.add(url)
    return result


def parse_song_metadata(html: str, expected_title: str, expected_artist: str, url: str) -> SongCandidate | None:
    root = parse_tree(html)
    # If provided, canonical identifies exactly the requested song.
    for n in root.walk():
        if n.tag == "link" and "canonical" in n.attrs.get("rel", "").split():
            try:
                canonical = safe_url(n.attrs.get("href", ""))
                if urlsplit(canonical).path.rstrip("/") != urlsplit(url).path.rstrip("/"):
                    return None
            except ValueError:
                return None
    nodes = list(root.walk())
    heading = next((n for n in nodes if n.tag == "h2" and title_matches(expected_title, clean_text(n.text()))), None)
    if heading is None:
        return None
    title = clean_text(heading.text())
    # Read only the song header before credits, never rankings/related songs.
    following = [n for n in nodes if n.order > heading.order]
    artist_node = None
    for n in following[:100]:
        if n.tag in ("h1", "h2") or (n.tag in ("p", "div", "span") and _CREDITS.match(clean_text(n.text()))):
            break
        if n.tag == "h3":
            artist_node = n
            break
        if n.tag == "a" and re.search(r"/artist/\d+", n.attrs.get("href", "")):
            artist_node = n
            break
    if artist_node is None:
        return None
    artist = clean_text(artist_node.text())
    if not artist_prefix_matches(expected_artist, artist):
        return None
    # Flatten only the header window. Its endpoint is the first credit label;
    # no fallback ever searches lyrics for words such as 'ending'.
    parts = root.text(blocks=True)
    title_pos = parts.find(heading.text())
    if title_pos < 0:
        return None
    start = parts.find(artist_node.text(), title_pos + len(heading.text()))
    if start < 0:
        return None
    start += len(artist_node.text())
    remainder = parts[start:]
    end = _CREDITS.search(remainder)
    if end is None:
        return None
    header = remainder[:end.start()]
    # Limit the metadata window so a redesigned site fails closed.
    if len(header) > 1600:
        return None
    lines = _tieup_lines(header)
    if not lines:
        # Adjacent inline spans / a <br> between production and role.
        lines = _tieup_lines(clean_text(header))
    return SongCandidate(title, artist, safe_url(url), " / ".join(lines))


class LookupCancelled(Exception):
    pass


class SiteUnavailable(Exception):
    def __init__(self, message: str, retry_after: float = 30):
        super().__init__(message)
        self.retry_after = retry_after


class UtanetClient:
    """One site, TLS verification on, bounded requests, short negative caching."""
    def __init__(self, *, session=None, interval: float = 1.0, timeout: float = 18.0, clock=time.monotonic):
        self.session = session or requests.Session()
        self.interval, self.timeout, self.clock = interval, timeout, clock
        self._last_request = -1000000.0
        self._blocked_until = 0.0
        self._cache = OrderedDict()
        self._lock = threading.Lock()

    def _get(self, url, cancel, deadline):
        url = safe_url(url)
        for _ in range(4):
            delay = max(0.0, self._last_request + self.interval - self.clock())
            if cancel.wait(delay) or self.clock() >= deadline:
                raise LookupCancelled()
            remaining = deadline - self.clock()
            self._last_request = self.clock()
            response = self.session.get(url, timeout=(min(3.0, remaining), min(5.0, remaining)),
                headers={"User-Agent": "VJ-yattaro-Tieup/1.0 (metadata lookup; permission required)", "Accept": "text/html", "Accept-Language": "ja"},
                allow_redirects=False, stream=True)
            try:
                status = response.status_code
                if status in (301, 302, 303, 307, 308):
                    url = safe_url(response.headers.get("Location", ""), url)
                    continue
                if status in (401, 403, 429):
                    try:
                        retry = max(300.0, min(3600.0, float(response.headers.get("Retry-After", 300))))
                    except (ValueError, TypeError):
                        retry = 300.0
                    raise SiteUnavailable(f"HTTP {status}; no browser/identity fallback", retry)
                response.raise_for_status()
                if "text/html" not in response.headers.get("Content-Type", "").lower():
                    raise SiteUnavailable("non-HTML response")
                chunks, length = [], 0
                for chunk in response.iter_content(chunk_size=32768):
                    if cancel.is_set() or self.clock() >= deadline:
                        raise LookupCancelled()
                    length += len(chunk)
                    if length > MAX_BYTES:
                        raise SiteUnavailable("response size limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                # Search forms and current song pages use UTF-8. Respect an
                # explicit Japanese charset, not requests' Latin-1 default.
                charset = re.search(r"charset=[\"']?([\w-]+)", response.headers.get("Content-Type", ""), re.I)
                encoding = charset.group(1) if charset else "utf-8"
                return raw.decode(encoding, errors="replace"), url
            finally:
                response.close()
        raise SiteUnavailable("too many redirects")

    def lookup(self, title: str, artist: str, *, cancel: threading.Event | None = None) -> LookupResult:
        title, artist = clean_text(title), clean_text(artist)
        result = LookupResult(title, artist)
        if not title or not artist:
            return replace(result, status="missing_identity")
        cancel = cancel or threading.Event()
        key = track_key(title, artist)
        with self._lock:
            now = self.clock()
            if cancel.is_set():
                return replace(result, status="cancelled")
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                self._cache.move_to_end(key)
                return replace(cached[1], cached=True)
            if cancel.is_set():
                return replace(result, status="cancelled")
            if now < self._blocked_until:
                return replace(result, status="cooldown")
            deadline = now + self.timeout
            try:
                candidates = []
                seen = set()
                for query in lookup_titles(title):
                    params = {"Aselect": "2", "Bselect": "3", "Keyword": query, "sort": "4"}
                    html, _url = self._get(ORIGIN + "/search/?" + urlencode(params), cancel, deadline)
                    found = parse_search_results(html)
                    for c in found:
                        if c.url not in seen and title_matches(title, c.title) and artist_prefix_matches(artist, c.artist):
                            candidates.append(c)
                            seen.add(c.url)
                    if candidates:
                        break
                candidates.sort(key=lambda c: (comparison_text(c.title) != comparison_text(title), comparison_text(c.artist) != comparison_text(artist)))
                if not candidates:
                    result = replace(result, status="not_found")
                else:
                    for c in candidates[:3]:
                        if not c.tieup:
                            html, url = self._get(c.url, cancel, deadline)
                            c = parse_song_metadata(html, c.title, artist, url)
                        if c is None:
                            continue
                        if c.tieup:
                            result = replace(result, comment=c.tieup, source_url=c.url, matched_title=c.title, matched_artist=c.artist, status="found")
                            break
                        result = replace(result, source_url=c.url, matched_title=c.title, matched_artist=c.artist, status="no_tieup")
                ttl = 86400 if result.comment else 900
            except LookupCancelled:
                return replace(result, status="cancelled" if cancel.is_set() else "timeout")
            except SiteUnavailable as exc:
                self._blocked_until = self.clock() + exc.retry_after
                result, ttl = replace(result, status="unavailable", detail=str(exc)), 30
            except (requests.RequestException, ValueError, LookupError) as exc:
                # A failure is not a confirmed 'no tie-up'. Do not poison the
                # success cache, expose a bounded status, and leave the UI alone.
                result, ttl = replace(result, status="error", detail=f"{type(exc).__name__}: {str(exc)[:250]}"), 30
            if not cancel.is_set():
                self._cache[key] = (self.clock() + ttl, result)
                self._cache.move_to_end(key)
                while len(self._cache) > 512:
                    self._cache.popitem(last=False)
            return result
