"""Optional Claude refinement of energy / vibe.

The free pass (classify.py) guarantees coverage via genre fallback, but it's
coarse — every track in a genre gets similar energy/vibe. This pass uses Claude
to refine per-track. Batched + cached so a few thousand tracks cost cents:
results are written with method='llm' and not re-requested on later runs.

    python cli.py llm            # refine tracks not yet LLM-classified
    python cli.py llm --force    # re-refine everything
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import config
import db
import text_utils

MODEL = "claude-haiku-4-5-20251001"  # cheap + fast; fine for tagging

_SYSTEM = (
    "You are a music tagging assistant. For each track you assign an energy level "
    "and activity/vibe tags. Be consistent and terse, and always answer for "
    "every track even when unsure."
)


def available() -> bool:
    """True if the LLM pass can run (key present)."""
    return bool(config.ANTHROPIC_API_KEY)


def _sanitize_genres(raw, fallback: list[str]) -> list[str]:
    """Keep only valid buckets, de-duped, max 2; fall back if the LLM gave none."""
    allowed = set(config.GENRE_BUCKETS.keys()) | {config.DEFAULT_GENRE}
    out: list[str] = []
    for g in raw or []:
        if g in allowed and g not in out:
            out.append(g)
        if len(out) == config.LLM_MAX_GENRES:
            break
    if not out:
        out = fallback[: config.LLM_MAX_GENRES] or [config.DEFAULT_GENRE]
    return out


def _sanitize_subgenres(raw, genres: list[str]) -> list[str]:
    """Keep only subgenres valid for the track's chosen buckets, de-duped, capped.

    Subgenres must belong to one of the assigned `genres` (per
    config.SUBGENRE_BUCKETS); anything else is dropped. Empty is fine — consumers
    fall back to the coarse genre — so there's no default to invent here.
    """
    allowed = {
        label
        for g in genres
        for label in config.SUBGENRE_BUCKETS.get(g, {})
    }
    out: list[str] = []
    for s in raw or []:
        if s in allowed and s not in out:
            out.append(s)
        if len(out) == config.MAX_SUBGENRES:
            break
    return out


def classify_batch(tracks: list[dict]) -> list[dict]:
    """tracks: [{id, name, artist_name, genres:[...], tags:[...]}]
    -> [{id, genres:[...], energy, vibes:[]}].

    The LLM also picks the genre bucket(s): one by default, a second ONLY for a
    genuine strong blend (never more than `config.LLM_MAX_GENRES`). Output genres
    are validated against the bucket list and fall back to the track's existing
    buckets if the model returns nothing usable.

    Requires `anthropic` installed and ANTHROPIC_API_KEY set. Imported lazily so
    the package is only needed when the LLM pass is actually used.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    listing = "\n".join(
        f"{i+1}. {t['artist_name']} - {t['name']}"
        f" [candidate genres: {', '.join(t.get('genres', [])) or 'unknown'}]"
        f" (tags: {', '.join(t.get('tags', [])[:8]) or 'none'})"
        for i, t in enumerate(tracks)
    )
    prompt = (
        "Tag each track below. Choose:\n"
        f"- genres: pick the SINGLE best-fitting bucket from {list(config.GENRE_BUCKETS.keys())}. "
        f"Return a 2nd bucket ONLY if the track is a genuine strong blend of two; "
        f"never return more than {config.LLM_MAX_GENRES}. Prefer one. The candidate "
        "genres listed per track are noisy (artist-level) hints, not a requirement.\n"
        f"- subgenres: zero to {config.MAX_SUBGENRES} precise subgenres, chosen ONLY from "
        f"the list for the bucket(s) you picked: {config.SUBGENRE_BUCKETS}. Leave empty if "
        "none clearly fit — do not guess.\n"
        f"- energy: exactly one of {['low', 'mid', 'high']}\n"
        f"- vibes: one or more of {list(config.VIBE_RULES.keys())}\n\n"
        "Return ONLY a JSON array, one object per track, with keys: "
        "index (1-based int), genres (array of 1-2 strings), subgenres (array), "
        "energy (string), vibes (array). No prose, no markdown.\n\n"
        f"{listing}"
    )
    msg = client.messages.create(
        model=MODEL, max_tokens=4000, system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    data = json.loads(text_utils.strip_code_fences(msg.content[0].text))
    out = []
    for obj in data:
        try:
            idx = int(obj["index"]) - 1
        except (KeyError, ValueError, TypeError):
            continue
        if 0 <= idx < len(tracks):
            genres = _sanitize_genres(obj.get("genres"), tracks[idx].get("genres", []))
            out.append({
                "id": tracks[idx]["id"],
                "genres": genres,
                "subgenres": _sanitize_subgenres(obj.get("subgenres"), genres),
                "energy": obj.get("energy"),
                "vibes": obj.get("vibes", []) or [],
            })
    return out


def _targets(force: bool) -> list[dict]:
    """Tracks needing LLM refinement, with the inputs the prompt needs."""
    with db.connect() as conn:
        where = "" if force else "WHERE l.method != 'llm'"
        rows = conn.execute(
            f"SELECT t.id, t.name, t.artist_name, l.genre_buckets "
            f"FROM labels l JOIN tracks t ON t.id = l.track_id {where}"
        ).fetchall()
        tags_by_track: dict[str, list[str]] = {}
        for r in conn.execute("SELECT track_id, tag FROM tags WHERE tag != '__none__'"):
            tags_by_track.setdefault(r["track_id"], []).append(r["tag"])
    return [
        {
            "id": r["id"], "name": r["name"], "artist_name": r["artist_name"],
            "genres": json.loads(r["genre_buckets"] or "[]"),
            "tags": tags_by_track.get(r["id"], []),
        }
        for r in rows
    ]


def refine(force: bool = False, progress=None) -> int:
    """Run the batched LLM pass over tracks that need it. Returns count refined.

    `progress` is an optional callable(done, total) for CLI progress reporting.
    Energy/vibes come from the LLM; the existing genre bucket is preserved.
    """
    targets = _targets(force)
    if not targets:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    refined = 0
    size = max(1, config.LLM_BATCH_SIZE)
    for i in range(0, len(targets), size):
        batch = targets[i : i + size]
        results = classify_batch(batch)
        rows = [
            {
                "track_id": r["id"],
                "genre_buckets": json.dumps(r["genres"]),  # LLM-refined, max 2
                "subgenres": json.dumps(r.get("subgenres", [])),
                "energy_band": r["energy"],
                "vibes": json.dumps(r["vibes"]),
                "method": "llm",
                "classified_at": now,
            }
            for r in results
        ]
        db.upsert_labels(rows)
        refined += len(rows)
        if progress:
            progress(min(i + size, len(targets)), len(targets))
    return refined
