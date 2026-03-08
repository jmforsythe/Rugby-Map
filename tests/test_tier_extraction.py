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

    def test_merit_filename_not_matched(self):
        """Merit filenames no longer match pyramid extraction after cleanup."""
        assert extract_tier_men_current("CANDY_1.json", "2025-2026") is None
        assert extract_tier_men_current("NOWIRUL_Division_1.json", "2025-2026") is None
        assert extract_tier_men_current("Division_1.json", "2025-2026") is None


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

    def test_merit_filename_not_matched(self):
        """Merit-only prefixes removed from pyramid map."""
        assert extract_tier_men_pre_2021("CANDY_1.json", "2018-2019") is None
        assert extract_tier_men_pre_2021("NOWIRUL_Division_1.json", "2018-2019") is None
        assert extract_tier_men_pre_2021("Division_1.json", "2018-2019") is None


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


class TestExtractTierMeritPath:
    """Tests for merit path routing via extract_tier."""

    def test_candy_current(self):
        result = extract_tier("merit/CANDY/CANDY_1.json", "2025-2026")
        assert result == (10, "Counties 4")

    def test_candy_current_tier_2(self):
        result = extract_tier("merit/CANDY/CANDY_2_North.json", "2025-2026")
        assert result == (11, "Counties 5")

    def test_candy_old_conference(self):
        result = extract_tier("merit/CANDY/Conference_1.json", "2013-2014")
        assert result == (10, "Level 10")

    def test_candy_old_ubs(self):
        result = extract_tier("merit/CANDY/UBS_Candy_League_D1.json", "2013-2014")
        assert result == (10, "Level 10")

    def test_devon_merit_table(self):
        result = extract_tier("merit/Devon/Devon_Merit_Table_1.json", "2013-2014")
        assert result == (11, "Level 11")

    def test_devon_merit_no_number(self):
        result = extract_tier("merit/Devon/Devon_Merit_Table_NE.json", "2025-2026")
        assert result == (10, "Counties 4"), "NE is not a number, so base 10 only"

    def test_essex_division(self):
        result = extract_tier("merit/Essex/Division_1.json", "2025-2026")
        assert result == (10, "Counties 4")

    def test_essex_division_eight(self):
        result = extract_tier("merit/Essex/Division_Eight.json", "2013-2014")
        assert result == (17, "Level 17")

    def test_grfu_district(self):
        result = extract_tier("merit/GRFU_District/Bristol_&_District_1.json", "2025-2026")
        assert result == (11, "Counties 5")

    def test_grfu_north_no_pyramid_collision(self):
        """GRFU North_1 resolves via merit path, not pyramid 'North' prefix."""
        result = extract_tier("merit/GRFU_District/North_1.json", "2013-2014")
        assert result == (11, "Level 11")

    def test_pyramid_north_still_works(self):
        """Pyramid North_1.json still resolves via pyramid path."""
        result = extract_tier("North_1.json", "2018-2019")
        assert result == (6, "Level 6")

    def test_hampshire_merit(self):
        result = extract_tier("merit/Hampshire/Hampshire_Merit_One.json", "2013-2014")
        assert result == (11, "Level 11")

    def test_hampshire_counties(self):
        result = extract_tier("merit/Hampshire/Counties_5_Hampshire.json", "2025-2026")
        assert result == (11, "Counties 5")

    def test_herts_middlesex_merit_table(self):
        result = extract_tier("merit/Herts_Middlesex/Merit_Table_1.json", "2013-2014")
        assert result == (11, "Level 11")

    def test_herts_middlesex_merit_championship(self):
        result = extract_tier("merit/Herts_Middlesex/Merit_Championship_1.json", "2025-2026")
        assert result == (12, "Level 12")

    def test_leicestershire_premiership(self):
        result = extract_tier("merit/Leicestershire/Leicestershire_Premiership.json", "2013-2014")
        assert result == (9, "Level 9")

    def test_leicestershire_lru(self):
        result = extract_tier("merit/Leicestershire/LRU_Division_1.json", "2013-2014")
        assert result == (10, "Level 10")

    def test_middlesex_premier_division(self):
        result = extract_tier("merit/Middlesex/Premier_Division.json", "2025-2026")
        assert result == (9, "Counties 3")

    def test_middlesex_division(self):
        result = extract_tier("merit/Middlesex/Division_1.json", "2025-2026")
        assert result == (10, "Counties 4")

    def test_midlands_reserve(self):
        result = extract_tier(
            "merit/Midlands_Reserve/Midlands_West_Reserve_League_Div_1.json", "2013-2014"
        )
        assert result == (11, "Level 11")

    def test_nowirul_premier_current(self):
        result = extract_tier("merit/NOWIRUL/NOWIRUL_BATHTIME_PREMIER_LEAGUE.json", "2025-2026")
        assert result == (9, "Counties 3")

    def test_nowirul_division_current(self):
        result = extract_tier("merit/NOWIRUL/NOWIRUL_BAINES_PLUMBING_DIVISION_1.json", "2025-2026")
        assert result == (10, "Counties 4")

    def test_nowirul_old_conference(self):
        result = extract_tier("merit/NOWIRUL/Bateman_BMW_Conference_A.json", "2013-2014")
        assert result == (10, "Level 10")

    def test_nowirul_old_premier(self):
        result = extract_tier("merit/NOWIRUL/Bateman_BMW_Premier_League.json", "2013-2014")
        assert result == (9, "Level 9")

    def test_nowirul_raging_bull_division(self):
        result = extract_tier("merit/NOWIRUL/Raging_Bull_Division_2_North.json", "2013-2014")
        assert result == (11, "Level 11")

    def test_rural_kent_dragon_fire(self):
        result = extract_tier("merit/Rural_Kent/Dragon_Fire_4_East.json", "2013-2014")
        assert result == (12, "Level 12")

    def test_rural_kent_kent_a(self):
        result = extract_tier("merit/Rural_Kent/Kent_A_Rural.json", "2025-2026")
        assert result == (9, "Counties 3")

    def test_sussex_counties(self):
        result = extract_tier("merit/Sussex/Harvey's_Brewery_Counties_3_Sussex.json", "2025-2026")
        assert result == (9, "Counties 3")

    def test_east_midlands_numbered(self):
        result = extract_tier(
            "merit/East_Midlands/East_Midlands_2_-_Bedfordshire_(North).json", "2025-2026"
        )
        assert result == (10, "Counties 4")

    def test_east_midlands_b_variant(self):
        result = extract_tier("merit/East_Midlands/East_Midlands_2_-_Northants_B.json", "2025-2026")
        assert result == (10, "Counties 4")

    def test_east_midlands_sponsor_named(self):
        result = extract_tier("merit/East_Midlands/Bombardier_League.json", "2013-2014")
        assert result == (10, "Level 10")

    def test_backslash_path(self):
        """Windows-style backslash paths are normalised."""
        result = extract_tier("merit\\CANDY\\CANDY_1.json", "2025-2026")
        assert result == (10, "Counties 4")

    def test_unknown_competition_returns_unknown(self):
        result = extract_tier("merit/UnknownComp/Premiership.json", "2025-2026")
        assert result == (999, "Unknown Tier")


class TestGetNumberFromTierName:
    """Tests for parsing numbers from tier filenames."""

    def test_numeric_1(self):
        assert get_number_from_tier_name("North_1.json", "North") == 1

    def test_word_two(self):
        assert get_number_from_tier_name("North_Two.json", "North") == 2

    def test_no_number(self):
        assert get_number_from_tier_name("North_Premier.json", "North") == 0
