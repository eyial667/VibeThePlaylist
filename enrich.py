"""Free web enrichment: Last.fm crowd tags (genre + mood signal).

Last.fm's track.getTopTags / artist.getTopTags return community tags with a
0-100 weight. We keep the strongest as our "aggregated opinion" source.
"""
from __future__ import annotations

import time

import requests

import config

_LASTFM_URL = "http://ws.audioscrobbler.com/2.0/"


def _lastfm(method: str, **params) -> dict:
    params.update({"method": method, "api_key": config.LASTFM_API_KEY, "format": "json"})
    try:
        resp = requests.get(_LASTFM_URL, params=params, timeout=15)
        if resp.status_code != 200:
            return {}
        return resp.json()
    except requests.RequestException:
        return {}


def _parse_tags(payload: dict) -> list[tuple[str, int]]:
    tags = (payload.get("toptags") or {}).get("tag") or []
    if isinstance(tags, dict):  # single-tag responses come back as a dict
        tags = [tags]
    out = []
    for t in tags:
        name = (t.get("name") or "").strip().lower()
        try:
            weight = int(t.get("count", 0))
        except (TypeError, ValueError):
            weight = 0
        if name and weight >= config.LASTFM_MIN_TAG_WEIGHT:
            out.append((name, weight))
    return out[: config.LASTFM_MAX_TAGS]


def fetch_track_tags(artist: str, track: str) -> list[tuple[str, int]]:
    """Tags for a specific track, falling back to the artist's tags if empty."""
    tags = _parse_tags(_lastfm("track.getTopTags", artist=artist, track=track))
    if not tags:
        tags = _parse_tags(_lastfm("artist.getTopTags", artist=artist))
    time.sleep(config.REQUEST_PAUSE_SEC)
    return tags


def has_lastfm() -> bool:
    return bool(config.LASTFM_API_KEY)
