"""Tests for GUI data loading and config-derived option lists.

Only the non-graphical parts are tested; no Tk window is created (works headless).
"""
import config
import gui


def test_option_lists_derive_from_config():
    assert gui.GENRES == list(config.GENRE_BUCKETS.keys()) + [config.DEFAULT_GENRE]
    assert gui.SUBGENRES == [sg for subs in config.SUBGENRE_BUCKETS.values() for sg in subs]
    assert gui.VIBES == list(config.VIBE_RULES.keys()) + [config.DEFAULT_VIBE]
    assert gui.ENERGIES == [b[2] for b in config.ENERGY_BANDS]


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


# --- per-section selection + include/exclude mode -------------------------
def test_selection_toggle_and_mode():
    s = gui.Selection()
    assert s.mode == gui.INCLUDE
    assert not s.is_on("Rock")
    s.toggle("Rock"); s.toggle("Jazz")
    assert s.is_on("Rock") and s.is_on("Jazz")
    # default INCLUDE mode -> ticked options are includes
    assert s.included() == {"Rock", "Jazz"} and s.excluded() == set()
    # flipping to EXCLUDE reinterprets the same ticks as excludes
    s.flip_mode()
    assert s.mode == gui.EXCLUDE
    assert s.included() == set() and s.excluded() == {"Rock", "Jazz"}
    # untick + clear
    s.toggle("Rock")
    assert s.excluded() == {"Jazz"}
    s.clear()
    assert s.included() == set() and s.excluded() == set()


def _row(genres=(), subgenres=(), vibes=(), energy="mid", artist="A", album="X"):
    return {"genres": list(genres), "subgenres": list(subgenres), "vibes": list(vibes),
            "energy": energy, "artist": artist, "album": album}


def _filters(**kw):
    empty = {k: set() for k in ("g", "sg", "v", "e", "ar", "al")}
    inc = {**empty, **{k[4:]: set(v) for k, v in kw.items() if k.startswith("inc_")}}
    exc = {**empty, **{k[4:]: set(v) for k, v in kw.items() if k.startswith("exc_")}}
    return inc, exc


def _modes(**kw):
    """Build any_modes dict; default all panels to OR (True)."""
    m = {k: True for k in ("g", "sg", "v", "e", "ar", "al")}
    m.update(kw)
    return m


def test_exclude_removes_matching_row():
    inc, exc = _filters(exc_ar=["BannedArtist"])
    assert gui.row_matches(_row(artist="BannedArtist"), inc, exc, _modes()) is False
    assert gui.row_matches(_row(artist="OtherArtist"),  inc, exc, _modes()) is True


def test_exclude_overrides_include():
    # include the genre but exclude the artist -> excluded wins
    inc, exc = _filters(inc_g=["Hip-hop/Rap"], exc_ar=["Z"])
    row = _row(genres=["Hip-hop/Rap"], artist="Z")
    assert gui.row_matches(row, inc, exc, _modes()) is False


def test_within_panel_or_vs_and():
    # Two genres selected; row has only one of them
    inc, exc = _filters(inc_g=["Jazz", "Rock"])
    row = _row(genres=["Jazz"])
    assert gui.row_matches(row, inc, exc, _modes(g=True))  is True   # OR: Jazz matches
    assert gui.row_matches(row, inc, exc, _modes(g=False)) is False  # AND: need Jazz AND Rock


def test_cross_panel_always_and():
    # Genre matches, energy doesn't — cross-panel is always AND → False
    inc, exc = _filters(inc_g=["Jazz"], inc_e=["high"])
    row = _row(genres=["Jazz"], energy="low")
    assert gui.row_matches(row, inc, exc, _modes()) is False


def test_no_filters_keeps_everything():
    inc, exc = _filters()
    assert gui.row_matches(_row(), inc, exc, _modes()) is True


def test_subgenre_include_and_exclude():
    inc, exc = _filters(inc_sg=["Drill"])
    assert gui.row_matches(_row(genres=["Hip-hop/Rap"], subgenres=["Drill"]),
                           inc, exc, _modes()) is True
    assert gui.row_matches(_row(genres=["Hip-hop/Rap"], subgenres=["Trap"]),
                           inc, exc, _modes()) is False
    inc, exc = _filters(exc_sg=["Drill"])
    assert gui.row_matches(_row(subgenres=["Drill"]), inc, exc, _modes()) is False
