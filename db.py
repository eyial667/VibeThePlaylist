"""SQLite cache + data store.

The DB is the source of truth across runs so that re-runs are incremental:
already-fetched tracks and already-enriched data are never re-requested.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterable, Iterator

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    artist_ids  TEXT NOT NULL,   -- json list
    artist_name TEXT NOT NULL,   -- primary artist, for convenience
    album       TEXT,
    added_at    TEXT,
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS artists (
    id      TEXT PRIMARY KEY,
    name    TEXT,
    genres  TEXT             -- json list of raw Spotify genres
);

CREATE TABLE IF NOT EXISTS features (
    track_id     TEXT PRIMARY KEY,
    energy       REAL,
    valence      REAL,
    danceability REAL,
    tempo        REAL,
    available    INTEGER DEFAULT 0,   -- 1 if audio-features were retrievable
    FOREIGN KEY (track_id) REFERENCES tracks(id)
);

CREATE TABLE IF NOT EXISTS tags (
    track_id TEXT,
    tag      TEXT,
    source   TEXT,
    weight   INTEGER,
    PRIMARY KEY (track_id, tag, source)
);

CREATE TABLE IF NOT EXISTS labels (
    track_id      TEXT PRIMARY KEY,
    genre_buckets TEXT,   -- json list
    subgenres     TEXT,   -- json list (precise, nested under genre_buckets)
    energy_band   TEXT,
    vibes         TEXT,   -- json list
    method        TEXT,
    classified_at TEXT,
    FOREIGN KEY (track_id) REFERENCES tracks(id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- genre-specification feature ------------------------------------------------
-- Rich per-track classification keyed by ISRC (the canonical join key). Rows
-- are upserted by ISRC; tracks with no resolvable ISRC use a normalized
-- "key:artist|title" fallback so they still get a result and show up in the
-- coverage report.
CREATE TABLE IF NOT EXISTS classifications (
    isrc             TEXT PRIMARY KEY,
    spotify_id       TEXT,
    title            TEXT,
    artist           TEXT,
    genre            TEXT,
    subgenre         TEXT,
    energy           TEXT,          -- low | mid | high
    vibe             TEXT,          -- json list
    confidence       REAL,
    features_source  TEXT,          -- reccobeats_lookup | reccobeats_extracted | none
    energy_raw       REAL,
    danceability     REAL,
    valence          REAL,
    acousticness     REAL,
    tempo            REAL,
    match_confidence REAL,          -- null when not name-matched
    classified_at    TEXT,
    model_used       TEXT,
    notes            TEXT
);

-- Generic external-API cache. Every external lookup (Spotify metadata,
-- ReccoBeats features, Deezer previews, and the LLM result) is cached here
-- keyed by (namespace, key) where key is the ISRC wherever possible, so re-runs
-- never re-hit an API for data already seen.
CREATE TABLE IF NOT EXISTS api_cache (
    namespace  TEXT,
    key        TEXT,
    value      TEXT,   -- json
    fetched_at TEXT,
    PRIMARY KEY (namespace, key)
);
"""

# Additive columns layered onto pre-existing DBs. CREATE TABLE IF NOT EXISTS
# can't add columns to a table that already exists, so we ALTER them in on init.
# (table, column, type) — applied only when the column is absent.
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("tracks", "isrc", "TEXT"),
    ("tracks", "spotify_id", "TEXT"),
    ("tracks", "match_confidence", "REAL"),
    ("tracks", "resolution_method", "TEXT"),
    ("labels", "subgenres", "TEXT"),  # from the subgenre classification work
]

# Columns that were removed from the schema and should be dropped from old DBs.
# `python cli.py clean` applies these; `ALTER TABLE DROP COLUMN` requires SQLite
# 3.35+ (2021-03), which is bundled in all supported Python versions (3.11+).
# (table, column) — skipped silently when the column is already absent.
_COLUMN_DROPS: list[tuple[str, str]] = [
    ("labels", "moods"),  # mood classification removed (almost no songs had one)
]


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Add any columns introduced after a DB was first created (idempotent).
    `CREATE TABLE IF NOT EXISTS` won't alter an existing table, so new nullable
    columns are backfilled here on every init()."""
    for table, column, coltype in _MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def clean() -> list[str]:
    """Drop stale columns from an existing DB and return a description of each change.

    Safe to run repeatedly — columns already absent are silently skipped.
    Requires SQLite 3.35+ (ALTER TABLE DROP COLUMN), bundled in Python 3.11+.
    """
    changes: list[str] = []
    with connect() as conn:
        for table, column in _COLUMN_DROPS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column in cols:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
                changes.append(f"dropped column {table}.{column}")
    return changes


# --- meta -----------------------------------------------------------------
def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_meta(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


# --- tracks ---------------------------------------------------------------
def upsert_tracks(rows: Iterable[dict]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO tracks(id, name, artist_ids, artist_name, album, added_at, duration_ms) "
            "VALUES(:id, :name, :artist_ids, :artist_name, :album, :added_at, :duration_ms) "
            "ON CONFLICT(id) DO NOTHING",
            [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "artist_ids": json.dumps(r["artist_ids"]),
                    "artist_name": r["artist_name"],
                    "album": r.get("album"),
                    "added_at": r.get("added_at"),
                    "duration_ms": r.get("duration_ms"),
                }
                for r in rows
            ],
        )


def all_track_ids() -> set[str]:
    with connect() as conn:
        return {r["id"] for r in conn.execute("SELECT id FROM tracks")}


def all_artist_ids() -> set[str]:
    ids: set[str] = set()
    with connect() as conn:
        for r in conn.execute("SELECT artist_ids FROM tracks"):
            ids.update(json.loads(r["artist_ids"]))
    return ids


# --- artists --------------------------------------------------------------
def upsert_artists(rows: Iterable[dict]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO artists(id, name, genres) VALUES(:id, :name, :genres) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, genres=excluded.genres",
            [{"id": r["id"], "name": r["name"], "genres": json.dumps(r["genres"])} for r in rows],
        )


def known_artist_ids() -> set[str]:
    with connect() as conn:
        return {r["id"] for r in conn.execute("SELECT id FROM artists")}


# --- features -------------------------------------------------------------
def upsert_features(rows: Iterable[dict]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO features(track_id, energy, valence, danceability, tempo, available) "
            "VALUES(:track_id, :energy, :valence, :danceability, :tempo, :available) "
            "ON CONFLICT(track_id) DO UPDATE SET energy=excluded.energy, valence=excluded.valence, "
            "danceability=excluded.danceability, tempo=excluded.tempo, available=excluded.available",
            list(rows),
        )


def track_ids_missing_features() -> set[str]:
    with connect() as conn:
        return {
            r["id"]
            for r in conn.execute(
                "SELECT t.id FROM tracks t LEFT JOIN features f ON t.id=f.track_id "
                "WHERE f.track_id IS NULL"
            )
        }


# --- tags -----------------------------------------------------------------
def upsert_tags(rows: Iterable[dict]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO tags(track_id, tag, source, weight) "
            "VALUES(:track_id, :tag, :source, :weight) "
            "ON CONFLICT(track_id, tag, source) DO UPDATE SET weight=excluded.weight",
            list(rows),
        )


def track_ids_missing_tags() -> set[str]:
    with connect() as conn:
        return {
            r["id"]
            for r in conn.execute(
                "SELECT t.id FROM tracks t LEFT JOIN tags g ON t.id=g.track_id "
                "WHERE g.track_id IS NULL"
            )
        }


# --- labels ---------------------------------------------------------------
def upsert_labels(rows: Iterable[dict]) -> None:
    # `subgenres` is optional for callers (added after the original schema);
    # default to an empty JSON list so older code paths keep working.
    payload = [{"subgenres": "[]", **r} for r in rows]
    with connect() as conn:
        conn.executemany(
            "INSERT INTO labels(track_id, genre_buckets, subgenres, energy_band, vibes, method, classified_at) "
            "VALUES(:track_id, :genre_buckets, :subgenres, :energy_band, :vibes, :method, :classified_at) "
            "ON CONFLICT(track_id) DO UPDATE SET genre_buckets=excluded.genre_buckets, "
            "subgenres=excluded.subgenres, energy_band=excluded.energy_band, "
            "vibes=excluded.vibes, "
            "method=excluded.method, classified_at=excluded.classified_at",
            payload,
        )


# --- genre-specification: track identifier write-back ---------------------
def set_track_identifiers(
    track_id: str,
    isrc: str | None,
    spotify_id: str | None,
    match_confidence: float | None,
    resolution_method: str | None,
) -> None:
    """Persist a resolved ISRC (and spotify_id) onto a library track row so
    future runs skip re-resolution. No-op if the track isn't in the library
    (ad-hoc CLI tracks have no `tracks` row)."""
    with connect() as conn:
        conn.execute(
            "UPDATE tracks SET isrc=?, spotify_id=?, match_confidence=?, "
            "resolution_method=? WHERE id=?",
            (isrc, spotify_id, match_confidence, resolution_method, track_id),
        )


def tracks_for_classification() -> list[dict]:
    """Library rows the batch classifier consumes (includes any cached ISRC)."""
    with connect() as conn:
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "artist_ids": json.loads(r["artist_ids"]),
                "artist_name": r["artist_name"],
                "album": r["album"],
                "isrc": r["isrc"] if "isrc" in r.keys() else None,
            }
            for r in conn.execute(
                "SELECT id, name, artist_ids, artist_name, album, isrc FROM tracks"
            )
        ]


# --- genre-specification: classifications ---------------------------------
_CLASSIFICATION_COLS = [
    "isrc", "spotify_id", "title", "artist", "genre", "subgenre", "energy",
    "vibe", "confidence", "features_source", "energy_raw", "danceability",
    "valence", "acousticness", "tempo", "match_confidence", "classified_at",
    "model_used", "notes",
]
_CLASSIFICATION_SQL = (
    "INSERT INTO classifications ({cols}) VALUES ({vals}) "
    "ON CONFLICT(isrc) DO UPDATE SET {updates}"
).format(
    cols=", ".join(_CLASSIFICATION_COLS),
    vals=", ".join(f":{c}" for c in _CLASSIFICATION_COLS),
    updates=", ".join(f"{c}=excluded.{c}" for c in _CLASSIFICATION_COLS if c != "isrc"),
)


def upsert_classification(row: dict) -> None:
    """Insert/replace one classification, keyed by ISRC (or fallback key)."""
    payload = {c: row.get(c) for c in _CLASSIFICATION_COLS}
    if isinstance(payload.get("vibe"), (list, tuple)):
        payload["vibe"] = json.dumps(list(payload["vibe"]))
    with connect() as conn:
        conn.execute(_CLASSIFICATION_SQL, payload)


def get_classification(isrc: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM classifications WHERE isrc=?", (isrc,)
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["vibe"] = json.loads(out["vibe"] or "[]")
    return out


def classified_keys() -> set[str]:
    """ISRCs (and fallback keys) already classified — used for batch resume."""
    with connect() as conn:
        return {r["isrc"] for r in conn.execute("SELECT isrc FROM classifications")}


def all_classifications() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM classifications ORDER BY classified_at"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["vibe"] = json.loads(d["vibe"] or "[]")
        out.append(d)
    return out


# --- generic external-API cache (keyed by namespace + ISRC where possible) --
def cache_get(namespace: str, key: str):
    """Return the cached JSON value for (namespace, key), or None on miss."""
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM api_cache WHERE namespace=? AND key=?",
            (namespace, key),
        ).fetchone()
    return json.loads(row["value"]) if row else None


def cache_set(namespace: str, key: str, value) -> None:
    from datetime import datetime, timezone

    with connect() as conn:
        conn.execute(
            "INSERT INTO api_cache(namespace, key, value, fetched_at) "
            "VALUES(?, ?, ?, ?) ON CONFLICT(namespace, key) DO UPDATE SET "
            "value=excluded.value, fetched_at=excluded.fetched_at",
            (namespace, key, json.dumps(value),
             datetime.now(timezone.utc).isoformat()),
        )
