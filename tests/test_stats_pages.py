"""Tests for the stats dashboard page generation."""

from __future__ import annotations

import json
from pathlib import Path

from rugby.stats_pages import (
    ALL_COMPETITIONS_KEY,
    GENDER_ALL,
    GENDER_MEN,
    GENDER_WOMEN,
    PYRAMID_KEY,
    compute_competition_breakdown,
    compute_season_stats,
)


def _write_league(path: Path, teams: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "league_name": "Test League",
                "league_url": "https://example.com/",
                "teams": [{"name": name, "url": None} for name in teams],
            }
        ),
        encoding="utf-8",
    )


def test_compute_season_stats_counts_teams_and_dedupes_clubs(tmp_path: Path) -> None:
    league_data_dir = tmp_path / "league_data"

    _write_league(
        league_data_dir / "2024-2025" / "Tier1.json",
        ["Alpha RFC", "Alpha RFC II", "Beta RFC"],
    )
    _write_league(
        league_data_dir / "2025-2026" / "Tier1.json",
        ["Alpha RFC", "Beta RFC", "Gamma RFC"],
    )

    stats = compute_season_stats(league_data_dir)

    assert [s["season"] for s in stats] == ["2024-2025", "2025-2026"]
    # 2024-2025: Alpha RFC + Alpha RFC II + Beta RFC = 3 teams, 2 clubs (II collapses).
    assert stats[0]["teams"] == 3
    assert stats[0]["clubs"] == 2
    # 2025-2026: 3 distinct teams, 3 distinct clubs.
    assert stats[1]["teams"] == 3
    assert stats[1]["clubs"] == 3


def test_compute_season_stats_empty_dir_returns_empty_list(tmp_path: Path) -> None:
    assert compute_season_stats(tmp_path / "missing") == []


def test_compute_competition_breakdown_splits_pyramid_from_merit(tmp_path: Path) -> None:
    league_data_dir = tmp_path / "league_data"

    _write_league(league_data_dir / "2025-2026" / "Tier1.json", ["Alpha RFC", "Beta RFC"])
    _write_league(
        league_data_dir / "2025-2026" / "merit" / "East_Midlands" / "Premier.json",
        ["Alpha RFC II", "Gamma RFC"],
    )

    breakdown = compute_competition_breakdown(league_data_dir)
    by_key = {c["key"]: c for c in breakdown["competitions"]}

    assert breakdown["seasons"] == ["2025-2026"]
    assert set(by_key) == {PYRAMID_KEY, ALL_COMPETITIONS_KEY, "East_Midlands"}
    assert by_key["East_Midlands"]["label"] == "East Midlands"

    # Pyramid-only: just the two non-merit teams/clubs (all genders).
    assert by_key[PYRAMID_KEY]["teams"][GENDER_ALL] == [2]
    assert by_key[PYRAMID_KEY]["clubs"][GENDER_ALL] == [2]
    # Merit-only competition: Alpha RFC II collapses to the Alpha RFC club.
    assert by_key["East_Midlands"]["teams"][GENDER_ALL] == [2]
    assert by_key["East_Midlands"]["clubs"][GENDER_ALL] == [2]
    # Combined: 4 distinct teams, 3 distinct clubs (Alpha shared across both).
    assert by_key[ALL_COMPETITIONS_KEY]["teams"][GENDER_ALL] == [4]
    assert by_key[ALL_COMPETITIONS_KEY]["clubs"][GENDER_ALL] == [3]


def test_compute_competition_breakdown_splits_by_gender(tmp_path: Path) -> None:
    league_data_dir = tmp_path / "league_data"

    _write_league(league_data_dir / "2025-2026" / "Tier1.json", ["Alpha RFC", "Beta RFC"])
    _write_league(
        league_data_dir / "2025-2026" / "Women's_Premiership.json",
        ["Alpha RFC Women", "Gamma RFC Women"],
    )

    breakdown = compute_competition_breakdown(league_data_dir)
    pyramid = next(c for c in breakdown["competitions"] if c["key"] == PYRAMID_KEY)

    assert pyramid["teams"][GENDER_MEN] == [2]
    assert pyramid["teams"][GENDER_WOMEN] == [2]
    assert pyramid["teams"][GENDER_ALL] == [4]
    assert pyramid["clubs"][GENDER_MEN] == [2]
    assert pyramid["clubs"][GENDER_WOMEN] == [2]
    assert pyramid["clubs"][GENDER_ALL] == [4]


def test_compute_competition_breakdown_empty_dir(tmp_path: Path) -> None:
    breakdown = compute_competition_breakdown(tmp_path / "missing")
    assert breakdown == {"seasons": [], "competitions": []}
