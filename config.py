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
PLAYLIST_SCHEMES = ["vibe", "genre", "combined"]  # any subset of these
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

# --- Enrichment knobs ---
LASTFM_MAX_TAGS = 10          # top N tags to keep per track/artist
LASTFM_MIN_TAG_WEIGHT = 5     # ignore tags below this Last.fm weight (0-100)
REQUEST_PAUSE_SEC = 0.2       # politeness delay between web API calls
