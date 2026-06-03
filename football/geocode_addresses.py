"""
Geocode football team_addresses JSON files to geocoded_teams.

Reads existing address JSON and writes geocoded output.  For the pyramid,
uses Wikidata ground coordinates first (instant, no Nominatim) and only
calls Nominatim for the long tail without Wikidata coverage.

Run:
  python -m football.geocode_addresses --season 2025-2026 --subdir pyramid
  python -m football.geocode_addresses --season 2025-2026 --subdir BSLFL
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
    write_json,
)
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
    use_pyramid = subdir == "pyramid"
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
    args = parser.parse_args()

    setup_logging()
    load_cache()
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
