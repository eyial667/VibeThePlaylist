from pathlib import Path


class _AnalysisResult:
    def __init__(self, scripts, datas, hiddenimports):
        self.pure = []
        self.scripts = scripts
        self.binaries = []
        self.datas = datas
        self.hiddenimports = hiddenimports


def _analysis(*args, **kwargs):
    return _AnalysisResult(args[0], kwargs["datas"], kwargs["hiddenimports"])


def test_pyinstaller_spec_executes_without___file__():
    repo_root = Path(__file__).resolve().parents[1]
    spec_path = repo_root / "VibeThePlaylist.spec"
    namespace = {
        "__name__": "__main__",
        "SPECPATH": str(repo_root),
        "Analysis": _analysis,
        "PYZ": lambda *args, **kwargs: ("pyz", args, kwargs),
        "EXE": lambda *args, **kwargs: ("exe", args, kwargs),
        "COLLECT": lambda *args, **kwargs: ("collect", args, kwargs),
        "BUNDLE": lambda *args, **kwargs: ("bundle", args, kwargs),
    }

    exec(compile(spec_path.read_text(encoding="utf-8"), str(spec_path), "exec"), namespace)

    assert namespace["ROOT"] == repo_root
    assert namespace["datas"] == [(str(repo_root / "genreclass" / "taxonomy.json"), "genreclass")]
    assert namespace["a"].scripts == ["gui.py"]
    assert namespace["a"].hiddenimports == [
        "classify",
        "db",
        "enrich",
        "playlists",
        "spotify_client",
    ]


def test_pyinstaller_spec_names_packaged_app_consistently():
    repo_root = Path(__file__).resolve().parents[1]
    spec_path = repo_root / "VibeThePlaylist.spec"
    captured = {}

    def _capture(name):
        def _inner(*args, **kwargs):
            captured[name] = {"args": args, "kwargs": kwargs}
            return (name, args, kwargs)

        return _inner

    namespace = {
        "__name__": "__main__",
        "SPECPATH": str(repo_root),
        "Analysis": _analysis,
        "PYZ": _capture("pyz"),
        "EXE": _capture("exe"),
        "COLLECT": _capture("collect"),
        "BUNDLE": _capture("bundle"),
    }

    exec(compile(spec_path.read_text(encoding="utf-8"), str(spec_path), "exec"), namespace)

    assert captured["exe"]["kwargs"]["name"] == "VibeThePlaylist"
    assert captured["collect"]["kwargs"]["name"] == "VibeThePlaylist"
