"""
Calculate travel distance statistics for all teams and leagues.

Uses the routed (road) distance matrix from ``rugby.distances_routed`` when
available, otherwise falls back to Haversine. Output JSON contains both km and
optional minutes per team / league, plus a ``summary.distance_source`` flag
indicating which method was used.
"""

import argparse
import json
import math

from core import (
    GeocodedLeague,
    GeocodedTeam,
    LeagueTravelDistances,
    TeamTravelDistances,
    TravelDistances,
)
from rugby import DATA_DIR
from rugby.distance_lookup import DistanceLookup


def distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two coordinates using Haversine formula.
    Returns distance in kilometers.
    """
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) * math.sin(dlat / 2) + math.cos(math.radians(lat1)) * math.cos(
        math.radians(lat2)
    ) * math.sin(dlon / 2) * math.sin(dlon / 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    d = 6371 * c
    return d


# Each cache key is the alphabetically-ordered pair of team names; values are
# (km, minutes-or-None).
_pair_cache: dict[tuple[str, str], tuple[float, float | None]] = {}


def _team_pair_values(
    team1: GeocodedTeam, team2: GeocodedTeam, lookup: DistanceLookup
) -> tuple[float, float | None]:
    key = (min(team1["name"], team2["name"]), max(team1["name"], team2["name"]))
    if key not in _pair_cache:
        lat1 = team1.get("latitude", 0.0)
        lon1 = team1.get("longitude", 0.0)
        lat2 = team2.get("latitude", 0.0)
        lon2 = team2.get("longitude", 0.0)
        _pair_cache[key] = (
            lookup.pair_km(lat1, lon1, lat2, lon2),
            lookup.pair_min(lat1, lon1, lat2, lon2),
        )
    return _pair_cache[key]


def team_pair_distance(team1: GeocodedTeam, team2: GeocodedTeam) -> float:
    """Backwards-compatible km-only pair lookup (Haversine when no routed cache).

    Kept so external callers / tests that import this function keep working.
    """
    return _team_pair_values(team1, team2, _DEFAULT_LOOKUP)[0]


def team_totals(
    team: GeocodedTeam, teams: list[GeocodedTeam], lookup: DistanceLookup
) -> tuple[float, float | None]:
    """Sum of pair distances (km) and (sum of pair minutes) for *team* vs *teams*.

    Minutes are returned as ``None`` if any required pair has no routed minutes
    (so we never mix km from routed and minutes from "no source").
    """
    total_km = 0.0
    total_min: float | None = 0.0
    for opponent in teams:
        if opponent["name"] == team["name"]:
            continue
        km, mins = _team_pair_values(team, opponent, lookup)
        total_km += km
        if total_min is not None:
            if mins is None:
                total_min = None
            else:
                total_min += mins
    return total_km, total_min


def team_average(
    team: GeocodedTeam, teams: list[GeocodedTeam], lookup: DistanceLookup
) -> tuple[float, float | None]:
    total_km, total_min = team_totals(team, teams, lookup)
    n = len(teams) - 1
    if n <= 0:
        return 0.0, None
    return total_km / n, (total_min / n if total_min is not None else None)


def league_average(league: GeocodedLeague, lookup: DistanceLookup) -> tuple[float, float | None]:
    valid_teams = [team for team in league["teams"] if "latitude" in team and "longitude" in team]
    if not valid_teams:
        return 0.0, None
    total_km = 0.0
    total_min: float | None = 0.0
    for team in valid_teams:
        avg_km, avg_min = team_average(team, valid_teams, lookup)
        total_km += avg_km
        if total_min is not None:
            if avg_min is None:
                total_min = None
            else:
                total_min += avg_min
    n = len(valid_teams)
    return total_km / n, (total_min / n if total_min is not None else None)


# Default lookup used by the historical helpers (``team_pair_distance`` etc.).
# Initialised to a Haversine-only lookup; ``main`` rebinds it once a season is
# known so the helpers see the routed cache.
_DEFAULT_LOOKUP: DistanceLookup = DistanceLookup()


def main() -> None:
    global _DEFAULT_LOOKUP

    parser = argparse.ArgumentParser(description="Calculate team and league travel distances")
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to calculate (e.g., 2024-2025, 2025-2026). Default: 2025-2026",
    )
    args = parser.parse_args()
    print("Calculating team and league travel distances...")

    # Load all geocoded teams
    geocoded_dir = DATA_DIR / "geocoded_teams" / args.season

    if not geocoded_dir.exists():
        print("Error: geocoded_teams directory not found")
        return

    lookup = DistanceLookup.load()
    _DEFAULT_LOOKUP = lookup
    print(
        f"  Distance source: {'routed' if lookup.has_routed else 'haversine'}"
        + (f" ({lookup.n_routed} geocodes)" if lookup.has_routed else "")
    )

    all_teams_data: dict[str, TeamTravelDistances] = {}
    league_stats: dict[str, LeagueTravelDistances] = {}
    missing_routed: list[tuple[str, float, float]] = []

    # Process each league file
    for json_file in sorted(geocoded_dir.rglob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            league_data: GeocodedLeague = json.load(f)

        league_name = league_data["league_name"]
        rel_parts = json_file.relative_to(geocoded_dir).parts
        if len(rel_parts) >= 3 and rel_parts[0] == "merit":
            comp_name = rel_parts[1].replace("_", " ")
            if comp_name.lower() not in league_name.lower():
                league_name = f"{comp_name} {league_name}"

        if lookup.has_routed:
            for team in league_data.get("teams", []):
                lat = team.get("latitude")
                lng = team.get("longitude")
                if lat is None or lng is None:
                    continue
                if lookup.coord_id(lat, lng) is None:
                    missing_routed.append((team.get("name", ""), lat, lng))

        avg_km, avg_min = league_average(league_data, lookup)
        league_stats[league_name] = {
            "league_name": league_name,
            "avg_distance_km": round(avg_km, 2),
            "team_count": len(league_data["teams"]),
        }
        if avg_min is not None:
            league_stats[league_name]["avg_duration_min"] = round(avg_min, 2)

        valid_teams = [
            team for team in league_data["teams"] if "latitude" in team and "longitude" in team
        ]
        for team in valid_teams:
            t_avg_km, t_avg_min = team_average(team, valid_teams, lookup)
            t_total_km, t_total_min = team_totals(team, valid_teams, lookup)
            entry: TeamTravelDistances = {
                "name": team["name"],
                "league": league_name,
                "avg_distance_km": round(t_avg_km, 2),
                "total_distance_km": round(t_total_km, 2),
            }
            if t_avg_min is not None:
                entry["avg_duration_min"] = round(t_avg_min, 2)
            if t_total_min is not None:
                entry["total_duration_min"] = round(t_total_min, 2)
            all_teams_data[team["name"]] = entry

    # Sort teams by average distance
    all_teams_data = dict(sorted(all_teams_data.items(), key=lambda x: x[1]["avg_distance_km"]))

    # Create output structure
    output: TravelDistances = {
        "teams": all_teams_data,
        "leagues": league_stats,
        "summary": {
            "total_teams": len(all_teams_data),
            "total_leagues": len(league_stats),
            "overall_avg_distance_km": (
                round(
                    sum(t["avg_distance_km"] for t in all_teams_data.values())
                    / len(all_teams_data),
                    2,
                )
                if all_teams_data
                else 0
            ),
            "distance_source": "routed" if lookup.has_routed else "haversine",
        },
    }

    # Save to JSON
    if not (DATA_DIR / "distance_cache").exists():
        (DATA_DIR / "distance_cache").mkdir(parents=True)
    output_file = DATA_DIR / "distance_cache" / f"{args.season}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print("\nDistance calculations complete.")
    print(f"  Output saved to: {output_file}")
    print(f"  Teams processed: {len(all_teams_data)}")
    print(f"  Leagues processed: {len(league_stats)}")
    print(f"  Overall average distance: {output["summary"]["overall_avg_distance_km"]} km")
    if missing_routed:
        examples = ", ".join(
            f"{n} ({lat:.4f},{lng:.4f})" for n, lat, lng in sorted(set(missing_routed))[:5]
        )
        print(
            f"\n  WARNING: {len(set(missing_routed))} team(s) in season {args.season} "
            "have no routed-cache entry; those teams used Haversine fallback. "
            "Run `make routed-distances` to refresh the cache. "
            f"Examples: {examples}"
        )

    # Print top 5 and bottom 5 teams
    print("\n" + "=" * 80)
    print("TOP 5 TEAMS - LOWEST AVERAGE TRAVEL DISTANCE")
    print("=" * 80)
    for i, team in enumerate(list(all_teams_data.values())[:5], 1):
        print(f"{i}. {team["name"]} ({team["league"]}): {team["avg_distance_km"]} km")

    print("\n" + "=" * 80)
    print("BOTTOM 5 TEAMS - HIGHEST AVERAGE TRAVEL DISTANCE")
    print("=" * 80)
    for i, team in enumerate(list(all_teams_data.values())[-5:], len(all_teams_data) - 4):
        print(f"{i}. {team["name"]} ({team["league"]}): {team["avg_distance_km"]} km")

    # Print league rankings
    sorted_leagues = sorted(league_stats.values(), key=lambda x: x["avg_distance_km"])
    print("\n" + "=" * 80)
    print("LEAGUE RANKINGS - AVERAGE TRAVEL DISTANCE")
    print("=" * 80)
    for i, league in enumerate(sorted_leagues[:10], 1):
        print(
            f"{i}. {league["league_name"]}: {league["avg_distance_km"]} km ({league["team_count"]} teams)"
        )
    print()


if __name__ == "__main__":
    main()
