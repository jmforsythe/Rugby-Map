"""Validate stored league URLs against live RFU meta-page listings.

Reads ``league_data`` (never ``_meta_leagues_cache.json``), scrapes RFU
competition pages fresh, and reports division/competition mismatches. Can
also check that ``fixture_data`` league URLs match ``league_data``.

Usage::

    python -m rugby.analysis.validate_league_urls
    python -m rugby.analysis.validate_league_urls --season 2026-2027
    python -m rugby.analysis.validate_league_urls --check-fixtures --offline
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.parse
from dataclasses import dataclass

from core import League, setup_logging
from core.config import CURRENT_SEASON
from rugby import DATA_DIR
from rugby.fixtures import normalize_fixtures
from rugby.scrape import (
    _BANNED_DIVISION_IDS,
    _BANNED_FILENAMES,
    _BANNED_WORDS,
    _competition_prefix,
    _is_junior_league,
    clean_filename,
    fetch_live_league_urls_by_name,
    normalize_rfu_league_url,
    rfu_league_lookup_key,
    rfu_league_url_key,
)

logger = logging.getLogger(__name__)

LEAGUE_DATA_DIR = DATA_DIR / "league_data"
FIXTURE_DATA_DIR = DATA_DIR / "fixture_data"

# Align with ``rugby.fixtures._FIXTURE_SHRINK_PROTECT_BEFORE``: do not treat RFU
# division changes as actionable when committed historical fixtures exist.
_STALE_URL_PROTECT_BEFORE = "2024-2025"


@dataclass(frozen=True)
class StoredLeague:
    league_name: str
    league_url: str
    relative_path: str
    source: str


@dataclass(frozen=True)
class LeagueUrlIssue:
    kind: str
    league_name: str
    relative_path: str = ""
    stored_url: str = ""
    expected_url: str = ""
    detail: str = ""


def _is_rfu_url(url: str) -> bool:
    return "englandrugby.com" in url.lower()


def _committed_fixture_count(season: str, relative_path: str) -> int:
    """Return normalized fixture count from ``fixture_data`` when present."""
    fixture_path = FIXTURE_DATA_DIR / season / relative_path
    if not fixture_path.is_file():
        return 0
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    return len(normalize_fixtures(data.get("fixtures", [])))


def _should_keep_stored_url(season: str, relative_path: str) -> tuple[bool, int]:
    """Return whether a stale RFU division should not replace the stored URL."""
    if season >= _STALE_URL_PROTECT_BEFORE:
        return False, 0
    fixture_count = _committed_fixture_count(season, relative_path)
    return fixture_count > 0, fixture_count


def _rfu_would_skip_league(league_name: str, league_url: str) -> bool:
    """Return whether ``rugby.scrape`` would exclude this RFU league from league_data."""
    if _is_junior_league(league_name):
        return True
    name_words = [w.strip("()") for w in league_name.lower().split()]
    if any(word in name_words for word in _BANNED_WORDS):
        return True
    params = urllib.parse.parse_qs(urllib.parse.urlparse(league_url).query)
    division_ids = params.get("division", [])
    if division_ids and int(division_ids[0]) in _BANNED_DIVISION_IDS:
        return True
    filename = clean_filename(league_name) + ".json"
    return filename in _BANNED_FILENAMES


def load_stored_leagues(season: str) -> list[StoredLeague]:
    """Load RFU league URLs from league_data and ``_fixture_only_leagues.json``."""
    league_dir = LEAGUE_DATA_DIR / season
    if not league_dir.is_dir():
        logger.error("League data directory not found: %s", league_dir)
        sys.exit(1)

    stored: list[StoredLeague] = []
    seen_paths: set[str] = set()

    for json_file in sorted(league_dir.rglob("*.json")):
        if json_file.name.startswith("_"):
            continue
        with open(json_file, encoding="utf-8") as f:
            data: League = json.load(f)
        league_name = data["league_name"]
        league_url = data.get("league_url", "")
        if not league_url or not _is_rfu_url(league_url):
            continue
        if _is_junior_league(league_name):
            continue
        relative = json_file.relative_to(league_dir).as_posix()
        stored.append(
            StoredLeague(
                league_name=league_name,
                league_url=league_url,
                relative_path=relative,
                source="league_data",
            )
        )
        seen_paths.add(relative)

    sidecar_path = league_dir / "_fixture_only_leagues.json"
    if sidecar_path.exists():
        with open(sidecar_path, encoding="utf-8") as f:
            entries = json.load(f)
        for entry in entries:
            league_name = entry["name"]
            league_url = entry.get("url", "")
            if not league_url or not _is_rfu_url(league_url):
                continue
            if _is_junior_league(league_name):
                continue
            filename = clean_filename(league_name) + ".json"
            parent_url = entry.get("parent_url", "")
            comp_name = _competition_prefix(parent_url) if parent_url else None
            if comp_name:
                relative = f"merit/{comp_name}/{filename}"
            else:
                relative = filename
            if relative in seen_paths:
                continue
            stored.append(
                StoredLeague(
                    league_name=league_name,
                    league_url=league_url,
                    relative_path=relative,
                    source="fixture_only",
                )
            )

    return stored


def compare_stored_to_rfu(
    stored: list[StoredLeague],
    rfu_by_key: dict[tuple[str, str], str],
    season: str,
) -> list[LeagueUrlIssue]:
    """Compare on-disk league URLs to a live RFU (competition, name) -> URL map."""
    issues: list[LeagueUrlIssue] = []
    stored_keys: set[tuple[str, str]] = set()

    for item in stored:
        lookup_key = rfu_league_lookup_key(item.league_url, item.league_name)
        stored_keys.add(lookup_key)
        rfu_url = rfu_by_key.get(lookup_key)
        if rfu_url is None:
            competition = lookup_key[0]
            issues.append(
                LeagueUrlIssue(
                    kind="missing_on_rfu",
                    league_name=item.league_name,
                    relative_path=item.relative_path,
                    stored_url=item.league_url,
                    detail=(
                        f"not listed on RFU meta pages for {season} "
                        f"(competition {competition}, {item.source})"
                    ),
                )
            )
            continue

        stored_key = rfu_league_url_key(normalize_rfu_league_url(item.league_url, season))
        rfu_key = rfu_league_url_key(rfu_url)
        if stored_key != rfu_key:
            keep_stored, fixture_count = _should_keep_stored_url(season, item.relative_path)
            if keep_stored:
                issues.append(
                    LeagueUrlIssue(
                        kind="stale_url_keep_fixtures",
                        league_name=item.league_name,
                        relative_path=item.relative_path,
                        stored_url=item.league_url,
                        expected_url=rfu_url,
                        detail=(
                            f"RFU lists division {rfu_key[1]} but {fixture_count} fixtures "
                            f"committed for division {stored_key[1]}; keeping stored URL "
                            f"(season before {_STALE_URL_PROTECT_BEFORE}) [{item.source}]"
                        ),
                    )
                )
                continue
            issues.append(
                LeagueUrlIssue(
                    kind="stale_url",
                    league_name=item.league_name,
                    relative_path=item.relative_path,
                    stored_url=item.league_url,
                    expected_url=rfu_url,
                    detail=(
                        f"division {stored_key[1]} -> {rfu_key[1]} "
                        f"(competition {stored_key[0]}, season {stored_key[2]}) "
                        f"[{item.source}]"
                    ),
                )
            )

    for lookup_key, rfu_url in sorted(rfu_by_key.items()):
        if lookup_key in stored_keys:
            continue
        league_name = lookup_key[1]
        if _rfu_would_skip_league(league_name, rfu_url):
            continue
        issues.append(
            LeagueUrlIssue(
                kind="missing_in_data",
                league_name=league_name,
                expected_url=rfu_url,
                detail=(
                    f"on RFU for {season} but absent from league_data "
                    f"(competition {lookup_key[0]})"
                ),
            )
        )

    return issues


def compare_fixture_to_league_data(season: str) -> list[LeagueUrlIssue]:
    """Report fixture_data league URLs that differ from league_data for the same path."""
    league_dir = LEAGUE_DATA_DIR / season
    fixture_dir = FIXTURE_DATA_DIR / season
    if not fixture_dir.is_dir():
        return []

    issues: list[LeagueUrlIssue] = []
    for fixture_file in sorted(fixture_dir.rglob("*.json")):
        if fixture_file.name.startswith("_"):
            continue
        relative = fixture_file.relative_to(fixture_dir)
        league_file = league_dir / relative
        if not league_file.exists():
            continue

        with open(fixture_file, encoding="utf-8") as f:
            fixture_data = json.load(f)
        with open(league_file, encoding="utf-8") as f:
            league_data = json.load(f)

        fixture_url = fixture_data.get("league_url", "")
        league_url = league_data.get("league_url", "")
        if not fixture_url or not league_url:
            continue
        if rfu_league_url_key(fixture_url) == rfu_league_url_key(league_url):
            continue

        issues.append(
            LeagueUrlIssue(
                kind="fixture_drift",
                league_name=league_data.get("league_name", fixture_file.stem),
                relative_path=relative.as_posix(),
                stored_url=fixture_url,
                expected_url=league_url,
                detail=(
                    f"fixture_data division {rfu_league_url_key(fixture_url)[1]} "
                    f"!= league_data division {rfu_league_url_key(league_url)[1]}"
                ),
            )
        )

    return issues


def _print_issues(season: str, issues: list[LeagueUrlIssue]) -> None:
    if not issues:
        print(f"\n{season}: all checked league URLs match.")
        return

    by_kind: dict[str, list[LeagueUrlIssue]] = {}
    for issue in issues:
        by_kind.setdefault(issue.kind, []).append(issue)

    print(f"\n{season}: {len(issues)} issue(s)")
    labels = {
        "stale_url": "Stale league_data URL (RFU division changed)",
        "stale_url_keep_fixtures": (
            "RFU division differs but stored URL kept (historical fixtures on disk)"
        ),
        "missing_on_rfu": "Stored league not found on RFU",
        "missing_in_data": "RFU league missing from league_data",
        "fixture_drift": "fixture_data URL differs from league_data",
    }
    for kind in (
        "stale_url",
        "stale_url_keep_fixtures",
        "fixture_drift",
        "missing_on_rfu",
        "missing_in_data",
    ):
        group = by_kind.get(kind)
        if not group:
            continue
        print(f"\n  {labels.get(kind, kind)} ({len(group)})")
        for issue in group:
            path = f"  [{issue.relative_path}]" if issue.relative_path else ""
            print(f"    - {issue.league_name}{path}")
            if issue.detail:
                print(f"      {issue.detail}")
            if issue.stored_url:
                print(f"      stored:   {issue.stored_url}")
            if issue.expected_url:
                print(f"      expected: {issue.expected_url}")


def validate_season(
    season: str,
    *,
    offline: bool,
    check_fixtures: bool,
) -> list[LeagueUrlIssue]:
    stored = load_stored_leagues(season)
    logger.info("Loaded %d stored RFU leagues for %s", len(stored), season)

    issues: list[LeagueUrlIssue] = []
    if not offline:
        logger.info("Scraping live RFU meta pages for %s (no meta cache)...", season)
        rfu_by_key = fetch_live_league_urls_by_name(season)
        logger.info("RFU returned %d (competition, name) pairs", len(rfu_by_key))
        issues.extend(compare_stored_to_rfu(stored, rfu_by_key, season))

    if check_fixtures:
        issues.extend(compare_fixture_to_league_data(season))

    _print_issues(season, issues)
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate league_data URLs against live RFU listings (no meta cache)."
    )
    parser.add_argument(
        "--season",
        type=str,
        default=CURRENT_SEASON,
        help=f"Season to validate (default: {CURRENT_SEASON})",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip RFU scrape; only compare fixture_data to league_data when --check-fixtures",
    )
    parser.add_argument(
        "--check-fixtures",
        action="store_true",
        help="Also compare fixture_data league_url values to league_data",
    )
    args = parser.parse_args()

    setup_logging()

    if args.offline and not args.check_fixtures:
        parser.error("--offline requires --check-fixtures")

    issues = validate_season(
        args.season,
        offline=args.offline,
        check_fixtures=args.check_fixtures,
    )
    actionable = [issue for issue in issues if issue.kind != "stale_url_keep_fixtures"]
    if actionable:
        sys.exit(1)


if __name__ == "__main__":
    main()
