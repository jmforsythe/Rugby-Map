"""Tests for enriching stale distance caches with island excl stats."""

import json

from rugby.distances import enrich_island_excl_stats


def test_enrich_adds_excl_fields_to_stale_cache(tmp_path, monkeypatch):
    season = "2099-2100"
    league_dir = tmp_path / "league_data" / season
    league_dir.mkdir(parents=True)
    league_file = league_dir / "Test_League.json"
    league_file.write_text(
        json.dumps(
            {
                "league_name": "Test League",
                "league_url": "",
                "teams": [
                    {"name": "Mainland RFC", "url": "", "image_url": None},
                    {"name": "Douglas RFC", "url": "", "image_url": None},
                ],
                "team_count": 2,
            }
        ),
        encoding="utf-8",
    )

    (tmp_path / "club_names.json").write_text("{}", encoding="utf-8")
    (tmp_path / "club_addresses.json").write_text(
        json.dumps({"Mainland RFC": "", "Douglas RFC": ""}), encoding="utf-8"
    )
    (tmp_path / "club_geocodes.json").write_text(
        json.dumps(
            {
                "Mainland RFC": {
                    "latitude": 53.48,
                    "longitude": -2.24,
                    "formatted_address": "",
                    "place_id": "",
                },
                "Douglas RFC": {
                    "latitude": 54.15,
                    "longitude": -4.48,
                    "formatted_address": "",
                    "place_id": "",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("rugby.clubs.CLUB_NAMES_FILE", tmp_path / "club_names.json")
    monkeypatch.setattr("rugby.clubs.CLUB_ADDRESSES_FILE", tmp_path / "club_addresses.json")
    monkeypatch.setattr("rugby.clubs.CLUB_GEOCODES_FILE", tmp_path / "club_geocodes.json")
    monkeypatch.setattr("rugby.clubs.RFU_COORD_CACHE_FILE", tmp_path / "no_rfu_cache.json")

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
