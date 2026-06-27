"""Orchestration for the genre-specification feature.

Ties the pieces together per track and persists one classification row, keyed by
ISRC, always producing a result by degrading gracefully:

  1. ISRC resolved + ReccoBeats lookup hit  -> numeric features + LLM
  2. Lookup miss -> Deezer preview -> ReccoBeats extraction -> features + LLM
  3. No features at all -> LLM from metadata + genre hints only
     (features_source="none", lower confidence)

Which path each track took is logged. Single-track and resumable batch flows
share this code; the batch skips already-classified rows unless `reclassify=True`
and is safe to Ctrl-C (every track is upserted as it finishes).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import config
import db
import providers as P
import text_utils
from genre_classifier import Classifier, HaikuClassifier, energy_from_features
from providers import (FeatureProvider, MetadataProvider, ReccoBeatsFeatureProvider,
                       SpotifyMetadataProvider)
from resolver import IdentifierResolver, Resolution

log = logging.getLogger("genre_pipeline")


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

    def pct(self, n: int) -> float:
        return round(100.0 * n / self.total, 1) if self.total else 0.0

    def summary_lines(self) -> list[str]:
        return [
            f"tracks processed:        {self.total}",
            f"resolved to real ISRC:   {self.resolved_isrc} ({self.pct(self.resolved_isrc)}%)",
            f"weak name-matches:       {self.weak_matches} ({self.pct(self.weak_matches)}%)",
            f"features via lookup:     {self.features_lookup} ({self.pct(self.features_lookup)}%)",
            f"features via extraction: {self.features_extracted} ({self.pct(self.features_extracted)}%)",
            f"LLM-only (no features):  {self.features_none} ({self.pct(self.features_none)}%)",
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

    # --- single track --------------------------------------------------------
    def classify_track(self, track: TrackInput, *, stats: CoverageStats | None = None,
                       persist: bool = True) -> dict:
        """Run the full flow for one track and return the persisted row dict."""
        if stats is not None:
            stats.total += 1

        res = self.resolver.resolve(
            isrc=track.isrc, spotify_id=track.spotify_id,
            artist=track.artist, title=track.title,
        )
        # If resolution found a title/artist but the input had richer ones, keep input.
        title = res.title or track.title or ""
        artist = res.artist or track.artist or ""
        if stats is not None:
            if res.has_isrc:
                stats.resolved_isrc += 1
            if res.weak:
                stats.weak_matches += 1

        # Write resolved identifiers back to the library row (skip resolution next run).
        if persist and track.track_id:
            db.set_track_identifiers(track.track_id, res.isrc, res.spotify_id,
                                     res.match_confidence, res.method)

        feats = self._get_features(res, artist, title)
        source = feats.get("features_source", P.SRC_NONE) if feats else P.SRC_NONE
        if stats is not None:
            if source == P.SRC_LOOKUP:
                stats.features_lookup += 1
            elif source == P.SRC_EXTRACTED:
                stats.features_extracted += 1
            else:
                stats.features_none += 1

        classification = self.classifier.classify({
            "title": title, "artist": artist, "album": res.album or track.album,
            "release_year": res.release_year, "genre_hints": res.genre_hints,
            "features": feats if source != P.SRC_NONE else None,
        })
        # Energy is authoritative from numeric features when available, regardless
        # of which Classifier implementation produced the label; fall back to the
        # model's judgment, and never persist a null energy.
        numeric_energy = energy_from_features(feats if source != P.SRC_NONE else None)
        final_energy = numeric_energy or classification.energy or "mid"
        classification = classification.model_copy(update={"energy": final_energy})

        row = self._build_row(res, artist, title, feats, source, classification)
        row["model_used"] = self.classifier.model_name
        log.info("classified %s | path=%s | %s/%s/%s", res.key, source,
                 row["genre"], row["subgenre"], row["energy"])
        if persist:
            db.upsert_classification(row)
        return row

    # --- batch (resumable) ---------------------------------------------------
    def classify_library(self, *, reclassify: bool = False, limit: int | None = None,
                         progress=None) -> CoverageStats:
        """Classify every library track. Resumable: already-classified rows are
        skipped unless `reclassify`. Safe to interrupt — each row is committed as
        it completes. `progress` is an optional callable(done, total)."""
        rows = db.tracks_for_classification()
        stats = CoverageStats()
        done_keys = set() if reclassify else db.classified_keys()

        # Pre-filter rows whose cached ISRC is already classified (cheap skip).
        pending = []
        for r in rows:
            cached_isrc = text_utils.clean_isrc(r.get("isrc"))
            if not reclassify and cached_isrc and cached_isrc in done_keys:
                continue
            pending.append(r)
        if limit is not None:
            pending = pending[:limit]

        total = len(pending)
        for i, r in enumerate(pending, start=1):
            # tracks(id) is the Spotify track id for every library row.
            ti = TrackInput(
                isrc=text_utils.clean_isrc(r.get("isrc")),
                spotify_id=r["id"],
                artist=r.get("artist_name"), title=r.get("name"),
                album=r.get("album"), track_id=r["id"],
            )
            try:
                # Resolve key first to honor resume on the fallback key too.
                if not reclassify:
                    res = self.resolver.resolve(
                        isrc=ti.isrc, spotify_id=ti.spotify_id,
                        artist=ti.artist, title=ti.title)
                    if res.key in done_keys:
                        if progress:
                            progress(i, total)
                        continue
                row = self.classify_track(ti, stats=stats)
                done_keys.add(row["isrc"])
            except Exception:  # noqa: BLE001 — one bad track must not kill the batch
                stats.errors += 1
                log.exception("failed to classify track id=%s", r.get("id"))
            if progress:
                progress(i, total)
        return stats

    # --- internals -----------------------------------------------------------
    def _get_features(self, res: Resolution, artist: str, title: str) -> dict | None:
        try:
            return self.features.get_features(
                isrc=res.isrc, spotify_id=res.spotify_id,
                artist=artist, title=title)
        except Exception:  # noqa: BLE001 — feature failure must degrade, not crash
            log.exception("feature provider failed for %s", res.key)
            return None

    @staticmethod
    def _build_row(res: Resolution, artist: str, title: str, feats: dict | None,
                   source: str, c) -> dict:
        feats = feats or {}
        notes = " ".join(filter(None, [res.notes, c.notes,
                                       f"suggested={c.suggested_label}" if c.suggested_label else ""]))
        return {
            "isrc": res.key,                      # real ISRC or fallback key
            "spotify_id": res.spotify_id,
            "title": title,
            "artist": artist,
            "genre": c.genre,
            "subgenre": c.subgenre,
            "energy": c.energy,
            "vibe": list(c.vibe),
            "confidence": c.confidence,
            "features_source": source,
            "energy_raw": feats.get("energy"),
            "danceability": feats.get("danceability"),
            "valence": feats.get("valence"),
            "acousticness": feats.get("acousticness"),
            "tempo": feats.get("tempo"),
            "match_confidence": res.match_confidence,
            "classified_at": datetime.now(timezone.utc).isoformat(),
            "model_used": config.CLASSIFIER_MODEL,  # overwritten by caller
            "notes": notes.strip() or None,
        }


# --- convenience entrypoints used by the CLI/GUI ---------------------------
def build_default_pipeline() -> GenrePipeline:
    return GenrePipeline()


def parse_track_arg(value: str) -> tuple[str, str]:
    """Split a CLI '--track \"artist - title\"' into (artist, title).

    Accepts ' - ', ' – ' (en dash) or a plain '-' separator; if none is present
    the whole string is treated as the title."""
    for sep in (" - ", " – ", " — ", " -", "- ", "-"):
        if sep in value:
            artist, _, title = value.partition(sep)
            return artist.strip(), title.strip()
    return "", value.strip()
