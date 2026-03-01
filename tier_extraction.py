"""
Tier extraction logic for mapping league filenames to tier numbers and names.

Supports both current (2022+) and historical filename formats for men's and women's leagues.
"""

MENS_TIERS_CURRENT: list[tuple[str, int, str]] = [
    ("Premiership", 1, "Premiership"),
    ("Championship", 2, "Championship"),
    ("National_League_1", 3, "National League 1"),
    ("National_League_2", 4, "National League 2"),
    ("Regional_1", 5, "Regional 1"),
    ("Regional_2", 6, "Regional 2"),
    ("Counties_1", 7, "Counties 1"),
    ("Counties_2", 8, "Counties 2"),
    ("Counties_3", 9, "Counties 3"),
    ("Counties_4", 10, "Counties 4"),
    ("Counties_5", 11, "Counties 5"),
]

WOMENS_TIERS_CURRENT: list[tuple[str, int, str]] = [
    ("Women's_Premiership", 101, "Premiership Women's"),
    ("Women's_NC_1", 104, "National Challenge 1"),
    ("Women's_NC_2", 105, "National Challenge 2"),
    ("Women's_NC_3", 106, "National Challenge 3"),
]


def extract_tier(filename: str, season: str = "2025-2026") -> tuple[int, str]:
    tier = extract_tier_men(filename, season)
    if tier is None:
        tier = extract_tier_women(filename, season)
    if tier is None:
        print("Warning: Could not extract tier from filename:", filename, "for season:", season)
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


def extract_tier_men_current(filename: str, season: str) -> tuple[int, str] | None:
    """Extract tier from 2022-2023 onwards filename format."""
    for prefix, tier_num, tier_name in MENS_TIERS_CURRENT:
        if filename.startswith(prefix):
            return (tier_num, tier_name)
    if filename.startswith("Cumbria_Conference"):
        if filename.endswith("1.json"):
            return (8, "Counties 2")
        if filename.endswith("2.json"):
            return (9, "Counties 3")
    return None


def extract_tier_women_current(filename: str, season: str) -> tuple[int, str] | None:
    """Extract tier from 2019-2020 onwards filename format."""
    for prefix, tier_num, tier_name in WOMENS_TIERS_CURRENT:
        if filename.startswith(prefix):
            return (tier_num, tier_name)
    if filename.startswith("Women's_Championship"):
        if filename.endswith("1.json"):
            return (102, "Championship 1")
        if filename.endswith("2.json"):
            return (103, "Championship 2")
    return None


def extract_tier_men_pre_2021(filename: str, season: str) -> tuple[int, str] | None:
    """Extract tier from 2021-2022 and earlier filename format."""
    filename = (
        filename.removeprefix("Tribute_")
        .removeprefix("Wadworth_")
        .removeprefix("Harvey's_of_")
        .removeprefix("Harvey\u2019s_Brewery_")
        .removeprefix("Greene_King_IPA_")
        .removeprefix("Shepherd_Neame_")
        .removeprefix("6X_")
        .removeprefix("Snows_Group_")
        .removeprefix("SSE_")
    )

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
        "2": 2,
        "Two": 2,
        "3": 3,
        "Three": 3,
        "4": 4,
        "Four": 4,
        "5": 5,
        "Five": 5,
    }
    num = 0
    for part in other_words:
        if part in num_map:
            num = num_map[part]
            break
    return num
