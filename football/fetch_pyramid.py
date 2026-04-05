"""
Fetch English football pyramid data for a given season.

Combines two data sources:
1. Wikipedia "List of football clubs in England" — club-to-league mappings
   (levels 1–10 for the current season).
2. Wikidata SPARQL — home-ground coordinates for each club.

Outputs geocoded_teams JSON files ready for map generation, one file per
division, stored under football/geocoded_teams/{season}/pyramid/.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import urllib.parse

import requests
from bs4 import BeautifulSoup

from core import setup_logging
from core.config import REPO_ROOT
from football import DATA_DIR

logger = logging.getLogger(__name__)

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_football_clubs_in_England"
_WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
_CACHE_FILE = DATA_DIR / "pyramid_cache.json"

_SPARQL_QUERY = """\
SELECT ?club ?clubLabel ?article ?ground ?groundLabel ?coord WHERE {
  VALUES ?type { wd:Q476028 wd:Q15944511 }
  ?club wdt:P31 ?type .
  ?club wdt:P17 wd:Q145 .
  ?club wdt:P115 ?ground .
  ?ground wdt:P625 ?coord .
  ?article schema:about ?club ;
           schema:isPartOf <https://en.wikipedia.org/> .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . }
}
"""

# Some clubs on the Wikipedia list don't have a Wikidata ground with coords,
# but DO have coords on the club entity itself.  This secondary query picks
# those up.
_SPARQL_CLUB_COORDS = """\
SELECT ?club ?clubLabel ?article ?coord WHERE {
  VALUES ?type { wd:Q476028 wd:Q15944511 }
  ?club wdt:P31 ?type .
  ?club wdt:P17 wd:Q145 .
  ?club wdt:P625 ?coord .
  ?article schema:about ?club ;
           schema:isPartOf <https://en.wikipedia.org/> .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . }
}
"""


# ---------------------------------------------------------------------------
# Wikipedia parsing
# ---------------------------------------------------------------------------


def _wiki_article_title(url: str) -> str:
    """Extract the article title from a Wikipedia URL, unquoted."""
    path = urllib.parse.urlparse(url).path
    return urllib.parse.unquote(path.split("/wiki/")[-1])


def _parse_wikipedia_clubs(html: str) -> list[dict]:
    """Parse the alphabetical club tables from the Wikipedia page.

    Returns a list of dicts with keys:
        name, wiki_title, league, league_wiki_title, level
    """
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

            club_cell = cells[club_idx]
            league_cell = cells[league_idx]
            lvl_cell = cells[lvl_idx]

            club_link = club_cell.find("a", href=True)
            if not club_link:
                continue

            club_name = club_link.get_text(strip=True)
            club_href = club_link["href"]
            if not club_href.startswith("/wiki/"):
                continue
            wiki_title = urllib.parse.unquote(club_href.split("/wiki/")[-1])

            league_link = league_cell.find("a", href=True)
            league_name = (
                league_link.get_text(strip=True)
                if league_link
                else league_cell.get_text(strip=True)
            )
            league_wiki = ""
            if league_link and league_link["href"].startswith("/wiki/"):
                league_wiki = urllib.parse.unquote(league_link["href"].split("/wiki/")[-1])

            try:
                level = int(lvl_cell.get_text(strip=True))
            except ValueError:
                continue

            clubs.append(
                {
                    "name": club_name,
                    "wiki_title": wiki_title,
                    "league": league_name,
                    "league_wiki": league_wiki,
                    "level": level,
                }
            )

    logger.info("Parsed %d clubs from Wikipedia", len(clubs))
    return clubs


# ---------------------------------------------------------------------------
# Wikidata SPARQL
# ---------------------------------------------------------------------------


def _parse_coord(wkt: str) -> tuple[float, float]:
    """Parse 'Point(lon lat)' WKT literal into (lat, lon)."""
    m = re.match(r"Point\(([^ ]+) ([^ ]+)\)", wkt)
    if not m:
        raise ValueError(f"Cannot parse WKT coordinate: {wkt}")
    lon, lat = float(m.group(1)), float(m.group(2))
    return lat, lon


def _run_sparql(query: str) -> list[dict]:
    """Execute a SPARQL query against the Wikidata endpoint."""
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": "RugbyMappingBot/1.0 (https://github.com/jmforsythe/Rugby-Map)",
    }
    resp = requests.get(
        _WIKIDATA_SPARQL,
        params={"query": query},
        headers=headers,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


def _fetch_wikidata_coords() -> dict[str, dict]:
    """Query Wikidata for football club ground coordinates.

    Returns a dict keyed by Wikipedia article title with values:
        {ground_name, latitude, longitude}
    """
    coords: dict[str, dict] = {}

    logger.info("Querying Wikidata for club ground coordinates …")
    for row in _run_sparql(_SPARQL_QUERY):
        article_url = row["article"]["value"]
        title = _wiki_article_title(article_url)
        lat, lon = _parse_coord(row["coord"]["value"])
        coords[title] = {
            "ground": row["groundLabel"]["value"],
            "latitude": lat,
            "longitude": lon,
        }

    ground_count = len(coords)
    logger.info("  Got %d clubs with ground coordinates", ground_count)

    logger.info("Querying Wikidata for club-level coordinates (fallback) …")
    for row in _run_sparql(_SPARQL_CLUB_COORDS):
        article_url = row["article"]["value"]
        title = _wiki_article_title(article_url)
        if title in coords:
            continue
        lat, lon = _parse_coord(row["coord"]["value"])
        coords[title] = {
            "ground": row["clubLabel"]["value"],
            "latitude": lat,
            "longitude": lon,
        }

    logger.info(
        "  Got %d additional clubs from club-level coords (total: %d)",
        len(coords) - ground_count,
        len(coords),
    )
    return coords


# ---------------------------------------------------------------------------
# Combine and output
# ---------------------------------------------------------------------------


def _sanitize_filename(name: str) -> str:
    """Convert a league/division name to a filesystem-safe string."""
    s = name.replace(" ", "_").replace("/", "_").replace("&", "and")
    s = re.sub(r"[^\w\-()]", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def _build_geocoded_teams(
    clubs: list[dict],
    coords: dict[str, dict],
) -> dict[str, dict]:
    """Group clubs by league and build geocoded_teams JSON structures.

    Returns dict mapping sanitized league filename -> GeocodedLeague dict.
    """
    leagues: dict[str, list[dict]] = {}
    matched = 0
    unmatched_names: list[str] = []

    for club in clubs:
        wiki_title = club["wiki_title"]
        coord = coords.get(wiki_title)

        team: dict = {
            "name": club["name"],
            "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(wiki_title)}",
            "image_url": None,
            "address": coord["ground"] if coord else None,
            "level": club["level"],
        }

        if coord:
            team["latitude"] = coord["latitude"]
            team["longitude"] = coord["longitude"]
            team["formatted_address"] = coord["ground"]
            matched += 1
        else:
            unmatched_names.append(f"  {club['name']} ({wiki_title})")

        league_key = club["league"]
        leagues.setdefault(league_key, []).append(team)

    logger.info(
        "Matched %d / %d clubs with coordinates (%.0f%%)",
        matched,
        len(clubs),
        100 * matched / max(len(clubs), 1),
    )
    if unmatched_names:
        logger.info(
            "%d clubs without coordinates:\n%s",
            len(unmatched_names),
            "\n".join(sorted(unmatched_names)[:50]),
        )
        if len(unmatched_names) > 50:
            logger.info("  … and %d more", len(unmatched_names) - 50)

    result: dict[str, dict] = {}
    for league_name, teams in sorted(leagues.items()):
        filename = _sanitize_filename(league_name)
        result[filename] = {
            "league_name": league_name,
            "league_url": "",
            "teams": teams,
            "team_count": len(teams),
        }

    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch English football pyramid data")
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached Wikidata results",
    )
    args = parser.parse_args()

    setup_logging()

    # --- 1. Fetch & parse Wikipedia ---
    logger.info("Fetching Wikipedia club list …")
    resp = requests.get(
        _WIKI_URL,
        headers={"User-Agent": "RugbyMappingBot/1.0 (https://github.com/jmforsythe/Rugby-Map)"},
        timeout=30,
    )
    resp.raise_for_status()
    clubs = _parse_wikipedia_clubs(resp.text)

    if not clubs:
        logger.error("No clubs parsed from Wikipedia — aborting")
        sys.exit(1)

    # --- 2. Fetch Wikidata coordinates (with cache) ---
    if _CACHE_FILE.exists() and not args.no_cache:
        logger.info("Loading cached Wikidata coordinates from %s", _CACHE_FILE.name)
        with open(_CACHE_FILE, encoding="utf-8") as f:
            coords = json.load(f)
    else:
        coords = _fetch_wikidata_coords()
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(coords, f, indent=2, ensure_ascii=False)
        logger.info("Saved Wikidata cache to %s", _CACHE_FILE.name)

    # --- 3. Combine and write output ---
    geocoded = _build_geocoded_teams(clubs, coords)

    out_dir = DATA_DIR / "geocoded_teams" / args.season / "pyramid"
    out_dir.mkdir(parents=True, exist_ok=True)

    for filename, data in geocoded.items():
        out_path = out_dir / f"{filename}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info(
        "Wrote %d league files to %s",
        len(geocoded),
        out_dir.relative_to(REPO_ROOT),
    )

    # Summary by level
    level_counts: dict[int, int] = {}
    level_matched: dict[int, int] = {}
    for club in clubs:
        lvl = club["level"]
        level_counts[lvl] = level_counts.get(lvl, 0) + 1
        if club["wiki_title"] in coords:
            level_matched[lvl] = level_matched.get(lvl, 0) + 1

    logger.info("Coverage by level:")
    for lvl in sorted(level_counts):
        total = level_counts[lvl]
        have = level_matched.get(lvl, 0)
        logger.info("  Level %2d: %3d / %3d (%.0f%%)", lvl, have, total, 100 * have / total)


if __name__ == "__main__":
    main()
