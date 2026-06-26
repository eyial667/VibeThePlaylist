# Spotify Liked-Songs Classifier — Design Plan

> Status: **proposed design, awaiting your review.** No code written yet.
> Last updated: 2026-06-26

## 1. Goal

Take all of your **Liked Songs** on Spotify and classify each track by **genre** and
**"vibe"** (mood/emotion + energy level + activity/context). Use the result to produce:

1. **A searchable local database** of every liked track with its tags.
2. **Auto-generated Spotify playlists**, with the scheme **configurable** so you can run
   any (or all) of:
   - **Vibe-primary** — e.g. `Chill`, `Workout`, `Late-night`, `Focus` (cross-genre).
   - **Genre-primary** — e.g. `Jazz`, `Hip-hop`, `Electronic`.
   - **Combined** — e.g. `Chill Jazz`, `High-energy Electronic`.

## 2. Key constraints (from our scoping)

- **Library size:** ~2,000–7,000 liked tracks → caching + incremental runs are mandatory.
- **Minimize LLM cost:** free web sources do the heavy lifting; LLM enrichment is
  **optional and OFF by default**.
- **Spotify app capabilities unknown:** since late 2024 Spotify restricted several Web API
  endpoints (notably **audio-features**, **recommendations**, **related-artists**) for
  *newly created* apps. The pipeline **probes capabilities at startup** and **degrades
  gracefully** if `audio-features` is unavailable.
- **Stack:** Python.

## 3. Label sources (free-first)

| Signal | Primary source | Fallback / notes |
|---|---|---|
| **Genre** | Spotify artist genres + **Last.fm tags** | Spotify genres work even on new apps but are *artist-level*. Last.fm tags = the "aggregated crowd opinion", and add track-level nuance. |
| **Energy** | Spotify **audio-features** (`energy`, `valence`, `danceability`, `tempo`) **if available** | If blocked: derive a coarse energy score from Last.fm mood tags + genre heuristics. |
| **Mood/emotion** | Last.fm tags (e.g. `melancholic`, `chill`, `euphoric`) | Optional LLM pass can sharpen this. |
| **Activity/context** | **Rule-based** mapping from (energy + genre + mood) | e.g. high-energy + electronic/hip-hop → `Workout/Party`; low-energy + acoustic/ambient → `Study/Late-night`. |
| **(Optional) LLM** | Claude API, **batched** (~50 tracks/call), **cached** | OFF by default. If enabled, cost for ~5k tracks is a few cents due to batching. Greatly improves "vibe" accuracy. |

### Why this mix
- You asked me to pick the best mix. Genre is most reliable from **consensus data**
  (Spotify + Last.fm). "Vibe" is fuzzier, so we combine **objective audio features when
  available** with **crowd mood tags**, and keep the **LLM as an optional booster** to
  respect the no-cost preference.

## 4. Architecture

```
liked songs ─► fetch ─► enrich (cached) ─► classify ─► ┌─► local DB + query CLI
                                                       └─► Spotify playlists
```

1. **Fetch** — pull all Liked Songs via Spotipy, store raw track + artist data.
2. **Enrich** — for each *new* track, gather signals from the sources above.
   Everything is written to a **local SQLite cache**, so re-runs only process newly
   liked tracks and never re-hit APIs for known ones.
3. **Classify** — apply a transparent, tunable **ruleset** to assign:
   - one or more **genre buckets**
   - an **energy band** (low / mid / high)
   - one or more **mood** tags
   - one or more **activity/vibe** labels
4. **Database deliverable** — SQLite DB + a small CLI:
   `python -m cli query --vibe chill --genre jazz --energy low`
5. **Playlist deliverable** — generate playlists per the configured scheme(s).
   **Idempotent:** re-runs *update* existing playlists (matched by name) instead of
   creating duplicates.

## 5. Proposed project layout

```
Spotify_Automation/
├── PLAN.md                 # this file
├── README.md              # setup + usage (written after you approve)
├── .env.example           # template for API keys / config
├── requirements.txt
├── config.py              # loads .env, defines buckets & rules config
├── spotify_client.py      # auth, fetch liked songs, capability probe
├── enrich.py              # Last.fm / MusicBrainz / audio-features fetchers
├── classify.py            # the tunable ruleset (genre + vibe) ← you'll tweak this most
├── db.py                  # SQLite schema + read/write/cache
├── playlists.py           # create/update playlists (3 configurable schemes)
├── llm.py                 # OPTIONAL Claude enrichment (off by default)
├── cli.py                 # entrypoint: fetch / enrich / classify / playlists / query
└── data/
    └── library.db         # generated SQLite cache
```

## 6. Data model (SQLite, sketch)

- **tracks**: `id, name, artist_ids, album, added_at, duration_ms`
- **artists**: `id, name, spotify_genres (json)`
- **features**: `track_id, energy, valence, danceability, tempo, source, available (bool)`
- **tags**: `track_id, tag, source (lastfm/musicbrainz), weight`
- **labels**: `track_id, genre_buckets (json), energy_band, moods (json), vibes (json),
  classified_at, method (rules/llm)`

## 7. Configuration knobs (in `config.py` / `.env`)

- `PLAYLIST_SCHEMES = ["vibe", "genre", "combined"]` — enable any subset.
- `USE_LLM = false` — toggle optional Claude enrichment.
- `GENRE_BUCKETS` — map of fine Spotify/Last.fm genres → your coarse buckets
  (e.g. `deep house`, `tech house` → `Electronic`). Editable.
- `VIBE_RULES` — the energy/mood/genre → activity mapping. Editable.
- `MIN_TRACKS_PER_PLAYLIST` — skip tiny clusters.
- API keys: `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`,
  `LASTFM_API_KEY`, (optional) `ANTHROPIC_API_KEY`.

## 8. Setup you'll need (all free)

1. **Spotify Developer app** → Client ID + Secret + Redirect URI.
   Scopes: `user-library-read`, `playlist-modify-public`, `playlist-modify-private`.
2. **Last.fm API account** → free API key.
3. (Optional) **Anthropic API key** — only if you enable the LLM pass.

The README will include click-by-click instructions for each.

## 9. Build order (once approved)

1. Project skeleton + `.env.example` + `requirements.txt` + README setup section.
2. Spotify auth + fetch liked songs + **capability probe** (tells us if audio-features works).
3. SQLite cache layer.
4. Enrichment (Spotify genres → Last.fm tags → optional audio-features).
5. Classification ruleset (genre buckets + energy/mood/vibe).
6. Query CLI over the DB.
7. Playlist generation (3 configurable schemes, idempotent).
8. (Optional) LLM enrichment module.

## 10. Open questions / things to confirm

- [ ] Genre granularity: how many coarse buckets do you want? (~10–15 like
      `Rock, Pop, Hip-hop, Electronic, Jazz, Classical, R&B, Metal, Folk/Acoustic, ...`)
      Or finer?
- [ ] Should a track be allowed in **multiple** playlists (multi-label) or forced into one?
- [ ] Public or private playlists? Prefix naming convention (e.g. `🤖 Chill`)?
- [ ] Confirm whether you want me to test the Spotify capability probe early (it determines
      how good the energy/vibe signal can be).
```
