"""Desktop GUI for browsing your classified library.

Shows every possible genre, vibe, and energy band (straight from config.py),
plus searchable scrollable lists of artists and albums (which cross-filter each
other). Tick an option's checkbox to select it. Each section has an INCLUDE /
EXCLUDE button below it: in INCLUDE mode the section's ticked options are required,
in EXCLUDE mode they are filtered out — so you can build "NOT" queries (e.g. include
Hip-hop/Rap genres but flip the Artists section to EXCLUDE a given artist). Excludes
are hard filters; includes combine via the Any/All match mode. The table updates
live as you click.

On first launch (or after logout) a login screen is shown; clicking "Connect to
Spotify" opens the browser for OAuth and returns automatically.

    conda activate Spotify
    python gui.py
"""
from __future__ import annotations

import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import config
import db


# --- option lists, derived from config so they always stay in sync ---------
GENRES = list(config.GENRE_BUCKETS.keys()) + [config.DEFAULT_GENRE]
SUBGENRES = [sg for subs in config.SUBGENRE_BUCKETS.values() for sg in subs]
VIBES = list(config.VIBE_RULES.keys()) + [config.DEFAULT_VIBE]
ENERGIES = [band[2] for band in config.ENERGY_BANDS]  # low / mid / high


def load_rows() -> list[dict]:
    """Read all labelled tracks once; filtering happens in-memory."""
    try:
        db.init()
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT t.id, t.artist_name, t.name, t.album, t.preview_url, "
                "l.genre_buckets, l.subgenres, l.energy_band, l.vibes "
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
            "preview_url": r["preview_url"],
            "genres": json.loads(r["genre_buckets"] or "[]"),
            "subgenres": json.loads(r["subgenres"] or "[]"),
            "energy": r["energy_band"],
            "vibes": json.loads(r["vibes"] or "[]"),
        })
    return out


# --- selection model: each option is simply on (✓) or off; the whole section
# is interpreted as INCLUDE or EXCLUDE depending on its mode button ------------
INCLUDE, EXCLUDE = "include", "exclude"
_MODE_FG = {INCLUDE: "#1a7f37", EXCLUDE: "#cf222e"}  # green / red


class Selection:
    """Per-section state: a set of ticked options plus a mode (include/exclude).
    Every ticked option is treated according to the current mode."""

    def __init__(self):
        self.selected: set[str] = set()
        self.mode: str = INCLUDE

    def toggle(self, opt: str) -> None:
        if opt in self.selected:
            self.selected.discard(opt)
        else:
            self.selected.add(opt)

    def is_on(self, opt: str) -> bool:
        return opt in self.selected

    def flip_mode(self) -> None:
        self.mode = EXCLUDE if self.mode == INCLUDE else INCLUDE

    def included(self) -> set[str]:
        return set(self.selected) if self.mode == INCLUDE else set()

    def excluded(self) -> set[str]:
        return set(self.selected) if self.mode == EXCLUDE else set()

    def clear(self) -> None:
        self.selected.clear()


def _make_check(parent, option, sel, on_change, label=None):
    """A native checkbox row that toggles `option` in `sel` on click. Native
    widget => no unicode glyph needed (the old ✓/✗ marks didn't render in some
    Tk fonts). Returns the Checkbutton."""
    var = tk.BooleanVar(value=sel.is_on(option))

    def toggle():
        if var.get():
            sel.selected.add(option)
        else:
            sel.selected.discard(option)
        on_change()

    return ttk.Checkbutton(parent, text=label or option, variable=var,
                           command=toggle, takefocus=0)


def _make_mode_button(parent, sel, on_flip):
    """The INCLUDE / EXCLUDE toggle shown below each section. Returns
    (button, sync) where sync() refreshes its label/colour from `sel.mode`."""
    btn = tk.Button(parent, width=9, takefocus=0)

    def sync():
        colour = _MODE_FG[sel.mode]
        btn.config(text=sel.mode.upper(), fg="white", bg=colour,
                   activebackground=colour, activeforeground="white")

    btn.config(command=on_flip)
    sync()
    return btn, sync


def row_matches(row: dict, inc: dict, exc: dict, any_mode: bool) -> bool:
    """Pure filter test. `inc`/`exc` map category keys (g,sg,v,e,ar,al) to sets of
    included/excluded values. Excludes are hard (any hit removes the row);
    includes combine via any_mode (Any=OR) or all (AND). In AND mode, multi-value
    categories (genres, subgenres, vibes) require ALL selected values to be
    present on the track, not just one."""
    if exc["g"] & set(row["genres"]):
        return False
    if exc["sg"] & set(row["subgenres"]):
        return False
    if exc["v"] & set(row["vibes"]):
        return False
    if row["energy"] in exc["e"]:
        return False
    if row["artist"] in exc["ar"]:
        return False
    if row["album"] in exc["al"]:
        return False
    checks = []
    if any_mode:
        if inc["g"]:
            checks.append(bool(inc["g"] & set(row["genres"])))
        if inc["sg"]:
            checks.append(bool(inc["sg"] & set(row["subgenres"])))
        if inc["v"]:
            checks.append(bool(inc["v"] & set(row["vibes"])))
        if inc["e"]:
            checks.append(row["energy"] in inc["e"])
        if inc["ar"]:
            checks.append(row["artist"] in inc["ar"])
        if inc["al"]:
            checks.append(row["album"] in inc["al"])
        if not checks:
            return True
        return any(checks)
    else:
        if inc["g"]:
            checks.append(inc["g"] <= set(row["genres"]))
        if inc["sg"]:
            checks.append(inc["sg"] <= set(row["subgenres"]))
        if inc["v"]:
            checks.append(inc["v"] <= set(row["vibes"]))
        if inc["e"]:
            checks.append(row["energy"] in inc["e"])
        if inc["ar"]:
            checks.append(row["artist"] in inc["ar"])
        if inc["al"]:
            checks.append(row["album"] in inc["al"])
        if not checks:
            return True
        return all(checks)


class CheckGroup(ttk.LabelFrame):
    """A labelled frame of on/off toggle rows with an INCLUDE/EXCLUDE mode
    button beneath them that decides how the ticked options are applied."""

    def __init__(self, master, title: str, options: list[str], on_change):
        super().__init__(master, text=title, padding=8)
        self.on_change = on_change
        self.sel = Selection()
        self._vars: dict[str, tk.BooleanVar] = {}
        for opt in options:
            var = tk.BooleanVar(value=False)
            self._vars[opt] = var

            def toggle(o=opt, v=var):
                if v.get():
                    self.sel.selected.add(o)
                else:
                    self.sel.selected.discard(o)
                self.on_change()

            ttk.Checkbutton(self, text=(opt or "(none)"), variable=var,
                            command=toggle, takefocus=0).pack(anchor="w", fill="x")
        bar = ttk.Frame(self)
        bar.pack(anchor="w", fill="x", pady=(6, 0))
        self.mode_btn, self._sync_mode = _make_mode_button(bar, self.sel, self.flip_mode)
        self.mode_btn.pack(side="left")
        ttk.Button(bar, text="None", width=6, command=self.clear).pack(side="left", padx=(4, 0))

    def flip_mode(self) -> None:
        self.sel.flip_mode()
        self._sync_mode()
        self.on_change()  # checkboxes don't change; only interpretation does

    def reset(self) -> None:
        """Clear ticks without firing on_change (used by 'Clear all filters')."""
        self.sel.clear()
        for v in self._vars.values():
            v.set(False)

    def clear(self) -> None:
        self.reset()
        self.on_change()

    def included(self) -> set[str]:
        return self.sel.included()

    def excluded(self) -> set[str]:
        return self.sel.excluded()


class CheckListGroup(ttk.LabelFrame):
    """A scrollable list of checkboxes (one per option) with a search box, for
    high-cardinality fields like artists/albums. Tick boxes to select; selections
    persist while you search. Selected items are shown first; the visible list is
    capped for responsiveness (narrow it by typing)."""

    RENDER_CAP = 200  # max checkboxes drawn at once (selected ones always shown)

    def __init__(self, master, title: str, options: list[str], on_change,
                 width: int = 26, canvas_height: int = 210):
        super().__init__(master, text=title, padding=6)
        self.on_change = on_change
        self.all_options = sorted(o for o in options if o)
        self.sel = Selection()  # ticked options + include/exclude mode
        self.allowed: set[str] | None = None  # cross-filter restriction (None = all)

        self.search_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.search_var, width=width).pack(fill="x")
        self.search_var.trace_add("write", lambda *_: self._render())

        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x", pady=(4, 0))
        self.mode_btn, self._sync_mode = _make_mode_button(bar, self.sel, self.flip_mode)
        self.mode_btn.pack(side="left")
        self.count_lbl = ttk.Label(bar, text="")
        self.count_lbl.pack(side="left", padx=(6, 0))
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
        Does not fire on_change; re-renders only when the restriction actually
        changed (re-rendering hundreds of widgets on every click was the lag)."""
        if allowed == self.allowed:
            return
        self.allowed = allowed
        self._render()

    def _visible(self, o: str, q: str) -> bool:
        active = self.sel.is_on(o)
        if q and not o.lower().startswith(q):
            return False
        # cross-filter hides options not allowed — unless already ticked
        if self.allowed is not None and o not in self.allowed and not active:
            return False
        return True

    def _render(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        q = self.search_var.get().strip().lower()
        matches = [o for o in self.all_options if self._visible(o, q)]
        # ticked options first so they're never hidden by the cap
        matches.sort(key=lambda o: (not self.sel.is_on(o), o.lower()))
        shown = matches[: self.RENDER_CAP]
        for o in shown:
            _make_check(self.inner, o, self.sel, self._on_toggle).pack(anchor="w", fill="x")
        if len(matches) > len(shown):
            ttk.Label(self.inner, foreground="gray",
                      text=f"… {len(matches) - len(shown)} more — type to narrow").pack(anchor="w")
        self.canvas.yview_moveto(0)
        self._update_count()

    def _on_toggle(self) -> None:
        self._update_count()
        self.on_change()

    def flip_mode(self) -> None:
        self.sel.flip_mode()
        self._sync_mode()
        self.on_change()  # checkboxes unchanged; only interpretation flips

    def _update_count(self) -> None:
        n = len(self.sel.selected)
        self.count_lbl.config(text=f"{n} ticked" if n else "")

    def reset(self) -> None:
        """Clear ticks without firing on_change (used by 'Clear all filters')."""
        self.sel.clear()
        self._render()

    def clear(self) -> None:
        self.reset()
        self.on_change()

    def included(self) -> set[str]:
        return self.sel.included()

    def excluded(self) -> set[str]:
        return self.sel.excluded()


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.pack(fill="both", expand=True)
        self.rows = load_rows()
        self._player = _PreviewPlayer()
        self._row_by_iid: dict[str, dict] = {}

        # cross-filter maps built once from all rows
        artists = sorted({r["artist"] for r in self.rows if r["artist"]})
        albums  = sorted({r["album"]  for r in self.rows if r["album"]})
        self.artist_albums: dict[str, set[str]] = {}
        self.album_artists: dict[str, set[str]] = {}
        for r in self.rows:
            a, al = r["artist"], r["album"]
            if a and al:
                self.artist_albums.setdefault(a, set()).add(al)
                self.album_artists.setdefault(al, set()).add(a)

        # --- filter notebook (tabs) ---
        nb = ttk.Notebook(self)
        nb.pack(fill="x", pady=(0, 6))

        # Tab 1: Genres & Vibes
        tab_gv = ttk.Frame(nb, padding=8)
        nb.add(tab_gv, text="  Genres & Vibes  ")
        self.genre_group = CheckGroup(tab_gv, "Genres", GENRES, self.refresh)
        self.genre_group.pack(side="left", fill="y", padx=(0, 10))
        self.subgenre_group = CheckListGroup(tab_gv, "Subgenres", SUBGENRES, self.refresh,
                                             width=24, canvas_height=180)
        self.subgenre_group.pack(side="left", fill="both", padx=(0, 10))
        self.vibe_group = CheckGroup(tab_gv, "Vibes", VIBES, self.refresh)
        self.vibe_group.pack(side="left", fill="y", padx=(0, 10))
        self.energy_group = CheckGroup(tab_gv, "Energy", ENERGIES, self.refresh)
        self.energy_group.pack(side="left", fill="y")

        # Tab 2: Artists
        tab_ar = ttk.Frame(nb, padding=8)
        nb.add(tab_ar, text="  Artists  ")
        self.artist_group = CheckListGroup(tab_ar, "Artists", artists, self.refresh,
                                           width=36, canvas_height=180)
        self.artist_group.pack(fill="both", expand=True)

        # Tab 3: Albums
        tab_al = ttk.Frame(nb, padding=8)
        nb.add(tab_al, text="  Albums  ")
        self.album_group = CheckListGroup(tab_al, "Albums", albums, self.refresh,
                                          width=36, canvas_height=180)
        self.album_group.pack(fill="both", expand=True)

        # --- controls bar ---
        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(0, 6))

        ttk.Button(controls, text="Clear all filters",
                   command=self.clear_all).pack(side="left")
        ttk.Button(controls, text="Sync library",
                   command=self._sync).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Log out",
                   command=self._logout).pack(side="left", padx=(8, 0))
        self.create_btn = ttk.Button(controls, text="Create playlist…",
                                     command=self.create_playlist)
        self.create_btn.pack(side="left", padx=(8, 0))
        self.classify_btn = ttk.Button(controls, text="Classify track…",
                                       command=self.classify_track)
        self.classify_btn.pack(side="left", padx=(8, 0))
        self.classify_lib_btn = ttk.Button(controls, text="Classify library…",
                                           command=self.classify_library)
        self.classify_lib_btn.pack(side="left", padx=(8, 0))

        ttk.Label(controls, text="Match:").pack(side="left", padx=(16, 4))
        self.match_mode = tk.StringVar(value="any")
        ttk.Radiobutton(controls, text="Any", value="any",
                        variable=self.match_mode, command=self.refresh).pack(side="left")
        ttk.Radiobutton(controls, text="All", value="all",
                        variable=self.match_mode, command=self.refresh).pack(side="left", padx=(4, 0))

        # search box (right-aligned)
        ttk.Label(controls, text="Search:").pack(side="right", padx=(0, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        ttk.Entry(controls, textvariable=self.search_var, width=26).pack(side="right")

        self.count_var = tk.StringVar()
        ttk.Label(controls, textvariable=self.count_var,
                  font=("TkDefaultFont", 10, "bold")).pack(side="right", padx=(0, 16))

        # --- results table ---
        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True)

        cols = ("play", "artist", "title", "album", "genres", "subgenres", "energy", "vibes")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=16)
        widths = {"play": 30, "artist": 150, "title": 200, "album": 160,
                  "genres": 140, "subgenres": 140, "energy": 55, "vibes": 170}
        headings = {"play": "▶", "artist": "Artist", "title": "Title", "album": "Album",
                    "genres": "Genres", "subgenres": "Subgenres", "energy": "Energy",
                    "vibes": "Vibes"}
        for c in cols:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], anchor="w", stretch=(c != "play"))
        self.tree.column("play", anchor="center", stretch=False)

        self.tree.tag_configure("playing", background="#d4edda")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        self.tree.bind("<Double-1>", self._on_double_click)

        # --- status bar ---
        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var,
                  foreground="gray40", font=("TkDefaultFont", 9)).pack(
            anchor="w", pady=(4, 0))

        self.refresh()

    # --- preview playback --------------------------------------------------
    def _on_double_click(self, event) -> None:
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        track = self._row_by_iid.get(row_id)
        if not track:
            return
        if not self._player.available:
            self.status_var.set("Install pygame to enable preview playback.")
            return
        if not track.get("preview_url"):
            self.status_var.set(f"No preview available for {track['artist']} — {track['title']}.")
            return
        self._player.toggle(track["id"], track["preview_url"],
                            lambda: self.after(0, self._update_player_ui))

    def _update_player_ui(self) -> None:
        pid = self._player.playing_id
        for iid, track in self._row_by_iid.items():
            if track["id"] == pid:
                self.tree.item(iid, tags=("playing",))
                self.status_var.set(f"▶  {track['artist']} — {track['title']}   (double-click to stop)")
            else:
                self.tree.item(iid, tags=())
        if pid is None:
            self.status_var.set("")

    # --- sync / logout -----------------------------------------------------
    def _sync(self) -> None:
        _offer_sync(self.winfo_toplevel(), self)

    def _logout(self) -> None:
        if not messagebox.askyesno("Log out", "Disconnect your Spotify account?"):
            return
        self._player.stop()
        import spotify_client as spc
        spc.logout()
        root = self.winfo_toplevel()
        for w in root.winfo_children():
            w.destroy()
        LoginFrame(root, on_success=lambda: _relaunch(root))

    # --- filtering ---------------------------------------------------------
    def clear_all(self) -> None:
        for group in (self.genre_group, self.subgenre_group, self.vibe_group,
                      self.energy_group, self.artist_group, self.album_group):
            group.reset()
        self.refresh()

    def _matches(self, row: dict, inc: dict, exc: dict) -> bool:
        return row_matches(row, inc, exc, self.match_mode.get() == "any")

    def refresh(self) -> None:
        inc = {
            "g": self.genre_group.included(), "sg": self.subgenre_group.included(),
            "v": self.vibe_group.included(),
            "e": self.energy_group.included(),
            "ar": self.artist_group.included(), "al": self.album_group.included(),
        }
        exc = {
            "g": self.genre_group.excluded(), "sg": self.subgenre_group.excluded(),
            "v": self.vibe_group.excluded(),
            "e": self.energy_group.excluded(),
            "ar": self.artist_group.excluded(), "al": self.album_group.excluded(),
        }

        # cross-filter subgenres by ticked genres (regardless of include/exclude mode)
        ticked_genres = self.genre_group.sel.selected
        if ticked_genres:
            allowed_subgenres = set().union(
                *(config.SUBGENRE_BUCKETS.get(g, []) for g in ticked_genres)
            )
        else:
            allowed_subgenres = set()  # hide all subgenres until a genre is chosen
        self.subgenre_group.set_allowed(allowed_subgenres)

        # cross-filter the panels by INCLUDED selections only (excludes don't narrow):
        # albums limited to included artists' albums, artists to included albums' artists
        allowed_albums = set().union(*(self.artist_albums.get(a, set()) for a in inc["ar"])) \
            if inc["ar"] else None
        allowed_artists = set().union(*(self.album_artists.get(al, set()) for al in inc["al"])) \
            if inc["al"] else None
        self.album_group.set_allowed(allowed_albums)
        self.artist_group.set_allowed(allowed_artists)

        q = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        self._row_by_iid: dict[str, dict] = {}
        self.shown_rows = [
            row for row in self.rows
            if self._matches(row, inc, exc) and self._search_matches(row, q)
        ]
        pid = self._player.playing_id
        for row in self.shown_rows:
            subgenres = ", ".join(row["subgenres"]) if row["subgenres"] else ", ".join(row["genres"])
            play_marker = "▶" if row["id"] == pid else ("♪" if row.get("preview_url") else "")
            tags = ("playing",) if row["id"] == pid else ()
            iid = self.tree.insert("", "end", tags=tags, values=(
                play_marker, row["artist"], row["title"], row["album"],
                ", ".join(row["genres"]), subgenres,
                row["energy"] or "?", ", ".join(row["vibes"]),
            ))
            self._row_by_iid[iid] = row
        self.count_var.set(f"{len(self.shown_rows)} / {len(self.rows)} tracks")

    def _search_matches(self, row: dict, q: str) -> bool:
        if not q:
            return True
        return q in row["artist"].lower() or q in row["title"].lower()

    # --- playlist creation -------------------------------------------------
    def _ask_string(self, title: str, prompt: str, initial: str = "") -> str | None:
        """Modal text prompt. Replaces simpledialog.askstring, which crashes on
        Python 3.14 / some window managers (wait_visibility on a window the WM
        deleted: 'window was deleted before its visibility changed')."""
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self.winfo_toplevel())
        win.resizable(False, False)
        result: dict[str, str | None] = {"value": None}

        ttk.Label(win, text=prompt, justify="left").pack(
            anchor="w", padx=12, pady=(12, 6))
        var = tk.StringVar(value=initial)
        entry = ttk.Entry(win, textvariable=var, width=40)
        entry.pack(fill="x", padx=12)
        entry.select_range(0, "end")

        def ok(_e=None):
            result["value"] = var.get()
            win.destroy()

        def cancel(_e=None):
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=12)
        ttk.Button(btns, text="OK", command=ok).pack(side="right")
        ttk.Button(btns, text="Cancel", command=cancel).pack(side="right", padx=(0, 6))
        entry.bind("<Return>", ok)
        win.bind("<Escape>", cancel)

        entry.focus_set()
        win.grab_set()
        self.wait_window(win)
        return result["value"]

    def _suggest_name(self) -> str:
        parts = (list(self.vibe_group.included()) + list(self.genre_group.included())
                 + list(self.energy_group.included()))
        return " ".join(parts) if parts else "Filtered"

    def create_playlist(self) -> None:
        tracks = list(self.shown_rows)
        if not tracks:
            messagebox.showwarning("Nothing to add", "No tracks match the current filters.")
            return
        name = self._ask_string(
            "Create Spotify playlist",
            f"{len(tracks)} track(s) will be added.\nPlaylist name "
            f"(prefix '{config.PLAYLIST_PREFIX}' is added automatically):",
            initial=self._suggest_name(),
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

    # --- genre-specification classify (off the UI thread) -----------------
    def _selected_track_seed(self) -> str:
        """Prefill text for the classify prompt from the selected table row."""
        sel = self.tree.selection()
        if sel:
            vals = self.tree.item(sel[0], "values")
            if vals:
                return f"{vals[0]} - {vals[1]}"  # "artist - title"
        return ""

    def classify_track(self) -> None:
        """Classify one track (selected row or typed 'artist - title') and show
        the stored result. Network + LLM run on a worker thread."""
        seed = self._selected_track_seed()
        spec = self._ask_string(
            "Classify track",
            "Enter a track as  artist - title  (or an ISRC, or a Spotify track ID).\n"
            "Resolves to ISRC, fetches features, and classifies with Claude.",
            initial=seed,
        )
        if not spec:
            return
        spec = spec.strip()
        self.classify_btn.config(state="disabled", text="Classifying…")

        def worker():
            try:
                import genreclass as gp
                import text_utils
                pipe = gp.build_default_pipeline()
                if text_utils.is_real_isrc(spec):
                    ti = gp.TrackInput(isrc=spec)
                elif " " not in spec and len(spec) == 22:  # looks like a Spotify id
                    ti = gp.TrackInput(spotify_id=spec)
                else:
                    artist, title = gp.parse_track_arg(spec)
                    ti = gp.TrackInput(artist=artist, title=title)
                row = pipe.classify_track(ti)
                self.after(0, lambda: self._classify_done(row, None))
            except Exception as exc:  # noqa: BLE001 - surface failures to the user
                self.after(0, lambda: self._classify_done(None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _classify_done(self, row: dict | None, exc: Exception | None) -> None:
        self.classify_btn.config(state="normal", text="Classify track…")
        if exc is not None:
            messagebox.showerror("Classification failed", str(exc))
            return
        import genreclass as gp
        messagebox.showinfo("Classification stored",
                            "\n".join(gp.format_result_lines(row)))

    def classify_library(self) -> None:
        """Batch-classify the whole library off the UI thread, with a live
        progress window. Resumable — skips already-classified tracks."""
        if not messagebox.askyesno(
            "Classify library",
            "Classify every library track (genre/subgenre/energy/vibe)?\n\n"
            "Already-classified tracks are skipped. This calls ReccoBeats, "
            "Deezer and Claude and may take a while; it's safe to close the "
            "window — progress is saved per track.",
        ):
            return
        self.classify_lib_btn.config(state="disabled", text="Classifying…")
        prog = tk.Toplevel(self)
        prog.title("Classifying library…")
        prog.transient(self.winfo_toplevel())
        status = tk.StringVar(value="Starting…")
        ttk.Label(prog, textvariable=status).pack(padx=20, pady=10)
        pbar = ttk.Progressbar(prog, length=320, mode="determinate")
        pbar.pack(padx=20, pady=(0, 12))

        def progress(done, total):
            def upd():
                pbar.config(maximum=max(total, 1), value=done)
                status.set(f"{done} / {total} tracks")
            self.after(0, upd)

        def worker():
            try:
                import genreclass as gp
                stats = gp.build_default_pipeline().classify_library(progress=progress)
                self.after(0, lambda: self._classify_lib_done(prog, stats, None))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._classify_lib_done(prog, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _classify_lib_done(self, prog, stats, exc: Exception | None) -> None:
        self.classify_lib_btn.config(state="normal", text="Classify library…")
        try:
            prog.destroy()
        except Exception:  # noqa: BLE001
            pass
        if exc is not None:
            messagebox.showerror("Batch classification failed", str(exc))
            return
        messagebox.showinfo("Library classified", "\n".join(stats.summary_lines()))


_PREVIEW_CACHE = str(config.DATA_DIR / ".preview_cache.mp3")


class _PreviewPlayer:
    """Download and play a 30-second Spotify preview clip (one at a time).

    Uses pygame.mixer when available; silently disabled otherwise so the rest
    of the GUI is unaffected if pygame isn't installed.
    """

    def __init__(self):
        self.playing_id: str | None = None
        try:
            import pygame
            pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=1024)
            pygame.mixer.init()
            self._pg = pygame
        except Exception:
            self._pg = None

    @property
    def available(self) -> bool:
        return self._pg is not None

    def toggle(self, track_id: str, url: str | None, on_change) -> None:
        """Play the preview, or stop it if this track is already playing."""
        if not self._pg:
            return
        if self.playing_id == track_id:
            self._pg.mixer.music.stop()
            self.playing_id = None
            on_change()
            return
        self.playing_id = track_id
        on_change()
        if not url:
            self.playing_id = None
            on_change()
            return
        threading.Thread(target=self._fetch_and_play,
                         args=(track_id, url, on_change), daemon=True).start()

    def stop(self) -> None:
        if self._pg:
            self._pg.mixer.music.stop()
        self.playing_id = None

    def _fetch_and_play(self, track_id: str, url: str, on_change) -> None:
        import time
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = resp.read()
            if self.playing_id != track_id:
                return
            self._pg.mixer.music.stop()
            with open(_PREVIEW_CACHE, "wb") as f:
                f.write(data)
            self._pg.mixer.music.load(_PREVIEW_CACHE)
            self._pg.mixer.music.play()
            while self._pg.mixer.music.get_busy() and self.playing_id == track_id:
                time.sleep(0.1)
        except Exception:
            pass
        if self.playing_id == track_id:
            self.playing_id = None
            on_change()


def _relaunch(root: tk.Tk) -> None:
    for w in root.winfo_children():
        w.destroy()
    app = App(root)
    root.after(100, lambda: _offer_sync(root, app))


class LoginFrame(ttk.Frame):
    """Shown on first launch or after logout. Guides the user through OAuth."""

    def __init__(self, master, on_success):
        super().__init__(master, padding=40)
        self.on_success = on_success
        self.pack(fill="both", expand=True)

        ttk.Label(self, text="VibeThePlaylist",
                  font=("TkDefaultFont", 28, "bold")).pack(pady=(80, 10))
        ttk.Label(self, text="Browse and playlist your Spotify library by genre, energy, and vibe.",
                  font=("TkDefaultFont", 12)).pack(pady=(0, 50))

        self.connect_btn = ttk.Button(self, text="Connect to Spotify", command=self._connect)
        self.connect_btn.pack(ipadx=24, ipady=10)

        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var,
                  foreground="gray40").pack(pady=(14, 0))

    def _connect(self) -> None:
        self.connect_btn.config(state="disabled")
        self.status_var.set("Opening browser — please log in to Spotify…")
        threading.Thread(target=self._auth_worker, daemon=True).start()

    def _auth_worker(self) -> None:
        try:
            import spotify_client as spc
            sp = spc.get_client_pkce()
            sp.current_user()  # forces the token exchange
            self.after(0, self.on_success)
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda: self._failed(str(exc)))

    def _failed(self, msg: str) -> None:
        self.connect_btn.config(state="normal")
        self.status_var.set(f"Connection failed: {msg}")


# ---------------------------------------------------------------------------
# Pipeline runner (fetch → enrich → classify) called from the GUI
# ---------------------------------------------------------------------------

def _run_pipeline(status_cb) -> None:
    """Run the full sync pipeline on the caller's thread (must be a worker thread)."""
    import classify
    import db
    import enrich
    import spotify_client as spc

    db.init()
    status_cb("Connecting to Spotify…")
    sp = spc.get_client_pkce()

    status_cb("Fetching liked songs…")
    known = db.all_track_ids()
    new = [t for t in spc.iter_liked_tracks(sp) if t["id"] not in known]
    db.upsert_tracks(new)

    status_cb("Enriching: artist genres…")
    caps = spc.probe_capabilities(sp)
    db.set_meta("audio_features_available", "1" if caps["audio_features"] else "0")
    need_artists = sorted(db.all_artist_ids() - db.known_artist_ids())
    if need_artists:
        db.upsert_artists(spc.fetch_artists(sp, need_artists))

    if caps["audio_features"]:
        missing = sorted(db.track_ids_missing_features())
        if missing:
            status_cb(f"Fetching audio features for {len(missing)} tracks…")
            db.upsert_features(spc.fetch_audio_features(sp, missing))

    if enrich.has_lastfm():
        missing = sorted(db.track_ids_missing_tags())
        if missing:
            status_cb(f"Fetching Last.fm tags for {len(missing)} tracks…")
            with db.connect() as conn:
                meta = {
                    r["id"]: (r["artist_name"], r["name"])
                    for r in conn.execute("SELECT id, artist_name, name FROM tracks")
                    if r["id"] in missing
                }
            rows = []
            for tid in missing:
                artist, name = meta[tid]
                found = enrich.fetch_track_tags(artist, name)
                for tag, weight in found:
                    rows.append({"track_id": tid, "tag": tag, "source": "lastfm", "weight": weight})
                if not found:
                    rows.append({"track_id": tid, "tag": "__none__", "source": "lastfm", "weight": 0})
            db.upsert_tags(rows)

    status_cb("Classifying…")
    classify.classify_all()


def _offer_sync(root: tk.Tk, app: "App") -> None:
    """After login, ask the user if they want to sync now and run the pipeline."""
    if not messagebox.askyesno(
        "Sync your library",
        "Would you like to sync your Spotify liked songs now?\n\n"
        "This fetches your library and classifies it by genre, energy, and vibe.\n"
        "It may take a few minutes the first time.",
    ):
        return

    prog = tk.Toplevel(root)
    prog.title("Syncing…")
    prog.transient(root)
    prog.resizable(False, False)
    status_var = tk.StringVar(value="Starting…")
    ttk.Label(prog, textvariable=status_var, wraplength=320,
              font=("TkDefaultFont", 11)).pack(padx=24, pady=(20, 8))
    pbar = ttk.Progressbar(prog, length=340, mode="indeterminate")
    pbar.pack(padx=24, pady=(0, 20))
    pbar.start()

    def status_cb(msg: str) -> None:
        root.after(0, lambda: status_var.set(msg))

    def worker() -> None:
        try:
            _run_pipeline(status_cb)
            root.after(0, lambda: _sync_done(prog, app, None))
        except Exception as exc:  # noqa: BLE001
            root.after(0, lambda: _sync_done(prog, app, exc))

    threading.Thread(target=worker, daemon=True).start()


def _sync_done(prog: tk.Toplevel, app: "App", exc: Exception | None) -> None:
    try:
        prog.destroy()
    except Exception:  # noqa: BLE001
        pass
    if exc is not None:
        messagebox.showerror("Sync failed", str(exc))
        return
    app.rows = load_rows()
    app.refresh()
    messagebox.showinfo("Sync complete", "Your library is ready!")


def main() -> None:
    root = tk.Tk()
    root.title("VibeThePlaylist")
    root.geometry("1480x720")

    import spotify_client as spc

    def launch_app(from_login: bool = False) -> None:
        for w in root.winfo_children():
            w.destroy()
        app = App(root)
        if from_login:
            root.after(100, lambda: _offer_sync(root, app))

    if spc.is_authenticated():
        launch_app(from_login=False)
    else:
        LoginFrame(root, on_success=lambda: launch_app(from_login=True))

    root.mainloop()


if __name__ == "__main__":
    main()
