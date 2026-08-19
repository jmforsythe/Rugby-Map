"""Tests for team -> Constituent Body lookup."""

import json

import pytest

from rugby import constituent_bodies


@pytest.fixture(autouse=True)
def _fake_club_cb_mapping(tmp_path, monkeypatch):
    """Point the module at a small fixture mapping instead of the real data file."""
    mapping_path = tmp_path / "club_cb_mapping.json"
    mapping_path.write_text(
        json.dumps(
            {
                "Barnes RFC": "Surrey Rugby",
                "Ampthill & District RFC": "East Midlands Rugby Union",
                "Blackheath FC": "Kent County Rugby Football Union Limited",
                "Alton RFC Ltd": "Hampshire RFU Ltd.",
                "Dorset & Wilts Society RFC": "Dorset & Wilts RFU",
                "Cobham Rugby Football Club Limited": "Surrey Rugby",
                "Henley Rugby Club Ltd": "Oxfordshire RFU",
                "Hitchin Rugby Ltd": "Hertfordshire RFU",
                "Fylde RFC": "Lancashire County RFU",
                "Percy RFC": "Cumbria RFU Ltd.",
                "Percy Park RFC": "Northumberland Rugby Union",
                "Ash RFC": "Kent County Rugby Football Union Limited",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(constituent_bodies, "CLUB_CB_MAPPING_PATH", mapping_path)
    constituent_bodies._load_normalized_club_cb_map.cache_clear()
    yield
    constituent_bodies._load_normalized_club_cb_map.cache_clear()


class TestGetConstituentBody:
    def test_exact_club_name(self):
        assert constituent_bodies.get_constituent_body("Barnes RFC") == "Surrey Rugby"

    def test_bare_club_name_without_suffix(self):
        assert constituent_bodies.get_constituent_body("Barnes") == "Surrey Rugby"

    def test_fc_suffix_matches_like_rfc(self):
        assert (
            constituent_bodies.get_constituent_body("Blackheath")
            == "Kent County Rugby Football Union Limited"
        )

    def test_ltd_suffix_stripped(self):
        assert constituent_bodies.get_constituent_body("Alton") == "Hampshire RFU Ltd."

    def test_rugby_football_club_limited_suffix_stripped(self):
        assert constituent_bodies.get_constituent_body("Cobham") == "Surrey Rugby"
        assert constituent_bodies.get_constituent_body("Cobham 2nd XV") == "Surrey Rugby"

    def test_rugby_club_ltd_suffix_stripped(self):
        assert constituent_bodies.get_constituent_body("Henley") == "Oxfordshire RFU"

    def test_bare_rugby_ltd_suffix_stripped(self):
        assert constituent_bodies.get_constituent_body("Hitchin") == "Hertfordshire RFU"

    def test_strips_trailing_team_number_and_gender_words(self):
        assert constituent_bodies.get_constituent_body("Barnes Women II") == "Surrey Rugby"
        assert constituent_bodies.get_constituent_body("Barnes 3rd XV") == "Surrey Rugby"

    def test_strips_trailing_parenthetical_annotation(self):
        assert constituent_bodies.get_constituent_body("Barnes (2nd XV)") == "Surrey Rugby"

    def test_strips_trailing_dash_annotation(self):
        assert constituent_bodies.get_constituent_body("Barnes - 3rd XV") == "Surrey Rugby"

    def test_ampersand_normalized(self):
        assert (
            constituent_bodies.get_constituent_body("Ampthill and District")
            == "East Midlands Rugby Union"
        )

    def test_no_match_returns_none(self):
        assert constituent_bodies.get_constituent_body("Some Unlisted Club") is None

    def test_does_not_overstrip_into_a_different_club(self):
        # "Society" isn't a strippable suffix word, so this must not collapse
        # into "Dorset & Wilts" and match some unrelated shorter entry.
        assert (
            constituent_bodies.get_constituent_body("Dorset & Wilts Society RFC")
            == "Dorset & Wilts RFU"
        )

    def test_empty_mapping_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(constituent_bodies, "CLUB_CB_MAPPING_PATH", tmp_path / "missing.json")
        constituent_bodies._load_normalized_club_cb_map.cache_clear()
        assert constituent_bodies.get_constituent_body("Barnes RFC") is None


class TestLongestPrefixFallback:
    """Branded team-tier names (e.g. "Fylde Hawks") that suffix-word stripping
    can't recognize -- these fall back to the longest known club name that
    prefixes the team name.
    """

    def test_falls_back_to_known_club_prefix(self):
        assert constituent_bodies.get_constituent_body("Fylde Hawks") == "Lancashire County RFU"

    def test_falls_back_with_parenthetical_and_team_number(self):
        assert (
            constituent_bodies.get_constituent_body("Fylde Hawks (2nd XV)")
            == "Lancashire County RFU"
        )

    def test_prefers_longest_matching_club_name(self):
        # "Percy" and "Percy Park" are both real clubs; "Percy Park Lions"
        # must resolve to Percy Park, not the shorter "Percy" prefix.
        assert (
            constituent_bodies.get_constituent_body("Percy Park Lions")
            == "Northumberland Rugby Union"
        )
        assert constituent_bodies.get_constituent_body("Percy Wanderers") == "Cumbria RFU Ltd."

    def test_does_not_match_without_word_boundary(self):
        # "Ashington" must not fall back to "Ash" just because it starts
        # with the same letters -- there's no word boundary between them.
        assert constituent_bodies.get_constituent_body("Ashington Rovers") is None

    def test_short_prefix_is_not_trusted(self):
        # "Ash" is a real club, but it's short enough that a word-boundary
        # prefix match is too likely to be coincidental to trust.
        assert constituent_bodies.get_constituent_body("Ash Wanderers") is None
