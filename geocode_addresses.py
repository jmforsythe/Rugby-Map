"""
Script to geocode team addresses using OpenStreetMap Nominatim API.
Reads team addresses from team_addresses/ directory and adds coordinates.
Includes client-side caching by address to minimize API calls.
"""

import argparse
import concurrent.futures
import json
import re
import threading
import time
from pathlib import Path

import requests

from utils import (
    AddressLeague,
    AddressTeam,
    GeocodedLeague,
    GeocodedTeam,
    GeocodeResult,
    print_block,
)

_cache_lock = threading.RLock()
_cache_dirty_lock = threading.Lock()

# Cache dirty flags
_address_cache_dirty = False

# Cache for address -> coordinates
geocode_cache: dict[str, GeocodeResult] = {}
CACHE_FILE = "geocode_cache.json"


def load_cache() -> None:
    """Load address cache from file."""
    global geocode_cache
    if Path(CACHE_FILE).exists():
        with open(CACHE_FILE, encoding="utf-8") as f:
            geocode_cache = json.load(f)
        print(f"Loaded {len(geocode_cache)} cached addresses")


def _mark_address_cache_dirty() -> None:
    global _address_cache_dirty
    with _cache_dirty_lock:
        _address_cache_dirty = True


def flush_cache(force: bool = False) -> None:
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


def save_cache() -> None:
    """Save address cache to file."""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(geocode_cache, f, indent=2, ensure_ascii=False)


def extract_uk_postcode(address: str) -> str | None:
    """Extract UK postcode from address string."""
    # UK postcode pattern: https://en.wikipedia.org/wiki/Postcodes_in_the_United_Kingdom
    postcode_pattern = r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}\b"
    match = re.search(postcode_pattern, address, re.IGNORECASE)
    if match:
        return match.group(0).strip()
    return None


def geocode_with_nominatim(
    address: str,
    max_retries: int = 3,
    backoff_base_seconds: float = 1.0,
    log_lines: list[str] | None = None,
) -> tuple[GeocodeResult | None, list[str]]:
    """
    Convert address to latitude and longitude using OpenStreetMap Nominatim API.
    Uses cache if address is found in cache.

    Returns:
        Tuple of (geocode_result_dict, log_lines_list)
        geocode_result_dict is None if geocoding failed
    """
    if log_lines is None:
        log_lines = []

    # Check cache first
    with _cache_lock:
        cached = geocode_cache.get(address)
    if cached:
        log_lines.append("    ✓ Using cached coordinates for address")
        return cached, log_lines

    base_url = "https://nominatim.openstreetmap.org/search"

    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "countrycodes": "gb,im,je,gg",  # UK + IoM + Jersey + Guernsey
        "addressdetails": 1,
    }

    headers = {"User-Agent": "RugbyMappingProject/1.0 (https://github.com/jmforsythe/Rugby-Map)"}

    retry_statuses = {503, 429}  # Service unavailable, rate limit

    for attempt in range(max_retries + 1):
        try:
            # Nominatim requires 1 second between requests
            time.sleep(1.0)

            response = requests.get(base_url, params=params, headers=headers, timeout=10)

            if response.status_code == 200:
                data = response.json()

                if len(data) > 0:
                    result: GeocodeResult = {
                        "latitude": float(data[0]["lat"]),
                        "longitude": float(data[0]["lon"]),
                        "formatted_address": data[0].get("display_name", address),
                        "place_id": data[0].get("place_id", ""),
                    }

                    with _cache_lock:
                        geocode_cache[address] = result
                    _mark_address_cache_dirty()

                    return result, log_lines
                else:
                    # No results - try with just postcode if we haven"t already
                    if params["q"] == address:  # First attempt with full address
                        postcode = extract_uk_postcode(address)
                        if postcode:
                            log_lines.append(
                                f"    ! No results for full address, trying postcode: {postcode}"
                            )
                            params["q"] = postcode
                            time.sleep(1.0)
                            response = requests.get(
                                base_url, params=params, headers=headers, timeout=10
                            )

                            if response.status_code == 200:
                                data = response.json()
                                if len(data) > 0:
                                    result: GeocodeResult = {
                                        "latitude": float(data[0]["lat"]),
                                        "longitude": float(data[0]["lon"]),
                                        "formatted_address": data[0].get("display_name", address),
                                        "place_id": data[0].get("place_id", ""),
                                    }

                                    with _cache_lock:
                                        geocode_cache[address] = result
                                    _mark_address_cache_dirty()

                                    log_lines.append("    ✓ Found using postcode")
                                    return result, log_lines

                    log_lines.append("    ✗ No results found for address or postcode")
                    return None, log_lines

            if response.status_code in retry_statuses and attempt < max_retries:
                sleep_seconds = backoff_base_seconds * (2**attempt)
                log_lines.append(
                    f"    ! Nominatim retry (status {response.status_code}) in {sleep_seconds:.1f}s"
                )
                time.sleep(sleep_seconds)
                continue

            log_lines.append(f"    ✗ Nominatim error: HTTP {response.status_code}")
            return None, log_lines

        except KeyboardInterrupt:
            log_lines.append("    ✗ Interrupted by user")
            raise

        except Exception as e:
            if attempt < max_retries:
                sleep_seconds = backoff_base_seconds * (2**attempt)
                log_lines.append(f"    ! Geocoding error, retry in {sleep_seconds:.1f}s: {e}")
                time.sleep(sleep_seconds)
                continue

            log_lines.append(f"    ✗ Geocoding error: {e}")
            return None, log_lines


def process_team(team: AddressTeam, api_retries: int = 3) -> tuple[GeocodedTeam, str]:
    """Process a single team: geocode the address.

    Args:
        team: Team data with address to geocode
        api_retries: Number of retries for transient failures

    Returns:
        Tuple of (GeocodedTeam, log_text) so the caller can print without interleaving.
    """
    log_lines: list[str] = [f"  Geocoding: {team["name"]}"]

    if "error" in team:
        log_lines.append("    ✗ Skipped - error in address fetch")
        result: GeocodedTeam = dict(team)  # type: ignore
        return result, "\n".join(log_lines)

    address: str | None = team.get("address")

    if not address:
        log_lines.append("    ✗ No address available")
        result = dict(team)  # type: ignore
        result["error"] = "no_address"  # type: ignore
        return result, "\n".join(log_lines)

    # Geocode the address
    coords: GeocodeResult | None
    coords, log_lines = geocode_with_nominatim(
        address, max_retries=api_retries, log_lines=log_lines
    )

    result = dict(team)  # type: ignore

    if coords:
        result.update(coords)  # type: ignore
        log_lines.append(f"    ✓ Coordinates: {coords["latitude"]}, {coords["longitude"]}")
    else:
        result["error"] = "geocoding_failed"  # type: ignore
        log_lines.append("    ✗ Geocoding failed")

    return result, "\n".join(log_lines)


def process_address_file(
    address_file_path: Path, season: str, max_workers: int = 10, api_retries: int = 3
) -> None:
    """Process a single address JSON file and geocode all teams."""
    print(f"{"="*80}")
    print(f"Processing: {address_file_path.name}")
    print(f"{"="*80}")

    # Check if output file already exists
    output_file = Path("geocoded_teams") / season / address_file_path.name
    if output_file.exists():
        print("  Skipping - already geocoded")
        return

    # Load address data
    with open(address_file_path, encoding="utf-8") as f:
        address_data: AddressLeague = json.load(f)

    league_name: str = address_data["league_name"]
    teams: list[AddressTeam] = address_data["teams"]

    print(f"League: {league_name}")
    print(f"Teams to geocode: {len(teams)}")

    team_results: list[GeocodedTeam | None] = [None] * len(teams)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures_to_idx: dict[concurrent.futures.Future, int] = {}

        for idx, team in enumerate(teams):
            future = executor.submit(process_team, team, api_retries)
            futures_to_idx[future] = idx

        try:
            for future in concurrent.futures.as_completed(futures_to_idx):
                idx = futures_to_idx[future]

                try:
                    result, log_text = future.result()
                    print_block(log_text)
                    team_results[idx] = result
                except KeyboardInterrupt:
                    print_block("  ✗ Interrupted by user")
                    raise
                except Exception as e:
                    print_block(f"  ✗ Error processing team: {e}")
                    error_result: GeocodedTeam = dict(teams[idx])  # type: ignore
                    error_result["error"] = f"exception: {e}"  # type: ignore
                    team_results[idx] = error_result

        finally:
            flush_cache()

    geocoded_teams: list[GeocodedTeam] = [r for r in team_results if r]

    # Count successes
    success_count: int = len([t for t in geocoded_teams if "error" not in t])

    # Save results
    output_data: GeocodedLeague = {
        "league_name": league_name,
        "league_url": address_data["league_url"],
        "teams": geocoded_teams,
        "team_count": len(geocoded_teams),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"✓ Saved to: {output_file}")
    print(f"  Successfully geocoded: {success_count}/{len(geocoded_teams)}")


def main() -> None:
    """Main function to process all address files."""
    parser = argparse.ArgumentParser(
        description="Geocode team addresses using OpenStreetMap Nominatim API"
    )
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to process (e.g., 2024-2025, 2025-2026). Default: 2025-2026",
    )
    parser.add_argument(
        "--workers", type=int, default=10, help="Max concurrent geocoding requests (default: 10)"
    )
    parser.add_argument(
        "--api-retries", type=int, default=3, help="Retries for transient failures (default: 3)"
    )
    parser.add_argument("--league", type=str, default=None, help="Process only a single league")
    args = parser.parse_args()

    season = args.season
    print(f"Processing season: {season}")

    load_cache()

    address_dir = Path("team_addresses") / season
    if not address_dir.exists():
        print(f"Error: team_addresses/{season} directory not found")
        print("Run fetch_addresses.py first to get team addresses")
        return

    address_files: list[Path] = sorted(address_dir.glob("*.json"))

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
            process_address_file(
                address_file, season, max_workers=args.workers, api_retries=args.api_retries
            )
        except KeyboardInterrupt:
            print("\n\n✗ Interrupted by user")
            print("Saving cache and exiting...")
            flush_cache(force=True)
            raise
        except Exception as e:
            print(f"\n✗ Error processing {address_file.name}: {e}")
            import traceback

            traceback.print_exc()
            flush_cache(force=True)

    print(f"{"="*80}")
    print('Complete! Geocoded data saved to "geocoded_teams" directory')
    print(f"Address cache size: {len(geocode_cache)}")
    print(f"{"="*80}")


if __name__ == "__main__":
    main()
