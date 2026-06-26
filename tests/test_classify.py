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
    monkeypatch.setattr(config, "MAX_GENRES", 2)
    result = classify._match_buckets(["jazz", "hip hop"])
    assert set(result) == {"Jazz", "Hip-hop/Rap"}


def test_match_buckets_single_label(monkeypatch):
    monkeypatch.setattr(config, "MULTI_LABEL", False)
    result = classify._match_buckets(["jazz", "hip hop"])
    assert len(result) == 1


def test_match_buckets_caps_to_max_genres(monkeypatch):
    monkeypatch.setattr(config, "MULTI_LABEL", True)
    monkeypatch.setattr(config, "MAX_GENRES", 2)
    # five raw genres touching 3+ buckets -> capped to 2
    result = classify._match_buckets(["french hip hop", "rap", "trap", "pop urbaine", "hardcore"])
    assert len(result) == 2


def test_match_buckets_ranks_by_hit_strength(monkeypatch):
    monkeypatch.setattr(config, "MULTI_LABEL", True)
    monkeypatch.setattr(config, "MAX_GENRES", 1)
    # Hip-hop/Rap has 3 needle hits vs Pop's incidental 1 ('pop' in 'pop urbaine')
    result = classify._match_buckets(["french hip hop", "rap", "trap", "pop urbaine"])
    assert result == ["Hip-hop/Rap"]


# --- moods -----------------------------------------------------------------
def test_match_moods():
    assert "energetic" in classify._match_moods(["energetic", "banger"])
    assert classify._match_moods(["nonsense"]) == []


# --- energy banding --------------------------------------------------------
def test_energy_band_from_features():
    assert classify._energy_band(0.9, [], [], []) == "high"
    assert classify._energy_band(0.5, [], [], []) == "mid"
    assert classify._energy_band(0.1, [], [], []) == "low"


def test_energy_band_inferred_from_moods_when_no_features():
    assert classify._energy_band(None, [], ["energetic"], []) == "high"
    assert classify._energy_band(None, [], ["chill"], []) == "low"


def test_energy_band_subgenre_hint():
    # "cloud rap" tag should pull a Hip-hop/Rap track down from the bucket default
    assert classify._energy_band(None, ["cloud rap"], [], ["Hip-hop/Rap"]) == "mid"


def test_energy_band_genre_fallback_when_no_signal():
    # no features, no mood, no sub-genre hint -> per-bucket default (never None for known genre)
    assert classify._energy_band(None, ["french", "rap"], [], ["Hip-hop/Rap"]) == "high"
    assert classify._energy_band(None, [], [], ["Jazz"]) == "low"


def test_energy_band_none_only_without_any_genre():
    assert classify._energy_band(None, [], [], []) is None


# --- vibe rules ------------------------------------------------------------
def test_match_vibes_high_energy_electronic_is_workout():
    vibes = classify._match_vibes("high", ["Electronic"], ["energetic"])
    assert "Workout" in vibes


def test_match_vibes_genre_fallback_when_nothing_fires():
    # No mood/energy signal, but a known genre -> genre fallback vibes (not empty)
    vibes = classify._match_vibes(None, ["Hip-hop/Rap"], [])
    assert vibes == config.GENRE_VIBES["Hip-hop/Rap"]
    assert config.DEFAULT_VIBE not in vibes


def test_match_vibes_default_only_without_known_genre():
    assert classify._match_vibes(None, ["NoSuchBucket"], []) == [config.DEFAULT_VIBE]


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


def test_classify_gives_coverage_for_tag_only_rap_track(temp_db):
    """Regression: rap track with genre tags but no mood tags / no features
    must still get an energy band AND a non-empty vibe (the bug we fixed)."""
    db.upsert_tracks([{"id": "r1", "name": "Punchline", "artist_ids": ["a1"],
                       "artist_name": "Rapper", "album": None, "added_at": None,
                       "duration_ms": 1}])
    db.upsert_artists([{"id": "a1", "name": "Rapper", "genres": ["french hip hop", "rap"]}])
    # note: no features rows, tags carry only genre/scene info
    db.upsert_tags([{"track_id": "r1", "tag": "rap", "source": "lastfm", "weight": 99},
                    {"track_id": "r1", "tag": "french", "source": "lastfm", "weight": 80}])
    classify.classify_all()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM labels WHERE track_id='r1'").fetchone()
    assert json.loads(row["genre_buckets"]) == ["Hip-hop/Rap"]
    assert row["energy_band"] is not None          # was NULL before the fix
    vibes = json.loads(row["vibes"])
    assert vibes and config.DEFAULT_VIBE not in vibes  # was [] / Unsorted before


def test_classify_preserves_llm_labels(seeded_db):
    # simulate an LLM-refined track, then re-run the free classify
    db.upsert_labels([{"track_id": "t1", "genre_buckets": json.dumps(["Electronic"]),
                       "energy_band": "mid", "moods": json.dumps(["dreamy"]),
                       "vibes": json.dumps(["Late-night"]), "method": "llm",
                       "classified_at": "now"}])
    classify.classify_all()  # default: must NOT overwrite method='llm'
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM labels WHERE track_id='t1'").fetchone()
    assert row["method"] == "llm"
    assert row["energy_band"] == "mid"
    # overwrite_llm=True reclassifies it back to rules
    classify.classify_all(overwrite_llm=True)
    with db.connect() as conn:
        row = conn.execute("SELECT method FROM labels WHERE track_id='t1'").fetchone()
    assert row["method"] == "rules"
