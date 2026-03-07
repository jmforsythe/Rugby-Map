"""
Tier extraction logic for mapping league filenames to tier numbers and names.

Supports both current (2022+) and historical filename formats for men's and women's leagues.
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


def _mens_current_tier_name(tier: int) -> str:
    if tier in MENS_CURRENT_TIER_NAMES:
        return MENS_CURRENT_TIER_NAMES[tier]
    if 3 <= tier <= 4:
        return f"National League {tier - 2}"
    if 5 <= tier <= 6:
        return f"Regional {tier - 4}"
    if 7 <= tier <= 11:
        return f"Counties {tier - 6}"
    return f"Level {tier}"


def _womens_current_tier_name(tier: int) -> str:
    if tier in WOMENS_CURRENT_TIER_NAMES:
        return WOMENS_CURRENT_TIER_NAMES[tier]
    if 102 <= tier <= 103:
        return f"Championship {tier - 101}"
    return f"National Challenge {tier - 103}"


def extract_tier(filename: str, season: str = "2025-2026") -> tuple[int, str]:
    tier = extract_tier_men(filename, season)
    if tier is None:
        tier = extract_tier_women(filename, season)
    if tier is None:
        logger.warning("Could not extract tier from filename: %s for season: %s", filename, season)
        return (999, "Unknown Tier")
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
    "Eagle_IPA_",
    "Waterfall_",
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
        "Counties": 6,
        "Bristol_&_District": 10,
        "CANDY": 9,
        "Devon_Merit": 10,
        "District_Premier": 10,
        "Division": 9,
        "East_Midlands": 9 if "B" in filename.removesuffix(".json").split("_") else 8,
        "Eastern_Counties": 8,
        "Gloucester_&_District": 10,
        "Hampshire_Premiership": 11,
        "Kent": 10,
        "Leicestershire_Merit": 9,
        "Merit_Championship": 11,
        "Merit_North": 12,
        "Merit_Premier": 10,
        "Merit_South": 12,
        "Merit": 10,
        "Midlands_Reserve_League": 10,
        "NOWIRUL": 9,
        "Premier_Division": 9,
        "Table": 10,
    }
    for prefix, offset in zeroth_tier_map.items():
        if cleaned.startswith(prefix):
            num = get_number_from_tier_name(cleaned, prefix)
            tier = offset + num
            return (tier, _mens_current_tier_name(tier))

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
        "Bristol_&_District": 10,
        "CANDY": 9,
        "Devon_Merit": 10,
        "District_Premier": 10,
        "Divsion": 9,
        "Division": 9,
        "East_Midlands": 8,
        "Gloucester_&_District": 10,
        "Hampshire_Premiership": 11,
        "Leicestershire_Merit": 9,
        "Merit_Championship": 11,
        "Merit_North": 12,
        "Merit_Premier": 10,
        "Merit_South": 12,
        "Merit": 10,
        "Midlands_Reserve_League": 10,
        "NC_Lancashire": 8,
        "NC_Midlands": 8,
        "NOWIRUL": 9,
        "Cotton_Traders_Premier": 9,
        "Cotton_Traders_Championship": 10,
        "Cotton_Traders_Conference": 10,
        "Cinque": 8,
        "Five_Grain": 8,
        "Late_Red": 8,
        "Estrella_Damm": 10,
        "Youngs_Bitter": 10,
        "League": 10,
        "Solent": 10,
        "Premier_Division": 9,
        "Premier": 8,
        "Security_Plus_Pennant": 10,
        "Table": 10,
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
    other_words = filename.removesuffix(".json")[len(prefix) :].removeprefix("_").split("_")
    num_map = {
        "1": 1,
        "One": 1,
        "A": 1,
        "2": 2,
        "Two": 2,
        "B": 2,
        "3": 3,
        "Three": 3,
        "C": 3,
        "4": 4,
        "Four": 4,
        "5": 5,
        "Five": 5,
        "6": 6,
        "Six": 6,
        "7": 7,
        "Seven": 7,
        "8": 8,
        "Eight": 8,
        "9": 9,
        "Nine": 9,
    }
    num = 0
    for part in other_words:
        if part in num_map:
            num = num_map[part]
            break
    return num
