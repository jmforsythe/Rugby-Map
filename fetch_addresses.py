"""
Script to fetch addresses from RFU team pages.
Scrapes each team page for Google Maps URL and extracts the address.
Saves intermediate results with addresses but no coordinates.
"""

from bs4 import BeautifulSoup
from urllib.parse import parse_qs, urlparse, unquote
import time
import json
from pathlib import Path
import argparse
import concurrent.futures
import threading
import random
from typing import Optional, Dict, Tuple, List

from utils import (
    Team, AddressTeam, AddressLeague, League,
    get_session, get_headers, print_block
)

_cache_lock = threading.RLock()

# Cache for club -> address data
club_cache: Dict[str, str] = {}
CLUB_CACHE_FILE = "club_address_cache.json"


def load_cache() -> None:
    """Load club address cache from file."""
    global club_cache
    if Path(CLUB_CACHE_FILE).exists():
        with open(CLUB_CACHE_FILE, 'r', encoding='utf-8') as f:
            club_cache = json.load(f)
        print(f"Loaded {len(club_cache)} cached club addresses")


def save_cache() -> None:
    """Save club address cache to file."""
    with open(CLUB_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(club_cache, f, indent=2, ensure_ascii=False)


def extract_address_from_maps_url(maps_url: str) -> Optional[str]:
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
    log_text: Optional[str]
    
    def __init__(self, message: str, *, log_text: Optional[str] = None) -> None:
        super().__init__(message)
        self.log_text = log_text


def team_name_to_club_name(team_name: str) -> str:
    """Convert team name to club name (remove II, III, IV suffixes)."""
    last_word = team_name.split(" ")[-1]
    if last_word in ["II", "III", "IV"]:
        return " ".join(team_name.split(" ")[:-1])
    return team_name


def get_club_address_from_page(
    team_url: str,
    delay_seconds: float = 2.0,
    max_retries: int = 3
) -> Tuple[Optional[str], List[str]]:
    """Scrape team page to get the Google Maps URL from club details button.
    
    Returns:
        Tuple of (maps_url, log_lines)
    """
    log_lines: List[str] = []
    
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


def fetch_club_address(
    club_name: str,
    team_url: str,
    delay_seconds: float = 2.0,
    max_retries: int = 3
) -> Tuple[Optional[str], str]:
    """Fetch address for a club by scraping a team page.
    
    Args:
        club_name: Name of the club (team name with II/III/IV suffix removed)
        team_url: URL to any team page for this club
        delay_seconds: Delay between requests
        max_retries: Maximum retry attempts
    
    Returns:
        Tuple of (address string, log_text) for thread-safe printing.
    """
    log_lines: List[str] = [f"  Fetching: {club_name}", f"    URL: {team_url}"]
    
    try:
        maps_url, fetch_logs = get_club_address_from_page(team_url, delay_seconds=delay_seconds, max_retries=max_retries)
        log_lines.extend(fetch_logs)
    except AntiBotDetected as e:
        if getattr(e, "log_text", None) is None:
            e.log_text = "\n".join(log_lines)
        raise
    
    if not maps_url:
        log_lines.append(f"    ✗ No maps URL found - likely anti-bot detection triggered")
        raise AntiBotDetected(f"No maps URL found for {club_name}", log_text="\n".join(log_lines))
    
    address = extract_address_from_maps_url(maps_url)
    
    if not address:
        log_lines.append(f"    ✗ Could not extract address from URL")
        return None, "\n".join(log_lines)
    
    log_lines.append(f"    Address: {address}")
    log_lines.append(f"    ✓ Address extracted")
    
    return (address, "\n".join(log_lines))


def process_league_file(
    league_file_path: Path,
    max_workers: int = 14,
    delay_seconds: float = 2.0,
    max_retries: int = 3
) -> None:
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
        league_data: League = json.load(f)
    
    league_name: str = league_data['league_name']
    teams: List[Team] = league_data['teams']
    
    print(f"League: {league_name}")
    print(f"Teams to process: {len(teams)}")
    
    team_results: List[Optional[AddressTeam]] = [None] * len(teams)
    club_futures: Dict[str, concurrent.futures.Future] = {}
    club_dependents: Dict[str, List[Tuple[int, Team]]] = {}
    
    def create_team_address(address: Optional[str], team: Team) -> AddressTeam:
        """Create TeamAddress by combining club name, address, and team-specific fields."""
        return {
            "name": team["name"],
            "url": team["url"],
            "image_url": team.get("image_url"),
            "address": address,
        }
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures_to_club: Dict[concurrent.futures.Future, str] = {}
        
        for idx, team in enumerate(teams):
            team_name = team['name']
            team_url = team['url']
            team_image_url = team.get('image_url')
            
            if team_name.startswith("To be arranged"):
                continue
            
            club_name = team_name_to_club_name(team_name)
            
            with _cache_lock:
                cached_address = club_cache.get(club_name)
            
            if cached_address is not None:
                team_result = create_team_address(cached_address, team)
                team_results[idx] = team_result
                
                log_lines = [
                    f"  Processing: {team_name}",
                    f"    ✓ Using cached club result ({club_name})",
                    f"    Address: {cached_address or 'N/A'}"
                ]
                
                print_block("\n".join(log_lines))
                continue
            
            club_dependents.setdefault(club_name, []).append((idx, team))
            
            if club_name in club_futures:
                continue
            
            future = executor.submit(fetch_club_address, club_name, team_url, delay_seconds, max_retries)
            club_futures[club_name] = future
            futures_to_club[future] = club_name
        
        try:
            for future in concurrent.futures.as_completed(futures_to_club):
                club_name = futures_to_club[future]
                
                try:
                    fetched_address: Optional[str]
                    log_text: str
                    fetched_address, log_text = future.result()
                    print_block(log_text)
                except AntiBotDetected as e:
                    for f in futures_to_club:
                        f.cancel()
                    if getattr(e, "log_text", None):
                        print_block(e.log_text)
                    print(f"{'='*80}")
                    print(f"ANTI-BOT DETECTION TRIGGERED")
                    print(f"Aborting processing to avoid being blocked")
                    print(f"{'='*80}")
                    save_cache()
                    raise
                except Exception as e:
                    print_block(f"  Processing: {club_name}\n    ✗ Error: {e}")
                    fetched_address = None
                
                # Store in cache (address only)
                with _cache_lock:
                    club_cache[club_name] = {"address": fetched_address}
                
                for idx, team in club_dependents.get(club_name, []):
                    team_results[idx] = create_team_address(fetched_address, team)
        
        finally:
            save_cache()
    
    teams_with_addresses: List[AddressTeam] = [r for r in team_results if r]
    
    # Save results
    output_data: AddressLeague = {
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


def main() -> None:
    """Main function to process all league files."""
    parser = argparse.ArgumentParser(description="Fetch addresses from RFU team pages")
    parser.add_argument("--workers", type=int, default=7, help="Max concurrent requests (default: 7)")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between requests (default: 2.0)")
    parser.add_argument("--retries", type=int, default=3, help="Max retries for failed requests (default: 3)")
    parser.add_argument("--league", type=str, default=None, help="Process only a single league")
    args = parser.parse_args()
    
    load_cache()
    
    league_dir = Path('league_data')
    if not league_dir.exists():
        print("Error: league_data directory not found")
        return
    
    league_files: List[Path] = sorted(league_dir.glob('*.json'))
    
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
