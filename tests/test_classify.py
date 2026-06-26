"""Tests for the classification rules engine."""
import json

import classify
import config
import db


# --- genre bucketing -------------------------------------------------------
def test_match_buckets_maps_fine_genre_to_coarse():
    assert classify._match_buckets(["deep house"]) == ["Electronic"]
    assert classify._match_buckets(["indie folk", "acoustic"]) == ["Folk/Acoustic"]


def test_match_buckets_unknown_genre_is_default():
    assert classify._match_buckets(["polka"]) == [config.DEFAULT_GENRE]


def test_match_buckets_multi_label(monkeypatch):
    monkeypatch.setattr(config, "MULTI_LABEL", True)
    result = classify._match_buckets(["jazz", "hip hop"])
    assert set(result) == {"Jazz", "Hip-hop/Rap"}


def test_match_buckets_single_label(monkeypatch):
    monkeypatch.setattr(config, "MULTI_LABEL", False)
    result = classify._match_buckets(["jazz", "hip hop"])
    assert len(result) == 1


# --- moods -----------------------------------------------------------------
def test_match_moods():
    assert "energetic" in classify._match_moods(["energetic", "banger"])
    assert classify._match_moods(["nonsense"]) == []


# --- energy banding --------------------------------------------------------
def test_energy_band_from_features():
    assert classify._energy_band(0.9, [], []) == "high"
    assert classify._energy_band(0.5, [], []) == "mid"
    assert classify._energy_band(0.1, [], []) == "low"


def test_energy_band_inferred_from_moods_when_no_features():
    assert classify._energy_band(None, [], ["energetic"]) == "high"
    assert classify._energy_band(None, [], ["chill"]) == "low"
    assert classify._energy_band(None, [], []) is None


# --- vibe rules ------------------------------------------------------------
def test_match_vibes_high_energy_electronic_is_workout():
    vibes = classify._match_vibes("high", ["Electronic"], ["energetic"])
    assert "Workout" in vibes


def test_match_vibes_default_when_nothing_fires():
    assert classify._match_vibes(None, [config.DEFAULT_GENRE], []) == [config.DEFAULT_VIBE]


# --- end to end ------------------------------------------------------------
def test_classify_all_labels_seeded_library(seeded_db):
    n = classify.classify_all()
    assert n == 2
    with db.connect() as conn:
        labels = {r["track_id"]: r for r in conn.execute("SELECT * FROM labels")}

    t1 = labels["t1"]
    assert json.loads(t1["genre_buckets"]) == ["Electronic"]
    assert t1["energy_band"] == "high"
    assert "Workout" in json.loads(t1["vibes"])

    t2 = labels["t2"]
    assert json.loads(t2["genre_buckets"]) == ["Folk/Acoustic"]
    assert t2["energy_band"] == "low"
    assert "melancholic" in json.loads(t2["moods"])


def test_classify_all_is_idempotent(seeded_db):
    classify.classify_all()
    classify.classify_all()  # second run must not duplicate rows
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM labels").fetchone()["c"]
    assert count == 2
