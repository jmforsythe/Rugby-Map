"""
Analyse league/tier movement of every team across all seasons.

Loads geocoded team data for every available season, tracks each team's tier
over time, and flags outliers that may indicate incorrect tier assignments.
"""

import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

from tier_extraction import extract_tier
from utils import setup_logging

logger = logging.getLogger(__name__)

GEOCODED_DIR = Path("geocoded_teams")
SEASONS = sorted(d.name for d in GEOCODED_DIR.iterdir() if d.is_dir() and "-" in d.name)

MENS_MAX_TIER = 99
WOMENS_MIN_TIER = 100


def load_all_seasons() -> dict[str, dict[str, list[tuple[str, int, str]]]]:
    """Load every season and return {team_name: {season: [(league, tier_num, tier_name), ...]}}.

    A team can appear in multiple leagues in the same season (rare but possible),
    so each season entry is a list.
    """
    team_history: dict[str, dict[str, list[tuple[str, int, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for season in SEASONS:
        season_dir = GEOCODED_DIR / season
        for filepath in sorted(season_dir.glob("*.json")):
            tier_num, tier_name = extract_tier(filepath.name, season)
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            league_name = data.get("league_name", filepath.stem)
            for team in data.get("teams", []):
                name = team["name"]
                team_history[name][season].append((league_name, tier_num, tier_name))

    return team_history


def analyse(team_history: dict[str, dict[str, list[tuple[str, int, str]]]]) -> None:
    """Run all analyses and print reports."""

    unknown_tiers: list[tuple[str, str, str]] = []
    duplicate_appearances: list[tuple[str, str, list[tuple[str, int, str]]]] = []
    cross_gender: list[tuple[str, str, list[tuple[str, int, str]]]] = []
    big_jumps: list[tuple[str, str, str, int, int, int, str | None, str]] = []

    for team, seasons in sorted(team_history.items()):
        prev_season: str | None = None
        prev_tier_num: int | None = None
        prev_tier_name: str | None = None
        prev_session_is_mens = True

        for season in SEASONS:
            entries = seasons.get(season)
            if entries is None:
                prev_season = None
                prev_tier_num = None
                prev_tier_name = None
                continue

            for league, tier_num, _tier_name in entries:
                if tier_num == 999:
                    unknown_tiers.append((team, season, league))

            if len(entries) > 1:
                mens_entries = [e for e in entries if e[1] < WOMENS_MIN_TIER]
                womens_entries = [e for e in entries if e[1] >= WOMENS_MIN_TIER]
                if len(mens_entries) > 1 or len(womens_entries) > 1:
                    duplicate_appearances.append((team, season, entries))
                if mens_entries and womens_entries:
                    cross_gender.append((team, season, entries))

            mens_entries_this = [e for e in entries if e[1] < WOMENS_MIN_TIER]
            womens_entries_this = [e for e in entries if e[1] >= WOMENS_MIN_TIER]

            for entry_set in [mens_entries_this, womens_entries_this]:
                if not entry_set:
                    continue
                best_tier = min(e[1] for e in entry_set)
                best_entry = next(e for e in entry_set if e[1] == best_tier)
                cur_tier_num = best_entry[1]
                cur_tier_name = best_entry[2]

                same_gender = (prev_session_is_mens and cur_tier_num < WOMENS_MIN_TIER) or (
                    not prev_session_is_mens and cur_tier_num >= WOMENS_MIN_TIER
                )
                if prev_season is not None and prev_tier_num is not None and same_gender:
                    jump = abs(cur_tier_num - prev_tier_num)
                    if jump >= 3:
                        big_jumps.append(
                            (
                                team,
                                prev_season,
                                season,
                                prev_tier_num,
                                cur_tier_num,
                                jump,
                                prev_tier_name,
                                cur_tier_name,
                            )
                        )

                prev_season = season
                prev_tier_num = cur_tier_num
                prev_tier_name = cur_tier_name
                prev_session_is_mens = cur_tier_num < WOMENS_MIN_TIER

    print_section(
        "UNKNOWN TIER ASSIGNMENTS (tier 999)",
        unknown_tiers,
        headers=["Team", "Season", "League"],
        row_fn=lambda r: r,
    )

    print_section(
        "DUPLICATE APPEARANCES IN SAME GENDER/SEASON",
        duplicate_appearances,
        headers=["Team", "Season", "Leagues (league, tier_num, tier_name)"],
        row_fn=lambda r: (r[0], r[1], "; ".join(f"{e[0]} (tier {e[1]}: {e[2]})" for e in r[2])),
    )

    print_section(
        "CROSS-GENDER APPEARANCES (men's + women's same season)",
        cross_gender,
        headers=["Team", "Season", "Leagues"],
        row_fn=lambda r: (r[0], r[1], "; ".join(f"{e[0]} (tier {e[1]}: {e[2]})" for e in r[2])),
    )

    big_jumps_sorted = sorted(big_jumps, key=lambda r: -r[5])
    print_section(
        "LARGE TIER JUMPS (>=3 levels between consecutive seasons)",
        big_jumps_sorted,
        headers=[
            "Team",
            "From Season",
            "To Season",
            "From Tier",
            "To Tier",
            "Jump",
            "From Name",
            "To Name",
        ],
        row_fn=lambda r: r,
    )

    print_tier_movement_summary(team_history)
    print_tier_distribution_by_season(team_history)


def print_tier_movement_summary(
    team_history: dict[str, dict[str, list[tuple[str, int, str]]]],
) -> None:
    """Print a summary of tier movement patterns across the whole dataset."""
    print("\n" + "=" * 80)
    print("TIER MOVEMENT SUMMARY")
    print("=" * 80)

    jump_counter: dict[int, int] = defaultdict(int)
    total_transitions = 0

    for _team, seasons in team_history.items():
        prev_tier = None
        for season in SEASONS:
            entries = seasons.get(season)
            if entries is None:
                prev_tier = None
                continue
            mens = [e for e in entries if e[1] < WOMENS_MIN_TIER]
            if mens:
                cur = min(e[1] for e in mens)
                if prev_tier is not None and prev_tier < WOMENS_MIN_TIER:
                    diff = cur - prev_tier
                    jump_counter[diff] += 1
                    total_transitions += 1
                prev_tier = cur
            else:
                prev_tier = None

    print(f"\nTotal season-to-season transitions (men's): {total_transitions}")
    print(f"{'Change':>8}  {'Count':>6}  {'Pct':>6}  Bar")
    print("-" * 60)
    for diff in sorted(jump_counter.keys()):
        count = jump_counter[diff]
        pct = count / total_transitions * 100 if total_transitions else 0
        bar = "#" * max(1, int(pct))
        label = "same" if diff == 0 else (f"+{diff}" if diff > 0 else str(diff))
        print(f"{label:>8}  {count:>6}  {pct:>5.1f}%  {bar}")


def print_tier_distribution_by_season(
    team_history: dict[str, dict[str, list[tuple[str, int, str]]]],
) -> None:
    """Print how many teams are at each tier per season."""
    print("\n" + "=" * 80)
    print("TEAM COUNT PER TIER PER SEASON (men's, tiers 1-11)")
    print("=" * 80)

    tier_season_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for _team, seasons in team_history.items():
        for season in SEASONS:
            entries = seasons.get(season)
            if entries is None:
                continue
            seen_tiers = set()
            for _, tier_num, _ in entries:
                if tier_num < WOMENS_MIN_TIER and tier_num not in seen_tiers:
                    tier_season_counts[season][tier_num] += 1
                    seen_tiers.add(tier_num)

    all_tiers = sorted({t for sc in tier_season_counts.values() for t in sc})

    header = f"{'Season':<12}" + "".join(f"{'T'+str(t):>6}" for t in all_tiers)
    print(header)
    print("-" * len(header))
    for season in SEASONS:
        counts = tier_season_counts.get(season, {})
        row = f"{season:<12}" + "".join(f"{counts.get(t, 0):>6}" for t in all_tiers)
        print(row)


def print_section(title, data, headers, row_fn):
    print("\n" + "=" * 80)
    print(f"{title} ({len(data)} found)")
    print("=" * 80)
    if not data:
        print("  (none)")
        return

    rows = [row_fn(d) for d in data]
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))

    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print("-" * sum(col_widths + [2 * (len(col_widths) - 1)]))
    for row in rows:
        print(fmt.format(*[str(v) for v in row]))


def print_team_timeline(
    team_history: dict[str, dict[str, list[tuple[str, int, str]]]],
    team_name: str,
) -> None:
    """Print the full tier history for a specific team."""
    if team_name not in team_history:
        print(f"Team '{team_name}' not found.")
        return
    print(f"\nTimeline for: {team_name}")
    print("-" * 60)
    seasons = team_history[team_name]
    for season in SEASONS:
        entries = seasons.get(season)
        if entries:
            for league, tier_num, tier_name in entries:
                print(f"  {season}  tier {tier_num:>3} ({tier_name:<20})  {league}")
        else:
            print(f"  {season}  ---")


def main() -> None:
    setup_logging()
    logger.info("Loading all %d seasons of data...", len(SEASONS))
    team_history = load_all_seasons()
    logger.info("Loaded %d unique team names across all seasons.", len(team_history))

    analyse(team_history)

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        matches = [name for name in team_history if query.lower() in name.lower()]
        if matches:
            for name in sorted(matches):
                print_team_timeline(team_history, name)
        else:
            print(f"\nNo teams matching '{query}'")


if __name__ == "__main__":
    main()
