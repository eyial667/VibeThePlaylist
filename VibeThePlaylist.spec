# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys


# PyInstaller executes spec files with `SPECPATH` in the global namespace, while
# `__file__` is not guaranteed to exist there.
ROOT = Path(SPECPATH).resolve()
ICON_ICO = ROOT / "assets" / "app.ico"
ICON_ICNS = ROOT / "assets" / "app.icns"

datas = [
    (str(ROOT / "genreclass" / "taxonomy.json"), "genreclass"),
]

hiddenimports = [
    "pipeline.classify",
    "db",
    "pipeline.enrich",
    "pipeline.playlists",
    "clients.spotify_client",
]

a = Analysis(
    ["gui.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VibeThePlaylist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(ICON_ICO) if ICON_ICO.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VibeThePlaylist",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="VibeThePlaylist.app",
        icon=str(ICON_ICNS) if ICON_ICNS.exists() else None,
        bundle_identifier="com.vibetheplaylist.desktop",
    )
