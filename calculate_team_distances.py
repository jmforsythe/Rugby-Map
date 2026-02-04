"""
Calculate travel distance statistics for all teams and leagues.
Outputs a JSON file with team and league distance data that can be used in map generation.
"""

import argparse
import json
import math
from pathlib import Path

from utils import (
    GeocodedLeague,
    GeocodedTeam,
    LeagueTravelDistances,
    TeamTravelDistances,
    TravelDistances,
)


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


distance_cache: dict[tuple[GeocodedTeam, GeocodedTeam], float] = {}


def team_pair_distance(team1: GeocodedTeam, team2: GeocodedTeam) -> float:
    if (min(team1["name"], team2["name"]), max(team1["name"], team2["name"])) not in distance_cache:
        distance_cache[(min(team1["name"], team2["name"]), max(team1["name"], team2["name"]))] = (
            distance(team1["latitude"], team1["longitude"], team2["latitude"], team2["longitude"])
        )
    return distance_cache[(min(team1["name"], team2["name"]), max(team1["name"], team2["name"]))]


def team_total_distance(team: GeocodedTeam, teams: list[GeocodedTeam]) -> float:
    total_distance = 0
    for opponent in teams:
        if opponent["name"] != team["name"]:
            dist = team_pair_distance(team, opponent)
            total_distance += dist
    return total_distance


def team_average_distance(team: GeocodedTeam, teams: list[GeocodedTeam]) -> float:
    total_distance = team_total_distance(team, teams)
    opponent_count = len(teams) - 1
    return total_distance / opponent_count if opponent_count > 0 else 0


def league_average_distance(league: GeocodedLeague) -> float:
    valid_teams = [team for team in league["teams"] if "latitude" in team and "longitude" in team]
    total_avg_distance = 0
    for team in valid_teams:
        total_avg_distance += team_average_distance(team, valid_teams)
    return total_avg_distance / len(valid_teams) if valid_teams else 0


def main():
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
    geocoded_dir = Path("geocoded_teams") / args.season

    if not geocoded_dir.exists():
        print("Error: geocoded_teams directory not found")
        return

    all_teams_data: dict[str, TeamTravelDistances] = {}
    league_stats: dict[str, LeagueTravelDistances] = {}

    # Process each league file
    for json_file in sorted(geocoded_dir.glob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            league_data: GeocodedLeague = json.load(f)

        league_stats[league_data["league_name"]] = {
            "league_name": league_data["league_name"],
            "avg_distance_km": league_average_distance(league_data),
            "team_count": len(league_data["teams"]),
        }
        valid_teams = [
            team for team in league_data["teams"] if "latitude" in team and "longitude" in team
        ]
        for team in valid_teams:
            avg_distance = team_average_distance(team, valid_teams)
            all_teams_data[team["name"]] = {
                "name": team["name"],
                "league": league_data["league_name"],
                "avg_distance_km": round(avg_distance, 2),
                "total_distance_km": round(team_total_distance(team, valid_teams), 2),
            }

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
        },
    }

    # Save to JSON
    if not Path("distance_cache_folder").exists():
        Path("distance_cache_folder").mkdir(parents=True)
    output_file = Path("distance_cache_folder") / f"{args.season}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print("\n✓ Distance calculations complete!")
    print(f"  Output saved to: {output_file}")
    print(f"  Teams processed: {len(all_teams_data)}")
    print(f"  Leagues processed: {len(league_stats)}")
    print(f"  Overall average distance: {output["summary"]["overall_avg_distance_km"]} km")

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
