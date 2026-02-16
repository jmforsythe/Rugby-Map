import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from fetch_addresses import team_name_to_club_name
from generate_webpages import get_common_css, get_footer_html
from make_tier_maps import extract_tier
from utils import (
    GeocodedLeague,
    TravelDistances,
    get_google_analytics_script,
    team_name_to_filepath,
)

IS_PRODUCTION = False


def collect_all_teams_data() -> dict[str, dict]:
    """
    Collect all team data from geocoded files across all seasons.

    Returns:
        Dictionary mapping team names to their aggregated data including:
        - All league participations across seasons
        - Latest address, coordinates, logo
        - URL to team page
    """
    geocoded_dir = Path("geocoded_teams")
    teams_data = defaultdict(
        lambda: {
            "name": None,
            "url": None,
            "image_url": None,
            "address": None,
            "latitude": None,
            "longitude": None,
            "formatted_address": None,
            "league_history": [],  # List of (season, league_name, position) tuples
        }
    )

    if not geocoded_dir.exists():
        return {}

    # Process each season
    for season_dir in sorted(geocoded_dir.iterdir()):
        if not season_dir.is_dir() or not re.match(r"\d{4}-\d{4}", season_dir.name):
            continue

        season = season_dir.name

        # Process each league file in the season
        for league_file in season_dir.glob("*.json"):
            with open(league_file, encoding="utf-8") as f:
                league_data: GeocodedLeague = json.load(f)

            league_name = league_data["league_name"]

            # Process each team in the league
            for position, team in enumerate(league_data["teams"], start=1):
                team_name = team["name"]

                # Update team data (using latest values)
                teams_data[team_name]["name"] = team_name
                teams_data[team_name]["url"] = team.get("url")
                teams_data[team_name]["image_url"] = team.get("image_url")

                # Update address/location if available
                if team.get("address"):
                    teams_data[team_name]["address"] = team["address"]
                if team.get("latitude"):
                    teams_data[team_name]["latitude"] = team["latitude"]
                if team.get("longitude"):
                    teams_data[team_name]["longitude"] = team["longitude"]
                if team.get("formatted_address"):
                    teams_data[team_name]["formatted_address"] = team["formatted_address"]

                # Add league participation to history
                teams_data[team_name]["league_history"].append(
                    {
                        "season": season,
                        "league": league_name,
                        "position": position,
                        "tier": extract_tier(
                            league_name.replace(" ", "_").replace("/", "_") + ".json", season
                        ),
                    }
                )

    return dict(teams_data)


def find_club_teams(team_name: str, all_teams: dict[str, dict]) -> list[str]:
    """
    Find other teams from the same club based on address matching.

    Compares addresses and coordinates to identify teams sharing the same physical location.

    Args:
        team_name: Name of the team to find club mates for
        all_teams: Dictionary of all teams data

    Returns:
        List of team names from the same club (excluding the input team)
    """
    team_data = all_teams.get(team_name)
    if not team_data:
        return []

    # Get this team's address
    team_address = team_data.get("address")
    team_lat = team_data.get("latitude")
    team_lon = team_data.get("longitude")

    if not team_address and not (team_lat and team_lon):
        return []

    # Find other teams with the same address or coordinates
    club_teams = []
    for other_team_name, other_data in all_teams.items():
        if other_team_name == team_name:
            continue

        other_address = other_data.get("address")
        other_lat = other_data.get("latitude")
        other_lon = other_data.get("longitude")

        # Match by address or match by coordinates (exact match)
        if (
            team_address
            and other_address
            and team_address == other_address
            or (
                team_lat
                and team_lon
                and other_lat
                and other_lon
                and team_lat == other_lat
                and team_lon == other_lon
            )
        ):
            club_teams.append(other_team_name)

    return sorted(club_teams)


def get_team_page_html(
    team_name: str,
    team_data: dict,
    all_teams: dict[str, dict],
    travel_distances_by_season: dict[str, TravelDistances],
) -> str:
    """Generate HTML content for a team's individual page."""

    # Get club teams
    club_teams = find_club_teams(team_name, all_teams)

    # Sort league history by season (most recent first)
    league_history = sorted(team_data["league_history"], key=lambda x: x["season"], reverse=True)

    # Group by season for display
    seasons_by_year = defaultdict(list)
    for entry in league_history:
        seasons_by_year[entry["season"]].append(entry)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{team_name} - English Rugby Union Team Info</title>
    <style>
        {get_common_css()}
        .team-header {{
            text-align: center;
            margin-bottom: 2em;
        }}
        .team-logo {{
            max-width: 150px;
            max-height: 150px;
            margin: 1em auto;
            display: block;
        }}
        .info-section {{
            background: white;
            border-radius: 8px;
            padding: 1.5em;
            margin: 1.5em 0;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .info-section h2 {{
            color: #2c3e50;
            font-size: 1.3em;
            margin-top: 0;
            margin-bottom: 1em;
            border-bottom: 2px solid #0066cc;
            padding-bottom: 0.5em;
        }}
        .info-row {{
            margin: 0.5em 0;
            line-height: 1.8;
        }}
        .info-label {{
            font-weight: 600;
            color: #555;
        }}
        .club-teams {{
            list-style: none;
            padding: 0;
        }}
        .club-teams li {{
            margin: 0.5em 0;
        }}
        .club-teams a {{
            display: inline-block;
            color: #0066cc;
            text-decoration: none;
            padding: 0.3em 0.8em;
            background: #f5f7fa;
            border-radius: 4px;
            border: 1px solid #e0e0e0;
            transition: all 0.2s;
        }}
        .club-teams a:hover {{
            background: #0066cc;
            color: white;
            transform: translateY(-1px);
        }}
        .league-history-table {{
            width: 100%;
            border-collapse: collapse;
        }}
        .league-history-table th {{
            background: #f5f7fa;
            padding: 0.8em;
            text-align: left;
            font-weight: 600;
            color: #2c3e50;
            border-bottom: 2px solid #0066cc;
        }}
        .league-history-table td {{
            padding: 0.8em;
            border-bottom: 1px solid #e0e0e0;
        }}
        .league-history-table tr:hover {{
            background: #f9f9f9;
        }}
        .position {{
            font-weight: 600;
            color: #0066cc;
        }}
        .address {{
            color: #666;
            font-style: italic;
        }}
    </style>
    {get_google_analytics_script()}
</head>
<body>
    <div class="back-link">
        <a href="./{ "" if IS_PRODUCTION else "index.html" }">← All Teams</a>
    </div>

    <div class="team-header">
        <h1>{team_name}</h1>
"""

    # Add logo if available
    if team_data.get("image_url"):
        html += f'        <img src="{team_data["image_url"]}" alt="{team_name} logo"'
        html += ' onerror="this.onerror=null; this.src=\'https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg\'" class="team-logo">\n'

    html += """    </div>
"""

    # Basic Info Section
    html += """    <div class="info-section">
        <h2>Basic Information</h2>
"""

    if team_data.get("formatted_address") or team_data.get("address"):
        address = team_data.get("formatted_address") or team_data.get("address")
        html += f'        <div class="info-row"><span class="info-label">Address:</span> <span class="address">{address}</span></div>\n'

    if team_data.get("url"):
        html += f'        <div class="info-row"><span class="info-label">RFU Profile:</span> <a href="{team_data["url"]}" target="_blank">View on England Rugby</a></div>\n'

    html += """    </div>
"""

    # Club Teams Section
    if club_teams:
        html += """    <div class="info-section">
        <h2>Other Teams at This Club</h2>
        <ul class="club-teams">
"""
        for club_team in club_teams:
            html += f'            <li><a href="{team_name_to_filepath(club_team)}">{club_team}</a></li>\n'

        html += """        </ul>
    </div>
"""

    # League History Section
    if league_history:
        html += """    <div class="info-section">
        <h2>League History</h2>
        <table class="league-history-table">
            <thead>
                <tr>
                    <th>Season</th>
                    <th>Tier: League</th>
                    <th>Position</th>
                    <th>Travel Distance (Average/Total)</th>
                </tr>
            </thead>
            <tbody>
"""

        for entry in league_history:
            season = entry["season"]
            league = entry["league"]
            position = entry["position"]
            tier = entry["tier"]

            # Don't show position for current season (in progress)
            if season == "2025-2026":
                position_display = '<span style="color: #666; font-style: italic;">Current</span>'
            else:
                position_display = f'<span class="position">#{position}</span>'

            # Get travel distances for this season
            travel_info = "N/A"
            if season in travel_distances_by_season:
                season_data = travel_distances_by_season[season]
                if "teams" in season_data and team_name in season_data["teams"]:
                    team_distances = season_data["teams"][team_name]
                    avg_dist = team_distances.get("avg_distance_km")
                    total_dist = team_distances.get("total_distance_km")

                    if avg_dist is not None and total_dist is not None:
                        travel_info = f"{avg_dist:.1f} km / {total_dist:.0f} km"
                    elif avg_dist is not None:
                        travel_info = f"{avg_dist:.1f} km avg"
                    elif total_dist is not None:
                        travel_info = f"{total_dist:.0f} km total"

            html += f"""                <tr>
                    <td>{season}</td>
                    <td>{tier[0]%100}: {league}</td>
                    <td>{position_display}</td>
                    <td>{travel_info}</td>
"""

        html += """            </tbody>
        </table>
    </div>
"""

    # Footer
    html += f"""
{get_footer_html()}
</body>
</html>
"""

    return html


def load_travel_distances() -> dict[str, TravelDistances]:
    """Load travel distances from cache files for all seasons.

    Returns:
        Dictionary mapping season -> TravelDistances
    """
    distances_dir = Path("distance_cache_folder")
    travel_distances_by_season = {}

    if not distances_dir.exists():
        return {}

    for distance_file in distances_dir.glob("*.json"):
        season = distance_file.stem  # e.g., "2018-2019"

        try:
            with open(distance_file, encoding="utf-8") as f:
                data = json.load(f)
                travel_distances_by_season[season] = data
        except Exception as e:
            print(f"  Warning: Could not load distances for {season}: {e}")

    return travel_distances_by_season


def generate_team_pages() -> None:
    """Generate individual HTML pages for all teams."""
    print("\nGenerating individual team pages...")

    # Collect all team data
    print("  Collecting team data from all seasons...")
    all_teams = collect_all_teams_data()

    if not all_teams:
        print("  No team data found!")
        return

    print(f"  Found {len(all_teams)} unique teams")

    # Load travel distances
    print("  Loading travel distances...")
    travel_distances_by_season = load_travel_distances()
    print(f"  Loaded distances for {len(travel_distances_by_season)} seasons")

    # Create teams directory
    teams_dir = Path("tier_maps/teams")
    teams_dir.mkdir(parents=True, exist_ok=True)

    # Generate page for each team
    generated_count = 0
    for team_name, team_data in all_teams.items():
        try:
            html_content = get_team_page_html(
                team_name, team_data, all_teams, travel_distances_by_season
            )

            # Create filename from team name
            filename = team_name_to_filepath(team_name)
            filepath = teams_dir / filename

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_content)

            generated_count += 1

        except Exception as e:
            print(f"  ✗ Error generating page for {team_name}: {e}")

    print(f"  ✓ Generated {generated_count} team pages in {teams_dir}")


def generate_teams_index() -> None:
    """Generate the teams/index.html page with searchable list of all teams."""
    teams_dir = Path("tier_maps/teams")
    if not teams_dir.exists():
        print("  ✗ Teams directory doesn't exist")
        return

    # Get all team HTML files
    team_files = sorted(teams_dir.glob("*.html"))
    if not team_files:
        print("  ✗ No team HTML files found")
        return

    # Extract team names from filenames (remove .html and convert underscores to spaces)
    teams_list = []
    for file_path in team_files:
        if file_path.name == "index.html":
            continue
        filename = file_path.name[:-5]  # Remove .html
        # Convert filename back to display name (rough conversion)
        display_name = filename.replace("_", " ")
        teams_list.append({"file": file_path.name, "name": display_name})

    # Sort by club name (remove II/III/IV suffixes for sorting)
    teams_list.sort(key=lambda x: team_name_to_club_name(x["name"]).lower())

    # Generate JavaScript array
    teams_js = ",\n            ".join(
        f'{{file: "{t["file"]}", name: "{t["name"]}"}}' for t in teams_list
    )

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>All Teams - English Rugby Union</title>
    <style>
        {get_common_css()}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 40px auto;
            padding: 0 20px;
            line-height: 1.6;
            color: #333;
            background: #f9f9f9;
        }}
        h1 {{
            font-size: 2.2em;
            margin-bottom: 0.3em;
            color: #2c3e50;
            text-align: center;
        }}
        .search-box {{
            text-align: center;
            margin: 2em 0;
        }}
        #searchInput {{
            width: 100%;
            max-width: 500px;
            padding: 12px 20px;
            font-size: 16px;
            border: 2px solid #e0e0e0;
            border-radius: 25px;
            outline: none;
            transition: border-color 0.2s;
        }}
        #searchInput:focus {{
            border-color: #0066cc;
        }}
        .team-count {{
            text-align: center;
            color: #666;
            margin: 1em 0 2em 0;
        }}
        .teams-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 1em;
            margin: 2em 0;
        }}
        .team-card {{
            background: white;
            padding: 1em 1.5em;
            border-radius: 6px;
            border: 1px solid #e0e0e0;
            transition: all 0.2s;
        }}
        .team-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,102,204,0.2);
            border-color: #0066cc;
        }}
        .team-card a {{
            color: #2c3e50;
            text-decoration: none;
            font-size: 1.05em;
            display: block;
            padding: 0;
            background: none;
            border: none;
            border-radius: 0;
            box-shadow: none;
            transform: none;
        }}
        .team-card:hover a {{
            color: #0066cc;
            background: none;
            border: none;
            transform: none;
            box-shadow: none;
        }}
        .no-results {{
            text-align: center;
            color: #666;
            font-size: 1.2em;
            margin: 3em 0;
            display: none;
        }}
        .footer {{
            margin-top: 3em;
            padding-top: 2em;
            border-top: 1px solid #ddd;
            font-size: 0.9em;
            color: #666;
            text-align: center;
            background: white;
            border-radius: 8px;
            padding: 1.5em;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .footer a {{
            color: #0066cc;
            text-decoration: none;
        }}
        .footer a:hover {{
            text-decoration: underline;
        }}
        .footer p {{
            margin: 0.5em 0;
        }}
    </style>

    {get_google_analytics_script()}

</head>
<body>
    <div class="back-link">
        <a href="../index.html">← Back to Season Maps</a>
    </div>

    <h1>All English Rugby Union Teams</h1>

    <div class="search-box">
        <input type="text" id="searchInput" placeholder="Search teams...">
    </div>

    <div class="team-count">
        <span id="visibleCount"></span> teams
    </div>

    <div class="teams-grid" id="teamsGrid"></div>

    <div class="no-results" id="noResults">No teams found matching your search.</div>

    <div class="footer">
        <p><a href="https://github.com/jmforsythe/Rugby-Map">View on GitHub</a></p>
        <p>Data sources: <a href="https://www.englandrugby.com/">England Rugby (RFU)</a> <a href="https://geoportal.statistics.gov.uk/">ONS</a> <a href="https://nominatim.openstreetmap.org/">OpenStreetMap</a></p>
    </div>

    <script>
        const teams = [
            {teams_js}
        ];

        const teamsGrid = document.getElementById('teamsGrid');
        const searchInput = document.getElementById('searchInput');
        const visibleCount = document.getElementById('visibleCount');
        const noResults = document.getElementById('noResults');

        function displayTeams(filteredTeams) {{
            teamsGrid.innerHTML = '';

            if (filteredTeams.length === 0) {{
                noResults.style.display = 'block';
                teamsGrid.style.display = 'none';
            }} else {{
                noResults.style.display = 'none';
                teamsGrid.style.display = 'grid';

                filteredTeams.forEach(team => {{
                    const card = document.createElement('div');
                    card.className = 'team-card';
                    card.innerHTML = `<a href="${{team.file}}">${{team.name}}</a>`;
                    teamsGrid.appendChild(card);
                }});
            }}

            visibleCount.textContent = filteredTeams.length;
        }}

        function filterTeams() {{
            const searchTerm = searchInput.value.toLowerCase();
            const filtered = teams.filter(team =>
                team.name.toLowerCase().includes(searchTerm)
            );
            displayTeams(filtered);
        }}

        searchInput.addEventListener('input', filterTeams);

        // Initial display
        displayTeams(teams);
    </script>
</body>
</html>
"""

    index_path = teams_dir / "index.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"  ✓ Generated teams index with {len(teams_list)} teams at {index_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate index.html pages for rugby maps.")
    parser.add_argument(
        "--production", action="store_true", help="Change folder structure for production"
    )
    args = parser.parse_args()
    global IS_PRODUCTION
    if args.production:
        IS_PRODUCTION = True

    generate_team_pages()
    generate_teams_index()


if __name__ == "__main__":
    main()
