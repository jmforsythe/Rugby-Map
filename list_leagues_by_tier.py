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
from urllib.parse import parse_qs, urlparse

from tier_extraction import extract_tier, get_competition_offset, mens_current_tier_name
from utils import setup_logging

logger = logging.getLogger(__name__)

GEOCODED_DIR = Path("geocoded_teams")
SEASONS = sorted(d.name for d in GEOCODED_DIR.iterdir() if d.is_dir() and "-" in d.name)

PYRAMID_KEY = "(pyramid)"

COMPETITION_NAMES: dict[str, str] = {
    # Pyramid
    "1699": "South West",
    "1597": "Midlands",
    "261": "London and SE",
    "1623": "Northern",
    "1605": "National Leagues",
    # Top-level
    "5": "Premiership",
    "173": "Championship",
    # Women's
    "1782": "Women's",
    "1764": "Women's Premiership",
    # Merit
    "183": "IMPACT Rugby North West Leagues",
    "202": "Hampshire Merit Tables",
    "1600": "Midlands Reserve Team Leagues",
    "252": "Leicestershire Competitions",
    "1694": "Group 1 Automotive Essex Merit League",
    "180": "Yorkshire League & Merit Tables",
    "100": "East Midlands Leagues",
    "1729": "Surrey County Leagues",
    "1770": "GRFU District Leagues",
    "77": "Devon Merit Tables",
    "206": "Harvey's Brewery Sussex Leagues",
    "104": "Eastern Counties Greene King",
    "1596": "Middlesex Merit Tables",
    "1681": "Rural Kent Leagues",
    "1636": "Nottinghamshire RFU Security Plus Pennant",
    "49": "CANDY League",
    "209": "Hertfordshire & Middlesex Merit Tables",
}


_LEAGUE_NAME_COMP_IDS: dict[str, str] = {
    "Premiership": "5",
    "Championship": "173",
    "National League One": "173",
}


def _extract_competition_id(league_url: str, league_name: str = "") -> str | None:
    """Return the ``competition`` query-param value from a league URL, or *None*.

    Falls back to matching *league_name* for early seasons whose files store
    Wikipedia URLs instead of England Rugby URLs.
    """
    try:
        qs = parse_qs(urlparse(league_url).query)
        ids = qs.get("competition", [])
        if ids:
            return ids[0]
    except Exception:
        pass
    return _LEAGUE_NAME_COMP_IDS.get(league_name)


LeagueRecord = tuple[str, int, str]  # (label, abs_tier, comp_display)


def load_league_data() -> dict[str, dict[str, list[LeagueRecord]]]:
    """Build ``{season: {comp_id: [(label, abs_tier, comp_display), …]}}``."""
    result: dict[str, dict[str, list[LeagueRecord]]] = {}

    for season in SEASONS:
        season_dir = GEOCODED_DIR / season
        comp_map: dict[str, list[LeagueRecord]] = defaultdict(list)

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
            comp_id = _extract_competition_id(data.get("league_url", ""), league_name) or "?"

            label = f"{league_name} ({team_count} teams)"
            comp_map[comp_id].append((label, abs_tier, comp_display))

        result[season] = dict(comp_map)

    return result


def print_season(
    season: str,
    comp_map: dict[str, list[LeagueRecord]],
    tier_filter: int | None = None,
    comp_filter: str | None = None,
) -> None:
    print(f"\n{'=' * 80}")
    print(f"  {season}")
    print(f"{'=' * 80}")

    for comp_id in sorted(comp_map, key=lambda c: (c == "?", c)):
        records = comp_map[comp_id]

        if tier_filter is not None:
            records = [r for r in records if r[1] == tier_filter]
        if comp_filter:
            cf = comp_filter.lower()
            comp_name_lower = COMPETITION_NAMES.get(comp_id, "").lower()
            if cf == "pyramid":
                records = [r for r in records if r[2] == PYRAMID_KEY]
            elif cf == comp_id or cf in comp_name_lower:
                pass  # whole competition matches — keep all records
            else:
                records = [r for r in records if r[2] != PYRAMID_KEY and cf in r[2].lower()]

        if not records:
            continue

        comp_name = COMPETITION_NAMES.get(comp_id, comp_id)
        print(f"\n  competition={comp_id} - {comp_name} ({len(records)} league(s))")
        print(f"  {'-' * 60}")

        tiers: dict[int, list[tuple[str, str]]] = defaultdict(list)
        for label, abs_tier, comp_display in records:
            tiers[abs_tier].append((label, comp_display))

        for abs_tier in sorted(tiers):
            tier_display = (
                mens_current_tier_name(abs_tier, season) if abs_tier < 100 else f"W{abs_tier}"
            )
            entries = tiers[abs_tier]
            print(f"    Tier {abs_tier:>3} ({tier_display})")

            pyramid = sorted(name for name, comp in entries if comp == PYRAMID_KEY)
            merit = sorted(
                ((name, comp) for name, comp in entries if comp != PYRAMID_KEY),
                key=lambda x: (x[1], x[0]),
            )

            for league in pyramid:
                print(f"      {league}")
            for league, comp_name in merit:
                print(f"      [{comp_name}] {league}")


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
    all_data = load_league_data()

    for season in seasons_to_show:
        if season not in all_data:
            print(f"\nNo data for season {season}")
            continue
        print_season(season, all_data[season], args.tier, args.competition)


if __name__ == "__main__":
    main()
