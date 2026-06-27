"""Resolve any track identifier to a canonical ISRC — the key everything
downstream joins on.

  ISRC            -> used directly
  Spotify ID      -> metadata fetch, read external_ids.isrc
  artist + title  -> Spotify search, best match's ISRC (with match_confidence)

No ISRC found -> a normalized "key:artist|title" fallback (flagged weak so it
shows in the coverage report). Matching is accent/feat/remix-robust (text_utils)
across American, European and Latin catalogs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

import text_utils

if TYPE_CHECKING:
    from providers import MetadataProvider

WEAK_MATCH_THRESHOLD = 0.62  # below this, a name-search match is flagged weak


@dataclass
class Resolution:
    key: str                       # ISRC when real, else "key:artist|title"
    isrc: str | None
    spotify_id: str | None
    title: str
    artist: str
    album: str | None = None
    release_year: int | None = None
    genre_hints: list[str] = field(default_factory=list)
    method: str = "isrc"           # isrc | spotify_id | search | unresolved
    match_confidence: float | None = None
    weak: bool = False
    notes: str = ""

    @property
    def has_isrc(self) -> bool:
        return text_utils.is_real_isrc(self.isrc)


def _year(date: str | None) -> int | None:
    try:
        return int(str(date)[:4]) if date else None
    except ValueError:
        return None


def _confidence(q_artist: str, q_title: str, cand: dict) -> float:
    """Similarity of a search candidate to the query (title weighted over artist)."""
    n = text_utils.normalize
    qa, qt = n(text_utils.primary_artist(q_artist)), n(text_utils.core_title(q_title))
    ca, ct = n(text_utils.primary_artist(cand.get("artist", ""))), n(text_utils.core_title(cand.get("title", "")))
    a = SequenceMatcher(None, qa, ca).ratio() if qa and ca else 0.0
    t = SequenceMatcher(None, qt, ct).ratio() if qt and ct else 0.0
    return round(0.45 * a + 0.55 * t, 4)


class IdentifierResolver:
    def __init__(self, metadata: "MetadataProvider"):
        self.metadata = metadata

    def resolve(self, *, isrc=None, spotify_id=None, artist=None, title=None) -> Resolution:
        artist, title = artist or "", title or ""

        clean = text_utils.clean_isrc(isrc)
        if clean:
            return Resolution(key=clean, isrc=clean, spotify_id=spotify_id,
                              title=title, artist=artist, method="isrc")

        if spotify_id:
            track = self.metadata.get_track(spotify_id)
            if track:
                return self._from_track(track, method="spotify_id")
            return self._unresolved(artist, title, spotify_id=spotify_id,
                                    notes="spotify_id_not_found")

        if artist or title:
            cand = self.metadata.search_track(artist, title)
            if not cand:
                return self._unresolved(artist, title, notes="no_search_result")
            res = self._from_track(cand, method="search")
            res.match_confidence = _confidence(artist, title, cand)
            res.weak = res.match_confidence < WEAK_MATCH_THRESHOLD or not res.has_isrc
            if res.weak:
                res.notes = (res.notes + " weak_match").strip()
            return res

        raise ValueError("resolve() needs an isrc, spotify_id, or artist+title")

    def _from_track(self, t: dict, *, method: str) -> Resolution:
        isrc = text_utils.clean_isrc(t.get("isrc"))
        artist, title = t.get("artist", ""), t.get("title", "")
        res = Resolution(
            key=isrc or text_utils.fallback_key(artist, title),
            isrc=isrc, spotify_id=t.get("spotify_id"), title=title, artist=artist,
            album=t.get("album"), release_year=_year(t.get("release_date")),
            genre_hints=list(t.get("genres") or []), method=method,
        )
        if not isrc:
            res.weak, res.notes = True, "no_isrc"
        return res

    @staticmethod
    def _unresolved(artist, title, *, spotify_id=None, notes="") -> Resolution:
        return Resolution(
            key=text_utils.fallback_key(artist, title), isrc=None, spotify_id=spotify_id,
            title=title, artist=artist, method="unresolved", weak=True,
            notes=(notes + " no_isrc").strip())
