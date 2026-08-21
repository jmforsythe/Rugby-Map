"""Runtime join layer over normalized club data.

Combines the thin per-season ``league_data/`` team lists with club-level
address/geocode data (``data/rugby/club_names.json``, ``club_addresses.json``,
``club_geocodes.json``) to reconstruct the ``GeocodedLeague``/``GeocodedTeam``
shape that ``rugby/addresses.py`` + ``rugby/geocode.py`` used to produce and
commit under ``data/rugby/geocoded_teams/``.

The three normalized files are keyed by *canonical* club name (the RFU
ground-truth name, falling back to the derived ``team_name_to_club_name``
result when no canonical name has been scraped yet — see
``scripts/migrate_geocoded_to_club_maps.py``), so fixing one club's address
or coordinates only ever requires a one-line edit in one file.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from core import GeocodedLeague, GeocodedTeam, GeocodeResult, League, Team, json_load_cache
from rugby import DATA_DIR
from rugby.addresses import team_name_to_club_name
from rugby.sync_rfu_coordinates import (
    RFU_COORD_CACHE_FILE,
    apply_rfu_coords_from_cache,
    load_rfu_coord_cache,
)

CLUB_NAMES_FILE = DATA_DIR / "club_names.json"
CLUB_ADDRESSES_FILE = DATA_DIR / "club_addresses.json"
CLUB_GEOCODES_FILE = DATA_DIR / "club_geocodes.json"


def load_team_club_map(path: Path | None = None) -> dict[str, str]:
    """Derived team/club name -> canonical RFU club name."""
    return json_load_cache(str(path if path is not None else CLUB_NAMES_FILE))


def resolve_club_name(team_name: str, team_club_map: dict[str, str]) -> str:
    """Canonical club name for a team, falling back to the derived name."""
    derived = team_name_to_club_name(team_name)
    return team_club_map.get(derived, derived)


def load_club_addresses(path: Path | None = None) -> dict[str, str | None]:
    """Canonical club name -> scraped address string (or ``None``)."""
    return json_load_cache(str(path if path is not None else CLUB_ADDRESSES_FILE))


def load_club_geocodes(path: Path | None = None) -> dict[str, GeocodeResult]:
    """Canonical club name -> Nominatim geocode result."""
    return json_load_cache(str(path if path is not None else CLUB_GEOCODES_FILE))


def load_rfu_club_coords(path: Path | None = None) -> dict[str, list[float] | None]:
    """Derived club name -> RFU-pinned ``[lat, lon]`` override (or ``None``).

    Kept separate from ``club_geocodes.json`` since RFU pins can deliberately
    disagree with the geocoded address (see ``rugby/sync_rfu_coordinates.py``).
    """
    return load_rfu_coord_cache(path if path is not None else RFU_COORD_CACHE_FILE)


def build_geocoded_team(
    team: Team,
    *,
    team_club_map: dict[str, str],
    club_addresses: dict[str, str | None],
    club_geocodes: dict[str, GeocodeResult],
    rfu_club_coords: dict[str, list[float] | None] | None = None,
) -> GeocodedTeam:
    """Join a single ``league_data`` team record with its club's address/geocode."""
    club = resolve_club_name(team["name"], team_club_map)
    result: GeocodedTeam = dict(team)  # type: ignore[assignment]
    result["address"] = club_addresses.get(club)

    geocode = club_geocodes.get(club)
    if geocode:
        result.update(geocode)  # type: ignore[typeddict-item]

    if rfu_club_coords:
        apply_rfu_coords_from_cache(result, rfu_club_coords)

    return result


def load_geocoded_league(
    league_path: Path,
    *,
    team_club_map: dict[str, str] | None = None,
    club_addresses: dict[str, str | None] | None = None,
    club_geocodes: dict[str, GeocodeResult] | None = None,
    rfu_club_coords: dict[str, list[float] | None] | None = None,
) -> GeocodedLeague:
    """Load a ``league_data/<season>/...`` file and join in club address/geocode data.

    Reconstructs the same ``GeocodedLeague`` shape that used to be committed
    under ``geocoded_teams/<season>/...``, without that directory needing to
    exist on disk. Works unmodified for ``merit/<Competition>/<file>.json``
    paths — the join only depends on ``teams``, not on where the file lives.
    """
    if team_club_map is None:
        team_club_map = load_team_club_map()
    if club_addresses is None:
        club_addresses = load_club_addresses()
    if club_geocodes is None:
        club_geocodes = load_club_geocodes()
    if rfu_club_coords is None:
        rfu_club_coords = load_rfu_club_coords()

    with open(league_path, encoding="utf-8") as f:
        league: League = json.load(f)

    teams = [
        build_geocoded_team(
            team,
            team_club_map=team_club_map,
            club_addresses=club_addresses,
            club_geocodes=club_geocodes,
            rfu_club_coords=rfu_club_coords,
        )
        for team in league["teams"]
        if not team["name"].startswith(("To be arranged", "TBC"))
    ]
    return {
        "league_name": league["league_name"],
        "league_url": league["league_url"],
        "teams": teams,
        "team_count": len(teams),
    }


def iter_geocoded_leagues(
    season_dir: Path,
    *,
    team_club_map: dict[str, str] | None = None,
    club_addresses: dict[str, str | None] | None = None,
    club_geocodes: dict[str, GeocodeResult] | None = None,
    rfu_club_coords: dict[str, list[float] | None] | None = None,
) -> Iterator[tuple[Path, GeocodedLeague]]:
    """Walk a ``league_data/<season>/`` directory, joining each league file.

    Mirrors how consumers used to walk ``geocoded_teams/<season>/`` directly
    (including ``merit/<Competition>/`` nesting), yielding
    ``(path, GeocodedLeague)`` pairs so callers can still recover the
    relative path (e.g. to tell merit files apart from top-level ones).
    """
    team_club_map = team_club_map if team_club_map is not None else load_team_club_map()
    club_addresses = club_addresses if club_addresses is not None else load_club_addresses()
    club_geocodes = club_geocodes if club_geocodes is not None else load_club_geocodes()
    rfu_club_coords = rfu_club_coords if rfu_club_coords is not None else load_rfu_club_coords()

    for path in sorted(p for p in season_dir.rglob("*.json") if not p.name.startswith("_")):
        yield path, load_geocoded_league(
            path,
            team_club_map=team_club_map,
            club_addresses=club_addresses,
            club_geocodes=club_geocodes,
            rfu_club_coords=rfu_club_coords,
        )
