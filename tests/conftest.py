"""Shared pytest fixtures: an isolated temp DB and a fake Spotify client.

Tests never touch the network or your real library — db.connect() reads
config.DB_PATH dynamically, so pointing it at a tmp file fully isolates each test.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import db  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Fresh, empty SQLite DB scoped to a single test."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init()
    return path


@pytest.fixture
def seeded_db(temp_db):
    """A small but representative library: one electronic banger, one mellow folk track."""
    db.upsert_tracks([
        {"id": "t1", "name": "Night Drive", "artist_ids": ["a1"], "artist_name": "DJ X",
         "album": "A", "added_at": None, "duration_ms": 200000},
        {"id": "t2", "name": "Quiet Morning", "artist_ids": ["a2"], "artist_name": "Folk Y",
         "album": "B", "added_at": None, "duration_ms": 180000},
    ])
    db.upsert_artists([
        {"id": "a1", "name": "DJ X", "genres": ["deep house", "electronic"]},
        {"id": "a2", "name": "Folk Y", "genres": ["indie folk", "acoustic"]},
    ])
    db.upsert_features([
        {"track_id": "t1", "energy": 0.85, "valence": 0.6, "danceability": 0.8,
         "tempo": 124, "available": 1},
        {"track_id": "t2", "energy": 0.2, "valence": 0.3, "danceability": 0.3,
         "tempo": 80, "available": 1},
    ])
    db.upsert_tags([
        {"track_id": "t1", "tag": "electronic", "source": "lastfm", "weight": 100},
        {"track_id": "t1", "tag": "energetic", "source": "lastfm", "weight": 90},
        {"track_id": "t2", "tag": "folk", "source": "lastfm", "weight": 100},
        {"track_id": "t2", "tag": "mellow", "source": "lastfm", "weight": 70},
        {"track_id": "t2", "tag": "melancholic", "source": "lastfm", "weight": 60},
    ])
    return temp_db


class FakeSpotify:
    """Minimal stand-in for spotipy.Spotify used by playlist tests."""

    def __init__(self, existing: dict[str, str] | None = None):
        self.created: dict[str, str] = dict(existing or {})  # name -> id
        self.items: dict[str, list[str]] = {pid: [] for pid in self.created.values()}
        self.replace_calls: list[tuple[str, list[str]]] = []
        self._counter = len(self.created)

    def current_user(self):
        return {"id": "me"}

    def current_user_playlists(self, limit=50):
        items = [{"name": n, "id": i, "owner": {"id": "me"}} for n, i in self.created.items()]
        return {"items": items, "next": None}

    def next(self, results):
        return None

    def user_playlist_create(self, user, name, public=False, description=""):
        self._counter += 1
        pid = f"pl{self._counter}"
        self.created[name] = pid
        self.items[pid] = []
        return {"id": pid}

    def playlist_replace_items(self, pid, uris):
        self.items[pid] = list(uris)
        self.replace_calls.append((pid, list(uris)))

    def playlist_add_items(self, pid, uris):
        self.items.setdefault(pid, []).extend(uris)


@pytest.fixture
def fake_spotify():
    return FakeSpotify
