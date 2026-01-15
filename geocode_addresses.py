"""  
Script to geocode team addresses using Google Geocoding API.
Reads team addresses from team_addresses/ directory and adds coordinates.
Includes client-side caching by address to minimize API calls.
"""

import requests
import time
import json
from pathlib import Path
import argparse
import concurrent.futures
import threading
from config import GOOGLE_API_KEY

_cache_lock = threading.RLock()
_cache_dirty_lock = threading.Lock()
_print_lock = threading.Lock()

# Cache dirty flags
_address_cache_dirty = False

# Cache for address -> coordinates
address_cache = {}
CACHE_FILE = 'address_cache.json'


def load_cache():
    """Load address cache from file."""
    global address_cache
    if Path(CACHE_FILE).exists():
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            address_cache = json.load(f)
        print(f"Loaded {len(address_cache)} cached addresses")


def _mark_address_cache_dirty():
    global _address_cache_dirty
    with _cache_dirty_lock:
        _address_cache_dirty = True


def flush_cache(force: bool = False):
    """Persist cache if dirty (or if force=True)."""
    global _address_cache_dirty
    with _cache_dirty_lock:
        address_dirty = _address_cache_dirty
        if force:
            address_dirty = True
        _address_cache_dirty = False
    
    if address_dirty:
        with _cache_lock:
            save_cache()


def save_cache():
    """Save address cache to file."""
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(address_cache, f, indent=2, ensure_ascii=False)


def _print_block(text: str) -> None:
    """Print multi-line text without interleaving across threads."""
    with _print_lock:
        print(text, flush=True)


def geocode_with_google(
    address,
    max_retries: int = 3,
    backoff_base_seconds: float = 1.0,
    log_lines: list[str] | None = None
):
    """
    Convert address to latitude and longitude using Google Geocoding API.
    Uses cache if address is found in cache.
    """
    if log_lines is None:
        log_lines = []
    
    # Check cache first
    with _cache_lock:
        cached = address_cache.get(address)
    if cached:
        log_lines.append(f"    ✓ Using cached coordinates for address")
        return cached, log_lines
    
    base_url = "https://maps.googleapis.com/maps/api/geocode/json"
    
    params = {
        'address': address,
        'key': GOOGLE_API_KEY
    }
    
    retry_statuses = {"OVER_QUERY_LIMIT", "UNKNOWN_ERROR", "RESOURCE_EXHAUSTED"}
    
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(base_url, params=params, timeout=10)
            data = response.json()
            
            if data.get('status') == 'OK' and len(data.get('results', [])) > 0:
                location = data['results'][0]['geometry']['location']
                result = {
                    'latitude': location['lat'],
                    'longitude': location['lng'],
                    'formatted_address': data['results'][0]['formatted_address'],
                    'place_id': data['results'][0].get('place_id', '')
                }
                
                with _cache_lock:
                    address_cache[address] = result
                _mark_address_cache_dirty()
                
                return result, log_lines
            
            status = data.get('status', 'UNKNOWN')
            if status == 'REQUEST_DENIED':
                log_lines.append(f"    ✗ Google API error: {data.get('error_message', 'API key required or invalid')}")
                return None, log_lines
            
            if status in retry_statuses and attempt < max_retries:
                sleep_seconds = backoff_base_seconds * (2 ** attempt)
                log_lines.append(f"    ! Google geocoding retry ({status}) in {sleep_seconds:.1f}s")
                time.sleep(sleep_seconds)
                continue
            
            log_lines.append(f"    ✗ Google geocoding failed: {status}")
            return None, log_lines
        
        except Exception as e:
            if attempt < max_retries:
                sleep_seconds = backoff_base_seconds * (2 ** attempt)
                log_lines.append(f"    ! Geocoding error, retry in {sleep_seconds:.1f}s: {e}")
                time.sleep(sleep_seconds)
                continue
            
            log_lines.append(f"    ✗ Geocoding error: {e}")
            return None, log_lines


def process_team(team, google_retries: int = 3):
    """Process a single team: geocode the address.
    
    Returns (result, log_text) so the caller can print without interleaving.
    """
    log_lines = [f"  Geocoding: {team['name']}"]
    
    if 'error' in team:
        log_lines.append(f"    ✗ Skipped - error in address fetch")
        return team, "\n".join(log_lines)
    
    address = team.get('address')
    
    if not address:
        log_lines.append(f"    ✗ No address available")
        result = dict(team)
        result['error'] = 'no_address'
        return result, "\n".join(log_lines)
    
    # Geocode the address
    coords, geocode_logs = geocode_with_google(
        address,
        max_retries=google_retries,
        log_lines=log_lines
    )
    
    result = dict(team)
    
    if coords:
        result.update(coords)
        geocode_logs.append(f"    ✓ Coordinates: {coords['latitude']}, {coords['longitude']}")
    else:
        result['error'] = 'geocoding_failed'
        geocode_logs.append(f"    ✗ Geocoding failed")
    
    return result, "\n".join(geocode_logs)


def process_address_file(address_file_path, max_workers=10, google_retries=3):
    """Process a single address JSON file and geocode all teams."""
    print(f"{'='*80}")
    print(f"Processing: {address_file_path.name}")
    print(f"{'='*80}")
    
    # Check if output file already exists
    output_file = address_file_path.parent.parent / 'geocoded_teams' / address_file_path.name
    if output_file.exists():
        print(f"  Skipping - already geocoded")
        return
    
    # Load address data
    with open(address_file_path, 'r', encoding='utf-8') as f:
        address_data = json.load(f)
    
    league_name = address_data['league_name']
    teams = address_data['teams']
    
    print(f"League: {league_name}")
    print(f"Teams to geocode: {len(teams)}")
    
    team_results = [None] * len(teams)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures_to_idx = {}
        
        for idx, team in enumerate(teams):
            future = executor.submit(process_team, team, google_retries)
            futures_to_idx[future] = idx
        
        try:
            for future in concurrent.futures.as_completed(futures_to_idx):
                idx = futures_to_idx[future]
                
                try:
                    result, log_text = future.result()
                    _print_block(log_text)
                    team_results[idx] = result
                except Exception as e:
                    _print_block(f"  ✗ Error processing team: {e}")
                    team_results[idx] = dict(teams[idx])
                    team_results[idx]['error'] = f"exception: {e}"
        
        finally:
            flush_cache()
    
    geocoded_teams = [r for r in team_results if r]
    
    # Save results
    output_data = {
        'league_name': league_name,
        'league_url': address_data['league_url'],
        'teams': geocoded_teams,
        'team_count': len(geocoded_teams),
        'success_count': len([t for t in geocoded_teams if 'error' not in t])
    }
    
    output_file.parent.mkdir(exist_ok=True)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Saved to: {output_file}")
    print(f"  Successfully geocoded: {output_data['success_count']}/{len(geocoded_teams)}")


def main():
    """Main function to process all address files."""
    parser = argparse.ArgumentParser(description="Geocode team addresses using Google API")
    parser.add_argument("--workers", type=int, default=10, help="Max concurrent geocoding requests (default: 10)")
    parser.add_argument("--google-retries", type=int, default=3, help="Retries for transient failures (default: 3)")
    parser.add_argument("--league", type=str, default=None, help="Process only a single league")
    args = parser.parse_args()
    
    load_cache()
    
    address_dir = Path('team_addresses')
    if not address_dir.exists():
        print("Error: team_addresses directory not found")
        print("Run fetch_addresses.py first to get team addresses")
        return
    
    address_files = sorted(address_dir.glob('*.json'))
    
    if args.league:
        league_arg = Path(args.league)
        if league_arg.exists():
            address_files = [league_arg]
        else:
            candidate = address_dir / args.league
            if candidate.suffix != ".json":
                candidate = candidate.with_suffix(".json")
            if not candidate.exists():
                print(f"Error: address file not found: {args.league}")
                return
            address_files = [candidate]
    
    print(f"Found {len(address_files)} address files to process")
    
    for address_file in address_files:
        try:
            process_address_file(address_file, max_workers=args.workers, google_retries=args.google_retries)
        except Exception as e:
            print(f"\n✗ Error processing {address_file.name}: {e}")
            import traceback
            traceback.print_exc()
            flush_cache(force=True)
    
    print(f"{'='*80}")
    print(f"Complete! Geocoded data saved to 'geocoded_teams' directory")
    print(f"Address cache size: {len(address_cache)}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
