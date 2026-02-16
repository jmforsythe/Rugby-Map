"""
Script to fetch addresses from RFU team pages.
Scrapes each team page for Google Maps URL and extracts the address.
Saves intermediate results with addresses but no coordinates.
"""

import argparse
import concurrent.futures
import json
import random
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from utils import (
    AddressLeague,
    AddressTeam,
    AntiBotDetectedError,
    League,
    Team,
    get_headers,
    get_session,
    print_block,
)

_cache_lock = threading.RLock()

# Cache for club -> address data
club_cache: dict[str, str] = {}
CLUB_CACHE_FILE = "club_address_cache.json"

# Track clubs without addresses
clubs_without_addresses: list[tuple[str, str]] = []  # (club_name, team_url)


def load_cache() -> None:
    """Load club address cache from file."""
    global club_cache
    if Path(CLUB_CACHE_FILE).exists():
        with open(CLUB_CACHE_FILE, encoding="utf-8") as f:
            club_cache = json.load(f)
        print(f"Loaded {len(club_cache)} cached club addresses")


def save_cache() -> None:
    """Save club address cache to file."""
    with open(CLUB_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(club_cache, f, indent=2, ensure_ascii=False)


def extract_address_from_maps_url(maps_url: str) -> str | None:
    """Extract address from Google Maps search URL."""
    parsed = urlparse(maps_url)
    params = parse_qs(parsed.query)

    if "query" in params:
        address = unquote(params["query"][0])
        address = address.replace("\n", ", ")
        return address
    return None


def team_name_to_club_name(team_name: str) -> str:
    """Convert team name to club name (remove II, III, IV suffixes)."""
    last_word = team_name.split(" ")[-1]
    if last_word in ["II", "III", "IV"]:
        return " ".join(team_name.split(" ")[:-1])
    return team_name


def extract_maps_url_from_soup(soup: BeautifulSoup) -> str | None:
    """Extract Google Maps URL from club details button.

    Args:
        soup: BeautifulSoup object of the parsed page

    Returns:
        Maps URL or None if not found
    """
    club_btn = soup.find(class_="c036-club-details-btn")
    if club_btn and club_btn.get("href"):
        maps_url = club_btn.get("href").replace("&amp;", "&")
        return maps_url
    return None


def extract_address_from_soup(soup: BeautifulSoup) -> str | None:
    """Extract address directly from page text using c036-club-details-address element.

    Args:
        soup: BeautifulSoup object of the parsed page

    Returns:
        Address text or None if not found
    """
    address_elem = soup.find(class_="c036-club-details-address")
    if address_elem:
        address_text = address_elem.get_text(strip=True)
        address_text = " ".join(address_text.split())  # Clean whitespace
        return address_text
    return None


def fetch_club_address(
    club_name: str, team_url: str, delay_seconds: float = 2.0, max_retries: int = 3
) -> tuple[str | None, str]:
    """Fetch address for a club by scraping a team page.

    Args:
        club_name: Name of the club (team name with II/III/IV suffix removed)
        team_url: URL to any team page for this club
        delay_seconds: Delay between requests
        max_retries: Maximum retry attempts

    Returns:
        Tuple of (address string, log_text) for thread-safe printing.
    """
    log_lines: list[str] = [f"  Fetching: {club_name}", f"    URL: {team_url}"]

    # Fetch the page once and try both extraction methods
    for attempt in range(max_retries):
        try:
            if delay_seconds and delay_seconds > 0:
                time.sleep(delay_seconds + random.uniform(0.0, 0.35))

            response = get_session().get(team_url, headers=get_headers(), timeout=10)
            if response.status_code == 202:
                log_lines.append("    ✗ 202 code - bot detection")
                raise AntiBotDetectedError("202 code", log_text="\n".join(log_lines))

            response.raise_for_status()
            soup = BeautifulSoup(response.content, "html.parser")

            # Method 1: Try getting address directly from page text
            address_text = extract_address_from_soup(soup)
            if address_text:
                log_lines.append(f"    Address: {address_text}")
                log_lines.append("    ✓ Address extracted from page text")
                return (address_text, "\n".join(log_lines))
            else:
                log_lines.append("    ! No address text found on page")

            # Method 2: Try getting address from Google Maps URL
            maps_url = extract_maps_url_from_soup(soup)
            if maps_url:
                address = extract_address_from_maps_url(maps_url)

                if address:
                    log_lines.append(f"    Address: {address}")
                    log_lines.append("    ✓ Address extracted from Maps URL")
                    return (address, "\n".join(log_lines))
                else:
                    log_lines.append("    ! Could not extract address from Maps URL")
            else:
                log_lines.append("    ! No Maps URL found")

            # Both methods failed on this page fetch
            break

        except AntiBotDetectedError:
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                log_lines.append(f"    ! Attempt {attempt + 1} failed: {e} - retrying...")
                time.sleep(1.0 * (attempt + 1))  # Exponential backoff
            else:
                log_lines.append(f"    ✗ All {max_retries} attempts failed: {e}")

    # If no methods worked, try modifying club name and retry once more
    possible_modifiers = ["women's", "ladies"]
    if any(mod.lower() in club_name.lower().split() for mod in possible_modifiers):
        club_name_words = club_name.split()
        modified_club_name = " ".join(
            word for word in club_name_words if word.lower() not in possible_modifiers
        )

        with _cache_lock:
            cached_address = club_cache.get(modified_club_name)
        if cached_address is not None:
            log_lines.append(
                f"    ✓ Found cached address for modified club name: {modified_club_name}"
            )
            return cached_address, "\n".join(log_lines)

        log_lines.append(f"    ! Retrying with modified club name: {modified_club_name}")
        modified_club_name_address = fetch_club_address(
            modified_club_name, team_url, delay_seconds, max_retries
        )
        print("foo:", modified_club_name_address)
        if modified_club_name_address[0]:
            log_lines.append(f"    ✓ Address found with modified club name: {modified_club_name}")
            return modified_club_name_address

    log_lines.append("    ✗ No address found using any method")
    return None, "\n".join(log_lines)


def process_league_file(
    league_file_path: Path,
    season: str,
    max_workers: int = 14,
    delay_seconds: float = 2.0,
    max_retries: int = 3,
) -> None:
    """Process a single league JSON file and fetch all addresses."""
    print(f"{"="*80}")
    print(f"Processing: {league_file_path.name}")
    print(f"{"="*80}")

    # Check if output file already exists
    output_file = Path("team_addresses") / season / league_file_path.name
    if output_file.exists():
        print("  Skipping - already processed")
        return

    # Load league data
    with open(league_file_path, encoding="utf-8") as f:
        league_data: League = json.load(f)

    league_name: str = league_data["league_name"]
    teams: list[Team] = league_data["teams"]

    print(f"League: {league_name}")
    print(f"Teams to process: {len(teams)}")

    team_results: list[AddressTeam | None] = [None] * len(teams)
    club_futures: dict[str, concurrent.futures.Future] = {}
    club_dependents: dict[str, list[tuple[int, Team]]] = {}

    def create_team_address(address: str | None, team: Team) -> AddressTeam:
        """Create TeamAddress by combining club name, address, and team-specific fields."""
        return {
            "name": team["name"],
            "url": team["url"],
            "image_url": team.get("image_url"),
            "address": address,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures_to_club: dict[concurrent.futures.Future, str] = {}

        for idx, team in enumerate(teams):
            team_name = team["name"]
            team_url = team["url"]

            if team_name.startswith("To be arranged") or team_name.startswith("TBC"):
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
                    f"    Address: {cached_address or "N/A"}",
                ]

                print_block("\n".join(log_lines))
                continue

            club_dependents.setdefault(club_name, []).append((idx, team))

            if club_name in club_futures:
                continue

            future = executor.submit(
                fetch_club_address, club_name, team_url, delay_seconds, max_retries
            )
            club_futures[club_name] = future
            futures_to_club[future] = club_name

        try:
            for future in concurrent.futures.as_completed(futures_to_club):
                club_name = futures_to_club[future]

                try:
                    fetched_address: str | None
                    log_text: str
                    fetched_address, log_text = future.result()
                    print_block(log_text)
                except AntiBotDetectedError as e:
                    for f in futures_to_club:
                        f.cancel()
                    if getattr(e, "log_text", None):
                        print_block(e.log_text)
                    print(f"{"="*80}")
                    print("ANTI-BOT DETECTION TRIGGERED")
                    print("Aborting processing to avoid being blocked")
                    print(f"{"="*80}")
                    save_cache()
                    raise
                except Exception as e:
                    print_block(f"  Processing: {club_name}\n    ✗ Error: {e}")
                    fetched_address = None

                # Store in cache (address only)
                with _cache_lock:
                    club_cache[club_name] = fetched_address

                for idx, team in club_dependents.get(club_name, []):
                    team_results[idx] = create_team_address(fetched_address, team)

                if fetched_address is None:
                    # Track clubs without addresses
                    dependents = club_dependents.get(club_name, [])
                    if dependents:
                        clubs_without_addresses.append((club_name, dependents[0][1]["url"]))

        finally:
            save_cache()

    teams_with_addresses: list[AddressTeam] = [r for r in team_results if r]

    # Save results
    output_data: AddressLeague = {
        "league_name": league_name,
        "league_url": league_data["league_url"],
        "teams": teams_with_addresses,
        "team_count": len(teams_with_addresses),
        "success_count": len([t for t in teams_with_addresses if "error" not in t]),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"✓ Saved to: {output_file}")
    print(f"  Successfully fetched: {output_data["success_count"]}/{len(teams_with_addresses)}")


def main() -> None:
    """Main function to process all league files."""
    global clubs_without_addresses
    clubs_without_addresses = []  # Reset at start of main

    parser = argparse.ArgumentParser(description="Fetch addresses from RFU team pages")
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to process (e.g., 2024-2025, 2025-2026). Default: 2025-2026",
    )
    parser.add_argument(
        "--workers", type=int, default=7, help="Max concurrent requests (default: 7)"
    )
    parser.add_argument(
        "--delay", type=float, default=1, help="Seconds between requests (default: 1)"
    )
    parser.add_argument(
        "--retries", type=int, default=3, help="Max retries for failed requests (default: 3)"
    )
    parser.add_argument("--league", type=str, default=None, help="Process only a single league")
    args = parser.parse_args()

    season = args.season
    print(f"Processing season: {season}")

    load_cache()

    league_dir = Path("league_data") / season
    if not league_dir.exists():
        print(f"Error: league_data/{season} directory not found")
        return

    league_files: list[Path] = sorted(league_dir.glob("*.json"))

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
            process_league_file(
                league_file,
                season,
                max_workers=args.workers,
                delay_seconds=args.delay,
                max_retries=args.retries,
            )
        except AntiBotDetectedError:
            print("\n✗ Anti-bot detection triggered")
            print("Please wait before running the script again.")
            save_cache()
            return
        except Exception as e:
            print(f"\n✗ Error processing {league_file.name}: {e}")
            import traceback

            traceback.print_exc()
            save_cache()

    print(f"{"="*80}")
    print('Complete! Addresses saved to "team_addresses" directory')
    print(f"Club cache size: {len(club_cache)}")
    print(f"{"="*80}")

    # Print clubs without addresses
    if clubs_without_addresses:
        print(f"\n{"="*80}")
        print(f"CLUBS WITHOUT ADDRESSES ({len(clubs_without_addresses)})")
        print(f"{"="*80}")
        for club_name, team_url in clubs_without_addresses:
            print(f"  {club_name}")
            print(f"    URL: {team_url}&season={season}")
        print(f"{"="*80}")
    else:
        print("\n✓ All clubs have addresses!")


if __name__ == "__main__":
    main()
