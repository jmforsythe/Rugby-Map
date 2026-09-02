"""Tests for team pages logic."""

from core import TeamTravelDistances
from rugby.team_pages import (
    TeamData,
    TeamFixtureEntry,
    _format_fixture_date,
    _format_fixture_result,
    _render_fixtures_section,
    build_club_index,
    build_id_to_page_key,
    collect_team_fixtures,
    get_team_page_html,
)
from rugby.travel_display import format_team_travel_distance_km, format_team_travel_time_min


def _minimal_team_data(**overrides) -> TeamData:
    team_data: TeamData = {
        "name": "Barnes",
        "url": None,
        "image_url": None,
        "address": None,
        "latitude": None,
        "longitude": None,
        "formatted_address": None,
        "constituent_body": None,
        "league_history": [],
        "team_ids": set(),
        "name_seasons": {},
    }
    team_data.update(overrides)
    return team_data


class TestFormatTravelCells:
    def test_km_both_parts(self):
        td: TeamTravelDistances = {
            "name": "X",
            "league": "L",
            "avg_distance_km": 12.5,
            "total_distance_km": 137.2,
        }
        assert format_team_travel_distance_km(td) == "12.5 km / 137 km"

    def test_time_missing_shows_em_dash(self):
        td: TeamTravelDistances = {
            "name": "X",
            "league": "L",
            "avg_distance_km": 10.0,
            "total_distance_km": 100.0,
        }
        assert format_team_travel_time_min(td) == "—"

    def test_time_both_parts(self):
        td: TeamTravelDistances = {
            "name": "X",
            "league": "L",
            "avg_distance_km": 1.0,
            "total_distance_km": 10.0,
            "avg_duration_min": 95.25,
            "total_duration_min": 1029.75,
        }
        assert format_team_travel_time_min(td) == "95 min / 1030 min"


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
        assert index["Team A"] == []
        assert index["Team B"] == []

    def test_same_coords_different_canonical_clubs_not_merged(self, monkeypatch):
        club_names = {
            "East London": "East London RFC",
            "Kings Cross Steelers": "Kings Cross Steelers RFC",
        }
        monkeypatch.setattr(
            "rugby.team_pages.load_team_club_map",
            lambda: club_names,
        )
        lat, lon = 51.528531, 0.008158
        teams = {
            "East London": {
                "name": "East London",
                "address": "71 Holland Road, West Ham, London, E15 3BP, United Kingdom",
                "latitude": lat,
                "longitude": lon,
                "league_history": [],
            },
            "Kings Cross Steelers": {
                "name": "Kings Cross Steelers",
                "address": "East London Rugby Club, 71 Holland Road, London, E15 3BP, United Kingdom",
                "latitude": lat,
                "longitude": lon,
                "league_history": [],
            },
        }
        index = build_club_index(teams)
        assert index["East London"] == []
        assert index["Kings Cross Steelers"] == []

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


class TestFixtureHelpers:
    def test_format_fixture_date(self):
        assert _format_fixture_date("2026-09-25") == "Fri 25 Sep 2026"

    def test_format_fixture_result_score_home(self):
        entry: TeamFixtureEntry = {
            "season": "2026-2027",
            "league_name": "Premiership",
            "date": "2026-09-25",
            "time": "19:45",
            "is_home": True,
            "opponent_id": 42,
            "match_url": "https://example.com/match",
            "home_score": 24,
            "away_score": 17,
        }
        assert _format_fixture_result(entry) == (
            '<span class="fixture-score">'
            '<span class="score-home own-score">24</span>'
            '<span class="score-sep">–</span>'
            '<span class="score-away">17</span>'
            '<span class="result-badge result-win">W</span>'
            "</span>"
        )

    def test_format_fixture_result_score_away(self):
        entry: TeamFixtureEntry = {
            "season": "2026-2027",
            "league_name": "Premiership",
            "date": "2026-09-25",
            "time": "19:45",
            "is_home": False,
            "opponent_id": 42,
            "match_url": "https://example.com/match",
            "home_score": 24,
            "away_score": 17,
        }
        assert _format_fixture_result(entry) == (
            '<span class="fixture-score">'
            '<span class="score-home">24</span>'
            '<span class="score-sep">–</span>'
            '<span class="score-away own-score">17</span>'
            '<span class="result-badge result-loss">L</span>'
            "</span>"
        )

    def test_format_fixture_result_draw(self):
        entry: TeamFixtureEntry = {
            "season": "2026-2027",
            "league_name": "Premiership",
            "date": "2026-09-25",
            "time": "19:45",
            "is_home": True,
            "opponent_id": 42,
            "match_url": "https://example.com/match",
            "home_score": 20,
            "away_score": 20,
        }
        assert _format_fixture_result(entry) == (
            '<span class="fixture-score">'
            '<span class="score-home own-score">20</span>'
            '<span class="score-sep">–</span>'
            '<span class="score-away">20</span>'
            '<span class="result-badge result-draw">D</span>'
            "</span>"
        )

    def test_format_fixture_result_kickoff(self):
        entry: TeamFixtureEntry = {
            "season": "2026-2027",
            "league_name": "Premiership",
            "date": "2026-09-25",
            "time": "15:00",
            "is_home": True,
            "opponent_id": 42,
            "match_url": "https://example.com/match",
        }
        assert _format_fixture_result(entry) == "15:00"


class TestCollectTeamFixtures:
    def test_groups_by_page_key_and_dedupes(self, tmp_path, monkeypatch):
        import rugby.team_pages as tp

        fixture_dir = tmp_path / "fixture_data" / "2026-2027"
        fixture_dir.mkdir(parents=True)
        fixture_dir.joinpath("Test_League.json").write_text(
            """{
  "league_name": "Test League",
  "league_url": "https://example.com/league",
  "fixtures": [
    {
      "date": "2026-09-25",
      "time": "15:00",
      "home_team_id": 10,
      "away_team_id": 20,
      "match_url": "https://example.com/match/1"
    }
  ]
}""",
            encoding="utf-8",
        )
        monkeypatch.setattr(tp, "DATA_DIR", tmp_path)

        all_teams = {
            "home-team": {
                "name": "Home Team",
                "team_ids": {10},
                "league_history": [],
                "name_seasons": {},
            },
            "away-team": {
                "name": "Away Team",
                "team_ids": {20},
                "league_history": [],
                "name_seasons": {},
            },
        }
        id_lookup = build_id_to_page_key(all_teams)
        fixtures = collect_team_fixtures(id_lookup)

        assert len(fixtures["home-team"]) == 1
        assert fixtures["home-team"][0]["is_home"] is True
        assert fixtures["home-team"][0]["opponent_id"] == 20
        assert len(fixtures["away-team"]) == 1
        assert fixtures["away-team"][0]["is_home"] is False


class TestRenderFixturesSection:
    def test_renders_collapsible_per_season(self):
        fixtures: list[TeamFixtureEntry] = [
            {
                "season": "2026-2027",
                "league_name": "Premiership",
                "date": "2026-09-25",
                "time": "15:00",
                "is_home": True,
                "opponent_id": 42,
                "match_url": "https://example.com/a",
            },
            {
                "season": "2025-2026",
                "league_name": "National League 1",
                "date": "2025-09-20",
                "time": "14:30",
                "is_home": False,
                "opponent_id": 43,
                "match_url": "https://example.com/b",
            },
        ]
        html = _render_fixtures_section(fixtures, {}, {}, {42: "Opp A", 43: "Opp B"}, set())

        assert 'class="fixtures-season" open' in html
        assert "2026-2027 (1 fixture)" in html
        assert "2025-2026 (1 fixture)" in html
        assert html.index("2026-2027") < html.index("2025-2026")
        assert html.count('<details class="fixtures-season"') == 2
        assert "Fixtures & Results" in html


class TestGetTeamPageHtml:
    def test_renders_constituent_body_row_when_known(self):
        team_data = _minimal_team_data(constituent_body="Surrey Rugby")
        html = get_team_page_html(
            "Barnes",
            team_data,
            {"Barnes": team_data},
            club_index={},
            travel_distances_by_season={},
            all_seasons=[],
            ambiguous_display_names=set(),
            team_fixtures=[],
            id_to_page_key={},
            team_id_names={},
        )
        assert "Constituent Body:" in html
        assert "Surrey Rugby" in html

    def test_omits_constituent_body_row_when_unknown(self):
        team_data = _minimal_team_data(constituent_body=None)
        html = get_team_page_html(
            "Barnes",
            team_data,
            {"Barnes": team_data},
            club_index={},
            travel_distances_by_season={},
            all_seasons=[],
            ambiguous_display_names=set(),
            team_fixtures=[],
            id_to_page_key={},
            team_id_names={},
        )
        assert "Constituent Body:" not in html
