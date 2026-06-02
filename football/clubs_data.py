"""Shared OpenFootball clubs parsing, name matching, and Nominatim geocoding."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import requests

from rugby.geocode import flush_cache, geocode_with_nominatim, load_cache

logger = logging.getLogger(__name__)

_CLUBS_BASE = "https://raw.githubusercontent.com/openfootball/clubs/master/europe"
CLUBS_URLS = [
    f"{_CLUBS_BASE}/england/eng.clubs.txt",
    f"{_CLUBS_BASE}/wales/wal.clubs.txt",
]
_USER_AGENT = "RugbyMappingProject/1.0 (https://github.com/jmforsythe/Rugby-Map)"

# Default territory when parsing each OpenFootball clubs file.
_CLUBS_SOURCES: list[tuple[str, str]] = [
    (f"{_CLUBS_BASE}/england/eng.clubs.txt", "England"),
    (f"{_CLUBS_BASE}/wales/wal.clubs.txt", "Wales"),
]


def infer_territory_from_location(location: str, default: str = "England") -> str:
    """Infer Crown dependency / country from an OpenFootball location string."""
    loc = location.lower()
    if "isle of man" in loc:
        return "Isle of Man"
    if "jersey" in loc:
        return "Jersey"
    if "guernsey" in loc:
        return "Guernsey"
    if "wales" in loc:
        return "Wales"
    return default


def infer_territory_from_team(name: str, wiki_title: str = "") -> str:
    """Guess territory for a team with no OpenFootball club record."""
    text = f"{name} {wiki_title.replace('_', ' ')}".lower()
    if "isle of man" in text or " fc isle of man" in text:
        return "Isle of Man"
    if "jersey" in text:
        return "Jersey"
    if "guernsey" in text:
        return "Guernsey"
    # Welsh clubs playing in the English pyramid (common cases).
    if any(
        token in text
        for token in (
            "cardiff",
            "swansea",
            "wrexham",
            "merthyr",
            "newport county",
            "newport county afc",
        )
    ):
        return "Wales"
    return "England"


def build_club_address(ground: str, town: str, territory: str) -> str | None:
    """Build a geocodable address string with the correct territory suffix."""
    parts = [p for p in (ground, town) if p]
    if not parts:
        return None
    return ", ".join([*parts, territory])


# Clubs whose names Nominatim cannot resolve; use known home grounds instead.
_GROUND_OVERRIDES: dict[str, str] = {
    "jersey bulls": "Springfield Stadium, St Helier, Jersey",
    "isle of man": "The Bowl, Douglas, Isle of Man",
}


def ground_override_address(name: str) -> str | None:
    """Return a known ground address for clubs that fail name-based geocoding."""
    return _GROUND_OVERRIDES.get(normalize_club_name(name))


def download_text(url: str) -> str:
    """Fetch a UTF-8 text file over HTTPS."""
    resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


def normalize_club_name(name: str) -> str:
    """Normalise a club name for matching."""
    name = name.lower()
    name = name.replace("&", " and ")
    name = re.sub(r"[.\u2019']", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    tokens = [t for t in name.split() if t not in {"fc", "afc"}]
    return " ".join(tokens).strip()


def _clean_ground(ground: str) -> str:
    ground = ground.lstrip("@").strip()
    return ground.replace("$", "").strip()


def _primary_town(location: str) -> str:
    town = location.split("\u203a", 1)[0]
    town = town.split(",", 1)[0]
    town = re.sub(r"\(.*?\)", "", town)
    return re.sub(r"\s+", " ", town).strip()


def parse_clubs(text: str, *, default_territory: str = "England") -> list[dict]:
    """Parse an OpenFootball ``*.clubs.txt`` file."""
    clubs: list[dict] = []
    current: dict | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("#") or stripped.startswith("="):
            continue

        if stripped.startswith("|"):
            if current is None:
                continue
            for alias in stripped.lstrip("|").split("|"):
                alias = _strip_comment(alias).strip()
                if alias:
                    current["aliases"].append(alias)
            continue

        body = _strip_comment(line).strip()
        if not body:
            continue

        parts = [p.strip() for p in body.split(",")]
        name = parts[0]
        ground = ""
        location_parts: list[str] = []

        for part in parts[1:]:
            if not part:
                continue
            if part.startswith("@"):
                ground = _clean_ground(part)
            elif re.fullmatch(r"\d{4}", part):
                continue
            else:
                location_parts.append(part)

        location_raw = ", ".join(location_parts)
        town = _primary_town(location_raw)
        territory = infer_territory_from_location(location_raw, default_territory)
        address = build_club_address(ground, town, territory)

        current = {
            "name": name,
            "aliases": [],
            "ground": ground,
            "town": town,
            "territory": territory,
            "address": address,
        }
        clubs.append(current)

    return clubs


def build_lookup(clubs: list[dict]) -> dict[str, dict]:
    """Build a normalised-name -> club lookup from canonical names and aliases."""
    lookup: dict[str, dict] = {}
    for club in clubs:
        for key in [club["name"], *club["aliases"]]:
            norm = normalize_club_name(key)
            if norm:
                lookup.setdefault(norm, club)
    return lookup


def load_clubs_lookup() -> dict[str, dict]:
    """Download eng.clubs.txt + wal.clubs.txt and return the alias lookup."""
    clubs: list[dict] = []
    for url, territory in _CLUBS_SOURCES:
        clubs.extend(parse_clubs(download_text(url), default_territory=territory))
    lookup = build_lookup(clubs)
    logger.info("Loaded %d clubs, %d alias keys", len(clubs), len(lookup))
    return lookup


def _prefix_match(norm: str, key: str) -> bool:
    """Whole-word prefix match (avoids ``newport`` matching ``newport pagnell``)."""
    if len(key) < 4 or len(norm) < 4:
        return False
    if norm == key:
        return True
    # Single-word keys must match exactly.
    if " " not in key:
        return False
    return norm.startswith(f"{key} ") or key.startswith(f"{norm} ")


def match_club(name: str, lookup: dict[str, dict]) -> dict | None:
    """Match a team name to an OpenFootball club record."""
    norm = normalize_club_name(name)
    if norm in lookup:
        return lookup[norm]

    best: dict | None = None
    best_len = 0
    for key, club in lookup.items():
        if not _prefix_match(norm, key):
            continue
        if len(key) > best_len:
            best = club
            best_len = len(key)
    return best


def _town_fallback(address: str) -> str | None:
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) >= 3:
        return f"{parts[-2]}, {parts[-1]}"
    return address


def geocode_team(team: dict) -> dict:
    """Geocode a single team dict that has an ``address`` field."""
    result = dict(team)
    address = team.get("address")
    fallback = _town_fallback(address) if address else None

    coords = None
    if address:
        coords, _ = geocode_with_nominatim(address)

    if not coords and fallback and fallback != address:
        coords, _ = geocode_with_nominatim(fallback)
        if coords:
            result["geocode_precision"] = "town"

    if coords:
        result.update(coords)
    elif address:
        result["error"] = "geocoding_failed"
    else:
        result["error"] = "no_address"
    return result


def geocode_pyramid_team(team: dict, wikidata_coords: dict[str, dict]) -> dict:
    """Geocode a pyramid team using OpenFootball, Wikidata, then name fallback."""
    if team.get("address"):
        result = geocode_team(team)
        if "error" not in result:
            result["geocode_source"] = "openfootball"
            return result

    wiki_title = team.get("wiki_title", "")
    wikidata = wikidata_coords.get(wiki_title)
    if wikidata:
        result = dict(team)
        ground = wikidata.get("ground") or team.get("address")
        result["address"] = ground
        result["latitude"] = wikidata["latitude"]
        result["longitude"] = wikidata["longitude"]
        result["formatted_address"] = ground
        result["geocode_precision"] = "wikidata"
        result["geocode_source"] = "wikidata"
        return result

    name = team.get("name", "")
    override = ground_override_address(name)
    if override:
        coords, _ = geocode_with_nominatim(override)
        if coords:
            result = dict(team)
            result["address"] = override
            result.update(coords)
            result["geocode_precision"] = "ground_override"
            result["geocode_source"] = "ground_override"
            return result

    if name:
        territory = team.get("territory") or infer_territory_from_team(name, wiki_title)
        fallback_addr = f"{name}, {territory}"
        coords, _ = geocode_with_nominatim(fallback_addr)
        if coords:
            result = dict(team)
            result["address"] = fallback_addr
            result["territory"] = territory
            result.update(coords)
            result["geocode_precision"] = "name"
            result["geocode_source"] = "nominatim_name"
            return result

    result = dict(team)
    result["error"] = "geocoding_failed"
    return result


def geocode_pyramid_league(address_league: dict, wikidata_coords: dict[str, dict]) -> dict:
    """Geocode every team in a pyramid AddressLeague."""
    geocoded_teams: list[dict] = []
    success = 0

    for team in address_league["teams"]:
        result = geocode_pyramid_team(team, wikidata_coords)
        if "error" not in result:
            success += 1
        geocoded_teams.append(result)

    logger.info(
        "  Geocoded %d/%d teams in %s",
        success,
        len(geocoded_teams),
        address_league["league_name"],
    )

    return {
        "league_name": address_league["league_name"],
        "league_url": address_league["league_url"],
        "teams": geocoded_teams,
        "team_count": len(geocoded_teams),
    }


def geocode_league(address_league: dict) -> dict:
    """Geocode every team in an AddressLeague, returning a GeocodedLeague dict."""
    geocoded_teams: list[dict] = []
    success = 0

    for team in address_league["teams"]:
        result = geocode_team(team)
        if "error" not in result:
            success += 1
        geocoded_teams.append(result)

    logger.info(
        "  Geocoded %d/%d teams in %s",
        success,
        len(geocoded_teams),
        address_league["league_name"],
    )

    return {
        "league_name": address_league["league_name"],
        "league_url": address_league["league_url"],
        "teams": geocoded_teams,
        "team_count": len(geocoded_teams),
    }


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


_TIME_PREFIX = re.compile(r"^\d{1,2}:\d{2}\s+")
_SCORE_SUFFIX = re.compile(r"\s+\d+\s*-\s*\d+.*$")
_ANNOTATION_SUFFIX = re.compile(r"\s+\[[^\]]*\]\s*$")


def _clean_fixture_team(text: str) -> str:
    text = _TIME_PREFIX.sub("", text.strip())
    text = _SCORE_SUFFIX.sub("", text)
    text = _ANNOTATION_SUFFIX.sub("", text)
    return text.strip()


def parse_division_teams(text: str) -> list[str]:
    """Extract the team roster from an OpenFootball fixture file."""
    seen: dict[str, None] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if " v " not in line:
            continue
        left, right = line.split(" v ", 1)
        for team in (_clean_fixture_team(left), _clean_fixture_team(right)):
            if team and not team.startswith(("#", "▪", "=")):
                seen.setdefault(team, None)
    return list(seen.keys())


def build_address_league(
    league_name: str,
    league_url: str,
    team_names: list[str],
    lookup: dict[str, dict],
) -> tuple[dict, list[str]]:
    """Match team names to OpenFootball clubs and produce an AddressLeague dict."""
    teams: list[dict] = []
    unmatched: list[str] = []

    for name in team_names:
        club = match_club(name, lookup)
        if club:
            teams.append(
                {
                    "name": name,
                    "url": "",
                    "image_url": None,
                    "address": club["address"],
                    "territory": club["territory"],
                }
            )
            if not club["address"]:
                unmatched.append(f"{name} (no ground/town in clubs file)")
        else:
            teams.append({"name": name, "url": "", "image_url": None, "address": None})
            unmatched.append(f"{name} (no club match)")

    return (
        {
            "league_name": league_name,
            "league_url": league_url,
            "teams": teams,
            "team_count": len(teams),
        },
        unmatched,
    )


__all__ = [
    "CLUBS_URLS",
    "build_address_league",
    "build_club_address",
    "build_lookup",
    "download_text",
    "flush_cache",
    "geocode_league",
    "geocode_pyramid_league",
    "geocode_pyramid_team",
    "geocode_team",
    "infer_territory_from_location",
    "infer_territory_from_team",
    "load_cache",
    "load_clubs_lookup",
    "match_club",
    "normalize_club_name",
    "parse_clubs",
    "parse_division_teams",
    "write_json",
]
