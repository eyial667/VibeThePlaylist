"""Tests for subgenre generation: config merge, overlay I/O, response parsing.

The Claude + web-search call itself is mocked — these tests never hit the network.
"""
import config
from pipeline import subgenre_gen


# --- config merge ----------------------------------------------------------
def test_merge_hand_curated_wins():
    base = {"Electronic": {"House": ["house"]}}
    overlay = {"Electronic": {"House": ["GENERATED"], "Techno": ["techno"]}}
    merged = config._merge_subgenres(base, overlay)
    assert merged["Electronic"]["House"] == ["house"]   # base needles preserved
    assert merged["Electronic"]["Techno"] == ["techno"]  # overlay adds new label


def test_merge_adds_overlay_only_bucket():
    base = {"Jazz": {"Bebop": ["bebop"]}}
    overlay = {"Latin": {"Salsa": ["salsa"]}}
    merged = config._merge_subgenres(base, overlay)
    assert merged["Latin"] == {"Salsa": ["salsa"]}
    assert merged["Jazz"] == {"Bebop": ["bebop"]}


def test_merge_handles_empty_overlay():
    base = {"Pop": {"Synthpop": ["synthpop"]}}
    assert config._merge_subgenres(base, {}) == base
    assert config._merge_subgenres(base, None) == base


def test_merged_config_exposes_generated_subgenres():
    # the seeded overlay should surface extra subgenres under existing buckets
    assert "Trap" in config.SUBGENRE_BUCKETS["Hip-hop/Rap"]
    assert "Reggaeton" in config.SUBGENRE_BUCKETS["Latin"]


# --- response parsing ------------------------------------------------------
def test_extract_json_plain():
    assert subgenre_gen._extract_json('{"Drill": ["drill"]}') == {"Drill": ["drill"]}


def test_extract_json_strips_fences_and_prose():
    fenced = '```json\n{"Deep House": ["deep house"]}\n```'
    assert subgenre_gen._extract_json(fenced) == {"Deep House": ["deep house"]}
    assert subgenre_gen._extract_json('result: {"Trap": "trap"} ok') == {"Trap": ["trap"]}


def test_extract_json_coerces_and_drops_junk():
    raw = '{"A": "low", "B": ["X", ""], "C": [], "D": 5}'
    out = subgenre_gen._extract_json(raw)
    assert out == {"A": ["low"], "B": ["x"]}  # lowercased; empty/non-list dropped


def test_extract_json_garbage_returns_empty():
    assert subgenre_gen._extract_json("no json here") == {}


# --- overlay file I/O ------------------------------------------------------
def test_write_then_load_roundtrip(tmp_path):
    path = tmp_path / "gen.py"
    data = {"Pop": {"Synthpop": ["synthpop"], "K-pop": ["k-pop"]}}
    subgenre_gen.write_generated(data, path)
    assert subgenre_gen.load_generated(path) == data


def test_load_missing_file_is_empty(tmp_path):
    assert subgenre_gen.load_generated(tmp_path / "absent.py") == {}


# --- regenerate orchestration (LLM mocked) ---------------------------------
def test_regenerate_writes_overlay(tmp_path, monkeypatch):
    path = tmp_path / "gen.py"
    monkeypatch.setattr(subgenre_gen, "generate_subgenres",
                        lambda g, max_subgenres=25: {f"{g} Sub": [g.lower()]})
    counts = subgenre_gen.regenerate(["Pop", "Rock"], path=path)
    assert counts == {"Pop": 1, "Rock": 1}
    written = subgenre_gen.load_generated(path)
    assert written["Pop"] == {"Pop Sub": ["pop"]}
    assert written["Rock"] == {"Rock Sub": ["rock"]}


def test_regenerate_preserves_existing_overlay(tmp_path, monkeypatch):
    path = tmp_path / "gen.py"
    subgenre_gen.write_generated({"Jazz": {"Bebop": ["bebop"]}}, path)
    monkeypatch.setattr(subgenre_gen, "generate_subgenres",
                        lambda g, max_subgenres=25: {"New": ["new"]})
    subgenre_gen.regenerate(["Pop"], path=path)
    merged = subgenre_gen.load_generated(path)
    assert merged["Jazz"] == {"Bebop": ["bebop"]}   # untouched
    assert merged["Pop"] == {"New": ["new"]}         # added
