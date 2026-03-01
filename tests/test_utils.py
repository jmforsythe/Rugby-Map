"""Tests for utility functions."""

from utils import team_name_to_filepath


class TestTeamNameToFilepath:
    """Tests for sanitizing team names into safe file paths."""

    def test_simple_name(self):
        assert team_name_to_filepath("Bath") == "Bath.html"

    def test_spaces(self):
        assert team_name_to_filepath("Old Elthamians") == "Old_Elthamians.html"

    def test_apostrophe(self):
        result = team_name_to_filepath("Bishop's Stortford")
        assert result == "Bishop's_Stortford.html"

    def test_ampersand(self):
        result = team_name_to_filepath("Berks & Bucks")
        assert result == "Berks_and_Bucks.html"

    def test_roman_numerals_suffix(self):
        result = team_name_to_filepath("Saracens II")
        assert result == "Saracens_II.html"
