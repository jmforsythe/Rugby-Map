"""Build pyramid / all-leagues / merit import trees from the latest geocoded season."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from core.types import GeocodedLeague
from rugby import DATA_DIR
from rugby.custom_map_imports import assign_all_leagues_colors, assign_league_colors
from rugby.tiers import extract_tier, get_competition_offset, mens_current_tier_name

logger = logging.getLogger(__name__)

GEOCODED_DIR = DATA_DIR / "geocoded_teams"


def latest_geocoded_season() -> str | None:
    """Return the lexicographically last season directory name, or *None*."""
    if not GEOCODED_DIR.is_dir():
        return None
    seasons = sorted(d.name for d in GEOCODED_DIR.iterdir() if d.is_dir())
    return seasons[-1] if seasons else None


def _tier_bucket() -> dict[str, Any]:
    return {"league_map": defaultdict(list)}


def build_season_imports(season: str | None = None) -> dict[str, Any]:
    """Scan one geocoded season and build import structures for the custom map."""
    resolved_season = season or latest_geocoded_season()
    if not resolved_season:
        return {"season": "", "pyramid": [], "allLeagues": [], "merit": []}

    season_dir = GEOCODED_DIR / resolved_season
    if not season_dir.is_dir():
        logger.warning("Geocoded season directory not found: %s", season_dir)
        return {"season": resolved_season, "pyramid": [], "allLeagues": [], "merit": []}

    pyramid: dict[int, dict[str, Any]] = {}
    all_leagues: dict[int, dict[str, Any]] = {}
    merit: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    merit_abs_tiers: set[int] = set()

    for json_path in sorted(season_dir.rglob("*.json")):
        rel_path = json_path.relative_to(season_dir).as_posix()
        rel_parts = list(json_path.relative_to(season_dir).parts)
        is_merit = len(rel_parts) >= 3 and rel_parts[0] == "merit"

        local_tier_num, _local_tier_name = extract_tier(rel_path, resolved_season)
        if is_merit:
            comp_key = rel_parts[1]
            comp_display = comp_key.replace("_", " ")
            offset = get_competition_offset(comp_key, resolved_season)
            abs_tier = local_tier_num + offset
            abs_tier_name = mens_current_tier_name(abs_tier, resolved_season)
            local_tier_label = _local_tier_name or comp_display
        else:
            comp_display = ""
            local_tier_label = ""
            abs_tier = local_tier_num
            abs_tier_name = extract_tier(rel_path, resolved_season)[1]

        try:
            with open(json_path, encoding="utf-8") as f:
                league: GeocodedLeague = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping %s: %s", json_path, exc)
            continue

        league_name = league.get("league_name")
        if not league_name:
            continue

        team_names = sorted(
            (team["name"] for team in league.get("teams", []) if team.get("name")),
            key=str.casefold,
        )
        if not team_names:
            continue

        if abs_tier not in all_leagues:
            all_leagues[abs_tier] = {"num": abs_tier, "name": abs_tier_name, **_tier_bucket()}
        all_leagues[abs_tier]["league_map"][league_name].extend(team_names)

        if is_merit:
            merit_abs_tiers.add(abs_tier)
            if comp_display not in merit:
                merit[comp_display] = {}
            if local_tier_label not in merit[comp_display]:
                merit[comp_display][local_tier_label] = {
                    "name": local_tier_label,
                    **_tier_bucket(),
                }
            merit[comp_display][local_tier_label]["league_map"][league_name].extend(team_names)
        else:
            if abs_tier not in pyramid:
                pyramid[abs_tier] = {"num": abs_tier, "name": abs_tier_name, **_tier_bucket()}
            pyramid[abs_tier]["league_map"][league_name].extend(team_names)

    def _finalize_tier(
        tier_num: int,
        tier_name: str,
        league_map: dict[str, list[str]],
        *,
        tier_id: str,
    ) -> dict[str, Any]:
        leagues: list[dict[str, Any]] = []
        for lg_name in sorted(league_map):
            teams = sorted(league_map[lg_name], key=str.casefold)
            if teams:
                leagues.append({"name": lg_name, "teams": teams})
        assign_league_colors(leagues, tier_num)
        return {
            "id": tier_id,
            "num": tier_num,
            "name": tier_name,
            "leagues": leagues,
        }

    pyramid_out = [
        _finalize_tier(t["num"], t["name"], t["league_map"], tier_id=f"t{t['num']}")
        for t in sorted(pyramid.values(), key=lambda x: x["num"])
        if t["league_map"]
    ]

    pyramid_by_num = {t["num"]: t for t in pyramid_out}

    all_leagues_out: list[dict[str, Any]] = []
    for t in sorted(all_leagues.values(), key=lambda x: x["num"]):
        if t["num"] not in merit_abs_tiers or not t["league_map"]:
            continue
        leagues: list[dict[str, Any]] = []
        for lg_name in sorted(t["league_map"]):
            teams = sorted(t["league_map"][lg_name], key=str.casefold)
            if teams:
                leagues.append({"name": lg_name, "teams": teams})
        pyramid_tier = pyramid_by_num.get(t["num"])
        if pyramid_tier:
            assign_all_leagues_colors(leagues, t["num"], pyramid_tier["leagues"])
        else:
            assign_league_colors(leagues, t["num"])
        all_leagues_out.append(
            {
                "id": f"al{t['num']}",
                "num": t["num"],
                "name": f"{t['name']} + Merit",
                "leagues": leagues,
            }
        )

    merit_out: list[dict[str, Any]] = []
    for comp_name in sorted(merit):
        tier_entries = merit[comp_name]
        comp_tiers: list[dict[str, Any]] = []
        for local_name in sorted(tier_entries, key=str.casefold):
            entry = tier_entries[local_name]
            leagues: list[dict[str, Any]] = []
            for lg_name in sorted(entry["league_map"]):
                teams = sorted(entry["league_map"][lg_name], key=str.casefold)
                if teams:
                    leagues.append({"name": lg_name, "teams": teams})
            if not leagues:
                continue
            assign_league_colors(leagues, 1)
            comp_tiers.append(
                {
                    "id": f"m_{comp_name}_{local_name}",
                    "num": 0,
                    "name": local_name,
                    "leagues": leagues,
                }
            )
        if comp_tiers:
            merit_out.append({"comp": comp_name, "tiers": comp_tiers})

    return {
        "season": resolved_season,
        "pyramid": pyramid_out,
        "allLeagues": all_leagues_out,
        "merit": merit_out,
    }


def write_season_imports_js(output_dir: Path, season: str | None = None) -> None:
    """Write ``season_imports.js`` to *output_dir*."""
    payload = build_season_imports(season)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "season_imports.js"
    compact = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by rugby.custom_map — do not edit\n")
        f.write("var SEASON_IMPORTS = ")
        f.write(compact)
        f.write(";\n")

    n_merit = sum(len(c["tiers"]) for c in payload["merit"])
    logger.info(
        "Wrote %s (season %s, %d pyramid tiers, %d all-league tiers, %d merit competitions)",
        output_path,
        payload["season"] or "none",
        len(payload["pyramid"]),
        len(payload["allLeagues"]),
        n_merit,
    )
