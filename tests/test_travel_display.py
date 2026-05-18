"""Tests for travel distance/time display formatting."""

from core import TeamTravelDistances
from rugby.travel_display import (
    ISLAND_TRAVEL_EXCL_LABEL,
    ISLAND_TRAVEL_INCL_LABEL,
    _island_stat_group_html,
    format_team_travel_distance_km,
    format_team_travel_time_min,
    has_dual_island_stats,
    render_popup_travel_html,
)


def test_has_dual_island_stats_only_when_excl_present():
    td: TeamTravelDistances = {
        "name": "A",
        "league": "L",
        "avg_distance_km": 10.0,
        "total_distance_km": 100.0,
    }
    assert not has_dual_island_stats(td)
    td["excl_avg_distance_km"] = 8.0
    assert has_dual_island_stats(td)


def test_format_team_travel_single_value():
    td: TeamTravelDistances = {
        "name": "A",
        "league": "L",
        "avg_distance_km": 12.5,
        "total_distance_km": 137.0,
    }
    assert format_team_travel_distance_km(td) == "12.5 km / 137 km"


def test_format_team_travel_dual_values():
    td: TeamTravelDistances = {
        "name": "A",
        "league": "L",
        "avg_distance_km": 120.0,
        "total_distance_km": 1200.0,
        "excl_avg_distance_km": 45.0,
        "excl_total_distance_km": 450.0,
    }
    assert format_team_travel_distance_km(td) == (
        _island_stat_group_html(True, "120.0 km / 1200 km")
        + _island_stat_group_html(False, "45.0 km / 450 km")
    )


def test_format_team_travel_time_dual():
    td: TeamTravelDistances = {
        "name": "A",
        "league": "L",
        "avg_distance_km": 1.0,
        "total_distance_km": 10.0,
        "avg_duration_min": 95.0,
        "total_duration_min": 1030.0,
        "excl_avg_duration_min": 60.0,
        "excl_total_duration_min": 600.0,
        "excl_avg_distance_km": 40.0,
        "excl_total_distance_km": 400.0,
    }
    assert format_team_travel_time_min(td) == (
        _island_stat_group_html(True, "95 min / 1030 min")
        + _island_stat_group_html(False, "60 min / 600 min")
    )


def test_render_popup_dual_includes_both_sections():
    team: TeamTravelDistances = {
        "name": "Mainland RFC",
        "league": "Test League",
        "avg_distance_km": 100.0,
        "total_distance_km": 900.0,
        "avg_duration_min": 90.0,
        "total_duration_min": 810.0,
        "excl_avg_distance_km": 50.0,
        "excl_total_distance_km": 400.0,
        "excl_avg_duration_min": 45.0,
        "excl_total_duration_min": 360.0,
    }
    league = {
        "league_name": "Test League",
        "avg_distance_km": 80.0,
        "team_count": 10,
        "avg_duration_min": 70.0,
        "excl_avg_distance_km": 55.0,
        "excl_avg_duration_min": 48.0,
    }
    html = render_popup_travel_html(team, league, distance_source="routed")
    assert ISLAND_TRAVEL_INCL_LABEL in html
    assert ISLAND_TRAVEL_EXCL_LABEL in html
    assert "island-stat-group--spaced" in html
    assert "island-travel-note" in html
    assert "100.00 km" in html
    assert "50.00 km" in html
