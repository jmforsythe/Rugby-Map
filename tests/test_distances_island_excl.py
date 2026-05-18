"""Tests for mainland excl-island travel stats in distances.py."""

from core import GeocodedTeam
from rugby.distance_lookup import DistanceLookup
from rugby.distances import _is_offshore_team, team_average, team_totals


def _team(name: str, lat: float, lon: float) -> GeocodedTeam:
    return {
        "name": name,
        "url": "",
        "image_url": None,
        "address": "",
        "latitude": lat,
        "longitude": lon,
        "formatted_address": "",
        "place_id": "",
    }


def test_offshore_detection():
    mainland = _team("London", 51.5, -0.1)
    iom = _team("Douglas", 54.15, -4.48)
    jersey = _team("St Helier", 49.19, -2.10)
    assert not _is_offshore_team(mainland)
    assert _is_offshore_team(iom)
    assert _is_offshore_team(jersey)


def test_mainland_excl_stats_skip_offshore_opponents():
    lookup = DistanceLookup()
    mainland = _team("Manchester", 53.48, -2.24)
    other_mainland = _team("Leeds", 53.80, -1.55)
    iom = _team("Douglas", 54.15, -4.48)
    teams = [mainland, other_mainland, iom]

    incl_avg, _ = team_average(mainland, teams, lookup)
    excl_avg, _ = team_average(mainland, teams, lookup, mainland_opponents_only=True)
    incl_total, _ = team_totals(mainland, teams, lookup)
    excl_total, _ = team_totals(mainland, teams, lookup, mainland_opponents_only=True)
    mainland_only_avg, _ = team_average(mainland, [mainland, other_mainland], lookup)

    assert excl_total < incl_total
    assert excl_avg == mainland_only_avg
    assert excl_total > 0
