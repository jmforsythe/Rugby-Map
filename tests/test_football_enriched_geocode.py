"""Tests for enriched club Nominatim query building."""

from football.clubs_data import enriched_club_nominatim_queries, geocode_with_club_queries


def test_enriched_club_nominatim_queries() -> None:
    queries = enriched_club_nominatim_queries(
        "St Michaels DH",
        "Cheshire League Premier Division",
        "England",
    )
    assert queries[0] == "St Michaels DH Cheshire FC, England"
    assert "St Michaels DH Cheshire football club, England" in queries
    assert "St Michaels DH football club, England" not in queries


def test_enriched_club_nominatim_queries_uses_county_alias() -> None:
    queries = enriched_club_nominatim_queries(
        "Easton",
        "Anglian Combination Premier Division",
        "England",
    )
    assert queries[0] == "Easton Norfolk FC, England"
    assert "Easton football club, England" not in queries


def test_geocode_with_club_queries_uses_first_hit(monkeypatch) -> None:
    calls: list[str] = []

    def fake_search(query: str, *, limit=5, **_kwargs):
        calls.append(query)
        if query == "St Michaels DH Cheshire FC, England":
            return [
                {
                    "latitude": 53.2,
                    "longitude": -2.6,
                    "formatted_address": query,
                    "place_id": "test",
                }
            ]
        return []

    monkeypatch.setattr("football.clubs_data.search_nominatim", fake_search)
    team = {"name": "St Michaels DH", "territory": "England"}
    result = geocode_with_club_queries(team, "Cheshire League Premier Division")
    assert result is not None
    assert result["geocode_source"] == "nominatim_club"
    assert result["address"] == "St Michaels DH Cheshire FC, England"
    assert calls[0] == "St Michaels DH Cheshire FC, England"


def test_geocode_with_club_queries_picks_lowest_league_z_score(monkeypatch) -> None:
    def fake_search(query: str, *, limit=5, **_kwargs):
        if query == "Easton Norfolk FC, England":
            return [
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
        return []

    monkeypatch.setattr("football.clubs_data.search_nominatim", fake_search)
    league = {
        "league_name": "Anglian Combination Premier Division",
        "teams": [
            {"name": "A", "latitude": 52.5, "longitude": 1.2},
            {"name": "B", "latitude": 52.6, "longitude": 1.3},
            {"name": "C", "latitude": 52.4, "longitude": 1.1},
            {"name": "D", "latitude": 52.55, "longitude": 1.25},
            {"name": "Easton", "territory": "England"},
        ],
    }
    result = geocode_with_club_queries(
        league["teams"][4],
        league["league_name"],
        league=league,
        team_index=4,
    )
    assert result is not None
    assert result["place_id"] == "norfolk"


def test_successful_geocode_removes_error_field(monkeypatch) -> None:
    team = {
        "name": "Acle United",
        "territory": "England",
        "error": "geocoding_failed",
    }

    def fake_search(query: str, *, limit=5, **_kwargs):
        if query == "Acle United Norfolk FC, England":
            return [
                {
                    "latitude": 52.63,
                    "longitude": 1.55,
                    "formatted_address": query,
                    "place_id": "test",
                }
            ]
        return []

    monkeypatch.setattr("football.clubs_data.search_nominatim", fake_search)
    result = geocode_with_club_queries(team, "Anglian Combination Premier Division")
    assert result is not None
    assert "error" not in result
    assert result["latitude"] == 52.63
