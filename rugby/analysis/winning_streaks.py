"""Find the longest winning streaks (by result, not league position).

Walks every completed fixture in ``data/rugby/fixture_data/`` for each canonical
team (same identity as ``rugby.team_pages``, so renames don't break a streak),
orders them chronologically across all seasons, and reports:

* the longest winning streak any team has ever had, and
* the longest winning streak currently ongoing (i.e. ending on that team's
  most recent completed match).

A draw, a loss, or a non-numeric result with no walkover status ends a streak.
Walkovers (``HWO``/``AWO``) count as a win/loss for whichever side benefited.

Each streak also records the range of levels the team played at during the
run (a promotion or relegation mid-streak widens the range), one span per
distinct pyramid tier or merit competition visited — merit levels are kept in
their own competition's raw numbering (``"CANDY: Level 2"``, matching the
league-history captions on team pages) rather than converted to an absolute
pyramid tier, since that conversion is season-specific and lossy.

Usage::

    python -m rugby.analysis.winning_streaks
    python -m rugby.analysis.winning_streaks --top 30 --min-length 5
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace

from core import setup_logging
from rugby.team_pages import (
    TeamData,
    TeamFixtureEntry,
    build_id_to_page_key,
    collect_all_teams_data,
    collect_team_fixtures,
    get_all_seasons,
)

DEFAULT_TOP = 25
DEFAULT_MIN_LENGTH = 5

# How many of the most recent seasons count as "currently fielding a team" for
# has_recent_league — a team last seen in league_data further back than this is
# treated as defunct/inactive even if its fixture data has no losing result yet
# to close out an "ongoing" streak.
RECENT_SEASON_WINDOW = 2

# Women's absolute (non-merit) tiers are encoded as 100 + tier, matching
# rugby.analysis.tier_streaks / rugby.tiers, so they need re-basing before
# display instead of printing the raw internal number.
WOMENS_MIN_TIER = 100


@dataclass(frozen=True, slots=True)
class LevelSpan:
    """One pyramid tier or merit competition a streak passed through."""

    is_merit: bool
    competition_key: str  # "" for the national pyramid
    min_tier: int  # raw tier number: absolute for pyramid, competition-relative for merit
    max_tier: int


@dataclass(frozen=True, slots=True)
class WinStreak:
    page_key: str
    display_name: str
    start_date: str
    end_date: str
    length: int
    still_going: bool
    level_spans: tuple[LevelSpan, ...]
    logo_url: str | None
    # True when the team has a league_data entry in one of the most recent
    # RECENT_SEASON_WINDOW seasons — lets callers drop "ongoing" streaks for
    # teams that have simply stopped appearing in fixture data (folded, merged,
    # dropped out of the league) rather than actually still playing.
    has_recent_league: bool = True


def _match_result(entry: TeamFixtureEntry) -> str | None:
    """Return ``"W"``, ``"L"``, ``"D"`` for *entry*, or ``None`` if undetermined."""
    status = entry.get("status")
    if status:
        if status == "HWO":
            return "W" if entry["is_home"] else "L"
        if status == "AWO":
            return "L" if entry["is_home"] else "W"
        return None

    home_score = entry.get("home_score")
    away_score = entry.get("away_score")
    if home_score is None or away_score is None:
        return None

    own_score = home_score if entry["is_home"] else away_score
    opponent_score = away_score if entry["is_home"] else home_score
    if own_score > opponent_score:
        return "W"
    if own_score < opponent_score:
        return "L"
    return "D"


@dataclass(frozen=True, slots=True)
class _TierInfo:
    is_merit: bool
    competition_key: str
    tier_num: int  # raw: absolute for pyramid, competition-relative for merit


def _tier_lookup(team_data: TeamData) -> dict[tuple[str, str], _TierInfo]:
    """``(season, league name) -> tier info`` for one team's league history.

    Tier numbers are used as recorded in league history: already absolute for
    the national pyramid (including 101+ for women's), and competition-relative
    (not pyramid-absolute) for merit leagues.
    """
    lookup: dict[tuple[str, str], _TierInfo] = {}
    for entry in team_data.get("league_history", []):
        lookup[(entry["season"], entry["league"])] = _TierInfo(
            is_merit=entry["is_merit"],
            competition_key=entry["competition_key"],
            tier_num=entry["tier"][0],
        )
    return lookup


def _completed_results_chronological(
    fixtures: list[TeamFixtureEntry],
    tier_lookup: dict[tuple[str, str], _TierInfo],
) -> list[tuple[str, str, _TierInfo | None]]:
    """Return ``(date, result, tier_info)`` rows with a determinable result, oldest first."""
    dated: list[tuple[str, str, str, _TierInfo | None]] = []
    for entry in fixtures:
        result = _match_result(entry)
        if result is None:
            continue
        tier_info = tier_lookup.get((entry["season"], entry["league_name"]))
        dated.append((entry["date"], entry["match_url"], result, tier_info))
    dated.sort(key=lambda row: (row[0], row[1]))
    return [(date, result, tier_info) for date, _url, result, tier_info in dated]


def _streaks_for_team(
    page_key: str,
    display_name: str,
    logo_url: str | None,
    results: list[tuple[str, str, _TierInfo | None]],
) -> list[WinStreak]:
    streaks: list[WinStreak] = []
    run_start: str | None = None
    run_end: str | None = None
    run_length = 0
    run_spans: dict[tuple[bool, str], list[int]] = {}

    def flush(*, still_going: bool) -> None:
        if run_length > 0 and run_start is not None and run_end is not None:
            spans = tuple(
                LevelSpan(is_merit=key[0], competition_key=key[1], min_tier=lo, max_tier=hi)
                for key, (lo, hi) in run_spans.items()
            )
            streaks.append(
                WinStreak(
                    page_key,
                    display_name,
                    run_start,
                    run_end,
                    run_length,
                    still_going=still_going,
                    level_spans=spans,
                    logo_url=logo_url,
                )
            )

    for date, result, tier_info in results:
        if result == "W":
            if run_length == 0:
                run_start = date
                run_spans = {}
            if tier_info is not None:
                key = (tier_info.is_merit, tier_info.competition_key)
                if key not in run_spans:
                    run_spans[key] = [tier_info.tier_num, tier_info.tier_num]
                else:
                    lo, hi = run_spans[key]
                    run_spans[key] = [min(lo, tier_info.tier_num), max(hi, tier_info.tier_num)]
            run_length += 1
            run_end = date
        else:
            flush(still_going=False)
            run_length = 0
            run_start = None
            run_end = None
            run_spans = {}

    flush(still_going=True)
    return streaks


def collect_all_win_streaks(min_length: int = 1) -> list[WinStreak]:
    all_teams = collect_all_teams_data()
    id_to_page_key = build_id_to_page_key(all_teams)
    fixtures_by_team = collect_team_fixtures(id_to_page_key)
    recent_seasons = frozenset(get_all_seasons()[:RECENT_SEASON_WINDOW])

    streaks: list[WinStreak] = []
    for page_key, fixtures in fixtures_by_team.items():
        team_data = all_teams.get(page_key, {})
        display_name = team_data.get("name") or page_key
        logo_url = team_data.get("image_url")
        has_recent_league = any(
            entry["season"] in recent_seasons for entry in team_data.get("league_history", [])
        )
        tier_lookup = _tier_lookup(team_data)
        results = _completed_results_chronological(fixtures, tier_lookup)
        team_streaks = _streaks_for_team(page_key, display_name, logo_url, results)
        streaks.extend(
            replace(s, has_recent_league=has_recent_league)
            for s in team_streaks
            if s.length >= min_length
        )

    return streaks


def _numeric_range(lo: int, hi: int) -> str:
    return str(lo) if lo == hi else f"{lo}-{hi}"


def _format_span(span: LevelSpan, *, prefix_pyramid: bool) -> str:
    is_womens = not span.is_merit and span.min_tier >= WOMENS_MIN_TIER
    if is_womens:
        lo, hi = sorted((span.min_tier - WOMENS_MIN_TIER, span.max_tier - WOMENS_MIN_TIER))
        level_str = f"Women's Level {_numeric_range(lo, hi)}"
    else:
        lo, hi = sorted((span.min_tier, span.max_tier))
        level_str = f"Level {_numeric_range(lo, hi)}"

    if span.is_merit:
        comp_display = span.competition_key.replace("_", " ")
        return f"{comp_display}: {level_str}"
    return f"Pyramid: {level_str}" if prefix_pyramid else level_str


def format_level_range(level_spans: tuple[LevelSpan, ...]) -> str:
    """``"Level 6"``, ``"Level 5-6"`` across a promotion, or ``"CANDY: Level 2"`` for merit.

    A streak that crosses between the national pyramid and a merit competition
    (or between two merit competitions) prefixes every span with where it was
    played — e.g. ``"Midlands Reserve: Level 1 · Pyramid: Level 7"`` — since a
    bare ``"Level 7"`` next to a merit span would otherwise read as the same
    kind of level.
    """
    prefix_pyramid = len(level_spans) > 1
    return " · ".join(_format_span(span, prefix_pyramid=prefix_pyramid) for span in level_spans)


def _print_table(headers: list[str], rows: list[tuple[str, ...]]) -> None:
    if not rows:
        print("  (none)")
        return
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print("-" * (sum(col_widths) + 2 * (len(col_widths) - 1)))
    for row in rows:
        print(fmt.format(*row))


def print_report(streaks: list[WinStreak], *, top: int) -> None:
    ever = sorted(streaks, key=lambda s: (-s.length, s.start_date))
    ongoing = sorted(
        (s for s in streaks if s.still_going),
        key=lambda s: (-s.length, s.start_date),
    )

    print("\n" + "=" * 90)
    print(f"LONGEST WINNING STREAKS EVER - TOP {top}")
    print("=" * 90)
    _print_table(
        ["Rank", "Team", "Level", "From", "To", "Length"],
        [
            (
                str(i),
                s.display_name,
                format_level_range(s.level_spans),
                s.start_date,
                s.end_date,
                str(s.length),
            )
            for i, s in enumerate(ever[:top], start=1)
        ],
    )

    print("\n" + "=" * 90)
    print(f"LONGEST WINNING STREAKS ONGOING - TOP {top}")
    print("=" * 90)
    _print_table(
        ["Rank", "Team", "Level", "Since", "Length"],
        [
            (
                str(i),
                s.display_name,
                format_level_range(s.level_spans),
                s.start_date,
                str(s.length),
            )
            for i, s in enumerate(ongoing[:top], start=1)
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Report longest winning streaks by match result.")
    parser.add_argument(
        "--top", type=int, default=DEFAULT_TOP, help=f"Rows to show (default {DEFAULT_TOP})"
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=DEFAULT_MIN_LENGTH,
        help=f"Minimum streak length in matches to include (default {DEFAULT_MIN_LENGTH})",
    )
    args = parser.parse_args()

    setup_logging()
    streaks = collect_all_win_streaks(min_length=args.min_length)
    print_report(streaks, top=args.top)


if __name__ == "__main__":
    main()
