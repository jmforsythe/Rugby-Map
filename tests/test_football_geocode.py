"""Tests for football pyramid geocoding order."""

from football.clubs_data import geocode_pyramid_team


def test_geocode_pyramid_team_prefers_wikidata_over_nominatim() -> None:
    team = {
        "name": "Arsenal",
        "wiki_title": "Arsenal_F.C.",
        "address": "Emirates Stadium, London, England",
        "address_source": "wikipedia",
        "territory": "England",
    }
    wikidata = {
        "Arsenal_F.C.": {
            "ground": "Emirates Stadium",
            "latitude": 51.555,
            "longitude": -0.108,
        }
    }
    result = geocode_pyramid_team(team, wikidata)
    assert result["geocode_source"] == "wikidata"
    assert result["latitude"] == 51.555


def test_geocode_pyramid_team_uses_nominatim_when_no_wikidata(monkeypatch) -> None:
    team = {
        "name": "Rugby Town",
        "wiki_title": "Rugby_Town_F.C.",
        "address": "Butlin Road, Rugby, Warwickshire",
        "address_source": "wikipedia",
        "territory": "England",
    }

    def fake_nominatim(address: str, **_kwargs):
        return (
            {
                "latitude": 52.37,
                "longitude": -1.24,
                "formatted_address": address,
                "place_id": "test",
            },
            [],
        )

    monkeypatch.setattr("football.clubs_data.geocode_with_nominatim", fake_nominatim)
    result = geocode_pyramid_team(team, {})
    assert result["geocode_source"] == "wikipedia"
    assert result["latitude"] == 52.37
