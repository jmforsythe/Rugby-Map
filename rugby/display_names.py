"""Compact team/league names for space-constrained graphics (social cards).

RFU names carry noise that eats horizontal space without helping a reader:
merit-league team names like ``"Darlington Mowden Park RFC 2nd XV Men"`` and
sponsor-laden league names like ``"Counties 4 Tribute Ale Somerset North"``.
These helpers shorten them to the forms the pyramid already uses
(``"Darlington Mowden Park II"``, ``"Counties 4 Somerset North"``). They are
display-only: never use the result as a lookup key.
"""

from __future__ import annotations

import re

_ORDINAL_ROMAN = {"2nd": "II", "3rd": "III", "4th": "IV", "5th": "V", "6th": "VI"}

_CB_TAG = re.compile(r"\s*\(CB\)")
_FULL_CLUB = (
    (re.compile(r"\bRugby Union Football Club\b"), "RUFC"),
    (re.compile(r"\bRugby Football Club\b"), "RFC"),
)
# "(2nd XV)" annotations on sides that already have their own name, e.g.
# "Birkenhead Park Wanderers (2nd XV)": the name alone identifies the team.
_PAREN_RESERVE = re.compile(r"\s*\(\s*(?:2nd|3rd|4th|5th|6th)\s+XVs?\s*\)\s*$", re.I)
# Trailing "2nd XV", "3rd XV Men", "2nd XV (Crusaders)" -> roman numeral.
_RESERVE_SUFFIX = re.compile(
    r"\s+(2nd|3rd|4th|5th|6th)\s+XVs?(?:\s+men)?(?:\s*\([^)]*\))?\s*$", re.I
)
_FIRST_XV_SUFFIX = re.compile(r"\s+1st\s+XV(?:\s+men)?\s*$", re.I)
_TRAILING_MEN = re.compile(r"\s+men\s*$", re.I)
_CLUB_SUFFIX = re.compile(r"\s+(?:RFC|RUFC)\s*$")

_LEAGUE_SPONSOR_WORDS = re.compile(r"\s+(?:Tribute Ale|Greene King|adm)\b")
_LEAGUE_LEADING_SPONSOR = re.compile(r"^Harvey'?s\s+(?:Wharf IPA|Brewery)\s+")
# NOWIRUL divisions carry a per-division sponsor between the prefix and "Division".
_NOWIRUL_SPONSOR = re.compile(r"^NOWIRUL\s+.*?\s*(Division\b)")
_SPACES = re.compile(r"\s{2,}")


def short_team_name(name: str) -> str:
    """``"Aldershot & Fleet RUFC 2nd XV Men"`` -> ``"Aldershot & Fleet II"``."""
    out = _CB_TAG.sub("", name.strip())
    for pattern, abbrev in _FULL_CLUB:
        out = pattern.sub(abbrev, out)
    out = _PAREN_RESERVE.sub("", out)
    reserve = _RESERVE_SUFFIX.search(out)
    if reserve:
        base = _CLUB_SUFFIX.sub("", out[: reserve.start()])
        out = f"{base} {_ORDINAL_ROMAN[reserve.group(1).lower()]}"
    else:
        out = _FIRST_XV_SUFFIX.sub("", out)
        out = _TRAILING_MEN.sub("", out)
    return _SPACES.sub(" ", out).strip() or name


def short_league_name(name: str) -> str:
    """``"Counties 4 Tribute Ale Somerset North"`` -> ``"Counties 4 Somerset North"``."""
    out = _LEAGUE_LEADING_SPONSOR.sub("", name.strip())
    out = _NOWIRUL_SPONSOR.sub(r"NOWIRUL \1", out)
    out = _LEAGUE_SPONSOR_WORDS.sub("", out)
    return _SPACES.sub(" ", out).strip() or name
