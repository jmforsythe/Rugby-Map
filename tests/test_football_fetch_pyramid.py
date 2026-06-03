"""Tests for football.fetch_pyramid Wikipedia parsing."""

from football.fetch_pyramid import parse_wikipedia_clubs

_SAMPLE_TABLE = """
<table class="wikitable">
<tr><th>Club</th><th>League/Division</th><th>Lvl</th></tr>
<tr>
  <td><a href="/wiki/Arsenal_F.C." title="Arsenal F.C.">Arsenal</a></td>
  <td><a href="/wiki/2025%E2%80%9326_Premier_League">Premier League</a></td>
  <td>1</td>
</tr>
<tr>
  <td><a href="/wiki/Ashby_Ivanhoe_F.C.">Ashby Ivanhoe</a></td>
  <td><a href="/wiki/2025%E2%80%9326_United_Counties_League">United Counties League Premier Division South</a></td>
  <td>9</td>
</tr>
</table>
"""


def test_parse_wikipedia_clubs_extracts_league_season_url() -> None:
    clubs = parse_wikipedia_clubs(_SAMPLE_TABLE)
    assert len(clubs) == 2
    assert clubs[0]["league_url"] == "https://en.wikipedia.org/wiki/2025%E2%80%9326_Premier_League"
    assert (
        clubs[1]["league_url"]
        == "https://en.wikipedia.org/wiki/2025%E2%80%9326_United_Counties_League"
    )
