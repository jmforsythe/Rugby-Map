"""Tests for enriching stale distance caches with island excl stats."""

import json

from rugby.distances import enrich_island_excl_stats


def test_enrich_adds_excl_fields_to_stale_cache(tmp_path, monkeypatch):
    season = "2099-2100"
    geocoded_dir = tmp_path / "geocoded_teams" / season
    geocoded_dir.mkdir(parents=True)
    league_file = geocoded_dir / "Test_League.json"
    league_file.write_text(
        json.dumps(
            {
                "league_name": "Test League",
                "league_url": "",
                "teams": [
                    {
                        "name": "Mainland RFC",
                        "url": "",
                        "image_url": None,
                        "address": "",
                        "latitude": 53.48,
                        "longitude": -2.24,
                        "formatted_address": "",
                        "place_id": "",
                    },
                    {
                        "name": "Douglas RFC",
                        "url": "",
                        "image_url": None,
                        "address": "",
                        "latitude": 54.15,
                        "longitude": -4.48,
                        "formatted_address": "",
                        "place_id": "",
                    },
                ],
                "team_count": 2,
            }
        ),
        encoding="utf-8",
    )

    stale: dict = {
        "teams": {
            "Mainland RFC": {
                "name": "Mainland RFC",
                "league": "Test League",
                "avg_distance_km": 100.0,
                "total_distance_km": 100.0,
            }
        },
        "leagues": {
            "Test League": {
                "league_name": "Test League",
                "avg_distance_km": 100.0,
                "team_count": 2,
            }
        },
        "summary": {},
    }

    monkeypatch.setattr("rugby.distances.DATA_DIR", tmp_path)
    enriched = enrich_island_excl_stats(stale, season)  # type: ignore[arg-type]

    mainland = enriched["teams"]["Mainland RFC"]
    assert "excl_avg_distance_km" in mainland
    assert mainland["excl_avg_distance_km"] < mainland["avg_distance_km"]
    assert "excl_avg_distance_km" in enriched["leagues"]["Test League"]
