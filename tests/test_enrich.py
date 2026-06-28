"""Tests for Last.fm tag parsing and fallback logic (network mocked)."""
import config
from pipeline import enrich


def _payload(tags):
    return {"toptags": {"tag": [{"name": n, "count": c} for n, c in tags]}}


def test_parse_tags_filters_by_min_weight(monkeypatch):
    monkeypatch.setattr(config, "LASTFM_MIN_TAG_WEIGHT", 50)
    parsed = enrich._parse_tags(_payload([("rock", 90), ("obscure", 10)]))
    assert parsed == [("rock", 90)]


def test_parse_tags_caps_at_max(monkeypatch):
    monkeypatch.setattr(config, "LASTFM_MIN_TAG_WEIGHT", 0)
    monkeypatch.setattr(config, "LASTFM_MAX_TAGS", 2)
    parsed = enrich._parse_tags(_payload([("a", 9), ("b", 8), ("c", 7)]))
    assert len(parsed) == 2


def test_parse_tags_handles_single_tag_dict():
    # Last.fm returns a dict (not list) when there's exactly one tag
    payload = {"toptags": {"tag": {"name": "jazz", "count": 80}}}
    assert enrich._parse_tags(payload) == [("jazz", 80)]


def test_parse_tags_empty_payload():
    assert enrich._parse_tags({}) == []


def test_fetch_track_tags_falls_back_to_artist(monkeypatch):
    monkeypatch.setattr(config, "REQUEST_PAUSE_SEC", 0)
    calls = []

    def fake_lastfm(method, **params):
        calls.append(method)
        if method == "track.getTopTags":
            return {}  # no track-level tags -> should fall back
        return _payload([("soul", 75)])

    monkeypatch.setattr(enrich, "_lastfm", fake_lastfm)
    tags = enrich.fetch_track_tags("Artist", "Song")
    assert tags == [("soul", 75)]
    assert calls == ["track.getTopTags", "artist.getTopTags"]


def test_has_lastfm(monkeypatch):
    monkeypatch.setattr(config, "LASTFM_API_KEY", "")
    assert enrich.has_lastfm() is False
    monkeypatch.setattr(config, "LASTFM_API_KEY", "abc")
    assert enrich.has_lastfm() is True
