"""Tests for team pages logic."""

from core import TeamTravelDistances
from rugby.team_pages import (
    _format_team_travel_distance_km,
    _format_team_travel_time_min,
    build_club_index,
)


class TestFormatTravelCells:
    def test_km_both_parts(self):
        td: TeamTravelDistances = {
            "name": "X",
            "league": "L",
            "avg_distance_km": 12.5,
            "total_distance_km": 137.2,
        }
        assert _format_team_travel_distance_km(td) == "12.5 km / 137 km"

    def test_time_missing_shows_em_dash(self):
        td: TeamTravelDistances = {
            "name": "X",
            "league": "L",
            "avg_distance_km": 10.0,
            "total_distance_km": 100.0,
        }
        assert _format_team_travel_time_min(td) == "—"

    def test_time_both_parts(self):
        td: TeamTravelDistances = {
            "name": "X",
            "league": "L",
            "avg_distance_km": 1.0,
            "total_distance_km": 10.0,
            "avg_duration_min": 95.25,
            "total_duration_min": 1029.75,
        }
        assert _format_team_travel_time_min(td) == "95 min / 1030 min"


class TestBuildClubIndex:
    """Tests for pre-building the club co-location index."""

    def test_same_address(self):
        teams = {
            "Team A": {
                "name": "Team A",
                "address": "123 Rugby Lane",
                "latitude": 51.5,
                "longitude": -0.1,
                "league_history": [],
            },
            "Team A II": {
                "name": "Team A II",
                "address": "123 Rugby Lane",
                "latitude": 51.5,
                "longitude": -0.1,
                "league_history": [],
            },
        }
        index = build_club_index(teams)
        assert index["Team A"] == ["Team A II"]
        assert index["Team A II"] == ["Team A"]

    def test_same_coords_different_address(self):
        teams = {
            "Team A": {
                "name": "Team A",
                "address": "123 Rugby Lane",
                "latitude": 51.5,
                "longitude": -0.1,
                "league_history": [],
            },
            "Team B": {
                "name": "Team B",
                "address": "456 Other St",
                "latitude": 51.5,
                "longitude": -0.1,
                "league_history": [],
            },
        }
        index = build_club_index(teams)
        assert index["Team A"] == ["Team B"]
        assert index["Team B"] == ["Team A"]

    def test_no_match(self):
        teams = {
            "Team A": {
                "name": "Team A",
                "address": "123 Rugby Lane",
                "latitude": 51.5,
                "longitude": -0.1,
                "league_history": [],
            },
            "Team B": {
                "name": "Team B",
                "address": "456 Other St",
                "latitude": 52.0,
                "longitude": -1.0,
                "league_history": [],
            },
        }
        index = build_club_index(teams)
        assert index["Team A"] == []
        assert index["Team B"] == []

    def test_empty_input(self):
        assert build_club_index({}) == {}
