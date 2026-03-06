"""
Analyse league/tier movement of every team across all seasons.

Loads geocoded team data for every available season, tracks each team's tier
over time, and flags outliers that may indicate incorrect tier assignments.
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple, TypeVar

from tier_extraction import extract_tier
from utils import setup_logging

logger = logging.getLogger(__name__)

GEOCODED_DIR = Path("geocoded_teams")
SEASONS = sorted(d.name for d in GEOCODED_DIR.iterdir() if d.is_dir() and "-" in d.name)

WOMENS_MIN_TIER = 100


class LeagueEntry(NamedTuple):
    league: str
    tier_num: int
    tier_name: str


class TierJump(NamedTuple):
    team: str
    from_season: str
    to_season: str
    from_tier: int
    to_tier: int
    jump: int
    from_league: str | None
    to_league: str


class UnknownTier(NamedTuple):
    team: str
    season: str
    league: str


class DuplicateAppearance(NamedTuple):
    team: str
    season: str
    entries: list[LeagueEntry]


TeamHistory = dict[str, dict[str, list[LeagueEntry]]]
T = TypeVar("T")


def load_all_seasons() -> TeamHistory:
    """Load every season and return {team_name: {season: [LeagueEntry, ...]}}.

    A team can appear in multiple leagues in the same season (rare but possible),
    so each season entry is a list.
    """
    team_history: TeamHistory = defaultdict(lambda: defaultdict(list))

    for season in SEASONS:
        season_dir = GEOCODED_DIR / season
        for filepath in sorted(season_dir.rglob("*.json")):
            tier_num, tier_name = extract_tier(filepath.name, season)
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            league_name = data.get("league_name", filepath.stem)
            for team in data.get("teams", []):
                name = team["name"]
                team_history[name][season].append(LeagueEntry(league_name, tier_num, tier_name))

    return team_history


def _is_mens(entry: LeagueEntry) -> bool:
    return entry.tier_num < WOMENS_MIN_TIER


def analyse(team_history: TeamHistory) -> None:
    """Run all analyses and print reports."""

    unknown_tiers: list[UnknownTier] = []
    duplicate_appearances: list[DuplicateAppearance] = []
    cross_gender: list[DuplicateAppearance] = []
    big_jumps: list[TierJump] = []

    for team, seasons in sorted(team_history.items()):
        prev_season: str | None = None
        prev_tier_num: int | None = None
        prev_league_name: str | None = None
        prev_is_mens = True

        for season in SEASONS:
            entries = seasons.get(season)
            if entries is None:
                prev_season = None
                prev_tier_num = None
                prev_league_name = None
                continue

            for entry in entries:
                if entry.tier_num == 999:
                    unknown_tiers.append(UnknownTier(team, season, entry.league))

            if len(entries) > 1:
                mens_entries = [e for e in entries if _is_mens(e)]
                womens_entries = [e for e in entries if not _is_mens(e) and e.tier_num != 999]
                if len(mens_entries) > 1 or len(womens_entries) > 1:
                    duplicate_appearances.append(DuplicateAppearance(team, season, entries))
                if mens_entries and womens_entries:
                    cross_gender.append(DuplicateAppearance(team, season, entries))

            mens_entries_this = [e for e in entries if _is_mens(e)]
            womens_entries_this = [e for e in entries if not _is_mens(e)]

            for entry_set in [mens_entries_this, womens_entries_this]:
                if not entry_set:
                    continue
                best = min(entry_set, key=lambda e: e.tier_num)

                same_gender = (prev_is_mens and _is_mens(best)) or (
                    not prev_is_mens and not _is_mens(best)
                )
                if prev_season is not None and prev_tier_num is not None and same_gender:
                    jump = abs(best.tier_num - prev_tier_num)
                    if jump >= 3:
                        big_jumps.append(
                            TierJump(
                                team=team,
                                from_season=prev_season,
                                to_season=season,
                                from_tier=prev_tier_num,
                                to_tier=best.tier_num,
                                jump=jump,
                                from_league=prev_league_name,
                                to_league=best.league,
                            )
                        )

                prev_season = season
                prev_tier_num = best.tier_num
                prev_league_name = best.league
                prev_is_mens = _is_mens(best)

    print_section(
        "UNKNOWN TIER ASSIGNMENTS (tier 999)",
        unknown_tiers,
        headers=["Team", "Season", "League"],
        row_fn=lambda r: r,
    )

    def _format_entries(d: DuplicateAppearance) -> tuple[str, str, str]:
        return (
            d.team,
            d.season,
            "; ".join(f"{e.league} (tier {e.tier_num}: {e.tier_name})" for e in d.entries),
        )

    print_section(
        "DUPLICATE APPEARANCES IN SAME GENDER/SEASON",
        duplicate_appearances,
        headers=["Team", "Season", "Leagues (league, tier_num, tier_name)"],
        row_fn=_format_entries,
    )

    print_section(
        "CROSS-GENDER APPEARANCES (men's + women's same season)",
        cross_gender,
        headers=["Team", "Season", "Leagues"],
        row_fn=_format_entries,
    )

    big_jumps_sorted = sorted(big_jumps, key=lambda r: -r.jump)
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
            "From League",
            "To League",
        ],
        row_fn=lambda r: r,
    )

    print_tier_movement_summary(team_history)
    print_tier_distribution_by_season(team_history)


def print_tier_movement_summary(team_history: TeamHistory) -> None:
    """Print a summary of tier movement patterns across the whole dataset."""
    print("\n" + "=" * 80)
    print("TIER MOVEMENT SUMMARY")
    print("=" * 80)

    jump_counter: dict[int, int] = defaultdict(int)
    total_transitions = 0

    for _team, seasons in team_history.items():
        prev_tier: int | None = None
        for season in SEASONS:
            entries = seasons.get(season)
            if entries is None:
                prev_tier = None
                continue
            mens = [e for e in entries if _is_mens(e)]
            if mens:
                cur = min(e.tier_num for e in mens)
                if prev_tier is not None:
                    jump_counter[cur - prev_tier] += 1
                    total_transitions += 1
                prev_tier = cur
            else:
                prev_tier = None

    print(f"\nTotal season-to-season transitions (men's): {total_transitions}")
    print(f"{'Change':>8}  {'Count':>6}  {'Pct':>6}  Bar")
    print("-" * 60)
    for diff in sorted(jump_counter):
        count = jump_counter[diff]
        pct = count / total_transitions * 100 if total_transitions else 0
        bar = "#" * max(1, int(pct))
        label = "same" if diff == 0 else (f"+{diff}" if diff > 0 else str(diff))
        print(f"{label:>8}  {count:>6}  {pct:>5.1f}%  {bar}")


def print_tier_distribution_by_season(team_history: TeamHistory) -> None:
    """Print how many teams are at each tier per season."""
    print("\n" + "=" * 80)
    print("TEAM COUNT PER TIER PER SEASON (men's)")
    print("=" * 80)

    tier_season_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for _team, seasons in team_history.items():
        for season in SEASONS:
            entries = seasons.get(season)
            if entries is None:
                continue
            seen_tiers: set[int] = set()
            for entry in entries:
                if _is_mens(entry) and entry.tier_num not in seen_tiers:
                    tier_season_counts[season][entry.tier_num] += 1
                    seen_tiers.add(entry.tier_num)

    all_tiers = sorted({t for sc in tier_season_counts.values() for t in sc})

    header = f"{'Season':<12}" + "".join(f"{'T'+str(t):>6}" for t in all_tiers)
    print(header)
    print("-" * len(header))
    for season in SEASONS:
        counts = tier_season_counts.get(season, {})
        row = f"{season:<12}" + "".join(f"{counts.get(t, 0):>6}" for t in all_tiers)
        print(row)


def print_section(
    title: str,
    data: Sequence[T],
    headers: list[str],
    row_fn: Callable[[T], tuple[object, ...]],
) -> None:
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


def print_team_timeline(team_history: TeamHistory, team_name: str) -> None:
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
            for entry in entries:
                print(
                    f"  {season}  tier {entry.tier_num:>3} ({entry.tier_name:<20})  {entry.league}"
                )
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
