"""Loader for the controlled classification taxonomy (taxonomy.json).

The taxonomy is the single editable source of allowed genre / subgenre / energy /
vibe values for the genre-specification classifier. Keeping it in a JSON file (not
Python) means non-developers can retune the vocabulary, and the classifier reads
the same lists used to validate its output, so the two never drift.

`load()` is cached; call `load.cache_clear()` (or pass a path) in tests that swap
the file. The classifier and validator both go through `Taxonomy` so an "other"
genre + free-text `suggested_label` is the only way to escape the vocabulary.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import config

# Sentinel returned when nothing in the taxonomy fits; pairs with suggested_label.
OTHER = "other"


@dataclass(frozen=True)
class Taxonomy:
    energy: tuple[str, ...]
    vibe: tuple[str, ...]
    genres: dict[str, tuple[str, ...]] = field(default_factory=dict)

    # --- membership helpers (all case-insensitive, OTHER always allowed) -----
    def genre_names(self) -> list[str]:
        return list(self.genres.keys())

    def is_genre(self, value: str | None) -> bool:
        return bool(value) and value in self.genres

    def subgenres(self, genre: str | None) -> tuple[str, ...]:
        return self.genres.get(genre or "", ())

    def is_subgenre(self, genre: str | None, value: str | None) -> bool:
        return bool(value) and value in self.subgenres(genre)

    def is_energy(self, value: str | None) -> bool:
        return value in self.energy

    def is_vibe(self, value: str | None) -> bool:
        return value in self.vibe

    def coerce_vibes(self, values) -> list[str]:
        """Keep only known vibe tokens, de-duped, order preserved."""
        out: list[str] = []
        for v in values or []:
            if isinstance(v, str) and v in self.vibe and v not in out:
                out.append(v)
        return out


def _from_dict(data: dict) -> Taxonomy:
    genres = {
        str(g): tuple(subs or [])
        for g, subs in (data.get("genres") or {}).items()
    }
    return Taxonomy(
        energy=tuple(data.get("energy") or config.ENERGY_LEVELS),
        vibe=tuple(data.get("vibe") or []),
        genres=genres,
    )


def load_path(path: Path) -> Taxonomy:
    """Load a taxonomy from an explicit path (uncached; handy for tests)."""
    with open(path, "r", encoding="utf-8") as fh:
        return _from_dict(json.load(fh))


@lru_cache(maxsize=1)
def load() -> Taxonomy:
    """Load the taxonomy at config.TAXONOMY_PATH (cached for the process)."""
    return load_path(Path(config.TAXONOMY_PATH))
