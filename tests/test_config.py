import importlib

import pytest

import config


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    importlib.reload(config)


def _assert_parent_dirs_are_writable(module) -> None:
    for parent, name in (
        (module.DB_PATH.parent, "db-parent.txt"),
        (module.TOKEN_CACHE_PATH.parent, "token-parent.txt"),
        (module.PREVIEW_CACHE_PATH.parent, "preview-parent.txt"),
    ):
        marker = parent / name
        marker.write_text("ok", encoding="utf-8")
        assert marker.read_text(encoding="utf-8") == "ok"


def test_data_dir_env_override_updates_runtime_paths(monkeypatch, tmp_path):
    data_dir = tmp_path / "appdata"
    monkeypatch.setenv("VIBETHEPLAYLIST_DATA_DIR", str(data_dir))
    monkeypatch.delenv("VIBETHEPLAYLIST_CACHE_DIR", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_DB_PATH", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_TOKEN_CACHE_PATH", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_PREVIEW_CACHE_PATH", raising=False)

    module = importlib.reload(config)

    assert module.DATA_DIR == data_dir
    assert module.CACHE_DIR == data_dir / "cache"
    assert module.DB_PATH == data_dir / "library.db"
    assert module.TOKEN_CACHE_PATH == data_dir / ".spotify_token_cache"
    assert module.PREVIEW_CACHE_PATH == data_dir / "cache" / "preview.mp3"
    assert module.DATA_DIR.exists()
    assert module.CACHE_DIR.exists()
    assert module.DB_PATH.parent.exists()
    assert module.TOKEN_CACHE_PATH.parent.exists()
    assert module.PREVIEW_CACHE_PATH.parent.exists()
    _assert_parent_dirs_are_writable(module)


def test_explicit_runtime_path_overrides_default_paths(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    db_path = tmp_path / "state" / "library.sqlite3"
    token_path = tmp_path / "secrets" / "spotify.token"
    preview_path = tmp_path / "media" / "preview.mp3"
    monkeypatch.setenv("VIBETHEPLAYLIST_DATA_DIR", str(data_dir))
    monkeypatch.setenv("VIBETHEPLAYLIST_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("VIBETHEPLAYLIST_DB_PATH", str(db_path))
    monkeypatch.setenv("VIBETHEPLAYLIST_TOKEN_CACHE_PATH", str(token_path))
    monkeypatch.setenv("VIBETHEPLAYLIST_PREVIEW_CACHE_PATH", str(preview_path))

    module = importlib.reload(config)

    assert module.DATA_DIR == data_dir
    assert module.CACHE_DIR == cache_dir
    assert module.DB_PATH == db_path
    assert module.TOKEN_CACHE_PATH == token_path
    assert module.PREVIEW_CACHE_PATH == preview_path
    assert module.DB_PATH.parent.exists()
    assert module.TOKEN_CACHE_PATH.parent.exists()
    assert module.PREVIEW_CACHE_PATH.parent.exists()
    _assert_parent_dirs_are_writable(module)
