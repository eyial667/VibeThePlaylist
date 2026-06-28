import importlib
from types import SimpleNamespace

import pytest

import config


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    importlib.reload(config)


def _verify_parent_dirs_writable(module) -> None:
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
    _verify_parent_dirs_writable(module)


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
    _verify_parent_dirs_writable(module)


def test_runtime_paths_for_scope_stays_under_data_dir(monkeypatch, tmp_path):
    data_dir = tmp_path / "appdata"
    monkeypatch.setenv("VIBETHEPLAYLIST_DATA_DIR", str(data_dir))
    monkeypatch.delenv("VIBETHEPLAYLIST_CACHE_DIR", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_DB_PATH", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_TOKEN_CACHE_PATH", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_PREVIEW_CACHE_PATH", raising=False)

    module = importlib.reload(config)
    paths = module.runtime_paths_for_scope(" listener/42 ")

    assert paths.scope == "listener_42"
    assert paths.data_dir == data_dir / "users" / "listener_42"
    assert paths.cache_dir == paths.data_dir / "cache"
    assert paths.db_path == paths.data_dir / "library.db"
    assert paths.token_cache_path == paths.data_dir / ".spotify_token_cache"
    assert paths.preview_cache_path == paths.data_dir / "cache" / "preview.mp3"
    _verify_parent_dirs_writable(module)
    _verify_parent_dirs_writable(SimpleNamespace(
        DB_PATH=paths.db_path,
        TOKEN_CACHE_PATH=paths.token_cache_path,
        PREVIEW_CACHE_PATH=paths.preview_cache_path,
    ))


def test_runtime_paths_for_scope_respects_explicit_file_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBETHEPLAYLIST_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VIBETHEPLAYLIST_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("VIBETHEPLAYLIST_DB_PATH", str(tmp_path / "state" / "library.sqlite3"))
    monkeypatch.setenv("VIBETHEPLAYLIST_TOKEN_CACHE_PATH", str(tmp_path / "secrets" / "spotify.token"))
    monkeypatch.setenv("VIBETHEPLAYLIST_PREVIEW_CACHE_PATH", str(tmp_path / "media" / "preview.mp3"))

    module = importlib.reload(config)
    paths = module.runtime_paths_for_scope("alice@example.com")

    assert paths.data_dir == tmp_path / "data" / "users" / "alice_example.com"
    assert paths.cache_dir == tmp_path / "cache" / "users" / "alice_example.com"
    assert paths.db_path == tmp_path / "state" / "users" / "alice_example.com" / "library.sqlite3"
    assert paths.token_cache_path == tmp_path / "secrets" / "users" / "alice_example.com" / "spotify.token"
    assert paths.preview_cache_path == tmp_path / "media" / "users" / "alice_example.com" / "preview.mp3"


def test_using_runtime_scope_swaps_paths_temporarily(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBETHEPLAYLIST_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("VIBETHEPLAYLIST_CACHE_DIR", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_DB_PATH", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_TOKEN_CACHE_PATH", raising=False)
    monkeypatch.delenv("VIBETHEPLAYLIST_PREVIEW_CACHE_PATH", raising=False)

    module = importlib.reload(config)
    original = (module.DATA_DIR, module.CACHE_DIR, module.DB_PATH, module.TOKEN_CACHE_PATH, module.PREVIEW_CACHE_PATH)

    with module.using_runtime_scope("user-7") as paths:
        assert module.DATA_DIR == paths.data_dir
        assert module.CACHE_DIR == paths.cache_dir
        assert module.DB_PATH == paths.db_path
        assert module.TOKEN_CACHE_PATH == paths.token_cache_path
        assert module.PREVIEW_CACHE_PATH == paths.preview_cache_path

    assert (module.DATA_DIR, module.CACHE_DIR, module.DB_PATH, module.TOKEN_CACHE_PATH, module.PREVIEW_CACHE_PATH) == original


def test_runtime_paths_for_scope_rejects_empty_sanitized_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBETHEPLAYLIST_DATA_DIR", str(tmp_path / "appdata"))
    module = importlib.reload(config)

    with pytest.raises(ValueError, match="Runtime scope must contain"):
        module.runtime_paths_for_scope("///")
