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


def _energy_band(energy: float | None, tags: list[str], moods: list[str],
                 genres: list[str]) -> str | None:
    if energy is not None:
        for lo, hi, name in config.ENERGY_BANDS:
            if lo <= energy < hi:
                return name
    # Fallback 1: infer from mood tags (when present).
    if {"energetic", "aggressive"} & set(moods):
        return "high"
    if {"chill", "melancholic", "dreamy"} & set(moods):
        return "low"
    # Fallback 2: sub-genre tag hints (e.g. "cloud rap" -> mid, "drill" -> high).
    hay = " | ".join(tags)
    for needle, band in config.SUBGENRE_ENERGY_HINTS:
        if needle in hay:
            return band
    # Fallback 3: per-bucket default so energy is never empty for a known genre.
    for g in genres:
        if g in config.GENRE_ENERGY:
            return config.GENRE_ENERGY[g]
    return None


def _genre_fallback_vibes(genres: list[str]) -> list[str]:
    out: list[str] = []
    for g in genres:
        out.extend(config.GENRE_VIBES.get(g, []))
    # de-dupe preserving order
    seen, deduped = set(), []
    for v in out:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    if not deduped:
        return [config.DEFAULT_VIBE]
    return deduped if config.MULTI_LABEL else deduped[:1]


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
        # Rules produced nothing (no mood/energy signal) -> use genre fallback.
        return _genre_fallback_vibes(genres)
    return fired if config.MULTI_LABEL else fired[:1]


def classify_all(overwrite_llm: bool = False) -> int:
    """Re-classify tracks from cached signals (rules + genre fallback).

    By default this preserves labels already refined by the LLM pass
    (method='llm') so re-running classify doesn't discard paid-for results.
    Set overwrite_llm=True to reclassify everything from scratch.
    Returns the number of tracks (re)classified.
    """
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
        llm_ids = set() if overwrite_llm else {
            r["track_id"]
            for r in conn.execute("SELECT track_id FROM labels WHERE method='llm'")
        }

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for t in tracks:
        tid = t["id"]
        if tid in llm_ids:
            continue  # keep LLM-refined labels
        raw_genres: list[str] = []
        for aid in json.loads(t["artist_ids"]):
            raw_genres.extend(artist_genres.get(aid, []))
        tags = tags_by_track.get(tid, [])
        raw_genres_plus = raw_genres + tags  # tags also carry genre hints

        genres = _match_buckets(raw_genres_plus)
        moods = _match_moods(tags)
        band = _energy_band(features.get(tid), tags, moods, genres)
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
