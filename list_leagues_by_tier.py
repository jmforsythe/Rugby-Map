"""List all leagues at each absolute tier level per season, grouped by competition.

Scans geocoded team data and prints a structured view of which leagues exist
at each pyramid level for every season, separated into pyramid and merit
competitions.  Useful for verifying tier extraction assignments.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from tier_extraction import extract_tier, get_competition_offset, mens_current_tier_name
from utils import setup_logging

logger = logging.getLogger(__name__)

GEOCODED_DIR = Path("geocoded_teams")
SEASONS = sorted(d.name for d in GEOCODED_DIR.iterdir() if d.is_dir() and "-" in d.name)

PYRAMID_KEY = "(pyramid)"


def load_league_tiers() -> dict[str, dict[int, dict[str, list[str]]]]:
    """Build {season: {abs_tier: {competition: [league_names]}}}."""
    result: dict[str, dict[int, dict[str, list[str]]]] = {}

    for season in SEASONS:
        season_dir = GEOCODED_DIR / season
        tier_map: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

        for filepath in sorted(season_dir.rglob("*.json")):
            rel = filepath.relative_to(season_dir).as_posix()
            local_tier, tier_name = extract_tier(rel, season)

            parts = rel.split("/")
            is_merit = len(parts) >= 3 and parts[0] == "merit"

            if is_merit:
                comp_key = parts[1]
                abs_tier = local_tier + get_competition_offset(comp_key, season)
                comp_display = comp_key.replace("_", " ")
            else:
                abs_tier = local_tier
                comp_display = PYRAMID_KEY

            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            league_name = data.get("league_name", filepath.stem)
            team_count = len(data.get("teams", []))

            label = f"{league_name} ({team_count} teams)"
            tier_map[abs_tier][comp_display].append(label)

        result[season] = dict(tier_map)

    return result


def print_season(
    season: str,
    tier_map: dict[int, dict[str, list[str]]],
    tier_filter: int | None = None,
    comp_filter: str | None = None,
) -> None:
    print(f"\n{'=' * 80}")
    print(f"  {season}")
    print(f"{'=' * 80}")

    for abs_tier in sorted(tier_map):
        if tier_filter is not None and abs_tier != tier_filter:
            continue
        comps = tier_map[abs_tier]
        tier_display = (
            mens_current_tier_name(abs_tier, season) if abs_tier < 100 else f"W{abs_tier}"
        )

        pyramid_leagues = comps.get(PYRAMID_KEY, [])
        merit_comps = {k: v for k, v in comps.items() if k != PYRAMID_KEY}

        if comp_filter:
            cf = comp_filter.lower()
            if cf != "pyramid":
                merit_comps = {k: v for k, v in merit_comps.items() if cf in k.lower()}
                pyramid_leagues = []
            else:
                merit_comps = {}

        if not pyramid_leagues and not merit_comps:
            continue

        total = sum(len(v) for v in comps.values())
        print(f"\n  Tier {abs_tier:>3} ({tier_display}) - {total} league(s)")
        print(f"  {'-' * 60}")

        if pyramid_leagues:
            for league in sorted(pyramid_leagues):
                print(f"    {league}")

        for comp_name in sorted(merit_comps):
            leagues = merit_comps[comp_name]
            print(f"    [{comp_name}]")
            for league in sorted(leagues):
                print(f"      {league}")


def main() -> None:
    setup_logging()

    import argparse

    parser = argparse.ArgumentParser(description="List leagues at each tier level per season")
    parser.add_argument(
        "--season",
        type=str,
        help="Show only this season (e.g. 2025-2026). Omit for all.",
    )
    parser.add_argument(
        "--tier",
        type=int,
        help="Show only this absolute tier number.",
    )
    parser.add_argument(
        "--competition",
        type=str,
        help='Filter by competition name (e.g. "Hampshire", "NOWIRUL", "pyramid").',
    )
    args = parser.parse_args()

    seasons_to_show = [args.season] if args.season else SEASONS

    logger.info("Loading league tier data for %d season(s)...", len(seasons_to_show))
    all_data = load_league_tiers()

    for season in seasons_to_show:
        if season not in all_data:
            print(f"\nNo data for season {season}")
            continue
        print_season(season, all_data[season], args.tier, args.competition)


if __name__ == "__main__":
    main()
