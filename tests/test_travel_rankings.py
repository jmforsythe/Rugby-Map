"""Tests for rugby.analysis.travel_rankings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import GeocodedLeague
from rugby.analysis.travel_rankings import (
    TeamRanking,
    _pair_km_min,
    _team_avg_km_min,
    build_rankings,
)
from rugby.distance_lookup import DistanceLookup


def _team(name: str, lat: float, lon: float) -> dict:
    return {"name": name, "latitude": lat, "longitude": lon}


def test_pair_km_min_estimates_when_no_routed_minutes() -> None:
    lookup = DistanceLookup()
    a = _team("A", 51.5, -0.1)
    b = _team("B", 51.6, -0.2)
    km, mins = _pair_km_min(a, b, lookup)
    assert km > 0
    assert mins > 0


def test_team_avg_km_min_single_opponent() -> None:
    lookup = DistanceLookup()
    teams = [_team("Near", 51.5, -0.1), _team("Far", 55.95, -3.19)]
    avg_km, avg_min = _team_avg_km_min(teams[0], teams, lookup)
    km, mins = _pair_km_min(teams[0], teams[1], lookup)
    assert avg_km == pytest.approx(km)
    assert avg_min == pytest.approx(mins)


def test_build_rankings_from_fixture_league(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    season = "2099-2100"
    geocoded_dir = tmp_path / "geocoded_teams" / season
    geocoded_dir.mkdir(parents=True)

    league: GeocodedLeague = {
        "league_name": "Test League",
        "league_url": "https://example.com",
        "team_count": 3,
        "teams": [
            _team("Alpha", 51.50, -0.10),
            _team("Bravo", 51.51, -0.11),
            _team("Charlie", 53.50, -2.20),
        ],
    }
    (geocoded_dir / "Test_League.json").write_text(json.dumps(league), encoding="utf-8")

    monkeypatch.setattr("rugby.analysis.travel_rankings.DATA_DIR", tmp_path)

    lookup = DistanceLookup()
    league_rows, team_rows, fixture_rows = build_rankings(season, lookup)

    assert len(league_rows) == 1
    assert league_rows[0].league == "Test League"
    assert len(team_rows) == 3
    assert len(fixture_rows) == 3

    nearest = min(fixture_rows, key=lambda r: r.min)
    farthest = max(fixture_rows, key=lambda r: r.min)
    assert {nearest.home, nearest.away} == {"Alpha", "Bravo"}
    assert "Charlie" in {farthest.home, farthest.away}

    assert min(team_rows, key=lambda r: r.avg_min).team in {"Alpha", "Bravo"}
    assert max(team_rows, key=lambda r: r.avg_min).team == "Charlie"


def test_most_travel_teams_exclude_offshore() -> None:
    mainland = TeamRanking(
        team="Cornish Pirates", league="Championship", avg_min=340.0, avg_km=480.0
    )
    island = TeamRanking(
        team="Guernsey", league="NL2 East", avg_min=241.0, avg_km=52.0, is_offshore=True
    )
    ranked = sorted([mainland, island], key=lambda r: (r.avg_min, r.avg_km))
    mainland_only = [r for r in ranked if not r.is_offshore]
    assert mainland_only[-1].team == "Cornish Pirates"
    assert all(r.team != "Guernsey" for r in mainland_only)
