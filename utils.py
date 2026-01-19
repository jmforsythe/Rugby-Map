"""
Shared type definitions and utility functions for the rugby mapping data pipeline.

Data flows through the following stages:
1. scrape_leagues_teams.py -> league_data/*.json (League)
2. fetch_addresses.py -> team_addresses/*.json (TeamAddressData)
3. geocode_addresses.py -> geocoded_teams/*.json (GeocodedLeague)
4. make_tier_maps.py -> tier_maps/*.html
"""

import functools
import json
import requests
import time
import threading
from typing import TypedDict, Optional, List, NotRequired, Dict


# ============================================================================
# Type Definitions
# ============================================================================

# Stage 1: League scraping (scrape_leagues_teams.py)

class Team(TypedDict):
    """Team data from RFU league tables"""
    name: str
    url: str
    image_url: Optional[str]


class LeagueInfo(TypedDict):
    """League metadata for scraping"""
    name: str
    url: str
    parent_url: str


class League(TypedDict):
    """Complete league data with teams (league_data/*.json)"""
    league_name: str
    league_url: str
    teams: List[Team]
    team_count: int


# Stage 2: Address fetching (fetch_addresses.py)

class AddressTeam(Team):
    """Team with fetched address (extends Team)"""
    address: Optional[str]


class AddressLeague(TypedDict):
    """League data with team addresses (team_addresses/*.json)"""
    league_name: str
    league_url: str
    teams: List[AddressTeam]
    team_count: int


# Stage 3: Geocoding (geocode_addresses.py)

class GeocodeResult(TypedDict):
    """Result from geocoding operation (also used for cache entries)"""
    latitude: float
    longitude: float
    formatted_address: str
    place_id: str  # Google Place ID


class GeocodedTeam(AddressTeam):
    """Team with geocoded coordinates (extends TeamAddress)"""
    latitude: NotRequired[float]
    longitude: NotRequired[float]
    formatted_address: NotRequired[str]
    place_id: NotRequired[str]


class GeocodedLeague(TypedDict):
    """League data with geocoded teams (geocoded_teams/*.json)"""
    league_name: str
    league_url: str
    teams: List[GeocodedTeam]
    team_count: int


# Stage 4: Map generation (make_tier_maps.py)

class MapTeam(GeocodedTeam):
    """Team with ITL region assignments and tier information for mapping"""
    league: str
    tier: str
    itl1: Optional[str]
    itl2: Optional[str]
    itl3: Optional[str]


# ============================================================================
# Shared Utility Functions
# ============================================================================

class AntiBotDetected(Exception):
    """Exception raised when anti-bot detection is triggered."""
    log_text: Optional[str]
    
    def __init__(self, message: str, *, log_text: Optional[str] = None) -> None:
        super().__init__(message)
        self.log_text = log_text


_thread_local = threading.local()
_print_lock = threading.Lock()


def get_session() -> requests.Session:
    """Get thread-local session (requests.Session is not thread-safe)."""
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        _thread_local.session = sess
    return sess


def get_headers(referer: Optional[str] = None) -> Dict[str, str]:
    """Get standard headers with optional referer for RFU website requests."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0"
    }
    if referer:
        headers["Referer"] = referer
    return headers


def make_request(
    url: str,
    referer: Optional[str] = None,
    max_retries: int = 3,
    timeout: int = 30,
    delay_seconds: float = 2.0
) -> requests.Response:
    """
    Make an HTTP GET request with retry logic and exponential backoff.
    
    Args:
        url: URL to request
        referer: Optional referer header
        max_retries: Maximum number of retry attempts
        timeout: Request timeout in seconds
        delay_seconds: Base delay between requests
    
    Returns:
        Response object
    
    Raises:
        requests.exceptions.RequestException: If all retries fail
    """
    for attempt in range(max_retries):
        try:
            if delay_seconds > 0:
                time.sleep(delay_seconds + attempt * 2)  # Increase delay with each attempt
            
            response = get_session().get(
                url,
                headers=get_headers(referer),
                timeout=timeout
            )
            response.raise_for_status()
            return response
            
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(5 * (attempt + 1))  # Exponential backoff on error
    
    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts")


def print_block(text: str) -> None:
    """Print multi-line text without interleaving across threads."""
    with _print_lock:
        print(text, flush=True)

@functools.cache
def json_load_cache(filename: str) -> Dict:
    """Load geocode cache from JSON file."""
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)
