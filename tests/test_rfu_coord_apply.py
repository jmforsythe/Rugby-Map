"""Tests for RFU club coord cache application."""

from rugby.sync_rfu_coordinates import apply_rfu_coords_from_cache, rfu_coords_for_team


def test_rfu_coords_for_team_returns_cached_pin() -> None:
    team = {
        "name": "Southmead",
        "url": "https://www.englandrugby.com/fixtures-and-results/search-results?team=19760",
    }
    cache = {"Southmead": [51.500065, -2.607953]}
    assert rfu_coords_for_team(team, cache) == (51.500065, -2.607953)


def test_rfu_coords_for_team_skips_non_rfu_url() -> None:
    team = {"name": "Southmead", "url": "https://example.com/team"}
    cache = {"Southmead": [51.500065, -2.607953]}
    assert rfu_coords_for_team(team, cache) is None


def test_apply_rfu_coords_from_cache_overrides_nominatim() -> None:
    team = {
        "name": "Southmead",
        "url": "https://www.englandrugby.com/fixtures-and-results/search-results?team=19760",
        "latitude": 51.499895,
        "longitude": -2.6078965,
        "error": "geocoding_failed",
    }
    cache = {"Southmead": [51.500065, -2.607953]}
    assert apply_rfu_coords_from_cache(team, cache) is True
    assert team["latitude"] == 51.500065
    assert team["longitude"] == -2.607953


def test_apply_rfu_coords_from_cache_noop_when_missing() -> None:
    team = {
        "name": "Unknown Club",
        "url": "https://www.englandrugby.com/fixtures-and-results/search-results?team=1",
        "latitude": 51.5,
        "longitude": -2.6,
    }
    assert apply_rfu_coords_from_cache(team, {}) is False
    assert team["latitude"] == 51.5
