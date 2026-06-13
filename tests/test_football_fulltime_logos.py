"""Tests for FA Full-Time team crest parsing."""

from football.fulltime_logos import (
    extract_crest_url_from_team_html,
    is_fulltime_team_url,
    is_usable_football_crest_url,
    team_url_cache_key,
)

_SAMPLE_TEAM_PAGE = """
<div class="team-header flex middle center">
  <img src="https://resources.thefa.com/images/ftimages/data/league4780817/65401.jpg" alt="Acle United">
  <h1>Acle United</h1>
</div>
"""


def test_extract_crest_url_from_team_html() -> None:
    url = extract_crest_url_from_team_html(_SAMPLE_TEAM_PAGE)
    assert url == "https://resources.thefa.com/images/ftimages/data/league4780817/65401.jpg"


def test_is_fulltime_team_url() -> None:
    assert is_fulltime_team_url(
        "https://fulltime.thefa.com/displayTeam.html?divisionseason=1&teamID=99"
    )
    assert not is_fulltime_team_url("https://en.wikipedia.org/wiki/Arsenal_F.C.")


def test_team_url_cache_key() -> None:
    url = "https://fulltime.thefa.com/displayTeam.html?divisionseason=597578400&teamID=871038236"
    assert team_url_cache_key(url) == "871038236"


def test_is_usable_football_crest_url_rejects_placeholder() -> None:
    assert is_usable_football_crest_url(
        "https://resources.thefa.com/images/ftimages/data/league4780817/65401.jpg"
    )
    assert not is_usable_football_crest_url(
        "https://fulltime.thefa.com/assets/images/icons/icon-club.svg"
    )
    assert not is_usable_football_crest_url("")
    assert not is_usable_football_crest_url(None)
