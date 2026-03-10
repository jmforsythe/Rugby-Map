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
    "CANDY": 9,
    "Devon": 10,
    "East_Midlands": 9,
    "Eastern_Counties": 9,
    "Essex": 9,
    "GRFU_District": 10,
    "Hampshire": 11,
    "Herts_Middlesex": 10,
    "Leicestershire": 9,
    "Middlesex": 10,
    "Midlands_Reserve": 9,
    "NOWIRUL": 10,
    "Nottinghamshire": 10,
    "Rural_Kent": 10,
    "Surrey": 10,
    "Sussex": 8,
}


def get_competition_offset(comp_name: str, season: str = "") -> int:
    """Return the pyramid tier offset for a merit competition.

    Adding this offset to a local tier number gives the absolute pyramid position.
    """
    return COMPETITION_OFFSETS.get(comp_name, 0)


def mens_current_tier_name(tier: int) -> str:
    """Return the display name for an absolute men's pyramid tier number."""
    if tier in MENS_CURRENT_TIER_NAMES:
        return MENS_CURRENT_TIER_NAMES[tier]
    if 3 <= tier <= 4:
        return f"National League {tier - 2}"
    if 5 <= tier <= 6:
        return f"Regional {tier - 4}"
    if 7 <= tier <= 11:
        return f"Counties {tier - 6}"
    return f"Level {tier}"


def _merit_tier_name(comp_display: str, local_tier: int) -> str:
    """Return a display name for a merit competition tier."""
    if local_tier <= 0:
        return f"{comp_display} Premier"
    return f"{comp_display} {local_tier}"


def _womens_current_tier_name(tier: int) -> str:
    if tier in WOMENS_CURRENT_TIER_NAMES:
        return WOMENS_CURRENT_TIER_NAMES[tier]
    if 102 <= tier <= 103:
        return f"Championship {tier - 101}"
    return f"National Challenge {tier - 103}"


_NAMED_MERIT_LEAGUES: dict[str, int] = {
    # Values are **local** tiers (absolute minus competition offset).
    # East Midlands (offset 9): Bombardier (0) > Eagle IPA/etc (2) > Directors/rest (3)
    "merit/East_Midlands/Bombardier": 0,
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
    # East Midlands Northants A & B: both geographic splits of EM Division 2
    "merit/East_Midlands/East_Midlands_2_-_Northants_A": 2,
    "merit/East_Midlands/East_Midlands_2_-_Northants_B": 2,
    # NOWIRUL (offset 10): Premier (-1) > Championship/Conference A (0) >
    # Conference B (1); generic Divisions use zeroth_tier_map offset 0
    "merit/NOWIRUL/Cotton_Traders_Premier": -1,
    "merit/NOWIRUL/NOWIRUL_Cotton_Traders_Premier": -1,
    "merit/NOWIRUL/Cotton_Traders_Championship": 0,
    "merit/NOWIRUL/NOWIRUL_Cotton_Traders_Championship": 0,
    "merit/NOWIRUL/Cotton_Traders_Conference_A": 0,
    "merit/NOWIRUL/Cotton_Traders_Conference_B": 1,
    # Hampshire (offset 11): Senior Merit (-1)
    "merit/Hampshire/Hampshire_Senior": -1,
    # Leicestershire (offset 9): Invitation Merit (1)
    "merit/Leicestershire/Leicestershire_Invitation": 1,
    # Surrey (offset 10): Premier (0) > Championship (1) > Alliance (2) >
    # Conference (3) > Combination 1 (4) > Combination 2 (5) >
    # Combination 3 / Foundation (6)
    "merit/Surrey/Surrey_Premier": 0,
    "merit/Surrey/Surrey_Chamionship": 1,
    "merit/Surrey/Surrey_Championship": 1,
    "merit/Surrey/Surrey_Alliance": 2,
    "merit/Surrey/Surrey_East_Conference": 3,
    "merit/Surrey/Surrey_West_Conference": 3,
    "merit/Surrey/Surrey_Conference": 3,
    "merit/Surrey/Surrey_Combination_1": 4,
    "merit/Surrey/Surrey_Combination_2": 5,
    "merit/Surrey/Surrey_Combination_3": 6,
    "merit/Surrey/Surrey_Foundation": 6,
    "merit/Surrey/London_Counties": 0,
    "merit/Surrey/London_1_South": 0,
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
        "merit/Hampshire/Counties": -5,
        "merit/Herts_Middlesex/Merit_Championship": 1,
        "merit/Herts_Middlesex/Merit_North": 2,
        "merit/Herts_Middlesex/Merit_South": 2,
        "merit/Sussex/Counties": -2,
        "merit/CANDY": 0,
        "merit/Devon": 0,
        "merit/East_Midlands": 1,
        "merit/Eastern_Counties": 0,
        "merit/Essex": 0,
        "merit/GRFU_District": 0,
        "merit/Hampshire/Solent": -1,
        "merit/Hampshire": 0,
        "merit/Herts_Middlesex": 0,
        "merit/Leicestershire": 0,
        "merit/Middlesex": 0,
        "merit/Midlands_Reserve": 0,
        "merit/NOWIRUL": 0,
        "merit/Nottinghamshire": 0,
        "merit/Rural_Kent": 0,
        "merit/Surrey": 0,
        "merit/Sussex": 0,
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

    zeroth_tier_map = {
        "National_League": 2,
        "North_Lancs_Cumbria": 7,
        "North_Lancashire": 7,
        "North": 5,
        "Midlands": 5,
        "London": 5,
        "South_West_Pilot": 6,
        "South_West": 5,
        "Cumbria": (6 if season >= "2018-2019" else 8),
        "Durham_Northumberland": 6,
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
        "Dorset_&_Wilts": 8,
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
        # Merit entries: values are local offsets (absolute - COMPETITION_OFFSETS)
        "merit/East_Midlands/East_Midlands": 0,
        "merit/Hampshire/Counties": -5,
        "merit/Herts_Middlesex/Merit_Championship": 1,
        "merit/Herts_Middlesex/Merit_North": 2,
        "merit/Herts_Middlesex/Merit_South": 2,
        "merit/Sussex/Counties": -2,
        "merit/CANDY": 0,
        "merit/Devon": 0,
        "merit/East_Midlands": 1,
        "merit/Eastern_Counties": 0,
        "merit/Essex": 0,
        "merit/GRFU_District": 0,
        "merit/Hampshire/Solent": -1,
        "merit/Hampshire": 0,
        "merit/Herts_Middlesex": 0,
        "merit/Leicestershire": 0,
        "merit/Middlesex": 0,
        "merit/Midlands_Reserve": 0,
        "merit/NOWIRUL": 0,
        "merit/Nottinghamshire": 0,
        "merit/Rural_Kent": 0,
        "merit/Surrey": 0,
        "merit/Sussex": 0,
    }
    if filename.startswith("Premiership"):
        return (1, "Premiership")
    if filename.startswith("Championship"):
        return (2, "Championship")
    if filename.startswith("National_League_1"):
        return (3, "National League 1")
    if filename.startswith("National_League_2"):
        return (4, "National League 2")
    for prefix, offset in zeroth_tier_map.items():
        if filename.startswith(prefix):
            num = get_number_from_tier_name(filename, prefix)
            if (
                prefix == "Berks_Bucks_&_Oxon"
                and season <= "2018-2019"
                and "Premier" not in filename
            ):
                num += 1
            tier = offset + num
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
    if filename.startswith("RFUW_"):
        filename = filename.replace("RFUW_", "Women's_")
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
    }
    num = 0
    for part in other_words:
        if part in num_map:
            num = num_map[part]
            break
    return num
