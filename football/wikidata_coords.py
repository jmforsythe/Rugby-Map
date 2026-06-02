"""Wikidata SPARQL lookup for English football club ground coordinates."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse

import requests

from football import DATA_DIR

logger = logging.getLogger(__name__)

_WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
_CACHE_FILE = DATA_DIR / "pyramid_cache.json"
_USER_AGENT = "RugbyMappingProject/1.0 (https://github.com/jmforsythe/Rugby-Map)"

_SPARQL_GROUND_COORDS = """\
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


def _wiki_article_title(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    return urllib.parse.unquote(path.split("/wiki/")[-1])


def _parse_coord(wkt: str) -> tuple[float, float]:
    match = re.match(r"Point\(([^ ]+) ([^ ]+)\)", wkt)
    if not match:
        raise ValueError(f"Cannot parse WKT coordinate: {wkt}")
    lon, lat = float(match.group(1)), float(match.group(2))
    return lat, lon


def _run_sparql(query: str) -> list[dict]:
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": _USER_AGENT,
    }
    resp = requests.get(
        _WIKIDATA_SPARQL,
        params={"query": query},
        headers=headers,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


def fetch_wikidata_coords() -> dict[str, dict]:
    """Query Wikidata for football club ground coordinates keyed by Wikipedia title."""
    coords: dict[str, dict] = {}

    logger.info("Querying Wikidata for club ground coordinates …")
    for row in _run_sparql(_SPARQL_GROUND_COORDS):
        title = _wiki_article_title(row["article"]["value"])
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
        title = _wiki_article_title(row["article"]["value"])
        if title in coords:
            continue
        lat, lon = _parse_coord(row["coord"]["value"])
        coords[title] = {
            "ground": row["clubLabel"]["value"],
            "latitude": lat,
            "longitude": lon,
        }

    logger.info("  Total Wikidata coords: %d", len(coords))
    return coords


def load_wikidata_coords(*, refresh: bool = False) -> dict[str, dict]:
    """Load Wikidata coords from cache, fetching if needed."""
    if not refresh and _CACHE_FILE.exists():
        with open(_CACHE_FILE, encoding="utf-8") as f:
            coords = json.load(f)
        logger.info("Loaded %d cached Wikidata coords", len(coords))
        return coords

    coords = fetch_wikidata_coords()
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(coords, f, indent=2, ensure_ascii=False)
    logger.info("Saved Wikidata cache to %s", _CACHE_FILE.name)
    return coords
