"""External data providers behind clean interfaces.

Three swappable roles, each an ABC so a paid/alternate backend can drop in later
without touching the resolver, classifier, or pipeline:

  MetadataProvider  -> title/artist/album/release/ISRC + artist genre hints
  FeatureProvider   -> numeric audio features (energy, danceability, valence, …)
  (Classifier lives in genre_classifier.py)

Concrete implementations here:
  SpotifyMetadataProvider   wraps the existing spotify_client (OAuth reused).
  ReccoBeatsFeatureProvider numeric features from ReccoBeats only — Spotify's
                            audio-features endpoint is deprecated (403 for new
                            apps since 2024-11-27) and is NEVER called. Lookup by
                            Spotify ID first; on a miss, fall back to fetching a
                            30s Deezer preview and POSTing it to ReccoBeats' audio
                            feature EXTRACTION endpoint.
  DeezerClient              finds a free 30s preview mp3 for the extraction path.

Every network result is cached through the DB cache layer (db.cache_get/set),
keyed by ISRC wherever possible, and calls are spaced out politely with backoff.
"""
from __future__ import annotations

import abc
import time
from typing import Iterable

import requests

import config
import db
import text_utils

# --- feature_source labels (persisted) -------------------------------------
SRC_LOOKUP = "reccobeats_lookup"
SRC_EXTRACTED = "reccobeats_extracted"
SRC_NONE = "none"

# Numeric feature fields we keep (ReccoBeats returns more; we store these).
FEATURE_FIELDS = ("energy", "danceability", "valence", "acousticness", "tempo")


# ===========================================================================
# MetadataProvider
# ===========================================================================
class MetadataProvider(abc.ABC):
    """Source of catalog metadata + identifier resolution data."""

    @abc.abstractmethod
    def get_track(self, spotify_id: str) -> dict | None:
        """Track by Spotify ID -> normalized dict (see _normalize_track) or None."""

    @abc.abstractmethod
    def search_track(self, artist: str, title: str) -> dict | None:
        """Best-match track for artist+title -> normalized dict or None."""

    def artist_genres(self, artist_ids: Iterable[str]) -> list[str]:
        """Optional artist-level genre hints (often empty; never required)."""
        return []


def _retry(fn, *, attempts: int = 3, base: float = 0.5):
    """Call fn() with exponential backoff on transient HTTP/network errors.

    Returns fn()'s value, or None if every attempt fails. 4xx (except 429) are
    treated as definitive misses and not retried."""
    for i in range(attempts):
        try:
            return fn()
        except requests.HTTPError as exc:  # noqa: PERF203
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


class SpotifyMetadataProvider(MetadataProvider):
    """Adapts the existing spotipy client to the MetadataProvider interface.

    Reuses spotify_client.get_client() (OAuth + token cache already configured);
    no new auth. Results are cached in the DB so repeated runs don't re-query.
    """

    def __init__(self, sp=None):
        self._sp = sp  # injected token source; lazily created if None

    @property
    def sp(self):
        if self._sp is None:
            import spotify_client as spc
            self._sp = spc.get_client()
        return self._sp

    # --- normalization -------------------------------------------------------
    @staticmethod
    def _normalize_track(t: dict) -> dict:
        artists = t.get("artists") or []
        return {
            "spotify_id": t.get("id"),
            "title": t.get("name", ""),
            "artist": ", ".join(a.get("name", "") for a in artists) if artists else "",
            "artist_ids": [a.get("id") for a in artists if a.get("id")],
            "album": (t.get("album") or {}).get("name"),
            "release_date": (t.get("album") or {}).get("release_date"),
            "isrc": (t.get("external_ids") or {}).get("isrc"),
            "genres": [],  # filled in by artist_genres() when needed
        }

    def get_track(self, spotify_id: str) -> dict | None:
        cached = db.cache_get("spotify_track", spotify_id)
        if cached is not None:
            return cached or None  # {} cached for a known-miss
        track = _retry(lambda: self.sp.track(spotify_id))
        norm = self._normalize_track(track) if track else None
        if norm:
            norm["genres"] = self.artist_genres(norm.get("artist_ids") or [])
        db.cache_set("spotify_track", spotify_id, norm or {})
        return norm

    def search_track(self, artist: str, title: str) -> dict | None:
        # Query with accent/feature/remix-folded terms for cross-region recall.
        core = text_utils.core_title(title)
        main_artist = text_utils.primary_artist(artist)
        q = f'track:"{core}" artist:"{main_artist}"' if main_artist else f'track:"{core}"'
        cache_key = text_utils.normalize(f"{main_artist}|{core}")
        cached = db.cache_get("spotify_search", cache_key)
        if cached is not None:
            return cached or None

        def _do():
            res = self.sp.search(q=q, type="track", limit=5)
            items = ((res or {}).get("tracks") or {}).get("items") or []
            # Fall back to a looser free-text query if the structured one misses.
            if not items:
                res = self.sp.search(q=f"{main_artist} {core}".strip(),
                                     type="track", limit=5)
                items = ((res or {}).get("tracks") or {}).get("items") or []
            return items[0] if items else None

        top = _retry(_do)
        norm = self._normalize_track(top) if top else None
        if norm:
            norm["genres"] = self.artist_genres(norm.get("artist_ids") or [])
        db.cache_set("spotify_search", cache_key, norm or {})
        return norm

    def artist_genres(self, artist_ids: Iterable[str]) -> list[str]:
        ids = [a for a in artist_ids if a]
        out: list[str] = []
        for aid in ids:
            cached = db.cache_get("spotify_artist", aid)
            if cached is None:
                data = _retry(lambda aid=aid: self.sp.artist(aid))
                cached = (data or {}).get("genres", []) if data else []
                db.cache_set("spotify_artist", aid, cached)
            for g in cached:
                if g not in out:
                    out.append(g)
        return out


# ===========================================================================
# FeatureProvider
# ===========================================================================
class FeatureProvider(abc.ABC):
    """Source of numeric audio features (energy/danceability/valence/…)."""

    @abc.abstractmethod
    def get_features(self, *, isrc: str | None, spotify_id: str | None,
                     artist: str = "", title: str = "") -> dict | None:
        """Return {energy, danceability, valence, acousticness, tempo,
        features_source} or None when no source produced features."""


class DeezerClient:
    """Free 30s preview lookup (broad international catalog). Used ONLY as the
    audio source for the ReccoBeats extraction fallback — Deezer's own API
    returns BPM/gain, not energy/danceability, so we never read features off it.
    """

    def __init__(self, base_url: str | None = None, session: requests.Session | None = None):
        self.base_url = (base_url or config.DEEZER_BASE_URL).rstrip("/")
        self.session = session or requests.Session()

    def preview_url(self, artist: str, title: str) -> str | None:
        core = text_utils.core_title(title)
        main_artist = text_utils.primary_artist(artist)
        cache_key = text_utils.normalize(f"{main_artist}|{core}")
        cached = db.cache_get("deezer_preview", cache_key)
        if cached is not None:
            return cached or None

        q = f'artist:"{main_artist}" track:"{core}"' if main_artist else f'track:"{core}"'

        def _do():
            resp = self.session.get(f"{self.base_url}/search",
                                    params={"q": q}, timeout=15)
            resp.raise_for_status()
            data = (resp.json() or {}).get("data") or []
            for item in data:
                if item.get("preview"):
                    return item["preview"]
            return None

        url = _retry(_do)
        time.sleep(config.REQUEST_PAUSE_SEC)  # Deezer previews throttle — be polite
        db.cache_set("deezer_preview", cache_key, url or "")
        return url

    def download_preview(self, url: str) -> bytes | None:
        def _do():
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.content

        return _retry(_do)


class ReccoBeatsFeatureProvider(FeatureProvider):
    """ReccoBeats audio features, with a Deezer->extraction fallback.

    Path 1 (lookup): GET /v1/track?ids=<spotify_id> -> internal ReccoBeats id,
                     then GET /v1/audio-features?ids=<rb_id> for the numbers.
    Path 2 (extract): on a lookup miss, grab a 30s Deezer preview and POST it to
                     /v1/analysis/audio-features (multipart, field 'audioFile').
    All results cached by ISRC (or fallback key).
    """

    def __init__(self, base_url: str | None = None, deezer: DeezerClient | None = None,
                 session: requests.Session | None = None, api_key: str | None = None):
        self.base_url = (base_url or config.RECCOBEATS_BASE_URL).rstrip("/")
        self.session = session or requests.Session()
        self.deezer = deezer or DeezerClient(session=self.session)
        self.api_key = api_key if api_key is not None else config.RECCOBEATS_API_KEY

    def _headers(self) -> dict:
        h = {"Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    # --- public --------------------------------------------------------------
    def get_features(self, *, isrc: str | None, spotify_id: str | None,
                     artist: str = "", title: str = "") -> dict | None:
        cache_key = isrc or text_utils.fallback_key(artist, title)
        cached = db.cache_get("reccobeats_features", cache_key)
        if cached is not None:
            return cached or None  # {} = cached known-miss

        result = None
        if spotify_id:
            result = self._lookup(spotify_id)
        if result is None:
            result = self._extract(artist, title)

        db.cache_set("reccobeats_features", cache_key, result or {})
        return result

    # --- path 1: lookup by Spotify id ---------------------------------------
    def _lookup(self, spotify_id: str) -> dict | None:
        def _track():
            resp = self.session.get(f"{self.base_url}/v1/track",
                                    params={"ids": spotify_id},
                                    headers=self._headers(), timeout=15)
            resp.raise_for_status()
            return resp.json()

        payload = _retry(_track)
        rb_id = _first_reccobeats_id(payload)
        if not rb_id:
            return None

        def _feats():
            resp = self.session.get(f"{self.base_url}/v1/audio-features",
                                    params={"ids": rb_id},
                                    headers=self._headers(), timeout=15)
            resp.raise_for_status()
            return resp.json()

        feats = _retry(_feats)
        row = _first_feature_obj(feats)
        if not row:
            return None
        time.sleep(config.REQUEST_PAUSE_SEC)
        return _normalize_features(row, SRC_LOOKUP)

    # --- path 2: Deezer preview -> extraction -------------------------------
    def _extract(self, artist: str, title: str) -> dict | None:
        if not (artist or title):
            return None
        url = self.deezer.preview_url(artist, title)
        if not url:
            return None
        clip = self.deezer.download_preview(url)
        if not clip:
            return None

        def _do():
            resp = self.session.post(
                f"{self.base_url}/v1/analysis/audio-features",
                files={"audioFile": ("preview.mp3", clip, "audio/mpeg")},
                headers=self._headers(), timeout=60,
            )
            resp.raise_for_status()
            return resp.json()

        data = _retry(_do)
        row = _first_feature_obj(data)
        if not row:
            return None
        time.sleep(config.REQUEST_PAUSE_SEC)
        return _normalize_features(row, SRC_EXTRACTED)


# --- ReccoBeats payload helpers (defensive: shapes vary / fields go missing) -
def _content_list(payload) -> list:
    """ReccoBeats wraps collections as {'content': [...]} or returns a bare list
    or a single object; normalize all three to a list."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("content"), list):
            return payload["content"]
        return [payload]
    return []


def _first_reccobeats_id(payload) -> str | None:
    for item in _content_list(payload):
        if isinstance(item, dict) and item.get("id"):
            return item["id"]
    return None


def _first_feature_obj(payload) -> dict | None:
    for item in _content_list(payload):
        if isinstance(item, dict) and any(f in item for f in FEATURE_FIELDS):
            return item
    return None


def _normalize_features(row: dict, source: str) -> dict:
    out = {f: _as_float(row.get(f)) for f in FEATURE_FIELDS}
    out["features_source"] = source
    return out


def _as_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
