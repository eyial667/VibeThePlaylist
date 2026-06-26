"""Desktop GUI for browsing your classified library.

Shows every possible genre, vibe, energy band, and mood (straight from config.py)
as checkboxes, plus searchable multi-select lists of artists and albums. Pick any
combination and the table updates live to show matching liked songs.

    conda activate Spotify
    python gui.py

Requires that you've already built the library:  python cli.py all
"""
from __future__ import annotations

import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import config
import db


# --- option lists, derived from config so they always stay in sync ---------
GENRES = list(config.GENRE_BUCKETS.keys()) + [config.DEFAULT_GENRE]
VIBES = list(config.VIBE_RULES.keys()) + [config.DEFAULT_VIBE]
ENERGIES = [band[2] for band in config.ENERGY_BANDS]  # low / mid / high
MOODS = list(config.MOOD_TAGS.keys())


def load_rows() -> list[dict]:
    """Read all labelled tracks once; filtering happens in-memory."""
    try:
        db.init()
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT t.id, t.artist_name, t.name, t.album, l.genre_buckets, "
                "l.energy_band, l.moods, l.vibes "
                "FROM labels l JOIN tracks t ON t.id = l.track_id"
            ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "artist": r["artist_name"],
            "title": r["name"],
            "album": r["album"] or "",
            "genres": json.loads(r["genre_buckets"] or "[]"),
            "energy": r["energy_band"],
            "moods": json.loads(r["moods"] or "[]"),
            "vibes": json.loads(r["vibes"] or "[]"),
        })
    return out


class CheckGroup(ttk.LabelFrame):
    """A labelled frame of checkboxes with Select all / none helpers."""

    def __init__(self, master, title: str, options: list[str], on_change):
        super().__init__(master, text=title, padding=8)
        self.vars: dict[str, tk.BooleanVar] = {}
        for opt in options:
            var = tk.BooleanVar(value=False)
            self.vars[opt] = var
            ttk.Checkbutton(self, text=opt or "(none)", variable=var,
                            command=on_change).pack(anchor="w")
        btns = ttk.Frame(self)
        btns.pack(anchor="w", pady=(6, 0))
        ttk.Button(btns, text="All", width=5,
                   command=lambda: self._set_all(True, on_change)).pack(side="left")
        ttk.Button(btns, text="None", width=6,
                   command=lambda: self._set_all(False, on_change)).pack(side="left")

    def _set_all(self, value: bool, on_change) -> None:
        for var in self.vars.values():
            var.set(value)
        on_change()

    def selected(self) -> set[str]:
        return {opt for opt, var in self.vars.items() if var.get()}


class SearchableListGroup(ttk.LabelFrame):
    """A filter box + scrollable multi-select list, for high-cardinality fields
    (artists, albums) where checkboxes are impractical. Type to narrow the list;
    selections persist across searches. Ctrl/Shift-click for multi-select."""

    def __init__(self, master, title: str, options: list[str], on_change,
                 height: int = 9, width: int = 26):
        super().__init__(master, text=title, padding=6)
        self.all_options = sorted(o for o in options if o)
        self.on_change = on_change
        self._selected: set[str] = set()
        self._shown: list[str] = []

        self.search_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.search_var, width=width).pack(fill="x")
        self.search_var.trace_add("write", lambda *_: self._refilter())

        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, pady=(4, 0))
        self.listbox = tk.Listbox(box, selectmode="extended", height=height,
                                  width=width, exportselection=False, activestyle="none")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._on_select())

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(4, 0))
        self.count_lbl = ttk.Label(bar, text="")
        self.count_lbl.pack(side="left")
        ttk.Button(bar, text="Clear", width=6, command=self.clear).pack(side="right")

        self._populate(self.all_options)

    def _populate(self, options: list[str]) -> None:
        self._shown = options
        self.listbox.delete(0, "end")
        for o in options:
            self.listbox.insert("end", o)
        for i, o in enumerate(options):
            if o in self._selected:
                self.listbox.selection_set(i)
        self._update_count()

    def _sync_from_view(self) -> None:
        """Fold the current listbox selection back into the persistent set."""
        shown_selected = {self._shown[i] for i in self.listbox.curselection()}
        for o in self._shown:           # forget any shown items now unselected
            self._selected.discard(o)
        self._selected |= shown_selected

    def _on_select(self) -> None:
        self._sync_from_view()
        self._update_count()
        self.on_change()

    def _refilter(self) -> None:
        self._sync_from_view()
        q = self.search_var.get().strip().lower()
        opts = [o for o in self.all_options if q in o.lower()] if q else self.all_options
        self._populate(opts)

    def _update_count(self) -> None:
        n = len(self._selected)
        self.count_lbl.config(text=f"{n} selected" if n else "")

    def clear(self) -> None:
        self._selected.clear()
        self.listbox.selection_clear(0, "end")
        self._update_count()
        self.on_change()

    def selected(self) -> set[str]:
        self._sync_from_view()
        return set(self._selected)


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.pack(fill="both", expand=True)
        self.rows = load_rows()

        # --- top: filter groups ---
        filters = ttk.Frame(self)
        filters.pack(fill="x")

        self.genre_group = CheckGroup(filters, "Genres", GENRES, self.refresh)
        self.genre_group.pack(side="left", fill="y", padx=(0, 8))

        self.vibe_group = CheckGroup(filters, "Vibes", VIBES, self.refresh)
        self.vibe_group.pack(side="left", fill="y", padx=8)

        right = ttk.Frame(filters)
        right.pack(side="left", fill="y", padx=8)
        self.energy_group = CheckGroup(right, "Energy", ENERGIES, self.refresh)
        self.energy_group.pack(fill="x")
        self.mood_group = CheckGroup(right, "Moods", MOODS, self.refresh)
        self.mood_group.pack(fill="x", pady=(8, 0))

        artists = sorted({r["artist"] for r in self.rows if r["artist"]})
        albums = sorted({r["album"] for r in self.rows if r["album"]})
        self.artist_group = SearchableListGroup(filters, "Artists", artists, self.refresh)
        self.artist_group.pack(side="left", fill="y", padx=8)
        self.album_group = SearchableListGroup(filters, "Albums", albums, self.refresh)
        self.album_group.pack(side="left", fill="y", padx=8)

        # --- middle: controls ---
        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=8)
        ttk.Button(controls, text="Clear all filters",
                   command=self.clear_all).pack(side="left")
        self.create_btn = ttk.Button(controls, text="Create Spotify playlist…",
                                      command=self.create_playlist)
        self.create_btn.pack(side="left", padx=(8, 0))
        ttk.Label(controls, text="   Match:").pack(side="left")
        self.match_mode = tk.StringVar(value="any")
        ttk.Radiobutton(controls, text="Any selected", value="any",
                        variable=self.match_mode, command=self.refresh).pack(side="left")
        ttk.Radiobutton(controls, text="All categories", value="all",
                        variable=self.match_mode, command=self.refresh).pack(side="left")
        self.count_var = tk.StringVar()
        ttk.Label(controls, textvariable=self.count_var,
                  font=("TkDefaultFont", 10, "bold")).pack(side="right")

        # --- bottom: results table ---
        cols = ("artist", "title", "album", "genres", "energy", "vibes")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=18)
        widths = {"artist": 150, "title": 200, "album": 170,
                  "genres": 150, "energy": 60, "vibes": 180}
        for c in cols:
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=widths[c], anchor="w")
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        if not self.rows:
            messagebox.showinfo(
                "No data yet",
                "No classified tracks found.\n\nRun this first:\n"
                "    conda activate Spotify\n    python cli.py all",
            )
        self.refresh()

    # --- filtering ---------------------------------------------------------
    def clear_all(self) -> None:
        for group in (self.genre_group, self.vibe_group, self.energy_group, self.mood_group):
            for var in group.vars.values():
                var.set(False)
        self.artist_group.clear()
        self.album_group.clear()
        self.refresh()

    def _matches(self, row: dict, sel_g, sel_v, sel_e, sel_m, sel_ar, sel_al) -> bool:
        any_mode = self.match_mode.get() == "any"
        checks = []
        if sel_g:
            checks.append(bool(sel_g & set(row["genres"])))
        if sel_v:
            checks.append(bool(sel_v & set(row["vibes"])))
        if sel_e:
            checks.append(row["energy"] in sel_e)
        if sel_m:
            checks.append(bool(sel_m & set(row["moods"])))
        if sel_ar:
            checks.append(row["artist"] in sel_ar)
        if sel_al:
            checks.append(row["album"] in sel_al)
        if not checks:
            return True  # nothing selected -> show everything
        return any(checks) if any_mode else all(checks)

    def refresh(self) -> None:
        sel_g = self.genre_group.selected()
        sel_v = self.vibe_group.selected()
        sel_e = self.energy_group.selected()
        sel_m = self.mood_group.selected()
        sel_ar = self.artist_group.selected()
        sel_al = self.album_group.selected()

        self.tree.delete(*self.tree.get_children())
        self.shown_rows = [
            row for row in self.rows
            if self._matches(row, sel_g, sel_v, sel_e, sel_m, sel_ar, sel_al)
        ]
        for row in self.shown_rows:
            self.tree.insert("", "end", values=(
                row["artist"], row["title"], row["album"], ", ".join(row["genres"]),
                row["energy"] or "?", ", ".join(row["vibes"]),
            ))
        self.count_var.set(f"{len(self.shown_rows)} / {len(self.rows)} tracks")

    # --- playlist creation -------------------------------------------------
    def _suggest_name(self) -> str:
        parts = (list(self.vibe_group.selected()) + list(self.genre_group.selected())
                 + list(self.energy_group.selected()) + list(self.mood_group.selected()))
        return " ".join(parts) if parts else "Filtered"

    def create_playlist(self) -> None:
        tracks = list(self.shown_rows)
        if not tracks:
            messagebox.showwarning("Nothing to add", "No tracks match the current filters.")
            return
        name = simpledialog.askstring(
            "Create Spotify playlist",
            f"{len(tracks)} track(s) will be added.\nPlaylist name "
            f"(prefix '{config.PLAYLIST_PREFIX}' is added automatically):",
            initialvalue=self._suggest_name(), parent=self,
        )
        if not name:
            return
        track_ids = [t["id"] for t in tracks]
        self.create_btn.config(state="disabled", text="Creating…")

        def worker():
            try:
                import spotify_client as spc
                import playlists
                sp = spc.get_client()
                full = playlists.create_named_playlist(sp, name.strip(), track_ids)
                self.after(0, lambda: self._create_done(full, len(track_ids), None))
            except Exception as exc:  # noqa: BLE001 - surface any failure to the user
                self.after(0, lambda: self._create_done(None, 0, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _create_done(self, full: str | None, count: int, exc: Exception | None) -> None:
        self.create_btn.config(state="normal", text="Create Spotify playlist…")
        if exc is not None:
            messagebox.showerror("Playlist failed", str(exc))
        else:
            messagebox.showinfo("Playlist ready", f"'{full}' now has {count} track(s).")


def main() -> None:
    root = tk.Tk()
    root.title("Spotify Liked-Songs — Genre / Vibe Browser")
    root.geometry("1480x720")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
