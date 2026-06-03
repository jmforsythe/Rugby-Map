"""
Fetch football club home grounds from English Wikipedia infoboxes.

Reads the ``| ground =`` (or ``| stadium =``) field from each club article's
wikitext via the MediaWiki API.  Results are cached in
``data/football/wiki_ground_cache.json`` keyed by ``wiki_title``.

Run standalone to enrich existing pyramid JSON:
  python -m football.wikipedia_grounds --season 2025-2026
  python -m football.wikipedia_grounds --season 2025-2026 --refresh
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path

import requests

from core import setup_logging
from core.config import REPO_ROOT
from football import DATA_DIR

logger = logging.getLogger(__name__)

_WIKI_API = "https://en.wikipedia.org/w/api.php"
_CACHE_FILE = DATA_DIR / "wiki_ground_cache.json"
_USER_AGENT = "RugbyMappingProject/1.0 (https://github.com/jmforsythe/Rugby-Map)"
_BATCH_SIZE = 50
_REQUEST_DELAY = 0.1

_GROUND_FIELDS = ("ground", "stadium")
_REF_TAG = re.compile(r"<ref[^>]*>.*?</ref>", re.IGNORECASE | re.DOTALL)
_SELF_CLOSING_REF = re.compile(r"<ref[^>]*/>", re.IGNORECASE)
_BR_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
_WIKI_LINK = re.compile(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]")
_TEMPLATE = re.compile(r"\{\{[^{}]*(?:\{\{[^{}]*\}\}[^{}]*)*\}\}")


def _normalize_title(title: str) -> str:
    return title.replace("_", " ").strip().casefold()


def _api_get(**params: object) -> dict:
    resp = requests.get(
        _WIKI_API,
        params={"format": "json", "formatversion": 2, **params},
        headers={"User-Agent": _USER_AGENT},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def clean_wiki_ground_value(raw: str) -> str | None:
    """Strip wikitext markup from an infobox ground/stadium value."""
    text = raw.strip()
    if not text:
        return None

    text = _REF_TAG.sub("", text)
    text = _SELF_CLOSING_REF.sub("", text)
    text = _BR_TAG.sub(", ", text)
    # Drop nested templates iteratively (e.g. {{small|…}}, {{coord|…}}).
    while _TEMPLATE.search(text):
        text = _TEMPLATE.sub("", text)
    text = _WIKI_LINK.sub(r"\1", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"'''+?", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")

    if not text or text.casefold() in {"?", "n/a", "tbc", "tba", "various"}:
        return None
    return text


def extract_ground_from_wikitext(wikitext: str) -> str | None:
    """Return the cleaned home ground string from article wikitext, if present."""
    for field in _GROUND_FIELDS:
        match = re.search(rf"^\|\s*{field}\s*=\s*(.*)$", wikitext, re.IGNORECASE | re.MULTILINE)
        if match:
            cleaned = clean_wiki_ground_value(match.group(1))
            if cleaned:
                return cleaned
    return None


def fetch_wikipedia_grounds(wiki_titles: list[str]) -> dict[str, str | None]:
    """Fetch infobox ground strings for Wikipedia article titles."""
    unique = sorted({t for t in wiki_titles if t})
    if not unique:
        return {}

    by_norm = {_normalize_title(t): t for t in unique}
    found: dict[str, str | None] = {t: None for t in unique}

    for batch in _chunks(unique, _BATCH_SIZE):
        data = _api_get(
            action="query",
            prop="revisions",
            rvprop="content",
            rvslots="main",
            titles="|".join(batch),
        )
        for page in data.get("query", {}).get("pages", []):
            title = page.get("title", "")
            wiki_title = by_norm.get(_normalize_title(title))
            if not wiki_title:
                continue
            revisions = page.get("revisions") or []
            if not revisions:
                continue
            wikitext = revisions[0].get("slots", {}).get("main", {}).get("content", "")
            found[wiki_title] = extract_ground_from_wikitext(wikitext)
        time.sleep(_REQUEST_DELAY)

    return found


def load_ground_cache(*, refresh: bool = False) -> dict[str, str | None]:
    if refresh or not _CACHE_FILE.exists():
        return {}
    with open(_CACHE_FILE, encoding="utf-8") as f:
        return dict(json.load(f).items())


def save_ground_cache(cache: dict[str, str | None]) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False, sort_keys=True)


def resolve_grounds(
    wiki_titles: list[str],
    *,
    refresh: bool = False,
) -> dict[str, str | None]:
    """Return infobox ground strings for titles, using cache and fetching missing entries."""
    cache = load_ground_cache(refresh=refresh)
    unique = sorted({t for t in wiki_titles if t})
    if refresh:
        missing = unique
    else:
        missing = [t for t in unique if t not in cache]
    if missing:
        logger.info("Fetching Wikipedia infobox grounds for %d clubs …", len(missing))
        fetched = fetch_wikipedia_grounds(missing)
        cache.update(fetched)
        save_ground_cache(cache)
    return {t: cache.get(t) for t in unique}


def apply_ground_to_team(team: dict, grounds: dict[str, str | None]) -> None:
    wiki_title = team.get("wiki_title", "")
    if not wiki_title:
        return
    ground = grounds.get(wiki_title)
    if ground:
        team["address"] = ground
        team["address_source"] = "wikipedia"


def apply_grounds_to_league(league: dict, grounds: dict[str, str | None]) -> int:
    updated = 0
    for team in league.get("teams", []):
        before = team.get("address")
        apply_ground_to_team(team, grounds)
        if team.get("address") and team.get("address") != before:
            updated += 1
    return updated


def enrich_pyramid_season(season: str, *, refresh: bool = False) -> tuple[int, int, int]:
    """Add Wikipedia infobox grounds to pyramid team_addresses and geocoded_teams JSON."""
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
    grounds = resolve_grounds(unique_titles, refresh=refresh)
    found = sum(1 for g in grounds.values() if g)

    total_updated = 0
    for path in json_paths:
        with open(path, encoding="utf-8") as f:
            league = json.load(f)
        updated = apply_grounds_to_league(league, grounds)
        if updated:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(league, f, indent=2, ensure_ascii=False)
                f.write("\n")
            total_updated += updated

    return found, len(unique_titles), total_updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Wikipedia infobox grounds for football clubs"
    )
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument(
        "--refresh", action="store_true", help="Ignore cache and re-fetch all grounds"
    )
    args = parser.parse_args()

    setup_logging()
    found, total, updated = enrich_pyramid_season(args.season, refresh=args.refresh)
    logger.info(
        "Resolved %d/%d Wikipedia grounds; updated %d team records under %s",
        found,
        total,
        updated,
        (DATA_DIR / "geocoded_teams" / args.season / "pyramid").relative_to(REPO_ROOT),
    )


if __name__ == "__main__":
    main()
