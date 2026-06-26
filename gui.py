"""Desktop GUI for browsing your classified library.

Shows every possible genre, vibe, energy band, and mood (straight from config.py),
plus searchable scrollable lists of artists and albums (which cross-filter each
other). Every option is tri-state — click cycles off → include (✓) → exclude (✗) —
so you can build "NOT" queries (e.g. include Hip-hop/Rap but exclude a given
artist). Excludes are hard filters; includes combine via the Any/All match mode.
The table updates live as you click.

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


# --- tri-state selection: each option is off / include (✓) / exclude (✗) -----
INCLUDE, EXCLUDE = "include", "exclude"
_MARK = {None: "☐", INCLUDE: "✓", EXCLUDE: "✗"}
_FG = {None: "gray40", INCLUDE: "#1a7f37", EXCLUDE: "#cf222e"}


class TriState:
    """Per-option state: absent (off), 'include', or 'exclude'. Clicking cycles."""

    def __init__(self):
        self.state: dict[str, str] = {}

    def cycle(self, opt: str) -> None:
        s = self.state.get(opt)
        nxt = INCLUDE if s is None else (EXCLUDE if s == INCLUDE else None)
        if nxt is None:
            self.state.pop(opt, None)
        else:
            self.state[opt] = nxt

    def get(self, opt: str):
        return self.state.get(opt)

    def included(self) -> set[str]:
        return {o for o, s in self.state.items() if s == INCLUDE}

    def excluded(self) -> set[str]:
        return {o for o, s in self.state.items() if s == EXCLUDE}

    def clear(self) -> None:
        self.state.clear()


def _make_tri_button(parent, option, tri, on_change, label=None):
    """A flat, left-aligned row button that cycles tri-state on click.
    Returns (button, redraw) where redraw() refreshes its marker/colour."""
    btn = tk.Button(parent, anchor="w", relief="flat", bd=0, highlightthickness=0,
                    padx=2, pady=0, takefocus=0)

    def redraw():
        s = tri.get(option)
        btn.config(text=f"{_MARK[s]} {label or option}", fg=_FG[s])

    def click():
        tri.cycle(option)
        redraw()
        on_change()

    btn.config(command=click)
    redraw()
    return btn, redraw


def row_matches(row: dict, inc: dict, exc: dict, any_mode: bool) -> bool:
    """Pure filter test. `inc`/`exc` map category keys (g,v,e,m,ar,al) to sets of
    included/excluded values. Excludes are hard (any hit removes the row);
    includes combine via any_mode (Any selected) or all (All categories)."""
    if exc["g"] & set(row["genres"]):
        return False
    if exc["v"] & set(row["vibes"]):
        return False
    if row["energy"] in exc["e"]:
        return False
    if exc["m"] & set(row["moods"]):
        return False
    if row["artist"] in exc["ar"]:
        return False
    if row["album"] in exc["al"]:
        return False
    checks = []
    if inc["g"]:
        checks.append(bool(inc["g"] & set(row["genres"])))
    if inc["v"]:
        checks.append(bool(inc["v"] & set(row["vibes"])))
    if inc["e"]:
        checks.append(row["energy"] in inc["e"])
    if inc["m"]:
        checks.append(bool(inc["m"] & set(row["moods"])))
    if inc["ar"]:
        checks.append(row["artist"] in inc["ar"])
    if inc["al"]:
        checks.append(row["album"] in inc["al"])
    if not checks:
        return True  # nothing included -> keep (subject to excludes above)
    return any(checks) if any_mode else all(checks)


class CheckGroup(ttk.LabelFrame):
    """A labelled frame of tri-state rows (off / include / exclude)."""

    def __init__(self, master, title: str, options: list[str], on_change):
        super().__init__(master, text=title, padding=8)
        self.on_change = on_change
        self.tri = TriState()
        self._redraws = []
        for opt in options:
            btn, redraw = _make_tri_button(self, opt, self.tri, on_change,
                                           label=(opt or "(none)"))
            btn.pack(anchor="w", fill="x")
            self._redraws.append(redraw)
        ttk.Button(self, text="None", width=6, command=self.clear).pack(anchor="w", pady=(6, 0))

    def _redraw_all(self) -> None:
        for r in self._redraws:
            r()

    def reset(self) -> None:
        """Clear without firing on_change (used by 'Clear all filters')."""
        self.tri.clear()
        self._redraw_all()

    def clear(self) -> None:
        self.reset()
        self.on_change()

    def included(self) -> set[str]:
        return self.tri.included()

    def excluded(self) -> set[str]:
        return self.tri.excluded()


class CheckListGroup(ttk.LabelFrame):
    """A scrollable list of checkboxes (one per option) with a search box, for
    high-cardinality fields like artists/albums. Tick boxes to select; selections
    persist while you search. Selected items are shown first; the visible list is
    capped for responsiveness (narrow it by typing)."""

    RENDER_CAP = 300  # max checkboxes drawn at once (selected ones always shown)

    def __init__(self, master, title: str, options: list[str], on_change,
                 width: int = 26, canvas_height: int = 210):
        super().__init__(master, text=title, padding=6)
        self.on_change = on_change
        self.all_options = sorted(o for o in options if o)
        self.tri = TriState()  # off / include / exclude per option
        self.allowed: set[str] | None = None  # cross-filter restriction (None = all)

        self.search_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.search_var, width=width).pack(fill="x")
        self.search_var.trace_add("write", lambda *_: self._render())

        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x", pady=(4, 0))
        self.count_lbl = ttk.Label(bar, text="")
        self.count_lbl.pack(side="left")
        ttk.Button(bar, text="Clear", width=6, command=self.clear).pack(side="right")

        mid = ttk.Frame(self)
        mid.pack(side="top", fill="both", expand=True, pady=(4, 0))
        self.canvas = tk.Canvas(mid, height=canvas_height, highlightthickness=0,
                                width=width * 8)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfig(self._win, width=e.width))
        # mouse-wheel scrolling only while the pointer is over this list
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel())
        self.canvas.bind("<Leave>", lambda _e: self._unbind_wheel())

        self._render()

    def _bind_wheel(self) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)   # Windows / macOS
        self.canvas.bind_all("<Button-4>", self._on_wheel)     # Linux scroll up
        self.canvas.bind_all("<Button-5>", self._on_wheel)     # Linux scroll down

    def _unbind_wheel(self) -> None:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(seq)

    def _on_wheel(self, event) -> None:
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            self.canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            self.canvas.yview_scroll(1, "units")

    def set_allowed(self, allowed: set[str] | None) -> None:
        """Restrict which options are offered (cross-filter from another panel).
        Does not fire on_change; re-renders in place."""
        self.allowed = allowed
        self._render()

    def _visible(self, o: str, q: str) -> bool:
        active = self.tri.get(o) is not None
        if q and q not in o.lower():
            return False
        # cross-filter hides options not allowed — unless already include/exclude
        if self.allowed is not None and o not in self.allowed and not active:
            return False
        return True

    def _render(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        q = self.search_var.get().strip().lower()
        matches = [o for o in self.all_options if self._visible(o, q)]
        # active (include/exclude) first so they're never hidden by the cap
        matches.sort(key=lambda o: (self.tri.get(o) is None, o.lower()))
        shown = matches[: self.RENDER_CAP]
        for o in shown:
            btn, _ = _make_tri_button(self.inner, o, self.tri, self._on_toggle)
            btn.pack(anchor="w", fill="x")
        if len(matches) > len(shown):
            ttk.Label(self.inner, foreground="gray",
                      text=f"… {len(matches) - len(shown)} more — type to narrow").pack(anchor="w")
        self.canvas.yview_moveto(0)
        self._update_count()

    def _on_toggle(self) -> None:
        self._update_count()
        self.on_change()

    def _update_count(self) -> None:
        inc, exc = len(self.tri.included()), len(self.tri.excluded())
        parts = []
        if inc:
            parts.append(f"{inc} ✓")
        if exc:
            parts.append(f"{exc} ✗")
        self.count_lbl.config(text="  ".join(parts))

    def reset(self) -> None:
        """Clear without firing on_change (used by 'Clear all filters')."""
        self.tri.clear()
        self._render()

    def clear(self) -> None:
        self.reset()
        self.on_change()

    def included(self) -> set[str]:
        return self.tri.included()

    def excluded(self) -> set[str]:
        return self.tri.excluded()


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
        # cross-filter maps: which albums each artist appears on, and vice versa
        self.artist_albums: dict[str, set[str]] = {}
        self.album_artists: dict[str, set[str]] = {}
        for r in self.rows:
            a, al = r["artist"], r["album"]
            if a and al:
                self.artist_albums.setdefault(a, set()).add(al)
                self.album_artists.setdefault(al, set()).add(a)
        self.artist_group = CheckListGroup(filters, "Artists", artists, self.refresh)
        self.artist_group.pack(side="left", fill="both", padx=8)
        self.album_group = CheckListGroup(filters, "Albums", albums, self.refresh)
        self.album_group.pack(side="left", fill="both", padx=8)

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
        ttk.Label(controls, text="click cycles  ☐ off → ✓ include → ✗ exclude",
                  foreground="gray40").pack(side="right", padx=12)

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
        for group in (self.genre_group, self.vibe_group, self.energy_group,
                      self.mood_group, self.artist_group, self.album_group):
            group.reset()
        self.refresh()

    def _matches(self, row: dict, inc: dict, exc: dict) -> bool:
        return row_matches(row, inc, exc, self.match_mode.get() == "any")

    def refresh(self) -> None:
        inc = {
            "g": self.genre_group.included(), "v": self.vibe_group.included(),
            "e": self.energy_group.included(), "m": self.mood_group.included(),
            "ar": self.artist_group.included(), "al": self.album_group.included(),
        }
        exc = {
            "g": self.genre_group.excluded(), "v": self.vibe_group.excluded(),
            "e": self.energy_group.excluded(), "m": self.mood_group.excluded(),
            "ar": self.artist_group.excluded(), "al": self.album_group.excluded(),
        }

        # cross-filter the panels by INCLUDED selections only (excludes don't narrow):
        # albums limited to included artists' albums, artists to included albums' artists
        allowed_albums = set().union(*(self.artist_albums.get(a, set()) for a in inc["ar"])) \
            if inc["ar"] else None
        allowed_artists = set().union(*(self.album_artists.get(al, set()) for al in inc["al"])) \
            if inc["al"] else None
        self.album_group.set_allowed(allowed_albums)
        self.artist_group.set_allowed(allowed_artists)

        self.tree.delete(*self.tree.get_children())
        self.shown_rows = [row for row in self.rows if self._matches(row, inc, exc)]
        for row in self.shown_rows:
            self.tree.insert("", "end", values=(
                row["artist"], row["title"], row["album"], ", ".join(row["genres"]),
                row["energy"] or "?", ", ".join(row["vibes"]),
            ))
        self.count_var.set(f"{len(self.shown_rows)} / {len(self.rows)} tracks")

    # --- playlist creation -------------------------------------------------
    def _suggest_name(self) -> str:
        parts = (list(self.vibe_group.included()) + list(self.genre_group.included())
                 + list(self.energy_group.included()) + list(self.mood_group.included()))
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
