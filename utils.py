"""
Shared type definitions and utility functions for the rugby mapping data pipeline.

Data flows through the following stages:
1. scrape_leagues_teams.py -> league_data/*.json (League)
2. fetch_addresses.py -> team_addresses/*.json (TeamAddressData)
3. geocode_addresses.py -> geocoded_teams/*.json (GeocodedLeague)
4. make_tier_maps.py -> tier_maps/*.html

Fixture pipeline (parallel to main pipeline):
- scrape_fixtures.py -> fixture_data/*.json (FixtureLeague)
- make_match_day_map.py -> tier_maps/match_day/*.html
"""

import functools
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import NotRequired, TypedDict

import requests

logger = logging.getLogger(__name__)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for the pipeline."""
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )


@dataclass
class AppConfig:
    """Shared configuration for the mapping pipeline."""

    is_production: bool = False
    season: str = "2025-2026"
    show_debug: bool = True


_config = AppConfig()


def get_config() -> AppConfig:
    """Return the global application config."""
    return _config


def set_config(
    *, is_production: bool = False, season: str = "2025-2026", show_debug: bool = True
) -> None:
    """Set global application config values."""
    _config.is_production = is_production
    _config.season = season
    _config.show_debug = show_debug


# ============================================================================
# Type Definitions
# ============================================================================

# Stage 1: League scraping (scrape_leagues_teams.py)


class Team(TypedDict):
    """Team data from RFU league tables"""

    name: str
    url: str
    image_url: str | None


class LeagueInfo(TypedDict):
    """League metadata for scraping"""

    name: str
    url: str
    parent_url: str


class League(TypedDict):
    """Complete league data with teams (league_data/*.json)"""

    league_name: str
    league_url: str
    teams: list[Team]
    team_count: int


# Stage 2: Address fetching (fetch_addresses.py)


class AddressTeam(Team):
    """Team with fetched address (extends Team)"""

    address: str | None


class AddressLeague(TypedDict):
    """League data with team addresses (team_addresses/*.json)"""

    league_name: str
    league_url: str
    teams: list[AddressTeam]
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
    teams: list[GeocodedTeam]
    team_count: int


# Fixture scraping (scrape_fixtures.py)


class Fixture(TypedDict):
    """A single scheduled match between two teams."""

    date: str  # ISO format "YYYY-MM-DD"
    time: str  # "HH:MM"
    home_team_id: int
    away_team_id: int
    match_url: str  # match-centre-community URL


class FixtureLeague(TypedDict):
    """All fixtures for one league/division."""

    league_name: str
    league_url: str
    fixtures: list[Fixture]


# Stage 4: Map generation (make_tier_maps.py)


class MapTeam(GeocodedTeam):
    """Team with ITL region assignments and tier information for mapping"""

    league: str
    league_url: str
    tier: str
    tier_num: int
    itl0: str | None
    itl1: str | None
    itl2: str | None
    itl3: str | None
    lad: str | None
    ward: str | None


class TeamTravelDistances(TypedDict):
    """Travel distance statistics for a team"""

    name: str
    league: str
    total_distance_km: float
    avg_distance_km: float


class LeagueTravelDistances(TypedDict):
    """Travel distance statistics for a league"""

    league_name: str
    avg_distance_km: float
    team_count: int


class TravelDistances(TypedDict):
    """Travel distance statistics for teams and leagues"""

    teams: dict[str, TeamTravelDistances]
    leagues: dict[str, LeagueTravelDistances]
    summary: dict[str, float | int]


# ============================================================================
# Shared Utility Functions
# ============================================================================


class AntiBotDetectedError(Exception):
    """Exception raised when anti-bot detection is triggered."""

    log_text: str | None

    def __init__(self, message: str, *, log_text: str | None = None) -> None:
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


def get_headers(referer: str | None = None) -> dict[str, str]:
    """Get standard headers with optional referer for RFU website requests."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _curl_fallback(url: str, referer: str | None, timeout: int) -> requests.Response:
    """Use curl as a fallback when requests gets a Cloudflare 202 challenge."""
    import subprocess

    cmd = [
        "curl",
        "-s",
        "-w",
        "\n%{http_code}",
        "-H",
        f"User-Agent: {get_headers()['User-Agent']}",
        "-H",
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "--max-time",
        str(timeout),
    ]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    lines = result.stdout.rsplit("\n", 1)
    body = lines[0] if len(lines) > 1 else result.stdout
    status = int(lines[-1]) if len(lines) > 1 and lines[-1].strip().isdigit() else 0

    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode("utf-8")
    resp.encoding = "utf-8"
    return resp


def make_request(
    url: str,
    referer: str | None = None,
    max_retries: int = 3,
    timeout: int = 30,
    delay_seconds: float = 2.0,
) -> requests.Response:
    """
    Make an HTTP GET request with retry logic and exponential backoff.

    Falls back to curl when the requests library receives a Cloudflare 202
    challenge (TLS fingerprint mismatch).

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
                time.sleep(delay_seconds + attempt * 2)

            response = get_session().get(url, headers=get_headers(referer), timeout=timeout)
            if response.status_code == 202:
                response = _curl_fallback(url, referer, timeout)
            if response.status_code in (202, 403):
                raise AntiBotDetectedError(f"{response.status_code} code")
            response.raise_for_status()
            return response

        except requests.exceptions.RequestException:
            if attempt == max_retries - 1:
                raise
            time.sleep(5 * (attempt + 1))

    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts")


def print_block(text: str) -> None:
    """Log multi-line text without interleaving across threads."""
    with _print_lock:
        logger.info(text)


@functools.cache
def json_load_cache(filename: str) -> dict:
    """Load geocode cache from JSON file."""
    with open(filename, encoding="utf-8") as f:
        return json.load(f)


def get_google_analytics_script() -> str:
    """Return Google Analytics script for embedding in HTML pages.

    Uses the GA_TRACKING_ID environment variable. Returns an empty string if not set.
    """
    ga_id = os.environ.get("GA_TRACKING_ID", "")
    if not ga_id:
        return ""
    return f"""
    <!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id={ga_id}"></script>
    <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());

    gtag('config', '{ga_id}');
    </script>
"""


def get_favicon_html(depth: int = 0) -> str:
    """Return <link> tags for favicon and manifest.

    Args:
        depth: directory depth relative to tier_maps/ root (0 = top-level, 1 = season, etc.)
    """
    if get_config().is_production:
        prefix = "/"
    else:
        prefix = "../" * depth if depth > 0 else ""
    return (
        f'    <link rel="icon" href="{prefix}favicon.svg" type="image/svg+xml">\n'
        f'    <link rel="manifest" href="{prefix}manifest.json">'
    )


def sanitize_team_name(team_name: str) -> str:
    """Convert team name to URL-safe format."""
    # Replace special characters with url-safe equivalents
    sanitized = team_name.replace(" ", "_").replace("/", "_").replace("&", "and").replace("|", "_")
    # Replace spaces and multiple hyphens/underscores with single underscore
    sanitized = re.sub(r"[\s_-]+", "_", sanitized)
    return sanitized.strip("_")


def team_name_to_filepath(team_name: str) -> str:
    """Convert team name to corresponding HTML filename."""
    return sanitize_team_name(team_name) + ".html"
