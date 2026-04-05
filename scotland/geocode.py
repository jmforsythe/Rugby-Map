"""
Geocode Scottish Rugby team addresses using coordinates from the club directory.

The club directory (club_directory_cache.json) already contains lat/lng for
every club, so this script simply looks up each team's coordinates from that
cache rather than calling an external geocoding API.

For any team whose club was not found in the directory (address is None),
it falls back to the shared Nominatim geocoder.

Output: scotland/geocoded_teams/{season}/{section}/{league}.json
"""

from __future__ import annotations

import argparse
import json

from rugby.geocode import geocode_with_nominatim
from scotland import DATA_DIR
from scotland.addresses import ClubInfo, build_club_lookup, fetch_club_directory, match_team_to_club


def _build_address_to_club(clubs: list[ClubInfo]) -> dict[str, ClubInfo]:
    """Build an address -> ClubInfo lookup for matching by address string."""
    lookup: dict[str, ClubInfo] = {}
    for club in clubs:
        if club.address:
            lookup[club.address] = club
    return lookup


def geocode_team(
    team: dict,
    club_lookup: dict[str, ClubInfo],
    address_lookup: dict[str, ClubInfo],
) -> dict:
    """Add lat/lng to a team dict using the club directory.

    Tries to match by team name first, then by address string.
    """
    result = dict(team)

    club = match_team_to_club(team["name"], club_lookup)

    if not club and team.get("address"):
        club = address_lookup.get(team["address"])

    if club:
        result["latitude"] = club.lat
        result["longitude"] = club.lng
        result["formatted_address"] = club.address or team.get("address", "")
        result["place_id"] = club.id
        return result

    if team.get("address"):
        coords, _ = geocode_with_nominatim(team["address"])
        if coords:
            result.update(coords)
            return result

    return result


def process_season(season: str) -> None:
    """Process all team_addresses files for a season into geocoded_teams."""
    clubs = fetch_club_directory()
    club_lookup = build_club_lookup(clubs)
    address_lookup = _build_address_to_club(clubs)

    address_base = DATA_DIR / "team_addresses" / season
    if not address_base.exists():
        print(f"Error: {address_base} not found. Run fetch_addresses.py first.")
        return

    output_base = DATA_DIR / "geocoded_teams" / season

    address_files = sorted(f for f in address_base.rglob("*.json") if not f.name.startswith("_"))
    print(f"Found {len(address_files)} address files to process")

    total_teams = 0
    geocoded_count = 0
    failed_teams: list[tuple[str, str]] = []

    for address_file in address_files:
        relative = address_file.relative_to(address_base)
        output_file = output_base / relative

        if output_file.exists():
            print(f"Skipping {address_file.name} (already exists)")
            continue

        with open(address_file, encoding="utf-8") as f:
            address_data = json.load(f)

        league_name = address_data["league_name"]
        print(f"\nGeocoding: {league_name}")

        geocoded_teams = []

        for team in address_data["teams"]:
            total_teams += 1
            result = geocode_team(team, club_lookup, address_lookup)

            if "latitude" in result:
                geocoded_count += 1
                print(f"  {team['name']}: {result['latitude']}, {result['longitude']}")
            else:
                failed_teams.append((team["name"], league_name))
                print(f"  {team['name']}: FAILED")

            geocoded_teams.append(result)

        output_data = {
            "league_name": league_name,
            "league_url": address_data["league_url"],
            "teams": geocoded_teams,
            "team_count": len(geocoded_teams),
        }

        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print(f"  Saved: {output_file}")

    print(f"\n{'='*80}")
    print(f"Complete! Geocoded data saved to {output_base}")
    print(f"  {geocoded_count}/{total_teams} teams geocoded")
    print(f"{'='*80}")

    if failed_teams:
        print(f"\nFAILED TEAMS ({len(failed_teams)}):")
        for name, league in failed_teams:
            print(f"  {name} ({league})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Geocode Scottish Rugby team addresses.")
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to process (e.g. 2024-2025). Default: 2025-2026",
    )
    args = parser.parse_args()

    print(f"Geocoding Scottish Rugby teams for season: {args.season}")
    process_season(args.season)


if __name__ == "__main__":
    main()
