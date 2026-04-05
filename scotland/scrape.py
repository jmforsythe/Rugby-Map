"""
Scrape Scottish Rugby league and team data from fixtures.scottishrugby.org.

Reads the season index page to discover league sections and league URLs,
then scrapes each league's table page for the team roster.

Output: scotland/league_data/{season}/{section}/{league}.json
"""

from __future__ import annotations

import argparse
import json
import re
from typing import TypedDict

from bs4 import BeautifulSoup, Tag

from core import make_request
from scotland import DATA_DIR

_BASE = "https://fixtures.scottishrugby.org/club-rugby"

_SECTIONS_TO_SKIP = {
    "Adult Cup Competitions",
    "Reserve Leagues",
    "Development Programmes",
    "Aspiring & Evolution",
    "Scottish Inter District Championship",
}

_BANNED_WORDS = [
    "cup",
    "shield",
    "plate",
    "bowl",
    "play-off",
    "playoff",
    "stage 2",
    "development",
    "aspiring",
]


class ScottishTeam(TypedDict):
    name: str
    url: str | None
    image_url: str | None


class ScottishLeague(TypedDict):
    league_name: str
    league_url: str
    section: str
    teams: list[ScottishTeam]
    team_count: int


class LeagueLink(TypedDict):
    name: str
    url: str
    section: str


def clean_filename(text: str) -> str:
    """Convert text to a safe filename."""
    text = re.sub(r'[<>:"/\\|?*]', "_", text)
    text = re.sub(r"\s+", "_", text)
    return text.strip("_")


def clean_section_name(text: str) -> str:
    """Convert section title to a directory-safe name."""
    text = re.sub(r'[<>:"/\\|?*]', "_", text)
    text = re.sub(r"\s+", "_", text)
    return text.strip("_")


def _results_url_to_table_url(url: str) -> str:
    """Convert a /results URL to /table URL."""
    if url.endswith("/results"):
        return url[: -len("/results")] + "/table"
    return url + "/table"


def scrape_league_index(season: str) -> list[LeagueLink]:
    """Scrape the season index page for all league links grouped by section."""
    index_url = f"{_BASE}/{season}"
    print(f"Fetching league index: {index_url}")

    response = make_request(index_url, delay_seconds=0.5)
    soup = BeautifulSoup(response.content, "html.parser")

    sections = soup.find_all("div", class_="section")
    leagues: list[LeagueLink] = []

    for section_div in sections:
        title_el = section_div.find(class_="title")
        if not title_el:
            continue
        section_name = title_el.get_text(strip=True)

        if section_name in _SECTIONS_TO_SKIP:
            print(f"  Skipping section: {section_name}")
            continue

        links = section_div.find_all("a", class_="link")
        section_leagues = 0

        for link in links:
            if not isinstance(link, Tag):
                continue
            league_name = link.get_text(strip=True)
            league_url = link.get("href", "")
            if not league_url:
                continue

            name_lower = league_name.lower()
            if any(banned in name_lower for banned in _BANNED_WORDS):
                print(f"  Skipping league (banned word): {league_name}")
                continue

            leagues.append(
                {
                    "name": league_name,
                    "url": str(league_url),
                    "section": section_name,
                }
            )
            section_leagues += 1

        print(f"  Section '{section_name}': {section_leagues} leagues")

    print(f"Total leagues to scrape: {len(leagues)}")
    return leagues


def scrape_teams_from_table(league_url: str, league_name: str) -> list[ScottishTeam]:
    """Scrape teams from a Scottish Rugby league table page."""
    table_url = _results_url_to_table_url(league_url)
    print(f"  Scraping table: {table_url}")

    response = make_request(table_url, delay_seconds=1)
    soup = BeautifulSoup(response.content, "html.parser")

    table = soup.find("table")
    if not isinstance(table, Tag):
        print(f"    Warning: no table found on {table_url}")
        return []

    teams: list[ScottishTeam] = []
    rows = table.find_all("tr")

    for row in rows:
        team_cell = row.find("td", class_="team-column")
        if not team_cell:
            continue

        team_name = team_cell.get_text(strip=True)
        if not team_name:
            continue

        team_link = team_cell.find("a")
        team_url: str | None = None
        if team_link and team_link.get("href"):
            team_url = str(team_link["href"])

        teams.append(
            {
                "name": team_name,
                "url": team_url,
                "image_url": None,
            }
        )

    print(f"    Found {len(teams)} teams in {league_name}")
    return teams


def scrape_season(season: str) -> None:
    """Scrape all leagues for a given season."""
    leagues = scrape_league_index(season)

    output_base = DATA_DIR / "league_data" / season
    output_base.mkdir(parents=True, exist_ok=True)

    skipped: list[LeagueLink] = []

    for league in leagues:
        league_name = league["name"]
        section = league["section"]
        league_url = league["url"]

        section_dir = output_base / clean_section_name(section)
        section_dir.mkdir(parents=True, exist_ok=True)

        filename = clean_filename(league_name) + ".json"
        output_path = section_dir / filename

        if output_path.exists():
            print(f"Skipping {league_name} (already exists)")
            continue

        teams = scrape_teams_from_table(league_url, league_name)

        if not teams:
            print(f"  Skipping save for {league_name} (no teams found)")
            skipped.append(league)
            continue

        league_data: ScottishLeague = {
            "league_name": league_name,
            "league_url": league_url,
            "section": section,
            "teams": teams,
            "team_count": len(teams),
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(league_data, f, indent=2, ensure_ascii=False)

        print(f"    Saved to: {output_path}")

    print(f"\nComplete! League data saved to {output_base}")
    if skipped:
        print(f"\nSkipped {len(skipped)} leagues (no teams):")
        for s in skipped:
            print(f"  {s['name']}: {s['url']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Scottish Rugby league and team data.")
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to scrape (e.g. 2025-2026). Default: 2025-2026",
    )
    args = parser.parse_args()

    print(f"Scraping Scottish Rugby data for season: {args.season}")
    scrape_season(args.season)


if __name__ == "__main__":
    main()
