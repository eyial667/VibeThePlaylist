"""Orchestration for the genre-specification feature.

Per track: resolve -> features -> classify -> persist one row keyed by ISRC,
always producing a result by degrading gracefully:
  1. ReccoBeats lookup hit          -> numeric features + LLM
  2. lookup miss -> Deezer/extract  -> features + LLM
  3. no features                    -> LLM from metadata only (lower confidence)
The single-track and resumable batch flows share this; the batch skips
already-classified rows unless reclassify and is Ctrl-C-safe (row-by-row upsert).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import config
import db
from utils import text_utils

from . import providers as P
from .classifier import Classifier, HaikuClassifier, energy_from_features
from .providers import (FeatureProvider, MetadataProvider,
                        ReccoBeatsFeatureProvider, SpotifyMetadataProvider)
from .resolver import IdentifierResolver, Resolution

log = logging.getLogger("genreclass")


@dataclass
class TrackInput:
    """One classification request (any subset of identifiers)."""
    isrc: str | None = None
    spotify_id: str | None = None
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    track_id: str | None = None  # library tracks(id), for ISRC write-back


@dataclass
class CoverageStats:
    """Running totals for the end-of-run summary."""
    total: int = 0
    resolved_isrc: int = 0
    weak_matches: int = 0
    features_lookup: int = 0
    features_extracted: int = 0
    features_none: int = 0
    errors: int = 0

    def record(self, res: "Resolution", source: str) -> None:
        self.total += 1
        self.resolved_isrc += res.has_isrc
        self.weak_matches += res.weak
        self.features_lookup += source == P.SRC_LOOKUP
        self.features_extracted += source == P.SRC_EXTRACTED
        self.features_none += source == P.SRC_NONE

    def _pct(self, n: int) -> str:
        return f"{round(100.0 * n / self.total, 1) if self.total else 0.0}%"

    def summary_lines(self) -> list[str]:
        return [
            f"tracks processed:        {self.total}",
            f"resolved to real ISRC:   {self.resolved_isrc} ({self._pct(self.resolved_isrc)})",
            f"weak name-matches:       {self.weak_matches} ({self._pct(self.weak_matches)})",
            f"features via lookup:     {self.features_lookup} ({self._pct(self.features_lookup)})",
            f"features via extraction: {self.features_extracted} ({self._pct(self.features_extracted)})",
            f"LLM-only (no features):  {self.features_none} ({self._pct(self.features_none)})",
            f"errors:                  {self.errors}",
        ]


class GenrePipeline:
    """Resolve -> features -> classify -> persist, with all deps injectable."""

    def __init__(self, metadata: MetadataProvider | None = None,
                 features: FeatureProvider | None = None,
                 classifier: Classifier | None = None):
        self.metadata = metadata or SpotifyMetadataProvider()
        self.features = features or ReccoBeatsFeatureProvider()
        self.classifier = classifier or HaikuClassifier()
        self.resolver = IdentifierResolver(self.metadata)

    def classify_track(self, track: TrackInput, *, stats: CoverageStats | None = None,
                       persist: bool = True, resolution: Resolution | None = None) -> dict:
        """Run the full flow for one track and return the persisted row dict.
        `resolution` lets the batch reuse the Resolution from its resume check."""
        res = resolution or self.resolver.resolve(
            isrc=track.isrc, spotify_id=track.spotify_id,
            artist=track.artist, title=track.title)
        title = res.title or track.title or ""
        artist = res.artist or track.artist or ""

        # Persist resolved identifiers onto the library row (skip resolving next run).
        if persist and track.track_id:
            db.set_track_identifiers(track.track_id, res.isrc, res.spotify_id,
                                     res.match_confidence, res.method)

        feats = self._get_features(res, artist, title)
        source = feats.get("features_source", P.SRC_NONE) if feats else P.SRC_NONE
        if stats is not None:
            stats.record(res, source)

        c = self.classifier.classify({
            "title": title, "artist": artist, "album": res.album or track.album,
            "release_year": res.release_year, "genre_hints": res.genre_hints,
            "features": feats if source != P.SRC_NONE else None,
        })
        # Energy is authoritative from numeric features regardless of classifier.
        energy = energy_from_features(feats if source != P.SRC_NONE else None) \
            or c.energy or "mid"
        c = c.model_copy(update={"energy": energy})

        row = self._build_row(res, artist, title, feats, source, c)
        row["model_used"] = self.classifier.model_name
        log.info("classified %s | path=%s | %s/%s/%s", res.key, source,
                 row["genre"], row["subgenre"], row["energy"])
        if persist:
            db.upsert_classification(row)
        return row

    def classify_library(self, *, reclassify: bool = False, limit: int | None = None,
                         progress=None) -> CoverageStats:
        """Classify every library track. Resumable (skips classified rows unless
        reclassify); safe to interrupt. `progress` is callable(done, total)."""
        stats = CoverageStats()
        done = set() if reclassify else db.classified_keys()

        # Cheap skip: drop rows whose already-cached ISRC is classified.
        pending = [r for r in db.tracks_for_classification()
                   if reclassify or text_utils.clean_isrc(r.get("isrc")) not in done]
        if limit is not None:
            pending = pending[:limit]

        total = len(pending)
        for i, r in enumerate(pending, start=1):
            ti = TrackInput(  # tracks(id) is the Spotify track id for library rows
                isrc=text_utils.clean_isrc(r.get("isrc")), spotify_id=r["id"],
                artist=r.get("artist_name"), title=r.get("name"),
                album=r.get("album"), track_id=r["id"])
            try:
                # Resolve once so resume can skip on the fallback key too; the
                # Resolution is reused by classify_track (no second resolve).
                res = None
                if not reclassify:
                    res = self.resolver.resolve(isrc=ti.isrc, spotify_id=ti.spotify_id,
                                                artist=ti.artist, title=ti.title)
                    if res.key in done:
                        continue
                done.add(self.classify_track(ti, stats=stats, resolution=res)["isrc"])
            except Exception:  # noqa: BLE001 — one bad track must not kill the batch
                stats.errors += 1
                log.exception("failed to classify track id=%s", r.get("id"))
            finally:
                if progress:
                    progress(i, total)
        return stats

    # --- internals -----------------------------------------------------------
    def _get_features(self, res: Resolution, artist: str, title: str) -> dict | None:
        try:
            return self.features.get_features(isrc=res.isrc, spotify_id=res.spotify_id,
                                              artist=artist, title=title)
        except Exception:  # noqa: BLE001 — feature failure must degrade, not crash
            log.exception("feature provider failed for %s", res.key)
            return None

    @staticmethod
    def _build_row(res: Resolution, artist: str, title: str, feats: dict | None,
                   source: str, c) -> dict:
        feats = feats or {}
        notes = " ".join(filter(None, [
            res.notes, c.notes,
            f"suggested={c.suggested_label}" if c.suggested_label else ""]))
        return {
            "isrc": res.key, "spotify_id": res.spotify_id, "title": title,
            "artist": artist, "genre": c.genre, "subgenre": c.subgenre,
            "energy": c.energy, "vibe": list(c.vibe), "confidence": c.confidence,
            "features_source": source, "energy_raw": feats.get("energy"),
            "danceability": feats.get("danceability"), "valence": feats.get("valence"),
            "acousticness": feats.get("acousticness"), "tempo": feats.get("tempo"),
            "match_confidence": res.match_confidence,
            "classified_at": datetime.now(timezone.utc).isoformat(),
            "model_used": config.CLASSIFIER_MODEL,  # overwritten by caller
            "notes": notes.strip() or None,
        }


# --- convenience entrypoints used by the CLI/GUI ---------------------------
def build_default_pipeline() -> GenrePipeline:
    return GenrePipeline()


def parse_track_arg(value: str) -> tuple[str, str]:
    """Split '--track "artist - title"' into (artist, title); no separator -> title."""
    for sep in (" - ", " – ", " — ", " -", "- ", "-"):
        if sep in value:
            artist, _, title = value.partition(sep)
            return artist.strip(), title.strip()
    return "", value.strip()


def format_result_lines(row: dict) -> list[str]:
    """Human-readable summary of a classification row, shared by the CLI and GUI."""
    isrc = row["isrc"] + ("  (no ISRC — fallback key)"
                          if text_utils.is_fallback_key(row["isrc"]) else "")
    feats = row["features_source"]
    if feats != P.SRC_NONE:
        feats += f"  (energy={row['energy_raw']}, tempo={row['tempo']})"
    conf = f"{row['confidence']}"
    if row["match_confidence"] is not None:
        conf += f"   match_confidence: {row['match_confidence']}"
    lines = [
        f"{row['artist']} — {row['title']}",
        f"ISRC:       {isrc}",
        f"Genre:      {row['genre']} / {row['subgenre'] or '—'}",
        f"Energy:     {row['energy']}",
        f"Vibe:       {', '.join(row['vibe']) or '—'}",
        f"Features:   {feats}",
        f"Confidence: {conf}",
    ]
    if row.get("notes"):
        lines.append(f"Notes:      {row['notes']}")
    return lines
