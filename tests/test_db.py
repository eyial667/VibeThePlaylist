"""Tests for the SQLite cache layer and its incremental helpers."""
import importlib

import config
import db


def _track(tid, aids=("a1",)):
    return {"id": tid, "name": f"name{tid}", "artist_ids": list(aids),
            "artist_name": "Artist", "album": None, "added_at": None, "duration_ms": 1}


def test_upsert_tracks_is_insert_only(temp_db):
    db.upsert_tracks([_track("t1")])
    # re-inserting same id with a different name must NOT overwrite (ON CONFLICT DO NOTHING)
    db.upsert_tracks([{**_track("t1"), "name": "CHANGED"}])
    with db.connect() as conn:
        name = conn.execute("SELECT name FROM tracks WHERE id='t1'").fetchone()["name"]
    assert name == "namet1"
    assert db.all_track_ids() == {"t1"}


def test_all_artist_ids_dedupes_across_tracks(temp_db):
    db.upsert_tracks([_track("t1", ["a1", "a2"]), _track("t2", ["a2", "a3"])])
    assert db.all_artist_ids() == {"a1", "a2", "a3"}


def test_missing_features_helper(temp_db):
    db.upsert_tracks([_track("t1"), _track("t2")])
    db.upsert_features([{"track_id": "t1", "energy": 0.5, "valence": 0.5,
                         "danceability": 0.5, "tempo": 100, "available": 1}])
    assert db.track_ids_missing_features() == {"t2"}


def test_missing_tags_helper(temp_db):
    db.upsert_tracks([_track("t1"), _track("t2")])
    db.upsert_tags([{"track_id": "t1", "tag": "rock", "source": "lastfm", "weight": 50}])
    assert db.track_ids_missing_tags() == {"t2"}


def test_artist_upsert_updates_genres(temp_db):
    db.upsert_artists([{"id": "a1", "name": "X", "genres": ["rock"]}])
    db.upsert_artists([{"id": "a1", "name": "X", "genres": ["jazz", "soul"]}])
    with db.connect() as conn:
        import json
        genres = json.loads(conn.execute("SELECT genres FROM artists WHERE id='a1'").fetchone()["genres"])
    assert genres == ["jazz", "soul"]
    assert db.known_artist_ids() == {"a1"}


def test_meta_roundtrip(temp_db):
    assert db.get_meta("missing", "fallback") == "fallback"
    db.set_meta("audio_features_available", "1")
    assert db.get_meta("audio_features_available") == "1"
    db.set_meta("audio_features_available", "0")  # upsert overwrites
    assert db.get_meta("audio_features_available") == "0"


def test_migrate_adds_subgenres_column_to_old_labels(temp_db):
    # simulate a DB created before the subgenres column existed
    with db.connect() as conn:
        conn.execute("DROP TABLE labels")
        conn.execute(
            "CREATE TABLE labels (track_id TEXT PRIMARY KEY, genre_buckets TEXT, "
            "energy_band TEXT, vibes TEXT, method TEXT, classified_at TEXT)"
        )
    db.init()  # runs the migration
    with db.connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(labels)")}
    assert "subgenres" in cols
    db.init()  # idempotent: a second run must not error


def test_clean_drops_stale_columns(temp_db):
    # Simulate a DB that still has the old moods column.
    with db.connect() as conn:
        conn.execute("ALTER TABLE labels ADD COLUMN moods TEXT")
    with db.connect() as conn:
        cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(labels)")}
    assert "moods" in cols_before

    changes = db.clean()
    assert any("moods" in c for c in changes)

    with db.connect() as conn:
        cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(labels)")}
    assert "moods" not in cols_after

    # Idempotent: second run reports nothing.
    assert db.clean() == []


def test_labels_upsert_overwrites(temp_db):
    db.upsert_tracks([_track("t1")])
    row = {"track_id": "t1", "genre_buckets": "[]", "energy_band": "low",
           "vibes": "[]", "method": "rules", "classified_at": "t"}
    db.upsert_labels([row])
    db.upsert_labels([{**row, "energy_band": "high"}])
    with db.connect() as conn:
        band = conn.execute("SELECT energy_band FROM labels WHERE track_id='t1'").fetchone()["energy_band"]
    assert band == "high"


def test_using_runtime_scope_isolates_per_user_dbs(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBETHEPLAYLIST_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("VIBETHEPLAYLIST_CACHE_DIR", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_DB_PATH", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_TOKEN_CACHE_PATH", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_PREVIEW_CACHE_PATH", raising=False)
    importlib.reload(config)
    try:
        with config.using_runtime_scope("alice"):
            db.init()
            db.set_meta("owner", "alice")

        with config.using_runtime_scope("bob"):
            db.init()
            assert db.get_meta("owner") is None
            db.set_meta("owner", "bob")

        with config.using_runtime_scope("alice"):
            assert db.get_meta("owner") == "alice"

        with config.using_runtime_scope("bob"):
            assert db.get_meta("owner") == "bob"
    finally:
        importlib.reload(config)
