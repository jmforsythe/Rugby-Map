"""Tests for latest-season custom map import generation."""

import json
from pathlib import Path

from rugby.custom_map_season_imports import build_season_imports


def _write_league(path: Path, league_name: str, teams: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"league_name": league_name, "teams": [{"name": n} for n in teams]}),
        encoding="utf-8",
    )


def test_build_season_imports_pyramid_only(tmp_path, monkeypatch) -> None:
    geocoded = tmp_path / "geocoded_teams"
    season = geocoded / "2025-2026"
    _write_league(
        season / "Regional_1_South_East.json",
        "Regional 1 South East",
        ["HUEL Tring", "Colchester"],
    )
    _write_league(
        geocoded / "2020-2021" / "National_League_3_London_&_SE.json",
        "National League 3 London & SE",
        ["Jersey"],
    )

    monkeypatch.setattr("rugby.custom_map_season_imports.GEOCODED_DIR", geocoded)

    payload = build_season_imports("2025-2026")
    assert payload["season"] == "2025-2026"
    assert len(payload["pyramid"]) == 1
    tier = payload["pyramid"][0]
    assert tier["num"] == 5
    assert tier["leagues"][0]["name"] == "Regional 1 South East"
    assert tier["leagues"][0]["teams"] == ["Colchester", "HUEL Tring"]
    assert not any(lg["name"] == "National League 3 London & SE" for lg in tier["leagues"])


def test_counties_1_southern_north_matches_pyramid_map_grey(monkeypatch) -> None:
    from pathlib import Path

    from rugby.custom_map_season_imports import build_season_imports

    geocoded = Path(__file__).resolve().parents[1] / "data" / "rugby" / "geocoded_teams"
    if not (geocoded / "2025-2026").is_dir():
        return

    monkeypatch.setattr("rugby.custom_map_season_imports.GEOCODED_DIR", geocoded)
    tier = next(t for t in build_season_imports("2025-2026")["pyramid"] if t["num"] == 7)
    southern = next(lg for lg in tier["leagues"] if "Southern North" in lg["name"])
    assert southern["color"] == "#808080"
