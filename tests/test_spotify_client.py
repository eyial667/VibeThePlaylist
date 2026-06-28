import time

import spotify_client


def test_token_has_required_scopes_accepts_superset():
    token = {"scope": "user-library-read playlist-modify-public playlist-modify-private"}
    assert spotify_client._token_has_required_scopes(token)


def test_token_has_required_scopes_rejects_missing_scope():
    token = {"scope": "user-library-read"}
    assert not spotify_client._token_has_required_scopes(token)


def test_is_authenticated_requires_valid_token_and_scopes(monkeypatch):
    token = {
        "scope": "user-library-read playlist-modify-public playlist-modify-private",
        "expires_at": time.time() + 60,
    }
    monkeypatch.setattr(spotify_client, "_get_cached_token", lambda cache_handler=None: token)
    assert spotify_client.is_authenticated()


def test_is_authenticated_rejects_scope_mismatch(monkeypatch):
    token = {
        "scope": "user-library-read",
        "refresh_token": "refreshable-but-under-scoped",
        "expires_at": time.time() - 60,
    }
    monkeypatch.setattr(spotify_client, "_get_cached_token", lambda cache_handler=None: token)
    assert not spotify_client.is_authenticated()


def test_is_authenticated_accepts_injected_mapping_cache():
    store = {
        "spotify_token_info": {
            "scope": "user-library-read playlist-modify-public playlist-modify-private",
            "refresh_token": "refreshable",
            "expires_at": time.time() - 60,
        }
    }
    assert spotify_client.is_authenticated(store)


def test_clear_cached_token_if_scope_mismatch_logs_out(monkeypatch):
    token = {"scope": "user-library-read"}
    called = {"logout": 0}

    monkeypatch.setattr(spotify_client, "_get_cached_token", lambda cache_handler=None: token)
    monkeypatch.setattr(
        spotify_client,
        "logout",
        lambda cache_handler=None: called.__setitem__("logout", called["logout"] + 1),
    )

    assert spotify_client._clear_cached_token_if_scope_mismatch()
    assert called["logout"] == 1


def test_logout_clears_injected_mapping_cache():
    store = {"spotify_token_info": {"access_token": "x"}}
    spotify_client.logout(store)
    assert "spotify_token_info" not in store


def test_get_client_pkce_clears_scope_mismatch_before_auth(monkeypatch):
    called = {"clear": 0}
    store = {}

    class DummyPKCE:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class DummySpotify:
        def __init__(self, auth_manager):
            self.auth_manager = auth_manager

    monkeypatch.setattr(spotify_client.config, "SPOTIFY_CLIENT_ID", "client-id")
    monkeypatch.setattr(
        spotify_client,
        "_clear_cached_token_if_scope_mismatch",
        lambda cache_handler=None, scopes=None: called.__setitem__("clear", called["clear"] + 1),
    )
    monkeypatch.setattr(spotify_client, "SpotifyPKCE", DummyPKCE)
    monkeypatch.setattr(spotify_client.spotipy, "Spotify", DummySpotify)

    client = spotify_client.get_client_pkce(cache_handler=store)
    assert called["clear"] == 1
    assert isinstance(client.auth_manager, DummyPKCE)
    assert isinstance(client.auth_manager.kwargs["cache_handler"], spotify_client.MappingCacheHandler)


def test_get_client_uses_injected_mapping_cache(monkeypatch):
    store = {}

    class DummyOAuth:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class DummySpotify:
        def __init__(self, auth_manager):
            self.auth_manager = auth_manager

    monkeypatch.setattr(spotify_client.config, "SPOTIFY_CLIENT_ID", "client-id")
    monkeypatch.setattr(spotify_client.config, "SPOTIFY_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(spotify_client, "SpotifyOAuth", DummyOAuth)
    monkeypatch.setattr(spotify_client.spotipy, "Spotify", DummySpotify)

    client = spotify_client.get_client(cache_handler=store)
    assert isinstance(client.auth_manager.kwargs["cache_handler"], spotify_client.MappingCacheHandler)
