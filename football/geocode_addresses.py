"""
Geocode football team_addresses JSON files to geocoded_teams.

Reads existing address JSON and writes geocoded output.  For the pyramid,
uses Wikidata ground coordinates first (instant, no Nominatim) and only
calls Nominatim for the long tail without Wikidata coverage.

Run:
  python -m football.geocode_addresses --season 2025-2026 --subdir pyramid
  python -m football.geocode_addresses --season 2025-2026 --subdir feeder
  python -m football.geocode_addresses --season 2025-2026 --subdir BSLFL

Re-run outlier recalc only (fast; uses existing geocoded JSON, Nominatim for
definite bad geocodes only):
  python -m football.geocode_addresses --season 2025-2026 --subdir feeder --recalc-outliers-only
"""

from __future__ import annotations

import argparse
import json
import logging

from core import setup_logging
from core.config import REPO_ROOT
from football import DATA_DIR
from football.clubs_data import (
    flush_cache,
    geocode_league,
    geocode_pyramid_league,
    load_cache,
    recalculate_wrong_locations,
    write_json,
)
from football.location_sanity import flag_league_location_outliers, is_definitely_wrong_location
from football.wikidata_coords import load_wikidata_coords

logger = logging.getLogger(__name__)


def geocode_address_dir(
    season: str,
    subdir: str,
    *,
    refresh_wikidata: bool = False,
) -> tuple[int, int]:
    """Geocode all JSON files under ``team_addresses/<season>/<subdir>/``."""
    address_dir = DATA_DIR / "team_addresses" / season / subdir
    if not address_dir.is_dir():
        raise FileNotFoundError(f"Address directory not found: {address_dir}")

    geo_dir = DATA_DIR / "geocoded_teams" / season / subdir
    use_pyramid = subdir in ("pyramid", "feeder")
    wikidata_coords = load_wikidata_coords(refresh=refresh_wikidata) if use_pyramid else {}

    total = 0
    success = 0

    for address_file in sorted(address_dir.glob("*.json")):
        with open(address_file, encoding="utf-8") as f:
            address_league = json.load(f)
        logger.info(
            "Geocoding %s (%d teams)",
            address_league.get("league_name", address_file.stem),
            address_league.get("team_count", 0),
        )
        if use_pyramid:
            geocoded = geocode_pyramid_league(address_league, wikidata_coords)
        else:
            geocoded = geocode_league(address_league)
        write_json(geo_dir / address_file.name, geocoded)
        flush_cache()
        total += geocoded["team_count"]
        success += sum(1 for t in geocoded["teams"] if "error" not in t)

    flush_cache(force=True)
    return success, total


def recalc_outliers_in_geocoded_dir(
    season: str,
    subdir: str,
    *,
    refresh_wikidata: bool = False,
) -> tuple[int, int, int]:
    """Re-run outlier recalc on existing ``geocoded_teams`` JSON only.

    Does not read ``team_addresses`` or re-geocode teams that already have
    acceptable coordinates.  Nominatim is only called for teams that still
    match the "definitely wrong" thresholds during :func:`recalculate_wrong_locations`.

    Returns ``(teams_replaced, leagues_updated, definite_outliers_remaining)``.
    """
    geo_dir = DATA_DIR / "geocoded_teams" / season / subdir
    if not geo_dir.is_dir():
        raise FileNotFoundError(f"Geocoded directory not found: {geo_dir}")

    use_pyramid = subdir in ("pyramid", "feeder")
    wikidata_coords = load_wikidata_coords(refresh=refresh_wikidata) if use_pyramid else {}

    replaced_total = 0
    leagues_updated = 0
    remaining_total = 0

    for geo_file in sorted(geo_dir.glob("*.json")):
        if geo_file.name.startswith("_"):
            continue
        with open(geo_file, encoding="utf-8") as f:
            league = json.load(f)

        league_name = league.get("league_name", geo_file.stem)
        flag_league_location_outliers(league)
        wrong_before = sum(
            1 for team in league.get("teams", []) if is_definitely_wrong_location(team)
        )
        if wrong_before:
            logger.info(
                "Recalc %s (%d definite outlier(s))",
                league_name,
                wrong_before,
            )

        replaced = recalculate_wrong_locations(
            league,
            wikidata_coords,
            use_pyramid=use_pyramid,
        )
        remaining = sum(1 for team in league.get("teams", []) if is_definitely_wrong_location(team))
        remaining_total += remaining

        if replaced:
            write_json(geo_file, league)
            flush_cache()
            replaced_total += replaced
            leagues_updated += 1
            logger.info(
                "  Replaced %d team(s) in %s (%d definite outlier(s) remain)",
                replaced,
                league_name,
                remaining,
            )

    flush_cache(force=True)
    return replaced_total, leagues_updated, remaining_total


def main() -> None:
    parser = argparse.ArgumentParser(description="Geocode football team_addresses JSON files")
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument(
        "--subdir",
        default="pyramid",
        help="Subdirectory under team_addresses/<season>/ (default: pyramid)",
    )
    parser.add_argument(
        "--refresh-wikidata",
        action="store_true",
        help="Re-fetch Wikidata coordinates instead of using cache",
    )
    parser.add_argument(
        "--recalc-outliers-only",
        action="store_true",
        help=(
            "Skip full geocoding; load existing geocoded_teams JSON and only "
            "re-search definite outlier locations"
        ),
    )
    args = parser.parse_args()

    setup_logging()
    load_cache()
    if args.recalc_outliers_only:
        replaced, leagues, remaining = recalc_outliers_in_geocoded_dir(
            args.season,
            args.subdir,
            refresh_wikidata=args.refresh_wikidata,
        )
        logger.info(
            "Recalculated %d team(s) in %d league(s); %d definite outlier(s) remain under %s",
            replaced,
            leagues,
            remaining,
            (DATA_DIR / "geocoded_teams" / args.season / args.subdir).relative_to(REPO_ROOT),
        )
        return

    success, total = geocode_address_dir(
        args.season,
        args.subdir,
        refresh_wikidata=args.refresh_wikidata,
    )
    logger.info(
        "Geocoded %d/%d teams to %s",
        success,
        total,
        (DATA_DIR / "geocoded_teams" / args.season / args.subdir).relative_to(REPO_ROOT),
    )


if __name__ == "__main__":
    main()
