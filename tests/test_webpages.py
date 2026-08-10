"""Tests for rugby season index page generation."""

from __future__ import annotations

from pathlib import Path

from rugby.webpages import _build_pyramid_section, detect_tier_files


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<html></html>", encoding="utf-8")


def test_merit_only_tiers_exclude_pyramid_and_combined_levels(tmp_path: Path) -> None:
    season = tmp_path / "2025-2026"
    season.mkdir()
    _touch(season / "Level_9.html")
    _touch(season / "Level_10.html")
    _touch(season / "Level_9_All_Leagues.html")
    _touch(season / "Level_10_All_Leagues.html")
    _touch(season / "Level_12_All_Leagues.html")
    _touch(season / "All_Leagues.html")

    tier_files = detect_tier_files(season)
    assert tier_files["tier_plus_merit"] == {
        "Level 9": "Level_9_All_Leagues.html",
        "Level 10": "Level_10_All_Leagues.html",
    }
    assert tier_files["merit_only_tiers"] == [("Level 12", "Level_12_All_Leagues.html")]


def test_pyramid_section_keeps_row_aligned_table_without_duplicate_merit() -> None:
    html = _build_pyramid_section(
        [
            ("Premiership", "Premiership.html"),
            ("Level 9", "Level_9.html"),
            ("Level 10", "Level_10.html"),
        ],
        all_tiers_href="All_Tiers.html",
        all_leagues_href="All_Leagues.html",
        tier_plus_merit={
            "Level 9": "Level_9_All_Leagues.html",
            "Level 10": "Level_10_All_Leagues.html",
        },
        merit_only_tiers=[
            ("Level 12", "Level_12_All_Leagues.html"),
            ("Level 13", "Level_13_All_Leagues.html"),
        ],
        season="2025-2026",
    )

    assert "tier-table--dual" in html
    assert "Level 9 + Merit" in html
    assert "Level 12 (Merit)" in html
    assert "Level 9 (Merit)" not in html
    assert "<td></td>" in html
