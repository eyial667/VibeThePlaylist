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
    n = classify.classify_all()
    print(f"Classified {n} track(s).")


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
    sub.add_parser("classify").set_defaults(func=cmd_classify)
    sub.add_parser("all").set_defaults(func=cmd_all)

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
