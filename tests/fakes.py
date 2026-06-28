"""Shared in-memory fakes for the genre-specification tests.

No network, no credentials: a FakeMetadataProvider / FakeFeatureProvider /
FakeClassifier driven by the multi-region fixture, plus a programmable
FakeSession for exercising the real ReccoBeats/Deezer HTTP clients offline.
"""
from __future__ import annotations

import json
import pathlib

from utils import text_utils
from genreclass.classifier import Classification, Classifier
from genreclass.providers import (SRC_EXTRACTED, SRC_LOOKUP, FeatureProvider,
                       MetadataProvider)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "tracks_multiregion.json"


def load_fixture() -> list[dict]:
    return json.load(open(FIXTURE, encoding="utf-8"))["tracks"]


def _score(qa: str, qt: str, ca: str, ct: str) -> float:
    from difflib import SequenceMatcher
    a = SequenceMatcher(None, qa, ca).ratio() if qa and ca else 0.0
    t = SequenceMatcher(None, qt, ct).ratio() if qt and ct else 0.0
    return 0.45 * a + 0.55 * t


class FakeMetadataProvider(MetadataProvider):
    """Resolves against the fixture's canonical entries."""

    def __init__(self, tracks: list[dict]):
        self.by_spotify = {}
        self.canonicals = []
        for t in tracks:
            c = t.get("canonical")
            if c:
                self.by_spotify[c["spotify_id"]] = c
                self.canonicals.append(c)
        self.search_calls: list[tuple[str, str]] = []

    def get_track(self, spotify_id: str) -> dict | None:
        c = self.by_spotify.get(spotify_id)
        return dict(c) if c else None

    def search_track(self, artist: str, title: str) -> dict | None:
        self.search_calls.append((artist, title))
        qa = text_utils.normalize(text_utils.primary_artist(artist))
        qt = text_utils.normalize(text_utils.core_title(title))
        best, best_score = None, 0.0
        for c in self.canonicals:
            ca = text_utils.normalize(text_utils.primary_artist(c["artist"]))
            ct = text_utils.normalize(text_utils.core_title(c["title"]))
            s = _score(qa, qt, ca, ct)
            if s > best_score:
                best, best_score = c, s
        # Floor so a totally-unknown query returns nothing (forces fallback key).
        return dict(best) if best and best_score >= 0.55 else None

    def artist_genres(self, artist_ids):
        return []


# Realistic numeric features per energy level for the fixture's feature_mode.
_FEATS = {
    SRC_LOOKUP: {"energy": 0.78, "danceability": 0.7, "valence": 0.6,
                 "acousticness": 0.08, "tempo": 122.0},
    SRC_EXTRACTED: {"energy": 0.33, "danceability": 0.45, "valence": 0.3,
                    "acousticness": 0.74, "tempo": 92.0},
}


class FakeFeatureProvider(FeatureProvider):
    """Returns features per the fixture's feature_mode, keyed by ISRC.

    Tracks the (isrc) -> source it produced so tests can assert lookup vs
    extraction vs none rates and that the fallback 'fires' on lookup misses.
    """

    def __init__(self, tracks: list[dict]):
        self.mode_by_isrc = {}
        for t in tracks:
            c = t.get("canonical")
            if c:
                self.mode_by_isrc[c["isrc"]] = t["feature_mode"]
        self.calls: list[str] = []

    def get_features(self, *, isrc, spotify_id, artist="", title=""):
        self.calls.append(isrc or text_utils.fallback_key(artist, title))
        mode = self.mode_by_isrc.get(isrc, "none")
        if mode == "lookup":
            return {**_FEATS[SRC_LOOKUP], "features_source": SRC_LOOKUP}
        if mode == "extract":
            return {**_FEATS[SRC_EXTRACTED], "features_source": SRC_EXTRACTED}
        return None


_GENRE_MAP = [
    (("reggaeton", "salsa", "bolero", "bachata", "mpb", "bossa", "latin", "urbano"), "Latin"),
    (("metal",), "Metal"),
    (("jazz", "bebop"), "Jazz"),
    (("house", "electronic", "edm", "krautrock", "techno"), "Electronic"),
    (("rock", "post-rock"), "Rock"),
    (("folk",), "Folk/Acoustic"),
    (("r&b", "soul"), "R&B/Soul"),
    (("rap", "hip hop"), "Hip-Hop/Rap"),
    (("ambient", "dark folk", "nordic"), "Ambient/Lo-fi"),
]


class FakeClassifier(Classifier):
    """Deterministic genre from hints; energy left for the pipeline to derive."""

    def __init__(self, model: str = "fake-haiku-4-5"):
        self._model = model
        self.calls = 0

    @property
    def model_name(self) -> str:
        return self._model

    def classify(self, track: dict) -> Classification:
        self.calls += 1
        hints = " ".join(track.get("genre_hints") or []).lower()
        genre = "Pop"
        for needles, g in _GENRE_MAP:
            if any(n in hints for n in needles):
                genre = g
                break
        return Classification(genre=genre, subgenre=None, energy=None,
                              vibe=["energetic"], confidence=0.7)


# --- HTTP-level fakes for the real ReccoBeats/Deezer clients ----------------
class FakeResponse:
    def __init__(self, *, json_data=None, content=b"", status=200):
        self._json = json_data
        self.content = content
        self.status_code = status

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err


class FakeSession:
    """Routes GET/POST to a user-supplied handler(method, url, **kwargs).

    Records every call so tests can assert the fallback path was taken and that
    caching prevents duplicate calls.
    """

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[str, str]] = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        return self.handler("GET", url, **kwargs)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url))
        return self.handler("POST", url, **kwargs)
