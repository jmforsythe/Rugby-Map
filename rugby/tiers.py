"""
Tier extraction logic for mapping league filenames to tier numbers and names.

Supports both current (2022+) and historical filename formats for men's and women's leagues.

Merit competitions return **local** tier numbers (1-based within the competition).
Use :func:`get_competition_offset` to translate back to absolute pyramid positions.
"""

import logging

logger = logging.getLogger(__name__)

MENS_CURRENT_TIER_NAMES: dict[int, str] = {
    1: "Premiership",
    2: "Championship",
}

WOMENS_CURRENT_TIER_NAMES: dict[int, str] = {
    101: "Premiership Women's",
}

COMPETITION_OFFSETS: dict[str, int] = {
    "CANDY": 8,
    "Devon": 9,
    "East_Midlands": 8,
    "Eastern_Counties": 8,
    "Essex": 8,
    "GRFU_District": 9,
    "Hampshire": 5,
    "Herts_Middlesex": 9,
    "Leicestershire": 8,
    "Middlesex": 9,
    "Midlands_Reserve": 8,
    "NOWIRUL": 8,
    "Nottinghamshire": 9,
    "Rural_Kent": 9,
    "Surrey": 9,
    "Sussex": 5,
}

# Season-specific overrides: (start_season, end_season, offset).
# Empty end_season means open-ended (all subsequent seasons).
# Checked before COMPETITION_OFFSETS when a season is provided.
_SEASON_OFFSETS: dict[str, list[tuple[str, str, int]]] = {
    "CANDY": [
        ("2022-2023", "", 9),
    ],
    "Devon": [
        ("2009-2010", "2009-2010", 8),
        ("2012-2013", "2013-2014", 10),
        ("2017-2018", "2017-2018", 10),
    ],
    "East_Midlands": [
        ("2009-2010", "2009-2010", 11),
    ],
    "Essex": [
        ("2008-2009", "2008-2009", 11),
        ("2010-2011", "2013-2014", 10),
        ("2014-2015", "2022-2023", 9),
    ],
    "GRFU_District": [
        ("2010-2011", "2011-2012", 11),
        ("2012-2013", "2015-2016", 12),
        ("2016-2017", "2019-2020", 11),
        ("2021-2022", "", 10),
    ],
    "Hampshire": [
        ("2008-2009", "2008-2009", 7),
        ("2009-2010", "2016-2017", 6),
        ("2022-2023", "2022-2023", 6),
        ("2025-2026", "2025-2026", 6),
    ],
    "Herts_Middlesex": [
        ("2008-2009", "2009-2010", 12),
        ("2010-2011", "2010-2011", 10),
        ("2011-2012", "2013-2014", 11),
        ("2017-2018", "2019-2020", 10),
        ("2021-2022", "2021-2022", 10),
        ("2023-2024", "2023-2024", 10),
    ],
    "Leicestershire": [
        ("2009-2010", "2009-2010", 11),
        ("2023-2024", "2023-2024", 7),
        ("2025-2026", "2025-2026", 9),
    ],
    "Middlesex": [
        ("2025-2026", "2025-2026", 8),
    ],
    "NOWIRUL": [
        ("2010-2011", "2014-2015", 9),
        ("2017-2018", "2017-2018", 9),
        ("2018-2019", "2018-2019", 10),
        ("2019-2020", "2019-2020", 9),
        ("2021-2022", "2021-2022", 7),
        ("2022-2023", "2023-2024", 10),
        ("2024-2025", "2025-2026", 9),
    ],
    "Nottinghamshire": [
        ("2009-2010", "2009-2010", 11),
    ],
    "Rural_Kent": [
        ("2008-2009", "2010-2011", 10),
        ("2011-2012", "2012-2013", 8),
        ("2013-2014", "2018-2019", 11),
        ("2019-2020", "2019-2020", 8),
        ("2021-2022", "2021-2022", 8),
        ("2022-2023", "2022-2023", 10),
        ("2023-2024", "", 11),
    ],
    "Surrey": [
        ("2009-2010", "2012-2013", 12),
        ("2015-2016", "2018-2019", 11),
        ("2019-2020", "2019-2020", 12),
        ("2021-2022", "2021-2022", 12),
        ("2022-2023", "", 11),
    ],
    "Sussex": [
        ("2022-2023", "", 6),
    ],
}


def get_competition_offset(comp_name: str, season: str = "") -> int:
    """Return the pyramid tier offset for a merit competition.

    Adding this offset to a local tier number gives the absolute pyramid position.
    Checks season-specific overrides before falling back to the default.
    """
    if season:
        ranges = _SEASON_OFFSETS.get(comp_name)
        if ranges:
            for start, end, offset in ranges:
                if season >= start and (not end or season <= end):
                    return offset
    return COMPETITION_OFFSETS.get(comp_name, 0)


def mens_current_tier_name(tier: int, season: str = "") -> str:
    """Return the display name for an absolute men's pyramid tier number.

    Before 2008-2009 the Championship (tier 2) did not exist; tiers 2-4 were
    National League 1 through 3.
    """
    pre_champ = bool(season) and season < "2009-2010"
    if tier == 1:
        return "Premiership"
    if pre_champ and 2 <= tier <= 4:
        return f"National League {tier - 1}"
    if tier == 2:
        return "Championship"
    if 3 <= tier <= 4:
        return f"National League {tier - 2}"
    if 5 <= tier <= 6:
        return f"Regional {tier - 4}"
    if 7 <= tier <= 11:
        return f"Counties {tier - 6}"
    return f"Level {tier}"


def _merit_tier_name(comp_display: str, local_tier: int) -> str:
    """Return a display name for a merit competition tier."""
    return f"{comp_display} {local_tier}"


def _womens_current_tier_name(tier: int) -> str:
    if tier in WOMENS_CURRENT_TIER_NAMES:
        return WOMENS_CURRENT_TIER_NAMES[tier]
    if 102 <= tier <= 103:
        return f"Championship {tier - 101}"
    return f"National Challenge {tier - 103}"


_WOMENS_FILENAME_OVERRIDES: dict[str, tuple[int, str]] = {
    "NC_Midlands_Merit.json": (104, "National Challenge 1"),
    "NC_Lancashire_Merit.json": (104, "National Challenge 1"),
    "Eastern_Counties_Women's_Merit_Table.json": (104, "National Challenge 1"),
}

# Men's end-of-season play-off fixture files (RFU competition 2319) under
# fixture_data/<season>/.
#
# Basenames match ``clean_filename(RFU competition title)`` from the fixtures/results page
# (same rule as ``rugby.scrape`` league JSON). When RFU retitles a play-off, filenames change —
# add the new basename here (and/or keep old keys for historical fixture_data trees).
#
# Must run before generic ``Regional`` / ``National_League`` filename matching.
_PLAYOFF_FIXTURE_FILENAME_OVERRIDES: dict[str, tuple[int, str]] = {
    "Championship_Relegation_and_National_1_Promotion.json": (
        3,
        "Play-off: Championship Relegation / National League 1 Promotion",
    ),
    "National_1_Relegation_and_National_2_Promotion.json": (
        4,
        "Play-off: National League 1 Relegation / National League 2 Promotion",
    ),
    "National_Two_Relegation_and_Regional_1_Promotion.json": (
        5,
        "Play-off: National League 2 Relegation / Regional 1 Promotion",
    ),
    "Regional_1_Relegation_and_Regional_2_Promotion.json": (
        6,
        "Play-off: Regional 1 Relegation / Regional 2 Promotion",
    ),
    "Regional_2_Relegation.json": (
        6,
        "Play-off: Regional 2 Relegation",
    ),
}


_NAMED_MERIT_LEAGUES: dict[str, int] = {
    # Values are **local** tiers (absolute minus competition offset).
    # East Midlands (offset 8): Bombardier (1) > Eagle IPA/etc (2) > Directors/rest (3)
    "merit/East_Midlands/Bombardier": 1,
    "merit/East_Midlands/Eagle_IPA": 2,
    "merit/East_Midlands/Youngs_Bitter": 2,
    "merit/East_Midlands/Youngs_London_Gold": 2,
    "merit/East_Midlands/Estrella_Damm_Merit": 3,
    "merit/East_Midlands/Estrella": 2,
    "merit/East_Midlands/Directors": 3,
    "merit/East_Midlands/Courage": 3,
    "merit/East_Midlands/Youngs_London_Stout": 3,
    "merit/East_Midlands/Youngs": 3,
    "merit/East_Midlands/Waggledance": 3,
    "merit/East_Midlands/Red_Stripe": 3,
    "merit/East_Midlands/Winter_Warmer": 3,
    "merit/East_Midlands/Banana_Bread": 3,
    "merit/East_Midlands/Dogs_Head": 3,
    # East Midlands Northants A & B: B sits one level below A
    "merit/East_Midlands/East_Midlands_2_-_Northants_A": 2,
    "merit/East_Midlands/East_Midlands_2_-_Northants_B": 3,
    # NOWIRUL (offset 8): Premier (1) > Championship/Conference A (2) >
    # Conference B (3); generic Divisions use zeroth_tier_map offset 2
    "merit/NOWIRUL/Cotton_Traders_Premier": 1,
    "merit/NOWIRUL/NOWIRUL_Cotton_Traders_Premier": 1,
    "merit/NOWIRUL/NOWIRUL_COTTON_TRADERS_PREMIER": 1,
    "merit/NOWIRUL/Bateman_BMW_Premier": 1,
    "merit/NOWIRUL/NOWIRUL_BATHTIME_PREMIER": 1,
    "merit/NOWIRUL/Cotton_Traders_Championship": 2,
    "merit/NOWIRUL/NOWIRUL_Cotton_Traders_Championship": 2,
    "merit/NOWIRUL/Cotton_Traders_Conference_A": 2,
    "merit/NOWIRUL/Cotton_Traders_Conference_B": 3,
    # Hampshire (offset 5): Senior Merit (5); Hampshire 2/3/4 sit directly
    # below pyramid Hampshire 1 (abs 10), not at offset+number.
    "merit/Hampshire/Hampshire_Senior": 5,
    "merit/Hampshire/Hampshire_2": 6,
    "merit/Hampshire/Hampshire_3": 7,
    "merit/Hampshire/Hampshire_4": 8,
    # Leicestershire (offset 8): Invitation Merit (2)
    "merit/Leicestershire/Leicestershire_Invitation": 2,
    # Surrey (offset 9): Premier (1) > Championship (2) > Alliance (3) >
    # Conference (4) > Combination 1 (5) > Combination 2 (6) >
    # Combination 3 / Foundation (7)
    "merit/Surrey/Surrey_Premier": 1,
    "merit/Surrey/Surrey_Chamionship": 2,
    "merit/Surrey/Surrey_Championship": 2,
    "merit/Surrey/Surrey_Alliance": 3,
    "merit/Surrey/Surrey_East_Conference": 4,
    "merit/Surrey/Surrey_West_Conference": 4,
    "merit/Surrey/Surrey_Conference": 4,
    "merit/Surrey/Surrey_Combination_1": 5,
    "merit/Surrey/Surrey_Combination_2": 6,
    "merit/Surrey/Surrey_Combination_3": 7,
    "merit/Surrey/Surrey_Foundation": 7,
    "merit/Surrey/Surrey_JONAP_Premier": 1,
    "merit/Surrey/Surrey_JONAP_Alliance": 3,
    "merit/Surrey/Surrey_JONAP_Conference": 4,
    "merit/Surrey/Surrey_JONAP_Combination_1": 5,
    "merit/Surrey/Surrey_JONAP_Combination_2": 6,
    "merit/Surrey/Surrey_JONAP_Combination_3": 7,
    "merit/Surrey/Surrey_JONAP_Foundation": 7,
    "merit/Surrey/London_Counties": 1,
    "merit/Surrey/London_1_South": 1,
    # Herts & Middlesex (offset 9): numbered North/South divisions sit directly
    # below Championship (local 1), one tier above the unnumbered variants.
    "merit/Herts_Middlesex/Merit_North_1": 2,
    "merit/Herts_Middlesex/Merit_North_2": 3,
    "merit/Herts_Middlesex/Merit_South_1": 2,
}


def _match_named_merit_leagues(path: str, season: str) -> tuple[int, str] | None:
    """Match merit paths with sponsor-named leagues before sponsor stripping.

    East Midlands merit leagues use sponsor names (Bombardier, Eagle IPA, etc.)
    as league identifiers. Sponsor stripping destroys this identity, so we match
    the original path first to assign distinct local tiers within the hierarchy.
    """
    for prefix, local_tier in _NAMED_MERIT_LEAGUES.items():
        if path.startswith(prefix):
            return (local_tier, f"Level {local_tier}")
    return None


def extract_tier(path_or_filename: str, season: str = "2025-2026") -> tuple[int, str]:
    """Extract tier from a league path or filename.

    Accepts either a bare filename (``"Premiership.json"``) or a relative path
    that includes the merit competition directory
    (``"merit/CANDY/Conference_1.json"``).  Merit paths are matched by
    ``"merit/<competition>"`` entries in each era's zeroth_tier_map.

    For merit paths, the returned tier number is **local** to the competition
    (1-based) and the name is competition-qualified (e.g. ``"Essex 2"``).
    """
    normalized = path_or_filename.replace("\\", "/")
    filename = normalized.split("/")[-1]

    override = _WOMENS_FILENAME_OVERRIDES.get(filename)
    if override is not None:
        return override

    playoff = _PLAYOFF_FIXTURE_FILENAME_OVERRIDES.get(filename)
    if playoff is not None:
        return playoff

    is_merit = normalized.startswith("merit/")

    result = _match_named_merit_leagues(normalized, season)
    if result is not None:
        if is_merit:
            comp = normalized.split("/")[1].replace("_", " ")
            return (result[0], _merit_tier_name(comp, result[0]))
        return result

    parts = normalized.split("/")
    parts[-1] = _strip_sponsor_prefix(parts[-1])
    cleaned = "/".join(parts)

    tier = extract_tier_men(cleaned, season)
    if tier is None:
        tier = extract_tier_women(cleaned, season)
    if tier is None:
        logger.warning(
            "Could not extract tier from: %s for season: %s",
            path_or_filename,
            season,
        )
        return (999, "Unknown Tier")

    if is_merit:
        comp = normalized.split("/")[1].replace("_", " ")
        return (tier[0], _merit_tier_name(comp, tier[0]))
    return tier


def extract_tier_men(filename: str, season: str) -> tuple[int, str] | None:
    season_start_year = int(season.split("-")[0])
    if season_start_year <= 2021:
        return extract_tier_men_pre_2021(filename, season)
    else:
        return extract_tier_men_current(filename, season)


def extract_tier_women(filename: str, season: str) -> tuple[int, str] | None:
    season_start_year = int(season.split("-")[0])
    if season_start_year <= 2018:
        return extract_tier_women_pre_2018(filename, season)
    else:
        return extract_tier_women_current(filename, season)


_SPONSOR_PREFIXES = [
    "Harvey's_Brewery_",
    "Harvey's_Wharf_IPA_",
    "Harvey's_Olympia_",
    "Harvey's_of_",
    "Harvey\u2019s_Brewery_",
    "Bateman_BMW_",
    "Cotton_Traders_",
    "Group_1_Automotive_",
    "Tribute_",
    "Wadworth_",
    "Greene_King_IPA_",
    "Shepherd_Neame_",
    "6X_",
    "Snows_Group_",
    "SSE_",
    "Alban_Wise_Insurance_",
    "Ellis_Mediation_",
    "Fill_Your_Boots_",
    "Webb_Ellis_",
    "Banana_Bread_Beer_",
    "Bombardier_",
    "Directors_",
    "Estrella_Damm_",
    "Youngs_London_Stout_",
    "Youngs_London_Gold_",
    "Sale_Sharks_",
    "County_Courier_Services_",
    "Howell_&_Co_",
    "INDEPENDENT_E-NRG_",
    "Waterfall_",
    "Freshnet_",
    "Raging_Bull_",
    "UBS_",
    "Howell&_Co_",
    "High_Bridge_Jewellers_",
    "Euromanx_",
    "Five_Grain_",
]


def _strip_sponsor_prefix(filename: str) -> str:
    """Remove known sponsor prefixes from league filenames."""
    for prefix in _SPONSOR_PREFIXES:
        if filename.startswith(prefix):
            filename = filename.removeprefix(prefix)
    return filename


def extract_tier_men_current(filename: str, season: str) -> tuple[int, str] | None:
    """Extract tier from 2022-2023 onwards filename format."""
    if filename == "Premiership.json":
        return (1, "Premiership")
    if filename == "Championship.json":
        return (2, "Championship")

    cleaned = _strip_sponsor_prefix(filename)

    zeroth_tier_map = {
        "National_League": 2,
        "Regional": 4,
        "Cumbria_Conference": 7,
        "Counties": 6,
        # Merit entries: values are local offsets (absolute - COMPETITION_OFFSETS)
        "merit/East_Midlands/East_Midlands": 0,
        "merit/Hampshire/Counties": 0,
        "merit/Herts_Middlesex/Merit_Championship": 1,
        "merit/Herts_Middlesex/Merit_North": 2,
        "merit/Herts_Middlesex/Merit_South": 2,
        "merit/Sussex/Counties": 0,
        "merit/CANDY": 0,
        "merit/Devon": (1 if season >= "2025-2026" else 0),
        "merit/East_Midlands": 1,
        "merit/Eastern_Counties": 0,
        "merit/Essex": 0,
        "merit/GRFU_District": 0,
        "merit/Hampshire/Solent": 5,
        "merit/Hampshire": 6,
        "merit/Herts_Middlesex": 0,
        "merit/Leicestershire": 0,
        "merit/Middlesex": 1,
        "merit/Midlands_Reserve": 0,
        "merit/NOWIRUL/Premier": 1,
        "merit/NOWIRUL": (2 if "2016-2017" <= season <= "2019-2020" else 1),
        "merit/Nottinghamshire": 0,
        "merit/Rural_Kent": 0,
        "merit/Surrey": 1,
        "merit/Sussex": 3,
    }
    for prefix, offset in zeroth_tier_map.items():
        if cleaned.startswith(prefix):
            num = get_number_from_tier_name(cleaned, prefix)
            tier = offset + num
            return (tier, mens_current_tier_name(tier))

    return None


def extract_tier_women_current(filename: str, season: str) -> tuple[int, str] | None:
    """Extract tier from 2019-2020 onwards filename format."""
    if filename.startswith("Women's_Premiership"):
        return (101, "Premiership Women's")

    zeroth_tier_map = {
        "Women's_Championship": 101,
        "Women's_NC": 103,
    }
    for prefix, offset in zeroth_tier_map.items():
        if filename.startswith(prefix):
            num = get_number_from_tier_name(filename, prefix)
            tier = offset + num
            return (tier, _womens_current_tier_name(tier))
    return None


def extract_tier_men_pre_2021(filename: str, season: str) -> tuple[int, str] | None:
    """Extract tier from 2021-2022 and earlier filename format."""
    filename = _strip_sponsor_prefix(filename)
    if filename.startswith("x") and len(filename) > 1 and filename[1].isupper():
        filename = filename[1:]

    pre_champ = season < "2009-2010"

    zeroth_tier_map = {
        "National_League": (1 if pre_champ else 2),
        "North_Midlands": 8,
        "North_Mids": 8,
        "North_Lancs_Cumbria": 7,
        "North_Lancashire": 7,
        "North_Lancs": 7,
        "North": 5,
        "Midlands_East_(South)_A": 11,
        "Midlands_East_(South)_B": 11,
        "Midlands_East_(North)_A": 11,
        "Midlands_East_(North)_B": 11,
        "Midlands": 5,
        "London": 5,
        "South_West_Pilot": 6,
        "South_West": 5,
        "Cumbria": (6 if season >= "2018-2019" else 8),
        "Durham_Northumberland": 6,
        "Durham_N'thm'land": 6,
        "Essex": 8,
        "Eastern_Counties": 8,
        "Hampshire": (9 if season >= "2018-2019" else 8),
        "Sussex": 8,
        "Herts_Middlesex": 8,
        "Kent": 8,
        "Surrey": 8,
        "Berks_Bucks_&_Oxon": 8,
        "Cornwall_Devon": 8,
        "Cornwall": 8,
        "Devon": 8,
        "Dorset_&_Wilts": 7,
        "Dorset": 7,
        "Gloucester": 8,
        "Somerset": 8,
        "Southern_Counties": 7,
        "Western_Counties": 7,
        "Yorkshire": 6,
        "Lancs_Cheshire": (7 if season >= "2018-2019" else 6),
        "South_Lancs_Cheshire": 6,
        "Lancashire_(North)": 8,
        "Cheshire": 8,
        "Merseyside": 8,
        "NC_Lancashire": 8,
        "NC_Midlands": 8,
        "Staffordshire": 8,
        "Warwickshire": 8,
        "NLD_Leics": 8,
        "NLD_N_Leics": 8,
        "Derbys_N_Leics": 8,
        "East_Mids_S_Leics": 8,
        "East_Midlands": 8,
        "Notts_Lincs": 8,
        "East_Counties": 8,
        # Merit entries: values are local offsets (absolute - COMPETITION_OFFSETS)
        "merit/East_Midlands/East_Midlands": 0,
        "merit/Hampshire/Counties": 0,
        "merit/Herts_Middlesex/Merit_Championship": 1,
        "merit/Herts_Middlesex/Merit_North": 2,
        "merit/Herts_Middlesex/Merit_South": 2,
        "merit/Sussex/Counties": 0,
        "merit/CANDY": 0,
        "merit/Devon": 0,
        "merit/East_Midlands": 1,
        "merit/Eastern_Counties": 0,
        "merit/Essex": 0,
        "merit/GRFU_District": 0,
        "merit/Hampshire/Solent": 5,
        "merit/Hampshire": (5 if season < "2014-2015" else 6),
        "merit/Herts_Middlesex": 0,
        "merit/Leicestershire": (1 if season < "2015-2016" else 0),
        "merit/Middlesex": 0,
        "merit/Midlands_Reserve": 0,
        "merit/NOWIRUL/Premier": 1,
        "merit/NOWIRUL": (2 if "2016-2017" <= season <= "2019-2020" else 1),
        "merit/Nottinghamshire": 0,
        "merit/Rural_Kent": 0,
        "merit/Surrey": 1,
        "merit/Sussex": 3,
    }
    if filename.startswith("Premiership"):
        return (1, "Premiership")
    if filename.startswith("Championship"):
        return (2, "Championship")
    for prefix, offset in zeroth_tier_map.items():
        if filename.startswith(prefix):
            num = get_number_from_tier_name(filename, prefix)
            if (
                prefix == "Berks_Bucks_&_Oxon"
                and season < "2005-2006"
                and "Premier" not in filename
            ):
                num -= 1
            tier = offset + num
            main_pyramid_prefixes = {
                "North",
                "Midlands",
                "London",
                "South_West",
                "South_West_Pilot",
            }
            if pre_champ and prefix in main_pyramid_prefixes:
                tier -= 1
            if prefix == "National_League":
                return (tier, f"National League {num}")
            return (tier, f"Level {tier}")
    return None


def extract_tier_women_pre_2018(filename: str, season: str) -> tuple[int, str] | None:
    if season < "2012-2013":
        return extract_tier_women_pre_2012(filename, season)
    if filename.startswith("Women's_Premiership"):
        return (101, "Premiership Women's")
    if filename.startswith("Women's_Championship"):
        if "2" in filename:
            return (103, "Championship 2")
        else:
            return (102, "Championship 1")
    num = get_number_from_tier_name(filename, "")
    if filename.startswith("Women") and num != 0:
        return (103 + num, f"National Challenge {num}")
    return None


def extract_tier_women_pre_2012(filename: str, season: str) -> tuple[int, str] | None:
    if filename.startswith("x") and len(filename) > 1:
        filename = filename[1:]
    if filename.startswith("RFUW_"):
        filename = filename.replace("RFUW_", "Women's_")
    if filename.startswith("wPrem"):
        filename = "Women's_Premiership.json"
    elif filename.startswith("w") and not filename.startswith("Women"):
        filename = "Women's_NC_" + filename[1:]
    if filename.startswith("NC_"):
        filename = "Women's_" + filename
    if filename.endswith("A.json"):
        filename = filename.removesuffix("A.json") + "1.json"
    elif filename.endswith("B.json"):
        filename = filename.removesuffix("B.json") + "2.json"
    return extract_tier_women_pre_2018(filename, "2012-2013")


def get_number_from_tier_name(filename: str, prefix: str) -> int:
    other_words = filename.removesuffix(".json")[len(prefix) :].lstrip("/_").split("_")
    num_map = {
        "Prem": 0,
        "Premier": 0,
        "1": 1,
        "One": 1,
        "First": 1,
        "A": 1,
        "Championship": 1,
        "2": 2,
        "Two": 2,
        "Second": 2,
        "B": 2,
        "3": 3,
        "Three": 3,
        "Third": 3,
        "C": 3,
        "4": 4,
        "Four": 4,
        "Fourth": 4,
        "D": 4,
        "5": 5,
        "Five": 5,
        "Fifth": 5,
        "6": 6,
        "Six": 6,
        "Sixth": 6,
        "7": 7,
        "Seven": 7,
        "Seventh": 7,
        "8": 8,
        "Eight": 8,
        "9": 9,
        "Nine": 9,
        "D1": 1,
        "D2": 2,
        "D3": 3,
        "D4": 4,
        "D5": 5,
        "1N": 1,
        "1S": 1,
        "2N": 2,
        "2S": 2,
        "2NE": 2,
        "2SW": 2,
        "3N": 3,
        "3S": 3,
        "3NE": 3,
        "3SW": 3,
        "4N": 4,
        "4S": 4,
        "4NE": 4,
        "4SW": 4,
        "5A": 5,
        "5B": 5,
        "5C": 5,
        "5N": 5,
        "5S": 5,
        "5NE": 5,
        "5SW": 5,
        "6N": 6,
        "6S": 6,
        "6NE": 6,
        "6SW": 6,
        "7N": 7,
        "7S": 7,
        "7NE": 7,
        "7SW": 7,
    }
    num = 0
    for part in other_words:
        if part in num_map:
            num = num_map[part]
            break
    return num
