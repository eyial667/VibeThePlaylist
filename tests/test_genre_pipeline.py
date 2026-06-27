"""End-to-end pipeline over the multi-region fixture (fakes for all providers).

Covers the spec's acceptance points: resolution across input types, lookup vs
extraction vs LLM-only rates, persistence to a test DB, a valid schema row even
when every feature source fails, and resumable batch classification. Prints the
final coverage summary.
"""
import config
import db
import taxonomy as tax
from fakes import (FakeClassifier, FakeFeatureProvider, FakeMetadataProvider,
                   load_fixture)
from genre_pipeline import CoverageStats, GenrePipeline, TrackInput
from providers import SRC_EXTRACTED, SRC_LOOKUP, SRC_NONE

TRACKS = load_fixture()


def _pipeline():
    return GenrePipeline(FakeMetadataProvider(TRACKS),
                         FakeFeatureProvider(TRACKS),
                         FakeClassifier())


def _input(t) -> TrackInput:
    inp = t["input"]
    return TrackInput(isrc=inp.get("isrc"), spotify_id=inp.get("spotify_id"),
                      artist=inp.get("artist"), title=inp.get("title"))


def test_classifies_all_three_input_types_and_persists(temp_db):
    pipe = _pipeline()
    stats = CoverageStats()
    for t in TRACKS:
        pipe.classify_track(_input(t), stats=stats)

    # Every record produced a persisted, schema-valid row.
    rows = db.all_classifications()
    assert len(rows) == sum(1 for _ in TRACKS)  # 28 distinct keys
    taxonomy = tax.load()
    for r in rows:
        assert r["genre"] == tax.OTHER or taxonomy.is_genre(r["genre"])
        assert r["energy"] in config.ENERGY_LEVELS  # never null
        assert r["features_source"] in (SRC_LOOKUP, SRC_EXTRACTED, SRC_NONE)
        assert isinstance(r["vibe"], list)


def test_coverage_matches_fixture_distribution(temp_db, capsys):
    pipe = _pipeline()
    stats = CoverageStats()
    for t in TRACKS:
        pipe.classify_track(_input(t), stats=stats)

    # Fixture has: 13 lookup, 9 extract, 6 none; 27 real ISRCs, 1 fallback key.
    assert stats.total == 28
    assert stats.resolved_isrc == 27
    assert stats.weak_matches >= 1
    assert stats.features_lookup == 13
    assert stats.features_extracted == 9
    assert stats.features_none == 6

    # Final summary is printed for the developer running the suite.
    print("\n=== genre-specification coverage ===")
    for line in stats.summary_lines():
        print("  " + line)
    out = capsys.readouterr().out
    assert "resolved to real ISRC" in out


def test_schema_valid_when_all_feature_sources_fail(temp_db):
    """A track whose name can't be resolved AND has no features still yields a
    complete, valid classification row (degradation path 3)."""
    made_up = next(t for t in TRACKS if t.get("canonical") is None)
    pipe = _pipeline()
    row = pipe.classify_track(_input(made_up))

    assert row["features_source"] == SRC_NONE
    assert row["isrc"].startswith("key:")        # fallback key, no ISRC
    assert row["energy"] in config.ENERGY_LEVELS  # never null
    assert row["energy_raw"] is None and row["tempo"] is None
    assert row["genre"]                           # always assigned
    stored = db.get_classification(row["isrc"])
    assert stored is not None and stored["vibe"] == row["vibe"]


def _seed_library():
    """Seed the tracks table from fixture records that have a Spotify id."""
    rows = []
    for t in TRACKS:
        c = t.get("canonical")
        if not c:
            continue
        rows.append({
            "id": c["spotify_id"], "name": c["title"],
            "artist_ids": c["artist_ids"], "artist_name": c["artist"],
            "album": c["album"], "added_at": None, "duration_ms": 1,
        })
    db.upsert_tracks(rows)
    return len(rows)


def test_batch_is_resumable_and_skips_classified(temp_db):
    n = _seed_library()
    pipe = _pipeline()

    stats1 = pipe.classify_library()
    assert stats1.total == n
    assert pipe.classifier.calls == n
    assert len(db.all_classifications()) == n

    # Second run with nothing new -> skips everything, no further LLM calls.
    calls_before = pipe.classifier.calls
    stats2 = pipe.classify_library()
    assert stats2.total == 0
    assert pipe.classifier.calls == calls_before

    # --reclassify forces a redo.
    stats3 = pipe.classify_library(reclassify=True)
    assert stats3.total == n
    assert pipe.classifier.calls == calls_before + n


def test_batch_writes_isrc_back_to_track_rows(temp_db):
    _seed_library()
    _pipeline().classify_library(limit=3)
    with db.connect() as conn:
        resolved = conn.execute(
            "SELECT COUNT(*) c FROM tracks WHERE isrc IS NOT NULL"
        ).fetchone()["c"]
    assert resolved >= 1  # resolved ISRCs were persisted onto the library rows
