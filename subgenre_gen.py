"""Generate an exhaustive subgenre list for a coarse genre via Claude + web search.

Run when a new coarse genre is added to `config.GENRE_BUCKETS`:

    python cli.py gen-subgenres                 # all buckets missing subgenres
    python cli.py gen-subgenres --genre Pop     # one bucket
    python cli.py gen-subgenres --all           # regenerate every bucket

Each call asks Claude to web-search for the genre's subgenres and return a
mapping of {subgenre label: [lowercase needle substrings]}. Results are written
to `subgenres_generated.py`, which `config.py` merges into `SUBGENRE_BUCKETS`
(hand-curated entries win). `anthropic` is imported lazily so it stays optional.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import config

MODEL = "claude-sonnet-4-6"  # supports the web_search_20260209 server tool
GENERATED_PATH = config.ROOT / "subgenres_generated.py"

_SYSTEM = (
    "You are a music taxonomy assistant. You research music genres and return "
    "precise, well-known subgenres. Be exhaustive but only include established "
    "subgenres, not one-off scene labels."
)

_HEADER = '''"""Auto-generated subgenre taxonomy — merged into config.SUBGENRE_BUCKETS.

Written by `python cli.py gen-subgenres`. Hand-curated entries in
`config.SUBGENRE_BUCKETS` take precedence over anything here. Edit via the CLI,
not by hand.

Shape: coarse bucket -> {subgenre label: [lowercase needle substrings]}.
"""
from __future__ import annotations

GENERATED_SUBGENRES: dict[str, dict[str, list[str]]] = '''


def available() -> bool:
    """True if the generator can run (Anthropic key present)."""
    return bool(config.ANTHROPIC_API_KEY)


def missing_genres() -> list[str]:
    """Coarse buckets in GENRE_BUCKETS that have no subgenres yet."""
    return [
        g for g in config.GENRE_BUCKETS
        if not config.SUBGENRE_BUCKETS.get(g)
    ]


# --- overlay file I/O ------------------------------------------------------
def load_generated(path: Path | None = None) -> dict[str, dict[str, list[str]]]:
    """Read the current generated overlay dict (empty if the file is absent)."""
    path = path or GENERATED_PATH
    if not path.exists():
        return {}
    spec = importlib.util.spec_from_file_location("_subgenres_generated_load", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return dict(getattr(module, "GENERATED_SUBGENRES", {}))


def write_generated(data: dict[str, dict[str, list[str]]], path: Path | None = None) -> None:
    """Serialise the overlay dict back to `subgenres_generated.py`.

    JSON is valid Python for str/list/dict, so the file stays importable.
    """
    path = path or GENERATED_PATH
    body = json.dumps(data, indent=4, ensure_ascii=False, sort_keys=True)
    path.write_text(_HEADER + body + "\n", encoding="utf-8")


# --- LLM call --------------------------------------------------------------
def _prompt(genre: str, max_subgenres: int) -> str:
    return (
        f"Research the music genre \"{genre}\" using web search and list its most "
        f"established subgenres (up to {max_subgenres}). For each subgenre, give "
        "the lowercase substrings that would appear in Spotify or Last.fm genre "
        "tags for tracks of that subgenre.\n\n"
        "After researching, output ONLY a JSON object (no prose, no markdown "
        "fences) mapping each subgenre's display name to an array of 1-3 "
        "lowercase needle strings, e.g.:\n"
        '{"Deep House": ["deep house"], "Tech House": ["tech house"]}'
    )


def _extract_json(text: str) -> dict[str, list[str]]:
    """Pull the JSON object out of a model response, tolerating stray prose."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.lstrip().startswith("json") else text
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        data = json.loads(match.group(0))
    # normalise: {label: [needles]}; coerce string needles to a list; drop junk
    out: dict[str, list[str]] = {}
    for label, needles in (data or {}).items():
        if isinstance(needles, str):
            needles = [needles]
        if isinstance(label, str) and isinstance(needles, list):
            clean = [str(n).lower() for n in needles if str(n).strip()]
            if clean:
                out[label] = clean
    return out


def generate_subgenres(genre: str, max_subgenres: int = 25) -> dict[str, list[str]]:
    """Ask Claude (with web search) for an exhaustive subgenre list for `genre`.

    Returns {subgenre label: [needles]}. Requires `anthropic` installed and
    ANTHROPIC_API_KEY set; imported lazily so it isn't a hard dependency.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}]
    messages = [{"role": "user", "content": _prompt(genre, max_subgenres)}]

    # Server-side web search runs inline; loop only to resume on pause_turn.
    for _ in range(6):
        resp = client.messages.create(
            model=MODEL, max_tokens=4096, system=_SYSTEM,
            tools=tools, messages=messages,
        )
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break

    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return _extract_json(text)


def regenerate(genres: list[str], max_subgenres: int = 25,
               path: Path | None = None, progress=None) -> dict[str, int]:
    """Generate subgenres for each genre and merge into the overlay file.

    Returns {genre: count}. `progress` is an optional callable(genre, count).
    """
    data = load_generated(path)
    counts: dict[str, int] = {}
    for genre in genres:
        subs = generate_subgenres(genre, max_subgenres)
        if subs:
            data[genre] = subs
        counts[genre] = len(subs)
        if progress:
            progress(genre, len(subs))
    write_generated(data, path)
    return counts
