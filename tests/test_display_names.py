"""Tests for compact display names used on social graphics."""

import pytest

from rugby.display_names import short_league_name, short_team_name


@pytest.mark.parametrize(
    ("raw", "short"),
    [
        ("Darlington Mowden Park RFC 2nd XV Men", "Darlington Mowden Park II"),
        ("Aldershot & Fleet RUFC 2nd XV Men", "Aldershot & Fleet II"),
        ("Old Gravesendians/Vigo 2nd XV men", "Old Gravesendians/Vigo II"),
        ("North Shields 2nd XV (Crusaders)", "North Shields II"),
        ("Shelford 4th XV", "Shelford IV"),
        ("East Midlands Rugby Union (CB) 1st XV Men", "East Midlands Rugby Union"),
        ("London Irish Amateur Wanderers (2nd XV)", "London Irish Amateur Wanderers"),
        ("Jersey Rugby Football Club Women", "Jersey RFC Women"),
        # Already compact or deliberately named: unchanged.
        ("Sale FC", "Sale FC"),
        ("Frome III", "Frome III"),
        ("Men of Kent", "Men of Kent"),
        ("Aylesbury Men I", "Aylesbury Men I"),
    ],
)
def test_short_team_name(raw, short):
    assert short_team_name(raw) == short


@pytest.mark.parametrize(
    ("raw", "short"),
    [
        ("Counties 4 Tribute Ale Somerset North", "Counties 4 Somerset North"),
        (
            "Counties 3 adm Lancashire & Cheshire Major Conference",
            "Counties 3 Lancashire & Cheshire Major Conference",
        ),
        ("Eastern Counties Greene King Division One North", "Eastern Counties Division One North"),
        ("Harvey's Wharf IPA Counties 4 Sussex Conference", "Counties 4 Sussex Conference"),
        ("Harveys Brewery Counties 3 Sussex", "Counties 3 Sussex"),
        ("NOWIRUL Alchemi Professional Solutions  Division 3 South", "NOWIRUL Division 3 South"),
        ("NOWIRUL The Last Drop Division 5 West", "NOWIRUL Division 5 West"),
        ("National League 1", "National League 1"),
    ],
)
def test_short_league_name(raw, short):
    assert short_league_name(raw) == short
