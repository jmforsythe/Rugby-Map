"""Tests for football geocode recalculation after location sanity failures."""

from football.clubs_data import recalculate_pyramid_team, recalculate_wrong_locations


def test_recalculate_pyramid_team_skips_failed_nominatim_name(monkeypatch) -> None:
    team = {
        "name": "Arsenal",
        "wiki_title": "Arsenal_F.C.",
        "territory": "England",
        "latitude": 53.0,
        "longitude": -2.0,
        "geocode_source": "nominatim_name",
        "centroid_distance_km": 250.0,
        "centroid_distance_z": 20.0,
    }
    wikidata = {
        "Arsenal_F.C.": {
            "ground": "Emirates Stadium",
            "latitude": 51.555,
            "longitude": -0.108,
        }
    }

    def fail_nominatim(*_args, **_kwargs):
        raise AssertionError("nominatim should not be retried")

    monkeypatch.setattr("football.clubs_data.geocode_with_nominatim", fail_nominatim)
    monkeypatch.setattr("football.clubs_data.geocode_with_club_queries", lambda *_a, **_k: None)
    league = {"league_name": "Premier Division", "teams": [team]}
    result = recalculate_pyramid_team(
        team,
        wikidata,
        skip_source="nominatim_name",
        league=league,
        team_index=0,
    )
    assert result is not None
    assert result["geocode_source"] == "wikidata"
    assert result["latitude"] == 51.555


def test_recalculate_wrong_locations_replaces_definite_outlier(monkeypatch) -> None:
    league = {
        "league_name": "Cheshire League Premier Division",
        "teams": [
            {"name": "A", "latitude": 53.2, "longitude": -2.5},
            {"name": "B", "latitude": 53.3, "longitude": -2.4},
            {"name": "C", "latitude": 53.25, "longitude": -2.45},
            {"name": "D", "latitude": 53.28, "longitude": -2.42},
            {
                "name": "Bad Club",
                "wiki_title": "Bad_Club_F.C.",
                "territory": "England",
                "latitude": 51.5,
                "longitude": 0.1,
                "geocode_source": "nominatim_name",
            },
        ],
    }
    wikidata = {
        "Bad_Club_F.C.": {
            "ground": "Town Ground",
            "latitude": 53.27,
            "longitude": -2.44,
        }
    }

    monkeypatch.setattr("football.clubs_data.geocode_with_nominatim", lambda *_a, **_k: (None, []))
    monkeypatch.setattr(
        "football.clubs_data.geocode_with_club_queries",
        lambda *_a, **_k: None,
    )
    replaced = recalculate_wrong_locations(league, wikidata)
    assert replaced == 1
    bad = next(t for t in league["teams"] if t["name"] == "Bad Club")
    assert bad["geocode_recalculated"] is True
    assert bad["geocode_source"] == "wikidata"
    assert bad.get("location_outlier") is not True


def test_recalculate_pyramid_team_prefers_enriched_club_search(monkeypatch) -> None:
    team = {
        "name": "St Michaels DH",
        "wiki_title": "St_Michaels_DH_FC",
        "territory": "England",
        "latitude": 54.7,
        "longitude": -1.5,
        "geocode_source": "nominatim_name",
        "centroid_distance_km": 303.9,
        "centroid_distance_z": 29.8,
    }
    league = {
        "league_name": "Cheshire League Premier Division",
        "teams": [
            {"name": "A", "latitude": 53.2, "longitude": -2.5},
            {"name": "B", "latitude": 53.3, "longitude": -2.4},
            {"name": "C", "latitude": 53.25, "longitude": -2.45},
            {"name": "D", "latitude": 53.28, "longitude": -2.42},
            team,
        ],
    }

    def fake_search(query: str, *, limit=5, **_kwargs):
        if query == "St Michaels DH Cheshire FC, England":
            return [
                {
                    "latitude": 53.24,
                    "longitude": -2.55,
                    "formatted_address": query,
                    "place_id": "test",
                }
            ]
        return []

    monkeypatch.setattr("football.clubs_data.search_nominatim", fake_search)

    result = recalculate_pyramid_team(
        team,
        {},
        skip_source="nominatim_name",
        league=league,
        team_index=4,
    )
    assert result is not None
    assert result["geocode_source"] == "nominatim_club"
    assert result["latitude"] == 53.24
    assert result.get("location_outlier") is not True
