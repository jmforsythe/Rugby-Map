"""
Fetch football club crest URLs from English Wikipedia.

Uses the MediaWiki API ``pageprops.page_image`` field (primary infobox image),
then resolves each file to an ``upload.wikimedia.org`` URL via ``imageinfo``.
Clubs without ``page_image`` fall back to the Wikipedia REST summary endpoint.

Cache: ``data/football/wiki_logo_cache.json`` keyed by ``wiki_title``.

Run standalone to enrich existing pyramid JSON:
  python -m football.wikipedia_logos --season 2025-2026
  python -m football.wikipedia_logos --season 2025-2026 --refresh
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.parse
from pathlib import Path

import requests

from core import setup_logging
from core.config import REPO_ROOT
from football import DATA_DIR

logger = logging.getLogger(__name__)

_WIKI_API = "https://en.wikipedia.org/w/api.php"
_WIKI_REST_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_CACHE_FILE = DATA_DIR / "wiki_logo_cache.json"
_USER_AGENT = "RugbyMappingProject/1.0 (https://github.com/jmforsythe/Rugby-Map)"
_BATCH_SIZE = 50
_REQUEST_DELAY = 0.1


def _normalize_title(title: str) -> str:
    return title.replace("_", " ").strip().casefold()


def _api_get(**params: object) -> dict:
    resp = requests.get(
        _WIKI_API,
        params={"format": "json", **params},
        headers={"User-Agent": _USER_AGENT},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def fetch_page_image_filenames(wiki_titles: list[str]) -> dict[str, str | None]:
    """Map wiki_title -> infobox image filename (no ``File:`` prefix), or None."""
    by_norm = {_normalize_title(t): t for t in wiki_titles}
    found: dict[str, str | None] = {t: None for t in wiki_titles}

    for batch in _chunks(wiki_titles, _BATCH_SIZE):
        data = _api_get(action="query", prop="pageprops", titles="|".join(batch))
        for page in data.get("query", {}).get("pages", {}).values():
            title = page.get("title", "")
            wiki_title = by_norm.get(_normalize_title(title))
            if not wiki_title:
                continue
            filename = page.get("pageprops", {}).get("page_image")
            found[wiki_title] = filename
        time.sleep(_REQUEST_DELAY)

    return found


def _normalize_filename(filename: str) -> str:
    name = filename[5:] if filename.startswith("File:") else filename
    return name.replace("_", " ").strip().casefold()


def fetch_file_urls(filenames: list[str]) -> dict[str, str]:
    """Map image filename -> direct ``upload.wikimedia.org`` URL."""
    unique = sorted({fn for fn in filenames if fn})
    urls: dict[str, str] = {}

    for batch in _chunks(unique, _BATCH_SIZE):
        file_titles = [f"File:{fn}" if not fn.startswith("File:") else fn for fn in batch]
        by_norm = {_normalize_filename(fn): fn for fn in batch}
        data = _api_get(
            action="query",
            prop="imageinfo",
            titles="|".join(file_titles),
            iiprop="url",
        )
        for page in data.get("query", {}).get("pages", {}).values():
            imageinfo = page.get("imageinfo")
            if not imageinfo:
                continue
            url = imageinfo[0].get("url")
            if not url:
                continue
            page_title = page.get("title", "")
            original = by_norm.get(_normalize_filename(page_title))
            if original:
                urls[original] = url
        time.sleep(_REQUEST_DELAY)

    return urls


def fetch_summary_images(wiki_titles: list[str]) -> dict[str, str | None]:
    """Fallback: REST page summary ``originalimage`` / ``thumbnail`` URLs."""
    result: dict[str, str | None] = {t: None for t in wiki_titles}
    for wiki_title in wiki_titles:
        path = urllib.parse.quote(wiki_title.replace(" ", "_"), safe="")
        try:
            resp = requests.get(
                f"{_WIKI_REST_SUMMARY}{path}",
                headers={"User-Agent": _USER_AGENT},
                timeout=30,
            )
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()
            original = data.get("originalimage", {}).get("source")
            thumb = data.get("thumbnail", {}).get("source")
            result[wiki_title] = original or thumb
        except requests.RequestException as exc:
            logger.debug("REST summary failed for %s: %s", wiki_title, exc)
        time.sleep(_REQUEST_DELAY)
    return result


def fetch_wikipedia_logos(wiki_titles: list[str]) -> dict[str, str | None]:
    """Resolve Wikipedia article titles to crest image URLs."""
    unique = sorted({t for t in wiki_titles if t})
    if not unique:
        return {}

    filenames = fetch_page_image_filenames(unique)
    file_urls = fetch_file_urls([fn for fn in filenames.values() if fn])

    result: dict[str, str | None] = {}
    for wiki_title in unique:
        filename = filenames.get(wiki_title)
        result[wiki_title] = file_urls.get(filename) if filename else None

    missing = [t for t in unique if not result.get(t)]
    if missing:
        logger.info("REST summary fallback for %d clubs without page_image", len(missing))
        summaries = fetch_summary_images(missing)
        for wiki_title, url in summaries.items():
            if url:
                result[wiki_title] = url

    return result


def load_logo_cache(*, refresh: bool = False) -> dict[str, str | None]:
    if refresh or not _CACHE_FILE.exists():
        return {}
    with open(_CACHE_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    return dict(raw.items())


def save_logo_cache(cache: dict[str, str | None]) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False, sort_keys=True)


def resolve_logos(
    wiki_titles: list[str],
    *,
    refresh: bool = False,
) -> dict[str, str | None]:
    """Return logo URLs for titles, using cache and fetching missing entries."""
    cache = load_logo_cache(refresh=refresh)
    unique = sorted({t for t in wiki_titles if t})
    if refresh:
        missing = unique
    else:
        missing = [t for t in unique if t not in cache or cache.get(t) is None]
    if missing:
        logger.info("Fetching Wikipedia logos for %d clubs …", len(missing))
        fetched = fetch_wikipedia_logos(missing)
        cache.update(fetched)
        save_logo_cache(cache)
    return {t: cache.get(t) for t in unique}


def apply_logos_to_team(team: dict, logos: dict[str, str | None]) -> None:
    wiki_title = team.get("wiki_title", "")
    if not wiki_title:
        return
    url = logos.get(wiki_title)
    if url:
        team["image_url"] = url


def apply_logos_to_league(league: dict, logos: dict[str, str | None]) -> int:
    updated = 0
    for team in league.get("teams", []):
        before = team.get("image_url")
        apply_logos_to_team(team, logos)
        if team.get("image_url") and team.get("image_url") != before:
            updated += 1
    return updated


def enrich_pyramid_season(season: str, *, refresh: bool = False) -> tuple[int, int, int]:
    """Add ``image_url`` to pyramid team_addresses and geocoded_teams JSON files."""
    wiki_titles: list[str] = []
    json_paths: list[Path] = []

    for subdir in ("team_addresses", "geocoded_teams"):
        pyramid_dir = DATA_DIR / subdir / season / "pyramid"
        if not pyramid_dir.is_dir():
            continue
        for path in sorted(pyramid_dir.glob("*.json")):
            json_paths.append(path)
            with open(path, encoding="utf-8") as f:
                league = json.load(f)
            for team in league.get("teams", []):
                title = team.get("wiki_title")
                if title:
                    wiki_titles.append(title)

    unique_titles = sorted({t for t in wiki_titles if t})
    logos = resolve_logos(unique_titles, refresh=refresh)
    found = sum(1 for url in logos.values() if url)

    total_updated = 0
    for path in json_paths:
        with open(path, encoding="utf-8") as f:
            league = json.load(f)
        updated = apply_logos_to_league(league, logos)
        if updated:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(league, f, indent=2, ensure_ascii=False)
                f.write("\n")
            total_updated += updated

    return found, len(unique_titles), total_updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Wikipedia crest URLs for football clubs")
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument(
        "--refresh", action="store_true", help="Ignore cache and re-fetch all logos"
    )
    args = parser.parse_args()

    setup_logging()
    found, total, updated = enrich_pyramid_season(args.season, refresh=args.refresh)
    logger.info(
        "Resolved %d/%d logos; updated %d team records under %s",
        found,
        total,
        updated,
        (DATA_DIR / "geocoded_teams" / args.season / "pyramid").relative_to(REPO_ROOT),
    )


if __name__ == "__main__":
    main()
