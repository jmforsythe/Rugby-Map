"""
Fetch canonical lat/lng from RFU team pages (Static Maps marker URL) and apply
them across committed ``geocoded_teams`` JSON.

Caches RFU lookups in ``data/caches/rfu_club_coords_cache.json`` only. The
original Nominatim cache (``data/caches/geocode_cache.json``) is intentionally
left untouched: that file stores Nominatim's address->coord answers and should
not be polluted with RFU-sourced pins, which can disagree with the geocoded
address.

Uses ``rugby.addresses`` for RFU HTTP pacing, GET + curl fallback, anti-bot
backoff (``handle_rfu_antibot_with_backoff``), and ``team_name_to_club_name``.

Usage:
    python -m rugby.sync_rfu_coordinates
    python -m rugby.sync_rfu_coordinates --dry-run
    python -m rugby.sync_rfu_coordinates --limit 5
    python -m rugby.sync_rfu_coordinates --skip-fetch
    python -m rugby.sync_rfu_coordinates --coords-json ./rfu_export.json

After repairing Wikipedia-era ``team=`` ids in geocoded JSON, refetch RFU map pins
for affected clubs (and allow large lat/lng corrections)::

    python -m rugby.sync_rfu_coordinates --invalidate-wikipedia-cache
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from core import AntiBotDetectedError, print_block
from core.config import CACHE_DIR
from rugby import DATA_DIR
from rugby.addresses import (
    get_rfu_team_page_response,
    handle_rfu_antibot_with_backoff,
    sleep_before_rfu_request,
    team_name_to_club_name,
)

GEOCODED_ROOT = DATA_DIR / "geocoded_teams"
RFU_COORD_CACHE_FILE = CACHE_DIR / "rfu_club_coords_cache.json"
RFU_CACHE_SAVE_EVERY = 5


def _is_wikipedia_league_url(league_url: object) -> bool:
    return isinstance(league_url, str) and "wikipedia.org" in league_url.lower()


def clubs_touching_wikipedia_geocoded_leagues() -> set[str]:
    """Normalized club names appearing under a Wikipedia ``league_url`` in geocoded_teams."""
    clubs: set[str] = set()
    for path in sorted(GEOCODED_ROOT.rglob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        if not _is_wikipedia_league_url(data.get("league_url")):
            continue
        teams = data.get("teams")
        if not isinstance(teams, list):
            continue
        for team in teams:
            if not isinstance(team, dict):
                continue
            team_name = team.get("name") or ""
            if not isinstance(team_name, str) or not team_name.strip():
                continue
            club = team_name_to_club_name(team_name)
            if club.strip():
                clubs.add(club)
    return clubs


MAP_MARKER_COORD_RE = re.compile(
    r"Map-Pointer\.svg\|(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Rough bounding box: GB, IoM, Channel Islands (covers RFU scope).
_LAT_MIN, _LAT_MAX = 49.0, 61.5
_LON_MIN, _LON_MAX = -11.0, 2.5

# Reject RFU marker if it disagrees with existing coords by more than this (bad HTML/parsing).
_MAX_DELTA_KM = 250.0


def extract_team_id(team_url: str) -> str | None:
    """Parse numeric RFU team id from search-results URL."""
    queries = parse_qs(urlparse(team_url).query)
    raw = queries.get("team", [None])[0]
    return raw


def canonical_team_page_url(team_id: str) -> str:
    return "https://www.englandrugby.com/fixtures-and-results/search-results" f"?team={team_id}"


def extract_coordinates_from_rfu_html(html: str) -> tuple[float, float] | None:
    """Return (lat, lon) from the embedded Static Maps marker fragment."""
    normalized = html.replace("&amp;", "&")
    match = MAP_MARKER_COORD_RE.search(normalized)
    if match:
        return float(match.group(1)), float(match.group(2))

    soup = BeautifulSoup(normalized, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src")
        if not isinstance(src, str):
            continue
        match = MAP_MARKER_COORD_RE.search(src.replace("&amp;", "&"))
        if match:
            return float(match.group(1)), float(match.group(2))
    return None


def plausible_coords(lat: float, lon: float) -> bool:
    return _LAT_MIN <= lat <= _LAT_MAX and _LON_MIN <= lon <= _LON_MAX


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat, dlon = rlat2 - rlat1, rlon2 - rlon1
    h = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def fetch_rfu_coordinates(
    team_id: str,
    *,
    club_name: str | None = None,
    delay_seconds: float = 1.0,
    max_retries: int = 3,
    timeout: int = 10,
) -> tuple[tuple[float, float] | None, str]:
    """GET RFU team page using the same retry pattern as ``fetch_club_address``.

    Cloudflare / anti-bot responses use exponential backoff via
    ``handle_rfu_antibot_with_backoff`` before giving up. After a successful HTTP
    response, if Static Maps coordinates are missing we ``break`` without
    further HTTP retries (same idea as when both address extraction paths fail
    on one response in addresses.py).
    """
    url = canonical_team_page_url(team_id)
    label = club_name if club_name else f"team_id={team_id}"
    log_lines: list[str] = [f"  Fetching RFU coords: {label}", f"    URL: {url}"]

    for attempt in range(max_retries):
        try:
            sleep_before_rfu_request(delay_seconds)

            response = get_rfu_team_page_response(url, timeout=timeout)
            if handle_rfu_antibot_with_backoff(response, log_lines, attempt, max_retries):
                continue

            response.raise_for_status()

            coords = extract_coordinates_from_rfu_html(response.text)
            if coords is not None and plausible_coords(coords[0], coords[1]):
                log_lines.append(f"    ✓ Coordinates: {coords[0]}, {coords[1]}")
                return coords, "\n".join(log_lines)

            log_lines.append("    ! No Static Maps coordinates found on page")
            break

        except AntiBotDetectedError:
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                log_lines.append(f"    ! Attempt {attempt + 1} failed: {e} - retrying...")
                time.sleep(1.0 * (attempt + 1))
            else:
                log_lines.append(f"    ✗ All {max_retries} attempts failed: {e}")

    log_lines.append("    ✗ No coordinates found using RFU page")
    return None, "\n".join(log_lines)


def apply_coords_to_team(
    team: dict[str, Any],
    canonical: tuple[float, float],
    *,
    allow_large_coordinate_jump: bool = False,
) -> bool:
    """Mutate team dict if canonical coords differ enough from stored coords."""
    lat_c, lon_c = canonical
    lat_old = team.get("latitude")
    lon_old = team.get("longitude")

    if (
        not allow_large_coordinate_jump
        and isinstance(lat_old, int | float)
        and isinstance(lon_old, int | float)
    ):
        delta_km = haversine_km(lat_old, lon_old, lat_c, lon_c)
        if delta_km > _MAX_DELTA_KM:
            print(
                f"  Skip sanity (delta {delta_km:.1f}km): {team.get('name')} "
                f"team_id={extract_team_id(team.get('url', '') or '')}"
            )
            return False

    changed = (
        not isinstance(lat_old, int | float)
        or not isinstance(lon_old, int | float)
        or abs(lat_old - lat_c) > 1e-6
        or abs(lon_old - lon_c) > 1e-6
    )
    if not changed:
        return False

    team["latitude"] = lat_c
    team["longitude"] = lon_c
    team.pop("error", None)
    return True


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON via tmp+replace; fall back to direct write if Windows blocks rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    try:
        tmp.replace(path)
    except PermissionError:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def save_rfu_coord_cache(path: Path, data: dict[str, list[float] | None]) -> None:
    _atomic_write_json(path, data)


def load_rfu_coord_cache(path: Path) -> dict[str, list[float] | None]:
    """Load club-name keyed cache: ``{ "Club Name": [lat, lon] | null }``."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, list[float] | None] = {}
    for club_key, val in raw.items():
        key = str(club_key)
        if val is None:
            out[key] = None
        elif isinstance(val, list) and len(val) == 2:
            out[key] = [float(val[0]), float(val[1])]
    return out


def discover_clubs_and_rep_team_ids() -> tuple[dict[str, str], int]:
    """Map club name -> one RFU ``team=`` id (min numeric id) for HTTP fetch."""
    club_ids: dict[str, set[str]] = {}
    skipped = 0

    for path in sorted(GEOCODED_ROOT.rglob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            skipped += 1
            continue
        for team in data.get("teams", []):
            team_name = team.get("name") or ""
            if isinstance(team_name, str) and (
                team_name.startswith("To be arranged") or team_name.startswith("TBC")
            ):
                continue
            url = team.get("url") or ""
            if "englandrugby.com" not in url:
                skipped += 1
                continue
            tid = extract_team_id(url)
            if not tid:
                skipped += 1
                continue
            if tid.isdigit() and int(tid) <= 0:
                skipped += 1
                continue
            club = team_name_to_club_name(team_name if isinstance(team_name, str) else "")
            if not club.strip():
                skipped += 1
                continue
            club_ids.setdefault(club, set()).add(tid)

    def _min_tid(ids_set: set[str]) -> str:
        return min(ids_set, key=lambda x: int(x) if x.isdigit() else 0)

    club_to_rep: dict[str, str] = {club: _min_tid(ids_set) for club, ids_set in club_ids.items()}
    return club_to_rep, skipped


def _reconfigure_stdio_utf8() -> None:
    """Make stdout/stderr tolerant of unicode (e.g. ``\u2713``) on Windows cp1252."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _reconfigure_stdio_utf8()

    parser = argparse.ArgumentParser(
        description="Sync RFU Static Map coordinates into geocoded data"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + report only; do not write league JSON or RFU coord cache",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only fetch / apply for first N clubs (order: ascending representative RFU team id)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between RFU requests before jitter (default: 1; same idea as rugby.addresses)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Max retries per club fetch on transient errors (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="HTTP timeout seconds (default: 10, matches rugby.addresses)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore saved RFU coord cache (start empty for this run)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Re-fetch clubs that previously returned no usable coordinates",
    )
    parser.add_argument(
        "--invalidate-wikipedia-cache",
        action="store_true",
        help=(
            "Drop ``rfu_club_coords_cache.json`` entries for clubs that appear in "
            "geocoded_teams leagues with a Wikipedia ``league_url``, then refetch "
            f"those clubs (for corrected ``team=`` ids). Also allows >{int(_MAX_DELTA_KM)}km coordinate "
            "changes when applying."
        ),
    )
    parser.add_argument(
        "--coords-json",
        type=Path,
        default=None,
        metavar="FILE",
        help="Merge club_name -> [lat, lon] map from FILE before HTTP",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Never call RFU HTTP; local ``rfu_club_coords_cache.json`` (+ --coords-json)",
    )
    args = parser.parse_args()
    if args.invalidate_wikipedia_cache and args.skip_fetch:
        parser.error(
            "--invalidate-wikipedia-cache needs RFU HTTP fetches (do not combine with --skip-fetch)"
        )

    club_to_rep, parse_skips = discover_clubs_and_rep_team_ids()
    club_names_ordered = sorted(club_to_rep.keys(), key=lambda c: int(club_to_rep[c]))
    if args.limit is not None:
        club_names_ordered = club_names_ordered[: max(0, args.limit)]

    print(
        f"Unique clubs (one RFU fetch each): {len(club_names_ordered)} "
        f"(parse skips/non-club rows: {parse_skips})"
    )

    rfu_cache: dict[str, list[float] | None] = {}
    if not args.fresh:
        rfu_cache = load_rfu_coord_cache(RFU_COORD_CACHE_FILE)

    if args.coords_json is not None:
        injected = load_rfu_coord_cache(args.coords_json)
        rfu_cache.update(injected)
        print(f"Merged {len(injected)} RFU coord entries from {args.coords_json}")

    allow_large_jump = bool(args.fresh or args.invalidate_wikipedia_cache)
    if args.invalidate_wikipedia_cache:
        wiki_clubs = clubs_touching_wikipedia_geocoded_leagues()
        removed = 0
        for club in wiki_clubs:
            if club in rfu_cache:
                removed += 1
            rfu_cache.pop(club, None)
        print(
            f"Invalidated RFU coord cache for {removed}/{len(wiki_clubs)} "
            "clubs tied to Wikipedia leagues (pending HTTP refetch)."
        )

    pending: list[str] = []
    for club in club_names_ordered:
        if club not in rfu_cache or (args.retry_failed and rfu_cache.get(club) is None):
            pending.append(club)

    if pending and args.skip_fetch:
        print("Skipping RFU HTTP (--skip-fetch).")
        pending = []
    elif pending:
        print(
            f"RFU HTTP fetch pending: {len(pending)} clubs "
            f"({len(rfu_cache)} entries already in local RFU club cache)"
        )

    try:
        for idx, club in enumerate(pending, start=1):
            rep_tid = club_to_rep[club]
            coords, log_text = fetch_rfu_coordinates(
                rep_tid,
                club_name=club,
                delay_seconds=args.delay,
                max_retries=args.retries,
                timeout=args.timeout,
            )
            print_block(log_text)

            rfu_cache[club] = [coords[0], coords[1]] if coords else None

            if not args.dry_run and (idx % RFU_CACHE_SAVE_EVERY == 0 or idx == len(pending)):
                save_rfu_coord_cache(RFU_COORD_CACHE_FILE, rfu_cache)

            if idx % 100 == 0 or idx == len(pending) or idx <= 5:
                ok = sum(1 for v in rfu_cache.values() if v is not None)
                print(
                    f"  Fetched {idx}/{len(pending)} (RFU cache: {ok} ok / "
                    f"{len(rfu_cache)} total)"
                )

    except AntiBotDetectedError as e:
        if not args.dry_run:
            save_rfu_coord_cache(RFU_COORD_CACHE_FILE, rfu_cache)
        print(getattr(e, "log_text", None) or str(e))
        print(
            "Anti-bot triggered."
            + (" RFU coord cache saved." if not args.dry_run else "")
            + " Wait several minutes and re-run the same command to resume."
        )
        raise SystemExit(1) from None

    if not args.dry_run:
        save_rfu_coord_cache(RFU_COORD_CACHE_FILE, rfu_cache)

    club_coords: dict[str, tuple[float, float]] = {}
    clubs_failed: list[str] = []
    for club in club_names_ordered:
        row = rfu_cache.get(club)
        if isinstance(row, list) and len(row) == 2:
            club_coords[club] = (float(row[0]), float(row[1]))
        else:
            clubs_failed.append(club)

    print(f"Resolved coordinates for {len(club_coords)}/{len(club_names_ordered)} clubs")
    print(f"No usable coords for {len(clubs_failed)} clubs")

    files_touched = 0
    teams_updated = 0

    for path in sorted(GEOCODED_ROOT.rglob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        dirty = False
        teams = data.get("teams")
        if not isinstance(teams, list):
            continue

        for team in teams:
            if not isinstance(team, dict):
                continue
            team_name = team.get("name") or ""
            if isinstance(team_name, str) and (
                team_name.startswith("To be arranged") or team_name.startswith("TBC")
            ):
                continue
            url = team.get("url") or ""
            if "englandrugby.com" not in url:
                continue
            club = team_name_to_club_name(team_name if isinstance(team_name, str) else "")
            if not club.strip() or club not in club_coords:
                continue
            canonical = club_coords[club]
            if not apply_coords_to_team(
                team,
                canonical,
                allow_large_coordinate_jump=allow_large_jump,
            ):
                continue

            dirty = True
            teams_updated += 1

        if dirty:
            files_touched += 1
            if not args.dry_run:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Files with team updates: {files_touched}; teams updated: {teams_updated}")

    if args.dry_run:
        print("Dry run: no files written.")
        return


if __name__ == "__main__":
    main()
