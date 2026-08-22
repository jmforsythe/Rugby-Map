"""Look up a team's RFU Constituent Body (the county union or services body a
club is affiliated to), from the club/CB export in ``data/rugby/club_cb_mapping.json``.

Team names in our geocoded data are individual sides ("Barnes Women II",
"Ampthill 5th XV") rather than the club names the RFU export uses ("Barnes
RFC", "Cobham Rugby Football Club Limited"), so lookups normalize both sides:
strip the club-type suffix ("RFC", "Rugby Club", "Ltd", ...), then strip
known team/gender/squad-tier words until either a match is found or nothing
is left to strip.

If that still finds nothing (e.g. a branded team-tier name like "Fylde Hawks
(2nd XV)", where "Hawks" isn't a word we know to strip), we fall back to the
longest known club name that is a word-boundary prefix of the team name --
"fylde hawks" isn't a club, but "fylde" is, and it's the longest such prefix
in the export. This is best-effort: nickname-only team names for clubs whose
RFU entry doesn't include that nickname, and standalone professional sides
not in the amateur club export (e.g. "Bristol Bears"), still won't match.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from rugby import DATA_DIR
from rugby.addresses import team_name_to_club_name
from rugby.clubs import CLUB_NAMES_FILE, load_team_club_map, resolve_club_name

CLUB_CB_MAPPING_PATH = DATA_DIR / "club_cb_mapping.json"

# Trailing tokens stripped one at a time (in order, from the end) when a
# lookup doesn't match. These cover team-number, gender, and squad-tier words
# appended to a club's base name -- club-type words ("RFC", "Rugby Club",
# ...) are handled separately by _strip_club_type_suffix.
_STRIPPABLE_SUFFIX_WORDS = frozenset(
    {
        "women",
        "womens",
        "ladies",
        "men",
        "mens",
        "girls",
        "development",
        "occasionals",
        "colts",
        "vets",
        "veterans",
        "academy",
        "junior",
        "juniors",
        "2nds",
        "3rds",
        "4ths",
        "5ths",
        "i",
        "ii",
        "iii",
        "iv",
        "v",
        "vi",
        "vii",
        "viii",
        "1st",
        "2nd",
        "3rd",
        "4th",
        "5th",
        "6th",
        "7th",
        "8th",
        "1xv",
        "2xv",
        "3xv",
        "4xv",
        "5xv",
        "xv",
        "xvs",
    }
)

# Club-type words/phrases stripped from the end, e.g. "Cobham Rugby Football
# Club Limited" -> "Cobham". Longer phrases are listed first so "rugby club"
# matches whole rather than leaving a dangling "club" or "rugby".
_CLUB_TYPE_SUFFIX_RE = re.compile(
    r"\s+(rugby football club|rugby club|rfc|rufc|rfu|fc|rugby)(\s+(ltd|limited))?$"
)
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")
_TRAILING_DASH_ANNOTATION_RE = re.compile(r"\s+-\s+\S.*$")
_PUNCTUATION_RE = re.compile(r"[.'’]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(name: str) -> str:
    n = name.strip().lower()
    n = n.replace("&", " and ")
    n = _PUNCTUATION_RE.sub("", n)
    if n.startswith("the "):
        n = n[4:]
    return _WHITESPACE_RE.sub(" ", n).strip()


def _strip_club_type_suffix(name: str) -> str:
    """Repeatedly strip a trailing "RFC"/"Rugby Club"/"Ltd"/etc. suffix."""
    while True:
        stripped = _CLUB_TYPE_SUFFIX_RE.sub("", name)
        if stripped == name:
            return name
        name = stripped


@lru_cache(maxsize=1)
def _load_team_club_map() -> dict[str, str]:
    if not CLUB_NAMES_FILE.exists():
        return {}
    return load_team_club_map(CLUB_NAMES_FILE)


@lru_cache(maxsize=1)
def _load_normalized_club_cb_map() -> dict[str, str]:
    if not CLUB_CB_MAPPING_PATH.exists():
        return {}
    with open(CLUB_CB_MAPPING_PATH, encoding="utf-8") as f:
        raw_mapping: dict[str, str] = json.load(f)

    normalized: dict[str, str] = {}
    for club, cb in raw_mapping.items():
        key = _strip_club_type_suffix(_normalize(club))
        normalized.setdefault(key, cb)
    return normalized


# A prefix match shorter than this is too likely to be a coincidental
# collision with an unrelated club (short/generic real club names like "Ash"
# or "Bp" are common in the export) to trust as a fallback guess.
_MIN_PREFIX_MATCH_LENGTH = 4


def _normalized_bases(team_name: str) -> list[str]:
    """Normalized, club-type-suffix-stripped forms of ``team_name``, without
    stripping team/gender/squad-tier words (that happens in ``_lookup_candidates``).
    """
    # team_name_to_club_name (shared with the rest of the codebase's club
    # grouping) already strips the common "II"/"2nd XV" shapes; run it first
    # so both this module and everything else agree on the base club name.
    raw_bases = {team_name_to_club_name(team_name), team_name}

    bases: list[str] = []
    seen: set[str] = set()
    for raw_base in raw_bases:
        base = _normalize(raw_base)
        base = _TRAILING_PAREN_RE.sub("", base).strip()
        base = _TRAILING_DASH_ANNOTATION_RE.sub("", base).strip()
        base = _strip_club_type_suffix(base)
        if base and base not in seen:
            seen.add(base)
            bases.append(base)
    return bases


def _lookup_candidates(team_name: str) -> list[str]:
    """Progressively shorter normalized forms of ``team_name``, longest first."""
    candidates: list[str] = []
    seen: set[str] = set()
    for base in _normalized_bases(team_name):
        tokens = base.split(" ")
        forms = [" ".join(tokens)]
        while tokens and tokens[-1] in _STRIPPABLE_SUFFIX_WORDS:
            tokens = tokens[:-1]
            forms.append(_strip_club_type_suffix(" ".join(tokens)))

        for form in forms:
            if form and form not in seen:
                seen.add(form)
                candidates.append(form)

    return candidates


def _longest_prefix_match(team_name: str, club_cb_map: dict[str, str]) -> str | None:
    """The CB for the longest known club name that is a word-boundary prefix
    of (a normalized form of) ``team_name``.

    Catches branded team-tier names the suffix-word list doesn't know about,
    e.g. "Fylde Hawks (2nd XV)" -> "fylde hawks" isn't a club, but "fylde" is,
    and it's the longest known club name prefixing it.
    """
    best_key: str | None = None
    for base in _normalized_bases(team_name):
        for key in club_cb_map:
            if len(key) < _MIN_PREFIX_MATCH_LENGTH:
                continue
            matches = base == key or base.startswith(key + " ")
            if matches and (best_key is None or len(key) > len(best_key)):
                best_key = key
    return club_cb_map[best_key] if best_key is not None else None


def get_constituent_body(team_name: str) -> str | None:
    """The Constituent Body a team's club is affiliated to, or ``None`` if unknown.

    Tries the ``club_names.json`` canonical club name first -- unlike the
    generic normalization below, this preserves disambiguating parentheticals
    like "Leigh (Kent)" that distinguish same-named clubs in different
    counties, rather than stripping them as if they were a squad-tier
    annotation. Then tries the full (normalized) team name, then
    progressively strips trailing team-number/gender/suffix words (e.g.
    "Barnes Women II" -> "Barnes Women" -> "Barnes") until a club match is
    found. Failing that, falls back to the longest known club name prefixing
    the team name (see ``_longest_prefix_match``).
    """
    club_cb_map = _load_normalized_club_cb_map()
    if not club_cb_map:
        return None
    canonical = resolve_club_name(team_name, _load_team_club_map())
    canonical_key = _strip_club_type_suffix(_normalize(canonical))
    if canonical_key in club_cb_map:
        return club_cb_map[canonical_key]
    for candidate in _lookup_candidates(team_name):
        if candidate in club_cb_map:
            return club_cb_map[candidate]
    return _longest_prefix_match(team_name, club_cb_map)
