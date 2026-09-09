"""Tests for league URL validation helpers."""

from __future__ import annotations

import json
from pathlib import Path

from rugby.analysis.validate_league_urls import (
    StoredLeague,
    compare_fixture_to_league_data,
    compare_stored_to_rfu,
    load_stored_leagues,
)
from rugby.scrape import normalize_rfu_league_url, rfu_league_lookup_key


def test_compare_stored_to_rfu_detects_stale_division() -> None:
    season = "2026-2027"
    stored_url = (
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=104&season=2026-2027&division=77284#tables"
    )
    stored = [
        StoredLeague(
            league_name="Eastern Counties Division One North",
            league_url=stored_url,
            relative_path="merit/Eastern_Counties/Eastern_Counties_Division_One_North.json",
            source="league_data",
        )
    ]
    expected_url = normalize_rfu_league_url(
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=104&season=2026-2027&division=79185",
        season,
    )
    rfu_by_key = {
        rfu_league_lookup_key(stored_url, "Eastern Counties Division One North"): expected_url
    }

    issues = compare_stored_to_rfu(stored, rfu_by_key, season)

    assert len(issues) == 1
    assert issues[0].kind == "stale_url"
    assert "77284" in issues[0].detail
    assert "79185" in issues[0].detail


def test_compare_stored_to_rfu_ok_when_keys_match() -> None:
    season = "2026-2027"
    url = normalize_rfu_league_url(
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=104&season=2026-2027&division=79185#fixtures",
        season,
    )
    stored = [
        StoredLeague(
            league_name="Eastern Counties Division One North",
            league_url=url,
            relative_path="merit/Eastern_Counties/Eastern_Counties_Division_One_North.json",
            source="league_data",
        )
    ]
    issues = compare_stored_to_rfu(
        stored,
        {rfu_league_lookup_key(url, "Eastern Counties Division One North"): url},
        season,
    )
    assert issues == []


def test_compare_stored_to_rfu_disambiguates_generic_merit_names() -> None:
    season = "2026-2027"
    essex_url = normalize_rfu_league_url(
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=1694&season=2026-2027&division=77125#tables",
        season,
    )
    middlesex_url = normalize_rfu_league_url(
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=1596&season=2026-2027&division=77122#tables",
        season,
    )
    stored = [
        StoredLeague(
            league_name="Division 2",
            league_url=essex_url,
            relative_path="merit/Essex/Division_2.json",
            source="league_data",
        ),
        StoredLeague(
            league_name="Division 2",
            league_url=middlesex_url,
            relative_path="merit/Middlesex/Division_2.json",
            source="league_data",
        ),
    ]
    rfu_by_key = {
        rfu_league_lookup_key(essex_url, "Division 2"): essex_url,
        rfu_league_lookup_key(middlesex_url, "Division 2"): middlesex_url,
    }

    issues = compare_stored_to_rfu(stored, rfu_by_key, season)

    assert issues == []


def test_load_stored_leagues_reads_league_data(tmp_path: Path, monkeypatch) -> None:
    import rugby.analysis.validate_league_urls as mod

    season = "2026-2027"
    league_dir = tmp_path / "league_data" / season
    league_dir.mkdir(parents=True)
    (league_dir / "Counties_1_Cumbria.json").write_text(
        json.dumps(
            {
                "league_name": "Counties 1 Cumbria",
                "league_url": (
                    "https://www.englandrugby.com/fixtures-and-results/search-results"
                    "?competition=1623&division=56766&season=2026-2027#tables"
                ),
                "teams": [],
                "team_count": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "LEAGUE_DATA_DIR", tmp_path / "league_data")

    stored = load_stored_leagues(season)

    assert len(stored) == 1
    assert stored[0].league_name == "Counties 1 Cumbria"
    assert stored[0].source == "league_data"


def test_compare_fixture_to_league_data_detects_drift(tmp_path: Path, monkeypatch) -> None:
    import rugby.analysis.validate_league_urls as mod

    season = "2024-2025"
    league_dir = tmp_path / "league_data" / season
    fixture_dir = tmp_path / "fixture_data" / season
    league_dir.mkdir(parents=True)
    fixture_dir.mkdir(parents=True)
    rel = "Counties_1_Cumbria.json"
    league_url = (
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=1623&division=99999&season=2024-2025#tables"
    )
    fixture_url = (
        "https://www.englandrugby.com/fixtures-and-results/search-results"
        "?competition=1623&division=56766&season=2024-2025#tables"
    )
    payload = {
        "league_name": "Counties 1 Cumbria",
        "league_url": league_url,
    }
    (league_dir / rel).write_text(json.dumps(payload), encoding="utf-8")
    (fixture_dir / rel).write_text(
        json.dumps({**payload, "league_url": fixture_url, "fixtures": []}),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "LEAGUE_DATA_DIR", tmp_path / "league_data")
    monkeypatch.setattr(mod, "FIXTURE_DATA_DIR", tmp_path / "fixture_data")

    issues = compare_fixture_to_league_data(season)

    assert len(issues) == 1
    assert issues[0].kind == "fixture_drift"
    assert issues[0].stored_url == fixture_url
    assert issues[0].expected_url == league_url
