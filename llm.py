"""OPTIONAL Claude enrichment. Off unless config.USE_LLM is True.

Batches many tracks per request and writes mood/vibe back as labels so the cost
for a few thousand tracks is a handful of cents. Only call for tracks that lack
strong signal from free sources if you want to keep it even cheaper.
"""
from __future__ import annotations

import json

import config

MODEL = "claude-haiku-4-5-20251001"  # cheap + fast; fine for tagging

_SYSTEM = (
    "You are a music tagging assistant. For each track, return concise genre, "
    "mood, and activity/vibe tags. Be consistent and terse."
)


def available() -> bool:
    return config.USE_LLM and bool(config.ANTHROPIC_API_KEY)


def classify_batch(tracks: list[dict]) -> list[dict]:
    """tracks: [{id, name, artist_name, tags:[...]}] -> [{id, moods:[], vibes:[]}].

    Requires `anthropic` installed and ANTHROPIC_API_KEY set. Imported lazily so
    the package is only needed when USE_LLM is enabled.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    listing = "\n".join(
        f"{i+1}. {t['artist_name']} - {t['name']} (tags: {', '.join(t.get('tags', [])[:6])})"
        for i, t in enumerate(tracks)
    )
    prompt = (
        "Tag each track. Use these mood options: "
        f"{list(config.MOOD_TAGS.keys())}. Vibe options: {list(config.VIBE_RULES.keys())}.\n"
        "Return ONLY JSON: a list of objects with keys index (1-based), moods, vibes.\n\n"
        f"{listing}"
    )
    msg = client.messages.create(
        model=MODEL, max_tokens=2000, system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json").strip()
    data = json.loads(text)
    out = []
    for obj in data:
        idx = int(obj["index"]) - 1
        if 0 <= idx < len(tracks):
            out.append({
                "id": tracks[idx]["id"],
                "moods": obj.get("moods", []),
                "vibes": obj.get("vibes", []),
            })
    return out
