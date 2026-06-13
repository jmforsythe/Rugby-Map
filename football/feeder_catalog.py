"""Level-11 Regional Feeder league catalog (Wikipedia + FA Full-Time discovery)."""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import requests

from core import setup_logging
from football import DATA_DIR
from football.fetch_pyramid import _sanitize_filename
from football.fulltime import fetch_league_index, list_division_options, pick_division_id

logger = logging.getLogger(__name__)

_WIKI_SYSTEM_URL = "https://en.wikipedia.org/wiki/English_football_league_system"
_WIKI_API = "https://en.wikipedia.org/w/api.php"
_USER_AGENT = "RugbyMappingProject/1.0 (https://github.com/jmforsythe/Rugby-Map)"
_CATALOG_DIR = DATA_DIR / "feeder_leagues"
_LEVEL = 11

# Wikipedia system-page label → article title for extlinks / opensearch.
_WIKI_TITLE_OVERRIDES: dict[str, str] = {
    "Cheshire League": "Cheshire Association Football League",
    "Devon Football League": "Devon Football League",
    "Dorset Premier League": "Dorset Premier Football League",
    "Essex Alliance League": "Essex Alliance Football League",
    "Essex & Suffolk Border League": "Essex and Suffolk Border Football League",
    "Essex Olympian League": "Essex Olympian Football League",
    "Humber Premier League": "Humber Premier League",
    "Kent County League": "Kent County League",
    "Central Midlands Alliance": "Central Midlands Alliance League",
    "Liverpool County Premier League": "Liverpool County Premier League",
    "Manchester League": "Manchester Football League",
    "Mid-Sussex League": "Mid Sussex Football League",
    "Middlesex County League": "Middlesex County Football League",
    "Northern Alliance": "Northern Football Alliance",
    "North Riding League": "North Riding Football League",
    "Nottinghamshire Senior League": "Nottinghamshire Senior League",
    "Oxfordshire Senior League": "Oxfordshire Senior Football League",
    "Peterborough & District League": "Peterborough and District Football League",
    "Sheffield & Hallamshire County Senior League": (
        "Sheffield_&_Hallamshire_County_Senior_Football_League"
    ),
    "Shropshire County Football League": "Shropshire County Football League",
    "Somerset County League": "Somerset County League",
    "Southern Combination League": "Southern Combination Football League",
    "Spartan South Midlands League": "Spartan South Midlands Football League",
    "St Piran League": "St Piran Football League",
    "Staffordshire County Senior League": "Staffordshire County Senior League",
    "Suffolk & Ipswich League": "Suffolk and Ipswich Football League",
    "Surrey Premier County Football League": "Surrey Premier County Football League",
    "Thames Valley Premier League": "Thames Valley Premier Football League",
    "Wearside League": "Wearside Football League",
    "West Cheshire League": "West Cheshire Association Football League",
    "West Lancashire League": "West Lancashire Football League",
    "West Midlands (Regional) League": "West Midlands (Regional) League",
    "West Yorkshire League": "West Yorkshire Association Football League",
    "Wiltshire Senior League": "Wiltshire Football League",
    "York League": "York Football League",
    "Yorkshire Amateur League": "Yorkshire Amateur Football League",
    "Midland League": "Midland Football League",
    "Anglian Combination": "Anglian Combination",
    "Bedfordshire County League": "Bedfordshire County Football League",
    "Cambridgeshire County League": "Cambridgeshire County Football League",
    "Gloucestershire County League": "Gloucestershire County Football League",
    "Hampshire Premier League": "Hampshire Premier League",
    "Herefordshire Football League": "Herefordshire Football League",
    "Hertfordshire Senior County League": "Hertfordshire Senior County Football League",
    "Leicestershire Senior League": "Leicestershire Senior Football League",
    "Lincolnshire Football League": "Lincolnshire Football League",
    "Northamptonshire Combination League": "Northamptonshire Combination Football League",
}


def catalog_path(season: str) -> Path:
    return _CATALOG_DIR / f"{season}.json"


def _api_get(**params: object) -> dict:
    resp = requests.get(
        _WIKI_API,
        params={"format": "json", "formatversion": 2, **params},
        headers={"User-Agent": _USER_AGENT},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


_CLUB_COUNT_RE = re.compile(
    r"^(.+?)\s*[\u2013\u2014\u2212\-–—]\s*(\d+)\s*clubs?\s*$",
    re.IGNORECASE,
)


def _parse_division_li(text: str) -> dict | None:
    cleaned = re.sub(r"\s+", " ", text.strip())
    match = _CLUB_COUNT_RE.match(cleaned)
    if not match:
        return None
    division_name = match.group(1).strip()
    return {
        "division_name": division_name,
        "level": _LEVEL,
        "expected_clubs": int(match.group(2)),
        "filename": _sanitize_filename(division_name),
    }


def parse_level11_from_wikipedia(html: str) -> list[dict]:
    """Parse Regional Feeder (level 11) divisions from the league-system page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Current layout: level-11 divisions live in a standalone <ul> (not inside the
    # summary wikitable row, which only shows the club total).
    for ul in soup.find_all("ul"):
        lis = ul.find_all("li", recursive=False)
        if not lis:
            continue
        first = lis[0].get_text(" ", strip=True)
        if not first.startswith("Anglian Combination Premier Division"):
            continue
        entries = [_parse_division_li(li.get_text(" ", strip=True)) for li in lis]
        parsed = [e for e in entries if e]
        if parsed:
            return parsed

    # Legacy layout: divisions nested in the level-11 wikitable row.
    for table in soup.find_all("table", class_="wikitable"):
        for row in table.find_all("tr"):
            header = row.find("th")
            if not header:
                continue
            header_text = header.get_text(" ", strip=True)
            if not re.match(r"11\s*\(", header_text):
                continue
            ul = row.find("ul")
            if not ul:
                continue
            parsed = [
                e
                for li in ul.find_all("li", recursive=False)
                if (e := _parse_division_li(li.get_text(" ", strip=True)))
            ]
            if parsed:
                return parsed
    return []


def league_stem(division_name: str) -> str:
    """Strip tier suffix to get a league family name for Wikipedia lookup."""
    name = re.sub(r"\s+", " ", division_name.strip())
    for pattern in (
        r"\s+Premier\s+Division(?:\s+(North|South|East|West))?$",
        r"\s+Senior\s+Division$",
        r"\s+Supreme\s+Division$",
        r"\s+Division\s+(One|Two|Three)(?:\s+(North|South|East|West))?$",
    ):
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            return name[: match.start()].strip()
    return name


def division_hint(division_name: str) -> str:
    """Substring used to pick the FA Full-Time division dropdown entry."""
    for marker in (
        "Premier Division",
        "Senior Division",
        "Supreme Division",
        "Division One",
        "Division Two",
        "Division Three",
    ):
        if marker.casefold() in division_name.casefold():
            idx = division_name.casefold().index(marker.casefold())
            return division_name[idx:].strip()
    return division_name


def wiki_article_for_division(division_name: str) -> str:
    stem = league_stem(division_name)
    if stem in _WIKI_TITLE_OVERRIDES:
        return _WIKI_TITLE_OVERRIDES[stem]
    if stem.endswith(" League"):
        return stem
    if " League" not in stem and " Alliance" not in stem:
        return f"{stem} Football League"
    return stem


def _first_query_page(data: dict) -> dict:
    pages = data.get("query", {}).get("pages", {})
    if isinstance(pages, dict):
        return next(iter(pages.values()))
    if isinstance(pages, list) and pages:
        return pages[0]
    return {}


def discover_fa_league_id(wiki_title: str) -> str | None:
    """Read ``league=`` from English Wikipedia extlinks for ``wiki_title``."""
    data = _api_get(
        action="query",
        titles=wiki_title,
        prop="extlinks",
        ellimit=50,
    )
    page = _first_query_page(data)
    if page.get("missing"):
        return None

    for link in page.get("extlinks") or []:
        url = link.get("url") or link.get("*", "")
        match = re.search(r"league=(\d+)", url)
        if match and "fulltime" in url.casefold() and "league=" in url.casefold():
            return match.group(1)
    return None


def resolve_wiki_title(division_name: str) -> str | None:
    """Return a Wikipedia article title for this feeder division."""
    candidates = [
        wiki_article_for_division(division_name),
        league_stem(division_name),
    ]
    seen: set[str] = set()
    for title in candidates:
        if title in seen:
            continue
        seen.add(title)
        data = _api_get(action="query", titles=title)
        page = _first_query_page(data)
        if not page.get("missing"):
            return title.replace(" ", "_")

    stem = league_stem(division_name)
    search = _api_get(action="opensearch", search=f"{stem} football", limit=8)
    for hit in search[1]:
        hit_cf = hit.casefold()
        if "football" not in hit_cf and "league" not in hit_cf:
            continue
        if stem.casefold() in hit_cf or hit_cf.startswith(stem.casefold()):
            return hit.replace(" ", "_")
    return None


def build_catalog_entry(division_name: str, expected_clubs: int) -> dict:
    wiki_title = resolve_wiki_title(division_name)
    fa_league_id = discover_fa_league_id(wiki_title) if wiki_title else None
    fa_division_id = None
    fa_division_label = None

    if fa_league_id:
        try:
            index_html = fetch_league_index(fa_league_id)
            divisions = list_division_options(index_html)
            hint = division_hint(division_name)
            fa_division_id = pick_division_id(divisions, division_hint=hint)
            if fa_division_id:
                fa_division_label = divisions.get(fa_division_id)
        except Exception as exc:
            logger.warning("FA division lookup failed for %s: %s", division_name, exc)

    return {
        "division_name": division_name,
        "level": _LEVEL,
        "expected_clubs": expected_clubs,
        "filename": _sanitize_filename(division_name),
        "wiki_title": wiki_title,
        "fa_league_id": fa_league_id,
        "fa_division_id": fa_division_id,
        "fa_division_label": fa_division_label,
        "division_hint": division_hint(division_name),
    }


def load_catalog(season: str) -> list[dict]:
    path = catalog_path(season)
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("divisions", data) if isinstance(data, dict) else data


def save_catalog(season: str, divisions: list[dict]) -> Path:
    path = catalog_path(season)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "season": season,
        "level": _LEVEL,
        "source": _WIKI_SYSTEM_URL,
        "divisions": divisions,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def discover_catalog(season: str, *, refresh: bool = False) -> list[dict]:
    """Build or update the feeder catalog for ``season``."""
    existing = {d["division_name"]: d for d in load_catalog(season)} if not refresh else {}

    logger.info("Fetching Wikipedia league system page …")
    resp = requests.get(_WIKI_SYSTEM_URL, headers={"User-Agent": _USER_AGENT}, timeout=60)
    resp.raise_for_status()
    parsed = parse_level11_from_wikipedia(resp.text)
    if not parsed:
        raise RuntimeError("Could not parse level 11 divisions from Wikipedia")

    logger.info("Found %d level-11 divisions on Wikipedia", len(parsed))
    divisions: list[dict] = []

    for idx, row in enumerate(parsed, start=1):
        name = row["division_name"]
        if name in existing and existing[name].get("fa_league_id") and not refresh:
            divisions.append(existing[name])
            logger.info("[%d/%d] %s (cached)", idx, len(parsed), name)
            continue

        logger.info("[%d/%d] Discovering %s …", idx, len(parsed), name)
        entry = build_catalog_entry(name, row["expected_clubs"])
        if name in existing:
            for key in ("fa_league_id", "fa_division_id", "wiki_title"):
                if not entry.get(key) and existing[name].get(key):
                    entry[key] = existing[name][key]
        divisions.append(entry)

    save_catalog(season, divisions)
    with_ft = sum(1 for d in divisions if d.get("fa_league_id"))
    with_div = sum(1 for d in divisions if d.get("fa_division_id"))
    logger.info(
        "Catalog saved: %d divisions, %d with FA league id, %d with FA division id",
        len(divisions),
        with_ft,
        with_div,
    )
    return divisions


def main() -> None:
    parser = argparse.ArgumentParser(description="Build level-11 feeder league catalog")
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-discover all divisions (ignore cached FA ids)",
    )
    args = parser.parse_args()
    setup_logging()
    discover_catalog(args.season, refresh=args.refresh)


if __name__ == "__main__":
    main()
