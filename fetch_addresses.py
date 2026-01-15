"""
Script to fetch addresses from RFU team pages.
Scrapes each team page for Google Maps URL and extracts the address.
Saves intermediate results with addresses but no coordinates.
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, urlparse, unquote
import time
import json
from pathlib import Path
import re
import argparse
import concurrent.futures
import threading
import random

_thread_local = threading.local()
_cache_lock = threading.RLock()
_print_lock = threading.Lock()

# Cache for club -> address data
club_cache = {}
CLUB_CACHE_FILE = "club_address_cache.json"


def load_cache():
    """Load club address cache from file."""
    global club_cache
    if Path(CLUB_CACHE_FILE).exists():
        with open(CLUB_CACHE_FILE, 'r', encoding='utf-8') as f:
            club_cache = json.load(f)
        print(f"Loaded {len(club_cache)} cached club addresses")


def save_cache():
    """Save club address cache to file."""
    with open(CLUB_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(club_cache, f, indent=2, ensure_ascii=False)


def get_session() -> requests.Session:
    """Thread-local session (requests.Session is not thread-safe)."""
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        _thread_local.session = sess
    return sess


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


def _print_block(text: str) -> None:
    """Print multi-line text without interleaving across threads."""
    with _print_lock:
        print(text, flush=True)


def extract_address_from_maps_url(maps_url):
    """Extract address from Google Maps search URL."""
    parsed = urlparse(maps_url)
    params = parse_qs(parsed.query)
    
    if 'query' in params:
        address = unquote(params['query'][0])
        address = address.replace('\n', ', ')
        return address
    return None


class AntiBotDetected(Exception):
    """Exception raised when anti-bot detection is triggered."""
    def __init__(self, message: str, *, log_text: str | None = None):
        super().__init__(message)
        self.log_text = log_text


def get_team_address(team_url, delay_seconds: float = 2.0, max_retries: int = 3):
    """Scrape team page to get the Google Maps URL from club details button."""
    log_lines = []
    
    for attempt in range(max_retries):
        try:
            if delay_seconds and delay_seconds > 0:
                time.sleep(delay_seconds + random.uniform(0.0, 0.35))

            response = get_session().get(team_url, headers=get_headers(), timeout=10)
            if response.status_code == 202:
                log_lines.append("    ✗ 202 code - bot detection")
                raise AntiBotDetected("202 code", log_text="\n".join(log_lines))

            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            club_btn = soup.find(class_='c036-club-details-btn')
            
            if club_btn and club_btn.get('href'):
                maps_url = club_btn.get('href').replace('&amp;', '&')
                return maps_url, log_lines
            
            return None, log_lines
        
        except AntiBotDetected:
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                log_lines.append(f"    ! Attempt {attempt + 1} failed: {e} - retrying...")
                time.sleep(1.0 * (attempt + 1))  # Exponential backoff
            else:
                log_lines.append(f"    ✗ All {max_retries} attempts failed: {e}")
                return None, log_lines
    
    return None, log_lines


def process_team(team_name, team_url, team_image_url, delay_seconds: float = 2.0, max_retries: int = 3):
    """Process a single team: scrape page, extract address.
    
    Returns (result, log_text) so the caller can print without interleaving.
    """
    log_lines = [f"  Processing: {team_name}", f"    URL: {team_url}"]
    
    try:
        maps_url, fetch_logs = get_team_address(team_url, delay_seconds=delay_seconds, max_retries=max_retries)
        log_lines.extend(fetch_logs)
    except AntiBotDetected as e:
        if getattr(e, "log_text", None) is None:
            e.log_text = "\n".join(log_lines)
        raise
    
    if not maps_url:
        log_lines.append(f"    ✗ No maps URL found - likely anti-bot detection triggered")
        raise AntiBotDetected(f"No maps URL found for {team_name}", log_text="\n".join(log_lines))
    
    address = extract_address_from_maps_url(maps_url)
    
    if not address:
        log_lines.append(f"    ✗ Could not extract address from URL")
        return None, "\n".join(log_lines)
    
    log_lines.append(f"    Address: {address}")
    log_lines.append(f"    ✓ Address extracted")
    
    return ({
        'name': team_name,
        'url': team_url,
        'image_url': team_image_url,
        'address': address,
        'maps_url': maps_url
    }, "\n".join(log_lines))


def team_name_to_club_name(team_name):
    """Convert team name to club name (remove II, III, IV suffixes)."""
    last_word = team_name.split(" ")[-1]
    if last_word in ["II", "III", "IV"]:
        return " ".join(team_name.split(" ")[:-1])
    return team_name


def process_league_file(league_file_path, max_workers=14, delay_seconds=2.0, max_retries=3):
    """Process a single league JSON file and fetch all addresses."""
    print(f"{'='*80}")
    print(f"Processing: {league_file_path.name}")
    print(f"{'='*80}")
    
    # Check if output file already exists
    output_file = league_file_path.parent.parent / 'team_addresses' / league_file_path.name
    if output_file.exists():
        print(f"  Skipping - already processed")
        return
    
    # Load league data
    with open(league_file_path, 'r', encoding='utf-8') as f:
        league_data = json.load(f)
    
    league_name = league_data['league_name']
    teams = league_data['teams']
    
    print(f"League: {league_name}")
    print(f"Teams to process: {len(teams)}")
    
    team_results = [None] * len(teams)
    club_futures = {}
    club_dependents = {}
    
    def materialize_team_result(base, team):
        result = dict(base)
        result["name"] = team["name"]
        result["url"] = team["url"]
        result["image_url"] = team.get("image_url")
        return result
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures_to_club = {}
        
        for idx, team in enumerate(teams):
            team_name = team['name']
            team_url = team['url']
            team_image_url = team.get('image_url')
            
            if team_name.startswith("To be arranged"):
                continue
            
            club_name = team_name_to_club_name(team_name)
            
            with _cache_lock:
                cached_club = club_cache.get(club_name)
            
            if cached_club:
                team_result = materialize_team_result(cached_club, team)
                team_results[idx] = team_result
                
                log_lines = [
                    f"  Processing: {team_name}",
                    f"    ✓ Using cached club result ({club_name})",
                    f"    Address: {team_result.get('address', 'N/A')}"
                ]
                
                _print_block("\n".join(log_lines))
                continue
            
            club_dependents.setdefault(club_name, []).append((idx, team))
            
            if club_name in club_futures:
                continue
            
            future = executor.submit(process_team, team_name, team_url, team_image_url, delay_seconds, max_retries)
            club_futures[club_name] = future
            futures_to_club[future] = club_name
        
        try:
            for future in concurrent.futures.as_completed(futures_to_club):
                club_name = futures_to_club[future]
                
                try:
                    base_result, log_text = future.result()
                    _print_block(log_text)
                except AntiBotDetected as e:
                    for f in futures_to_club:
                        f.cancel()
                    if getattr(e, "log_text", None):
                        _print_block(e.log_text)
                    print(f"{'='*80}")
                    print(f"ANTI-BOT DETECTION TRIGGERED")
                    print(f"Aborting processing to avoid being blocked")
                    print(f"{'='*80}")
                    save_cache()
                    raise
                except Exception as e:
                    _print_block(f"  Processing: {club_name}\n    ✗ Error: {e}")
                    base_result = {"error": f"exception: {e}"}
                
                if base_result is not None:
                    with _cache_lock:
                        club_cache[club_name] = dict(base_result)
                
                for idx, team in club_dependents.get(club_name, []):
                    if base_result is None:
                        team_results[idx] = None
                    else:
                        team_results[idx] = materialize_team_result(base_result, team)
        
        finally:
            save_cache()
    
    teams_with_addresses = [r for r in team_results if r]
    
    # Save results
    output_data = {
        'league_name': league_name,
        'league_url': league_data['league_url'],
        'teams': teams_with_addresses,
        'team_count': len(teams_with_addresses),
        'success_count': len([t for t in teams_with_addresses if 'error' not in t])
    }
    
    output_file.parent.mkdir(exist_ok=True)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Saved to: {output_file}")
    print(f"  Successfully fetched: {output_data['success_count']}/{len(teams_with_addresses)}")


def main():
    """Main function to process all league files."""
    parser = argparse.ArgumentParser(description="Fetch addresses from RFU team pages")
    parser.add_argument("--workers", type=int, default=14, help="Max concurrent requests (default: 14)")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between requests (default: 2.0)")
    parser.add_argument("--retries", type=int, default=3, help="Max retries for failed requests (default: 3)")
    parser.add_argument("--league", type=str, default=None, help="Process only a single league")
    args = parser.parse_args()
    
    load_cache()
    
    league_dir = Path('league_data')
    if not league_dir.exists():
        print("Error: league_data directory not found")
        return
    
    league_files = sorted(league_dir.glob('*.json'))
    
    if args.league:
        league_arg = Path(args.league)
        if league_arg.exists():
            league_files = [league_arg]
        else:
            candidate = league_dir / args.league
            if candidate.suffix != ".json":
                candidate = candidate.with_suffix(".json")
            if not candidate.exists():
                print(f"Error: league file not found: {args.league}")
                return
            league_files = [candidate]
    
    print(f"Found {len(league_files)} league files to process")
    
    for league_file in league_files:
        try:
            process_league_file(league_file, max_workers=args.workers, delay_seconds=args.delay, max_retries=args.retries)
        except AntiBotDetected:
            print(f"\n✗ Anti-bot detection triggered")
            print(f"Please wait before running the script again.")
            save_cache()
            return
        except Exception as e:
            print(f"\n✗ Error processing {league_file.name}: {e}")
            import traceback
            traceback.print_exc()
            save_cache()
    
    print(f"{'='*80}")
    print(f"Complete! Addresses saved to 'team_addresses' directory")
    print(f"Club cache size: {len(club_cache)}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
