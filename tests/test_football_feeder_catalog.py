"""Tests for level-11 feeder catalog parsing and FA division selection."""

from football.feeder_catalog import (
    division_hint,
    league_stem,
    parse_level11_from_wikipedia,
    wiki_article_for_division,
)
from football.fulltime import pick_division_id

_SAMPLE_SYSTEM_LIST = """
<ul>
<li>Anglian Combination Premier Division – 15 clubs</li>
<li>West Cheshire League Division One – 16 clubs</li>
<li>Midland League Division Two – 16 clubs</li>
</ul>
"""


def test_parse_level11_from_wikipedia() -> None:
    entries = parse_level11_from_wikipedia(_SAMPLE_SYSTEM_LIST)
    assert len(entries) == 3
    assert entries[0]["division_name"] == "Anglian Combination Premier Division"
    assert entries[0]["level"] == 11
    assert entries[0]["expected_clubs"] == 15


def test_wiki_article_for_division() -> None:
    assert (
        wiki_article_for_division("Anglian Combination Premier Division") == "Anglian Combination"
    )
    assert (
        wiki_article_for_division("West Cheshire League Division One")
        == "West Cheshire Association Football League"
    )
    assert (
        wiki_article_for_division("Sheffield & Hallamshire County Senior League Premier Division")
        == "Sheffield_&_Hallamshire_County_Senior_Football_League"
    )


def test_league_stem_and_division_hint() -> None:
    assert (
        league_stem("Bedfordshire County League Premier Division") == "Bedfordshire County League"
    )
    assert division_hint("Spartan South Midlands League Division Two") == "Division Two"


def test_pick_division_id_deprioritizes_cup() -> None:
    divisions = {
        "111": "Uhlsport Division 1",
        "222": "County Cup",
    }
    assert (
        pick_division_id(
            divisions,
            division_hint="Somerset County League Premier Division",
        )
        == "111"
    )


def test_pick_division_id_deprioritizes_lower_tiers() -> None:
    divisions = {
        "1": "Uhlsport Division 1",
        "2": "Uhlsport Division 2",
        "3": "Uhlsport Division 3",
    }
    assert pick_division_id(divisions, division_hint="Premier Division") == "1"


def test_pick_division_id_deprioritizes_ladies() -> None:
    divisions = {
        "1": "Premier Division",
        "2": "Ladies Premier Division",
    }
    assert pick_division_id(divisions, division_hint="Premier Division") == "1"


def test_pick_division_id_prefers_premier() -> None:
    divisions = {
        "918037212": "Premier Division",
        "597578400": "Swaffham Town",
        "7311373": "2003-04",
    }
    assert (
        pick_division_id(
            divisions,
            division_hint="Anglian Combination Premier Division",
        )
        == "918037212"
    )
