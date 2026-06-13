"""Rank leagues, teams, and within-league fixture pairs by travel time.

Uses routed driving minutes from ``rugby.distance_lookup`` when available,
otherwise estimates minutes from road/Haversine km at the UK average drive speed.

Fixtures are every distinct pair of geocoded teams in the same league file (not
an RFU fixture list).

Usage::

    python -m rugby.analysis.travel_rankings
    python -m rugby.analysis.travel_rankings --season 2025-2026 --top 15
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from itertools import combinations

from core import GeocodedLeague, GeocodedTeam
from rugby import DATA_DIR
from rugby.distance_lookup import DistanceLookup
from rugby.distances import league_average, league_display_name, team_average
from rugby.offshore_travel import AVG_UK_DRIVE_KMH, classify_region

DEFAULT_SEASON = "2025-2026"
DEFAULT_TOP = 10


@dataclass(frozen=True, slots=True)
class LeagueRanking:
    league: str
    avg_min: float
    avg_km: float
    team_count: int


@dataclass(frozen=True, slots=True)
class TeamRanking:
    team: str
    league: str
    avg_min: float
    avg_km: float
    is_offshore: bool = False


@dataclass(frozen=True, slots=True)
class FixtureRanking:
    home: str
    away: str
    league: str
    min: float
    km: float


def _valid_teams(league: GeocodedLeague) -> list[GeocodedTeam]:
    return [t for t in league["teams"] if "latitude" in t and "longitude" in t]


def _is_offshore_team(team: GeocodedTeam) -> bool:
    lat = team.get("latitude")
    lon = team.get("longitude")
    if lat is None or lon is None:
        return False
    return classify_region(float(lat), float(lon)) != "mainland"


def _pair_km_min(
    team1: GeocodedTeam, team2: GeocodedTeam, lookup: DistanceLookup
) -> tuple[float, float]:
    lat1 = float(team1["latitude"])
    lon1 = float(team1["longitude"])
    lat2 = float(team2["latitude"])
    lon2 = float(team2["longitude"])
    km = lookup.pair_km(lat1, lon1, lat2, lon2)
    mins = lookup.pair_min(lat1, lon1, lat2, lon2)
    if mins is None:
        mins = km / AVG_UK_DRIVE_KMH * 60.0
    return km, mins


def _team_avg_km_min(
    team: GeocodedTeam, teams: list[GeocodedTeam], lookup: DistanceLookup
) -> tuple[float, float]:
    """Average opponent km/min; falls back to estimated minutes when unrouted."""
    opponents = [o for o in teams if o["name"] != team["name"]]
    if not opponents:
        return 0.0, 0.0
    km_sum = 0.0
    min_sum = 0.0
    for opponent in opponents:
        km, mins = _pair_km_min(team, opponent, lookup)
        km_sum += km
        min_sum += mins
    n = len(opponents)
    return km_sum / n, min_sum / n


def load_season_leagues(season: str) -> list[tuple[str, GeocodedLeague]]:
    geocoded_dir = DATA_DIR / "geocoded_teams" / season
    if not geocoded_dir.is_dir():
        raise FileNotFoundError(f"geocoded_teams directory not found for season {season!r}")

    leagues: list[tuple[str, GeocodedLeague]] = []
    for json_file in sorted(geocoded_dir.rglob("*.json")):
        with json_file.open(encoding="utf-8") as f:
            league_data: GeocodedLeague = json.load(f)
        league_name = league_display_name(json_file, geocoded_dir, league_data)
        leagues.append((league_name, league_data))
    return leagues


def build_rankings(
    season: str, lookup: DistanceLookup
) -> tuple[list[LeagueRanking], list[TeamRanking], list[FixtureRanking]]:
    league_rows: list[LeagueRanking] = []
    team_rows: list[TeamRanking] = []
    fixture_rows: list[FixtureRanking] = []

    for league_name, league_data in load_season_leagues(season):
        teams = _valid_teams(league_data)
        if len(teams) < 2:
            continue

        avg_km, avg_min, _excl_km, _excl_min = league_average(league_data, lookup)
        if avg_min is None and avg_km:
            avg_min = avg_km / AVG_UK_DRIVE_KMH * 60.0
        league_rows.append(
            LeagueRanking(
                league=league_name,
                avg_min=avg_min or 0.0,
                avg_km=avg_km,
                team_count=len(league_data["teams"]),
            )
        )

        for team in teams:
            t_avg_km, t_avg_min = team_average(team, teams, lookup)
            if t_avg_min is None:
                t_avg_km, t_avg_min = _team_avg_km_min(team, teams, lookup)
            team_rows.append(
                TeamRanking(
                    team=team["name"],
                    league=league_name,
                    avg_min=t_avg_min or 0.0,
                    avg_km=t_avg_km,
                    is_offshore=_is_offshore_team(team),
                )
            )

        for team_a, team_b in combinations(teams, 2):
            km, mins = _pair_km_min(team_a, team_b, lookup)
            fixture_rows.append(
                FixtureRanking(
                    home=team_a["name"],
                    away=team_b["name"],
                    league=league_name,
                    min=mins,
                    km=km,
                )
            )

    return league_rows, team_rows, fixture_rows


def _format_min_km(mins: float, km: float) -> str:
    return f"{mins:.0f} min avg, {km:.1f} km avg"


def _format_fixture(mins: float, km: float) -> str:
    return f"{mins:.0f} min, {km:.1f} km"


def _print_section(title: str, rows: list[str]) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    if not rows:
        print("(none)")
        return
    for line in rows:
        print(line)


def print_rankings(
    season: str,
    lookup: DistanceLookup,
    *,
    top: int,
    league_rows: list[LeagueRanking],
    team_rows: list[TeamRanking],
    fixture_rows: list[FixtureRanking],
) -> None:
    source = "routed" if lookup.has_routed else "haversine"
    print(f"Travel rankings — {season}")
    print(
        f"  Leagues: {len(league_rows)}  Teams: {len(team_rows)}  Fixture pairs: {len(fixture_rows)}"
    )
    print(
        f"  Distance source: {source}"
        + (f" ({lookup.n_routed} geocodes)" if lookup.has_routed else "")
    )
    print(
        "  Minutes: routed driving time when cached; otherwise estimated from km "
        f"@ {AVG_UK_DRIVE_KMH:.0f} km/h"
    )

    by_league_min = sorted(league_rows, key=lambda r: (r.avg_min, r.avg_km))
    by_team_min = sorted(team_rows, key=lambda r: (r.avg_min, r.avg_km))
    by_mainland_team_min = sorted(
        (r for r in team_rows if not r.is_offshore),
        key=lambda r: (r.avg_min, r.avg_km),
    )
    by_fixture_min = sorted(fixture_rows, key=lambda r: (r.min, r.km))

    _print_section(
        f"LEAGUES — LEAST TRAVEL (top {top})",
        [
            f"{i}. {r.league}: {_format_min_km(r.avg_min, r.avg_km)} ({r.team_count} teams)"
            for i, r in enumerate(by_league_min[:top], 1)
        ],
    )
    _print_section(
        f"LEAGUES — MOST TRAVEL (top {top})",
        [
            f"{i}. {r.league}: {_format_min_km(r.avg_min, r.avg_km)} ({r.team_count} teams)"
            for i, r in enumerate(reversed(by_league_min[-top:]), 1)
        ],
    )
    _print_section(
        f"TEAMS — LEAST TRAVEL (top {top})",
        [
            f"{i}. {r.team} | {r.league} | {_format_min_km(r.avg_min, r.avg_km)}"
            for i, r in enumerate(by_team_min[:top], 1)
        ],
    )
    _print_section(
        f"TEAMS — MOST TRAVEL (top {top}, mainland teams only)",
        [
            f"{i}. {r.team} | {r.league} | {_format_min_km(r.avg_min, r.avg_km)}"
            for i, r in enumerate(reversed(by_mainland_team_min[-top:]), 1)
        ],
    )
    _print_section(
        f"FIXTURES — SHORTEST (top {top})",
        [
            f"{i}. {r.home} vs {r.away} | {r.league} | {_format_fixture(r.min, r.km)}"
            for i, r in enumerate(by_fixture_min[:top], 1)
        ],
    )
    _print_section(
        f"FIXTURES — LONGEST (top {top})",
        [
            f"{i}. {r.home} vs {r.away} | {r.league} | {_format_fixture(r.min, r.km)}"
            for i, r in enumerate(reversed(by_fixture_min[-top:]), 1)
        ],
    )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank leagues, teams, and within-league pairs by travel time"
    )
    parser.add_argument(
        "--season",
        default=DEFAULT_SEASON,
        help=f"Season under geocoded_teams/ (default: {DEFAULT_SEASON})",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help=f"Number of rows to show in each section (default: {DEFAULT_TOP})",
    )
    args = parser.parse_args()

    if args.top < 1:
        parser.error("--top must be at least 1")

    lookup = DistanceLookup.load()
    league_rows, team_rows, fixture_rows = build_rankings(args.season, lookup)
    print_rankings(
        args.season,
        lookup,
        top=args.top,
        league_rows=league_rows,
        team_rows=team_rows,
        fixture_rows=fixture_rows,
    )


if __name__ == "__main__":
    main()
