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
                "Wasps": "Warwickshire RFU",
                "Wasps FC": "Middlesex County RFU",
                "Alton RFC Ltd": "Hampshire RFU Ltd.",
                "Hartlepool Rovers RUFC": "Durham County Rugby Union",
                "Zorbing Zebras RFC Ltd": "Surrey Rugby",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(constituent_bodies, "CLUB_CB_MAPPING_PATH", mapping_path)
    constituent_bodies._load_club_cb_map.cache_clear()
    constituent_bodies._load_fallback_club_cb_map.cache_clear()
    yield
    constituent_bodies._load_club_cb_map.cache_clear()
    constituent_bodies._load_fallback_club_cb_map.cache_clear()


class TestGetConstituentBody:
    def test_exact_club_name(self):
        assert constituent_bodies.get_constituent_body("Barnes RFC") == "Surrey Rugby"

    def test_resolves_team_name_to_canonical_club_name_first(self):
        # "Barnes Women II" isn't in the CB export, but club_names.json (the
        # real one, not mocked here) resolves it to the canonical "Barnes RFC".
        assert constituent_bodies.get_constituent_body("Barnes Women II") == "Surrey Rugby"

    def test_case_insensitive_match(self):
        assert constituent_bodies.get_constituent_body("barnes rfc") == "Surrey Rugby"

    def test_ampersand_is_not_normalized(self):
        # No fuzzy normalization: "and" won't match a "&" entry.
        assert constituent_bodies.get_constituent_body("Ampthill and District RFC") is None
        assert (
            constituent_bodies.get_constituent_body("Ampthill & District RFC")
            == "East Midlands Rugby Union"
        )

    def test_does_not_strip_club_type_suffix(self):
        # "Nonexistent Rugby Club" isn't in the export under any name, and
        # must not be guessed at by stripping/adding a club-type suffix to
        # find an unrelated shorter or longer entry.
        assert constituent_bodies.get_constituent_body("Nonexistent Rugby Club") is None

    def test_does_not_collapse_distinct_clubs_with_shared_prefix(self):
        # "Wasps" and "Wasps FC" are different real clubs with different CBs;
        # neither should be guessed from the other.
        assert constituent_bodies.get_constituent_body("Wasps") == "Warwickshire RFU"
        assert constituent_bodies.get_constituent_body("Wasps FC") == "Middlesex County RFU"

    def test_no_match_returns_none(self):
        assert constituent_bodies.get_constituent_body("Some Unlisted Club") is None

    def test_empty_mapping_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(constituent_bodies, "CLUB_CB_MAPPING_PATH", tmp_path / "missing.json")
        constituent_bodies._load_club_cb_map.cache_clear()
        assert constituent_bodies.get_constituent_body("Barnes RFC") is None

    def test_ltd_suffix_fallback(self):
        # The export has "Alton RFC Ltd"; our canonical name is "Alton RFC".
        assert constituent_bodies.get_constituent_body("Alton RFC") == "Hampshire RFU Ltd."

    def test_rufc_rfc_equivalence_fallback(self):
        # The export has "Hartlepool Rovers RUFC"; canonical name uses "RFC".
        assert (
            constituent_bodies.get_constituent_body("Hartlepool Rovers RFC")
            == "Durham County Rugby Union"
        )

    def test_fallback_does_not_match_a_bare_name_against_a_suffixed_one(self):
        # "Zorbing Zebras" (no club-type suffix, and not in club_names.json so
        # it resolves to itself) must not match "Zorbing Zebras RFC Ltd" via
        # the fallback -- that would reintroduce the same bare-name-collision
        # risk the exact-only lookup avoids.
        assert constituent_bodies.get_constituent_body("Zorbing Zebras") is None
        assert constituent_bodies.get_constituent_body("Zorbing Zebras RFC") == "Surrey Rugby"
