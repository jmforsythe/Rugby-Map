"""Tests for rugby.fixtures league discovery."""

from __future__ import annotations

import json
from pathlib import Path

from rugby.fixtures import _discover_fixture_only_leagues


def test_fixture_only_merit_leagues_use_competition_subdir(tmp_path: Path) -> None:
    sidecar = [
        {
            "name": "Premiership South East",
            "url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=202&season=2026-2027&division=71150#tables",
            "parent_url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=202&season=2026-2027",
        },
        {
            "name": "Table 1",
            "url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=209&season=2026-2027&division=68220#tables",
            "parent_url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=209&season=2026-2027",
        },
    ]
    league_dir = tmp_path / "2026-2027"
    league_dir.mkdir()
    (league_dir / "_fixture_only_leagues.json").write_text(json.dumps(sidecar), encoding="utf-8")

    discovered = _discover_fixture_only_leagues(league_dir)

    assert discovered == [
        (
            "Premiership South East",
            sidecar[0]["url"],
            Path("merit/Hampshire/Premiership_South_East.json"),
        ),
        (
            "Table 1",
            sidecar[1]["url"],
            Path("merit/Herts_Middlesex/Table_1.json"),
        ),
    ]
