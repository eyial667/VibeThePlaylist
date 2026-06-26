"""Tests for GUI data loading and config-derived option lists.

Only the non-graphical parts are tested; no Tk window is created (works headless).
"""
import config
import gui


def test_option_lists_derive_from_config():
    assert gui.GENRES == list(config.GENRE_BUCKETS.keys()) + [config.DEFAULT_GENRE]
    assert gui.VIBES == list(config.VIBE_RULES.keys()) + [config.DEFAULT_VIBE]
    assert gui.ENERGIES == [b[2] for b in config.ENERGY_BANDS]
    assert gui.MOODS == list(config.MOOD_TAGS.keys())


def test_load_rows_returns_track_ids_and_labels(seeded_db):
    import classify
    classify.classify_all()
    rows = gui.load_rows()
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    assert "t1" in by_id and "t2" in by_id
    assert by_id["t1"]["genres"] == ["Electronic"]
    assert "Workout" in by_id["t1"]["vibes"]
    assert by_id["t1"]["album"] == "A"  # album loaded for the artist/album filters


def test_load_rows_empty_when_no_labels(temp_db):
    assert gui.load_rows() == []
