"""Tests for geocode_addresses outlier-only recalc mode."""

import json

from football.geocode_addresses import recalc_outliers_in_geocoded_dir


def test_recalc_outliers_only_updates_changed_leagues(tmp_path, monkeypatch) -> None:
    season = "2025-2026"
    subdir = "feeder"
    geo_dir = tmp_path / "geocoded_teams" / season / subdir
    geo_dir.mkdir(parents=True)

    league = {
        "league_name": "Test League",
        "teams": [
            {"name": "Good", "latitude": 52.0, "longitude": 1.0, "geocode_source": "wikidata"},
            {
                "name": "Bad",
                "latitude": 51.0,
                "longitude": -2.0,
                "geocode_source": "nominatim_club",
                "centroid_distance_km": 200.0,
                "centroid_distance_z": 10.0,
            },
        ],
    }
    geo_file = geo_dir / "Test_League.json"
    geo_file.write_text(json.dumps(league), encoding="utf-8")

    calls: list[str] = []

    def fake_recalculate(league_data, wikidata_coords, *, use_pyramid=True):
        calls.append(league_data["league_name"])
        league_data["teams"][1]["latitude"] = 52.1
        league_data["teams"][1]["longitude"] = 1.1
        league_data["teams"][1]["geocode_source"] = "nominatim_club"
        league_data["teams"][1].pop("centroid_distance_z", None)
        league_data["teams"][1].pop("centroid_distance_km", None)
        return 1

    monkeypatch.setattr("football.geocode_addresses.DATA_DIR", tmp_path)
    monkeypatch.setattr(
        "football.geocode_addresses.recalculate_wrong_locations",
        fake_recalculate,
    )
    monkeypatch.setattr("football.geocode_addresses.load_wikidata_coords", lambda **_k: {})
    monkeypatch.setattr("football.geocode_addresses.flush_cache", lambda **_k: None)
    monkeypatch.setattr("football.geocode_addresses.load_cache", lambda: None)

    replaced, leagues, remaining = recalc_outliers_in_geocoded_dir(season, subdir)

    assert calls == ["Test League"]
    assert replaced == 1
    assert leagues == 1
    updated = json.loads(geo_file.read_text(encoding="utf-8"))
    assert updated["teams"][1]["latitude"] == 52.1


def test_recalc_outliers_only_skips_unchanged_leagues(tmp_path, monkeypatch) -> None:
    season = "2025-2026"
    subdir = "feeder"
    geo_dir = tmp_path / "geocoded_teams" / season / subdir
    geo_dir.mkdir(parents=True)

    league = {
        "league_name": "Clean League",
        "teams": [
            {"name": "Good", "latitude": 52.0, "longitude": 1.0, "geocode_source": "wikidata"}
        ],
    }
    geo_file = geo_dir / "Clean_League.json"
    original = json.dumps(league)
    geo_file.write_text(original, encoding="utf-8")

    monkeypatch.setattr("football.geocode_addresses.DATA_DIR", tmp_path)
    monkeypatch.setattr(
        "football.geocode_addresses.recalculate_wrong_locations",
        lambda *_a, **_k: 0,
    )
    monkeypatch.setattr("football.geocode_addresses.load_wikidata_coords", lambda **_k: {})
    monkeypatch.setattr("football.geocode_addresses.flush_cache", lambda **_k: None)
    monkeypatch.setattr("football.geocode_addresses.load_cache", lambda: None)

    replaced, leagues, _remaining = recalc_outliers_in_geocoded_dir(season, subdir)

    assert replaced == 0
    assert leagues == 0
    assert geo_file.read_text(encoding="utf-8") == original
