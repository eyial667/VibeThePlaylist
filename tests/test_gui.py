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


# --- tri-state include/exclude (NOT) -------------------------------------
def test_tristate_cycles_off_include_exclude():
    t = gui.TriState()
    assert t.get("Rock") is None
    t.cycle("Rock"); assert t.get("Rock") == gui.INCLUDE
    t.cycle("Rock"); assert t.get("Rock") == gui.EXCLUDE
    t.cycle("Rock"); assert t.get("Rock") is None
    t.cycle("Rock"); t.cycle("Jazz")  # include Rock(again via 1), include Jazz
    t.cycle("Jazz")                    # Jazz -> exclude
    assert t.included() == {"Rock"} and t.excluded() == {"Jazz"}


def _row(genres=(), vibes=(), energy="mid", moods=(), artist="A", album="X"):
    return {"genres": list(genres), "vibes": list(vibes), "energy": energy,
            "moods": list(moods), "artist": artist, "album": album}


def _filters(**kw):
    empty = {k: set() for k in ("g", "v", "e", "m", "ar", "al")}
    inc = {**empty, **{k[4:]: set(v) for k, v in kw.items() if k.startswith("inc_")}}
    exc = {**empty, **{k[4:]: set(v) for k, v in kw.items() if k.startswith("exc_")}}
    return inc, exc


def test_exclude_removes_matching_row():
    inc, exc = _filters(exc_ar=["BannedArtist"])
    assert gui.row_matches(_row(artist="BannedArtist"), inc, exc, any_mode=True) is False
    assert gui.row_matches(_row(artist="OtherArtist"), inc, exc, any_mode=True) is True


def test_exclude_overrides_include():
    # include the genre but exclude the artist -> excluded wins
    inc, exc = _filters(inc_g=["Hip-hop/Rap"], exc_ar=["Z"])
    row = _row(genres=["Hip-hop/Rap"], artist="Z")
    assert gui.row_matches(row, inc, exc, any_mode=True) is False


def test_include_any_vs_all():
    inc, exc = _filters(inc_g=["Jazz"], inc_e=["high"])
    row = _row(genres=["Jazz"], energy="low")  # matches genre, not energy
    assert gui.row_matches(row, inc, exc, any_mode=True) is True    # Any
    assert gui.row_matches(row, inc, exc, any_mode=False) is False  # All


def test_no_filters_keeps_everything():
    inc, exc = _filters()
    assert gui.row_matches(_row(), inc, exc, any_mode=True) is True
