"""Tests for team pages logic."""

from rugby.team_pages import build_club_index


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
