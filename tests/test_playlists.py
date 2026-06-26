"""Tests for playlist clustering and idempotent Spotify sync (via a fake client)."""
import json

import config
import db
import playlists


def _label(tid, genres, vibes):
    return {"track_id": tid, "genre_buckets": json.dumps(genres), "energy_band": "high",
            "moods": "[]", "vibes": json.dumps(vibes), "method": "rules", "classified_at": "t"}


def _seed_labels(rows):
    db.upsert_tracks([
        {"id": r["track_id"], "name": r["track_id"], "artist_ids": ["a"],
         "artist_name": "A", "album": None, "added_at": None, "duration_ms": 1}
        for r in rows
    ])
    db.upsert_labels(rows)


def test_clusters_cover_all_three_schemes(temp_db, monkeypatch):
    monkeypatch.setattr(config, "PLAYLIST_SCHEMES", ["vibe", "genre", "combined"])
    monkeypatch.setattr(config, "MIN_TRACKS_PER_PLAYLIST", 1)
    _seed_labels([_label("t1", ["Jazz"], ["Chill"]), _label("t2", ["Jazz"], ["Chill"])])
    clusters = playlists._clusters()
    assert "Jazz" in clusters          # genre scheme
    assert "Chill" in clusters         # vibe scheme
    assert "Chill Jazz" in clusters    # combined scheme


def test_clusters_respect_min_tracks(temp_db, monkeypatch):
    monkeypatch.setattr(config, "PLAYLIST_SCHEMES", ["genre"])
    monkeypatch.setattr(config, "MIN_TRACKS_PER_PLAYLIST", 2)
    _seed_labels([_label("t1", ["Jazz"], ["Chill"]), _label("t2", ["Rock"], ["Chill"])])
    clusters = playlists._clusters()
    assert clusters == {}  # each genre has only one track -> filtered out


def test_sync_dry_run_writes_nothing(temp_db, fake_spotify, monkeypatch):
    monkeypatch.setattr(config, "PLAYLIST_SCHEMES", ["genre"])
    monkeypatch.setattr(config, "MIN_TRACKS_PER_PLAYLIST", 1)
    _seed_labels([_label("t1", ["Jazz"], ["Chill"])])
    sp = fake_spotify()
    summary = playlists.sync_playlists(sp, dry_run=True)
    assert ("Jazz", 1) in summary
    assert sp.created == {}  # nothing created


def test_create_named_playlist_adds_prefix_and_tracks(temp_db, fake_spotify):
    sp = fake_spotify()
    full = playlists.create_named_playlist(sp, "Chill Jazz", ["t1", "t2"])
    assert full == f"{config.PLAYLIST_PREFIX}Chill Jazz"
    pid = sp.created[full]
    assert sp.items[pid] == ["spotify:track:t1", "spotify:track:t2"]


def test_create_named_playlist_is_idempotent(temp_db, fake_spotify):
    sp = fake_spotify()
    playlists.create_named_playlist(sp, "Chill", ["t1"])
    playlists.create_named_playlist(sp, "Chill", ["t1", "t2"])  # update, not duplicate
    full = f"{config.PLAYLIST_PREFIX}Chill"
    assert list(sp.created).count(full) == 1
    assert len(sp.created) == 1
    pid = sp.created[full]
    assert sp.items[pid] == ["spotify:track:t1", "spotify:track:t2"]


def test_create_named_playlist_does_not_double_prefix(temp_db, fake_spotify):
    sp = fake_spotify()
    name = f"{config.PLAYLIST_PREFIX}Already"
    full = playlists.create_named_playlist(sp, name, ["t1"])
    assert full == name  # prefix not applied twice
