"""Tests for the LLM refinement orchestration (the Claude API call is mocked)."""
import json

import classify
import config
import db
import llm


def _seed_classified_rap(track_id="r1"):
    db.upsert_tracks([{"id": track_id, "name": "Punchline", "artist_ids": ["a1"],
                       "artist_name": "Rapper", "album": None, "added_at": None,
                       "duration_ms": 1}])
    db.upsert_artists([{"id": "a1", "name": "Rapper", "genres": ["rap"]}])
    db.upsert_tags([{"track_id": track_id, "tag": "rap", "source": "lastfm", "weight": 99}])
    classify.classify_all()  # produces a method='rules' label first


def test_available(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    assert llm.available() is False
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-xxx")
    assert llm.available() is True


def test_refine_writes_llm_labels_and_preserves_genre(temp_db, monkeypatch):
    _seed_classified_rap()

    def fake_batch(tracks):
        # one result per input track; assert we were handed genres + tags
        assert tracks[0]["genres"] == ["Hip-hop/Rap"]
        return [{"id": t["id"], "energy": "high", "moods": ["aggressive"],
                 "vibes": ["Workout", "Party"]} for t in tracks]

    monkeypatch.setattr(llm, "classify_batch", fake_batch)
    n = llm.refine()
    assert n == 1
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM labels WHERE track_id='r1'").fetchone()
    assert row["method"] == "llm"
    assert row["energy_band"] == "high"
    assert json.loads(row["moods"]) == ["aggressive"]
    assert json.loads(row["genre_buckets"]) == ["Hip-hop/Rap"]  # genre preserved


def test_refine_is_cached_and_force_redoes(temp_db, monkeypatch):
    _seed_classified_rap()
    calls = {"n": 0}

    def fake_batch(tracks):
        calls["n"] += 1
        return [{"id": t["id"], "energy": "mid", "moods": [], "vibes": ["Chill"]} for t in tracks]

    monkeypatch.setattr(llm, "classify_batch", fake_batch)
    assert llm.refine() == 1            # first pass refines
    assert llm.refine() == 0            # already method='llm' -> nothing to do
    assert llm.refine(force=True) == 1  # force re-refines
    assert calls["n"] == 2              # batch called only when there was work


def test_refine_batches_respect_size(temp_db, monkeypatch):
    for i in range(5):
        _seed_classified_rap(f"r{i}")
    monkeypatch.setattr(config, "LLM_BATCH_SIZE", 2)
    batch_sizes = []

    def fake_batch(tracks):
        batch_sizes.append(len(tracks))
        return [{"id": t["id"], "energy": "high", "moods": [], "vibes": ["Workout"]} for t in tracks]

    monkeypatch.setattr(llm, "classify_batch", fake_batch)
    total = llm.refine()
    assert total == 5
    assert batch_sizes == [2, 2, 1]  # 5 tracks in batches of 2
