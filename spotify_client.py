"""Spotify access: OAuth, PKCE, fetching liked songs/artists/features, capability probe."""
from __future__ import annotations

import os
import time
from typing import Iterator

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth, SpotifyPKCE

import config


def _token_cache_path() -> str:
    return str(config.TOKEN_CACHE_PATH)


def _pkce_config_error() -> str:
    if config.PACKAGED_APP:
        return (
            "Spotify login is not configured in this build. Rebuild the app with a bundled "
            "SPOTIFY_CLIENT_ID before sharing it."
        )
    return (
        "Spotify login is not configured. Set SPOTIFY_CLIENT_ID in your environment or "
        "copy local_settings.example.py to local_settings.py and fill it in."
    )


def _oauth_config_error() -> str:
    if config.PACKAGED_APP:
        return (
            "Spotify credentials are not configured in this build. Rebuild the app with "
            "bundled SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET."
        )
    return (
        "Missing Spotify credentials. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in "
        "your environment or copy local_settings.example.py to local_settings.py and fill it in."
    )


def _get_cached_token() -> dict | None:
    return CacheFileHandler(cache_path=_token_cache_path()).get_cached_token()


def _required_scopes(scopes: str | None = None) -> set[str]:
    raw = scopes if scopes is not None else config.SPOTIFY_SCOPES
    return {scope for scope in raw.split() if scope}


def _token_has_required_scopes(token: dict | None, scopes: str | None = None) -> bool:
    if not token:
        return False
    granted = {scope for scope in str(token.get("scope") or "").split() if scope}
    return _required_scopes(scopes).issubset(granted)


def _token_is_valid_or_refreshable(token: dict | None) -> bool:
    if not token:
        return False
    return bool(token.get("refresh_token")) or token.get("expires_at", 0) > time.time()


def _clear_cached_token_if_scope_mismatch(scopes: str | None = None) -> bool:
    token = _get_cached_token()
    if token and not _token_has_required_scopes(token, scopes):
        logout()
        return True
    return False


def is_authenticated() -> bool:
    """True if a valid or refreshable token is cached (no network call)."""
    try:
        token = _get_cached_token()
        return _token_is_valid_or_refreshable(token) and _token_has_required_scopes(token)
    except Exception:
        return False


def get_client_pkce() -> spotipy.Spotify:
    """PKCE-based client — no client secret required. Used by the GUI."""
    if not config.SPOTIFY_CLIENT_ID:
        raise RuntimeError(_pkce_config_error())
    _clear_cached_token_if_scope_mismatch()
    auth = SpotifyPKCE(
        client_id=config.SPOTIFY_CLIENT_ID,
        redirect_uri=config.SPOTIFY_REDIRECT_URI,
        scope=config.SPOTIFY_SCOPES,
        cache_path=_token_cache_path(),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth)


def logout() -> None:
    """Remove the cached token so the next launch shows the login screen."""
    try:
        os.remove(_token_cache_path())
    except FileNotFoundError:
        pass


def get_client() -> spotipy.Spotify:
    if not (config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET):
        raise RuntimeError(_oauth_config_error())
    _clear_cached_token_if_scope_mismatch()
    auth = SpotifyOAuth(
        client_id=config.SPOTIFY_CLIENT_ID,
        client_secret=config.SPOTIFY_CLIENT_SECRET,
        redirect_uri=config.SPOTIFY_REDIRECT_URI,
        scope=config.SPOTIFY_SCOPES,
        cache_path=_token_cache_path(),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth)


def probe_capabilities(sp: spotipy.Spotify) -> dict[str, bool]:
    """Detect which (possibly restricted) endpoints this app can use.

    Spotify locked audio-features / recommendations for apps created after late
    2024. We test against one real liked track and report what works.
    """
    caps = {"audio_features": False}
    try:
        first = sp.current_user_saved_tracks(limit=1)
        items = first.get("items", [])
        if not items:
            return caps
        track_id = items[0]["track"]["id"]
        feats = sp.audio_features([track_id])
        caps["audio_features"] = bool(feats and feats[0])
    except spotipy.SpotifyException:
        caps["audio_features"] = False
    return caps


def iter_liked_tracks(sp: spotipy.Spotify) -> Iterator[dict]:
    """Yield normalized liked-track dicts (handles pagination)."""
    results = sp.current_user_saved_tracks(limit=50)
    while results:
        for item in results["items"]:
            t = item.get("track")
            if not t or not t.get("id"):
                continue  # local files / unavailable tracks
            yield {
                "id": t["id"],
                "name": t["name"],
                "artist_ids": [a["id"] for a in t["artists"] if a.get("id")],
                "artist_name": t["artists"][0]["name"] if t["artists"] else "",
                "album": (t.get("album") or {}).get("name"),
                "added_at": item.get("added_at"),
                "duration_ms": t.get("duration_ms"),
                "preview_url": t.get("preview_url"),
            }
        results = sp.next(results) if results.get("next") else None


def fetch_artists(sp: spotipy.Spotify, artist_ids: list[str]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(artist_ids), 50):
        chunk = artist_ids[i : i + 50]
        for a in sp.artists(chunk)["artists"]:
            if a:
                out.append({"id": a["id"], "name": a["name"], "genres": a.get("genres", [])})
    return out


def fetch_audio_features(sp: spotipy.Spotify, track_ids: list[str]) -> list[dict]:
    """Return feature rows; available=0 rows when the endpoint is blocked."""
    rows: list[dict] = []
    for i in range(0, len(track_ids), 100):
        chunk = track_ids[i : i + 100]
        try:
            feats = sp.audio_features(chunk)
        except spotipy.SpotifyException:
            feats = [None] * len(chunk)
        for tid, f in zip(chunk, feats):
            if f:
                rows.append({
                    "track_id": tid, "energy": f.get("energy"), "valence": f.get("valence"),
                    "danceability": f.get("danceability"), "tempo": f.get("tempo"), "available": 1,
                })
            else:
                rows.append({
                    "track_id": tid, "energy": None, "valence": None,
                    "danceability": None, "tempo": None, "available": 0,
                })
    return rows
