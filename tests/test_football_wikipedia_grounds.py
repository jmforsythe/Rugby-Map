"""Tests for Wikipedia infobox ground extraction."""

from football.wikipedia_grounds import clean_wiki_ground_value, extract_ground_from_wikitext


def test_extract_ground_from_infobox_wikitext() -> None:
    wikitext = """
{{Infobox football club
| clubname = Rugby Town
| ground = Butlin Road, [[Rugby, Warwickshire]]
| capacity = 5,000
}}
"""
    assert extract_ground_from_wikitext(wikitext) == "Butlin Road, Rugby, Warwickshire"


def test_extract_stadium_field_when_ground_missing() -> None:
    wikitext = """
{{Infobox football club
| clubname = Wrexham
| stadium = [[Racecourse Ground]]
}}
"""
    assert extract_ground_from_wikitext(wikitext) == "Racecourse Ground"


def test_clean_wiki_ground_value_strips_refs_and_templates() -> None:
    raw = "[[Emirates Stadium]]<ref name=ground/> {{small|(since 2006)}}"
    assert clean_wiki_ground_value(raw) == "Emirates Stadium"


def test_clean_wiki_ground_value_rejects_empty_placeholders() -> None:
    assert clean_wiki_ground_value("TBC") is None
    assert clean_wiki_ground_value("  ") is None
