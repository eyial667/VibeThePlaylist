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
    energy_band   TEXT,
    moods         TEXT,   -- json list
    vibes         TEXT,   -- json list
    method        TEXT,
    classified_at TEXT,
    FOREIGN KEY (track_id) REFERENCES tracks(id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


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
    with connect() as conn:
        conn.executemany(
            "INSERT INTO labels(track_id, genre_buckets, energy_band, moods, vibes, method, classified_at) "
            "VALUES(:track_id, :genre_buckets, :energy_band, :moods, :vibes, :method, :classified_at) "
            "ON CONFLICT(track_id) DO UPDATE SET genre_buckets=excluded.genre_buckets, "
            "energy_band=excluded.energy_band, moods=excluded.moods, vibes=excluded.vibes, "
            "method=excluded.method, classified_at=excluded.classified_at",
            list(rows),
        )
