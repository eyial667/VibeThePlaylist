"""Tests for the SQLite cache layer and its incremental helpers."""
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


def test_labels_upsert_overwrites(temp_db):
    db.upsert_tracks([_track("t1")])
    row = {"track_id": "t1", "genre_buckets": "[]", "energy_band": "low",
           "moods": "[]", "vibes": "[]", "method": "rules", "classified_at": "t"}
    db.upsert_labels([row])
    db.upsert_labels([{**row, "energy_band": "high"}])
    with db.connect() as conn:
        band = conn.execute("SELECT energy_band FROM labels WHERE track_id='t1'").fetchone()["energy_band"]
    assert band == "high"
