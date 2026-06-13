"""Tests for football league location outlier detection."""

from football.location_sanity import (
    flag_league_location_outliers,
    is_definitely_wrong_location,
    is_offshore_team,
    league_centroid,
    pick_best_league_geocode,
)


def _team(name: str, lat: float, lon: float) -> dict:
    return {"name": name, "latitude": lat, "longitude": lon}


def test_league_centroid_ignores_failed_geocodes() -> None:
    teams = [
        _team("A", 52.0, -1.0),
        {"name": "B", "error": "geocoding_failed"},
        _team("C", 52.2, -0.8),
    ]
    assert league_centroid(teams) == (52.1, -0.9)


def test_pick_best_league_geocode_prefers_in_league_candidate() -> None:
    league = {
        "teams": [
            {"name": "A", "latitude": 52.5, "longitude": 1.2},
            {"name": "B", "latitude": 52.6, "longitude": 1.3},
            {"name": "C", "latitude": 52.4, "longitude": 1.1},
            {"name": "D", "latitude": 52.55, "longitude": 1.25},
            {"name": "Easton", "latitude": 51.47, "longitude": -2.69},
        ]
    }
    candidates = [
        {
            "latitude": 51.4780548,
            "longitude": -2.6995546,
            "formatted_address": "Somerset club",
            "place_id": "somerset",
        },
        {
            "latitude": 52.6533294,
            "longitude": 1.1573613,
            "formatted_address": "Norfolk Easton",
            "place_id": "norfolk",
        },
    ]
    best = pick_best_league_geocode(candidates, league, team_index=4)
    assert best is not None
    assert best["place_id"] == "norfolk"


def test_flag_league_location_outliers_skips_small_leagues() -> None:
    league = {
        "league_name": "Tiny",
        "teams": [_team("A", 52.0, -1.0), _team("B", 52.1, -0.9)],
    }
    assert flag_league_location_outliers(league, min_teams=4) == []
    assert "location_outlier" not in league["teams"][0]


def test_flag_league_location_outliers_flags_distant_team() -> None:
    league = {
        "league_name": "Local",
        "teams": [
            _team("Near A", 51.50, -0.10),
            _team("Near B", 51.51, -0.11),
            _team("Near C", 51.49, -0.09),
            _team("Near D", 51.52, -0.12),
            _team("Wrong place", 53.50, -2.50),
        ],
    }
    outliers = flag_league_location_outliers(league, sigma=2.0, min_teams=4)
    assert [t["name"] for t in outliers] == ["Wrong place"]
    wrong = league["teams"][4]
    assert wrong["location_outlier"] is True
    assert wrong["centroid_distance_km"] > 100
    assert wrong["centroid_distance_z"] > 2
    assert league["location_sanity"]["outlier_count"] == 1


def test_flag_league_location_outliers_clears_previous_flags() -> None:
    league = {
        "league_name": "Local",
        "teams": [
            {"name": "A", "latitude": 51.5, "longitude": -0.1, "location_outlier": True},
            _team("B", 51.51, -0.11),
            _team("C", 51.49, -0.09),
            _team("D", 51.52, -0.12),
        ],
    }
    flag_league_location_outliers(league, min_teams=4)
    assert "location_outlier" not in league["teams"][0]


def test_is_definitely_wrong_location_nominatim_name() -> None:
    team = {
        "name": "Shelley",
        "latitude": 53.5,
        "longitude": -1.8,
        "geocode_source": "nominatim_name",
        "centroid_distance_km": 215.0,
        "centroid_distance_z": 92.8,
    }
    assert is_definitely_wrong_location(team)


def test_is_definitely_wrong_location_ignores_offshore() -> None:
    team = {
        "name": "Guernsey",
        "territory": "Guernsey",
        "latitude": 49.45,
        "longitude": -2.54,
        "geocode_source": "wikipedia",
        "centroid_distance_km": 402.0,
        "centroid_distance_z": 30.0,
    }
    assert is_offshore_team(team)
    assert not is_definitely_wrong_location(team)


def test_is_definitely_wrong_location_wikipedia_needs_distance() -> None:
    team = {
        "name": "Calne Town",
        "latitude": 51.44,
        "longitude": -2.0,
        "geocode_source": "wikipedia",
        "centroid_distance_km": 32.8,
        "centroid_distance_z": 17.2,
    }
    assert not is_definitely_wrong_location(team)

    team["centroid_distance_km"] = 120.0
    assert is_definitely_wrong_location(team)
