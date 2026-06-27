# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Secrets — do not read `.env`

**Never read, open, print, or otherwise access the contents of the `.env` file.** It
holds live credentials (Spotify client secret, Last.fm key, optional Anthropic key).
If you need to know which variables exist, read `.env.example` (the safe template) or
`config.py` (where they are loaded) instead. The same applies to the Spotify token
cache under `data/`. These are gitignored and must never be committed or echoed.

## Environment

Always run Python through the project's conda env — it is where dependencies live:

```bash
conda activate Spotify
```

## Commands

```bash
pip install -r requirements.txt   # one-time deps
python setup_env.py               # interactive prompt -> writes .env (Spotify + Last.fm keys)

python cli.py all                 # fetch -> enrich -> classify (full pipeline)
python cli.py fetch               # pull Liked Songs into data/library.db
python cli.py enrich              # artist genres + Last.fm tags + audio-features (runs capability probe)
python cli.py classify            # re-apply config.py rules -> labels (fast, no network)
python cli.py gen-subgenres       # research subgenres for new genres (Claude + web search)
python cli.py query --vibe Chill --genre Jazz --energy low
python cli.py playlists --dry-run # preview clusters, writes nothing
python cli.py playlists           # create/update Spotify playlists (idempotent)

python gui.py                     # Tkinter desktop browser (needs a display)
```

```bash
pip install -r requirements-dev.txt   # adds pytest
python -m pytest                       # full suite (no network/credentials needed)
python -m pytest tests/test_classify.py::test_classify_all_labels_seeded_library  # single test
```

Tests live in `tests/`. They isolate by pointing `config.DB_PATH` at a tmp file (the
`temp_db` / `seeded_db` fixtures in `conftest.py`) and mock Spotify with `FakeSpotify`
and Last.fm via monkeypatching `enrich._lastfm` — so nothing hits the network. There is
no linter; `python -m py_compile *.py` is a quick syntax check.

## Architecture

A pipeline that classifies Spotify Liked Songs by genre + "vibe" (mood + energy +
activity), backed by a SQLite cache, producing a queryable DB and auto-generated
playlists. Data flows in stages, each reading the previous stage's output from the DB:

```
fetch (spotify_client) -> enrich (spotify_client + enrich) -> classify -> {query/gui, playlists}
```

- **`config.py`** is the control surface and the most-edited file. `GENRE_BUCKETS`,
  `SUBGENRE_BUCKETS`, `MOOD_TAGS`, `ENERGY_BANDS`, and `VIBE_RULES` define the entire
  classification taxonomy; everything else (CLI, GUI) derives its option lists from these
  dicts, so editing config is how you retune results — no code changes needed.
  `SUBGENRE_BUCKETS` nests precise subgenres under each coarse bucket (capped by
  `MAX_SUBGENRES`); a subgenre only applies when its parent bucket matched, and consumers
  fall back to the coarse genre when none does. It is the hand-curated
  `_SUBGENRE_BUCKETS_BASE` merged (via `_merge_subgenres`, base wins) with the
  auto-generated overlay in `subgenres_generated.py`. Also holds toggles (`PLAYLIST_SCHEMES`
  — `vibe`/`genre`/`subgenre`/`combined`, `MULTI_LABEL`, `USE_LLM`, prefixes) and
  loads `.env`.

- **`db.py`** is the cache and source of truth across runs. Tables: `tracks`, `artists`,
  `features`, `tags`, `labels`, `meta`. The incremental design depends on the
  `*_missing_*` / `all_*_ids` helpers — each stage only processes rows absent from its
  target table, so re-runs never re-hit APIs for known data. `connect()` reads
  `config.DB_PATH` dynamically, which is what makes headless testing possible.

- **`classify.py`** is a pure rules engine over cached signals (no network). Genre =
  substring match of a track's combined Spotify genres + Last.fm tags against
  `GENRE_BUCKETS`. Subgenre = `_match_subgenres()` scores `SUBGENRE_BUCKETS` entries the
  same way, but only within already-matched buckets (empty = fall back to coarse genre).
  Energy = `audio-features.energy` band when available, else inferred from mood tags.
  Vibes = `VIBE_RULES` conditions over (energy band, genres, moods). `MULTI_LABEL`
  controls single- vs multi-bucket assignment.

- **`spotify_client.py`** handles OAuth, pagination, and crucially `probe_capabilities()`:
  Spotify blocked the `audio-features` endpoint for apps created after late 2024. The
  enrich stage probes this, stores the result in `meta`, and the whole pipeline degrades
  gracefully (energy from tags) when blocked. Any new use of restricted endpoints must
  guard on this.

- **`enrich.py`** = Last.fm crowd tags (the "aggregated opinion" genre/mood source),
  with track->artist tag fallback. Zero-tag tracks get a `__none__` sentinel tag so they
  aren't re-queried every run.

- **`playlists.py`** builds name->track_id clusters for the enabled `PLAYLIST_SCHEMES`
  (`vibe` / `genre` / `subgenre` / `combined`) and syncs idempotently: playlists are
  matched by the `PLAYLIST_PREFIX`-prefixed name and updated in place rather than
  duplicated. The opt-in `subgenre` scheme falls back to the coarse genre for tracks
  with no subgenre.

- **`llm.py`** is optional Claude enrichment, off unless `USE_LLM` and `ANTHROPIC_API_KEY`
  are set; `anthropic` is imported lazily so it isn't a hard dependency.

- **`subgenre_gen.py`** (`cli.py gen-subgenres`) calls Claude (Sonnet) with the web-search
  tool to fetch an exhaustive subgenre list for coarse genres missing one (or `--genre` /
  `--all`), writing results to the `subgenres_generated.py` overlay. Needs `ANTHROPIC_API_KEY`;
  `anthropic` is imported lazily. Run `classify` afterwards to apply.

- **`gui.py`** is a read-only Tkinter browser; it loads all labelled rows once and filters
  in-memory.

## Conventions

- After editing `config.py` rules, you only need `python cli.py classify` (then
  `playlists`) — do not re-fetch or re-enrich.
- Network stages must stay incremental: when adding a data source, add a `*_missing_*`
  helper and only process those rows.
- `.env`, `data/`, and the Spotify token cache are gitignored and must never be committed.
- `main` is a protected branch: no direct pushes. Land every change via a feature branch
  + PR; the required `pytest` checks (Python 3.11/3.12/3.13) must pass before merge.

## Next feature — Spotify auth & web app

The next planned feature turns this single-user local tool into a multi-user **web app**
where each user logs in with their own Spotify account. Full task breakdown lives in
`docs/TODO-webapp-auth.md` (on the `feature/spotify-auth-webapp` branch). Key points:

- Replace the desktop OAuth + file token cache (`data/.spotify_token_cache`) with the
  web **Authorization Code flow**: `/login`, `/callback`, `/logout` routes, `state`
  (CSRF) validation, and refresh-token handling.
- Store tokens **per user/session** via a custom Spotipy `CacheHandler` (not the shared
  file), so libraries and credentials don't collide.
- Refactor `spotify_client.get_client()` to accept an **injected token source** so the
  CLI and web backend share the existing fetch/enrich/classify/playlists pipeline
  unchanged; scope the SQLite store per user.
- Port the Tkinter filter/table/create-playlist UI to the browser.
- The `audio-features` capability probe and tag-based fallback carry over as-is.
