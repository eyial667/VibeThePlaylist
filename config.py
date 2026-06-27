"""Central configuration: API keys, buckets, and classification rules.

Everything you'll want to tune lives here. Edit GENRE_BUCKETS / VIBE_RULES freely.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Paths ---
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "library.db"

# --- Credentials (from .env) ---
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

SPOTIFY_SCOPES = "user-library-read playlist-modify-public playlist-modify-private"

# --- Behaviour toggles ---
USE_LLM = False  # optional Claude enrichment; off by default to avoid cost
PLAYLIST_SCHEMES = ["vibe", "genre", "combined"]  # subset of: vibe/genre/subgenre/combined
PLAYLIST_VISIBILITY_PUBLIC = False
PLAYLIST_PREFIX = "🤖 "
MIN_TRACKS_PER_PLAYLIST = 8  # skip tiny clusters
MULTI_LABEL = True  # a track may land in several playlists

# --- Genre buckets ---------------------------------------------------------
# Map fine-grained Spotify/Last.fm genre substrings -> your coarse bucket.
# Matching is substring + case-insensitive against all of a track's raw genres.
# First matching bucket (in dict order) wins for the "primary" genre; with
# MULTI_LABEL all matching buckets are kept.
GENRE_BUCKETS: dict[str, list[str]] = {
    "Hip-hop/Rap": ["hip hop", "rap", "trap", "drill", "grime"],
    "R&B/Soul": ["r&b", "rnb", "soul", "funk", "neo soul", "motown"],
    "Electronic": ["edm", "house", "techno", "trance", "dubstep", "electro",
                   "drum and bass", "dnb", "garage", "synthwave", "electronic"],
    "Pop": ["pop", "synthpop", "k-pop", "dance pop"],
    "Rock": ["rock", "punk", "grunge", "indie rock", "alternative"],
    "Metal": ["metal", "metalcore", "hardcore", "djent"],
    "Jazz": ["jazz", "bebop", "swing", "fusion"],
    "Classical": ["classical", "orchestra", "baroque", "piano", "opera"],
    "Folk/Acoustic": ["folk", "acoustic", "singer-songwriter", "americana", "country"],
    "Latin": ["latin", "reggaeton", "salsa", "bachata", "cumbia"],
    "Reggae/Dub": ["reggae", "dub", "dancehall", "ska"],
    "Ambient/Lo-fi": ["ambient", "lo-fi", "lofi", "chillhop", "downtempo", "instrumental"],
}
DEFAULT_GENRE = "Other"
# Cap on how many buckets the free classifier keeps per track (strongest first).
# Prevents noisy over-labelling from artist-level genres. Ignored when
# MULTI_LABEL is False (then it's always 1). The LLM pass uses LLM_MAX_GENRES.
MAX_GENRES = 2

# --- Subgenres -------------------------------------------------------------
# Precise subgenres nested under each coarse bucket above. Matching mirrors
# GENRE_BUCKETS (substring, case-insensitive against a track's raw genres +
# Last.fm tags), but a subgenre only applies when its parent bucket already
# matched — so subgenres never contradict the coarse genre. When no subgenre
# matches, consumers fall back to the coarse genre, so existing behaviour is
# preserved. Tune freely here, then re-run `python cli.py classify`.
# Format: coarse bucket -> {subgenre label: [needle substrings]}.
# `_SUBGENRE_BUCKETS_BASE` is the hand-curated set; it is merged below with the
# auto-generated overlay in `subgenres_generated.py` (produced by
# `python cli.py gen-subgenres`). Hand-curated entries win on conflict.
_SUBGENRE_BUCKETS_BASE: dict[str, dict[str, list[str]]] = {
    "Hip-hop/Rap": {
        "Cloud Rap": ["cloud rap"], "Drill": ["drill"], "Trap": ["trap"],
        "Boom Bap": ["boom bap"], "Grime": ["grime"],
    },
    "R&B/Soul": {
        "Neo Soul": ["neo soul", "neo-soul"], "Funk": ["funk"], "Motown": ["motown"],
    },
    "Electronic": {
        "House": ["house"], "Techno": ["techno"], "Trance": ["trance"],
        "Drum & Bass": ["drum and bass", "dnb"], "Dubstep": ["dubstep"],
        "Garage": ["garage"], "Synthwave": ["synthwave"],
    },
    "Pop": {
        "Synthpop": ["synthpop"], "Indie Pop": ["indie pop"],
        "Dance Pop": ["dance pop"], "K-pop": ["k-pop"],
    },
    "Rock": {
        "Classic Rock": ["classic rock"], "Alternative": ["alternative"],
        "Indie Rock": ["indie rock"], "Punk": ["punk"], "Grunge": ["grunge"],
    },
    "Metal": {
        "Metalcore": ["metalcore"], "Hardcore": ["hardcore"], "Djent": ["djent"],
    },
    "Jazz": {
        "Bebop": ["bebop"], "Swing": ["swing"], "Fusion": ["fusion"],
    },
    "Latin": {
        "Reggaeton": ["reggaeton"], "Salsa": ["salsa"], "Bachata": ["bachata"],
        "Cumbia": ["cumbia"], "Latin Pop": ["latin pop"],
    },
    "Reggae/Dub": {
        "Dancehall": ["dancehall"], "Ska": ["ska"], "Dub": ["dub"],
    },
    "Ambient/Lo-fi": {
        "Lo-fi": ["lo-fi", "lofi"], "Chillhop": ["chillhop"],
        "Downtempo": ["downtempo"], "Ambient": ["ambient"],
    },
}


def _merge_subgenres(
    base: dict[str, dict[str, list[str]]],
    overlay: dict[str, dict[str, list[str]]],
) -> dict[str, dict[str, list[str]]]:
    """Merge the auto-generated overlay into the hand-curated base.

    Hand-curated entries take precedence: a subgenre label present in `base`
    keeps its needles even if the overlay also defines it. Overlay-only buckets
    and labels are added. The result is what consumers see as SUBGENRE_BUCKETS.
    """
    out = {bucket: dict(subs) for bucket, subs in base.items()}
    for bucket, subs in (overlay or {}).items():
        dst = out.setdefault(bucket, {})
        for label, needles in subs.items():
            dst.setdefault(label, list(needles))  # base wins on conflict
    return out


try:
    from subgenres_generated import GENERATED_SUBGENRES as _GENERATED_SUBGENRES
except Exception:  # overlay is optional; absence just means no generated extras
    _GENERATED_SUBGENRES = {}

SUBGENRE_BUCKETS: dict[str, dict[str, list[str]]] = _merge_subgenres(
    _SUBGENRE_BUCKETS_BASE, _GENERATED_SUBGENRES
)

# Cap on subgenres the free classifier keeps per track (strongest first).
MAX_SUBGENRES = 2

# --- Vibe / mood / activity rules ------------------------------------------
# Mood tag normalisation: map raw Last.fm tag substrings -> canonical mood.
MOOD_TAGS: dict[str, list[str]] = {
    "melancholic": ["sad", "melancholic", "melancholy", "depressing", "somber"],
    "chill": ["chill", "mellow", "relaxing", "calm", "laid-back", "smooth"],
    "energetic": ["energetic", "upbeat", "hype", "banger", "intense", "driving"],
    "euphoric": ["euphoric", "uplifting", "happy", "feel good", "joyful"],
    "dark": ["dark", "moody", "brooding", "haunting", "ominous"],
    "romantic": ["romantic", "love", "sensual", "sexy"],
    "dreamy": ["dreamy", "ethereal", "atmospheric", "ambient", "spacey"],
    "aggressive": ["aggressive", "angry", "heavy", "brutal"],
}

# Energy banding from Spotify audio-features `energy` (0..1), used when available.
ENERGY_BANDS = [(0.0, 0.40, "low"), (0.40, 0.70, "mid"), (0.70, 1.01, "high")]

# Activity/context rules. Each rule fires if ANY of its trigger sets match.
# Evaluated against: energy_band, genre buckets, moods.
# Format: vibe_label -> list of conditions; a condition is a dict that ALL
# of its keys must satisfy (energy: exact band; genres/moods: intersection non-empty).
VIBE_RULES: dict[str, list[dict]] = {
    "Workout": [
        {"energy": "high", "genres": ["Electronic", "Hip-hop/Rap", "Metal", "Rock"]},
        {"moods": ["energetic", "aggressive"]},
    ],
    "Party": [
        {"energy": "high", "genres": ["Electronic", "Pop", "Latin", "Hip-hop/Rap"]},
        {"moods": ["euphoric"], "energy": "high"},
    ],
    "Focus/Study": [
        {"energy": "low", "genres": ["Ambient/Lo-fi", "Classical", "Jazz"]},
        {"genres": ["Ambient/Lo-fi"]},
    ],
    "Late-night": [
        {"energy": "low", "moods": ["dark", "melancholic", "dreamy"]},
        {"genres": ["R&B/Soul"], "moods": ["romantic", "chill"]},
    ],
    "Chill": [
        {"moods": ["chill", "dreamy"]},
        {"energy": "low", "genres": ["Folk/Acoustic", "R&B/Soul"]},
    ],
    "Feel-good": [
        {"moods": ["euphoric", "romantic"]},
    ],
}
DEFAULT_VIBE = "Unsorted"

# --- Genre-based fallback --------------------------------------------------
# When audio-features are unavailable AND Last.fm tags carry no mood signal
# (common for e.g. rap/hip-hop libraries), classification falls back to these
# per-bucket defaults so energy and vibes are never empty. Coarse by design;
# the optional LLM pass (cli.py llm) refines these per-track.
GENRE_ENERGY: dict[str, str] = {
    "Hip-hop/Rap": "high",
    "R&B/Soul": "low",
    "Electronic": "high",
    "Pop": "mid",
    "Rock": "high",
    "Metal": "high",
    "Jazz": "low",
    "Classical": "low",
    "Folk/Acoustic": "low",
    "Latin": "high",
    "Reggae/Dub": "mid",
    "Ambient/Lo-fi": "low",
    "Other": "mid",
}
GENRE_VIBES: dict[str, list[str]] = {
    "Hip-hop/Rap": ["Workout", "Party"],
    "R&B/Soul": ["Chill", "Late-night"],
    "Electronic": ["Party", "Workout"],
    "Pop": ["Feel-good", "Party"],
    "Rock": ["Workout"],
    "Metal": ["Workout"],
    "Jazz": ["Focus/Study", "Late-night"],
    "Classical": ["Focus/Study"],
    "Folk/Acoustic": ["Chill"],
    "Latin": ["Party"],
    "Reggae/Dub": ["Chill"],
    "Ambient/Lo-fi": ["Focus/Study", "Chill"],
    "Other": ["Feel-good"],
}
# A few sub-genre tag hints that override the bucket energy default (free, no LLM).
# Matched as substrings against a track's Last.fm tags.
SUBGENRE_ENERGY_HINTS: list[tuple[str, str]] = [
    ("cloud rap", "mid"), ("lo-fi", "low"), ("lofi", "low"), ("boom bap", "mid"),
    ("ambient", "low"), ("drill", "high"), ("trap", "high"), ("hardcore", "high"),
    ("ballad", "low"), ("acoustic", "low"), ("slow", "low"),
]

# --- LLM refinement (cli.py llm) ---
LLM_BATCH_SIZE = 40           # tracks per Claude request (keeps cost tiny)
LLM_MAX_GENRES = 2            # cap genres the LLM assigns per track (prefers 1)

# --- Enrichment knobs ---
LASTFM_MAX_TAGS = 10          # top N tags to keep per track/artist
LASTFM_MIN_TAG_WEIGHT = 5     # ignore tags below this Last.fm weight (0-100)
REQUEST_PAUSE_SEC = 0.2       # politeness delay between web API calls
