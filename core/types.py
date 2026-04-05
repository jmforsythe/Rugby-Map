"""Shared type definitions for the mapping data pipeline."""

import functools
import json
import re
from typing import NotRequired, TypedDict

# Stage 1: League scraping


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


# Stage 2: Address fetching


class AddressTeam(Team):
    """Team with fetched address (extends Team)"""

    address: str | None


class AddressLeague(TypedDict):
    """League data with team addresses (team_addresses/*.json)"""

    league_name: str
    league_url: str
    teams: list[AddressTeam]
    team_count: int


# Stage 3: Geocoding


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


# Fixture scraping


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


# Stage 4: Map generation


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


# Utility functions tied to types


@functools.cache
def json_load_cache(filename: str) -> dict:
    """Load and cache a JSON file."""
    with open(filename, encoding="utf-8") as f:
        return json.load(f)


def sanitize_team_name(team_name: str) -> str:
    """Convert team name to URL-safe format."""
    sanitized = team_name.replace(" ", "_").replace("/", "_").replace("&", "and").replace("|", "_")
    sanitized = re.sub(r"[\s_-]+", "_", sanitized)
    return sanitized.strip("_")


def team_name_to_filepath(team_name: str) -> str:
    """Convert team name to corresponding HTML filename."""
    return sanitize_team_name(team_name) + ".html"
