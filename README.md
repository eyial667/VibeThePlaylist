# Spotify Liked-Songs Genre/Vibe Classifier

Classify your Spotify **Liked Songs** by **genre** and **vibe** (mood + energy +
activity), then browse them in a local database and/or auto-generate Spotify
playlists. Free-first: it relies on Spotify artist genres + Last.fm crowd tags;
an optional Claude pass can sharpen the "vibe" but is **off by default**.

See `PLAN.md` for the full design rationale.

## Setup

All commands assume the project's conda env:

```bash
conda activate Spotify
pip install -r requirements.txt
```

### 1. Spotify Developer app (free)
1. Go to https://developer.spotify.com/dashboard → **Create app**.
2. Set a **Redirect URI** of `http://127.0.0.1:8888/callback` (Edit Settings → Redirect URIs → Add).
3. Copy the **Client ID** and **Client Secret**.

> Note: apps created after late 2024 cannot use Spotify's `audio-features`
> endpoint. The tool **probes this automatically** and falls back to inferring
> energy from Last.fm tags if it's blocked.

### 2. Last.fm API key (free)
1. Go to https://www.last.fm/api/account/create and fill the form.
2. Copy the **API key**.

### 3. Configure
```bash
cp .env.example .env
# edit .env and paste your keys
```

### 4. (Optional) Claude enrichment
Only if you want LLM-assisted vibe tagging: set `ANTHROPIC_API_KEY` in `.env`,
`pip install anthropic`, and set `USE_LLM = True` in `config.py`.

## Usage

```bash
conda activate Spotify

python cli.py all                 # fetch liked songs -> enrich -> classify
# or run stages individually:
python cli.py fetch
python cli.py enrich
python cli.py classify

# optional: refine mood/energy/vibe with Claude (needs ANTHROPIC_API_KEY)
python cli.py llm                 # only processes tracks not yet LLM-refined (cached)
python cli.py llm --force         # re-refine everything

# explore your library:
python cli.py query --vibe Chill --genre Jazz
python cli.py query --energy high --limit 30

# playlists:
python cli.py playlists --dry-run   # preview clusters, writes nothing
python cli.py playlists             # create/update playlists in Spotify
```

The first command that touches Spotify opens a browser for one-time OAuth login.

## Graphical browser

After building the library, launch the desktop GUI to explore it visually:

```bash
conda activate Spotify
python gui.py
```

It shows every **genre**, **vibe**, **energy band**, and **mood** (pulled live
from `config.py`) as checkboxes. Tick any combination and the table updates
instantly. Use **Match: Any selected** (a track matching any ticked filter) or
**All categories** (must satisfy each category you've touched). Requires a
desktop session (it opens a real window).

## Re-running
Everything is cached in `data/library.db`. Re-runs only fetch/enrich **newly
liked** tracks. Playlist sync is **idempotent** — playlists are matched by name
(prefix `🤖 `) and updated in place rather than duplicated.

## Energy & vibe coverage

When Spotify audio-features are blocked (apps created after late 2024) **and**
Last.fm tags carry no mood signal — common for rap/hip-hop libraries — there's
nothing for mood/energy rules to match. To avoid empty energy/vibes, classification
falls back to per-genre defaults (`GENRE_ENERGY` / `GENRE_VIBES` in `config.py`)
plus a few sub-genre tag hints (`SUBGENRE_ENERGY_HINTS`), so **energy and vibe are
never empty**. This fallback is coarse (every track in a genre gets a similar
default); run `python cli.py llm` to refine mood/energy/vibe **per track** with
Claude. LLM results are cached (method `llm`) and preserved by later `classify` runs.

## Tuning
Open `config.py`:
- `GENRE_BUCKETS` — fold fine genres into your coarse buckets.
- `MOOD_TAGS`, `ENERGY_BANDS`, `VIBE_RULES` — define what each vibe means.
- `GENRE_ENERGY`, `GENRE_VIBES`, `SUBGENRE_ENERGY_HINTS` — the coverage fallback.
- `PLAYLIST_SCHEMES` — any of `["vibe", "genre", "combined"]`.
- `MIN_TRACKS_PER_PLAYLIST`, `PLAYLIST_PREFIX`, `PLAYLIST_VISIBILITY_PUBLIC`,
  `MULTI_LABEL`, `USE_LLM`, `LLM_BATCH_SIZE`.

After editing, just re-run `python cli.py classify` (and `playlists`) — no need
to re-fetch.
```
