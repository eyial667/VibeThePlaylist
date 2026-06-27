"""Entrypoint. Run stages individually or `all` end-to-end.

Examples:
    conda activate Spotify
    python cli.py fetch          # pull liked songs into the DB
    python cli.py enrich         # artist genres + Last.fm tags + audio features
    python cli.py classify       # apply rules -> labels
    python cli.py playlists --dry-run
    python cli.py playlists       # create/update Spotify playlists
    python cli.py query --vibe Chill --genre Jazz
    python cli.py all            # fetch -> enrich -> classify
"""
from __future__ import annotations

import argparse
import json

from tqdm import tqdm

import classify
import config
import db
import enrich
import spotify_client as spc


def cmd_fetch(args) -> None:
    db.init()
    sp = spc.get_client()
    known = db.all_track_ids()
    new = []
    for t in tqdm(spc.iter_liked_tracks(sp), desc="liked tracks"):
        if t["id"] not in known:
            new.append(t)
    db.upsert_tracks(new)
    print(f"Fetched {len(new)} new track(s); library now has {len(db.all_track_ids())}.")


def cmd_enrich(args) -> None:
    db.init()
    sp = spc.get_client()

    caps = spc.probe_capabilities(sp)
    db.set_meta("audio_features_available", "1" if caps["audio_features"] else "0")
    print(f"Capability probe: audio-features {'AVAILABLE' if caps['audio_features'] else 'BLOCKED'}.")

    # artists (genres)
    need_artists = sorted(db.all_artist_ids() - db.known_artist_ids())
    if need_artists:
        db.upsert_artists(spc.fetch_artists(sp, need_artists))
    print(f"Artists enriched: {len(need_artists)} new.")

    # audio features (only if available)
    if caps["audio_features"]:
        missing = sorted(db.track_ids_missing_features())
        if missing:
            db.upsert_features(spc.fetch_audio_features(sp, missing))
        print(f"Audio features fetched: {len(missing)} track(s).")
    else:
        print("Skipping audio features (endpoint blocked) — energy inferred from tags.")

    # last.fm tags
    if enrich.has_lastfm():
        missing = sorted(db.track_ids_missing_tags())
        with db.connect() as conn:
            meta = {
                r["id"]: (r["artist_name"], r["name"])
                for r in conn.execute("SELECT id, artist_name, name FROM tracks")
                if r["id"] in missing
            }
        rows = []
        for tid in tqdm(missing, desc="last.fm tags"):
            artist, name = meta[tid]
            found = enrich.fetch_track_tags(artist, name)
            for tag, weight in found:
                rows.append({"track_id": tid, "tag": tag, "source": "lastfm", "weight": weight})
            if not found:
                # sentinel so zero-tag tracks aren't re-queried on every run
                rows.append({"track_id": tid, "tag": "__none__", "source": "lastfm", "weight": 0})
        db.upsert_tags(rows)
        print(f"Last.fm tags fetched for {len(missing)} track(s).")
    else:
        print("LASTFM_API_KEY not set — skipping tag enrichment (genre/vibe quality reduced).")


def cmd_classify(args) -> None:
    db.init()
    n = classify.classify_all(overwrite_llm=getattr(args, "overwrite_llm", False))
    print(f"Classified {n} track(s).")


def cmd_llm(args) -> None:
    db.init()
    import llm
    if not llm.available():
        print("ANTHROPIC_API_KEY not set — add it to .env to use the LLM pass. "
              "(The free genre fallback already gives full coverage.)")
        return
    bar = tqdm(total=0, desc="llm refine")

    def progress(done, total):
        bar.total = total
        bar.n = done
        bar.refresh()

    n = llm.refine(force=args.force, progress=progress)
    bar.close()
    if n == 0:
        print("Nothing to refine — all tracks already LLM-classified. Use --force to redo.")
    else:
        print(f"LLM-refined {n} track(s).")


def cmd_genre_classify(args) -> None:
    """Genre/subgenre/energy/vibe classification, persisted by ISRC.

    Single track:  --isrc / --spotify-id / --track "artist - title"
    Whole library: --all   (resumable; skips classified rows unless --reclassify)
    """
    db.init()
    import logging
    import genre_pipeline as gp

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not config.ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY not set — add it to .env to use the classifier.")
        return

    pipeline = gp.build_default_pipeline()

    if args.all:
        bar = tqdm(total=0, desc="genre-classify")

        def progress(done, total):
            bar.total = total
            bar.n = done
            bar.refresh()

        stats = pipeline.classify_library(
            reclassify=args.reclassify, limit=args.limit, progress=progress)
        bar.close()
        print("\nCoverage summary:")
        for line in stats.summary_lines():
            print(f"  {line}")
        return

    # --- single-track flows ---
    if args.isrc:
        track = gp.TrackInput(isrc=args.isrc)
    elif args.spotify_id:
        track = gp.TrackInput(spotify_id=args.spotify_id)
    elif args.track:
        artist, title = gp.parse_track_arg(args.track)
        track = gp.TrackInput(artist=artist, title=title)
    else:
        print("Provide one of --isrc / --spotify-id / --track \"artist - title\", "
              "or --all for the whole library.")
        return

    row = pipeline.classify_track(track)
    vibes = ", ".join(row["vibe"]) or "—"
    sub = row["subgenre"] or "—"
    print(f"\n{row['artist']} — {row['title']}")
    print(f"  ISRC:     {row['isrc']}"
          + ("  (no ISRC — fallback key)" if row["isrc"].startswith("key:") else ""))
    print(f"  Genre:    {row['genre']} / {sub}")
    print(f"  Energy:   {row['energy']}")
    print(f"  Vibe:     {vibes}")
    print(f"  Features: {row['features_source']}"
          + (f" (energy={row['energy_raw']}, tempo={row['tempo']})"
             if row["features_source"] != "none" else ""))
    print(f"  Confidence: {row['confidence']}"
          + (f"   match_confidence: {row['match_confidence']}"
             if row["match_confidence"] is not None else ""))
    if row["notes"]:
        print(f"  Notes:    {row['notes']}")


def cmd_playlists(args) -> None:
    db.init()
    import playlists
    sp = spc.get_client()
    summary = playlists.sync_playlists(sp, dry_run=args.dry_run)
    head = "Would create/update" if args.dry_run else "Synced"
    print(f"{head} {len(summary)} playlist(s):")
    for name, count in summary:
        print(f"  {count:>5}  {name}")


def cmd_query(args) -> None:
    db.init()
    sql = (
        "SELECT t.artist_name, t.name, l.genre_buckets, l.energy_band, l.vibes "
        "FROM labels l JOIN tracks t ON t.id=l.track_id WHERE 1=1"
    )
    params: list = []
    if args.genre:
        sql += " AND l.genre_buckets LIKE ?"
        params.append(f"%{args.genre}%")
    if args.vibe:
        sql += " AND l.vibes LIKE ?"
        params.append(f"%{args.vibe}%")
    if args.energy:
        sql += " AND l.energy_band = ?"
        params.append(args.energy)
    sql += " LIMIT ?"
    params.append(args.limit)
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    for r in rows:
        genres = ", ".join(json.loads(r["genre_buckets"] or "[]"))
        vibes = ", ".join(json.loads(r["vibes"] or "[]"))
        print(f"{r['artist_name']} — {r['name']}  [{genres} | {r['energy_band']} | {vibes}]")
    print(f"\n{len(rows)} result(s).")


def cmd_all(args) -> None:
    cmd_fetch(args)
    cmd_enrich(args)
    cmd_classify(args)


def main() -> None:
    p = argparse.ArgumentParser(description="Spotify liked-songs genre/vibe classifier")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("fetch").set_defaults(func=cmd_fetch)
    sub.add_parser("enrich").set_defaults(func=cmd_enrich)
    cl = sub.add_parser("classify")
    cl.add_argument("--overwrite-llm", action="store_true",
                    help="reclassify everything, discarding LLM-refined labels")
    cl.set_defaults(func=cmd_classify)
    sub.add_parser("all").set_defaults(func=cmd_all)

    lm = sub.add_parser("llm", help="refine mood/energy/vibe with Claude (needs ANTHROPIC_API_KEY)")
    lm.add_argument("--force", action="store_true", help="re-refine all tracks, even already-done ones")
    lm.set_defaults(func=cmd_llm)

    gc = sub.add_parser(
        "genre-classify",
        help="classify genre/subgenre/energy/vibe via ISRC + ReccoBeats + Claude "
             "(needs ANTHROPIC_API_KEY)")
    gsel = gc.add_mutually_exclusive_group()
    gsel.add_argument("--isrc", help="classify a single track by ISRC")
    gsel.add_argument("--spotify-id", dest="spotify_id",
                      help="classify a single track by Spotify track ID")
    gsel.add_argument("--track", help='classify a single track by "artist - title"')
    gsel.add_argument("--all", action="store_true",
                      help="batch-classify the whole library (resumable)")
    gc.add_argument("--reclassify", action="store_true",
                    help="with --all: re-classify rows already classified")
    gc.add_argument("--limit", type=int, default=None,
                    help="with --all: cap how many tracks to process this run")
    gc.add_argument("--verbose", action="store_true",
                    help="log the resolution/feature path taken per track")
    gc.set_defaults(func=cmd_genre_classify)

    pl = sub.add_parser("playlists")
    pl.add_argument("--dry-run", action="store_true", help="show clusters without writing")
    pl.set_defaults(func=cmd_playlists)

    q = sub.add_parser("query")
    q.add_argument("--genre")
    q.add_argument("--vibe")
    q.add_argument("--energy", choices=["low", "mid", "high"])
    q.add_argument("--limit", type=int, default=50)
    q.set_defaults(func=cmd_query)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
