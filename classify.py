"""Turn raw signals (Spotify genres, Last.fm tags, audio features) into labels.

Pure functions over data already cached in the DB. Transparent + tunable via
config.GENRE_BUCKETS / MOOD_TAGS / ENERGY_BANDS / VIBE_RULES.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import config
import db


def _match_buckets(raw_genres: list[str]) -> list[str]:
    hay = " | ".join(g.lower() for g in raw_genres)
    matched = [
        bucket
        for bucket, needles in config.GENRE_BUCKETS.items()
        if any(n in hay for n in needles)
    ]
    if not matched:
        return [config.DEFAULT_GENRE]
    return matched if config.MULTI_LABEL else matched[:1]


def _match_moods(tags: list[str]) -> list[str]:
    hay = " | ".join(tags)
    return [
        mood
        for mood, needles in config.MOOD_TAGS.items()
        if any(n in hay for n in needles)
    ]


def _energy_band(energy: float | None, tags: list[str], moods: list[str]) -> str | None:
    if energy is not None:
        for lo, hi, name in config.ENERGY_BANDS:
            if lo <= energy < hi:
                return name
    # Fallback when audio-features is unavailable: infer from mood tags.
    if {"energetic", "aggressive"} & set(moods):
        return "high"
    if {"chill", "melancholic", "dreamy"} & set(moods):
        return "low"
    return None


def _match_vibes(energy_band: str | None, genres: list[str], moods: list[str]) -> list[str]:
    gset, mset = set(genres), set(moods)
    fired: list[str] = []
    for vibe, conditions in config.VIBE_RULES.items():
        for cond in conditions:
            ok = True
            if "energy" in cond and cond["energy"] != energy_band:
                ok = False
            if "genres" in cond and not (set(cond["genres"]) & gset):
                ok = False
            if "moods" in cond and not (set(cond["moods"]) & mset):
                ok = False
            if ok:
                fired.append(vibe)
                break
    if not fired:
        return [config.DEFAULT_VIBE]
    return fired if config.MULTI_LABEL else fired[:1]


def classify_all() -> int:
    """Re-classify every track from cached signals. Returns count classified."""
    with db.connect() as conn:
        tracks = conn.execute("SELECT id, artist_ids FROM tracks").fetchall()
        artist_genres = {
            r["id"]: json.loads(r["genres"] or "[]")
            for r in conn.execute("SELECT id, genres FROM artists")
        }
        features = {
            r["track_id"]: r["energy"]
            for r in conn.execute("SELECT track_id, energy FROM features WHERE available=1")
        }
        tags_by_track: dict[str, list[str]] = {}
        for r in conn.execute("SELECT track_id, tag FROM tags"):
            tags_by_track.setdefault(r["track_id"], []).append(r["tag"])

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for t in tracks:
        tid = t["id"]
        raw_genres: list[str] = []
        for aid in json.loads(t["artist_ids"]):
            raw_genres.extend(artist_genres.get(aid, []))
        tags = tags_by_track.get(tid, [])
        raw_genres_plus = raw_genres + tags  # tags also carry genre hints

        genres = _match_buckets(raw_genres_plus)
        moods = _match_moods(tags)
        band = _energy_band(features.get(tid), tags, moods)
        vibes = _match_vibes(band, genres, moods)

        rows.append({
            "track_id": tid,
            "genre_buckets": json.dumps(genres),
            "energy_band": band,
            "moods": json.dumps(moods),
            "vibes": json.dumps(vibes),
            "method": "rules",
            "classified_at": now,
        })

    db.upsert_labels(rows)
    return len(rows)
