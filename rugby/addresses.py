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

import requests
from bs4 import BeautifulSoup, Tag

from core import (
    AddressLeague,
    AddressTeam,
    AntiBotDetectedError,
    League,
    Team,
    get_headers,
    get_session,
    print_block,
    setup_logging,
)
from core.config import CACHE_DIR, CURRENT_SEASON
from rugby import DATA_DIR

_cache_lock = threading.RLock()

# Cache for club -> address data
club_cache: dict[str, str | None] = {}
CLUB_CACHE_FILE = str(CACHE_DIR / "club_address_cache.json")

# Cache for derived club_name -> ground-truth club name (from RFU's own
# "c036-club-details-heading" element), keyed the same way as club_cache so
# the two caches can be joined directly.
club_name_cache: dict[str, str] = {}
CLUB_NAME_CACHE_FILE = str(CACHE_DIR / "club_canonical_name_cache.json")

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


def load_name_cache() -> None:
    """Load canonical club name cache from file."""
    global club_name_cache
    if Path(CLUB_NAME_CACHE_FILE).exists():
        with open(CLUB_NAME_CACHE_FILE, encoding="utf-8") as f:
            club_name_cache = json.load(f)
        print(f"Loaded {len(club_name_cache)} cached canonical club names")


def save_name_cache() -> None:
    """Save canonical club name cache to file."""
    with open(CLUB_NAME_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(club_name_cache, f, indent=2, ensure_ascii=False, sort_keys=True)


def extract_address_from_maps_url(maps_url: str) -> str | None:
    """Extract address from Google Maps search URL."""
    parsed = urlparse(maps_url)
    params = parse_qs(parsed.query)

    for key in ("query", "q"):
        if key in params:
            address = unquote(params[key][0])
            address = address.replace("\n", ", ")
            return address
    return None


def team_name_to_club_name(team_name: str) -> str:
    """Convert team name to club name (remove II, III, IV suffixes)."""
    last_word = team_name.split(" ")[-1]
    if last_word in ["II", "III", "IV", "V"]:
        return " ".join(team_name.split(" ")[:-1])
    last_two_words = " ".join(team_name.split(" ")[-2:])
    if last_two_words in [
        "2nd XV",
        "3rd XV",
        "4th XV",
        "5th XV",
        "(2nd XV)",
        "(3rd XV)",
        "(4th XV)",
        "(5th XV)",
    ]:
        return " ".join(team_name.split(" ")[:-2])
    return team_name


def extract_maps_url_from_soup(soup: BeautifulSoup) -> str | None:
    """Extract Google Maps URL from club details button.

    Args:
        soup: BeautifulSoup object of the parsed page

    Returns:
        Maps URL or None if not found
    """
    club_btn = soup.find(class_="c036-club-details-btn")
    if isinstance(club_btn, Tag):
        href = club_btn.get("href")
        if isinstance(href, str):
            return href.replace("&amp;", "&")
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


def is_space_separated_address(address: str | None) -> bool:
    """Return True when ``address`` looks like RFU page text (no comma separators)."""
    return bool(address and "," not in address)


def extract_address_from_rfu_soup(
    soup: BeautifulSoup, *, prefer_maps: bool = True
) -> tuple[str | None, str]:
    """Extract a club address from an RFU team page.

    By default prefers the comma-separated string embedded in the Google Maps
    link (``c036-club-details-btn``), falling back to the space-separated page
    text (``c036-club-details-address``).

    Returns:
        ``(address, source)`` where ``source`` is ``"maps"``, ``"page"``, or ``""``.
    """
    page_text = extract_address_from_soup(soup)
    maps_url = extract_maps_url_from_soup(soup)
    maps_address = extract_address_from_maps_url(maps_url) if maps_url else None

    if prefer_maps:
        if maps_address:
            return maps_address, "maps"
        if page_text:
            return page_text, "page"
    else:
        if page_text:
            return page_text, "page"
        if maps_address:
            return maps_address, "maps"
    return None, ""


def extract_club_name_from_soup(soup: BeautifulSoup) -> str | None:
    """Extract the ground-truth club name from the c036-club-details-heading element.

    This is the RFU's own name for the club (e.g. "Tamworth RUFC"), independent
    of any per-team display name, so it doesn't depend on ``team_name_to_club_name``
    correctly stripping a "II"/"3rd XV"/etc. suffix.

    Args:
        soup: BeautifulSoup object of the parsed page

    Returns:
        Club name text or None if not found
    """
    heading_elem = soup.find(class_="c036-club-details-heading")
    if heading_elem:
        name_text = heading_elem.get_text(strip=True)
        name_text = " ".join(name_text.split())  # Clean whitespace
        return name_text or None
    return None


def sleep_before_rfu_request(delay_seconds: float) -> None:
    """Pacing before hitting RFU (matches historical scripts: delay + small jitter)."""
    if delay_seconds and delay_seconds > 0:
        time.sleep(delay_seconds + random.uniform(0.0, 0.35))


def get_rfu_team_page_response(url: str, *, timeout: int = 10) -> requests.Response:
    """Single GET to an RFU page with curl fallback when requests sees Cloudflare 202."""
    response = get_session().get(url, headers=get_headers(), timeout=timeout)
    if response.status_code == 202:
        from core.http import _curl_fallback

        response = _curl_fallback(url, None, timeout)
    return response


def maybe_raise_rfu_antibot(response: requests.Response, *, log_text: str) -> None:
    """Raise AntiBotDetectedError when RFU still returns a challenge after curl fallback.

    Prefer :func:`handle_rfu_antibot_with_backoff` inside retry loops so transient
    Cloudflare blocks get exponential backoff before giving up.
    """
    if response.status_code in (202, 403):
        raise AntiBotDetectedError(f"{response.status_code} code", log_text=log_text)


def handle_rfu_antibot_with_backoff(
    response: requests.Response,
    log_lines: list[str],
    attempt: int,
    max_retries: int,
    *,
    backoff_base_seconds: float = 5.0,
) -> bool:
    """If response is anti-bot, either backoff and retry or raise.

    Uses exponential waits ``backoff_base_seconds * 2**attempt`` before the
    next HTTP attempt (same ``attempt`` index as the caller's retry loop).

    Returns:
        ``True`` if the caller should ``continue`` to the next loop iteration.
        ``False`` if the response is ok (not 202/403 after curl fallback).

    Raises:
        AntiBotDetectedError: On the final attempt when still blocked.
    """
    if response.status_code not in (202, 403):
        return False

    log_lines.append(f"    ✗ {response.status_code} blocked - bot detection")

    if attempt < max_retries - 1:
        wait = backoff_base_seconds * (2**attempt)
        log_lines.append(
            f"    ! Anti-bot — backing off {wait:.1f}s then retry ({attempt + 1}/{max_retries})..."
        )
        time.sleep(wait)
        return True

    raise AntiBotDetectedError(f"{response.status_code} code", log_text="\n".join(log_lines))


def _fetch_rfu_page_with_retries(
    team_url: str, log_lines: list[str], delay_seconds: float, max_retries: int
) -> BeautifulSoup | None:
    """Fetch and parse an RFU team page, handling anti-bot backoff and retries.

    Returns:
        Parsed soup, or None if all retries were exhausted on non-antibot errors.

    Raises:
        AntiBotDetectedError: If anti-bot detection persists through the final retry.
    """
    for attempt in range(max_retries):
        try:
            sleep_before_rfu_request(delay_seconds)

            response = get_rfu_team_page_response(team_url, timeout=10)
            if handle_rfu_antibot_with_backoff(response, log_lines, attempt, max_retries):
                continue

            response.raise_for_status()
            return BeautifulSoup(response.content, "html.parser")

        except AntiBotDetectedError:
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                log_lines.append(f"    ! Attempt {attempt + 1} failed: {e} - retrying...")
                time.sleep(1.0 * (attempt + 1))  # Exponential backoff
            else:
                log_lines.append(f"    ✗ All {max_retries} attempts failed: {e}")
    return None


def _cache_canonical_name_from_soup(
    club_name: str, soup: BeautifulSoup, log_lines: list[str]
) -> None:
    """Extract the RFU club heading from an already-fetched page and cache it.

    Piggybacks on address-fetch requests so the canonical name cache stays
    populated at zero extra request cost.
    """
    canonical_name = extract_club_name_from_soup(soup)
    if canonical_name:
        with _cache_lock:
            club_name_cache.setdefault(club_name, canonical_name)
        log_lines.append(f"    Canonical name: {canonical_name}")


def fetch_club_address(
    club_name: str,
    team_url: str,
    delay_seconds: float = 2.0,
    max_retries: int = 3,
    *,
    prefer_maps: bool = True,
) -> tuple[str | None, str]:
    """Fetch address for a club by scraping a team page.

    Args:
        club_name: Name of the club (team name with II/III/IV suffix removed)
        team_url: URL to any team page for this club
        delay_seconds: Delay between requests
        max_retries: Maximum retry attempts
        prefer_maps: When True (default), use the Google Maps URL query first.

    Returns:
        Tuple of (address string, log_text) for thread-safe printing.
    """
    log_lines: list[str] = [f"  Fetching: {club_name}", f"    URL: {team_url}"]

    # Fetch the page once and try both extraction methods
    soup = _fetch_rfu_page_with_retries(team_url, log_lines, delay_seconds, max_retries)
    if soup is not None:
        _cache_canonical_name_from_soup(club_name, soup, log_lines)

        address, source = extract_address_from_rfu_soup(soup, prefer_maps=prefer_maps)
        if address:
            log_lines.append(f"    Address: {address}")
            if source == "maps":
                log_lines.append("    ✓ Address extracted from Maps URL")
            elif source == "page":
                log_lines.append("    ✓ Address extracted from page text")
            return (address, "\n".join(log_lines))

        log_lines.append("    ! No address text found on page")
        log_lines.append("    ! No Maps URL found")

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
            modified_club_name,
            team_url,
            delay_seconds,
            max_retries,
            prefer_maps=prefer_maps,
        )
        if modified_club_name_address[0]:
            log_lines.append(f"    ✓ Address found with modified club name: {modified_club_name}")
            return modified_club_name_address

    log_lines.append("    ✗ No address found using any method")
    return None, "\n".join(log_lines)


def fetch_club_canonical_name(
    club_name: str, team_url: str, delay_seconds: float = 2.0, max_retries: int = 3
) -> tuple[str | None, str]:
    """Fetch the ground-truth club name from an RFU team page's details heading.

    Used to backfill ``club_name_cache`` for clubs that already have a cached
    address (so ``fetch_club_address``'s piggybacked extraction never ran).

    Args:
        club_name: Derived club name (cache key), used only for logging.
        team_url: URL to any team page for this club.
        delay_seconds: Delay between requests.
        max_retries: Maximum retry attempts.

    Returns:
        Tuple of (canonical club name, log_text) for thread-safe printing.
    """
    log_lines: list[str] = [f"  Fetching: {club_name}", f"    URL: {team_url}"]

    soup = _fetch_rfu_page_with_retries(team_url, log_lines, delay_seconds, max_retries)
    if soup is None:
        log_lines.append("    ✗ No canonical name found (page fetch failed)")
        return None, "\n".join(log_lines)

    canonical_name = extract_club_name_from_soup(soup)
    if canonical_name:
        log_lines.append(f"    Canonical name: {canonical_name}")
        log_lines.append("    ✓ Canonical name extracted")
    else:
        log_lines.append("    ! No canonical name found on page")
    return canonical_name, "\n".join(log_lines)


def process_league_file(
    league_file_path: Path,
    league_dir: Path,
    season: str,
    max_workers: int = 14,
    delay_seconds: float = 2.0,
    max_retries: int = 3,
    *,
    force: bool = False,
) -> None:
    """Process a single league JSON file and fetch all addresses."""
    print(f"{"="*80}")
    print(f"Processing: {league_file_path.name}")
    print(f"{"="*80}")

    # Mirror subdirectory structure (e.g. merit/) from league_data to team_addresses
    relative = league_file_path.relative_to(league_dir)
    output_file = DATA_DIR / "team_addresses" / season / relative
    if output_file.exists() and not force:
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
                    if e.log_text is not None:
                        print_block(e.log_text)
                    print(f"{"="*80}")
                    print("ANTI-BOT DETECTION TRIGGERED")
                    print("Aborting processing to avoid being blocked")
                    print(f"{"="*80}")
                    save_cache()
                    save_name_cache()
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
            save_name_cache()

    teams_with_addresses: list[AddressTeam] = [r for r in team_results if r]

    # Save results
    output_data: AddressLeague = {
        "league_name": league_name,
        "league_url": league_data["league_url"],
        "teams": teams_with_addresses,
        "team_count": len(teams_with_addresses),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    success_count = len([t for t in teams_with_addresses if "error" not in t])
    print(f"[ok] Saved to: {output_file}")
    print(f"  Successfully fetched: {success_count}/{len(teams_with_addresses)}")


def main() -> None:
    """Main function to process all league files."""
    global clubs_without_addresses
    clubs_without_addresses = []  # Reset at start of main

    parser = argparse.ArgumentParser(description="Fetch addresses from RFU team pages")
    parser.add_argument(
        "--season",
        type=str,
        default=CURRENT_SEASON,
        help=f"Season to process (e.g., 2024-2025, 2025-2026). Default: {CURRENT_SEASON}",
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch addresses even if team_addresses JSON already exists",
    )
    args = parser.parse_args()

    setup_logging()

    season = args.season
    print(f"Processing season: {season}")

    load_cache()
    load_name_cache()

    league_dir = DATA_DIR / "league_data" / season
    if not league_dir.exists():
        print(f"Error: league_data/{season} directory not found")
        return

    league_files: list[Path] = sorted(
        f for f in league_dir.rglob("*.json") if not f.name.startswith("_")
    )

    if args.league:
        league_arg = Path(args.league)
        if league_arg.exists():
            league_files = [league_arg]
        else:
            # Search both root and subdirectories
            candidates = list(league_dir.rglob(f"{args.league}*.json"))
            if not candidates:
                candidate = league_dir / args.league
                if candidate.suffix != ".json":
                    candidate = candidate.with_suffix(".json")
                if candidate.exists():
                    candidates = [candidate]
            if not candidates:
                print(f"Error: league file not found: {args.league}")
                return
            league_files = candidates

    print(f"Found {len(league_files)} league files to process")

    for league_file in league_files:
        try:
            process_league_file(
                league_file,
                league_dir,
                season,
                max_workers=args.workers,
                delay_seconds=args.delay,
                max_retries=args.retries,
                force=args.force,
            )
        except AntiBotDetectedError:
            print("\n✗ Anti-bot detection triggered")
            print("Please wait before running the script again.")
            save_cache()
            save_name_cache()
            return
        except Exception as e:
            print(f"\n✗ Error processing {league_file.name}: {e}")
            import traceback

            traceback.print_exc()
            save_cache()
            save_name_cache()

    print(f"{"="*80}")
    print('Complete! Addresses saved to "team_addresses" directory')
    print(f"Club cache size: {len(club_cache)}")
    print(f"Canonical name cache size: {len(club_name_cache)}")
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
        print("\n[ok] All clubs have addresses!")


if __name__ == "__main__":
    main()
