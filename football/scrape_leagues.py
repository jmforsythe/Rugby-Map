"""
Scrape BSLFL league tables from FA Full-Time and generate team_addresses files.

Reads https://www.bslfl.co.uk/league-tables for the Full-Time division URLs,
then scrapes each division's league table for the team roster.  Teams are
matched against the club address cache (club_addresses.json) produced by
scrape_bslfl.py to produce team_addresses files ready for geocoding.

Output: football/team_addresses/{season}/BSLFL/{division}.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup, Tag

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils import make_request

_BASE_URL = "https://www.bslfl.co.uk"
_LEAGUE_TABLES_URL = f"{_BASE_URL}/league-tables"
_FULLTIME_BASE = "https://fulltime.thefa.com"

_SCRIPT_DIR = Path(__file__).parent
_ADDRESS_CACHE_FILE = _SCRIPT_DIR / "club_addresses.json"

_RESERVE_SUFFIXES = re.compile(
    r"\s*(?:Reserves|2nd\s*Team|2ND|Development)\s*$",
    re.IGNORECASE,
)

_NAME_FIXES: dict[str, str] = {
    "Foots Cray Lions": "Footscray Lions FC",
    "Agenda Oldsmiths United": "AFC Oldsmiths",
}


def _normalize(name: str) -> str:
    """Lowercase, strip FC suffix, and collapse whitespace."""
    name = name.lower().strip()
    name = re.sub(r"\s+fc$", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


def _load_address_cache() -> list[dict]:
    if not _ADDRESS_CACHE_FILE.exists():
        print(f"Error: {_ADDRESS_CACHE_FILE} not found. Run scrape_bslfl.py first.")
        sys.exit(1)
    with open(_ADDRESS_CACHE_FILE, encoding="utf-8") as f:
        return json.load(f)


def _build_club_lookup(clubs: list[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for club in clubs:
        lookup[_normalize(club["name"])] = club
    return lookup


def _match_team(
    team_name: str,
    lookup: dict[str, dict],
) -> dict | None:
    """Match a Full-Time team name to a club from the address cache."""
    if team_name in _NAME_FIXES:
        team_name = _NAME_FIXES[team_name]

    norm = _normalize(team_name)

    if norm in lookup:
        return lookup[norm]

    stripped = _RESERVE_SUFFIXES.sub("", team_name).strip()
    if stripped != team_name:
        return _match_team(stripped, lookup)

    for club_norm, club in lookup.items():
        if norm.startswith(club_norm) or club_norm.startswith(norm):
            return club

    return None


def scrape_division_links() -> list[dict]:
    """Scrape the BSLFL league-tables page for Full-Time division URLs.

    Returns list of dicts: {name, url, fulltime_url}
    """
    print(f"Fetching league tables page: {_LEAGUE_TABLES_URL}")
    response = make_request(_LEAGUE_TABLES_URL, delay_seconds=1)
    soup = BeautifulSoup(response.content, "html.parser")

    divisions: list[dict] = []
    for a in soup.find_all("a"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if "fulltime.thefa.com/index.html" in href and "divisionseason=" in href:
            name = re.sub(r"^click here for\s*", "", text, flags=re.IGNORECASE).strip()
            divisions.append(
                {
                    "name": name,
                    "fulltime_url": href,
                }
            )

    print(f"  Found {len(divisions)} divisions")
    for d in divisions:
        print(f"    {d['name']}: {d['fulltime_url']}")

    return divisions


def scrape_teams_from_fulltime(fulltime_url: str) -> list[dict]:
    """Scrape teams from a Full-Time league table page.

    Returns list of dicts: {name, url}
    """
    print(f"  Scraping: {fulltime_url}")
    response = make_request(fulltime_url, delay_seconds=1.5)
    soup = BeautifulSoup(response.content, "html.parser")

    table = soup.find("table", class_="cell-dividers")
    if not table or not isinstance(table, Tag):
        print("    Warning: no league table found")
        return []

    teams: list[dict] = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        team_cell = cells[1]
        team_name = team_cell.get_text(strip=True)
        if not team_name or team_name.startswith("*"):
            continue

        link = team_cell.find("a")
        team_url = f"{_FULLTIME_BASE}{link['href']}" if link and link.get("href") else None

        teams.append({"name": team_name, "url": team_url})

    print(f"    Found {len(teams)} teams")
    return teams


def _clean_filename(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*]', "_", text)
    text = re.sub(r"\s+", "_", text)
    return text.strip("_")


def process_season(season: str) -> None:
    """Scrape all BSLFL divisions and generate team_addresses files."""
    clubs = _load_address_cache()
    lookup = _build_club_lookup(clubs)

    divisions = scrape_division_links()
    output_base = _SCRIPT_DIR / "team_addresses" / season / "BSLFL"
    output_base.mkdir(parents=True, exist_ok=True)

    unmatched: list[tuple[str, str]] = []

    for division in divisions:
        div_name = division["name"]
        filename = _clean_filename(div_name) + ".json"
        output_file = output_base / filename

        if output_file.exists():
            print(f"Skipping {div_name} (already exists)")
            continue

        ft_teams = scrape_teams_from_fulltime(division["fulltime_url"])
        if not ft_teams:
            print(f"  Skipping {div_name} (no teams)")
            continue

        address_teams: list[dict] = []
        for ft_team in ft_teams:
            club = _match_team(ft_team["name"], lookup)
            if club:
                address_teams.append(
                    {
                        "name": ft_team["name"],
                        "url": ft_team["url"],
                        "image_url": club.get("image_url"),
                        "address": club.get("address"),
                    }
                )
                print(f"    {ft_team['name']} -> {club['name']} ({club.get('address', 'N/A')})")
            else:
                address_teams.append(
                    {
                        "name": ft_team["name"],
                        "url": ft_team["url"],
                        "image_url": None,
                        "address": None,
                    }
                )
                unmatched.append((ft_team["name"], div_name))
                print(f"    {ft_team['name']} -> NO MATCH")

        league_data = {
            "league_name": div_name,
            "league_url": division["fulltime_url"],
            "teams": address_teams,
            "team_count": len(address_teams),
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(league_data, f, indent=2, ensure_ascii=False)

        matched = sum(1 for t in address_teams if t["address"])
        print(f"    Saved: {output_file} ({matched}/{len(address_teams)} matched)")

    print(f"\n{'='*60}")
    print(f"Complete! Team addresses saved to {output_base}")
    print(f"{'='*60}")

    if unmatched:
        print(f"\nUNMATCHED TEAMS ({len(unmatched)}):")
        for name, div in sorted(set(unmatched)):
            print(f"  {name} ({div})")
    else:
        print("\nAll teams matched!")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape BSLFL league tables and generate team_addresses files."
    )
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season label (e.g. 2025-2026). Default: 2025-2026",
    )
    args = parser.parse_args()

    print(f"Scraping BSLFL league data for season: {args.season}")
    process_season(args.season)


if __name__ == "__main__":
    main()
