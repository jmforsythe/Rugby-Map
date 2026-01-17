from bs4 import BeautifulSoup
import json
import time
import re
from pathlib import Path
from typing import Optional, List

from utils import Team, LeagueInfo, League, make_request, AntiBotDetected


META_LEAGUE_URLS: List[str] = [
    "https://www.englandrugby.com/fixtures-and-results/search-results?competition=1699&season=2025-2026", # South West
    "https://www.englandrugby.com/fixtures-and-results/search-results?competition=1597&season=2025-2026", # Midlands
    "https://www.englandrugby.com/fixtures-and-results/search-results?competition=261&season=2025-2026", # London and SE
    "https://www.englandrugby.com/fixtures-and-results/search-results?competition=1623&season=2025-2026", # Northern
    "https://www.englandrugby.com/fixtures-and-results/search-results?competition=1605&season=2025-2026" # National Leagues
]

leagues: List[LeagueInfo] = [
    {
        "name": "Premiership",
        "url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=5&division=68225&season=2025-2026",
        "parent_url": "https://www.englandrugby.com/fixtures-and-results"
    },
    {
        "name": "Championship",
        "url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=173&division=67198&season=2025-2026",
        "parent_url": "https://www.englandrugby.com/fixtures-and-results"
    }
]

WOMENS_META_LEAGUE_URLS: List[str] = [
    "https://www.englandrugby.com/fixtures-and-results/search-results?competition=1782&season=2025-2026"
]

womens_leagues: List[LeagueInfo] = [
    {
        "name": "Women's Premiership",
        "url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=1764&division=68284&season=2025-2026",
        "parent_url": "https://www.englandrugby.com/fixtures-and-results"
    }
]

def clean_filename(text: str) -> str:
    """Convert text to a safe filename"""
    # Remove or replace invalid filename characters
    text = re.sub(r'[<>:"/\\|?*]', "_", text)
    text = re.sub(r"\s+", "_", text)
    return text.strip("_")

def scrape_teams_from_league(league_url: str, league_name: str, referer: Optional[str] = None) -> List[Team]:
    """Scrape team data from a league table"""
    # Add season parameter and tables anchor if not already present
    if "&season=2025-2026#tables" not in league_url:
        # Remove any existing anchor
        base_url = league_url.split("#")[0]
        # Add season if not present
        if "season=" not in base_url:
            league_url = f"{base_url}&season=2025-2026#tables"
        else:
            league_url = f"{base_url}#tables"
    
    print(f"Scraping teams from: {league_url}")
    
    response = make_request(league_url, referer=referer)
    
    # Check for anti-bot 202 response
    if response.status_code == 202:
        print(f"    \u2717 202 code - bot detection triggered for {league_name}")
        raise AntiBotDetected(f"202 response for {league_name}")
    
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
            team_image_url: Optional[str] = None
            if img and img.get("src"):
                team_image_url = img["src"]
                # Make absolute URL if needed
                if team_image_url.startswith("/"):
                    team_image_url = f"https://www.englandrugby.com{team_image_url}"
            
            teams.append({
                "name": team_name,
                "url": team_url,
                "image_url": team_image_url
            })
    
    print(f"  Found {len(teams)} teams in {league_name}")
    return teams
    
def scrape_leagues_from_page(page_url: str) -> List[LeagueInfo]:
    """Scrape league links from the related-leagues-overview div"""
    print(f"\nScraping leagues from: {page_url}")
    
    response = make_request(page_url)
    
    # Check for anti-bot 202 response
    if response.status_code == 202:
        print(f"  ✗ 202 code - bot detection triggered")
        raise AntiBotDetected(f"202 response for {page_url}")
    
    soup = BeautifulSoup(response.content, "html.parser")
    
    # Find the div with id "related-leagues-overview"
    leagues_div = soup.find("div", id="related-leagues-overview")
    
    if not leagues_div:
        print("  Warning: Could not find div with id \"related-leagues-overview\"")
        return []
    
    leagues: List[LeagueInfo] = []
    
    # Find all hrefs within this div
    links = leagues_div.find_all("a", href=True)
    
    for link in links:
        league_name = link.get_text(strip=True)
        league_url = link["href"]
        
        # Make absolute URL if needed
        if league_url.startswith("/"):
            league_url = f"https://www.englandrugby.com{league_url}"
        
        if league_name and league_url:
            leagues.append({
                "name": league_name,
                "url": league_url,
                "parent_url": page_url
            })
    
    print(f"  Found {len(leagues)} leagues")
    return leagues

def main() -> None:    
    # Create output directory if it doesn"t exist
    output_dir = Path("league_data")
    output_dir.mkdir(exist_ok=True)
    
    global leagues

    # Process each top-level URL
    for meta_url in META_LEAGUE_URLS:
        # Scrape leagues from this page
        try:
            leagues.extend(scrape_leagues_from_page(meta_url))
        except AntiBotDetected as e:
            print(f"\n✗ Anti-bot detection triggered while scraping {meta_url}")
            print(f"Please wait before running the script again.")
            return
        
    global womens_leagues

    for meta_url in WOMENS_META_LEAGUE_URLS:
        # Scrape leagues from this page
        try:
            womens_leagues.extend(scrape_leagues_from_page(meta_url))
        except AntiBotDetected as e:
            print(f"\n✗ Anti-bot detection triggered while scraping {meta_url}")
            print(f"Please wait before running the script again.")
            return
        
    # For each league, scrape teams and create JSON file
    for league in leagues + womens_leagues:
        league_name = league["name"]
        league_url = league["url"]
        parent_url = league["parent_url"]
        
        # Create filename from league name
        filename = clean_filename(league_name) + ".json"
        output_path = output_dir / filename
        
        # Skip if file already exists
        if output_path.exists():
            print(f"Skipping {league_name} (already exists)")
            continue
        
        try:
            # Scrape teams from this league
            teams = scrape_teams_from_league(league_url, league_name, referer=parent_url)
            
            # Prepare data for JSON output
            league_data: League = {
                "league_name": league_name,
                "league_url": league_url,
                "teams": teams,
                "team_count": len(teams)
            }
            
            # Write JSON file
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(league_data, f, indent=2, ensure_ascii=False)
            
            print(f"    Saved to: {output_path}")

        except AntiBotDetected as e:
            print(f"\n✗ Anti-bot detection triggered while scraping {league_name}")
            print(f"Please wait before running the script again.")
            return
        
        # Be polite to the server - longer delay
        time.sleep(3)
            
    print(f"\nComplete! League data saved to \"{output_dir}\" directory")

if __name__ == "__main__":
    main()
