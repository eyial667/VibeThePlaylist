"""HaikuClassifier: strict JSON parsing, taxonomy coercion, energy derivation."""
import taxonomy as tax
from genre_classifier import (Classification, HaikuClassifier, coerce_to_taxonomy,
                              energy_from_features, parse_json_object)


class _Msg:
    def __init__(self, text):
        self.content = [type("B", (), {"text": text})()]


class _Client:
    """Returns a queued list of assistant texts (one per .create call)."""
    def __init__(self, *texts):
        self._texts = list(texts)
        self.calls = 0

        class _M:
            def create(inner, **kw):
                self.calls += 1
                return _Msg(self._texts[min(self.calls - 1, len(self._texts) - 1)])
        self.messages = _M()


def _clf(*texts):
    return HaikuClassifier(taxonomy=tax.load(), client=_Client(*texts))


def test_parses_prefilled_json_object():
    obj = parse_json_object('"genre": "Pop"}', prefill="{")
    assert obj == {"genre": "Pop"}
    # fenced output is tolerated too
    assert parse_json_object('```json\n{"genre": "Rock"}\n```') == {"genre": "Rock"}


def test_valid_output_kept_and_energy_from_features():
    c = _clf('"genre": "Latin", "subgenre": "reggaeton", "energy": "low", '
             '"vibe": ["sensual","energetic"], "confidence": 0.9, '
             '"suggested_label": null, "notes": null}')
    r = c.classify({"title": "Dákiti", "artist": "Bad Bunny",
                    "features": {"energy": 0.82, "danceability": 0.7}})
    assert r.genre == "Latin" and r.subgenre == "reggaeton"
    assert r.vibe == ["sensual", "energetic"]
    # numeric energy (0.82 -> high) overrides the model's "low"
    assert r.energy == "high"


def test_unknown_genre_becomes_other_with_suggested_label():
    c = _clf('"genre": "Polka", "subgenre": "oompah", "energy": "mid", '
             '"vibe": ["playful","bogus-vibe"], "confidence": 1.5}')
    r = c.classify({"title": "X", "artist": "Y", "features": None})
    assert r.genre == tax.OTHER
    assert r.suggested_label == "Polka"
    assert r.subgenre is None            # dropped: not under a valid genre
    assert r.vibe == ["playful"]         # unknown vibe filtered out
    assert r.confidence == 1.0           # clamped to [0,1]
    assert r.energy == "mid"


def test_invalid_json_retries_then_falls_back_safely():
    c = _clf("not json", "still not json")
    r = c.classify({"title": "X", "artist": "Y"})
    assert c._client.calls == 2          # one retry happened
    assert r.genre == tax.OTHER
    assert r.notes == "unparseable_model_output"
    assert r.energy == "mid"             # never null, even on total failure


def test_retry_succeeds_on_second_attempt():
    c = _clf("garbage", '"genre": "Jazz", "energy": "low", "vibe": ["chill"]}')
    r = c.classify({"title": "So What", "artist": "Miles Davis", "features": None})
    assert c._client.calls == 2
    assert r.genre == "Jazz" and r.vibe == ["chill"]


def test_energy_from_features_bands():
    assert energy_from_features({"energy": 0.1}) == "low"
    assert energy_from_features({"energy": 0.55}) == "mid"
    assert energy_from_features({"energy": 0.9}) == "high"
    # falls back to danceability when energy missing
    assert energy_from_features({"danceability": 0.8}) == "high"
    assert energy_from_features({}) is None
    assert energy_from_features(None) is None


def test_coerce_to_taxonomy_filters_subgenre_against_genre():
    t = tax.load()
    c = Classification(genre="Jazz", subgenre="reggaeton", energy="high",
                       vibe=["chill"])
    out = coerce_to_taxonomy(c, t)
    assert out.genre == "Jazz"
    assert out.subgenre is None  # reggaeton isn't a Jazz subgenre
    assert out.energy == "high"
