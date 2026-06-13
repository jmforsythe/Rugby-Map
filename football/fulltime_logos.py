"""
Fetch club crest URLs from FA Full-Time team pages.

Crests appear in the ``team-header flex middle center`` block on each
``displayTeam.html`` page (``resources.thefa.com`` image URLs).

Cache: ``data/football/fulltime_logo_cache.json`` keyed by Full-Time ``teamID``.

Run standalone to enrich existing JSON:
  python -m football.fulltime_logos --season 2025-2026 --subdir feeder
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from core import make_request, setup_logging
from core.config import REPO_ROOT
from football import DATA_DIR

logger = logging.getLogger(__name__)

_FULLTIME_BASE = "https://fulltime.thefa.com"
_CACHE_FILE = DATA_DIR / "fulltime_logo_cache.json"
_TEAM_HEADER_CLASSES = frozenset({"team-header", "flex", "middle", "center"})
_FT_PLACEHOLDER_CREST = "icon-club.svg"


def is_usable_football_crest_url(url: object) -> bool:
    """True when ``url`` is a real crest, not the FA Full-Time generic club placeholder."""
    if not isinstance(url, str):
        return False
    text = url.strip()
    if not text.startswith("https://"):
        return False
    return _FT_PLACEHOLDER_CREST not in text.casefold()


def team_url_cache_key(team_url: str) -> str | None:
    """Stable cache key from a Full-Time team page URL."""
    parsed = urlparse(team_url)
    if "fulltime.thefa.com" not in (parsed.netloc or "").casefold():
        return None
    team_id = parse_qs(parsed.query).get("teamID", [None])[0]
    return team_id or team_url


def is_fulltime_team_url(url: object) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    return "fulltime.thefa.com" in url.casefold() and "displayteam" in url.casefold()


def extract_crest_url_from_team_html(html: str) -> str | None:
    """Parse crest ``src`` from the team-header block on a Full-Time team page."""
    soup = BeautifulSoup(html, "html.parser")
    header: Tag | None = None

    for element in soup.find_all(class_=True):
        classes = set(element.get("class", []))
        if _TEAM_HEADER_CLASSES.issubset(classes):
            header = element
            break

    if header is None:
        header = soup.find(class_=re.compile(r"\bteam-header\b"))
    if header is None or not isinstance(header, Tag):
        return None

    img = header.find("img", src=True)
    if img is None:
        return None

    src = (img.get("src") or "").strip()
    if not src:
        return None
    if src.startswith("//"):
        src = f"https:{src}"
    elif src.startswith("/"):
        src = f"{_FULLTIME_BASE}{src}"
    if not is_usable_football_crest_url(src):
        return None
    return src


def fetch_crest_url_for_team_page(team_url: str) -> str | None:
    """Download a Full-Time team page and return the crest image URL."""
    if not is_fulltime_team_url(team_url):
        return None
    response = make_request(team_url, delay_seconds=1.0)
    return extract_crest_url_from_team_html(response.text)


def load_fulltime_logo_cache(*, refresh: bool = False) -> dict[str, str | None]:
    if refresh or not _CACHE_FILE.exists():
        return {}
    with open(_CACHE_FILE, encoding="utf-8") as f:
        return dict(json.load(f).items())


def save_fulltime_logo_cache(cache: dict[str, str | None]) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False, sort_keys=True)


def resolve_fulltime_logos(
    team_urls: list[str],
    *,
    refresh: bool = False,
) -> dict[str, str | None]:
    """Return crest URLs keyed by original ``team_url`` (cache keyed by ``teamID``)."""
    cache = load_fulltime_logo_cache(refresh=refresh)
    unique_urls = sorted({u for u in team_urls if is_fulltime_team_url(u)})
    result: dict[str, str | None] = {url: None for url in unique_urls}

    to_fetch: list[tuple[str, str]] = []
    for url in unique_urls:
        key = team_url_cache_key(url)
        if not key:
            continue
        if refresh or key not in cache or cache.get(key) is None:
            to_fetch.append((url, key))
        else:
            result[url] = cache[key]

    if to_fetch:
        logger.info("Fetching Full-Time crests for %d teams …", len(to_fetch))
    for url, key in to_fetch:
        crest = fetch_crest_url_for_team_page(url)
        cache[key] = crest
        result[url] = crest
        if crest:
            logger.debug("  %s -> %s", key, crest)
    if to_fetch:
        save_fulltime_logo_cache(cache)

    return result


def apply_fulltime_logo_to_team(team: dict, logos: dict[str, str | None]) -> None:
    if team.get("image_url"):
        return
    url = team.get("url", "")
    if not is_fulltime_team_url(url):
        return
    crest = logos.get(url)
    if is_usable_football_crest_url(crest):
        team["image_url"] = crest


def apply_fulltime_logos_to_league(league: dict, logos: dict[str, str | None]) -> int:
    updated = 0
    for team in league.get("teams", []):
        before = team.get("image_url")
        apply_fulltime_logo_to_team(team, logos)
        if team.get("image_url") and team.get("image_url") != before:
            updated += 1
    return updated


def enrich_season_logos(
    season: str,
    *,
    subdirs: tuple[str, ...] = ("pyramid", "feeder"),
    refresh: bool = False,
) -> tuple[int, int, int]:
    """Backfill ``image_url`` on team JSON using Full-Time team pages."""
    json_paths: list[Path] = []
    team_urls: list[str] = []

    for subdir in subdirs:
        root = DATA_DIR / "geocoded_teams" / season / subdir
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.json")):
            json_paths.append(path)
            with open(path, encoding="utf-8") as f:
                league = json.load(f)
            for team in league.get("teams", []):
                if team.get("image_url"):
                    continue
                url = team.get("url", "")
                if is_fulltime_team_url(url):
                    team_urls.append(url)

    unique_urls = sorted(set(team_urls))
    logos = resolve_fulltime_logos(unique_urls, refresh=refresh)
    found = sum(1 for url in unique_urls if logos.get(url))

    total_updated = 0
    for path in json_paths:
        with open(path, encoding="utf-8") as f:
            league = json.load(f)
        updated = apply_fulltime_logos_to_league(league, logos)
        if updated:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(league, f, indent=2, ensure_ascii=False)
                f.write("\n")
            total_updated += updated
            addr_path = DATA_DIR / "team_addresses" / season / path.parent.name / path.name
            if addr_path.is_file():
                with open(addr_path, encoding="utf-8") as f:
                    addr_league = json.load(f)
                apply_fulltime_logos_to_league(addr_league, logos)
                with open(addr_path, "w", encoding="utf-8") as f:
                    json.dump(addr_league, f, indent=2, ensure_ascii=False)
                    f.write("\n")

    return found, len(unique_urls), total_updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch FA Full-Time crest URLs for football clubs")
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument(
        "--subdir",
        action="append",
        default=["feeder"],
        help="Subdirectory under geocoded_teams/<season>/ (repeatable)",
    )
    parser.add_argument("--refresh", action="store_true", help="Ignore cache and re-fetch")
    args = parser.parse_args()

    setup_logging()
    subdirs = tuple(args.subdir)
    found, total, updated = enrich_season_logos(args.season, subdirs=subdirs, refresh=args.refresh)
    logger.info(
        "Resolved %d/%d Full-Time crests; updated %d team records under %s",
        found,
        total,
        updated,
        (DATA_DIR / "geocoded_teams" / args.season).relative_to(REPO_ROOT),
    )


if __name__ == "__main__":
    main()
