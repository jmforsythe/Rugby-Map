"""Tests for football map item loading."""

import json

from football.map_common import load_pyramid_items


def test_load_pyramid_items_skips_definitely_wrong_locations(tmp_path) -> None:
    league = {
        "league_name": "Test League",
        "league_url": "",
        "teams": [
            {"name": "Good", "latitude": 52.0, "longitude": 0.1, "level": 11},
            {
                "name": "Misplaced",
                "latitude": 50.1,
                "longitude": -5.5,
                "level": 11,
                "geocode_source": "nominatim_name",
                "centroid_distance_km": 300.0,
                "centroid_distance_z": 25.0,
            },
        ],
    }
    (tmp_path / "Test_League.json").write_text(json.dumps(league), encoding="utf-8")

    items = load_pyramid_items(tmp_path)

    assert [item.name for item in items] == ["Good"]


def test_load_pyramid_items_keeps_ground_sourced_outliers(tmp_path) -> None:
    league = {
        "league_name": "National League South",
        "league_url": "",
        "teams": [
            {
                "name": "Torquay United",
                "latitude": 50.476,
                "longitude": -3.526,
                "level": 6,
                "geocode_source": "wikipedia",
                "centroid_distance_km": 225.0,
                "centroid_distance_z": 12.0,
            },
        ],
    }
    (tmp_path / "National_League_South.json").write_text(json.dumps(league), encoding="utf-8")

    items = load_pyramid_items(tmp_path)

    assert [item.name for item in items] == ["Torquay United"]
