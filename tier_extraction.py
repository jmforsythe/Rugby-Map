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


def extract_tier(path_or_filename: str, season: str = "2025-2026") -> tuple[int, str]:
    """Extract tier from a league path or filename.

    Accepts either a bare filename (``"Premiership.json"``) or a relative path
    that includes the merit competition directory
    (``"merit/CANDY/Conference_1.json"``).  Merit paths are matched by
    ``"merit/<competition>"`` entries in each era's zeroth_tier_map.
    """
    normalized = path_or_filename.replace("\\", "/")
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
        "merit/East_Midlands/East_Midlands": 8,
        "merit/Hampshire/Counties": 6,
        "merit/Herts_Middlesex/Merit_Championship": 11,
        "merit/Herts_Middlesex/Merit_North": 12,
        "merit/Herts_Middlesex/Merit_South": 12,
        "merit/Sussex/Counties": 6,
        "merit/CANDY": 9,
        "merit/Devon": 10,
        "merit/East_Midlands": 10,
        "merit/Eastern_Counties": 8,
        "merit/Essex": 9,
        "merit/GRFU_District": 10,
        "merit/Hampshire": 10,
        "merit/Herts_Middlesex": 10,
        "merit/Leicestershire": 9,
        "merit/Middlesex": 9,
        "merit/Midlands_Reserve": 10,
        "merit/NOWIRUL": 9,
        "merit/Nottinghamshire": 10,
        "merit/Rural_Kent": 8,
        "merit/Sussex": 8,
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
        "NC_Lancashire": 8,
        "NC_Midlands": 8,
        "merit/East_Midlands/East_Midlands": 8,
        "merit/Hampshire/Counties": 6,
        "merit/Herts_Middlesex/Merit_Championship": 11,
        "merit/Herts_Middlesex/Merit_North": 12,
        "merit/Herts_Middlesex/Merit_South": 12,
        "merit/Sussex/Counties": 6,
        "merit/CANDY": 9,
        "merit/Devon": 10,
        "merit/East_Midlands": 10,
        "merit/Eastern_Counties": 8,
        "merit/Essex": 9,
        "merit/GRFU_District": 10,
        "merit/Hampshire": 10,
        "merit/Herts_Middlesex": 10,
        "merit/Leicestershire": 9,
        "merit/Middlesex": 9,
        "merit/Midlands_Reserve": 10,
        "merit/NOWIRUL": 9,
        "merit/Nottinghamshire": 10,
        "merit/Rural_Kent": 8,
        "merit/Sussex": 8,
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
        "D1": 1,
        "D2": 2,
        "D3": 3,
        "D4": 4,
        "D5": 5,
    }
    num = 0
    for part in other_words:
        if part in num_map:
            num = num_map[part]
            break
    return num
