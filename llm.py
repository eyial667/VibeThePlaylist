"""Optional Claude refinement of mood / energy / vibe.

The free pass (classify.py) guarantees coverage via genre fallback, but it's
coarse — every track in a genre gets similar energy/vibe. This pass uses Claude
to refine per-track, which matters most for libraries where Last.fm tags carry no
mood signal (e.g. rap/hip-hop). Batched + cached so a few thousand tracks cost
cents: results are written with method='llm' and not re-requested on later runs.

    python cli.py llm            # refine tracks not yet LLM-classified
    python cli.py llm --force    # re-refine everything
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import config
import db

MODEL = "claude-haiku-4-5-20251001"  # cheap + fast; fine for tagging

_SYSTEM = (
    "You are a music tagging assistant. For each track you assign an energy level, "
    "moods, and activity/vibe tags. Be consistent and terse, and always answer for "
    "every track even when unsure."
)


def available() -> bool:
    """True if the LLM pass can run (key present)."""
    return bool(config.ANTHROPIC_API_KEY)


def classify_batch(tracks: list[dict]) -> list[dict]:
    """tracks: [{id, name, artist_name, genres:[...], tags:[...]}]
    -> [{id, energy, moods:[], vibes:[]}].

    Requires `anthropic` installed and ANTHROPIC_API_KEY set. Imported lazily so
    the package is only needed when the LLM pass is actually used.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    listing = "\n".join(
        f"{i+1}. {t['artist_name']} - {t['name']}"
        f" [genre: {', '.join(t.get('genres', [])) or 'unknown'}]"
        f" (tags: {', '.join(t.get('tags', [])[:8]) or 'none'})"
        for i, t in enumerate(tracks)
    )
    prompt = (
        "Tag each track below. Choose:\n"
        f"- energy: exactly one of {['low', 'mid', 'high']}\n"
        f"- moods: zero or more of {list(config.MOOD_TAGS.keys())}\n"
        f"- vibes: one or more of {list(config.VIBE_RULES.keys())}\n\n"
        "Return ONLY a JSON array, one object per track, with keys: "
        "index (1-based int), energy (string), moods (array), vibes (array). "
        "No prose, no markdown.\n\n"
        f"{listing}"
    )
    msg = client.messages.create(
        model=MODEL, max_tokens=4000, system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:].strip() if text.lstrip().startswith("json") else text.strip()
    data = json.loads(text)
    out = []
    for obj in data:
        try:
            idx = int(obj["index"]) - 1
        except (KeyError, ValueError, TypeError):
            continue
        if 0 <= idx < len(tracks):
            out.append({
                "id": tracks[idx]["id"],
                "energy": obj.get("energy"),
                "moods": obj.get("moods", []) or [],
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
    Energy/moods/vibes come from the LLM; the existing genre bucket is preserved.
    """
    targets = _targets(force)
    if not targets:
        return 0

    with db.connect() as conn:
        genres_by_id = {
            r["track_id"]: r["genre_buckets"]
            for r in conn.execute("SELECT track_id, genre_buckets FROM labels")
        }

    now = datetime.now(timezone.utc).isoformat()
    refined = 0
    size = max(1, config.LLM_BATCH_SIZE)
    for i in range(0, len(targets), size):
        batch = targets[i : i + size]
        results = classify_batch(batch)
        rows = [
            {
                "track_id": r["id"],
                "genre_buckets": genres_by_id.get(r["id"], "[]"),  # keep genre
                "energy_band": r["energy"],
                "moods": json.dumps(r["moods"]),
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
