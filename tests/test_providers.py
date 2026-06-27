"""ReccoBeats feature provider: lookup hit, Deezer->extraction fallback, caching.

Drives the REAL ReccoBeatsFeatureProvider / DeezerClient against a FakeSession,
so the lookup->extraction control flow and the DB cache layer are exercised
without any network.
"""
from fakes import FakeResponse, FakeSession
from genreclass.providers import (SRC_EXTRACTED, SRC_LOOKUP, DeezerClient,
                       ReccoBeatsFeatureProvider)

RB = "https://api.reccobeats.com"
DZ = "https://api.deezer.com"

FEATURES = {"energy": 0.65, "danceability": 0.4, "valence": 0.34,
            "acousticness": 0.33, "tempo": 140.0, "loudness": -6.5}


def _provider(handler):
    session = FakeSession(handler)
    deezer = DeezerClient(base_url=DZ, session=session)
    prov = ReccoBeatsFeatureProvider(base_url=RB, deezer=deezer, session=session,
                                     api_key="")
    return prov, session


def test_lookup_hit_returns_features_from_reccobeats(temp_db):
    def handler(method, url, **kw):
        if url.endswith("/v1/track"):
            return FakeResponse(json_data={"content": [{"id": "rb-123"}]})
        if url.endswith("/v1/audio-features"):
            return FakeResponse(json_data={"content": [FEATURES]})
        raise AssertionError(f"unexpected call {method} {url}")

    prov, session = _provider(handler)
    feats = prov.get_features(isrc="USUM71902345", spotify_id="sp123")
    assert feats["features_source"] == SRC_LOOKUP
    assert feats["energy"] == 0.65 and feats["tempo"] == 140.0
    # Deezer was never touched on a lookup hit.
    assert not any(DZ in u for _, u in session.calls)


def test_extraction_fallback_fires_on_lookup_miss(temp_db):
    """Lookup returns no ReccoBeats id -> Deezer preview -> POST extraction."""
    posted = {}

    def handler(method, url, **kw):
        if url.endswith("/v1/track"):
            return FakeResponse(json_data={"content": []})         # miss
        if url.startswith(DZ + "/search"):
            return FakeResponse(json_data={"data": [{"preview": DZ + "/clip.mp3"}]})
        if url == DZ + "/clip.mp3":
            return FakeResponse(content=b"ID3fake-mp3-bytes")
        if url.endswith("/v1/analysis/audio-features"):
            posted["files"] = kw.get("files")
            return FakeResponse(json_data=FEATURES)               # bare object
        raise AssertionError(f"unexpected call {method} {url}")

    prov, session = _provider(handler)
    feats = prov.get_features(isrc="USUM71902345", spotify_id="sp123",
                              artist="Billie Eilish", title="Bad Guy")
    assert feats["features_source"] == SRC_EXTRACTED
    assert feats["energy"] == 0.65
    # The fallback actually POSTed the downloaded clip under field 'audioFile'.
    assert "audioFile" in posted["files"]
    assert any(u.endswith("/v1/analysis/audio-features") for _, u in session.calls)
    assert any(u.startswith(DZ + "/search") for _, u in session.calls)


def test_returns_none_when_all_sources_miss(temp_db):
    def handler(method, url, **kw):
        if url.endswith("/v1/track"):
            return FakeResponse(json_data={"content": []})
        if url.startswith(DZ + "/search"):
            return FakeResponse(json_data={"data": []})  # no preview
        raise AssertionError(f"unexpected call {method} {url}")

    prov, _ = _provider(handler)
    assert prov.get_features(isrc="ZZZZ99999999", spotify_id="x",
                             artist="No", title="One") is None


def test_features_are_cached_by_isrc(temp_db):
    calls = {"n": 0}

    def handler(method, url, **kw):
        calls["n"] += 1
        if url.endswith("/v1/track"):
            return FakeResponse(json_data={"content": [{"id": "rb-1"}]})
        if url.endswith("/v1/audio-features"):
            return FakeResponse(json_data={"content": [FEATURES]})
        raise AssertionError(url)

    prov, _ = _provider(handler)
    a = prov.get_features(isrc="USUM71902345", spotify_id="sp")
    n_after_first = calls["n"]
    b = prov.get_features(isrc="USUM71902345", spotify_id="sp")  # served from cache
    assert a == b
    assert calls["n"] == n_after_first  # no further HTTP calls


def test_http_4xx_miss_is_not_retried(temp_db):
    def handler(method, url, **kw):
        if url.endswith("/v1/track"):
            return FakeResponse(status=404)
        if url.startswith(DZ + "/search"):
            return FakeResponse(json_data={"data": []})
        raise AssertionError(url)

    prov, _ = _provider(handler)
    # 404 on lookup -> falls through to extraction (which also misses) -> None
    assert prov.get_features(isrc="USUM71902345", spotify_id="sp",
                             artist="A", title="B") is None
