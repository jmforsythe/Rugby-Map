"""
Fetch the full English football pyramid (levels 1-10) for map generation.

Hybrid pipeline:
  1. Wikipedia "List of football clubs in England" -- club-to-league-to-level
     membership for the *current* season, including National League North/South,
     Isthmian, Southern, NPL, and county/regional leagues down to step 6.
  2. OpenFootball ``eng.clubs.txt`` + ``wal.clubs.txt`` -- canonical club names,
     aliases, and home-ground/town strings for geocoding where available.
  3. Wikidata SPARQL -- ground coordinates for the long tail of lower-tier clubs
     not in the OpenFootball clubs file (~240 clubs vs ~1,000+ in the pyramid).
  4. OpenStreetMap Nominatim (via ``rugby.geocode``) -- geocoding OpenFootball
     addresses, and a last-resort ``"{club name}, England"`` name search.

OpenFootball's ``england`` repo only covers levels 1-5; Wikipedia is the only
practical open-data source for division membership at steps 6+.

Output:
  data/football/team_addresses/<season>/pyramid/<Division>.json
  data/football/geocoded_teams/<season>/pyramid/<Division>.json

Run:
  python -m football.fetch_pyramid --season 2025-2026
  python -m football.fetch_pyramid --season 2025-2026 --min-level 6
  python -m football.fetch_pyramid --season 2025-2026 --skip-geocode
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import urllib.parse

import requests
from bs4 import BeautifulSoup

from core import setup_logging
from core.config import REPO_ROOT
from football import DATA_DIR
from football.clubs_data import (
    flush_cache,
    geocode_pyramid_league,
    infer_territory_from_team,
    load_cache,
    load_clubs_lookup,
    match_club,
    write_json,
)
from football.league_names import canonical_league_name
from football.wikidata_coords import load_wikidata_coords
from football.wikipedia_logos import apply_logos_to_league, resolve_logos

logger = logging.getLogger(__name__)

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_football_clubs_in_England"
_USER_AGENT = "RugbyMappingProject/1.0 (https://github.com/jmforsythe/Rugby-Map)"

# Flag-icon links in the Wikipedia table point at country articles, not clubs.
_COUNTRY_TITLES = frozenset(
    {
        "Wales",
        "Jersey",
        "Guernsey",
        "Isle_of_Man",
        "England",
        "Scotland",
        "Northern_Ireland",
    }
)

_SEASON_RE = re.compile(r"^\d{4}-\d{4}$")


def _sanitize_filename(name: str) -> str:
    s = name.replace(" ", "_").replace("/", "_").replace("&", "and")
    s = re.sub(r"[^\w\-()]", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def _extract_club_from_cell(club_cell) -> tuple[str, str] | None:
    """Return (display_name, wiki_title), skipping flag/country icon links."""
    best: tuple[str, str] | None = None

    for link in club_cell.find_all("a", href=True):
        href = link["href"]
        if not href.startswith("/wiki/"):
            continue
        title = urllib.parse.unquote(href.split("/wiki/")[-1])
        if title in _COUNTRY_TITLES or title.startswith(("File:", "Category:")):
            continue
        name = link.get_text(strip=True)
        if not name:
            continue
        if best is None or len(name) > len(best[0]):
            best = (name, title)

    return best


def parse_wikipedia_clubs(html: str) -> list[dict]:
    """Parse club-to-league mappings from the Wikipedia list page."""
    soup = BeautifulSoup(html, "html.parser")
    clubs: list[dict] = []

    for table in soup.find_all("table", class_="wikitable"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if "club" not in headers or "lvl" not in headers:
            continue

        col_map = {h: i for i, h in enumerate(headers)}
        club_idx = col_map.get("club", -1)
        league_idx = col_map.get("league/division", col_map.get("league", -1))
        lvl_idx = col_map.get("lvl", -1)
        if club_idx < 0 or league_idx < 0 or lvl_idx < 0:
            continue

        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= max(club_idx, league_idx, lvl_idx):
                continue

            extracted = _extract_club_from_cell(cells[club_idx])
            if not extracted:
                continue
            club_name, wiki_title = extracted

            league_cell = cells[league_idx]
            league_link = league_cell.find("a", href=True)
            league_name = (
                league_link.get_text(strip=True)
                if league_link
                else league_cell.get_text(strip=True)
            )

            try:
                level = int(cells[lvl_idx].get_text(strip=True))
            except ValueError:
                continue

            clubs.append(
                {
                    "name": club_name,
                    "wiki_title": wiki_title,
                    "league": league_name,
                    "level": level,
                }
            )

    logger.info("Parsed %d clubs from Wikipedia", len(clubs))
    return clubs


def build_leagues(
    wiki_clubs: list[dict],
    lookup: dict[str, dict],
    *,
    min_level: int = 1,
    max_level: int = 10,
) -> tuple[dict[str, dict], list[str]]:
    """Group Wikipedia clubs into AddressLeague dicts keyed by sanitised filename."""
    leagues: dict[str, dict] = {}
    unmatched: list[str] = []

    for club in wiki_clubs:
        level = club["level"]
        if level < min_level or level > max_level:
            continue

        matched = match_club(club["name"], lookup)
        wiki_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(club['wiki_title'])}"
        territory = (
            matched["territory"]
            if matched
            else infer_territory_from_team(club["name"], club["wiki_title"])
        )

        team: dict = {
            "name": club["name"],
            "url": wiki_url,
            "image_url": None,
            "address": matched["address"] if matched else None,
            "territory": territory,
            "wiki_title": club["wiki_title"],
            "level": level,
        }

        if not matched:
            unmatched.append(f"{club['name']} (no OpenFootball club match)")
        elif not matched["address"]:
            unmatched.append(f"{club['name']} (no address in clubs file)")

        league_name = canonical_league_name(club["league"])
        filename = _sanitize_filename(league_name)
        if filename not in leagues:
            leagues[filename] = {
                "league_name": league_name,
                "league_url": _WIKI_URL,
                "teams": [],
            }
        leagues[filename]["teams"].append(team)

    for data in leagues.values():
        data["team_count"] = len(data["teams"])

    return leagues, unmatched


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch full English football pyramid (levels 1-10) for mapping"
    )
    parser.add_argument("--season", default="2025-2026", help="Season label, e.g. 2025-2026")
    parser.add_argument(
        "--min-level",
        type=int,
        default=1,
        help="Lowest pyramid level to include (default: 1)",
    )
    parser.add_argument(
        "--max-level",
        type=int,
        default=10,
        help="Highest pyramid level to include (default: 10)",
    )
    parser.add_argument(
        "--refresh-wikidata",
        action="store_true",
        help="Re-fetch Wikidata coordinates instead of using cache",
    )
    parser.add_argument(
        "--skip-geocode",
        action="store_true",
        help="Only write team_addresses; skip Nominatim geocoding",
    )
    parser.add_argument(
        "--refresh-logos",
        action="store_true",
        help="Re-fetch Wikipedia crest URLs instead of using cache",
    )
    parser.add_argument(
        "--skip-logos",
        action="store_true",
        help="Do not fetch Wikipedia crest URLs",
    )
    args = parser.parse_args()

    setup_logging()

    if not _SEASON_RE.match(args.season):
        parser.error("--season must look like 2025-2026")
    if args.min_level < 1 or args.max_level > 10 or args.min_level > args.max_level:
        parser.error("--min-level/--max-level must be between 1 and 10")

    season = args.season
    logger.info(
        "Building pyramid for %s (levels %d-%d)",
        season,
        args.min_level,
        args.max_level,
    )

    logger.info("Fetching Wikipedia club list …")
    resp = requests.get(_WIKI_URL, headers={"User-Agent": _USER_AGENT}, timeout=30)
    resp.raise_for_status()
    wiki_clubs = parse_wikipedia_clubs(resp.text)
    if not wiki_clubs:
        logger.error("No clubs parsed from Wikipedia — aborting")
        sys.exit(1)

    lookup = load_clubs_lookup()
    wikidata_coords: dict[str, dict] = {}
    if not args.skip_geocode:
        wikidata_coords = load_wikidata_coords(refresh=args.refresh_wikidata)

    leagues, unmatched = build_leagues(
        wiki_clubs,
        lookup,
        min_level=args.min_level,
        max_level=args.max_level,
    )

    if not leagues:
        logger.error("No leagues in requested level range")
        sys.exit(1)

    logos: dict[str, str | None] = {}
    if not args.skip_logos:
        wiki_titles = sorted(
            {
                team["wiki_title"]
                for data in leagues.values()
                for team in data["teams"]
                if team.get("wiki_title")
            }
        )
        logos = resolve_logos(wiki_titles, refresh=args.refresh_logos)
        logo_count = sum(1 for url in logos.values() if url)
        logger.info("Wikipedia logos: %d/%d clubs", logo_count, len(wiki_titles))
        for data in leagues.values():
            apply_logos_to_league(data, logos)

    if not args.skip_geocode:
        load_cache()

    addr_dir = DATA_DIR / "team_addresses" / season / "pyramid"
    geo_dir = DATA_DIR / "geocoded_teams" / season / "pyramid"

    total_teams = 0
    total_geocoded = 0

    for filename, address_league in sorted(leagues.items()):
        logger.info("=" * 70)
        logger.info("%s (%d teams)", address_league["league_name"], address_league["team_count"])
        total_teams += address_league["team_count"]

        write_json(addr_dir / f"{filename}.json", address_league)

        if args.skip_geocode:
            continue

        geocoded = geocode_pyramid_league(address_league, wikidata_coords)
        total_geocoded += sum(1 for t in geocoded["teams"] if "error" not in t)
        write_json(geo_dir / f"{filename}.json", geocoded)
        flush_cache()

    if not args.skip_geocode:
        flush_cache(force=True)

    logger.info("=" * 70)
    logger.info(
        "Wrote %d division files to %s",
        len(leagues),
        (geo_dir if not args.skip_geocode else addr_dir).relative_to(REPO_ROOT),
    )
    logger.info("Total teams: %d", total_teams)
    if not args.skip_geocode:
        logger.info(
            "Total geocoded: %d/%d (%.0f%%)",
            total_geocoded,
            total_teams,
            100 * total_geocoded / max(total_teams, 1),
        )

    if unmatched:
        logger.info("Unmatched / address-less (%d):", len(unmatched))
        for item in sorted(unmatched)[:40]:
            logger.info("  %s", item)
        if len(unmatched) > 40:
            logger.info("  … and %d more", len(unmatched) - 40)


if __name__ == "__main__":
    main()
