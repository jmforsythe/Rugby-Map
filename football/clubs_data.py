"""Football team geocoding helpers and territory inference."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from rugby.geocode import flush_cache, geocode_with_nominatim, load_cache

logger = logging.getLogger(__name__)


def infer_territory_from_location(location: str, default: str = "England") -> str:
    """Infer Crown dependency / country from a ground or location string."""
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
    """Guess territory for a team when the ground string has no country hint."""
    text = f"{name} {wiki_title.replace('_', ' ')}".lower()
    if "isle of man" in text or " fc isle of man" in text:
        return "Isle of Man"
    if "jersey" in text:
        return "Jersey"
    if "guernsey" in text:
        return "Guernsey"
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


# Clubs whose names Nominatim cannot resolve; use known home grounds instead.
_GROUND_OVERRIDES: dict[str, str] = {
    "jersey bulls": "Springfield Stadium, St Helier, Jersey",
    "isle of man": "The Bowl, Douglas, Isle of Man",
}


def normalize_club_name(name: str) -> str:
    """Normalise a club name for override lookup."""
    name = name.lower()
    name = name.replace("&", " and ")
    name = re.sub(r"[.\u2019']", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    tokens = [t for t in name.split() if t not in {"fc", "afc"}]
    return " ".join(tokens).strip()


def ground_override_address(name: str) -> str | None:
    """Return a known ground address for clubs that fail name-based geocoding."""
    return _GROUND_OVERRIDES.get(normalize_club_name(name))


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
        if team.get("address_source"):
            result["geocode_source"] = team["address_source"]
    elif address:
        result["error"] = "geocoding_failed"
    else:
        result["error"] = "no_address"
    return result


def geocode_pyramid_team(team: dict, wikidata_coords: dict[str, dict]) -> dict:
    """Geocode a pyramid team using Wikidata, Wikipedia ground, then name fallback."""
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

    if team.get("address"):
        result = geocode_team(team)
        if "error" not in result:
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


__all__ = [
    "flush_cache",
    "geocode_league",
    "geocode_pyramid_league",
    "geocode_pyramid_team",
    "geocode_team",
    "ground_override_address",
    "infer_territory_from_location",
    "infer_territory_from_team",
    "load_cache",
    "normalize_club_name",
    "write_json",
]
