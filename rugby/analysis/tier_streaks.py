"""Find teams with the longest consecutive-season streaks at the same pyramid tier.

Uses the same canonical team identity as ``rugby.team_pages`` (transitive merge on
display name and RFU ``team=`` id) so renames do not break a streak. Merit leagues
are converted to absolute pyramid tiers via :func:`rugby.tiers.get_competition_offset`,
matching ``rugby.analysis.tier_movement``.

Usage::

    python -m rugby.analysis.tier_streaks
    python -m rugby.analysis.tier_streaks --top 30 --min-length 8
    python -m rugby.analysis.tier_streaks --tier 3
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from dataclasses import dataclass

from core import setup_logging
from rugby.team_pages import LeagueHistoryEntry, collect_all_teams_data, get_all_seasons
from rugby.tiers import get_competition_offset

logger = logging.getLogger(__name__)

WOMENS_MIN_TIER = 100
UNKNOWN_TIER = 999
DEFAULT_TOP = 25
DEFAULT_MIN_LENGTH = 5

# RFU competitions were curtailed or inconsistently recorded during COVID. A team
# absent from these seasons does not break a streak when the tier matches before
# and after the gap. If a team *does* appear in a gap season at a different tier,
# the streak still ends (e.g. Saracens in Championship 2020-2021).
COVID_GAP_SEASONS = frozenset({"2020-2021", "2021-2022"})


@dataclass(frozen=True, slots=True)
class TierStreak:
    page_key: str
    display_name: str
    gender: str
    tier_num: int
    tier_name: str
    start_season: str
    end_season: str
    length: int
    names_seen: tuple[str, ...]


def _absolute_tier(entry: LeagueHistoryEntry) -> int:
    tier_num = entry["tier"][0]
    if entry["is_merit"] and entry["competition_key"]:
        tier_num += get_competition_offset(entry["competition_key"], entry["season"])
    return tier_num


def _is_mens_tier(tier_num: int) -> bool:
    return tier_num < WOMENS_MIN_TIER


def _seasons_consecutive(prev_season: str, season: str) -> bool:
    return prev_season.split("-")[1] == season.split("-")[0]


def _bridgeable_season_gap(
    prev_season: str,
    season: str,
    tier: int,
    *,
    all_seasons: list[str],
    by_season: dict[str, list[LeagueHistoryEntry]],
    mens: bool,
) -> int | None:
    """Return season steps from ``prev_season`` to ``season`` if the gap is bridgeable.

    A gap is bridgeable when every season strictly between the two endpoints is
    either a COVID gap season with no recorded appearance, or an appearance at
    the same ``tier``. Returns ``None`` when the streak cannot continue.
    """
    try:
        prev_idx = all_seasons.index(prev_season)
        season_idx = all_seasons.index(season)
    except ValueError:
        return None
    if season_idx <= prev_idx:
        return None

    for idx in range(prev_idx + 1, season_idx + 1):
        gap_season = all_seasons[idx]
        best = _best_entry_for_gender(by_season.get(gap_season, []), mens=mens)
        if best is None:
            if gap_season not in COVID_GAP_SEASONS:
                return None
            continue
        if _absolute_tier(best) != tier:
            return None

    return season_idx - prev_idx


def _best_entry_for_gender(
    entries: list[LeagueHistoryEntry], *, mens: bool
) -> LeagueHistoryEntry | None:
    candidates = [
        e
        for e in entries
        if _is_mens_tier(_absolute_tier(e)) == mens and _absolute_tier(e) != UNKNOWN_TIER
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda e: _absolute_tier(e))


def _append_tier_streak(
    streaks: list[TierStreak],
    *,
    page_key: str,
    display_name: str,
    gender: str,
    streak_tier: int | None,
    streak_tier_name: str,
    streak_start: str,
    streak_end: str,
    streak_length: int,
    names_in_streak: set[str],
) -> None:
    if streak_length <= 0 or streak_tier is None:
        return
    streaks.append(
        TierStreak(
            page_key=page_key,
            display_name=display_name,
            gender=gender,
            tier_num=streak_tier,
            tier_name=streak_tier_name,
            start_season=streak_start,
            end_season=streak_end,
            length=streak_length,
            names_seen=tuple(sorted(names_in_streak)),
        )
    )


def _collect_streaks_for_team(
    page_key: str,
    display_name: str,
    league_history: list[LeagueHistoryEntry],
    all_seasons: list[str],
) -> list[TierStreak]:
    by_season: dict[str, list[LeagueHistoryEntry]] = defaultdict(list)
    for entry in league_history:
        by_season[entry["season"]].append(entry)

    streaks: list[TierStreak] = []

    for mens in (True, False):
        gender = "men's" if mens else "women's"
        streak_tier: int | None = None
        streak_tier_name = ""
        streak_start = ""
        streak_end = ""
        streak_length = 0
        names_in_streak: set[str] = set()
        prev_active_season: str | None = None

        for season in all_seasons:
            best = _best_entry_for_gender(by_season.get(season, []), mens=mens)
            if best is None:
                if season in COVID_GAP_SEASONS and streak_length > 0:
                    continue
                _append_tier_streak(
                    streaks,
                    page_key=page_key,
                    display_name=display_name,
                    gender=gender,
                    streak_tier=streak_tier,
                    streak_tier_name=streak_tier_name,
                    streak_start=streak_start,
                    streak_end=streak_end,
                    streak_length=streak_length,
                    names_in_streak=names_in_streak,
                )
                streak_tier = None
                streak_length = 0
                names_in_streak = set()
                prev_active_season = None
                continue

            tier = _absolute_tier(best)
            tier_name = best["tier"][1]
            team_name = best.get("team_name") or display_name

            steps: int | None = None
            if streak_length > 0 and streak_tier == tier and prev_active_season is not None:
                steps = _bridgeable_season_gap(
                    prev_active_season,
                    season,
                    tier,
                    all_seasons=all_seasons,
                    by_season=by_season,
                    mens=mens,
                )

            if steps is not None:
                streak_length += steps
                streak_end = season
                names_in_streak.add(team_name)
            else:
                _append_tier_streak(
                    streaks,
                    page_key=page_key,
                    display_name=display_name,
                    gender=gender,
                    streak_tier=streak_tier,
                    streak_tier_name=streak_tier_name,
                    streak_start=streak_start,
                    streak_end=streak_end,
                    streak_length=streak_length,
                    names_in_streak=names_in_streak,
                )
                streak_tier = tier
                streak_tier_name = tier_name
                streak_start = season
                streak_end = season
                streak_length = 1
                names_in_streak = {team_name}

            prev_active_season = season

        _append_tier_streak(
            streaks,
            page_key=page_key,
            display_name=display_name,
            gender=gender,
            streak_tier=streak_tier,
            streak_tier_name=streak_tier_name,
            streak_start=streak_start,
            streak_end=streak_end,
            streak_length=streak_length,
            names_in_streak=names_in_streak,
        )

    return streaks


def collect_all_streaks(min_length: int = 1) -> tuple[list[str], list[TierStreak]]:
    all_seasons = sorted(get_all_seasons())
    all_teams = collect_all_teams_data()
    logger.info(
        "Loaded %d canonical teams across %d seasons",
        len(all_teams),
        len(all_seasons),
    )

    streaks: list[TierStreak] = []
    for page_key, team_data in all_teams.items():
        display_name = team_data.get("name") or page_key
        team_streaks = _collect_streaks_for_team(
            page_key,
            display_name,
            team_data["league_history"],
            all_seasons,
        )
        streaks.extend(s for s in team_streaks if s.length >= min_length)

    return all_seasons, streaks


def _tier_label(tier_num: int) -> str:
    if tier_num >= WOMENS_MIN_TIER:
        return f"W{tier_num - 100}"
    return str(tier_num)


def _format_names(names: tuple[str, ...], display_name: str) -> str:
    others = [n for n in names if n != display_name]
    if not others:
        return display_name
    return f"{display_name} (also: {', '.join(others)})"


def print_overall_report(streaks: list[TierStreak], *, top: int) -> None:
    ranked = sorted(streaks, key=lambda s: (-s.length, s.tier_num, s.display_name.lower()))
    print("\n" + "=" * 100)
    print(f"LONGEST TIER STREAKS - TOP {top} (all tiers)")
    print("=" * 100)
    if not ranked:
        print("  (none)")
        return

    headers = ["Rank", "Team", "Gender", "Tier", "Tier name", "Seasons", "Length"]
    rows: list[tuple[str, ...]] = []
    for i, streak in enumerate(ranked[:top], start=1):
        season_span = (
            streak.start_season
            if streak.start_season == streak.end_season
            else f"{streak.start_season} to {streak.end_season}"
        )
        rows.append(
            (
                str(i),
                _format_names(streak.names_seen, streak.display_name),
                streak.gender,
                _tier_label(streak.tier_num),
                streak.tier_name,
                season_span,
                str(streak.length),
            )
        )
    _print_table(headers, rows)


def print_per_tier_report(streaks: list[TierStreak], *, top_per_tier: int) -> None:
    by_tier: dict[int, list[TierStreak]] = defaultdict(list)
    for streak in streaks:
        by_tier[streak.tier_num].append(streak)

    print("\n" + "=" * 100)
    print(f"LONGEST STREAK AT EACH TIER - TOP {top_per_tier} PER TIER")
    print("=" * 100)

    for tier_num in sorted(by_tier):
        tier_streaks = sorted(
            by_tier[tier_num],
            key=lambda s: (-s.length, s.display_name.lower()),
        )[:top_per_tier]
        sample_name = tier_streaks[0].tier_name
        print(f"\n--- Tier {_tier_label(tier_num)} ({sample_name}) ---")
        headers = ["Team", "Gender", "Seasons", "Length"]
        rows = [
            (
                _format_names(s.names_seen, s.display_name),
                s.gender,
                (
                    s.start_season
                    if s.start_season == s.end_season
                    else f"{s.start_season} to {s.end_season}"
                ),
                str(s.length),
            )
            for s in tier_streaks
        ]
        _print_table(headers, rows, indent=2)


def print_tier_filter_report(streaks: list[TierStreak], tier: int, *, top: int) -> None:
    filtered = [s for s in streaks if s.tier_num == tier]
    ranked = sorted(filtered, key=lambda s: (-s.length, s.display_name.lower()))

    print("\n" + "=" * 100)
    print(f"LONGEST STREAKS AT TIER {_tier_label(tier)} - TOP {top}")
    print("=" * 100)
    if not ranked:
        print("  (none)")
        return

    headers = ["Rank", "Team", "Gender", "Tier name", "Seasons", "Length"]
    rows = []
    for i, streak in enumerate(ranked[:top], start=1):
        season_span = (
            streak.start_season
            if streak.start_season == streak.end_season
            else f"{streak.start_season} to {streak.end_season}"
        )
        rows.append(
            (
                str(i),
                _format_names(streak.names_seen, streak.display_name),
                streak.gender,
                streak.tier_name,
                season_span,
                str(streak.length),
            )
        )
    _print_table(headers, rows)


def _print_table(headers: list[str], rows: list[tuple[str, ...]], *, indent: int = 0) -> None:
    if not rows:
        print(" " * indent + "  (none)")
        return

    prefix = " " * indent
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(prefix + fmt.format(*headers))
    print(prefix + "-" * (sum(col_widths) + 2 * (len(col_widths) - 1)))
    for row in rows:
        print(prefix + fmt.format(*row))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report longest consecutive-season streaks without a tier change."
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help=f"Number of streaks to show in ranked sections (default {DEFAULT_TOP})",
    )
    parser.add_argument(
        "--top-per-tier",
        type=int,
        default=5,
        help="Number of streaks to show per tier in the per-tier section (default 5)",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=DEFAULT_MIN_LENGTH,
        help=f"Minimum streak length in seasons to include (default {DEFAULT_MIN_LENGTH})",
    )
    parser.add_argument(
        "--tier",
        type=int,
        default=None,
        help="Only report streaks at this absolute tier number (e.g. 3 for National 1)",
    )
    args = parser.parse_args()

    setup_logging()
    all_seasons, streaks = collect_all_streaks(min_length=args.min_length)

    print("=" * 100)
    print("TIER STREAK REPORT")
    print("=" * 100)
    print(f"Seasons analysed: {all_seasons[0]} .. {all_seasons[-1]} ({len(all_seasons)} seasons)")
    print(f"Streaks with length >= {args.min_length}: {len(streaks)}")
    print(
        "Missing seasons break a streak except COVID gap years "
        f"({', '.join(sorted(COVID_GAP_SEASONS))}) when tier is unchanged. "
        "Merit tiers use absolute pyramid positions."
    )

    if args.tier is not None:
        print_tier_filter_report(streaks, args.tier, top=args.top)
    else:
        print_overall_report(streaks, top=args.top)
        print_per_tier_report(streaks, top_per_tier=args.top_per_tier)


if __name__ == "__main__":
    main()
