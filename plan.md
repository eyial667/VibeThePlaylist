# Genre Specification — Precise Subgenres

## Overview
In addition to the coarse `GENRE_BUCKETS` (e.g. `Hip-hop/Rap`, `Electronic`), the
classifier now also assigns **precise subgenres** (Cloud Rap, Drill, Trap, Reggaeton,
Salsa, House, Techno…) automatically from the same cached signals (artist genres +
Last.fm tags). Subgenres are stored alongside the existing labels and surfaced in the
query CLI, the generated playlists, and the GUI — always falling back to the coarse
genre when no subgenre can be determined, so existing behaviour and playlists are
unaffected.

This matches the app's architecture: a CLI + SQLite pipeline with a read-only Tkinter
GUI, classifying via *pure rules over cached signals*, with an optional Claude
refinement pass.

## How it works
- **Taxonomy** lives in `config.SUBGENRE_BUCKETS`: `coarse bucket -> {subgenre label ->
  [needle substrings]}`, plus `MAX_SUBGENRES`. Tune it like `GENRE_BUCKETS`; only a
  `python cli.py classify` re-run is needed afterwards (no re-fetch/re-enrich).
- **Rules pass (always on)**: `classify._match_subgenres()` mirrors `_match_buckets()` —
  scores subgenre needles against raw genres + tags, but only considers subgenres whose
  parent bucket already matched, so a subgenre never contradicts the coarse genre.
  Capped at `MAX_SUBGENRES`, strongest first. Empty when nothing matches.
- **LLM pass (optional)**: `llm.py` also asks Claude for subgenres constrained to the
  chosen bucket(s); `_sanitize_subgenres()` validates the result. LLM rows
  (`method='llm'`) are preserved across re-classify, so refined subgenres survive.
- **Storage**: new nullable `labels.subgenres` column (JSON list). `db.init()` runs a
  lightweight idempotent migration (`ALTER TABLE … ADD COLUMN`) so pre-existing DBs gain
  the column without a framework.

## Surfaces
- **Query**: `python cli.py query --subgenre Drill`; output shows the precise subgenre,
  falling back to the coarse genre when empty.
- **Playlists**: add `"subgenre"` to `config.PLAYLIST_SCHEMES` to generate precise
  subgenre playlists; tracks without a subgenre fall back to their coarse-genre playlist.
- **GUI**: a Subgenres filter group + a Subgenres column (showing the coarse genre as
  fallback text).

## Backwards compatibility
`subgenres` is nullable and "empty means fall back to coarse genre" at every consumer, so
existing DBs, queries, playlists, and the GUI keep working with no reclassification
beyond a normal `python cli.py classify`.

## Tests
- `tests/test_classify.py`: `_match_subgenres` mapping, parent-bucket constraint, empty
  fallback, cap, and end-to-end label writes; LLM-preservation extended to subgenres.
- `tests/test_db.py`: migration adds `subgenres` to an old `labels` table (idempotent).
- `tests/test_playlists.py`: `subgenre` scheme clustering with coarse-genre fallback.
- `tests/test_gui.py`: option list derivation and subgenre include/exclude filtering.

## Files changed
`config.py`, `db.py`, `classify.py`, `llm.py`, `cli.py`, `playlists.py`, `gui.py`,
and the corresponding tests; docs in `CLAUDE.md`.
