from pathlib import Path


class _AnalysisResult:
    def __init__(self, datas):
        self.pure = []
        self.scripts = []
        self.binaries = []
        self.datas = datas


def _analysis(*args, **kwargs):
    return _AnalysisResult(kwargs["datas"])


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
