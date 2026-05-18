"""Tests for tier extraction logic.

Merit paths return **local** tier numbers (within the competition) and
competition-qualified display names (e.g. "CANDY 1", "Essex Premier").
Use ``get_competition_offset`` to convert to absolute pyramid positions.
"""

from rugby.tiers import (
    extract_tier,
    extract_tier_men_current,
    extract_tier_men_pre_2021,
    extract_tier_women_current,
    extract_tier_women_pre_2018,
    get_competition_offset,
    get_number_from_tier_name,
)


def test_competition_offsets_east_midlands_nottinghamshire_2008_2009_era() -> None:
    """East Midlands apex maps under nationals via offset 10 through 2010-2011 (then base offset 8).

    Nottinghamshire offset 10 while tier_mappings apex is Midlands 5 East (North) (2008-2009–2019-2020);
    offset 9 when the stem uses Midlands 4 naming (2021-2022+).
    """
    for season in ("2008-2009", "2009-2010", "2010-2011"):
        assert get_competition_offset("East_Midlands", season) == 10
    for season in ("2008-2009", "2009-2010", "2010-2011", "2011-2012", "2019-2020"):
        assert get_competition_offset("Nottinghamshire", season) == 10
    assert get_competition_offset("East_Midlands", "2007-2008") == 8
    assert get_competition_offset("Nottinghamshire", "2007-2008") == 9
    assert get_competition_offset("East_Midlands", "2011-2012") == 8
    assert get_competition_offset("Nottinghamshire", "2021-2022") == 9


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

    def test_yorkshire_5a(self):
        """Yorkshire 5A/5B/5C must parse as tier 11 pre-championship (offset 6 + 5, no -1 for county)."""
        assert extract_tier_men_pre_2021("Yorkshire_5A.json", "2007-2008") == (11, "Level 11")
        assert extract_tier_men_pre_2021("Yorkshire_5B.json", "2007-2008") == (11, "Level 11")
        assert extract_tier_men_pre_2021("Yorkshire_5C.json", "2008-2009") == (11, "Level 11")

    def test_yorkshire_5_named_variants(self):
        """Named Yorkshire 5 variants: tier 11 pre-championship (no -1 for county), 11 post."""
        assert extract_tier_men_pre_2021("Yorkshire_5_North_West.json", "2006-2007") == (
            11,
            "Level 11",
        )
        assert extract_tier_men_pre_2021("Yorkshire_Division_Five.json", "2009-2010") == (
            11,
            "Level 11",
        )

    def test_bbo_pre_premier_era(self):
        """BB&O 1 is the top county league before 2005 (no Premier existed), no -1 for county prefix."""
        assert extract_tier_men_pre_2021("xBerks_Bucks_&_Oxon_1.json", "2000-2001") == (
            8,
            "Level 8",
        )
        assert extract_tier_men_pre_2021("xBerks_Bucks_&_Oxon_2.json", "2000-2001") == (
            9,
            "Level 9",
        )

    def test_bbo_with_premier(self):
        """Once Premier exists (2004+), BB&O Premier is tier 8 (no -1 for county), BB&O 1 is tier 9."""
        assert extract_tier_men_pre_2021("Berks_Bucks_&_Oxon_Premier.json", "2005-2006") == (
            8,
            "Level 8",
        )
        assert extract_tier_men_pre_2021("Berks_Bucks_&_Oxon_1_North.json", "2005-2006") == (
            9,
            "Level 9",
        )
        assert extract_tier_men_pre_2021("Berks_Bucks_&_Oxon_Premier.json", "2004-2005") == (
            8,
            "Level 8",
        )
        assert extract_tier_men_pre_2021("Berks_Bucks_&_Oxon_1_North.json", "2004-2005") == (
            9,
            "Level 9",
        )
        assert extract_tier_men_pre_2021("Berks_Bucks_&_Oxon_1_South.json", "2004-2005") == (
            9,
            "Level 9",
        )

    def test_midlands_east_geographic_split(self):
        """Midlands East (South) A/B are geographic splits at tier 11."""
        assert extract_tier_men_pre_2021("Midlands_East_(South)_A.json", "2009-2010") == (
            11,
            "Level 11",
        )
        assert extract_tier_men_pre_2021("Midlands_East_(South)_B.json", "2009-2010") == (
            11,
            "Level 11",
        )

    def test_derbys_notts_nld_n_leics_one_level_deeper_than_peer_counties(self):
        """Derbys/N Leics, NLD/N Leics, Notts/Lincs use tier-9 base (not offset 8)."""
        expected = (9, "Level 9")
        for fn in (
            "Derbys_N_Leics.json",
            "NLD_N_Leics.json",
            "Notts_Lincs.json",
            "xDerbys_N_Leics.json",
            "xNLD_N_Leics.json",
            "xNotts_Lincs.json",
        ):
            assert extract_tier_men_pre_2021(fn, "2005-2006") == expected
            assert extract_tier(fn, "2005-2006") == expected
            assert extract_tier_men_pre_2021(fn, "2004-2005") == expected
            assert extract_tier(fn, "2018-2019") == expected


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


class TestExtractTierWomenPreRfuRestructure:
    """Women's tiers before RFUW renamed the pyramid from 2007–2008 (dual Prem / no Championship).

    Premiership 1 → 101, Premiership 2 → 102; National Challenge levels sit one notch higher
    (NC1 → tier 103) than post-restructure (NC1 → 104).
    """

    def test_dual_premiership_separate_levels(self):
        assert extract_tier("wPremiership_1.json", "2002-2003") == (101, "Premiership 1 Women's")
        assert extract_tier("wPremiership_2.json", "2006-2007") == (102, "Premiership 2 Women's")
        assert extract_tier("xwPremiership_2.json", "2004-2005") == (102, "Premiership 2 Women's")

    def test_w_prem_without_ordinal(self):
        assert extract_tier("wPremiership.json", "2006-2007") == (101, "Premiership Women's")

    def test_national_challenge_bumped_prior_to_rfuf_rename(self):
        assert extract_tier("wMidlands_1.json", "2002-2003") == (103, "National Challenge 1")
        assert extract_tier("wSouth_West_2.json", "2005-2006") == (104, "National Challenge 2")

    def test_from_rfuf_rename_season_w_prem_still_folded(self):
        assert extract_tier("wPremiership_1.json", "2007-2008") == (101, "Premiership Women's")


class TestExtractTier:
    """Tests for the main extract_tier dispatcher."""

    def test_mens_current(self):
        assert extract_tier("Premiership.json", "2025-2026") == (1, "Premiership")

    def test_womens_current(self):
        assert extract_tier("Women's_Premiership.json", "2025-2026") == (
            101,
            "Premiership Women's",
        )

    def test_end_of_season_playoff_fixture_files(self):
        """fixture_data-only RFU play-off leagues (see rugby.fixtures._EXTRA_FIXTURE_URLS_BY_SEASON)."""
        assert extract_tier(
            "Championship_Relegation_and_National_1_Promotion.json", "2025-2026"
        ) == (
            3,
            "Play-off: Championship Relegation / National League 1 Promotion",
        )
        assert extract_tier("Regional_2_Relegation.json", "2025-2026") == (
            6,
            "Play-off: Regional 2 Relegation",
        )
        assert extract_tier(
            "2025-2026/National_1_Relegation_and_National_2_Promotion.json", "2025-2026"
        ) == (
            4,
            "Play-off: National League 1 Relegation / National League 2 Promotion",
        )

    def test_unknown_returns_999(self):
        result = extract_tier("Totally_Unknown.json", "2025-2026")
        assert result == (999, "Unknown Tier")

    def test_pre_2021_mens(self):
        assert extract_tier("Premiership.json", "2018-2019") == (1, "Premiership")


class TestExtractTierMeritPath:
    """Tests for merit path routing via extract_tier.

    Merit paths return local tier numbers and competition-qualified names.
    """

    def test_candy_current(self):
        result = extract_tier("merit/CANDY/CANDY_1.json", "2025-2026")
        assert result == (1, "CANDY 1")

    def test_candy_current_tier_2(self):
        result = extract_tier("merit/CANDY/CANDY_2_North.json", "2025-2026")
        assert result == (2, "CANDY 2")

    def test_candy_old_conference(self):
        result = extract_tier("merit/CANDY/Conference_1.json", "2013-2014")
        assert result == (1, "CANDY 1")

    def test_candy_old_ubs(self):
        result = extract_tier("merit/CANDY/UBS_Candy_League_D1.json", "2013-2014")
        assert result == (1, "CANDY 1")

    def test_devon_merit_table(self):
        result = extract_tier("merit/Devon/Devon_Merit_Table_1.json", "2013-2014")
        assert result == (1, "Devon 1")

    def test_devon_merit_no_number(self):
        result = extract_tier("merit/Devon/Devon_Merit_Table_NE.json", "2025-2026")
        assert result == (1, "Devon 1"), "NE is not a number, so base offset only"

    def test_essex_division(self):
        result = extract_tier("merit/Essex/Division_1.json", "2025-2026")
        assert result == (1, "Essex 1")

    def test_essex_division_eight(self):
        result = extract_tier("merit/Essex/Division_Eight.json", "2013-2014")
        assert result == (8, "Essex 8")

    def test_grfu_district(self):
        result = extract_tier("merit/GRFU_District/Bristol_&_District_1.json", "2025-2026")
        assert result == (1, "GRFU District 1")

    def test_grfu_north_no_pyramid_collision(self):
        """GRFU North_1 resolves via merit path, not pyramid 'North' prefix."""
        result = extract_tier("merit/GRFU_District/North_1.json", "2013-2014")
        assert result == (1, "GRFU District 1")

    def test_pyramid_north_still_works(self):
        """Pyramid North_1.json still resolves via pyramid path."""
        result = extract_tier("North_1.json", "2018-2019")
        assert result == (6, "Level 6")

    def test_hampshire_merit(self):
        result = extract_tier("merit/Hampshire/Hampshire_Merit_One.json", "2013-2014")
        assert result == (6, "Hampshire 6")

    def test_hampshire_counties(self):
        result = extract_tier("merit/Hampshire/Counties_5_Hampshire.json", "2025-2026")
        assert result == (5, "Hampshire 5")

    def test_herts_middlesex_merit_table(self):
        result = extract_tier("merit/Herts_Middlesex/Merit_Table_1.json", "2013-2014")
        assert result == (1, "Herts Middlesex 1")

    def test_herts_middlesex_merit_championship(self):
        result = extract_tier("merit/Herts_Middlesex/Merit_Championship_1.json", "2025-2026")
        assert result == (2, "Herts Middlesex 2")

    def test_leicestershire_premiership(self):
        result = extract_tier("merit/Leicestershire/Leicestershire_Premiership.json", "2013-2014")
        assert result == (1, "Leicestershire 1")

    def test_leicestershire_lru(self):
        result = extract_tier("merit/Leicestershire/LRU_Division_1.json", "2013-2014")
        assert result == (2, "Leicestershire 2")

    def test_leicestershire_offset_when_apex_midlands_4_east_south(self) -> None:
        """Premiership/LRU apex feeds Midlands 4 East (South) in tier_mappings (2010-2011–2014-2015)."""
        for season in ("2010-2011", "2011-2012", "2012-2013", "2013-2014", "2014-2015"):
            assert get_competition_offset("Leicestershire", season) == 9
        assert get_competition_offset("Leicestershire", "2015-2016") == 8

    def test_middlesex_premier_division(self):
        result = extract_tier("merit/Middlesex/Premier_Division.json", "2025-2026")
        assert result == (1, "Middlesex 1")

    def test_middlesex_division(self):
        result = extract_tier("merit/Middlesex/Division_1.json", "2025-2026")
        assert result == (2, "Middlesex 2")

    def test_midlands_reserve(self):
        result = extract_tier(
            "merit/Midlands_Reserve/Midlands_West_Reserve_League_Div_1.json", "2013-2014"
        )
        assert result == (1, "Midlands Reserve 1")

    def test_midlands_reserve_geographic_apex_filenames(self):
        """2011-2012 style stems lack Div 1/2 — match North/South apex via :data:`_NAMED_MERIT_LEAGUES`."""
        assert extract_tier(
            "merit/Midlands_Reserve/North_-_West_Midlands_Reserve_Team_League.json", "2011-2012"
        ) == (1, "Midlands Reserve 1")
        assert extract_tier(
            "merit/Midlands_Reserve/South_-_West_Midlands_Reserve_Team_League.json", "2011-2012"
        ) == (1, "Midlands Reserve 1")
        assert get_competition_offset("Midlands_Reserve", "2011-2012") == 10
        assert get_competition_offset("Midlands_Reserve", "2013-2014") == 10

    def test_nowirul_premier_current(self):
        result = extract_tier("merit/NOWIRUL/NOWIRUL_BATHTIME_PREMIER_LEAGUE.json", "2025-2026")
        assert result == (1, "NOWIRUL 1")

    def test_nowirul_division_current(self):
        result = extract_tier("merit/NOWIRUL/NOWIRUL_BAINES_PLUMBING_DIVISION_1.json", "2025-2026")
        assert result == (2, "NOWIRUL 2")

    def test_nowirul_old_conference(self):
        result = extract_tier("merit/NOWIRUL/Bateman_BMW_Conference_A.json", "2013-2014")
        assert result == (2, "NOWIRUL 2")

    def test_nowirul_old_premier(self):
        result = extract_tier("merit/NOWIRUL/Bateman_BMW_Premier_League.json", "2013-2014")
        assert result == (1, "NOWIRUL 1")

    def test_nowirul_raging_bull_division(self):
        result = extract_tier("merit/NOWIRUL/Raging_Bull_Division_2_North.json", "2013-2014")
        assert result == (3, "NOWIRUL 3")

    def test_rural_kent_dragon_fire(self):
        result = extract_tier("merit/Rural_Kent/Dragon_Fire_4_East.json", "2013-2014")
        assert result == (4, "Rural Kent 4")

    def test_rural_kent_kent_a(self):
        result = extract_tier("merit/Rural_Kent/Kent_A_Rural.json", "2025-2026")
        assert result == (1, "Rural Kent 1")

    def test_sussex_counties(self):
        result = extract_tier("merit/Sussex/Harvey's_Brewery_Counties_3_Sussex.json", "2025-2026")
        assert result == (3, "Sussex 3")

    def test_east_midlands_numbered(self):
        result = extract_tier(
            "merit/East_Midlands/East_Midlands_2_-_Bedfordshire_(North).json", "2025-2026"
        )
        assert result == (2, "East Midlands 2")

    def test_east_midlands_b_variant(self):
        result = extract_tier("merit/East_Midlands/East_Midlands_2_-_Northants_B.json", "2025-2026")
        assert result == (3, "East Midlands 3")

    def test_east_midlands_sponsor_named(self):
        result = extract_tier("merit/East_Midlands/Bombardier_League.json", "2013-2014")
        assert result == (1, "East Midlands 1")

    def test_east_midlands_eagle_ipa_2009_2010_reserves_courage_rung(self):
        """Absent Courage Best file: Eagle IPA is local 3 so tier 12 stays Leicestershire LRU-only."""
        assert extract_tier("merit/East_Midlands/Eagle_IPA_League.json", "2009-2010") == (
            3,
            "East Midlands 3",
        )
        assert extract_tier("merit/East_Midlands/Eagle_IPA_League.json", "2013-2014") == (
            2,
            "East Midlands 2",
        )
        assert extract_tier("merit/East_Midlands/Youngs_Bitter.json", "2013-2014") == (
            3,
            "East Midlands 3",
        )
        assert extract_tier("merit/East_Midlands/Estrella_Damm.json", "2013-2014") == (
            5,
            "East Midlands 5",
        )
        assert extract_tier("merit/East_Midlands/Youngs_London_Stout.json", "2013-2014") == (
            6,
            "East Midlands 6",
        )
        assert extract_tier("merit/East_Midlands/Dogs_Head_DNA.json", "2013-2014") == (
            7,
            "East Midlands 7",
        )

    def test_rural_kent_invicta_apex_2010_2011(self):
        """2010-2011: Invicta Three is local 1 above Divisions A–C."""
        season = "2010-2011"
        assert extract_tier("merit/Rural_Kent/Invicta_Three.json", season) == (1, "Rural Kent 1")
        assert extract_tier("merit/Rural_Kent/Division_A.json", season) == (2, "Rural Kent 2")
        assert extract_tier("merit/Rural_Kent/Division_B.json", season) == (3, "Rural Kent 3")
        assert extract_tier("merit/Rural_Kent/Division_C.json", season) == (4, "Rural Kent 4")
        assert extract_tier("merit/Rural_Kent/Division_A.json", "2011-2012") == (
            1,
            "Rural Kent 1",
        )

    def test_backslash_path(self):
        """Windows-style backslash paths are normalised."""
        result = extract_tier("merit\\CANDY\\CANDY_1.json", "2025-2026")
        assert result == (1, "CANDY 1")

    def test_unknown_competition_returns_unknown(self):
        result = extract_tier("merit/UnknownComp/Premiership.json", "2025-2026")
        assert result == (999, "Unknown Tier")

    def test_surrey_jonap_stripped(self):
        """2009-2010 JONAP Foundation is local tier 9 (nine-rung ladder); other seasons unchanged."""
        result = extract_tier("merit/Surrey/Surrey_JONAP_Foundation_League.json", "2009-2010")
        assert result == (9, "Surrey 9")
        result_no_jonap = extract_tier("merit/Surrey/Surrey_Foundation.json", "2010-2011")
        assert result_no_jonap == (7, "Surrey 7")

    def test_surrey_jonap_alliance(self):
        assert extract_tier("merit/Surrey/Surrey_JONAP_Alliance.json", "2009-2010") == (
            2,
            "Surrey 2",
        )
        assert extract_tier("merit/Surrey/Surrey_JONAP_Alliance.json", "2010-2011") == (
            3,
            "Surrey 3",
        )

    def test_surrey_premier_filenames_nine_rung_2010_2012(self):
        """2010-2011..2012-2013: Surrey_Premier ladder uses nine locals (conference bands split)."""
        assert extract_tier("merit/Surrey/Surrey_Conference_1.json", "2011-2012") == (
            3,
            "Surrey 3",
        )
        assert extract_tier("merit/Surrey/Surrey_Foundation_League.json", "2010-2011") == (
            9,
            "Surrey 9",
        )
        assert extract_tier("merit/Surrey/Surrey_Conference_1.json", "2013-2014") == (
            3,
            "Surrey 3",
        )
        assert extract_tier("merit/Surrey/Surrey_Foundation_League.json", "2014-2015") == (
            10,
            "Surrey 10",
        )
        assert get_competition_offset("Surrey", "2011-2012") == 12
        assert get_competition_offset("Surrey", "2013-2014") == 10
        assert get_competition_offset("Surrey", "2014-2015") == 10
        assert extract_tier("merit/Surrey/Surrey_Championship.json", "2015-2016") == (
            2,
            "Surrey 2",
        )

    def test_herts_middlesex_compound_suffixes(self):
        """Merit Table 7NE/7SW/6NE/5NE must parse the number despite joined suffix."""
        assert extract_tier("merit/Herts_Middlesex/Merit_Table_7NE.json", "2015-2016") == (
            7,
            "Herts Middlesex 7",
        )
        assert extract_tier("merit/Herts_Middlesex/Merit_Table_7SW.json", "2015-2016") == (
            7,
            "Herts Middlesex 7",
        )
        assert extract_tier("merit/Herts_Middlesex/Merit_Table_6NE.json", "2017-2018") == (
            6,
            "Herts Middlesex 6",
        )
        assert extract_tier("merit/Herts_Middlesex/Merit_Table_5NE.json", "2018-2019") == (
            5,
            "Herts Middlesex 5",
        )

    def test_hampshire_merit_2_local_tier(self):
        """Hampshire 2/3/4 in merit sit directly below pyramid Hampshire 1."""
        assert extract_tier("merit/Hampshire/Hampshire_2.json", "2021-2022") == (
            6,
            "Hampshire 6",
        )
        assert extract_tier("merit/Hampshire/Hampshire_3.json", "2021-2022") == (
            7,
            "Hampshire 7",
        )
        assert extract_tier("merit/Hampshire/Hampshire_4.json", "2021-2022") == (
            8,
            "Hampshire 8",
        )

    def test_five_grain_sponsor_stripped(self):
        """Five_Grain_ sponsor prefix is stripped so 'Five' is not parsed as 5."""
        result = extract_tier("merit/Rural_Kent/Five_Grain_4_East.json", "2018-2019")
        assert result == (4, "Rural Kent 4")


class TestGetNumberFromTierName:
    """Tests for parsing numbers from tier filenames."""

    def test_numeric_1(self):
        assert get_number_from_tier_name("North_1.json", "North") == 1

    def test_word_two(self):
        assert get_number_from_tier_name("North_Two.json", "North") == 2

    def test_no_number(self):
        assert get_number_from_tier_name("North_Premier.json", "North") == 0

    def test_ordinal_first(self):
        assert get_number_from_tier_name("First_Division.json", "") == 1

    def test_ordinal_third(self):
        assert get_number_from_tier_name("Third_Division.json", "") == 3

    def test_ordinal_seventh(self):
        assert get_number_from_tier_name("Seventh_Division.json", "") == 7

    def test_letter_d(self):
        assert get_number_from_tier_name("Leicestershire_Merit_D.json", "Leicestershire_Merit") == 4

    def test_compound_5a(self):
        assert get_number_from_tier_name("Yorkshire_5A.json", "Yorkshire") == 5

    def test_compound_7ne(self):
        assert get_number_from_tier_name("Merit_Table_7NE.json", "Merit_Table") == 7

    def test_compound_6sw(self):
        assert get_number_from_tier_name("Merit_Table_6SW.json", "Merit_Table") == 6


class TestNamedMeritLeagues:
    """Tests for pre-strip named merit league matching.

    Named merit leagues return local tiers with competition-qualified names.
    """

    def test_bombardier_top_tier(self):
        result = extract_tier("merit/East_Midlands/Bombardier_League.json", "2013-2014")
        assert result == (1, "East Midlands 1")

    def test_eagle_ipa_second_tier(self):
        result = extract_tier("merit/East_Midlands/Eagle_IPA_League.json", "2014-2015")
        assert result == (2, "East Midlands 2")

    def test_directors_fourth_tier(self):
        result = extract_tier("merit/East_Midlands/Directors_League.json", "2014-2015")
        assert result == (4, "East Midlands 4")

    def test_estrella_main_ladder_fifth_tier(self):
        result = extract_tier("merit/East_Midlands/Estrella_Damm.json", "2014-2015")
        assert result == (5, "East Midlands 5")

    def test_youngs_bitter_main_ladder_third_tier(self):
        result = extract_tier("merit/East_Midlands/Youngs_Bitter.json", "2014-2015")
        assert result == (3, "East Midlands 3")

    def test_bombardier_merit_table(self):
        result = extract_tier("merit/East_Midlands/Bombardier_Merit_Table.json", "2019-2020")
        assert result == (1, "East Midlands 1")

    def test_directors_merit_table(self):
        result = extract_tier("merit/East_Midlands/Directors_Merit_Table.json", "2019-2020")
        assert result == (4, "East Midlands 4")

    def test_banana_bread_parallel_seventh_tier(self):
        result = extract_tier("merit/East_Midlands/Banana_Bread_Beer_Merit_Table.json", "2019-2020")
        assert result == (7, "East Midlands 7")

    def test_numbered_format_unaffected(self):
        """Post-2022 numbered format should not be caught by named league matching."""
        result = extract_tier(
            "merit/East_Midlands/East_Midlands_2_-_Bedfordshire_(North).json", "2025-2026"
        )
        assert result == (2, "East Midlands 2")

    def test_middlesex_ordinal_divisions(self):
        """Ordinal-named divisions should get distinct local tiers."""
        first = extract_tier("merit/Middlesex/First_Division.json", "2012-2013")
        third = extract_tier("merit/Middlesex/Third_Division.json", "2012-2013")
        seventh = extract_tier("merit/Middlesex/Seventh_Division.json", "2012-2013")
        assert first == (1, "Middlesex 1")
        assert third == (3, "Middlesex 3")
        assert seventh == (7, "Middlesex 7")
