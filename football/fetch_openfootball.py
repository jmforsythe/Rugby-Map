"""
Build English football pyramid data (levels 1-5) from OpenFootball fixtures.

For levels 6-10 (National League North/South, Isthmian, Southern, NPL, county
leagues), use ``football.fetch_pyramid`` instead -- OpenFootball only publishes
the top five tiers.

Run:
  python -m football.fetch_openfootball --season 2025-2026
  python -m football.fetch_openfootball --season 2025-2026 --skip-geocode
"""

from __future__ import annotations

import argparse
import logging
import re

from core import setup_logging
from football import DATA_DIR
from football.clubs_data import (
    build_address_league,
    download_text,
    flush_cache,
    geocode_league,
    load_cache,
    load_clubs_lookup,
    parse_division_teams,
    write_json,
)

logger = logging.getLogger(__name__)

_ENGLAND_RAW = "https://raw.githubusercontent.com/openfootball/england/master"

_DIVISIONS: dict[int, tuple[str, str]] = {
    1: ("Premier League", "1-premierleague"),
    2: ("Championship", "2-championship"),
    3: ("League One", "3-league1"),
    4: ("League Two", "4-league2"),
    5: ("National League", "5-nationalleague"),
}

_SEASON_RE = re.compile(r"^\d{4}-\d{4}$")


def _season_to_openfootball(season: str) -> str:
    start, end = season.split("-")
    return f"{start}-{end[2:]}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build English football levels 1-5 from OpenFootball + Nominatim"
    )
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument(
        "--divisions",
        type=int,
        nargs="+",
        choices=sorted(_DIVISIONS),
        default=sorted(_DIVISIONS),
    )
    parser.add_argument("--skip-geocode", action="store_true")
    args = parser.parse_args()

    setup_logging()

    if not _SEASON_RE.match(args.season):
        parser.error("--season must look like 2025-2026")

    of_season = _season_to_openfootball(args.season)
    lookup = load_clubs_lookup()

    if not args.skip_geocode:
        load_cache()

    addr_base = DATA_DIR / "team_addresses" / args.season / "openfootball"
    geo_base = DATA_DIR / "geocoded_teams" / args.season / "openfootball"

    for level in args.divisions:
        league_name, slug = _DIVISIONS[level]
        url = f"{_ENGLAND_RAW}/{of_season}/{slug}.txt"
        logger.info("Level %d — %s", level, league_name)

        try:
            div_text = download_text(url)
        except Exception as exc:
            logger.warning("Could not fetch %s (%s)", url, exc)
            continue

        team_names = parse_division_teams(div_text)
        league_url = f"https://github.com/openfootball/england/blob/master/{of_season}/{slug}.txt"
        address_league, _ = build_address_league(league_name, league_url, team_names, lookup)
        write_json(addr_base / f"{slug}.json", address_league)

        if not args.skip_geocode:
            geocoded = geocode_league(address_league)
            write_json(geo_base / f"{slug}.json", geocoded)
            flush_cache()

    if not args.skip_geocode:
        flush_cache(force=True)


if __name__ == "__main__":
    main()
