"""Claude Haiku classifier: metadata + features -> genre/subgenre/energy/vibe.

Behind the `Classifier` interface so another model can be swapped in. Output is
constrained to the controlled taxonomy (taxonomy.json) — the model picks allowed
values or returns genre="other" with a free-text suggested_label. Strict JSON via
a prefilled assistant turn, low temperature, fence-stripping + one retry on bad
JSON, pydantic validation, then taxonomy coercion. Energy is taken from numeric
features when present, else the model's judgment.
"""
from __future__ import annotations

import abc
import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

import config
import text_utils

from . import taxonomy as tax

TEMPERATURE = 0.2


class Classification(BaseModel):
    """Validated classifier output (pre-taxonomy-coercion)."""
    genre: str = tax.OTHER
    subgenre: str | None = None
    energy: str | None = None
    vibe: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    suggested_label: str | None = None
    notes: str | None = None

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.5

    @field_validator("vibe", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> list:
        if v is None:
            return []
        return [v] if isinstance(v, str) else list(v)


class Classifier(abc.ABC):
    @abc.abstractmethod
    def classify(self, track: dict) -> Classification:
        ...

    @property
    def model_name(self) -> str:
        return "unknown"


def energy_from_features(features: dict | None) -> str | None:
    """low/mid/high from numeric energy (falling back to danceability), using the
    same bands as the rules engine. None when no numeric signal is available."""
    if not features:
        return None
    value = features.get("energy")
    if value is None:
        value = features.get("danceability")
    if value is None:
        return None
    return next((name for lo, hi, name in config.ENERGY_BANDS if lo <= value < hi), None)


def coerce_to_taxonomy(c: Classification, taxonomy: tax.Taxonomy) -> Classification:
    """Force model output into the controlled vocabulary: unknown genre -> 'other'
    (named genre kept as suggested_label), subgenre dropped if not under the
    genre, vibes filtered to known tokens, invalid energy dropped."""
    genre = c.genre if taxonomy.is_genre(c.genre) else tax.OTHER
    suggested = c.suggested_label
    if genre == tax.OTHER and not suggested and c.genre and c.genre != tax.OTHER:
        suggested = c.genre  # model named a genre we don't carry
    return c.model_copy(update={
        "genre": genre,
        "subgenre": c.subgenre if taxonomy.is_subgenre(genre, c.subgenre) else None,
        "energy": c.energy if taxonomy.is_energy(c.energy) else None,
        "vibe": taxonomy.coerce_vibes(c.vibe),
        "suggested_label": suggested,
    })


def parse_json_object(text: str, *, prefill: str = "") -> dict:
    """Parse a JSON object from model text, tolerating fences and a prefilled
    opening brace. Raises json.JSONDecodeError on failure."""
    return json.loads(text_utils.strip_code_fences(prefill + text))


_SYSTEM = (
    "You are a precise music classification engine for an internationally diverse "
    "catalog (American, European and Latin music — assume no dominant region or "
    "language). You assign a single best genre and subgenre, an energy level, and "
    "one or more vibe tags, choosing ONLY from the allowed vocabulary you are "
    "given. If nothing fits, return genre \"other\" with a short free-text "
    "suggested_label. Always answer for the track, even when metadata is sparse. "
    "Respond with a single JSON object and nothing else."
)


def _prompt(track: dict, taxonomy: tax.Taxonomy) -> str:
    genre_lines = "\n".join(f"  - {g}: {', '.join(subs)}"
                            for g, subs in taxonomy.genres.items())
    feats = track.get("features") or {}
    feat_str = ", ".join(f"{k}={feats[k]}" for k in (
        "energy", "danceability", "valence", "acousticness", "tempo")
        if feats.get(k) is not None) or "none (unavailable)"
    return (
        "Classify this track.\n\n"
        f"Title: {track.get('title', '')}\n"
        f"Artist(s): {track.get('artist', '')}\n"
        f"Album: {track.get('album') or 'unknown'}\n"
        f"Release year: {track.get('release_year') or 'unknown'}\n"
        f"Spotify artist genre hints (noisy, optional): "
        f"{', '.join(track.get('genre_hints') or []) or 'none'}\n"
        f"Numeric audio features: {feat_str}\n\n"
        "Allowed genres and their subgenres:\n"
        f"{genre_lines}\n\n"
        f"Allowed energy levels: {', '.join(taxonomy.energy)}\n"
        f"Allowed vibe tags (pick 1-3): {', '.join(taxonomy.vibe)}\n\n"
        "Rules:\n"
        "- genre MUST be one of the allowed genres, or exactly \"other\".\n"
        "- subgenre MUST be one of the chosen genre's subgenres, or null.\n"
        "- If numeric features are given, let them inform energy.\n"
        "- Use \"other\" + suggested_label only when no allowed genre fits.\n\n"
        "Return ONLY a JSON object with keys: genre (string), subgenre "
        "(string|null), energy (string), vibe (array), confidence (0..1 float), "
        "suggested_label (string|null), notes (short string|null)."
    )


class HaikuClassifier(Classifier):
    """Claude Haiku classifier (model id from config.CLASSIFIER_MODEL)."""

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 taxonomy: tax.Taxonomy | None = None, client=None):
        self.model = model or config.CLASSIFIER_MODEL
        self.api_key = api_key if api_key is not None else config.ANTHROPIC_API_KEY
        self.taxonomy = taxonomy or tax.load()
        self._client = client

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def _call(self, prompt: str) -> str:
        """One Claude call with a prefilled '{' assistant turn for strict JSON."""
        msg = self.client.messages.create(
            model=self.model, max_tokens=400, temperature=TEMPERATURE,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt},
                      {"role": "assistant", "content": "{"}],
        )
        return msg.content[0].text

    def _ask(self, prompt: str) -> dict | None:
        """Call Claude and parse JSON, retrying once. None on total failure."""
        for attempt in range(2):
            retry = "" if attempt == 0 else ("\n\nYour previous reply was not valid "
                                             "JSON. Reply with ONLY the JSON object.")
            try:
                return parse_json_object(self._call(prompt + retry), prefill="{")
            except (json.JSONDecodeError, IndexError, KeyError):
                continue
        return None

    def classify(self, track: dict) -> Classification:
        data = self._ask(_prompt(track, self.taxonomy))
        if data is None:
            result = Classification(genre=tax.OTHER, confidence=0.1,
                                    notes="unparseable_model_output")
        else:
            try:
                result = Classification.model_validate(data)
            except ValidationError:
                result = Classification(genre=tax.OTHER, confidence=0.1,
                                        notes="schema_validation_failed")
        result = coerce_to_taxonomy(result, self.taxonomy)
        # Numeric features win over the model's guess; energy is never null.
        energy = energy_from_features(track.get("features")) or result.energy or "mid"
        return result.model_copy(update={"energy": energy})
