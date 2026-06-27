"""Genre-specification feature: classify a track into genre / subgenre / energy /
vibe, keyed by ISRC, and persist it.

Layout:
  resolver.py    any identifier -> canonical ISRC (the join key)
  providers.py   MetadataProvider (Spotify) + FeatureProvider (ReccoBeats/Deezer)
  classifier.py  Claude Haiku classifier, constrained to taxonomy.json
  pipeline.py    resolve -> features -> classify -> persist (single + batch)
  taxonomy.py    loader for taxonomy.json (the editable controlled vocabulary)

Shared infrastructure (config, db, text_utils, spotify_client) lives at the repo
root and is reused by the original pipeline too. The CLI/GUI import this package
as the single entry point.
"""
from __future__ import annotations

from .pipeline import (CoverageStats, GenrePipeline, TrackInput,
                       build_default_pipeline, format_result_lines, parse_track_arg)

__all__ = [
    "GenrePipeline", "TrackInput", "CoverageStats",
    "build_default_pipeline", "parse_track_arg", "format_result_lines",
]
