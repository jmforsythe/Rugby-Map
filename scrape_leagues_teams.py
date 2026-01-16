import requests
from bs4 import BeautifulSoup
import json
import time
import re
from pathlib import Path
from urllib.parse import urlparse

# Create a session to maintain cookies
session = requests.Session()

META_LEAGUE_URLS = [
    "https://www.englandrugby.com/fixtures-and-results/search-results?competition=1699&season=2025-2026", # South West
    "https://www.englandrugby.com/fixtures-and-results/search-results?competition=1597&season=2025-2026", # Midlands
    "https://www.englandrugby.com/fixtures-and-results/search-results?competition=261&season=2025-2026", # London and SE
    "https://www.englandrugby.com/fixtures-and-results/search-results?competition=1623&season=2025-2026", # Northern
    "https://www.englandrugby.com/fixtures-and-results/search-results?competition=1605&season=2025-2026" # National Leagues
]

leagues = [
    {
        "name": "Premier",
        "url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=5&division=68225&season=2025-2026",
        "parent_url": "https://www.englandrugby.com/fixtures-and-results"
    },
    {
        "name": "Championship",
        "url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=173&division=67198&season=2025-2026",
        "parent_url": "https://www.englandrugby.com/fixtures-and-results"
    }
]

def get_headers(referer=None):
    """Get headers with optional referer"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-GB,en-US;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin' if referer else 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0'
    }
    if referer:
        headers['Referer'] = referer
    return headers

def make_request(url, referer=None, max_retries=3):
    """Make a request with retry logic and exponential backoff"""
    for attempt in range(max_retries):
        try:
            time.sleep(2 + attempt * 2)  # Increase delay with each attempt
            response = session.get(url, timeout=30, headers=get_headers(referer))
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise
            print(f"    Attempt {attempt + 1} failed, retrying... ({e})")
            time.sleep(5 * (attempt + 1))  # Exponential backoff
    return None


def clean_filename(text):
    """Convert text to a safe filename"""
    # Remove or replace invalid filename characters
    text = re.sub(r'[<>:"/\\|?*]', '_', text)
    text = re.sub(r'\s+', '_', text)
    return text.strip('_')

def scrape_teams_from_league(league_url, league_name, referer=None):
    """Scrape team data from a league table"""
    # Add season parameter and tables anchor if not already present
    if '&season=2025-2026#tables' not in league_url:
        # Remove any existing anchor
        base_url = league_url.split('#')[0]
        # Add season if not present
        if 'season=' not in base_url:
            league_url = f"{base_url}&season=2025-2026#tables"
        else:
            league_url = f"{base_url}#tables"
    
    print(f"  Scraping teams from: {league_url}")
    
    try:
        response = make_request(league_url, referer=referer)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        teams = []
        
        # Find all table cells with class containing 'coh-style-team-name'
        team_cells = soup.find_all('td', class_=lambda x: x and 'coh-style-team-name' in x)
        
        for cell in team_cells:
            # Find the href within the cell
            link = cell.find('a', href=True)
            if link:
                team_name = link.get_text(strip=True)
                team_url = link['href']
                
                # Make absolute URL if needed
                if team_url.startswith('/'):
                    team_url = f"https://www.englandrugby.com{team_url}"
                
                # Find image sibling
                img = cell.find('img')
                team_image_url = None
                if img and img.get('src'):
                    team_image_url = img['src']
                    # Make absolute URL if needed
                    if team_image_url.startswith('/'):
                        team_image_url = f"https://www.englandrugby.com{team_image_url}"
                
                teams.append({
                    'name': team_name,
                    'url': team_url,
                    'image_url': team_image_url
                })
        
        print(f"    Found {len(teams)} teams in {league_name}")
        return teams
    
    except Exception as e:
        print(f"    Error scraping teams from {league_name}: {e}")
        return []

def scrape_leagues_from_page(page_url):
    """Scrape league links from the related-leagues-overview div"""
    print(f"\nScraping leagues from: {page_url}")
    
    try:
        response = make_request(page_url)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find the div with id 'related-leagues-overview'
        leagues_div = soup.find('div', id='related-leagues-overview')
        
        if not leagues_div:
            print("  Warning: Could not find div with id 'related-leagues-overview'")
            return []
        
        leagues = []
        
        # Find all hrefs within this div
        links = leagues_div.find_all('a', href=True)
        
        for link in links:
            league_name = link.get_text(strip=True)
            league_url = link['href']
            
            # Make absolute URL if needed
            if league_url.startswith('/'):
                league_url = f"https://www.englandrugby.com{league_url}"
            
            if league_name and league_url:
                leagues.append({
                    'name': league_name,
                    'url': league_url,
                    "parent_url": page_url
                })
        
        print(f"  Found {len(leagues)} leagues")
        return leagues
    
    except Exception as e:
        print(f"  Error scraping page: {e}")
        return []

def main():    
    # Create output directory if it doesn't exist
    output_dir = Path('league_data')
    output_dir.mkdir(exist_ok=True)
    
    global leagues

    # Process each top-level URL
    for meta_url in META_LEAGUE_URLS:
        # Scrape leagues from this page
        leagues.extend(scrape_leagues_from_page(meta_url))
        
    # For each league, scrape teams and create JSON file
    for league in leagues:
        league_name = league['name']
        league_url = league['url']
        parent_url = league["parent_url"]
        
        # Create filename from league name
        filename = clean_filename(league_name) + '.json'
        output_path = output_dir / filename
        
        # Skip if file already exists
        if output_path.exists():
            print(f"  Skipping {league_name} (already exists)")
            continue
        
        # Scrape teams from this league
        teams = scrape_teams_from_league(league_url, league_name, referer=parent_url)
        
        # Prepare data for JSON output
        league_data = {
            'league_name': league_name,
            'league_url': league_url,
            'teams': teams,
            'team_count': len(teams)
        }
        
        # Write JSON file
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(league_data, f, indent=2, ensure_ascii=False)
        
        print(f"    Saved to: {output_path}")
        
        # Be polite to the server - longer delay
        time.sleep(3)
            
    print(f"\nComplete! League data saved to '{output_dir}' directory")

if __name__ == '__main__':
    main()
