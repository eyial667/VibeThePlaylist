"""External data providers behind swappable interfaces.

  MetadataProvider  -> title/artist/album/release/ISRC + artist genre hints
  FeatureProvider   -> numeric audio features (energy/danceability/valence/…)

SpotifyMetadataProvider reuses the existing spotify_client (no new auth).
ReccoBeatsFeatureProvider gets features from ReccoBeats only (Spotify's
audio-features is deprecated/403 for new apps and is never called): lookup by
Spotify ID, else a 30s Deezer preview POSTed to ReccoBeats' extraction endpoint.
Every result is cached in the DB keyed by ISRC; calls retry with backoff.
"""
from __future__ import annotations

import abc
import time
from typing import Iterable

import requests

import config
import db
import text_utils

SRC_LOOKUP = "reccobeats_lookup"
SRC_EXTRACTED = "reccobeats_extracted"
SRC_NONE = "none"

# Numeric feature fields we keep (ReccoBeats returns more; we store these).
FEATURE_FIELDS = ("energy", "danceability", "valence", "acousticness", "tempo")


def _retry(fn, *, attempts: int = 3, base: float = 0.5):
    """Call fn() with exponential backoff; return its value or None on failure.
    A 4xx other than 429 is a definitive miss and is not retried."""
    for i in range(attempts):
        try:
            return fn()
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            if code and code != 429 and 400 <= code < 500:
                return None
            if i == attempts - 1:
                return None
            time.sleep(base * (2 ** i))
        except requests.RequestException:
            if i == attempts - 1:
                return None
            time.sleep(base * (2 ** i))
    return None


def _fetch_json(call):
    """_retry an HTTP request callable, returning parsed JSON or None."""
    def _do():
        resp = call()
        resp.raise_for_status()
        return resp.json()
    return _retry(_do)


# === MetadataProvider ======================================================
class MetadataProvider(abc.ABC):
    @abc.abstractmethod
    def get_track(self, spotify_id: str) -> dict | None:
        """Track by Spotify ID -> normalized dict (see _normalize_track) or None."""

    @abc.abstractmethod
    def search_track(self, artist: str, title: str) -> dict | None:
        """Best-match track for artist+title -> normalized dict or None."""

    def artist_genres(self, artist_ids: Iterable[str]) -> list[str]:
        """Optional artist-level genre hints (often empty; never required)."""
        return []


class SpotifyMetadataProvider(MetadataProvider):
    """Adapts the existing spotipy client; DB-cached so re-runs don't re-query."""

    def __init__(self, sp=None):
        self._sp = sp  # injected token source; lazily created if None

    @property
    def sp(self):
        if self._sp is None:
            import spotify_client as spc
            self._sp = spc.get_client()
        return self._sp

    @staticmethod
    def _normalize_track(t: dict) -> dict:
        artists = t.get("artists") or []
        return {
            "spotify_id": t.get("id"),
            "title": t.get("name", ""),
            "artist": ", ".join(a.get("name", "") for a in artists),
            "artist_ids": [a.get("id") for a in artists if a.get("id")],
            "album": (t.get("album") or {}).get("name"),
            "release_date": (t.get("album") or {}).get("release_date"),
            "isrc": (t.get("external_ids") or {}).get("isrc"),
            "genres": [],  # filled in by artist_genres() when needed
        }

    def _normalize_with_genres(self, track: dict | None) -> dict | None:
        if not track:
            return None
        norm = self._normalize_track(track)
        norm["genres"] = self.artist_genres(norm.get("artist_ids") or [])
        return norm

    def get_track(self, spotify_id: str) -> dict | None:
        cached = db.cache_get("spotify_track", spotify_id)
        if cached is not None:
            return cached or None  # {} = cached known-miss
        norm = self._normalize_with_genres(_retry(lambda: self.sp.track(spotify_id)))
        db.cache_set("spotify_track", spotify_id, norm or {})
        return norm

    def search_track(self, artist: str, title: str) -> dict | None:
        core, main = text_utils.core_title(title), text_utils.primary_artist(artist)
        cache_key = text_utils.normalize(f"{main}|{core}")
        cached = db.cache_get("spotify_search", cache_key)
        if cached is not None:
            return cached or None

        def _search(q):
            res = self.sp.search(q=q, type="track", limit=5)
            return ((res or {}).get("tracks") or {}).get("items") or []

        def _do():
            q = f'track:"{core}" artist:"{main}"' if main else f'track:"{core}"'
            items = _search(q) or _search(f"{main} {core}".strip())  # loose fallback
            return items[0] if items else None

        norm = self._normalize_with_genres(_retry(_do))
        db.cache_set("spotify_search", cache_key, norm or {})
        return norm

    def artist_genres(self, artist_ids: Iterable[str]) -> list[str]:
        out: list[str] = []
        for aid in (a for a in artist_ids if a):
            cached = db.cache_get("spotify_artist", aid)
            if cached is None:
                data = _retry(lambda aid=aid: self.sp.artist(aid))
                cached = (data or {}).get("genres", []) if data else []
                db.cache_set("spotify_artist", aid, cached)
            out.extend(g for g in cached if g not in out)
        return out


# === FeatureProvider =======================================================
class FeatureProvider(abc.ABC):
    @abc.abstractmethod
    def get_features(self, *, isrc: str | None, spotify_id: str | None,
                     artist: str = "", title: str = "") -> dict | None:
        """{energy, danceability, valence, acousticness, tempo, features_source}
        or None when no source produced features."""


class DeezerClient:
    """Free 30s preview lookup — used ONLY as the audio source for ReccoBeats
    extraction (Deezer's own API returns BPM/gain, not energy/danceability)."""

    def __init__(self, base_url: str | None = None, session: requests.Session | None = None):
        self.base_url = (base_url or config.DEEZER_BASE_URL).rstrip("/")
        self.session = session or requests.Session()

    def preview_url(self, artist: str, title: str) -> str | None:
        core, main = text_utils.core_title(title), text_utils.primary_artist(artist)
        cache_key = text_utils.normalize(f"{main}|{core}")
        cached = db.cache_get("deezer_preview", cache_key)
        if cached is not None:
            return cached or None

        q = f'artist:"{main}" track:"{core}"' if main else f'track:"{core}"'
        payload = _fetch_json(lambda: self.session.get(
            f"{self.base_url}/search", params={"q": q}, timeout=15))
        url = next((i["preview"] for i in (payload or {}).get("data") or []
                    if i.get("preview")), None)
        time.sleep(config.REQUEST_PAUSE_SEC)  # previews throttle — be polite
        db.cache_set("deezer_preview", cache_key, url or "")
        return url

    def download_preview(self, url: str) -> bytes | None:
        def _do():
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.content
        return _retry(_do)


class ReccoBeatsFeatureProvider(FeatureProvider):
    """ReccoBeats features with a Deezer->extraction fallback, cached by ISRC.

    lookup:  GET /v1/track?ids=<spotify_id> -> rb id -> GET /v1/audio-features
    extract: on a miss, POST a 30s Deezer preview to /v1/analysis/audio-features.
    """

    def __init__(self, base_url: str | None = None, deezer: DeezerClient | None = None,
                 session: requests.Session | None = None, api_key: str | None = None):
        self.base_url = (base_url or config.RECCOBEATS_BASE_URL).rstrip("/")
        self.session = session or requests.Session()
        self.deezer = deezer or DeezerClient(session=self.session)
        self.api_key = api_key if api_key is not None else config.RECCOBEATS_API_KEY
        self.headers = {"Accept": "application/json"}
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"

    def get_features(self, *, isrc, spotify_id, artist="", title="") -> dict | None:
        cache_key = isrc or text_utils.fallback_key(artist, title)
        cached = db.cache_get("reccobeats_features", cache_key)
        if cached is not None:
            return cached or None  # {} = cached known-miss
        result = (self._lookup(spotify_id) if spotify_id else None) \
            or self._extract(artist, title)
        db.cache_set("reccobeats_features", cache_key, result or {})
        return result

    def _get(self, path, params):
        return _fetch_json(lambda: self.session.get(
            f"{self.base_url}{path}", params=params, headers=self.headers, timeout=15))

    def _lookup(self, spotify_id: str) -> dict | None:
        rb_id = _first_reccobeats_id(self._get("/v1/track", {"ids": spotify_id}))
        if not rb_id:
            return None
        row = _first_feature_obj(self._get("/v1/audio-features", {"ids": rb_id}))
        if not row:
            return None
        time.sleep(config.REQUEST_PAUSE_SEC)
        return _normalize_features(row, SRC_LOOKUP)

    def _extract(self, artist: str, title: str) -> dict | None:
        if not (artist or title):
            return None
        url = self.deezer.preview_url(artist, title)
        clip = self.deezer.download_preview(url) if url else None
        if not clip:
            return None
        data = _fetch_json(lambda: self.session.post(
            f"{self.base_url}/v1/analysis/audio-features",
            files={"audioFile": ("preview.mp3", clip, "audio/mpeg")},
            headers=self.headers, timeout=60))
        row = _first_feature_obj(data)
        if not row:
            return None
        time.sleep(config.REQUEST_PAUSE_SEC)
        return _normalize_features(row, SRC_EXTRACTED)


# --- ReccoBeats payload helpers (defensive: shapes vary / fields go missing) -
def _content_list(payload) -> list:
    """Normalize {'content': [...]} / a bare list / a single object to a list."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        content = payload.get("content")
        return content if isinstance(content, list) else [payload]
    return []


def _first_item(payload, predicate) -> dict | None:
    return next((i for i in _content_list(payload)
                 if isinstance(i, dict) and predicate(i)), None)


def _first_reccobeats_id(payload) -> str | None:
    item = _first_item(payload, lambda i: i.get("id"))
    return item["id"] if item else None


def _first_feature_obj(payload) -> dict | None:
    return _first_item(payload, lambda i: any(f in i for f in FEATURE_FIELDS))


def _as_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_features(row: dict, source: str) -> dict:
    return {**{f: _as_float(row.get(f)) for f in FEATURE_FIELDS},
            "features_source": source}
