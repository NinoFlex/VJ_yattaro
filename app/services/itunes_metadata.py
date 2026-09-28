"""Safe Japanese localization for official Shazam WebView2 results.

Recognition identity always comes from the live Shazam result. Apple/iTunes is used
for Japanese metadata only when the Apple song is tied to the same Shazam result.
Current Shazam ``/song/<id>`` routes use the Apple song ID, so an exact iTunes lookup
of that same numeric ID is authoritative even when the route slug is an English
translation (for example ``Star`` -> ``星``). Legacy ``/track/<id>`` routes use a
Shazam-specific ID and therefore still require independent title/search proof.
"""
from __future__ import annotations

import html as html_lib
from concurrent.futures import ThreadPoolExecutor
import re
import time
import unicodedata
from urllib.parse import parse_qs, unquote, urlparse

_REJECT = (
    "page not found", "page was not found", "requested page", "music discovery",
    "要求されたページは見つかりませんでした",
    "ユーザーが今shazamで見つけている曲", "今shazamで見つけている曲",
    "shazamで見つけている曲", "音楽発見", "charts & lyrics", "song lyrics",
)

_LIVE_SOURCES = {
    "track-heading", "dialog-heading", "jsonld", "meta", "og:title",
    "apple-track-id", "dialog-apple-track-id", "recognition-response",
    "result-region", "new-result-heading", "route-slug-artist", "recognition-network",
}

_VISIBLE_HEADING_SOURCES = {"track-heading", "dialog-heading", "result-region", "new-result-heading"}

# Shazam UI section/tab labels that can sit next to a correct artist name and must
# never be interpreted as the recognized song title. Keep these as exact matches
# so ordinary song titles containing these words are not rejected.
_UI_TITLE_LABELS = {
    "概要", "overview", "歌詞", "lyrics", "ビデオ", "video", "videos",
    "ミュージックビデオ", "music video", "関連", "related", "クレジット", "credits",
    "トップソング", "top songs", "アルバム", "albums", "おすすめ", "featured",
    "フッター", "footer", "shazam フッター", "shazam footer",
    "ヘッダー", "header", "shazam ヘッダー", "shazam header",
    "ナビゲーション", "navigation", "shazam ナビゲーション", "shazam navigation",
    "世界トップ200チャート", "global top 200 chart", "global top 200",
}


def is_generic_ui_title(value: str) -> bool:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if text in {x.casefold() for x in _UI_TITLE_LABELS}:
        return True
    if re.fullmatch(r"(?:世界|global)\s*トップ?\s*\d{1,4}\s*(?:チャート|chart)", text, re.I):
        return True
    if re.fullmatch(r"global\s*top\s*\d{1,4}(?:\s*chart)?", text, re.I):
        return True
    return bool(re.fullmatch(r"shazam\s*(?:フッター|footer|ヘッダー|header|ナビゲーション|navigation)", text, re.I))


def clean_metadata(value) -> str:
    text = html_lib.unescape(str(value or "")).strip()
    text = " ".join(text.split())
    if len(text) > 500:
        return ""
    folded = text.casefold()
    if any(x.casefold() in folded for x in _REJECT):
        return ""
    return text


# These pre-fix sources guessed the artist from nearby arbitrary page text. Do
# not trust them even when an older, not-yet-rebuilt helper is still installed.
_PROXIMITY_ARTIST_SOURCES = {"track-heading-nearby", "route-slug-dom"}
_ARTIST_PROMO = re.compile(
    r"apple\s*music\s*(?:\u306b|\u3067|\u3092|\u3068)"
    r"|\u66f2\u5168\u4f53\u3092\u30d5\u30eb\u518d\u751f"
    r"|\u958b\u767a\u8005\u5411\u3051\s*shazamkit|shazamkit\s+for\s+developers"
    r"|(?:connect(?:\s+to)?|listen(?:\s+now)?\s+(?:on|in)|open\s+in|play\s+(?:on|in)|try|subscribe\s+to)\s+apple\s*music\b"
    r"|connect\s+(?:with|to)\s+shazam|full\s+songs?\s+(?:in|on)\s+shazam", re.I,
)
_ARTIST_UI = re.compile(
    r"(?:apple\s*music|connect\s+apple\s*music|get\s+the\s+app|download\s+shazam|open\s+in\s+shazam"
    r"|\u306b\u63a5\u7d9a|\u30a2\u30d7\u30ea\u3092\u5165\u624b|\u30a2\u30d7\u30ea\u3092\u30c0\u30a6\u30f3\u30ed\u30fc\u30c9"
    r"|shazam\s+(?:footer|header|navigation)|\u6982\u8981|\u6b4c\u8a5e|\u5171\u6709|\u518d\u751f)", re.I,
)


def clean_artist(value, source: str = "") -> str:
    """Reject Shazam UI/CTA text without rejecting bands such as Live or 311.

    Source is supplied only for *raw* bridge values. Once independently resolved,
    the recovered artist is checked without the original source's provenance.
    """
    text = clean_metadata(value)
    if str(source or "").split("+", 1)[0].casefold() in _PROXIMITY_ARTIST_SOURCES:
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = re.sub(r"[\u200b-\u200d\ufeff]", "", folded)
    if _ARTIST_PROMO.search(folded) or _ARTIST_UI.fullmatch(folded):
        return ""
    return text


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(ch for ch in value if ch.isalnum())


def contains_japanese(value: str) -> bool:
    for ch in str(value or ""):
        code = ord(ch)
        if 0x3040 <= code <= 0x30FF or 0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
            return True
    return False


def apple_id_from_url(value: str) -> str:
    """Return a real Apple song ID only; album IDs and Shazam IDs are rejected."""
    try:
        url = urlparse(str(value or ""))
        if url.scheme not in ("https", "http") or url.hostname not in ("music.apple.com", "itunes.apple.com"):
            return ""
        track_id = parse_qs(url.query).get("i", [""])[0]
        if re.fullmatch(r"[0-9]{6,20}", track_id):
            return track_id
        match = re.search(r"/song/[^/?#]+/([0-9]{6,20})(?:[/?#]|$)", url.path, re.I)
        return match.group(1) if match else ""
    except (ValueError, TypeError):
        return ""


def _safe_shazam_song_url(value: str) -> str:
    try:
        url = urlparse(str(value or ""))
        if url.scheme != "https" or url.hostname not in ("www.shazam.com", "shazam.com"):
            return ""
        if not re.search(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:song|track)/[0-9]{6,20}(?:/|$)", url.path, re.I):
            return ""
        return url.geturl()
    except (ValueError, TypeError):
        return ""


def _shazam_route_identity(value: str) -> tuple[str, str]:
    safe = _safe_shazam_song_url(value)
    if not safe:
        return "", ""
    try:
        parts = [p for p in urlparse(safe).path.split("/") if p]
        for i, part in enumerate(parts[:-1]):
            if part.casefold() not in ("song", "track"):
                continue
            track_id = parts[i + 1]
            if not track_id.isdigit():
                continue
            slug = unquote(parts[i + 2]).strip() if i + 2 < len(parts) else ""
            return track_id, slug
    except (ValueError, TypeError):
        pass
    return "", ""


def _shazam_route_kind(value: str) -> str:
    """Return ``song`` for Apple-backed routes and ``track`` for legacy Shazam IDs."""
    safe = _safe_shazam_song_url(value)
    if not safe:
        return ""
    try:
        parts = [p for p in urlparse(safe).path.split("/") if p]
        for i, part in enumerate(parts[:-1]):
            kind = part.casefold()
            if kind not in ("song", "track"):
                continue
            if i + 1 < len(parts) and parts[i + 1].isdigit():
                return kind
    except (ValueError, TypeError):
        pass
    return ""


def _title_from_shazam_route(value: str) -> str:
    _, slug = _shazam_route_identity(value)
    if not slug or slug.isdigit():
        return ""
    # Keep the route fallback readable. It is only a last resort; Japanese live
    # metadata and safe Apple metadata always take priority.
    return clean_metadata(slug.replace("-", " ").title())


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return {x for x in re.split(r"[^\w]+", normalized, flags=re.UNICODE) if x}


def _route_metadata_compatible(route_title: str, metadata_title: str, identity_verified: bool) -> bool:
    route_title, metadata_title = clean_metadata(route_title), clean_metadata(metadata_title)
    if not metadata_title:
        return False
    if not route_title:
        return identity_verified
    if normalize(route_title) == normalize(metadata_title):
        return True
    a, b = _tokens(route_title), _tokens(metadata_title)
    return bool(a and b and a == b)


def _read_html_attribute(tag: str, name: str) -> str:
    match = re.search(
        r"(?:^|\s)" + re.escape(name) + r"\s*=\s*(?:\"([^\"<>]*)\"|'([^'<>]*)')",
        tag,
        flags=re.I | re.S,
    )
    if not match:
        return ""
    return html_lib.unescape(match.group(1) if match.group(1) is not None else match.group(2)).strip()


def _find_meta_content(page: str, wanted: str) -> str:
    for tag in re.findall(r"<meta\b[^>]*>", page, flags=re.I):
        key = _read_html_attribute(tag, "property") or _read_html_attribute(tag, "name")
        if key.casefold() != wanted.casefold():
            continue
        content = _read_html_attribute(tag, "content")
        if content:
            return clean_metadata(content)
    return ""


def _find_canonical(page: str) -> str:
    for tag in re.findall(r"<link\b[^>]*>", page, flags=re.I):
        rel = _read_html_attribute(tag, "rel")
        if "canonical" not in {x.casefold() for x in rel.split()}:
            continue
        href = _read_html_attribute(tag, "href")
        if href:
            return href
    return ""


def _parse_og_title(value: str) -> str:
    compact = clean_metadata(value)
    if "|" in compact:
        compact = compact.split("|", 1)[0].strip()
    compact = re.sub(r"\s*[：:]\s*(?:歌詞|lyrics|music video|ミュージック).*$", "", compact, flags=re.I).strip()
    compact = re.sub(r"\s*[-–—|]\s*Shazam\s*$", "", compact, flags=re.I).strip()
    if " - " in compact:
        compact = compact.rsplit(" - ", 1)[0].strip()
    by = re.match(r"^(.*?)\s+by\s+(.+)$", compact, flags=re.I)
    if by:
        compact = by.group(1).strip()
    return clean_metadata(compact)


def _parse_og_artist(value: str) -> str:
    """Extract the performer from Shazam's localized og:title when present.

    Current Shazam pages commonly expose headings such as
    ``Title - Artist | Shazam`` (or a localized lyrics suffix).  This is only
    consumed after the page's Shazam route ID is independently verified, so it
    never establishes recognition identity by itself.
    """
    compact = clean_metadata(value)
    if "|" in compact:
        compact = compact.split("|", 1)[0].strip()
    compact = re.sub(r"\s*[：:]\s*(?:歌詞|lyrics|music video|ミュージック).*$", "", compact, flags=re.I).strip()
    compact = re.sub(r"\s*[-–—|]\s*Shazam\s*$", "", compact, flags=re.I).strip()
    by = re.match(r"^(.*?)\s+by\s+(.+)$", compact, flags=re.I)
    if by:
        return clean_artist(by.group(2))
    if " - " in compact:
        return clean_artist(compact.rsplit(" - ", 1)[1])
    return ""


def _same_shazam_route(expected: str, candidate: str) -> bool:
    expected_id, _ = _shazam_route_identity(expected)
    candidate_id, _ = _shazam_route_identity(candidate)
    return bool(expected_id and candidate_id and expected_id == candidate_id)


class ITunesMetadataResolver:
    def __init__(self):
        self._result_cache: dict[tuple, tuple[float, tuple[str, str]]] = {}
        self._id_cache: dict[tuple[str, str], tuple[float, dict | None]] = {}
        self._search_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        self._page_cache: dict[str, tuple[float, tuple[str, str, str, bool]]] = {}
        self._route_search_proof_cache: dict[tuple[str, str], tuple[float, bool]] = {}
        self._retry_after = 0.0

    @staticmethod
    def _cancelled(cancelled) -> bool:
        return bool(cancelled and cancelled.is_set())

    @staticmethod
    def _source_base(source: str) -> str:
        return str(source or "").split("+", 1)[0].casefold()

    def _lookup_apple_id(self, track_id: str, lang: str, cancelled=None) -> dict | None:
        if self._cancelled(cancelled):
            return None
        key = (track_id, lang)
        cached = self._id_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        if time.monotonic() < self._retry_after:
            return None
        try:
            import requests
            response = requests.get(
                "https://itunes.apple.com/lookup",
                params={"id": track_id, "entity": "song", "country": "JP", "lang": lang},
                headers={"User-Agent": "VJ-yattaro-ShazamWebView/1.2", "Accept": "application/json"},
                timeout=(4, 8),
            )
            if response.status_code == 429:
                self._retry_after = time.monotonic() + 120
                print("ShazamMetadata: Apple lookup rate limit; keeping Shazam metadata")
                return None
            response.raise_for_status()
            for item in response.json().get("results", []):
                if item.get("kind") != "song" or str(item.get("trackId")) != track_id:
                    continue
                parsed = {
                    "trackId": track_id,
                    "trackName": clean_metadata(item.get("trackName")),
                    "artistName": clean_metadata(item.get("artistName")),
                    "collectionName": clean_metadata(item.get("collectionName")),
                }
                if parsed["trackName"]:
                    self._id_cache[key] = (time.monotonic() + 86400, parsed)
                    return parsed
            self._id_cache[key] = (time.monotonic() + 120, None)
        except Exception as exc:
            self._id_cache[key] = (time.monotonic() + 45, None)
            print(f"ShazamMetadata: Apple ID lookup unavailable ({type(exc).__name__})")
        return None

    @staticmethod
    def _apple_candidate_matches(candidate: dict, expected_title: str, expected_artist: str, jp: dict | None) -> bool:
        if not expected_title and not expected_artist:
            return False
        if expected_title:
            wanted = normalize(expected_title)
            if wanted not in {normalize(candidate.get("trackName", "")), normalize((jp or {}).get("trackName", ""))}:
                return False
        if expected_artist:
            wanted = normalize(expected_artist)
            if wanted not in {normalize(candidate.get("artistName", "")), normalize((jp or {}).get("artistName", ""))}:
                return False
        return True

    def _lookup_apple_pair(self, track_id: str, cancelled=None) -> tuple[dict | None, dict | None]:
        """Fetch EN identity and JP localization in parallel to minimize post-match latency."""
        if self._cancelled(cancelled):
            return None, None
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="AppleLookup") as pool:
            en_future = pool.submit(self._lookup_apple_id, track_id, "en_us", cancelled)
            jp_future = pool.submit(self._lookup_apple_id, track_id, "ja_jp", cancelled)
            return en_future.result(), jp_future.result()

    def _resolve_exact_apple(self, track_id: str, expected_title: str, expected_artist: str,
                             allow_without_text: bool, cancelled=None) -> tuple[str, str] | None:
        en, jp = self._lookup_apple_pair(track_id, cancelled)
        if en is None and jp is None:
            print(f"ShazamMetadata: Apple exact-ID lookup trackId={track_id} -> not found")
            return None
        check = en or jp
        verified = allow_without_text or self._apple_candidate_matches(check, expected_title, expected_artist, jp)
        print(
            "ShazamMetadata: Apple exact-ID verification "
            f"trackId={track_id} expectedTitle={expected_title!r} expectedArtist={expected_artist!r} "
            f"appleTitle={(check or {}).get('trackName','')!r} appleArtist={(check or {}).get('artistName','')!r} verified={verified}"
        )
        if not verified:
            return None
        localized = jp or en
        if not localized or not localized.get("trackName"):
            return None
        print(f"ShazamMetadata: Apple Japanese metadata accepted by exact ID trackId={track_id} title={localized['trackName']!r}")
        return localized["trackName"], localized.get("artistName", "")

    def _route_title_search_confirms_id(self, route_id: str, route_title: str, cancelled=None) -> bool:
        """Use Apple Search as an independent proof for route-only ID candidates.

        The Shazam route slug can be romanized while Apple JP stores the title in
        Japanese, so direct text comparison can fail even for the same song.  We
        therefore search Apple with the exact Shazam route title and accept the
        numeric ID only when that search independently returns the same track ID.
        This is deliberately stricter than blindly equating Shazam IDs with Apple IDs.
        """
        route_id = str(route_id or "").strip()
        route_title = clean_metadata(route_title)
        if (not re.fullmatch(r"[0-9]{6,20}", route_id) or not route_title
                or self._cancelled(cancelled) or time.monotonic() < self._retry_after):
            return False
        key = (route_id, normalize(route_title))
        cached = self._route_search_proof_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            import requests
            search_terms = [route_title]
            shorter = re.sub(
                r"\s+(?:original\s+karaoke|karaoke|off\s+vocal|instrumental|game\s+version|version)\s*$",
                "", route_title, flags=re.I,
            ).strip()
            if shorter and normalize(shorter) != normalize(route_title):
                search_terms.append(shorter)

            for search_term in search_terms:
                response = requests.get(
                    "https://itunes.apple.com/search",
                    params={
                        "term": search_term, "country": "JP", "media": "music",
                        "entity": "song", "limit": 25, "lang": "ja_jp",
                    },
                    headers={"User-Agent": "VJ-yattaro-ShazamWebView/1.2.7", "Accept": "application/json"},
                    timeout=(4, 8),
                )
                if response.status_code == 429:
                    self._retry_after = time.monotonic() + 120
                    print("ShazamMetadata: Apple route-title proof rate limit")
                    return False
                response.raise_for_status()
                for rank, item in enumerate(response.json().get("results", [])):
                    if rank >= 10:
                        break
                    if item.get("kind") != "song" or str(item.get("trackId") or "") != route_id:
                        continue
                    parsed = {
                        "trackId": route_id,
                        "trackName": clean_metadata(item.get("trackName")),
                        "artistName": clean_metadata(item.get("artistName")),
                        "collectionName": clean_metadata(item.get("collectionName")),
                    }
                    if parsed["trackName"]:
                        self._id_cache[(route_id, "ja_jp")] = (time.monotonic() + 3600, parsed)
                    self._route_search_proof_cache[key] = (time.monotonic() + 3600, True)
                    print(
                        "ShazamMetadata: Apple route-title search proved route ID "
                        f"shazamTrackId={route_id} routeTitle={route_title!r} searchTerm={search_term!r} "
                        f"rank={rank + 1} appleTitle={parsed['trackName']!r} artist={parsed['artistName']!r}"
                    )
                    return True
            self._route_search_proof_cache[key] = (time.monotonic() + 45, False)
            print(
                "ShazamMetadata: Apple route-title search did not prove route ID "
                f"shazamTrackId={route_id} routeTitle={route_title!r}"
            )
        except Exception as exc:
            self._route_search_proof_cache[key] = (time.monotonic() + 45, False)
            print(f"ShazamMetadata: Apple route-title proof unavailable ({type(exc).__name__})")
        return False

    def _resolve_route_id_candidate(self, route_id: str, route_title: str, route_kind: str = "", cancelled=None) -> tuple[str, str] | None:
        """Recover Apple metadata when Shazam exposed only a route.

        Shazam's current ``/song/<id>`` route is Apple-catalog backed: the numeric
        route ID is the Apple song ID.  That exact-ID lookup is therefore stronger
        than comparing the localized Apple title with the English route slug.
        Legacy ``/track/<id>`` routes are a different Shazam namespace and keep the
        old independent text/search verification.
        """
        route_id = str(route_id or "").strip()
        route_title = clean_metadata(route_title)
        route_kind = str(route_kind or "").strip().casefold()
        if (not re.fullmatch(r"[0-9]{6,20}", route_id) or not route_title
                or self._cancelled(cancelled)):
            return None
        en, jp = self._lookup_apple_pair(route_id, cancelled)
        if en is None and jp is None:
            print(f"ShazamMetadata: route-ID Apple candidate trackId={route_id} -> not found")
            return None

        localized = jp or en
        # Current /song routes are keyed by the Apple song ID itself.  Require a
        # real song record with both title and artist before accepting it; this is
        # intentionally NOT applied to legacy /track routes.
        if route_kind == "song" and localized and localized.get("trackName") and localized.get("artistName"):
            print(
                "ShazamMetadata: Apple Japanese metadata accepted by exact Shazam /song ID "
                f"trackId={route_id} routeTitle={route_title!r} "
                f"appleTitle={localized['trackName']!r} artist={localized.get('artistName','')!r}"
            )
            return localized["trackName"], localized.get("artistName", "")

        proof_titles = [clean_metadata((item or {}).get("trackName")) for item in (en, jp)]
        proof_titles = [title for title in proof_titles if title]
        text_verified = any(_route_metadata_compatible(route_title, title, False) for title in proof_titles)
        search_verified = False
        if not text_verified and not self._cancelled(cancelled):
            search_verified = self._route_title_search_confirms_id(route_id, route_title, cancelled)
        verified = text_verified or search_verified
        print(
            "ShazamMetadata: route-ID Apple candidate verification "
            f"shazamTrackId={route_id} routeKind={route_kind!r} routeTitle={route_title!r} "
            f"appleEnTitle={(en or {}).get('trackName','')!r} "
            f"appleJpTitle={(jp or {}).get('trackName','')!r} "
            f"textVerified={text_verified} searchVerified={search_verified} verified={verified}"
        )
        if not verified:
            return None
        localized = jp or en
        if not localized or not localized.get("trackName"):
            return None
        print(
            "ShazamMetadata: Apple Japanese metadata accepted after route-ID proof "
            f"trackId={route_id} title={localized['trackName']!r} "
            f"artist={localized.get('artistName','')!r}"
        )
        return localized["trackName"], localized.get("artistName", "")

    def _search_exact_matches(self, title: str, artist: str, cancelled=None) -> list[dict]:
        key = (normalize(title), normalize(artist))
        cached = self._search_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return list(cached[1])
        if not all(key) or self._cancelled(cancelled) or time.monotonic() < self._retry_after:
            return []

        def fetch_language(lang: str):
            import requests
            if self._cancelled(cancelled):
                return lang, 0, []
            response = requests.get(
                "https://itunes.apple.com/search",
                params={
                    "term": f"{title} {artist}", "country": "JP", "media": "music",
                    "entity": "song", "limit": 25, "lang": lang,
                },
                headers={"User-Agent": "VJ-yattaro-ShazamWebView/1.2.4", "Accept": "application/json"},
                timeout=(4, 8),
            )
            if response.status_code == 429:
                return lang, 429, []
            response.raise_for_status()
            return lang, response.status_code, response.json().get("results", [])

        output: dict[str, dict] = {}
        try:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="AppleSearch") as pool:
                futures = [pool.submit(fetch_language, lang) for lang in ("en_us", "ja_jp")]
                responses = [future.result() for future in futures]
            if any(status == 429 for _, status, _ in responses):
                self._retry_after = time.monotonic() + 120
                print("ShazamMetadata: Apple search rate limit; keeping Shazam metadata")
                return []
            for lang, _, items in responses:
                for item in items:
                    if item.get("kind") != "song":
                        continue
                    track_id = str(item.get("trackId") or "")
                    name = clean_metadata(item.get("trackName"))
                    performer = clean_metadata(item.get("artistName"))
                    if not re.fullmatch(r"[0-9]{6,20}", track_id):
                        continue
                    parsed = {
                        "trackId": track_id, "trackName": name, "artistName": performer,
                        "collectionName": clean_metadata(item.get("collectionName")),
                    }
                    # Search results are the same Apple catalog records used by lookup;
                    # cache them by language so strict localization does not make a third request.
                    if name:
                        self._id_cache[(track_id, lang)] = (time.monotonic() + 3600, parsed)
                    if normalize(name) != key[0] or normalize(performer) != key[1]:
                        continue
                    output.setdefault(track_id, parsed)
        except Exception as exc:
            print(f"ShazamMetadata: Apple strict search unavailable ({type(exc).__name__})")
            return []
        matches = list(output.values())
        self._search_cache[key] = (time.monotonic() + (3600 if matches else 45), matches)
        return matches

    def _resolve_strict_search(self, title: str, artist: str, cancelled=None) -> tuple[str, str] | None:
        matches = self._search_exact_matches(title, artist, cancelled)
        if not matches:
            print(f"ShazamMetadata: Apple strict search title={title!r} artist={artist!r} -> no exact title+artist match")
            return None
        localized = []
        variants = set()
        for candidate in matches:
            jp = self._lookup_apple_id(candidate["trackId"], "ja_jp", cancelled)
            name = clean_metadata((jp or {}).get("trackName") or candidate["trackName"])
            performer = clean_metadata((jp or {}).get("artistName") or candidate["artistName"])
            if not name:
                continue
            variants.add(normalize(name))
            localized.append((candidate, name, performer))
        if not localized:
            return None
        if len(variants) != 1:
            print(
                f"ShazamMetadata: Apple strict search rejected as ambiguous title={title!r} artist={artist!r} "
                f"exactMatches={len(matches)} localizedVariants={len(variants)}"
            )
            return None
        candidate, name, performer = localized[0]
        print(
            f"ShazamMetadata: Apple strict search accepted shazamTitle={title!r} artist={artist!r} "
            f"appleTrackId={candidate['trackId']} enTitle={candidate['trackName']!r} "
            f"enArtist={candidate['artistName']!r} jpTitle={name!r} exactMatches={len(matches)}"
        )
        return name, performer

    def _shazam_page_metadata(self, route_url: str, cancelled=None) -> tuple[str, str, str, bool]:
        url = _safe_shazam_song_url(route_url)
        if not url or self._cancelled(cancelled):
            return "", "", "", False
        cached = self._page_cache.get(url)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        resolved = ("", "", "", False)
        try:
            import requests
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VJ-yattaro/1.2",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5",
                },
                timeout=(4, 8),
            )
            response.raise_for_status()
            page = response.text
            if len(page) <= 4_000_000:
                og_title = _find_meta_content(page, "og:title")
                title = _parse_og_title(og_title)
                artist = _parse_og_artist(og_title)
                page_url = _find_meta_content(page, "og:url") or _find_canonical(page)
                resolved = (title, artist, page_url, _same_shazam_route(url, page_url))
        except Exception as exc:
            print(f"ShazamMetadata: public Shazam page unavailable ({type(exc).__name__})")
        self._page_cache[url] = (time.monotonic() + (3600 if resolved[0] else 45), resolved)
        return resolved

    def _cache(self, key: tuple, value: tuple[str, str], seconds: float = 86400) -> tuple[str, str]:
        if len(self._result_cache) >= 512:
            self._result_cache.pop(next(iter(self._result_cache)))
        value = (value[0], clean_artist(value[1]))
        self._result_cache[key] = (time.monotonic() + seconds, value)
        return value

    def resolve(self, result: dict, language: str, country: str, cancelled=None) -> tuple[str, str]:
        observed_title = clean_metadata(result.get("title"))
        if is_generic_ui_title(observed_title):
            print(f"ShazamMetadata: ignored Shazam UI label misread as title: {observed_title!r}")
            observed_title = ""
        observed_artist = clean_artist(result.get("artist"), result.get("source", ""))
        if result.get("artist") and not observed_artist:
            print(f"ShazamMetadata: ignored untrusted/UI artist: {result.get('artist')!r}")
        route_url = _safe_shazam_song_url(str(result.get("url") or ""))
        route_id = str(result.get("shazamTrackId") or "").strip()
        parsed_route_id, _ = _shazam_route_identity(route_url)
        if not re.fullmatch(r"[0-9]{6,20}", route_id):
            route_id = parsed_route_id
        source = self._source_base(str(result.get("source") or ""))
        apple_url = str(result.get("appleMusicUrl") or "").strip()
        route_title = _title_from_shazam_route(route_url)
        route_kind = _shazam_route_kind(route_url)

        live_title = observed_title if source in _LIVE_SOURCES else ""
        if observed_title and source in _VISIBLE_HEADING_SOURCES:
            trusted_title = observed_title
        elif observed_title and route_title and _route_metadata_compatible(route_title, observed_title, False):
            trusted_title = observed_title
        elif observed_title and not route_url and (source in _LIVE_SOURCES or not source):
            # A direct recognition response with no Shazam route is still exact request-scoped
            # evidence. Preserve it if Apple cannot safely localize it.
            trusted_title = observed_title
        else:
            trusted_title = ""

        title_for_match = live_title or trusted_title or route_title or observed_title
        cache_key = (
            "route", route_id, route_url, live_title, trusted_title, observed_artist,
            apple_url, source,
        )
        cached = self._result_cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        print(
            f"ShazamMetadata: route identity shazamTrackId={route_id} slugTitle={route_title!r} "
            f"observedTitle={observed_title!r} trustedTitle={trusted_title!r} "
            f"artist={observed_artist!r} appleMusic={apple_url!r} source={source!r}"
        )

        # Exact Apple link is strongest. A bridge-provided appleTrackId is accepted only
        # after v1.2 because the bridge now derives it exclusively from an Apple URL.
        apple_track_id = apple_id_from_url(apple_url)
        if not apple_track_id:
            candidate_id = str(result.get("appleTrackId") or "").strip()
            if re.fullmatch(r"[0-9]{6,20}", candidate_id):
                apple_track_id = candidate_id
        if apple_track_id and not self._cancelled(cancelled):
            exact = self._resolve_exact_apple(
                apple_track_id, title_for_match, observed_artist,
                allow_without_text=bool(apple_url), cancelled=cancelled,
            )
            if exact:
                if (live_title and contains_japanese(live_title) and not is_generic_ui_title(live_title)
                        and not contains_japanese(exact[0])):
                    print(
                        "ShazamMetadata: exact Apple ID verified same song but JP title is non-Japanese; "
                        f"preserving trusted live Shazam Japanese title {live_title!r}"
                    )
                    return self._cache(cache_key, (live_title, observed_artist or exact[1]), 3600)
                # A real Apple Music track link + verified Apple lookup is authoritative.
                # Never keep a Shazam UI tab/section label such as '概要' over this title.
                return self._cache(cache_key, (exact[0], exact[1] or observed_artist))

        # Route-only fallback: some Shazam /song IDs happen to be the real Apple song
        # ID. Probe it only when the live page did not give enough title/artist text,
        # and accept it only after Apple EN proves an exact match to the route slug.
        # This recovers Japanese titles without ever equating all Shazam IDs to Apple IDs.
        if (not apple_track_id and route_id and route_title
                and (not live_title or not observed_artist)
                and not self._cancelled(cancelled)):
            route_exact = self._resolve_route_id_candidate(route_id, route_title, route_kind, cancelled)
            if route_exact:
                return self._cache(cache_key, route_exact, 3600)

        # The live WebView can expose the exact /song route before its title/artist
        # DOM has painted. As a second route-only recovery path, fetch that exact
        # localized Shazam page and accept its title+artist only when og:url/canonical
        # independently repeats the same Shazam route ID. This rejects redirects or
        # stale unrelated pages while recovering cases such as a romanized route slug
        # ("Hikari") whose ja-JP page title is Japanese ("光").
        route_page = None
        if (route_url and not observed_artist
                and not self._cancelled(cancelled)):
            route_page = self._shazam_page_metadata(route_url, cancelled)
            page_title, page_artist, page_url, identity_verified = route_page
            print(
                f"ShazamMetadata: route-only public page title={page_title or '(not found)'!r} "
                f"artist={page_artist!r} identityVerified={identity_verified} pageUrl={page_url!r}"
            )
            if identity_verified and page_title and page_artist:
                print(
                    "ShazamMetadata: route-only public page accepted after exact route-ID proof "
                    f"title={page_title!r} artist={page_artist!r}"
                )
                return self._cache(cache_key, (page_title, page_artist), 3600)

        # Without an exact link, search Apple using the exact live Shazam title+artist first.
        attempted = False
        if live_title and observed_artist and not self._cancelled(cancelled):
            attempted = True
            print(f"ShazamMetadata: Apple strict search using live Shazam evidence first title={live_title!r} artist={observed_artist!r}")
            strict = self._resolve_strict_search(live_title, observed_artist, cancelled)
            if strict:
                if contains_japanese(live_title) and not contains_japanese(strict[0]):
                    return self._cache(cache_key, (live_title, observed_artist or strict[1]), 3600)
                return self._cache(cache_key, (strict[0], strict[1] or observed_artist))

        if route_title and observed_artist and normalize(route_title) != normalize(live_title) and not self._cancelled(cancelled):
            attempted = True
            print(f"ShazamMetadata: Apple strict search fallback using route title={route_title!r} artist={observed_artist!r}")
            strict = self._resolve_strict_search(route_title, observed_artist, cancelled)
            if strict:
                if live_title and contains_japanese(live_title) and not contains_japanese(strict[0]):
                    return self._cache(cache_key, (live_title, observed_artist or strict[1]), 3600)
                return self._cache(cache_key, (strict[0], strict[1] or observed_artist))

        if not attempted:
            print(
                f"ShazamMetadata: Apple strict search skipped liveTitle={live_title!r} "
                f"routeTitle={route_title!r} artist={observed_artist!r}; title+artist are both required"
            )

        # Static Shazam HTML is supplemental only and must prove the same route identity
        # AND be textually compatible with the recognized route slug.
        if (route_url and (observed_artist or live_title or trusted_title)
                and not self._cancelled(cancelled)):
            page_title, page_artist, page_url, identity_verified = (
                route_page or self._shazam_page_metadata(route_url, cancelled)
            )
            print(
                f"ShazamMetadata: public Shazam page title={page_title or '(not found)'!r} "
                f"identityVerified={identity_verified} pageUrl={page_url!r}"
            )
            if page_title and _route_metadata_compatible(route_title, page_title, identity_verified):
                performer = observed_artist or (clean_artist(page_artist) if identity_verified else "")
                return self._cache(cache_key, (page_title, performer), 3600)
            if page_title:
                print(f"ShazamMetadata: rejected unrelated Shazam metadata routeTitle={route_title!r} metadataTitle={page_title!r}")

        # Critical v0.5.5 behavior: if the exact live Japanese Shazam result already
        # contains Japanese, never replace it with the romanized route slug merely because
        # Apple could not be safely linked on this attempt.
        if live_title and contains_japanese(live_title) and observed_artist:
            print(
                "ShazamMetadata: Apple exact linkage unresolved; using Japanese title from exact live Shazam result "
                f"title={live_title!r} artist={observed_artist!r}"
            )
            return self._cache(cache_key, (live_title, observed_artist), 45)
        if not route_url and observed_title:
            return self._cache(cache_key, (observed_title, observed_artist), 45)
        if trusted_title:
            return self._cache(cache_key, (trusted_title, observed_artist), 3600)
        if route_title:
            print(f"ShazamMetadata: route title selected after Apple verification failed shazamTrackId={route_id} title={route_title!r}")
            return self._cache(cache_key, (route_title, observed_artist), 45)
        return "", clean_artist(observed_artist)
