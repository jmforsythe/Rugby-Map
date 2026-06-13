"""
Fetch English football Regional Feeder leagues (level 11 / Step 7).

Uses ``data/football/feeder_leagues/<season>.json`` for division metadata and
FA Full-Time league IDs (discovered via :mod:`football.feeder_catalog`).  Falls
back to Wikipedia league-article member lists when Full-Time is unavailable.

Output:
  data/football/team_addresses/<season>/feeder/<Division>.json
  data/football/geocoded_teams/<season>/feeder/<Division>.json

Run:
  python -m football.feeder_catalog --season 2025-2026
  python -m football.fetch_feeder --season 2025-2026
  python -m football.fetch_feeder --season 2025-2026 --discover-catalog
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import urllib.parse

import requests

from core import setup_logging
from core.config import REPO_ROOT
from football import DATA_DIR
from football.clubs_data import (
    flush_cache,
    geocode_pyramid_league,
    infer_territory_from_location,
    infer_territory_from_team,
    load_cache,
    write_json,
)
from football.feeder_catalog import catalog_path, discover_catalog, load_catalog
from football.fetch_pyramid import _sanitize_filename
from football.fulltime import resolve_table_url, scrape_teams_from_table
from football.fulltime_logos import (
    apply_fulltime_logos_to_league,
    is_fulltime_team_url,
    resolve_fulltime_logos,
)
from football.league_names import canonical_league_name
from football.wikidata_coords import load_wikidata_coords
from football.wikipedia_grounds import resolve_grounds
from football.wikipedia_logos import apply_logos_to_league, resolve_logos
from football.wikipedia_members import parse_wikipedia_member_clubs

logger = logging.getLogger(__name__)

_USER_AGENT = "RugbyMappingProject/1.0 (https://github.com/jmforsythe/Rugby-Map)"
_WIKI_API = "https://en.wikipedia.org/w/api.php"
_SEASON_RE = re.compile(r"^\d{4}-\d{4}$")
_LEVEL = 11


def _wikipedia_division_table_html(wiki_title: str) -> str | None:
    """Return HTML for the current-season member-club table on a league article."""
    data = requests.get(
        _WIKI_API,
        params={
            "action": "parse",
            "page": wiki_title.replace("_", " "),
            "prop": "text",
            "format": "json",
            "formatversion": 2,
        },
        headers={"User-Agent": _USER_AGENT},
        timeout=60,
    ).json()
    if "error" in data:
        return None
    text = data.get("parse", {}).get("text", "")
    if isinstance(text, dict):
        text = text.get("*", "")
    return text if isinstance(text, str) else None


def roster_from_fulltime(entry: dict) -> list[dict]:
    """Scrape teams from FA Full-Time for a catalog entry."""
    fa_league_id = entry.get("fa_league_id")
    if not fa_league_id:
        return []

    table_url = resolve_table_url(
        fa_league_id,
        division_hint=entry.get("division_hint") or entry["division_name"],
        fa_division_id=entry.get("fa_division_id"),
    )
    if not table_url:
        return []

    entry["fulltime_table_url"] = table_url
    return scrape_teams_from_table(table_url)


def roster_from_wikipedia(entry: dict) -> list[dict]:
    """Parse teams from the league's Wikipedia member-club section."""
    wiki_title = entry.get("wiki_title")
    if not wiki_title:
        return []

    html = _wikipedia_division_table_html(wiki_title)
    if not html:
        return []
    hint = entry.get("division_hint") or entry["division_name"]
    return parse_wikipedia_member_clubs(html, division_hint=hint)


def build_address_league(entry: dict, teams_raw: list[dict], wiki_grounds: dict) -> dict:
    """Build an AddressLeague dict for one feeder division."""
    division_name = canonical_league_name(entry["division_name"])
    league_url = entry.get("fulltime_table_url") or (
        f"https://en.wikipedia.org/wiki/{entry['wiki_title']}" if entry.get("wiki_title") else ""
    )

    teams: list[dict] = []
    for raw in teams_raw:
        name = raw["name"]
        wiki_title = raw.get("wiki_title")
        wiki_url = (
            f"https://en.wikipedia.org/wiki/{urllib.parse.quote(wiki_title)}"
            if wiki_title
            else raw.get("url") or ""
        )
        ground = wiki_grounds.get(wiki_title) if wiki_title else None
        default_territory = infer_territory_from_team(name, wiki_title or "")
        territory = (
            infer_territory_from_location(ground, default_territory)
            if ground
            else default_territory
        )
        page_url = raw.get("url") if is_fulltime_team_url(raw.get("url")) else wiki_url
        team: dict = {
            "name": name,
            "url": page_url,
            "image_url": raw.get("image_url"),
            "address": ground,
            "territory": territory,
            "level": _LEVEL,
        }
        if wiki_title:
            team["wiki_title"] = wiki_title
        if ground:
            team["address_source"] = "wikipedia"
        teams.append(team)

    return {
        "league_name": division_name,
        "league_url": league_url,
        "teams": teams,
        "team_count": len(teams),
    }


def fetch_division(
    entry: dict,
    wiki_grounds: dict[str, str | None],
) -> tuple[dict | None, str]:
    """Return ``(address_league, source_label)`` or ``(None, reason)``."""
    teams = roster_from_fulltime(entry)
    source = "fulltime"
    expected = entry.get("expected_clubs") or 0
    if expected and teams and len(teams) < expected * 0.75:
        wiki_teams = roster_from_wikipedia(entry)
        if len(wiki_teams) > len(teams):
            teams = wiki_teams
            source = "wikipedia"
    if not teams:
        teams = roster_from_wikipedia(entry)
        source = "wikipedia"
    if not teams:
        return None, "no_roster"

    league = build_address_league(entry, teams, wiki_grounds)
    if not league["teams"]:
        return None, "empty"
    return league, source


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch English football Regional Feeder leagues (level 11)"
    )
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument(
        "--discover-catalog",
        action="store_true",
        help="Refresh feeder_leagues/<season>.json before fetching",
    )
    parser.add_argument(
        "--refresh-catalog",
        action="store_true",
        help="Force re-discovery of all FA league ids",
    )
    parser.add_argument(
        "--division",
        action="append",
        help="Only fetch divisions whose name contains this substring (repeatable)",
    )
    parser.add_argument("--skip-geocode", action="store_true")
    parser.add_argument("--refresh-wikidata", action="store_true")
    parser.add_argument("--refresh-grounds", action="store_true")
    parser.add_argument("--skip-logos", action="store_true")
    parser.add_argument("--refresh-logos", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch and overwrite existing division files",
    )
    args = parser.parse_args()

    setup_logging()

    if not _SEASON_RE.match(args.season):
        parser.error("--season must look like 2025-2026")

    season = args.season
    if args.discover_catalog or not catalog_path(season).is_file():
        discover_catalog(season, refresh=args.refresh_catalog)

    catalog = load_catalog(season)
    if not catalog:
        logger.error("Empty feeder catalog at %s", catalog_path(season))
        sys.exit(1)

    filters = [f.casefold() for f in args.division or []]
    if filters:
        catalog = [e for e in catalog if any(f in e["division_name"].casefold() for f in filters)]

    if not catalog:
        logger.error("No catalog entries match filters")
        sys.exit(1)

    wikidata_coords: dict[str, dict] = {}
    if not args.skip_geocode:
        wikidata_coords = load_wikidata_coords(refresh=args.refresh_wikidata)
        load_cache()

    addr_dir = DATA_DIR / "team_addresses" / season / "feeder"
    geo_dir = DATA_DIR / "geocoded_teams" / season / "feeder"
    addr_dir.mkdir(parents=True, exist_ok=True)
    if not args.skip_geocode:
        geo_dir.mkdir(parents=True, exist_ok=True)

    total_teams = 0
    total_geocoded = 0
    skipped = 0
    sources: dict[str, int] = {}

    for idx, entry in enumerate(catalog, start=1):
        name = entry["division_name"]
        logger.info("[%d/%d] %s", idx, len(catalog), name)

        filename = entry.get("filename") or _sanitize_filename(name)
        out_addr = addr_dir / f"{filename}.json"
        if not args.force and out_addr.is_file():
            logger.info("  Skipping (already exists; use --force to refresh)")
            continue

        league, source = fetch_division(entry, {})
        if league is None:
            logger.warning("  Skipped (%s)", source)
            skipped += 1
            continue

        club_titles = sorted({t["wiki_title"] for t in league["teams"] if t.get("wiki_title")})
        if club_titles:
            wiki_grounds = resolve_grounds(club_titles, refresh=args.refresh_grounds)
            for team in league["teams"]:
                title = team.get("wiki_title")
                ground = wiki_grounds.get(title) if title else None
                if ground:
                    team["address"] = ground
                    team["address_source"] = "wikipedia"
                    team["territory"] = infer_territory_from_location(
                        ground,
                        team.get("territory") or infer_territory_from_team(team["name"], title),
                    )

        sources[source] = sources.get(source, 0) + 1
        filename = entry.get("filename") or _sanitize_filename(league["league_name"])

        if not args.skip_logos:
            titles = sorted({t["wiki_title"] for t in league["teams"] if t.get("wiki_title")})
            if titles:
                wiki_logos = resolve_logos(titles, refresh=args.refresh_logos)
                apply_logos_to_league(league, wiki_logos)
            ft_urls = sorted(
                {
                    t["url"]
                    for t in league["teams"]
                    if not t.get("image_url") and is_fulltime_team_url(t.get("url"))
                }
            )
            if ft_urls:
                ft_logos = resolve_fulltime_logos(ft_urls, refresh=args.refresh_logos)
                apply_fulltime_logos_to_league(league, ft_logos)

        write_json(addr_dir / f"{filename}.json", league)
        total_teams += league["team_count"]
        logger.info("  Wrote %d teams (%s)", league["team_count"], source)

        if args.skip_geocode:
            continue

        geocoded = geocode_pyramid_league(league, wikidata_coords)
        total_geocoded += sum(1 for t in geocoded["teams"] if "error" not in t)
        write_json(geo_dir / f"{filename}.json", geocoded)
        flush_cache()

    if not args.skip_geocode:
        flush_cache(force=True)

    logger.info("=" * 70)
    out = geo_dir if not args.skip_geocode else addr_dir
    logger.info(
        "Wrote %d/%d divisions to %s",
        len(catalog) - skipped,
        len(catalog),
        out.relative_to(REPO_ROOT),
    )
    logger.info("Total teams: %d", total_teams)
    logger.info("Roster sources: %s", sources)
    if not args.skip_geocode:
        logger.info(
            "Geocoded: %d/%d (%.0f%%)",
            total_geocoded,
            total_teams,
            100 * total_geocoded / max(total_teams, 1),
        )


if __name__ == "__main__":
    main()
