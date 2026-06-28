"""Text-normalization robustness across American / European / Latin titles."""
import text_utils as tu


def test_fold_accents_across_regions():
    assert tu.fold_accents("Beyoncé") == "Beyonce"
    assert tu.fold_accents("Sigur Rós") == "Sigur Ros"
    assert tu.fold_accents("José José") == "Jose Jose"
    assert tu.fold_accents("Désenchantée") == "Desenchantee"
    assert tu.fold_accents("Mötley Crüe") == "Motley Crue"
    # ligatures / non-decomposing letters
    assert tu.fold_accents("Æther") == "AEther"
    assert tu.fold_accents("Straße") == "Strasse"
    assert tu.fold_accents("SØlv") == "SOlv"


def test_strip_features_variants():
    assert tu.strip_features("Dákiti (feat. Jhay Cortez)") == "Dákiti"
    assert tu.strip_features("This Is What You Came For (feat. Rihanna)") == \
        "This Is What You Came For"
    assert tu.strip_features("TQG (with Shakira)") == "TQG"
    assert tu.strip_features("Song ft. Someone") == "Song"
    assert tu.strip_features("No Credit Here") == "No Credit Here"


def test_strip_version_variants():
    assert tu.strip_version("Blinding Lights - Remastered 2021") == "Blinding Lights"
    assert tu.strip_version("Enter Sandman - Remastered 2021") == "Enter Sandman"
    assert tu.strip_version("De Música Ligera (Remasterizado)") == "De Música Ligera"
    assert tu.strip_version("Track (Radio Edit)") == "Track"
    assert tu.strip_version("Track (DJ X Remix)") == "Track"
    assert tu.strip_version("Track (Live)") == "Track"


def test_core_title_handles_nesting_either_way():
    assert tu.core_title("Bad Guy (feat. X) - Sped Up") == "Bad Guy"
    assert tu.core_title("Song (Remix) (feat. Y)") == "Song"
    assert tu.core_title("Dákiti (feat. Jhay Cortez) - Remix") == "Dákiti"


def test_primary_artist_splits_collaborations():
    assert tu.primary_artist("Calvin Harris feat. Rihanna") == "Calvin Harris"
    assert tu.primary_artist("Bad Bunny, Jhay Cortez") == "Bad Bunny"
    assert tu.primary_artist("KAROL G & Shakira") == "KAROL G"
    assert tu.primary_artist("Daft Punk") == "Daft Punk"


def test_fallback_key_is_stable_across_variants():
    a = tu.fallback_key("Bad Bunny, Jhay Cortez", "Dákiti (feat. Jhay Cortez)")
    b = tu.fallback_key("bad bunny", "Dakiti - Remix")
    assert a == b == "key:bad bunny|dakiti"


def test_isrc_validation_and_cleaning():
    assert tu.is_real_isrc("USUM71902345")
    assert tu.is_real_isrc("QM6MZ2040267")
    assert not tu.is_real_isrc("not-an-isrc")
    assert not tu.is_real_isrc("key:artist|title")
    assert tu.clean_isrc("us-um7-19-02345") == "USUM71902345"
    assert tu.clean_isrc("garbage") is None
