"""Tests for Wikipedia member-club parsing."""

from football.wikipedia_members import (
    division_heading_matches,
    is_valid_club_name,
    parse_wikipedia_member_clubs,
)

_SAMPLE_MEMBER_HTML = """
<h2>2025&ndash;26 member clubs</h2>
<h3>Division Two</h3>
<table class="wikitable">
<tr><th>Club</th><th>Home ground</th></tr>
<tr><td><a href="/wiki/Test_F.C.">Test FC</a></td><td>Main Road</td></tr>
<tr><td>Plain United</td><td>High Street</td></tr>
</table>
<h3>Champions</h3>
<ul>
<li>1995&ndash;96 Ortonians</li>
<li>1996&ndash;97 Deeping Rangers</li>
</ul>
"""

_MIDLAND_HTML = """
<h2>Current clubs (2024&ndash;25)</h2>
<h3>Premier Division</h3>
<ul><li>Wrong</li></ul>
<h3>Division Two</h3>
<table class="wikitable">
<tr><th>Club</th><th>Location</th></tr>
<tr><td><a href="/wiki/Acle_United_F.C.">Acle United</a></td><td>Acle</td></tr>
<tr><td>Bungay Town</td><td>Bungay</td></tr>
</table>
"""


def test_is_valid_club_name_rejects_champion_rows() -> None:
    assert not is_valid_club_name("1995–96 Ortonians")
    assert not is_valid_club_name("9th [ a ]")
    assert is_valid_club_name("Acle United")


def test_division_heading_matches_rejects_veterans_premier() -> None:
    assert division_heading_matches("Premier Division", "Premier Division")
    assert not division_heading_matches("Veterans Premier Division", "Premier Division")
    assert not division_heading_matches("Division One", "Premier Division")
    assert division_heading_matches("Premier East", "Premier Division East")


def test_parse_wikipedia_member_clubs_uses_wikitable() -> None:
    clubs = parse_wikipedia_member_clubs(_SAMPLE_MEMBER_HTML, division_hint="Division Two")
    assert len(clubs) == 2
    assert clubs[0]["name"] == "Test FC"


def test_parse_midland_division_two_table() -> None:
    clubs = parse_wikipedia_member_clubs(_MIDLAND_HTML, division_hint="Division Two")
    assert len(clubs) == 2
    assert clubs[0]["name"] == "Acle United"
