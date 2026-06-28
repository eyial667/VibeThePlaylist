"""Identifier resolution -> ISRC across all three input types, with name variants."""
from utils import text_utils as tu
from fakes import FakeMetadataProvider, load_fixture
from genreclass.resolver import IdentifierResolver


def _resolver():
    return IdentifierResolver(FakeMetadataProvider(load_fixture()))


def test_resolves_from_explicit_isrc():
    r = _resolver().resolve(isrc="USUM71902345", artist="Billie Eilish", title="Bad Guy")
    assert r.has_isrc and r.isrc == "USUM71902345"
    assert r.method == "isrc"
    assert r.match_confidence is None  # direct, not a name match


def test_resolves_from_spotify_id():
    r = _resolver().resolve(spotify_id="us0000000000000000a001")
    assert r.has_isrc and r.isrc == "USUG11904206"
    assert r.method == "spotify_id"
    assert r.title == "Blinding Lights"


def test_resolves_from_artist_title_with_confidence():
    r = _resolver().resolve(artist="Kendrick Lamar", title="HUMBLE.")
    assert r.has_isrc and r.isrc == "USUM71703861"
    assert r.method == "search"
    assert r.match_confidence is not None and r.match_confidence > 0.9
    assert not r.weak


def test_name_match_survives_accents_feat_and_remix_variants():
    res = _resolver()
    # accents stripped + feature credit added + remix suffix -> same recording
    r = res.resolve(artist="Rosalia", title="MALAMENTE (Remix)")
    assert r.isrc == "ESA0X1800010"
    r2 = res.resolve(artist="Bad Bunny feat. Jhay Cortez", title="Dakiti")
    assert r2.isrc == "QM6MZ2040267"
    r3 = res.resolve(artist="Soda Stereo", title="De Musica Ligera - Remasterizado")
    assert r3.isrc == "ARF119000010"


def test_all_three_input_types_reach_isrc():
    res = _resolver()
    for t in load_fixture():
        c = t.get("canonical")
        if not c:
            continue
        inp = t["input"]
        r = res.resolve(isrc=inp.get("isrc"), spotify_id=inp.get("spotify_id"),
                        artist=inp.get("artist"), title=inp.get("title"))
        assert r.has_isrc, f"{inp} failed to resolve"
        assert r.isrc == c["isrc"]


def test_unresolvable_name_falls_back_to_key_and_is_flagged():
    r = _resolver().resolve(artist="Totally Made Up Artist 9000",
                            title="A Song That Does Not Exist")
    assert not r.has_isrc
    assert r.key.startswith("key:")
    assert r.weak
    assert "no_isrc" in r.notes


def test_invalid_isrc_input_degrades_to_fallback():
    r = _resolver().resolve(isrc="not-a-real-isrc", artist="A", title="B")
    assert not r.has_isrc
    assert r.key == tu.fallback_key("A", "B")
