"""Tests for RFU league URL normalization and comparison."""

from __future__ import annotations

import json
from pathlib import Path

from rugby.fixtures import _discover_leagues
from rugby.scrape import normalize_rfu_league_url, rfu_league_url_key


def test_rfu_league_url_key_ignores_fragment() -> None:
    old = (
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=104&season=2026-2027&division=77284#tables"
    )
    new = (
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=104&season=2026-2027&division=79185#fixtures"
    )
    assert rfu_league_url_key(old) == ("104", "77284", "2026-2027")
    assert rfu_league_url_key(new) == ("104", "79185", "2026-2027")
    assert rfu_league_url_key(old) != rfu_league_url_key(new)


def test_normalize_rfu_league_url_adds_season_and_tables_fragment() -> None:
    url = (
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=104&division=79185"
    )
    normalized = normalize_rfu_league_url(url, "2026-2027")
    assert normalized.endswith("#tables")
    assert rfu_league_url_key(normalized) == ("104", "79185", "2026-2027")


def test_discover_leagues_prefers_meta_cache_when_division_differs(tmp_path: Path) -> None:
    season = "2026-2027"
    league_dir = tmp_path / "league_data" / season / "merit" / "Eastern_Counties"
    league_dir.mkdir(parents=True)
    stale_url = (
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=104&season=2026-2027&division=77284#tables"
    )
    (league_dir / "Eastern_Counties_Division_One_North.json").write_text(
        json.dumps(
            {
                "league_name": "Eastern Counties Division One North",
                "league_url": stale_url,
                "teams": [],
                "team_count": 0,
            }
        ),
        encoding="utf-8",
    )
    meta_url = (
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=104&season=2026-2027&division=79185"
    )
    (tmp_path / "league_data" / season / "_meta_leagues_cache.json").write_text(
        json.dumps(
            {
                "https://example.com/meta": [
                    {
                        "name": "Eastern Counties Division One North",
                        "url": meta_url,
                        "parent_url": "https://example.com/meta",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    import rugby.fixtures as fixtures_mod

    original_data_dir = fixtures_mod.DATA_DIR
    try:
        fixtures_mod.DATA_DIR = tmp_path
        discovered = _discover_leagues(season)
    finally:
        fixtures_mod.DATA_DIR = original_data_dir

    assert len(discovered) == 1
    _, league_url, _ = discovered[0]
    assert rfu_league_url_key(league_url) == ("104", "79185", "2026-2027")
