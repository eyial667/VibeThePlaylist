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

## Private desktop packaging (V1 groundwork)

The desktop app can now be prepared for a private packaged build without
shipping a `.env` file to testers:

1. Copy `local_settings.example.py` to `local_settings.py`
2. Fill in the shared Spotify / Last.fm values on **your** machine only
3. Keep `local_settings.py` uncommitted (it is gitignored)
4. Install PyInstaller in your env:

   ```bash
   conda activate Spotify
   pip install pyinstaller
   ```

5. Build the GUI with the included `VibeThePlaylist.spec`

PyInstaller builds for the **current OS only**, so create each package on that OS:

### Windows

```bash
conda activate Spotify
pyinstaller --clean VibeThePlaylist.spec
```

Output: `dist/VibeThePlaylist/VibeThePlaylist.exe`

### macOS

```bash
conda activate Spotify
pyinstaller --clean VibeThePlaylist.spec
```

Output: `dist/VibeThePlaylist.app`

### Linux

```bash
conda activate Spotify
pyinstaller --clean VibeThePlaylist.spec
```

Output: `dist/VibeThePlaylist/VibeThePlaylist`

Packaged builds now store their writable state (DB, token cache, preview cache)
under the user's OS app-data directory instead of the repository checkout.

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
default); run `python cli.py llm` to refine genre, mood, energy, and vibe
**per track** with Claude. The LLM picks the single best genre bucket (a second
only for a genuine blend, capped by `LLM_MAX_GENRES`), which fixes the
"3–7 genres per track" noise from artist-level Spotify genres. LLM results are
cached (method `llm`) and preserved by later `classify` runs.

## ISRC-based genre/subgenre classification (`genre-classify`)

A second, higher-resolution classifier assigns each track a **genre, subgenre,
energy, and vibe** and stores the result in its own `classifications` table,
keyed by **ISRC**. It works for an entire international catalog (American,
European, Latin — no region is assumed) and degrades gracefully so every track
always gets a result.

```bash
# single track, three ways:
python cli.py genre-classify --isrc QM6MZ2040267
python cli.py genre-classify --spotify-id 4iV5W9uYEdYUVa79Axb7Rh
python cli.py genre-classify --track "Bad Bunny - Dákiti"

# whole library (resumable — safe to Ctrl-C and re-run; skips done rows):
python cli.py genre-classify --all
python cli.py genre-classify --all --reclassify     # redo everything
python cli.py genre-classify --all --limit 200 --verbose
```

Needs `ANTHROPIC_API_KEY` in `.env`. In the GUI, **Classify track…** classifies
the selected/entered track and shows the stored result; **Classify library…**
runs the resumable batch off the UI thread with a progress bar.

The feature lives in the **`genreclass/`** package, separate from the original
pipeline at the repo root:

```
genreclass/
  resolver.py    any identifier -> canonical ISRC (the join key)
  providers.py   MetadataProvider (Spotify) + FeatureProvider (ReccoBeats/Deezer)
  classifier.py  Claude Haiku classifier, constrained to taxonomy.json
  pipeline.py    resolve -> features -> classify -> persist (single + batch)
  taxonomy.py    loader for taxonomy.json (the editable controlled vocabulary)
  taxonomy.json  the controlled vocabulary itself
```

Shared infrastructure (`config.py`, `db.py`, `text_utils.py`, `spotify_client.py`)
stays at the root and is reused by the original pipeline.

### How a track is resolved to an ISRC

Everything downstream joins on **ISRC**, never on raw artist+title, so each input
is normalized first (`genreclass/resolver.py`):

| Input you have        | What happens                                                        |
|-----------------------|---------------------------------------------------------------------|
| ISRC                  | used directly                                                       |
| Spotify track ID      | track fetched, `external_ids.isrc` read                            |
| Artist + title only   | Spotify search → best match's ISRC, with a `match_confidence`       |

Matching is robust to diacritics/accents, non-ASCII titles, `feat.`/`ft.`
credits, and remix/edit/version suffixes (`text_utils.py`) across all regions.
The resolved ISRC + Spotify ID are written back onto the library row so future
runs skip resolution. If **no** ISRC can be found, the track falls back to a
normalized `key:artist|title` identifier, is flagged as a weak match, and shows
up in the coverage report.

### Feature fallback chain

Numeric audio features (energy, danceability, valence, acousticness, tempo) come
from **ReccoBeats only** — Spotify's `audio-features` endpoint is deprecated
(403 for new apps since 2024-11-27) and is **never** called. The provider
degrades in order, recording which path it took (`features_source`):

1. **`reccobeats_lookup`** — ReccoBeats lookup by Spotify ID → audio features.
2. **`reccobeats_extracted`** — on a lookup miss, fetch a 30 s **Deezer** preview
   (broad international catalog, free) and POST the clip to ReccoBeats'
   audio-feature **extraction** endpoint. (Deezer's own API only returns BPM/gain,
   so it's used purely as the audio source.)
3. **`none`** — no features available; Claude classifies from metadata + genre
   hints alone, at lower confidence.

The energy label is derived from the numeric `energy`/`danceability` when present
(consistent across any classifier), otherwise from the model's judgment. All
external results — Spotify metadata, ReccoBeats features, Deezer previews — are
cached in the DB keyed by ISRC, so re-runs don't re-hit the APIs.

### Editing the taxonomy

The classifier is constrained to a controlled vocabulary in
**`genreclass/taxonomy.json`** (allowed `genres` → `subgenres`, the `energy`
enum, and the `vibe` list). The model must pick from these or return
`genre: "other"` with a free-text `suggested_label` rather than inventing a
label. Edit the file to retune — add/remove genres, subgenres, or vibes — then
re-run `python cli.py genre-classify --all --reclassify`. The model id is
configurable via `CLASSIFIER_MODEL` in `.env` (default `claude-haiku-4-5`) so you
can A/B another model without code changes. Providers (`MetadataProvider`,
`FeatureProvider`, `Classifier`) sit behind interfaces in
`genreclass/providers.py` and `genreclass/classifier.py`, so a paid feature
source or a different model can be dropped in later.

## Tuning
Open `config.py`:
- `GENRE_BUCKETS` — fold fine genres into your coarse buckets.
- `MAX_GENRES` — cap on buckets the free classifier keeps per track (strongest first).
- `MOOD_TAGS`, `ENERGY_BANDS`, `VIBE_RULES` — define what each vibe means.
- `GENRE_ENERGY`, `GENRE_VIBES`, `SUBGENRE_ENERGY_HINTS` — the coverage fallback.
- `PLAYLIST_SCHEMES` — any of `["vibe", "genre", "combined"]`.
- `MIN_TRACKS_PER_PLAYLIST`, `PLAYLIST_PREFIX`, `PLAYLIST_VISIBILITY_PUBLIC`,
  `MULTI_LABEL`, `USE_LLM`, `LLM_BATCH_SIZE`.

After editing, just re-run `python cli.py classify` (and `playlists`) — no need
to re-fetch.
