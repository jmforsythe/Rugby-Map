"""Football team geocoding helpers and territory inference."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from football.league_names import league_search_geography
from football.location_sanity import (
    flag_league_location_outliers,
    is_definitely_wrong_location,
    pick_best_league_geocode,
    team_geocode_source,
)
from rugby.geocode import flush_cache, geocode_with_nominatim, load_cache, search_nominatim

logger = logging.getLogger(__name__)

NOMINATIM_MULTI_LIMIT = 5


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
    "wivenhoe town": "Broad Lane, Wivenhoe CO7 9QT, Essex, England",
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


def _wikidata_ground_is_venue(ground: str, team: dict) -> bool:
    """False when Wikidata's ground label is just the club name, not a stadium address."""
    text = ground.strip()
    if not text:
        return False
    label = text.casefold()
    name = (team.get("name") or "").casefold().strip()
    if label == name:
        return False
    if label.rstrip(".") in {f"{name} f.c.", f"{name} fc", f"{name} football club"}:
        return False
    if re.search(r"\bf\.?c\.?\s*$", label):
        return False
    return True


def _nominatim_candidates(address: str, team: dict) -> list[str]:
    """Build ordered Nominatim query strings for a Wikipedia infobox ground."""
    candidates = [address]
    territory = team.get("territory") or "England"
    if territory == "England" and address.count(",") < 2:
        candidates.append(f"{address}, England")
    fallback = _town_fallback(address)
    if fallback and fallback not in candidates:
        candidates.append(fallback)
    return candidates


def _finalize_geocoded_team(result: dict) -> dict:
    """Drop failure/outlier metadata after a successful geocode."""
    for key in (
        "error",
        "location_outlier",
        "centroid_distance_km",
        "centroid_distance_z",
        "geocode_recalculated",
        "geocode_replaced_source",
    ):
        result.pop(key, None)
    return result


def enriched_club_nominatim_queries(
    team_name: str,
    league_name: str,
    territory: str = "England",
) -> list[str]:
    """Build ordered Nominatim queries using club name, league region, and football club."""
    geo = league_search_geography(league_name)
    name = team_name.strip()
    queries: list[str] = []
    if geo:
        queries.extend(
            (
                f"{name} {geo} FC, {territory}",
                f"{name} {geo} football club, {territory}",
                f"{name} FC {geo}, {territory}",
                f"{name}, {geo}, {territory}",
            )
        )
    else:
        queries.extend(
            (
                f"{name} football club, {territory}",
                f"{name} FC, {territory}",
                f"{name}, {territory}",
            )
        )
    seen: set[str] = set()
    ordered: list[str] = []
    for query in queries:
        if query not in seen:
            seen.add(query)
            ordered.append(query)
    return ordered


def _coords_identity(coords: dict) -> str | tuple[float, float]:
    place_id = coords.get("place_id")
    if place_id not in (None, ""):
        return str(place_id)
    return (float(coords["latitude"]), float(coords["longitude"]))


def geocode_with_club_queries(
    team: dict,
    league_name: str,
    *,
    source: str = "nominatim_club",
    league: dict | None = None,
    team_index: int | None = None,
) -> dict | None:
    """Geocode a team using league-context club search queries."""
    name = team.get("name", "")
    if not name or not league_name:
        return None
    wiki_title = team.get("wiki_title", "")
    territory = team.get("territory") or infer_territory_from_team(name, wiki_title)

    candidates: list[tuple[str, dict]] = []
    seen: set[str | tuple[float, float]] = set()
    for query in enriched_club_nominatim_queries(name, league_name, territory):
        for coords in search_nominatim(query, limit=NOMINATIM_MULTI_LIMIT):
            key = _coords_identity(coords)
            if key in seen:
                continue
            seen.add(key)
            candidates.append((query, coords))

    if not candidates:
        return None

    if league is not None and team_index is not None:
        best_coords = pick_best_league_geocode(
            [coords for _, coords in candidates],
            league,
            team_index,
        )
        if best_coords is None:
            return None
        best_key = _coords_identity(best_coords)
        best_query = next(
            query for query, coords in candidates if _coords_identity(coords) == best_key
        )
    else:
        best_query, best_coords = candidates[0]

    precision = "club" if source == "nominatim_club" else "name"
    return _apply_nominatim_result(
        team,
        best_query,
        best_coords,
        territory=territory,
        source=source,
        precision=precision,
    )


def refine_league_nominatim_teams(league: dict[str, Any]) -> int:
    """Re-pick Nominatim hits using full-league context for name/club geocodes."""
    league_name = league.get("league_name", "")
    refined = 0

    for index, team in enumerate(league.get("teams", [])):
        if team_geocode_source(team) not in ("nominatim_club", "nominatim_name"):
            continue
        before = (
            team.get("latitude"),
            team.get("longitude"),
            team.get("place_id"),
        )
        alt = geocode_with_club_queries(
            team,
            league_name,
            league=league,
            team_index=index,
        )
        if alt is None:
            continue
        after = (alt.get("latitude"), alt.get("longitude"), alt.get("place_id"))
        if after == before:
            continue
        league["teams"][index] = alt
        refined += 1

    return refined


def _apply_wikidata_coords(team: dict, wikidata: dict) -> dict:
    result = dict(team)
    ground = wikidata.get("ground") or team.get("address")
    result["address"] = ground
    result["latitude"] = wikidata["latitude"]
    result["longitude"] = wikidata["longitude"]
    result["formatted_address"] = ground
    result["geocode_precision"] = "wikidata"
    result["geocode_source"] = "wikidata"
    return _finalize_geocoded_team(result)


def _apply_nominatim_result(
    team: dict,
    query: str,
    coords: dict,
    *,
    territory: str | None = None,
    source: str = "nominatim_name",
    precision: str | None = None,
) -> dict:
    result = dict(team)
    result["address"] = query
    if territory:
        result["territory"] = territory
    result.update(coords)
    if precision:
        result["geocode_precision"] = precision
    elif source == "nominatim_name":
        result["geocode_precision"] = "name"
    elif source == "nominatim_club":
        result["geocode_precision"] = "club"
    else:
        result["geocode_precision"] = "region"
    result["geocode_source"] = source
    return _finalize_geocoded_team(result)


def recalculate_pyramid_team(
    team: dict,
    wikidata_coords: dict[str, dict],
    *,
    skip_source: str,
    league: dict[str, Any],
    team_index: int,
) -> dict | None:
    """Try geocoding methods other than the one that produced a bad location."""
    wiki_title = team.get("wiki_title", "")
    wikidata = wikidata_coords.get(wiki_title)
    name = team.get("name", "")
    league_name = league.get("league_name", "")
    candidates: list[dict] = []

    def add(candidate: dict | None) -> None:
        if candidate and "error" not in candidate:
            candidates.append(candidate)

    add(
        geocode_with_club_queries(
            team,
            league_name,
            league=league,
            team_index=team_index,
        )
    )

    if (
        skip_source != "wikipedia"
        and team.get("address")
        and team.get("address_source") == "wikipedia"
    ):
        result = geocode_team(team)
        if "error" not in result:
            result["geocode_source"] = "wikipedia"
            add(result)

    if (
        skip_source != "wikidata"
        and wikidata
        and _wikidata_ground_is_venue(wikidata.get("ground") or "", team)
    ):
        add(_apply_wikidata_coords(team, wikidata))

    if skip_source != "ground_override":
        override = ground_override_address(name)
        if override:
            coords, _ = geocode_with_nominatim(override)
            if coords:
                add(
                    _apply_nominatim_result(
                        team,
                        override,
                        coords,
                        source="ground_override",
                    )
                )

    if name and skip_source == "nominatim_club":
        territory = team.get("territory") or infer_territory_from_team(name, wiki_title)
        fallback_addr = f"{name}, {territory}"
        coords, _ = geocode_with_nominatim(fallback_addr)
        if coords:
            add(
                _apply_nominatim_result(
                    team,
                    fallback_addr,
                    coords,
                    territory=territory,
                    source="nominatim_name",
                )
            )

    for candidate in candidates:
        trial = {**league, "teams": list(league["teams"])}
        trial["teams"][team_index] = candidate
        flag_league_location_outliers(trial)
        if not is_definitely_wrong_location(candidate):
            return candidate

    return None


def recalculate_league_team(
    team: dict,
    *,
    skip_source: str,
    league: dict[str, Any],
    team_index: int,
) -> dict | None:
    """Retry geocoding for non-pyramid leagues using address and regional hints."""
    league_name = league.get("league_name", "")
    candidates: list[dict] = []

    def add(candidate: dict | None) -> None:
        if candidate and "error" not in candidate:
            candidates.append(candidate)

    add(
        geocode_with_club_queries(
            team,
            league_name,
            league=league,
            team_index=team_index,
        )
    )

    if skip_source != "wikipedia" and team.get("address"):
        result = geocode_team(team)
        if "error" not in result:
            if team.get("address_source"):
                result["geocode_source"] = team["address_source"]
            add(result)

    for candidate in candidates:
        trial = {**league, "teams": list(league["teams"])}
        trial["teams"][team_index] = candidate
        flag_league_location_outliers(trial)
        if not is_definitely_wrong_location(candidate):
            return candidate

    return None


def recalculate_wrong_locations(
    league: dict[str, Any],
    wikidata_coords: dict[str, dict] | None = None,
    *,
    use_pyramid: bool = True,
) -> int:
    """Re-geocode teams flagged as definitely wrong. Returns count replaced."""
    flag_league_location_outliers(league)
    wikidata_coords = wikidata_coords or {}
    replaced = 0

    for index, team in enumerate(league.get("teams", [])):
        if not is_definitely_wrong_location(team):
            continue
        skip_source = team_geocode_source(team)
        if not skip_source:
            continue
        if use_pyramid:
            alt = recalculate_pyramid_team(
                team,
                wikidata_coords,
                skip_source=skip_source,
                league=league,
                team_index=index,
            )
        else:
            alt = recalculate_league_team(
                team,
                skip_source=skip_source,
                league=league,
                team_index=index,
            )
        if alt is None or alt.get("error"):
            continue
        alt["geocode_recalculated"] = True
        alt["geocode_replaced_source"] = skip_source
        league["teams"][index] = alt
        replaced += 1
        logger.warning(
            "  Recalculated %s (%s -> %s)",
            team.get("name"),
            skip_source,
            team_geocode_source(alt),
        )

    flag_league_location_outliers(league)
    return replaced


def _log_location_outliers(league: dict) -> None:
    outliers = [team for team in league.get("teams", []) if team.get("location_outlier")]
    if not outliers:
        return
    sanity = league.get("location_sanity", {})
    definite = sum(1 for team in outliers if is_definitely_wrong_location(team))
    logger.warning(
        "  %d location outlier(s) in %s (%d definite, threshold %.1fkm)",
        len(outliers),
        league["league_name"],
        definite,
        sanity.get("threshold_km", 0),
    )
    for team in outliers:
        marker = " [RECALC]" if is_definitely_wrong_location(team) else ""
        logger.warning(
            "    %s: %.1fkm from centroid (z=%.1f, source=%s)%s",
            team.get("name"),
            team.get("centroid_distance_km", 0),
            team.get("centroid_distance_z", 0),
            team_geocode_source(team) or "?",
            marker,
        )


def geocode_team(team: dict) -> dict:
    """Geocode a single team dict that has an ``address`` field."""
    result = dict(team)
    address = team.get("address")
    coords = None
    if address:
        for query in _nominatim_candidates(address, team):
            coords, _ = geocode_with_nominatim(query)
            if coords:
                if query != address:
                    result["address"] = query
                    result["geocode_precision"] = (
                        "town" if query == _town_fallback(address) else "region"
                    )
                break

    if coords:
        result.update(coords)
        if team.get("address_source"):
            result["geocode_source"] = team["address_source"]
        return _finalize_geocoded_team(result)
    if address:
        result["error"] = "geocoding_failed"
    else:
        result["error"] = "no_address"
    return result


def geocode_pyramid_team(
    team: dict,
    wikidata_coords: dict[str, dict],
    *,
    league_name: str = "",
) -> dict:
    """Geocode a pyramid team using Wikipedia ground, then Wikidata, then name fallback."""
    wiki_title = team.get("wiki_title", "")
    wikidata = wikidata_coords.get(wiki_title)

    if team.get("address"):
        result = geocode_team(team)
        if "error" not in result:
            if team.get("address_source"):
                result["geocode_source"] = team["address_source"]
            return result

    if wikidata and _wikidata_ground_is_venue(wikidata.get("ground") or "", team):
        return _apply_wikidata_coords(team, wikidata)

    name = team.get("name", "")
    override = ground_override_address(name)
    if override:
        coords, _ = geocode_with_nominatim(override)
        if coords:
            return _apply_nominatim_result(
                team,
                override,
                coords,
                source="ground_override",
            )

    if name:
        if league_name:
            enriched = geocode_with_club_queries(team, league_name)
            if enriched:
                return enriched

        territory = team.get("territory") or infer_territory_from_team(name, wiki_title)
        fallback_addr = f"{name}, {territory}"
        coords, _ = geocode_with_nominatim(fallback_addr)
        if coords:
            return _apply_nominatim_result(
                team,
                fallback_addr,
                coords,
                territory=territory,
            )

    result = dict(team)
    result["error"] = "geocoding_failed"
    return result


def geocode_pyramid_league(address_league: dict, wikidata_coords: dict[str, dict]) -> dict:
    """Geocode every team in a pyramid AddressLeague."""
    geocoded_teams: list[dict] = []
    success = 0

    for team in address_league["teams"]:
        result = geocode_pyramid_team(
            team,
            wikidata_coords,
            league_name=address_league["league_name"],
        )
        if "error" not in result:
            success += 1
        geocoded_teams.append(result)

    logger.info(
        "  Geocoded %d/%d teams in %s",
        success,
        len(geocoded_teams),
        address_league["league_name"],
    )
    result = {
        "league_name": address_league["league_name"],
        "league_url": address_league["league_url"],
        "teams": geocoded_teams,
        "team_count": len(geocoded_teams),
    }
    refined = refine_league_nominatim_teams(result)
    if refined:
        logger.info("  Refined %d Nominatim pick(s) in %s", refined, address_league["league_name"])
    recalculate_wrong_locations(result, wikidata_coords)
    _log_location_outliers(result)
    return result


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
    result = {
        "league_name": address_league["league_name"],
        "league_url": address_league["league_url"],
        "teams": geocoded_teams,
        "team_count": len(geocoded_teams),
    }
    recalculate_wrong_locations(result, use_pyramid=False)
    _log_location_outliers(result)
    return result


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


__all__ = [
    "enriched_club_nominatim_queries",
    "flush_cache",
    "geocode_league",
    "geocode_pyramid_league",
    "geocode_pyramid_team",
    "geocode_team",
    "geocode_with_club_queries",
    "ground_override_address",
    "infer_territory_from_location",
    "infer_territory_from_team",
    "load_cache",
    "normalize_club_name",
    "recalculate_pyramid_team",
    "recalculate_wrong_locations",
    "refine_league_nominatim_teams",
    "write_json",
]
