"""Look up a team's RFU Constituent Body (the county union or services body a
club is affiliated to), from the club/CB export in ``data/rugby/club_cb_mapping.json``.

Lookups go through the canonical club name in ``club_names.json`` (the same
name used everywhere else in the codebase to group a club's teams), then
match it against the RFU export case-insensitively. Beyond that there's no
attempt to strip a club-type suffix down to a bare name or guess at a club
from a partial/prefix match: two differently-named clubs (e.g. "Wasps" and
"Wasps FC", which are different real clubs) must never be collapsed into the
same CB lookup, so a miss here just means the CB is unknown rather than
guessed.

The one exception is a small, low-risk fallback for two known RFU-export
inconsistencies that don't reflect different clubs: some CBs (e.g. Hampshire)
append "Ltd"/"Limited" to every club name, and the export is inconsistent
about "RFC" vs "RUFC" vs "Rugby Football Club" vs "Rugby Club". Both sides of
that fallback still require a club-type suffix to be present -- a bare name
like "Wasps" never matches a suffixed one like "Wasps FC" -- so it only
equates formatting differences, never a real vs. absent suffix.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from rugby import DATA_DIR
from rugby.clubs import CLUB_NAMES_FILE, load_team_club_map, resolve_club_name

CLUB_CB_MAPPING_PATH = DATA_DIR / "club_cb_mapping.json"

# Trailing "Ltd"/"Ltd."/"(Ltd)"/"Limited" -- a legal-entity marker some CBs
# (e.g. Hampshire) append to every club name in the export, never a
# distinguishing part of a club's identity.
_LTD_SUFFIX_RE = re.compile(r"\s*\(?\bltd\.?\)?\s*$|\s*\blimited\s*$", re.IGNORECASE)

# Club-type suffix words the RFU export spells inconsistently for the same
# club. Normalized to one token, never removed entirely, so a name with no
# club-type suffix at all can never match one that has one.
_CLUB_TYPE_EQUIV_RE = re.compile(
    r"\b(rufc|rfc|rugby union football club|rugby football club|rugby club)\b",
    re.IGNORECASE,
)


def _normalize_for_fallback(name: str) -> str:
    n = _LTD_SUFFIX_RE.sub("", name).strip()
    n = _CLUB_TYPE_EQUIV_RE.sub("rfc", n)
    return n


@lru_cache(maxsize=1)
def _load_team_club_map() -> dict[str, str]:
    if not CLUB_NAMES_FILE.exists():
        return {}
    return load_team_club_map(CLUB_NAMES_FILE)


@lru_cache(maxsize=1)
def _load_club_cb_map() -> dict[str, str]:
    if not CLUB_CB_MAPPING_PATH.exists():
        return {}
    with open(CLUB_CB_MAPPING_PATH, encoding="utf-8") as f:
        raw_mapping: dict[str, str] = json.load(f)
    return {club.strip().lower(): cb for club, cb in raw_mapping.items()}


@lru_cache(maxsize=1)
def _load_fallback_club_cb_map() -> dict[str, str]:
    club_cb_map = _load_club_cb_map()
    fallback: dict[str, str] = {}
    for club, cb in club_cb_map.items():
        fallback.setdefault(_normalize_for_fallback(club), cb)
    return fallback


def get_constituent_body(team_name: str) -> str | None:
    """The Constituent Body a team's club is affiliated to, or ``None`` if unknown.

    Resolves ``team_name`` to its canonical club name (via ``club_names.json``)
    and looks that up in the RFU export directly. Falls back to a Ltd/Limited-
    and club-type-suffix-normalized match (see module docstring) if the exact
    match fails.
    """
    club_cb_map = _load_club_cb_map()
    if not club_cb_map:
        return None
    canonical = resolve_club_name(team_name, _load_team_club_map())
    exact = club_cb_map.get(canonical.strip().lower())
    if exact is not None:
        return exact
    return _load_fallback_club_cb_map().get(_normalize_for_fallback(canonical).lower())
