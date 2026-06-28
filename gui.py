"""Desktop GUI — PyQt6 edition.

Browse your Spotify Liked Songs by genre, energy, and vibe.
Double-click a row to play/stop its 30-second preview.

    conda activate Spotify
    python gui.py
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request

try:
    from PyQt6.QtCore import (
        Qt, QThread, QTimer, QAbstractTableModel, QModelIndex, pyqtSignal,
    )
    from PyQt6.QtGui import QColor, QFont
    from PyQt6.QtWidgets import (
        QAbstractItemView, QApplication, QDialog, QFrame, QGroupBox,
        QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
        QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
        QProgressBar, QPushButton, QStackedWidget,
        QTabWidget, QTableView, QVBoxLayout, QWidget,
    )
    _PYQT6_AVAILABLE = True
except (ImportError, OSError):
    # PyQt6 / a display is unavailable (e.g. headless CI).  Provide stub base
    # classes so the module body can be imported and the pure data/logic layer
    # (GENRES, load_rows, Selection, row_matches) remains accessible.
    _PYQT6_AVAILABLE = False

    class _Stub:                          # type: ignore[misc]
        """No-op base for Qt widget/model subclasses in headless environments."""
        def __init__(self, *a, **kw): pass
        def __init_subclass__(cls, **kw): pass

    def pyqtSignal(*a, **kw):            # type: ignore[misc]
        return None

    class _QtSentinel:
        """Attribute-chain sentinel: returns *itself* for any attribute access
        so that lookups like ``Qt.ItemDataRole.DisplayRole`` or
        ``Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable`` evaluate
        to a harmless sentinel rather than raising AttributeError."""
        def __getattr__(self, name: str) -> "_QtSentinel":
            return self
        def __or__(self, other: object) -> object:
            return self
        def __ror__(self, other: object) -> object:
            return self
        def __int__(self) -> int:
            return 0

    class Qt:                            # type: ignore[misc]
        ItemDataRole = ItemFlag = CheckState = AlignmentFlag = _QtSentinel()

    QThread = QTimer = QAbstractTableModel = QModelIndex = _Stub  # type: ignore[misc,assignment]
    QColor = QFont = _Stub                                         # type: ignore[misc,assignment]
    (QAbstractItemView, QApplication, QDialog, QFrame, QGroupBox,
     QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
     QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
     QProgressBar, QPushButton, QStackedWidget, QTabWidget,
     QTableView, QVBoxLayout, QWidget) = (_Stub,) * 21             # type: ignore[misc,assignment]

import config
import db

log = logging.getLogger(__name__)
_DEFAULT_PLAYLIST_SAVE_ERROR_MESSAGE = (
    "Spotify could not save the playlist. Log out and sign back in, then try again. "
    "If that does not help, try changing the playlist name and retry."
)

# ---------------------------------------------------------------------------
# Option lists (derived from config so they stay in sync)
# ---------------------------------------------------------------------------
GENRES   = list(config.GENRE_BUCKETS.keys()) + [config.DEFAULT_GENRE]
SUBGENRES = [sg for subs in config.SUBGENRE_BUCKETS.values() for sg in subs]
VIBES    = list(config.VIBE_RULES.keys()) + [config.DEFAULT_VIBE]
ENERGIES = [band[2] for band in config.ENERGY_BANDS]

INCLUDE, EXCLUDE = "include", "exclude"

# ---------------------------------------------------------------------------
# Stylesheet (auto light / dark)
# ---------------------------------------------------------------------------
_GREEN      = "#1DB954"
_GREEN_DARK = "#17a348"


def _build_stylesheet(dark: bool) -> str:
    if dark:
        bg        = "#1e1e1e"
        surface   = "#2a2a2a"
        border    = "#3a3a3a"
        text      = "#e8e8e8"
        muted     = "#888888"
        hover     = "#333333"
        pressed   = "#3d3d3d"
        disabled  = "#555555"
        sel_bg    = "#1a3d27"
        alt_row   = "#252525"
        hdr_bg    = "#1e1e1e"
        scroll    = "#555555"
        show_bg   = "#1a3d27"; show_bd = "#2d6a4f"; show_fg = "#6fcf97"
        hide_bg   = "#3d1a1a"; hide_bd = "#7a2020"; hide_fg = "#f28b82"
        input_bg  = "#2a2a2a"
        pane_bg   = "#252525"
    else:
        bg        = "#f7f7f7"
        surface   = "#ffffff"
        border    = "#d0d0d0"
        text      = "#1a1a1a"
        muted     = "#888888"
        hover     = "#f0f0f0"
        pressed   = "#e0e0e0"
        disabled  = "#aaaaaa"
        sel_bg    = "#e6f4ea"
        alt_row   = "#fafafa"
        hdr_bg    = "#f7f7f7"
        scroll    = "#d0d0d0"
        show_bg   = "#e6f4ea"; show_bd = "#a8d5b5"; show_fg = "#2d6a4f"
        hide_bg   = "#fdecea"; hide_bd = "#f4a8a0"; hide_fg = "#b71c1c"
        input_bg  = "#ffffff"
        pane_bg   = "#ffffff"

    return f"""
/* ── base ───────────────────────────────────────────── */
QMainWindow, QDialog, QWidget {{
    background-color: {bg};
    font-family: "Segoe UI", "Ubuntu", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: {text};
}}

/* ── buttons ─────────────────────────────────────────── */
QPushButton {{
    background-color: {surface};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 8px 18px;
    color: {text};
    font-size: 13px;
}}
QPushButton:hover   {{ background-color: {hover}; }}
QPushButton:pressed {{ background-color: {pressed}; }}
QPushButton:disabled {{ color: {disabled}; background: {hover}; }}

QPushButton#primary {{
    background-color: {_GREEN};
    color: white;
    border: none;
    font-weight: 700;
    font-size: 14px;
    padding: 12px 32px;
    border-radius: 24px;
}}
QPushButton#primary:hover   {{ background-color: {_GREEN_DARK}; }}
QPushButton#primary:pressed {{ background-color: #148a3e; }}

QPushButton#showing {{
    background-color: {show_bg};
    border: 1px solid {show_bd};
    color: {show_fg};
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 4px;
}}
QPushButton#hiding {{
    background-color: {hide_bg};
    border: 1px solid {hide_bd};
    color: {hide_fg};
    font-size: 11px;
    padding: 3px 8px;
    border-radius: 4px;
}}

/* ── inputs ──────────────────────────────────────────── */
QLineEdit {{
    background-color: {input_bg};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 7px 12px;
    font-size: 13px;
    color: {text};
    selection-background-color: {_GREEN};
}}
QLineEdit:focus {{ border-color: {_GREEN}; }}

/* ── tabs ────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {border};
    border-radius: 0 0 8px 8px;
    background-color: {pane_bg};
    top: -1px;
}}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 9px 20px;
    color: {muted};
    font-size: 13px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    color: {text};
    font-weight: 600;
    border-bottom: 2px solid {_GREEN};
}}
QTabBar::tab:hover:!selected {{ color: {text}; }}

/* ── list widget ─────────────────────────────────────── */
QListWidget {{
    background-color: {pane_bg};
    border: none;
    outline: 0;
}}
QListWidget::item {{
    padding: 5px 4px;
    border-radius: 4px;
}}
QListWidget::item:hover    {{ background-color: {hover}; }}
QListWidget::item:selected {{ background-color: {sel_bg}; color: {text}; }}

/* ── table ───────────────────────────────────────────── */
QTableView {{
    background-color: {surface};
    alternate-background-color: {alt_row};
    border: 1px solid {border};
    border-radius: 8px;
    gridline-color: transparent;
    selection-background-color: {sel_bg};
    selection-color: {text};
    outline: 0;
}}
QTableView::item {{ padding: 0 8px; border: none; }}
QTableView::item:selected {{ background-color: {sel_bg}; color: {text}; }}
QHeaderView::section {{
    background-color: {hdr_bg};
    border: none;
    border-bottom: 1px solid {border};
    padding: 8px 10px;
    font-weight: 700;
    font-size: 11px;
    color: {muted};
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QHeaderView::section:first {{ border-radius: 8px 0 0 0; }}

/* ── scrollbars ──────────────────────────────────────── */
QScrollBar:vertical {{
    width: 8px; background: transparent; margin: 0; border: none;
}}
QScrollBar::handle:vertical {{
    background: {scroll}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    height: 8px; background: transparent;
}}
QScrollBar::handle:horizontal {{
    background: {scroll}; border-radius: 4px; min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── group boxes ─────────────────────────────────────── */
QGroupBox {{
    font-size: 11px;
    font-weight: 700;
    color: {muted};
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border: none;
    border-top: 1px solid {border};
    margin-top: 8px;
    padding-top: 16px;
    background: transparent;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    top: -2px;
    padding: 0 4px;
}}

/* ── progress bar ────────────────────────────────────── */
QProgressBar {{
    border: none;
    border-radius: 3px;
    background-color: {border};
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background-color: {_GREEN}; border-radius: 3px; }}
"""


# ---------------------------------------------------------------------------
# Data helpers (unchanged from Tkinter version)
# ---------------------------------------------------------------------------

def load_rows() -> list[dict]:
    """Read all labelled tracks from DB; filtering happens in-memory."""
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


class Selection:
    def __init__(self):
        self.selected: set[str] = set()
        self.mode: str = INCLUDE

    def toggle(self, opt: str) -> None:
        if opt in self.selected:
            self.selected.discard(opt)
        else:
            self.selected.add(opt)

    def flip_mode(self) -> None:
        self.mode = EXCLUDE if self.mode == INCLUDE else INCLUDE

    def is_on(self, opt: str) -> bool:
        return opt in self.selected

    def clear(self) -> None:
        self.selected.clear()

    def included(self) -> set[str]:
        return set(self.selected) if self.mode == INCLUDE else set()

    def excluded(self) -> set[str]:
        return set(self.selected) if self.mode == EXCLUDE else set()


def row_matches(row: dict, inc: dict, exc: dict, any_modes: dict[str, bool]) -> bool:
    """Pure filter. Excludes are hard stops; includes use per-panel any_modes.

    any_modes maps panel key → True (OR: any selected item matches) or
    False (AND: all selected items must match). Cross-panel logic is always AND.
    """
    if exc["g"]  & set(row["genres"]):    return False
    if exc["sg"] & set(row["subgenres"]): return False
    if exc["v"]  & set(row["vibes"]):     return False
    if row["energy"] in exc["e"]:         return False
    if row["artist"] in exc["ar"]:        return False
    if row["album"]  in exc["al"]:        return False

    def _set_panel(key: str, row_vals: list) -> bool:
        s = inc[key]
        if not s:
            return True
        rv = set(row_vals)
        return bool(s & rv) if any_modes.get(key, True) else s <= rv

    def _scalar(key: str, val: str) -> bool:
        return not inc[key] or val in inc[key]

    return (
        _set_panel("g",  row["genres"])
        and _set_panel("sg", row["subgenres"])
        and _set_panel("v",  row["vibes"])
        and _scalar("e",  row["energy"])
        and _scalar("ar", row["artist"])
        and _scalar("al", row["album"])
    )


# ---------------------------------------------------------------------------
# Preview player (unchanged logic; callback posted to main thread by caller)
# ---------------------------------------------------------------------------


class _PreviewPlayer:
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
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = resp.read()
            if self.playing_id != track_id:
                return
            self._pg.mixer.music.stop()
            config.PREVIEW_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(config.PREVIEW_CACHE_PATH, "wb") as f:
                f.write(data)
            self._pg.mixer.music.load(str(config.PREVIEW_CACHE_PATH))
            self._pg.mixer.music.play()
            while self._pg.mixer.music.get_busy() and self.playing_id == track_id:
                time.sleep(0.1)
        except Exception:
            pass
        if self.playing_id == track_id:
            self.playing_id = None
            on_change()


# ---------------------------------------------------------------------------
# Pipeline runner (unchanged)
# ---------------------------------------------------------------------------

def _run_pipeline(status_cb, sp=None) -> None:
    import classify
    import enrich
    import spotify_client as spc

    db.init()
    status_cb("Connecting to Spotify…")
    if sp is None:
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
                    rows.append({"track_id": tid, "tag": tag,
                                 "source": "lastfm", "weight": weight})
                if not found:
                    rows.append({"track_id": tid, "tag": "__none__",
                                 "source": "lastfm", "weight": 0})
            db.upsert_tags(rows)

    status_cb("Classifying…")
    classify.classify_all()


# ---------------------------------------------------------------------------
# Worker threads
# ---------------------------------------------------------------------------

class _AuthWorker(QThread):
    succeeded = pyqtSignal()
    failed    = pyqtSignal(str)

    def run(self) -> None:
        try:
            import spotify_client as spc
            sp = spc.get_client_pkce()
            sp.current_user()
            self.succeeded.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class _SyncWorker(QThread):
    status_changed = pyqtSignal(str)
    finished       = pyqtSignal()
    failed         = pyqtSignal(str)

    def __init__(self, sp=None):
        super().__init__()
        self._sp = sp

    def run(self) -> None:
        try:
            _run_pipeline(lambda msg: self.status_changed.emit(msg), sp=self._sp)
            self.finished.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class _PlaylistWorker(QThread):
    finished = pyqtSignal(str)
    failed   = pyqtSignal(str)

    def __init__(self, name: str, track_ids: list[str], sp=None):
        super().__init__()
        self._name      = name
        self._track_ids = track_ids
        self._sp        = sp

    def run(self) -> None:
        try:
            import playlists
            import spotify_client as spc
            sp   = self._sp or spc.get_client_pkce()
            full = playlists.create_named_playlist(sp, self._name, self._track_ids)
            self.finished.emit(full)
        except Exception as exc:
            log.exception("Failed to save playlist from GUI: %r", self._name)
            self.failed.emit(getattr(exc, "user_message", _DEFAULT_PLAYLIST_SAVE_ERROR_MESSAGE))


# ---------------------------------------------------------------------------
# Track table model
# ---------------------------------------------------------------------------

_ENERGY_LABEL = {"low": "Low", "mid": "Mid", "high": "High"}
_COLS         = ("", "",   "Artist", "Song", "Album", "Genre", "Energy", "Vibe")
_COL_WIDTHS   = (32,  30,  185,      225,    185,     150,     70,       200)
_COL_PLAY     = 0
_COL_CHECK    = 1


class TrackTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._playing_id: str | None = None
        self._checked_ids: set[str] = set()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(_COLS)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == _COL_CHECK:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == _COL_CHECK:
            return (Qt.CheckState.Checked if row["id"] in self._checked_ids
                    else Qt.CheckState.Unchecked)

        if role == Qt.ItemDataRole.DisplayRole:
            if col == _COL_PLAY:
                if row["id"] == self._playing_id:
                    return "▶"
                return "♪" if row.get("preview_url") else ""
            if col == _COL_CHECK: return None
            if col == 2: return row["artist"]
            if col == 3: return row["title"]
            if col == 4: return row["album"] or "—"
            if col == 5: return ", ".join(row["genres"]) or "—"
            if col == 6: return _ENERGY_LABEL.get(row.get("energy") or "", "—")
            if col == 7: return ", ".join(row["vibes"]) or "—"

        if role == Qt.ItemDataRole.BackgroundRole:
            if row["id"] == self._playing_id:
                return QColor("#d4edda")
            if row["id"] in self._checked_ids:
                return QColor("#ddeeff")

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == _COL_PLAY:
                return Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        if role == Qt.ItemDataRole.FontRole and col == _COL_PLAY:
            f = QFont()
            f.setPointSize(11)
            return f

        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if index.column() == _COL_CHECK and role == Qt.ItemDataRole.CheckStateRole:
            row = self._rows[index.row()]
            if value == Qt.CheckState.Checked.value or value == Qt.CheckState.Checked:
                self._checked_ids.add(row["id"])
            else:
                self._checked_ids.discard(row["id"])
            self.dataChanged.emit(index, index,
                                  [Qt.ItemDataRole.CheckStateRole,
                                   Qt.ItemDataRole.BackgroundRole])
            return True
        return False

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _COLS[section]
        return None

    def update_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def clear_checked(self) -> None:
        self._checked_ids.clear()
        if self._rows:
            self.dataChanged.emit(
                self.index(0, _COL_CHECK),
                self.index(len(self._rows) - 1, _COL_CHECK),
                [Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.BackgroundRole],
            )

    def select_shown(self) -> None:
        self._checked_ids.update(r["id"] for r in self._rows)
        if self._rows:
            self.dataChanged.emit(
                self.index(0, _COL_CHECK),
                self.index(len(self._rows) - 1, _COL_CHECK),
                [Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.BackgroundRole],
            )

    def all_shown_checked(self) -> bool:
        return bool(self._rows) and all(r["id"] in self._checked_ids for r in self._rows)

    def checked_count(self) -> int:
        return len(self._checked_ids)

    def set_playing(self, track_id: str | None) -> None:
        self._playing_id = track_id
        if self._rows:
            top_left  = self.index(0, _COL_PLAY)
            bot_right = self.index(len(self._rows) - 1, _COL_PLAY)
            self.dataChanged.emit(top_left, bot_right,
                                  [Qt.ItemDataRole.DisplayRole,
                                   Qt.ItemDataRole.BackgroundRole])

    def row_at(self, idx: int) -> dict | None:
        return self._rows[idx] if 0 <= idx < len(self._rows) else None


# ---------------------------------------------------------------------------
# Filter panel (one class for all categories)
# ---------------------------------------------------------------------------

class FilterPanel(QGroupBox):
    """A labelled, searchable list of checkboxes with Include/Exclude and OR/AND modes."""

    changed = pyqtSignal()

    def __init__(self, title: str, options: list[str],
                 searchable: bool = False, parent=None):
        super().__init__(title, parent)
        self.sel       = Selection()
        self.allowed: set[str] | None = None
        self._searchable = searchable
        self._any_mode   = True  # OR by default (any selected item matches)

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 18, 8, 8)

        if searchable:
            self.search_box = QLineEdit()
            self.search_box.setPlaceholderText("Search…")
            self.search_box.textChanged.connect(self._apply_filter)
            root.addWidget(self.search_box)

        self.list_widget = QListWidget()
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        root.addWidget(self.list_widget, 1)

        self._populate(options)
        self.list_widget.itemChanged.connect(self._on_item_changed)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.mode_btn = QPushButton("Include")
        self.mode_btn.setObjectName("showing")
        self.mode_btn.setFixedWidth(74)
        self.mode_btn.clicked.connect(self._flip_mode)
        self.match_btn = QPushButton("OR")
        self.match_btn.setObjectName("showing")
        self.match_btn.setFixedWidth(46)
        self.match_btn.clicked.connect(self._flip_match)
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet("color: #888; font-size: 11px;")
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.clear)
        bar.addWidget(self.mode_btn)
        bar.addWidget(self.match_btn)
        bar.addWidget(self.count_lbl)
        bar.addStretch()
        bar.addWidget(clear_btn)
        root.addLayout(bar)

    # --- population ---------------------------------------------------------

    def _populate(self, options: list[str]) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for opt in options:
            item = QListWidgetItem(opt or "(none)")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

    def populate(self, options: list[str]) -> None:
        """Reload options (called for artist/album panels after data loads)."""
        self.sel.clear()
        self._populate(options)
        self._update_count()

    # --- internal -----------------------------------------------------------

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if item.checkState() == Qt.CheckState.Checked:
            self.sel.selected.add(item.text())
        else:
            self.sel.selected.discard(item.text())
        self._update_count()
        self.changed.emit()

    def _apply_filter(self) -> None:
        q = (self.search_box.text().strip().lower()
             if self._searchable and hasattr(self, "search_box") else "")
        for i in range(self.list_widget.count()):
            item   = self.list_widget.item(i)
            text   = item.text().lower()
            ticked = item.checkState() == Qt.CheckState.Checked
            in_q   = not q or q in text
            in_al  = self.allowed is None or item.text() in self.allowed
            item.setHidden(not ticked and not (in_q and in_al))

    def _flip_mode(self) -> None:
        self.sel.flip_mode()
        if self.sel.mode == INCLUDE:
            self.mode_btn.setText("Include")
            self.mode_btn.setObjectName("showing")
        else:
            self.mode_btn.setText("Exclude")
            self.mode_btn.setObjectName("hiding")
        self.mode_btn.style().unpolish(self.mode_btn)
        self.mode_btn.style().polish(self.mode_btn)
        self.changed.emit()

    def _flip_match(self) -> None:
        self._any_mode = not self._any_mode
        if self._any_mode:
            self.match_btn.setText("OR")
            self.match_btn.setObjectName("showing")
        else:
            self.match_btn.setText("AND")
            self.match_btn.setObjectName("hiding")
        self.match_btn.style().unpolish(self.match_btn)
        self.match_btn.style().polish(self.match_btn)
        self.changed.emit()

    def _update_count(self) -> None:
        n = len(self.sel.selected)
        self.count_lbl.setText(f"{n} selected" if n else "")

    # --- public API ---------------------------------------------------------

    def set_allowed(self, allowed: set[str] | None) -> None:
        if allowed == self.allowed:
            return
        self.allowed = allowed
        self._apply_filter()

    def reset(self) -> None:
        self.sel.clear()
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
        self.list_widget.blockSignals(False)
        if self.sel.mode == EXCLUDE:
            self.sel.flip_mode()
            self.mode_btn.setText("Include")
            self.mode_btn.setObjectName("showing")
            self.mode_btn.style().unpolish(self.mode_btn)
            self.mode_btn.style().polish(self.mode_btn)
        if not self._any_mode:
            self._any_mode = True
            self.match_btn.setText("OR")
            self.match_btn.setObjectName("showing")
            self.match_btn.style().unpolish(self.match_btn)
            self.match_btn.style().polish(self.match_btn)
        self._update_count()

    def clear(self) -> None:
        self.reset()
        self.changed.emit()

    @property
    def any_mode(self) -> bool:
        return self._any_mode

    def included(self) -> set[str]:
        return self.sel.included()

    def excluded(self) -> set[str]:
        return self.sel.excluded()


# ---------------------------------------------------------------------------
# Sync progress dialog
# ---------------------------------------------------------------------------

class SyncDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Syncing your library…")
        self.setFixedWidth(400)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(28, 28, 28, 28)

        self.status_lbl = QLabel("Starting…")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("font-size: 13px; color: #333;")

        self.pbar = QProgressBar()
        self.pbar.setRange(0, 0)
        self.pbar.setFixedHeight(6)
        self.pbar.setTextVisible(False)

        layout.addWidget(self.status_lbl)
        layout.addWidget(self.pbar)

    def set_status(self, msg: str) -> None:
        self.status_lbl.setText(msg)


# ---------------------------------------------------------------------------
# Login screen
# ---------------------------------------------------------------------------

class LoginWidget(QWidget):
    succeeded = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        title = QLabel("VibeThePlaylist")
        title.setStyleSheet(
            "font-size: 36px; font-weight: 700; color: #1a1a1a; letter-spacing: -1px;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel(
            "Browse and playlist your Spotify library\nby genre, energy, and vibe."
        )
        subtitle.setStyleSheet("font-size: 15px; color: #666; line-height: 1.5;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.connect_btn = QPushButton("Connect to Spotify")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.setFixedWidth(240)
        self.connect_btn.clicked.connect(self._connect)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #888; font-size: 12px;")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch(3)
        layout.addWidget(title,       alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(4)
        layout.addWidget(subtitle,    alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(24)
        layout.addWidget(self.connect_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(8)
        layout.addWidget(self.status_lbl)
        layout.addStretch(4)

        self._worker: _AuthWorker | None = None

    def _connect(self) -> None:
        self.connect_btn.setEnabled(False)
        self.status_lbl.setText("Opening browser — please log in to Spotify…")
        self._worker = _AuthWorker()
        self._worker.succeeded.connect(self.succeeded)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _failed(self, msg: str) -> None:
        self.connect_btn.setEnabled(True)
        self.status_lbl.setText(f"Connection failed: {msg}")


# ---------------------------------------------------------------------------
# Main library browser
# ---------------------------------------------------------------------------

class LibraryWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._player          = _PreviewPlayer()
        self.rows: list[dict] = []
        self._sp              = None   # shared Spotify client; set by MainWindow
        self._sync_worker: _SyncWorker | None       = None
        self._playlist_worker: _PlaylistWorker | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 10)
        root.setSpacing(12)

        # ── left: track table + status ──────────────────────────────────────
        self.model = TrackTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        for i, w in enumerate(_COL_WIDTHS):
            self.table.setColumnWidth(i, w)
        for fixed_col in (_COL_PLAY, _COL_CHECK):
            self.table.horizontalHeader().setSectionResizeMode(
                fixed_col, QHeaderView.ResizeMode.Fixed)

        self.table.doubleClicked.connect(self._on_double_click)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #888; font-size: 12px; padding: 2px 0;")

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(self.table)
        left.addWidget(self.status_lbl)

        # ── right: filters + controls ───────────────────────────────────────
        tabs = QTabWidget()

        # Tab 1 — Genre
        g = QWidget()
        g_layout = QHBoxLayout(g)
        g_layout.setContentsMargins(12, 8, 12, 8)
        g_layout.setSpacing(16)
        self.genre_panel    = FilterPanel("Genres",    GENRES,    parent=g)
        self.subgenre_panel = FilterPanel("Subgenres", SUBGENRES,
                                          searchable=True, parent=g)
        for p in (self.genre_panel, self.subgenre_panel):
            g_layout.addWidget(p)
            p.changed.connect(self.refresh)
        tabs.addTab(g, "Genre")

        # Tab 2 — Vibe
        v = QWidget()
        v_layout = QHBoxLayout(v)
        v_layout.setContentsMargins(12, 8, 12, 8)
        v_layout.setSpacing(16)
        self.vibe_panel   = FilterPanel("Vibes",  VIBES,    parent=v)
        self.energy_panel = FilterPanel("Energy", ENERGIES, parent=v)
        for p in (self.vibe_panel, self.energy_panel):
            v_layout.addWidget(p)
            p.changed.connect(self.refresh)
        tabs.addTab(v, "Vibe")

        # Tab 3 — Artists
        ar = QWidget()
        ar_layout = QHBoxLayout(ar)
        ar_layout.setContentsMargins(12, 8, 12, 8)
        self.artist_panel = FilterPanel("Artists", [], searchable=True, parent=ar)
        ar_layout.addWidget(self.artist_panel)
        self.artist_panel.changed.connect(self.refresh)
        tabs.addTab(ar, "Artists")

        # Tab 4 — Albums
        al = QWidget()
        al_layout = QHBoxLayout(al)
        al_layout.setContentsMargins(12, 8, 12, 8)
        self.album_panel = FilterPanel("Albums", [], searchable=True, parent=al)
        al_layout.addWidget(self.album_panel)
        self.album_panel.changed.connect(self.refresh)
        tabs.addTab(al, "Albums")

        # ── controls bar ────────────────────────────────────────────────────
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search songs or artists…")
        self.search_box.textChanged.connect(self.refresh)

        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet("font-weight: 600; font-size: 13px; color: #555;")

        self.reset_btn    = QPushButton("Reset filters")
        self.refresh_btn  = QPushButton("Refresh library")
        self.playlist_btn = QPushButton("Save as playlist…")
        self.select_btn   = QPushButton("Select all")
        self.logout_btn   = QPushButton("Log out")

        self.reset_btn.clicked.connect(self._reset_filters)
        self.refresh_btn.clicked.connect(self._sync)
        self.playlist_btn.clicked.connect(self._create_playlist)
        self.select_btn.clicked.connect(self._toggle_select_all)
        self.logout_btn.clicked.connect(self._logout)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(self.reset_btn)
        btn_row.addWidget(self.refresh_btn)
        btn_row.addWidget(self.playlist_btn)
        btn_row.addWidget(self.select_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.logout_btn)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.search_box)
        right.addWidget(self.count_lbl)
        right.addWidget(tabs)
        right.addLayout(btn_row)

        root.addLayout(left, 3)
        root.addLayout(right, 2)

        self.model.dataChanged.connect(self._on_model_data_changed)

        # ── cross-filter maps ────────────────────────────────────────────────
        self.artist_albums: dict[str, set[str]] = {}
        self.album_artists: dict[str, set[str]] = {}

    def _on_model_data_changed(self, _tl, _br, roles) -> None:
        if Qt.ItemDataRole.CheckStateRole in roles:
            self.refresh()

    # ── data loading ────────────────────────────────────────────────────────

    def load(self) -> None:
        self.rows = load_rows()

        artists = sorted({r["artist"] for r in self.rows if r["artist"]})
        albums  = sorted({r["album"]  for r in self.rows if r["album"]})
        self.artist_panel.populate(artists)
        self.album_panel.populate(albums)

        self.artist_albums = {}
        self.album_artists = {}
        for r in self.rows:
            a, al = r["artist"], r["album"]
            if a and al:
                self.artist_albums.setdefault(a, set()).add(al)
                self.album_artists.setdefault(al, set()).add(a)

        self.refresh()

    # ── filtering ───────────────────────────────────────────────────────────

    def refresh(self) -> None:
        inc = {
            "g":  self.genre_panel.included(),
            "sg": self.subgenre_panel.included(),
            "v":  self.vibe_panel.included(),
            "e":  self.energy_panel.included(),
            "ar": self.artist_panel.included(),
            "al": self.album_panel.included(),
        }
        exc = {
            "g":  self.genre_panel.excluded(),
            "sg": self.subgenre_panel.excluded(),
            "v":  self.vibe_panel.excluded(),
            "e":  self.energy_panel.excluded(),
            "ar": self.artist_panel.excluded(),
            "al": self.album_panel.excluded(),
        }

        # cross-filter subgenres by ticked genres
        ticked_genres = self.genre_panel.sel.selected
        allowed_sg = (
            set().union(*(config.SUBGENRE_BUCKETS.get(g, []) for g in ticked_genres))
            if ticked_genres else set()
        )
        self.subgenre_panel.set_allowed(allowed_sg)

        # cross-filter artists ↔ albums
        allowed_albums = (
            set().union(*(self.artist_albums.get(a, set()) for a in inc["ar"]))
            if inc["ar"] else None
        )
        allowed_artists = (
            set().union(*(self.album_artists.get(al, set()) for al in inc["al"]))
            if inc["al"] else None
        )
        self.album_panel.set_allowed(allowed_albums)
        self.artist_panel.set_allowed(allowed_artists)

        any_modes = {
            "g":  self.genre_panel.any_mode,
            "sg": self.subgenre_panel.any_mode,
            "v":  self.vibe_panel.any_mode,
            "e":  self.energy_panel.any_mode,
            "ar": self.artist_panel.any_mode,
            "al": self.album_panel.any_mode,
        }
        q = self.search_box.text().strip().lower()
        shown = [
            r for r in self.rows
            if row_matches(r, inc, exc, any_modes)
            and (not q or q in r["artist"].lower() or q in r["title"].lower())
        ]
        self.model.update_rows(shown)

        n, total = len(shown), len(self.rows)
        checked  = self.model.checked_count()
        label    = f"{n} song{'s' if n != 1 else ''}"
        if n != total:
            label += f" of {total}"
        if checked:
            label += f"  ·  {checked} selected"
        self.count_lbl.setText(label)
        self.select_btn.setText(
            "Deselect all" if self.model.all_shown_checked() else "Select all"
        )

    def _toggle_select_all(self) -> None:
        if self.model.all_shown_checked():
            self.model.clear_checked()
        else:
            self.model.select_shown()

    def _reset_filters(self) -> None:
        for p in (self.genre_panel, self.subgenre_panel, self.vibe_panel,
                  self.energy_panel, self.artist_panel, self.album_panel):
            p.reset()
        self.search_box.clear()
        self.model.clear_checked()
        self.refresh()

    # ── preview playback ────────────────────────────────────────────────────

    def _on_double_click(self, index: QModelIndex) -> None:
        row = self.model.row_at(index.row())
        if not row:
            return
        if not self._player.available:
            self.status_lbl.setText("Install pygame for 30s preview playback.")
            return
        if not row.get("preview_url"):
            self.status_lbl.setText(
                f"No preview available for {row['artist']} — {row['title']}."
            )
            return
        self._player.toggle(
            row["id"], row["preview_url"],
            lambda: QTimer.singleShot(0, self._update_player_ui),
        )

    def _update_player_ui(self) -> None:
        pid = self._player.playing_id
        self.model.set_playing(pid)
        if pid:
            for r in self.model._rows:
                if r["id"] == pid:
                    self.status_lbl.setText(
                        f"▶   {r['artist']}  —  {r['title']}     "
                        "(double-click to stop)"
                    )
                    break
        else:
            self.status_lbl.setText("")

    # ── sync ────────────────────────────────────────────────────────────────

    def _sync(self) -> None:
        reply = QMessageBox.question(
            self, "Refresh library",
            "Sync your Spotify liked songs?\n\n"
            "This fetches your library and classifies it.\n"
            "May take a few minutes.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._run_sync()

    def offer_initial_sync(self) -> None:
        reply = QMessageBox.question(
            self, "Sync your library",
            "Would you like to sync your Spotify liked songs now?\n\n"
            "This classifies them by genre, energy, and vibe.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._run_sync()

    def _run_sync(self) -> None:
        dlg    = SyncDialog(self)
        worker = _SyncWorker(sp=self._sp)
        worker.status_changed.connect(dlg.set_status)
        worker.finished.connect(dlg.accept)
        worker.failed.connect(
            lambda msg: (dlg.reject(),
                         QMessageBox.critical(self, "Sync failed", msg))
        )
        worker.finished.connect(self.load)
        worker.start()
        self._sync_worker = worker
        dlg.exec()

    # ── playlist creation ────────────────────────────────────────────────────

    def _suggest_name(self) -> str:
        parts = (list(self.vibe_panel.included())
                 + list(self.genre_panel.included())
                 + list(self.energy_panel.included()))
        return " ".join(parts) if parts else "Filtered"

    def _create_playlist(self) -> None:
        checked = self.model._checked_ids
        if checked:
            to_save = [r for r in self.rows if r["id"] in checked]
            prompt  = f"{len(to_save)} selected song(s) will be added.\n\nPlaylist name:"
        else:
            to_save = self.model._rows
            prompt  = f"{len(to_save)} shown song(s) will be added.\n\nPlaylist name:"
        if not to_save:
            QMessageBox.warning(self, "Nothing to save",
                                "No songs match the current filters.")
            return
        name, ok = QInputDialog.getText(
            self, "Save as Spotify playlist",
            prompt,
            text=self._suggest_name(),
        )
        if not ok or not name.strip():
            return
        self.playlist_btn.setEnabled(False)
        worker = _PlaylistWorker(name.strip(), [r["id"] for r in to_save], sp=self._sp)
        worker.finished.connect(lambda full: (
            self.playlist_btn.setEnabled(True),
            QMessageBox.information(
                self, "Playlist saved",
                f'"{full}" is now in your Spotify.'
            ),
        ))
        worker.failed.connect(lambda msg: (
            self.playlist_btn.setEnabled(True),
            QMessageBox.critical(self, "Playlist failed", msg),
        ))
        worker.start()
        self._playlist_worker = worker

    # ── logout ───────────────────────────────────────────────────────────────

    def _logout(self) -> None:
        reply = QMessageBox.question(
            self, "Log out",
            "Disconnect your Spotify account?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._player.stop()
        import spotify_client as spc
        spc.logout()
        self.window().show_login()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VibeThePlaylist")
        self.resize(1440, 820)
        self.setMinimumSize(900, 600)

        self._stack   = QStackedWidget()
        self._login   = LoginWidget()
        self._library = LibraryWidget()

        self._stack.addWidget(self._login)
        self._stack.addWidget(self._library)
        self.setCentralWidget(self._stack)

        self._login.succeeded.connect(self._on_login_success)

        import spotify_client as spc
        if spc.is_authenticated():
            self._library._sp = spc.get_client_pkce()
            self._library.load()
            self._stack.setCurrentWidget(self._library)
        else:
            self._stack.setCurrentWidget(self._login)

    def _on_login_success(self) -> None:
        import spotify_client as spc
        self._library._sp = spc.get_client_pkce()
        self._library.load()
        self._stack.setCurrentWidget(self._library)
        QTimer.singleShot(150, self._library.offer_initial_sync)

    def show_login(self) -> None:
        self._stack.setCurrentWidget(self._login)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _is_dark(app) -> bool:
    from PyQt6.QtGui import QPalette
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def main() -> None:
    import sys
    app = QApplication(sys.argv)
    app.setStyleSheet(_build_stylesheet(_is_dark(app)))
    app.setApplicationName("VibeThePlaylist")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
