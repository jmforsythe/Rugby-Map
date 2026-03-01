"""Tests for tier extraction logic."""

from tier_extraction import (
    extract_tier,
    extract_tier_men_current,
    extract_tier_men_pre_2021,
    extract_tier_women_current,
    extract_tier_women_pre_2018,
    get_number_from_tier_name,
)


class TestExtractTierMenCurrent:
    """Tests for current men's tier extraction (2022+)."""

    def test_premiership(self):
        assert extract_tier_men_current("Premiership.json", "2025-2026") == (1, "Premiership")

    def test_championship(self):
        assert extract_tier_men_current("Championship.json", "2025-2026") == (2, "Championship")

    def test_national_league_1(self):
        assert extract_tier_men_current("National_League_1_North.json", "2025-2026") == (
            3,
            "National League 1",
        )

    def test_regional_1(self):
        assert extract_tier_men_current("Regional_1_North.json", "2025-2026") == (
            5,
            "Regional 1",
        )

    def test_counties_3(self):
        assert extract_tier_men_current("Counties_3_Sussex.json", "2025-2026") == (
            9,
            "Counties 3",
        )

    def test_cumbria_conference_1(self):
        assert extract_tier_men_current("Cumbria_Conference_1.json", "2025-2026") == (
            8,
            "Counties 2",
        )

    def test_cumbria_conference_2(self):
        assert extract_tier_men_current("Cumbria_Conference_2.json", "2025-2026") == (
            9,
            "Counties 3",
        )

    def test_unknown_returns_none(self):
        assert extract_tier_men_current("Unknown_League.json", "2025-2026") is None


class TestExtractTierWomenCurrent:
    """Tests for current women's tier extraction."""

    def test_womens_premiership(self):
        assert extract_tier_women_current("Women's_Premiership.json", "2025-2026") == (
            101,
            "Premiership Women's",
        )

    def test_womens_championship_1(self):
        assert extract_tier_women_current("Women's_Championship_1.json", "2025-2026") == (
            102,
            "Championship 1",
        )

    def test_womens_championship_2(self):
        assert extract_tier_women_current("Women's_Championship_2.json", "2025-2026") == (
            103,
            "Championship 2",
        )

    def test_womens_nc_1(self):
        assert extract_tier_women_current("Women's_NC_1_North.json", "2025-2026") == (
            104,
            "National Challenge 1",
        )


class TestExtractTierMenPre2021:
    """Tests for historical men's tier extraction."""

    def test_premiership(self):
        assert extract_tier_men_pre_2021("Premiership.json", "2018-2019") == (1, "Premiership")

    def test_national_league_with_prefix(self):
        result = extract_tier_men_pre_2021("Greene_King_IPA_Championship.json", "2018-2019")
        assert result == (2, "Championship")

    def test_north_1(self):
        result = extract_tier_men_pre_2021("North_1.json", "2018-2019")
        assert result == (6, "Level 6")

    def test_london_2(self):
        result = extract_tier_men_pre_2021("London_2_North.json", "2018-2019")
        assert result == (7, "Level 7")

    def test_unknown_returns_none(self):
        assert extract_tier_men_pre_2021("Totally_Unknown.json", "2018-2019") is None


class TestExtractTierWomenPre2018:
    """Tests for historical women's tier extraction."""

    def test_womens_premiership(self):
        assert extract_tier_women_pre_2018("Women's_Premiership.json", "2017-2018") == (
            101,
            "Premiership Women's",
        )

    def test_womens_championship_2(self):
        assert extract_tier_women_pre_2018("Women's_Championship_2.json", "2017-2018") == (
            103,
            "Championship 2",
        )


class TestExtractTier:
    """Tests for the main extract_tier dispatcher."""

    def test_mens_current(self):
        assert extract_tier("Premiership.json", "2025-2026") == (1, "Premiership")

    def test_womens_current(self):
        assert extract_tier("Women's_Premiership.json", "2025-2026") == (
            101,
            "Premiership Women's",
        )

    def test_unknown_returns_999(self):
        result = extract_tier("Totally_Unknown.json", "2025-2026")
        assert result == (999, "Unknown Tier")

    def test_pre_2021_mens(self):
        assert extract_tier("Premiership.json", "2018-2019") == (1, "Premiership")


class TestGetNumberFromTierName:
    """Tests for parsing numbers from tier filenames."""

    def test_numeric_1(self):
        assert get_number_from_tier_name("North_1.json", "North") == 1

    def test_word_two(self):
        assert get_number_from_tier_name("North_Two.json", "North") == 2

    def test_no_number(self):
        assert get_number_from_tier_name("North_Premier.json", "North") == 0
