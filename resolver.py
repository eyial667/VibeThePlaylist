"""Identifier resolution -> canonical ISRC (the join key for everything else).

Track rows reach us with wildly different identifiers — some have an ISRC, some
only a Spotify track ID, some only artist + title typed at the CLI. Downstream
(features lookup, classification cache, persistence) joins exclusively on ISRC,
never on raw artist+title, so every input is funnelled here first:

    has ISRC                -> use it (method="isrc", confidence None)
    has Spotify track ID    -> fetch track, read external_ids.isrc (method="spotify_id")
    artist + title only     -> Spotify search, best match's ISRC (method="search",
                               with a match_confidence; weak matches flagged)

If no ISRC can be found we fall back to a normalized "key:artist|title" key
(see text_utils.fallback_key) and mark the record so it surfaces in the coverage
report. The resolved ISRC + spotify_id are written back to the library row by the
caller so future runs skip resolution.

Matching is accent/feature/remix-robust via text_utils, because the catalog spans
American, European and Latin music with no dominant region.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

import text_utils

if TYPE_CHECKING:  # avoid a hard import cycle; providers imports nothing from us
    from providers import MetadataProvider

# Below this combined artist+title similarity a search match is "weak" and flagged.
WEAK_MATCH_THRESHOLD = 0.62


@dataclass
class Resolution:
    """Outcome of resolving one track to its canonical key."""
    key: str                       # ISRC when real, else "key:artist|title"
    isrc: str | None               # real ISRC or None
    spotify_id: str | None
    title: str
    artist: str
    album: str | None = None
    release_year: int | None = None
    genre_hints: list[str] = field(default_factory=list)
    method: str = "isrc"           # isrc | spotify_id | search | unresolved
    match_confidence: float | None = None  # only set for search matches
    weak: bool = False             # weak name-match or no ISRC at all
    notes: str = ""

    @property
    def has_isrc(self) -> bool:
        return text_utils.is_real_isrc(self.isrc)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _match_confidence(query_artist: str, query_title: str, cand: dict) -> float:
    """Score a search candidate against the query on normalized artist + core
    title (accent/feature/remix folded). Title weighted a touch over artist."""
    qa = text_utils.normalize(text_utils.primary_artist(query_artist))
    qt = text_utils.normalize(text_utils.core_title(query_title))
    ca = text_utils.normalize(text_utils.primary_artist(cand.get("artist", "")))
    ct = text_utils.normalize(text_utils.core_title(cand.get("title", "")))
    artist_sim = _similarity(qa, ca) if qa and ca else 0.0
    title_sim = _similarity(qt, ct) if qt and ct else 0.0
    return round(0.45 * artist_sim + 0.55 * title_sim, 4)


def _year(date: str | None) -> int | None:
    if not date:
        return None
    try:
        return int(str(date)[:4])
    except ValueError:
        return None


class IdentifierResolver:
    """Resolves any track input to a `Resolution`, using a MetadataProvider."""

    def __init__(self, metadata: "MetadataProvider"):
        self.metadata = metadata

    # --- public entrypoints --------------------------------------------------
    def from_isrc(self, isrc: str, *, artist: str = "", title: str = "") -> Resolution:
        clean = text_utils.clean_isrc(isrc)
        if clean:
            return Resolution(key=clean, isrc=clean, spotify_id=None,
                              title=title, artist=artist, method="isrc")
        # Caller handed us a non-ISRC string; degrade to a fallback key.
        return self._unresolved(artist, title, notes="invalid_isrc")

    def from_spotify_id(self, spotify_id: str) -> Resolution:
        track = self.metadata.get_track(spotify_id)
        if not track:
            return self._unresolved("", "", spotify_id=spotify_id,
                                    notes="spotify_id_not_found")
        return self._from_track(track, method="spotify_id")

    def from_name(self, artist: str, title: str) -> Resolution:
        cand = self.metadata.search_track(artist, title)
        if not cand:
            return self._unresolved(artist, title, notes="no_search_result")
        conf = _match_confidence(artist, title, cand)
        res = self._from_track(cand, method="search")
        res.match_confidence = conf
        res.weak = conf < WEAK_MATCH_THRESHOLD or not res.has_isrc
        # Keep the user's typed artist/title when the match is weak (more honest
        # in reports), otherwise prefer the canonical metadata.
        if res.weak and not res.title:
            res.title, res.artist = title, artist
        if res.weak:
            res.notes = (res.notes + " weak_match").strip()
        return res

    def resolve(self, *, isrc: str | None = None, spotify_id: str | None = None,
                artist: str | None = None, title: str | None = None) -> Resolution:
        """Dispatch on whichever identifier is present, best-first."""
        if isrc and text_utils.clean_isrc(isrc):
            r = self.from_isrc(isrc, artist=artist or "", title=title or "")
            # enrich with metadata if we also have a spotify id
            if spotify_id and not r.spotify_id:
                r.spotify_id = spotify_id
            return r
        if spotify_id:
            return self.from_spotify_id(spotify_id)
        if artist or title:
            return self.from_name(artist or "", title or "")
        raise ValueError("resolve() needs at least one of isrc / spotify_id / artist+title")

    # --- internals -----------------------------------------------------------
    def _from_track(self, track: dict, *, method: str) -> Resolution:
        isrc = text_utils.clean_isrc(track.get("isrc"))
        artist = track.get("artist", "")
        title = track.get("title", "")
        key = isrc or text_utils.fallback_key(artist, title)
        res = Resolution(
            key=key,
            isrc=isrc,
            spotify_id=track.get("spotify_id"),
            title=title,
            artist=artist,
            album=track.get("album"),
            release_year=_year(track.get("release_date")),
            genre_hints=list(track.get("genres") or []),
            method=method,
        )
        if not isrc:
            res.weak = True
            res.notes = "no_isrc"
        return res

    def _unresolved(self, artist: str, title: str, *, spotify_id: str | None = None,
                    notes: str = "") -> Resolution:
        return Resolution(
            key=text_utils.fallback_key(artist, title),
            isrc=None,
            spotify_id=spotify_id,
            title=title,
            artist=artist,
            method="unresolved",
            weak=True,
            notes=(notes + " no_isrc").strip(),
        )
