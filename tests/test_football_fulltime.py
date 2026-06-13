"""Tests for FA Full-Time table URL parsing."""

from football.fulltime import _parse_table_query, scrape_teams_from_table

_SAMPLE_INDEX = """
<a href="table.html?league=4780817&amp;selectedSeason=767941262&amp;selectedDivision=918037212&amp;selectedCompetition=0">Full Table</a>
"""

_SAMPLE_TABLE = """
<table class="cell-dividers">
<tr><th>Pos</th><th>Team</th><th>Pld</th></tr>
<tr><td>1</td><td><a href="/displayTeam.html?teamID=1">Acle United</a></td><td>10</td></tr>
<tr><td>2</td><td><a href="/displayTeam.html?teamID=2">Norwich CEYMS</a></td><td>10</td></tr>
</table>
"""


def test_parse_table_query() -> None:
    params = _parse_table_query(_SAMPLE_INDEX)
    assert params is not None
    assert params["league"] == "4780817"
    assert params["selectedDivision"] == "918037212"


def test_scrape_teams_from_table_parses_html() -> None:
    from unittest.mock import MagicMock, patch

    response = MagicMock()
    response.content = _SAMPLE_TABLE
    with patch("football.fulltime.make_request", return_value=response):
        teams = scrape_teams_from_table("https://fulltime.thefa.com/table.html?league=1")
    assert len(teams) == 2
    assert teams[0]["name"] == "Acle United"
    assert teams[0]["url"].endswith("teamID=1")
