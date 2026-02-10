import argparse
import json
import re
import urllib.parse
from pathlib import Path

from bs4 import BeautifulSoup

from utils import AntiBotDetectedError, League, LeagueInfo, Team, make_request


def get_meta_league_urls(season: str) -> list[str]:
    """Get meta league URLs for the given season."""
    return [
        f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=1699&season={season}",  # South West
        f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=1597&season={season}",  # Midlands
        f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=261&season={season}",  # London and SE
        f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=1623&season={season}",  # Northern
        f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=1605&season={season}",  # National Leagues
    ]


PREM_MAP = {
    "2025-2026": "68225",
    "2024-2025": "67686",
    "2023-2024": "51168",
    "2022-2023": "42405",
    "2021-2022": "35441",
    "2020-2021": "69325",
    "2019-2020": "69319",
}

CHAMP_MAP = {
    "2025-2026": "67198",
    "2024-2025": "57597",
    "2023-2024": "47253",
    "2022-2023": "39369",
    "2021-2022": "33636",
    "2020-2021": "31117",
    "2019-2020": "21751",
}


def get_leagues(season: str) -> list[LeagueInfo]:
    """Get initial league list for the given season."""
    return [
        {
            "name": "Premiership",
            "url": f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=5&division={PREM_MAP[season]}&season={season}",
            "parent_url": "https://www.englandrugby.com/fixtures-and-results",
        },
        {
            "name": "Championship",
            "url": f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=173&division={CHAMP_MAP[season]}&season={season}",
            "parent_url": "https://www.englandrugby.com/fixtures-and-results",
        },
    ]


def get_womens_meta_league_urls(season: str) -> list[str]:
    """Get women's meta league URLs for the given season."""
    return [
        f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=1782&season={season}"
    ]


WOMENS_PREM_MAP = {
    "2025-2026": "68284",
    "2024-2025": "58646",
    "2023-2024": "49157",
    "2022-2023": "41643",
    "2021-2022": "33312",
    "2020-2021": "31109",
    "2019-2020": "24448",
}


def get_womens_leagues(season: str) -> list[LeagueInfo]:
    """Get initial women's league list for the given season."""
    return [
        {
            "name": "Women's Premiership",
            "url": f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=1764&division={WOMENS_PREM_MAP[season]}&season={season}",
            "parent_url": "https://www.englandrugby.com/fixtures-and-results",
        }
    ]


def clean_filename(text: str) -> str:
    """Convert text to a safe filename"""
    # Remove or replace invalid filename characters
    text = re.sub(r'[<>:"/\\|?*]', "_", text)
    text = re.sub(r"\s+", "_", text)
    return text.strip("_")


def scrape_teams_from_league(
    league_url: str, league_name: str, season: str, referer: str | None = None
) -> list[Team]:
    print(f"Scraping teams from: {league_url}")

    response = make_request(league_url, referer=referer)

    # Check for anti-bot 202 response
    if response.status_code == 202:
        print(f"    \u2717 202 code - bot detection triggered for {league_name}")
        raise AntiBotDetectedError(f"202 response for {league_name}")

    soup = BeautifulSoup(response.content, "html.parser")

    teams = []

    # Find all table cells with class containing "coh-style-team-name"
    team_cells = soup.find_all("td", class_=lambda x: x and "coh-style-team-name" in x)

    for cell in team_cells:
        # Find the href within the cell
        link = cell.find("a", href=True)
        if link:
            team_name = link.get_text(strip=True)
            team_url = link["href"]

            # Make absolute URL if needed
            if team_url.startswith("/"):
                team_url = f"https://www.englandrugby.com{team_url}"

            # Find image sibling
            img = cell.find("img")
            team_image_url: str | None = None
            if img and img.get("src"):
                team_image_url = img["src"]
                # Make absolute URL if needed
                if team_image_url.startswith("/"):
                    team_image_url = f"https://www.englandrugby.com{team_image_url}"

            teams.append({"name": team_name, "url": team_url, "image_url": team_image_url})

    print(f"  Found {len(teams)} teams in {league_name}")
    return teams


def scrape_leagues_from_page(page_url: str) -> list[LeagueInfo]:
    """Scrape league links from the related-leagues-overview div"""
    print(f"\nScraping leagues from: {page_url}")

    response = make_request(page_url)

    # Check for anti-bot 202 response
    if response.status_code == 202:
        print("  ✗ 202 code - bot detection triggered")
        raise AntiBotDetectedError(f"202 response for {page_url}")

    soup = BeautifulSoup(response.content, "html.parser")

    # Find the div with id "related-leagues-overview"
    leagues_div = soup.find("div", id="related-leagues-overview")

    if not leagues_div:
        print('  Warning: Could not find div with id "related-leagues-overview"')
        return []

    leagues: list[LeagueInfo] = []

    # Find all hrefs within this div
    links = leagues_div.find_all("a", href=True)

    for link in links:
        league_name = link.get_text(strip=True)
        league_url = link["href"]

        # Make absolute URL if needed
        if league_url.startswith("/"):
            league_url = f"https://www.englandrugby.com{league_url}"

        if league_name and league_url:
            leagues.append({"name": league_name, "url": league_url, "parent_url": page_url})

    print(f"  Found {len(leagues)} leagues")
    return leagues


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape RFU website for league and team data.")
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to scrape (e.g., 2024-2025, 2025-2026). Default: 2025-2026",
    )
    args = parser.parse_args()
    season = args.season

    print(f"Scraping data for season: {season}")

    # Create output directory for this season
    output_dir = Path("league_data") / season
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get league URLs for this season
    leagues = get_leagues(season)
    meta_league_urls = get_meta_league_urls(season)

    # Process each top-level URL
    for meta_url in meta_league_urls:
        # Scrape leagues from this page
        try:
            leagues.extend(scrape_leagues_from_page(meta_url))
        except AntiBotDetectedError:
            print(f"\n✗ Anti-bot detection triggered while scraping {meta_url}")
            print("Please wait before running the script again.")
            return

    # Get women's leagues for this season
    womens_leagues = get_womens_leagues(season)
    womens_meta_league_urls = get_womens_meta_league_urls(season)

    for meta_url in womens_meta_league_urls:
        # Scrape leagues from this page
        try:
            womens_leagues.extend(scrape_leagues_from_page(meta_url))
        except AntiBotDetectedError:
            print(f"\n✗ Anti-bot detection triggered while scraping {meta_url}")
            print("Please wait before running the script again.")
            return

    skipped_leagues: list[LeagueInfo] = []

    # For each league, scrape teams and create JSON file
    for league in leagues + womens_leagues:
        league_name = league["name"]
        league_url = league["url"]
        parent_url = league["parent_url"]

        banned_words = ["playoff", "play-off", "phase", "shield", "trophy", "plate", "salver"]
        if any(word in league_name.lower() for word in banned_words):
            print(f"Skipping {league_name} (playoff/phase league)")
            continue

        # Create filename from league name
        filename = clean_filename(league_name) + ".json"
        output_path = output_dir / filename

        # Skip if file already exists
        if output_path.exists():
            print(f"Skipping {league_name} (already exists)")
            continue

        parsed_url = urllib.parse.urlparse(league_url)
        # Add season parameter if not present
        query_params = urllib.parse.parse_qs(parsed_url.query)
        if "season" not in query_params:
            query_params["season"] = [season]
        new_query = urllib.parse.urlencode(query_params, doseq=True)
        league_url = urllib.parse.urlunparse(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                parsed_url.params,
                new_query,
                "tables",
            )
        )

        try:
            # Scrape teams from this league
            teams = scrape_teams_from_league(league_url, league_name, season, referer=parent_url)

            if len(teams) == 0:
                print(f"  Skipping saving {league_name} (no teams found)")
                skipped_leagues.append(league)
                continue

            # Prepare data for JSON output
            league_data: League = {
                "league_name": league_name,
                "league_url": league_url,
                "teams": teams,
                "team_count": len(teams),
            }

            # Write JSON file
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(league_data, f, indent=2, ensure_ascii=False)

            print(f"    Saved to: {output_path}")

        except AntiBotDetectedError:
            print(f"\n✗ Anti-bot detection triggered while scraping {league_name}")
            print("Please wait before running the script again.")
            return

    print(f'\nComplete! League data saved to "{output_dir}" directory')
    for skipped in skipped_leagues:
        print(f"Skipped league {skipped["name"]}: {skipped["url"]}")


if __name__ == "__main__":
    main()
