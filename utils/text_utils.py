"""Text normalization for robust identifier matching across American, European
and Latin catalogs: folds diacritics, "feat."/"ft." credits, and remix/edit/
version suffixes so search queries and the fallback dedupe key agree on what
"the same track" is. Stdlib-only (unicodedata)."""
from __future__ import annotations

import re
import unicodedata

# Guest-credit markers: "feat.", "ft.", "featuring", "with", "con" (es/it/pt).
_FEAT_RE = re.compile(
    r"\s*[\(\[]?\s*(?:feat\.?|ft\.?|featuring|con|avec|with)\s+[^\)\]]*[\)\]]?\s*$",
    re.IGNORECASE,
)
# Trailing version/remix/edit qualifiers, usually in brackets:
#   "(Radio Edit)", "- Remastered 2011", "(Live)", "(Acoustic Version)",
#   "(DJ X Remix)", "- Single Version", "(Sped Up)", "(Remix)".
_VERSION_WORDS = (
    # English
    r"remaster\w*|remix|re-?edit|edit|radio edit|club mix|extended(?: mix)?|"
    r"mix|version|live|acoustic|unplugged|instrumental|demo|mono|stereo|"
    r"single version|album version|deluxe|bonus track|sped up|slowed|"
    r"reprise|interlude|skit|original mix|"
    # Spanish / Portuguese / French / Italian / German (multi-region catalog)
    r"remasteriz\w*|en vivo|ao vivo|en directo|directo|vers[ií][oó]n|vers[ãa]o|"
    r"ac[uú]stic[oa]|edici[oó]n|en concert|en public|dal vivo|edizione|"
    r"live-?version|radio-?edit"
)
_VERSION_RE = re.compile(
    r"\s*(?:[\(\[][^\)\]]*\b(?:" + _VERSION_WORDS + r")\b[^\)\]]*[\)\]]"
    r"|[-–—]\s*[^()\[\]]*\b(?:" + _VERSION_WORDS + r")\b.*)$",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def fold_accents(text: str) -> str:
    """Strip diacritics: 'Beyoncé' -> 'Beyonce', 'Sigur Rós' -> 'Sigur Ros'.

    NFKD-decomposes then drops combining marks; also maps a few ligatures/letters
    that don't decompose (ß, ø, æ, œ, đ, ł) which appear across European catalogs.
    """
    special = {"ß": "ss", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE",
               "œ": "oe", "Œ": "OE", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L"}
    text = "".join(special.get(ch, ch) for ch in text)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def strip_features(title: str) -> str:
    """Remove a trailing 'feat./ft./featuring …' credit from a title."""
    return _FEAT_RE.sub("", title).strip()


def strip_version(title: str) -> str:
    """Remove a trailing remix/edit/live/version qualifier from a title."""
    prev = None
    out = title
    # Apply repeatedly so "Song (Live) (Remastered)" collapses fully.
    while out != prev:
        prev = out
        out = _VERSION_RE.sub("", out).strip()
    return out


def core_title(title: str) -> str:
    """Title reduced to its core recording name: features + version stripped.

    Applied repeatedly because the two can be nested either way round, e.g.
    'Bad Guy (feat. X) - Sped Up' (version hides the feat) or
    'Song (Remix) (feat. Y)' (feat hides the version)."""
    prev, out = None, (title or "").strip()
    while out != prev:
        prev = out
        out = strip_features(strip_version(out)).strip()
    return out


def normalize(text: str | None) -> str:
    """Aggressive comparable form: accent-folded, lowercased, punctuation
    removed, whitespace collapsed. Used for fuzzy artist/title comparison."""
    if not text:
        return ""
    folded = fold_accents(text).lower()
    folded = _PUNCT_RE.sub(" ", folded)
    return _WS_RE.sub(" ", folded).strip()


def primary_artist(artist: str | None) -> str:
    """First credited artist, splitting on common multi-artist separators."""
    if not artist:
        return ""
    # Split on feat-style joiners and list separators; keep the first name.
    parts = re.split(r"\s*(?:,|&|/|;|\bx\b|\bfeat\.?\b|\bft\.?\b|\band\b|\bwith\b)\s*",
                     artist, flags=re.IGNORECASE)
    return parts[0].strip() if parts else artist.strip()


def fallback_key(artist: str | None, title: str | None) -> str:
    """Stable 'key:artist|title' identifier for tracks with no resolvable ISRC.

    Built from the normalized primary artist + core title so the same recording
    keyed twice (e.g. via slightly different variants) collapses to one row."""
    a = normalize(primary_artist(artist))
    t = normalize(core_title(title or ""))
    return f"key:{a}|{t}"


def is_fallback_key(key: str | None) -> bool:
    """True for a 'key:artist|title' fallback identifier (no real ISRC)."""
    return bool(key) and key.startswith("key:")


# Real ISRCs are 12 chars: 2-letter country + 3 alnum registrant + 2-digit year
# + 5-digit designation (commonly stored without hyphens).
_ISRC_RE = re.compile(r"^[A-Za-z]{2}[A-Za-z0-9]{3}\d{2}\d{5}$")


def is_real_isrc(value: str | None) -> bool:
    """True for a syntactically valid ISRC; False for fallback 'key:' ids."""
    return bool(value) and bool(_ISRC_RE.match(value.replace("-", "").strip()))


def clean_isrc(value: str | None) -> str | None:
    """Uppercase + de-hyphenate an ISRC; None if it isn't one."""
    if not value:
        return None
    candidate = value.replace("-", "").strip().upper()
    return candidate if is_real_isrc(candidate) else None


def strip_code_fences(text: str) -> str:
    """Strip a ```/```json markdown code fence from LLM output, tolerating a
    missing closing fence. Shared by the LLM passes that parse JSON replies."""
    text = (text or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.lstrip().lower().startswith("json"):
                text = text.lstrip()[4:]
    return text.strip()
