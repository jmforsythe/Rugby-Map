"""Routed (road) distance & duration matrix via self-hosted OSRM.

Walks every ``data/rugby/geocoded_teams/<season>/`` directory (men's pyramid +
merit + women's, every season), dedupes to distinct points (rounded to
``GEOCODE_DECIMALS`` dp), and calls a self-hosted OSRM ``/table`` endpoint in
chunks for the full N x N matrix.

The cache is **global, not per-season**, because club locations are static --
a club's stadium does not move when a season changes. One file covers every
season, current or historical.

Output (single global cache):
    data/rugby/distance_cache/routed/all.npz   -- distance_km, duration_min, lats, lons
    data/rugby/distance_cache/routed/all.json  -- geocode index (id -> teams/leagues)

Unreachable pairs (e.g. cross-sea hops that are missing from OSM ferry data)
may be stored as NaN; callers can fall back via ``rugby.distance_lookup`` (pure mainland
pairs use scaled Haversine for km unless the offshore air-bridge model applies).
(Jersey, Guernsey, Isle of Man, Southampton, Liverpool) are always merged into
the geocode set so segmented crown-dependency travel can reuse OSRM inland legs.

Usage (assumes ``osrm-routed`` running on http://localhost:5000):

    python -m rugby.distances_routed                 # build the global cache
    python -m rugby.distances_routed --chunk 256
    python -m rugby.distances_routed --osrm-url http://localhost:5000
    python -m rugby.distances_routed --ping-only     # smoke-test OSRM only

Setup notes for self-hosted OSRM (one-off, in WSL Ubuntu with Docker):

    mkdir -p ~/osrm && cd ~/osrm
    curl -fSL -o gb.osm.pbf https://download.geofabrik.de/europe/great-britain-latest.osm.pbf
    docker run --rm -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend \
        osrm-extract -p /opt/car.lua /data/gb.osm.pbf
    docker run --rm -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend \
        osrm-partition /data/gb.osrm
    docker run --rm -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend \
        osrm-customize /data/gb.osrm
    docker run --rm -d -p 5000:5000 -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend \
        osrm-routed --algorithm mld --max-table-size 5000 /data/gb.osrm
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core import json_load_cache, setup_logging
from rugby import DATA_DIR
from rugby.offshore_travel import augment_coord_meta_for_routing_waypoints

logger = logging.getLogger(__name__)

GEOCODE_DECIMALS = 6
DEFAULT_OSRM_URL = "http://localhost:5000"
DEFAULT_CHUNK = 256
DEFAULT_TIMEOUT = 600

ROUTED_DIR = DATA_DIR / "distance_cache" / "routed"
CACHE_NAME = "all"  # global cache; club locations are static across seasons
GLOBAL_NPZ = ROUTED_DIR / f"{CACHE_NAME}.npz"
GLOBAL_SIDECAR = ROUTED_DIR / f"{CACHE_NAME}.json"


def _round_coord(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, GEOCODE_DECIMALS), round(lon, GEOCODE_DECIMALS))


def _iter_season_dirs() -> list[Path]:
    """All available season directories under ``geocoded_teams/`` (sorted)."""
    root = DATA_DIR / "geocoded_teams"
    if not root.is_dir():
        return []
    return sorted(d for d in root.iterdir() if d.is_dir())


def collect_geocodes(
    seasons: list[str] | None = None,
) -> tuple[
    list[tuple[float, float]],
    dict[tuple[float, float], list[dict[str, str]]],
    list[str],
]:
    """Return (sorted unique coords, coord -> team metadata, seasons covered).

    Walks every season under ``data/rugby/geocoded_teams/`` (pyramid + merit +
    women's combined; routing is gender/competition-agnostic). If ``seasons`` is
    given, only those subdirs are scanned.

    Club locations are static across seasons, so a single cache built from all
    historical + current seasons covers every map / popup / stats use.
    """
    if seasons is None:
        season_dirs = _iter_season_dirs()
    else:
        season_dirs = [DATA_DIR / "geocoded_teams" / s for s in seasons]
    season_dirs = [d for d in season_dirs if d.is_dir()]
    if not season_dirs:
        raise SystemExit(f"no season directories under {DATA_DIR / 'geocoded_teams'}")

    coord_meta: dict[tuple[float, float], list[dict[str, str]]] = defaultdict(list)
    seen_team_keys: set[tuple[tuple[float, float], str, str]] = set()
    for season_dir in season_dirs:
        for filepath in sorted(season_dir.rglob("*.json")):
            league = json_load_cache(str(filepath))
            league_name = league.get("league_name", "")
            for team in league.get("teams", []):
                lat = team.get("latitude")
                lon = team.get("longitude")
                if lat is None or lon is None:
                    continue
                key = _round_coord(float(lat), float(lon))
                team_name = team.get("name", "")
                meta_key = (key, team_name, league_name)
                if meta_key in seen_team_keys:
                    continue
                seen_team_keys.add(meta_key)
                coord_meta[key].append(
                    {
                        "team": team_name,
                        "league": league_name,
                        "address": team.get("formatted_address", team.get("address", "")),
                    }
                )

    augment_coord_meta_for_routing_waypoints(coord_meta)

    coords = sorted(coord_meta.keys())
    seasons_covered = [d.name for d in season_dirs]
    return coords, dict(coord_meta), seasons_covered


def _make_session() -> requests.Session:
    """Session with automatic retries on transient errors / connection drops."""
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _osrm_table_chunk(
    session: requests.Session,
    base_url: str,
    src_coords: list[tuple[float, float]],
    dst_coords: list[tuple[float, float]],
    timeout: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Single OSRM /table call for sources -> destinations.

    Sends only the sources concatenated with the destinations (no globally
    indexed coordinate list), which keeps URLs short for chunked calls.
    """
    n_src = len(src_coords)
    locs = src_coords + dst_coords
    locs_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in locs)
    url = f"{base_url}/table/v1/driving/{locs_str}"
    params = {
        "annotations": "distance,duration",
        "sources": ";".join(str(i) for i in range(n_src)),
        "destinations": ";".join(str(j) for j in range(n_src, n_src + len(dst_coords))),
    }
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM /table error: {data.get('code')} {data.get('message', '')}")
    distances = data.get("distances")
    durations = data.get("durations")
    if distances is None or durations is None:
        raise RuntimeError("OSRM response missing distance/duration matrices")
    return np.asarray(distances, dtype=np.float32), np.asarray(durations, dtype=np.float32)


def osrm_full_matrix(
    coords: list[tuple[float, float]],
    *,
    base_url: str = DEFAULT_OSRM_URL,
    chunk: int = DEFAULT_CHUNK,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[np.ndarray, np.ndarray]:
    """Build the full N x N (distance_m, duration_s) via chunked /table calls."""
    n = len(coords)
    distance_m = np.full((n, n), np.nan, dtype=np.float32)
    duration_s = np.full((n, n), np.nan, dtype=np.float32)

    n_chunks = (n + chunk - 1) // chunk
    total_calls = n_chunks * n_chunks
    done = 0
    t0 = time.time()
    session = _make_session()

    for i_start in range(0, n, chunk):
        i_end = min(n, i_start + chunk)
        src_coords = coords[i_start:i_end]
        for j_start in range(0, n, chunk):
            j_end = min(n, j_start + chunk)
            dst_coords = coords[j_start:j_end]
            d_arr, t_arr = _osrm_table_chunk(session, base_url, src_coords, dst_coords, timeout)
            distance_m[i_start:i_end, j_start:j_end] = d_arr
            duration_s[i_start:i_end, j_start:j_end] = t_arr
            done += 1
            elapsed = time.time() - t0
            logger.info(
                "OSRM table chunk %d/%d (%.1fs elapsed)",
                done,
                total_calls,
                elapsed,
            )
    return distance_m, duration_s


def build_matrix(
    *,
    base_url: str,
    chunk: int,
    timeout: int,
    seasons: list[str] | None = None,
) -> Path:
    """Build (or rebuild) the global routed matrix from every season's geocodes.

    Pass ``seasons`` to limit collection (e.g. for testing); the cache file
    location is the same global ``all.npz`` regardless.
    """
    coords, coord_meta, seasons_covered = collect_geocodes(seasons)
    n = len(coords)
    logger.info(
        "Global routed cache: %d distinct geocodes across %d seasons (%s..%s)",
        n,
        len(seasons_covered),
        seasons_covered[0] if seasons_covered else "?",
        seasons_covered[-1] if seasons_covered else "?",
    )

    distance_m, duration_s = osrm_full_matrix(
        coords, base_url=base_url, chunk=chunk, timeout=timeout
    )

    distance_km = (distance_m / 1000.0).astype(np.float32)
    duration_min = (duration_s / 60.0).astype(np.float32)

    n_unreachable = int(np.isnan(distance_km).sum())
    if n_unreachable:
        logger.info(
            "Unreachable pairs (NaN): %d / %d (%.2f%%)",
            n_unreachable,
            n * n,
            100.0 * n_unreachable / (n * n),
        )

    ROUTED_DIR.mkdir(parents=True, exist_ok=True)

    lats = np.array([lat for lat, _ in coords], dtype=np.float64)
    lons = np.array([lon for _, lon in coords], dtype=np.float64)

    np.savez_compressed(
        GLOBAL_NPZ,
        distance_km=distance_km,
        duration_min=duration_min,
        lats=lats,
        lons=lons,
    )

    sidecar = {
        "kind": "global",
        "seasons_covered": seasons_covered,
        "generated_at": datetime.now(UTC).isoformat(),
        "geocode_decimals": GEOCODE_DECIMALS,
        "n_geocodes": n,
        "n_unreachable_pairs": n_unreachable,
        "osrm_url": base_url,
        "chunk_size": chunk,
        "geocodes": [
            {
                "id": i,
                "lat": coords[i][0],
                "lon": coords[i][1],
                "teams": coord_meta[coords[i]],
            }
            for i in range(n)
        ],
    }
    GLOBAL_SIDECAR.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Wrote %s (%.1f MB)", GLOBAL_NPZ, GLOBAL_NPZ.stat().st_size / 1024 / 1024)
    logger.info(
        "Wrote %s (%.1f KB)",
        GLOBAL_SIDECAR,
        GLOBAL_SIDECAR.stat().st_size / 1024,
    )
    return GLOBAL_NPZ


def load_matrix() -> tuple[np.ndarray, np.ndarray, list[tuple[float, float]], dict] | None:
    """Load the global routed matrix if present.

    Returns ``(distance_km, duration_min, coords, sidecar)`` or ``None`` if the
    cache has not been built yet.
    """
    if not GLOBAL_NPZ.exists():
        return None
    data = np.load(GLOBAL_NPZ)
    coords = list(zip(data["lats"].tolist(), data["lons"].tolist(), strict=True))
    sidecar: dict = {}
    if GLOBAL_SIDECAR.exists():
        sidecar = json.loads(GLOBAL_SIDECAR.read_text(encoding="utf-8"))
    return data["distance_km"], data["duration_min"], coords, sidecar


def _ping_osrm(base_url: str) -> None:
    """Smoke-test that OSRM is up by routing two GB-mainland points."""
    test_coords = [(51.5074, -0.1278), (52.4862, -1.8904)]
    try:
        d, t = _osrm_table_chunk(_make_session(), base_url, test_coords, test_coords, timeout=30)
    except Exception as exc:  # noqa: BLE001 -- diagnostic
        raise SystemExit(f"OSRM at {base_url} not reachable: {exc}") from exc
    logger.info(
        "OSRM ping ok: London<->Birmingham %.1f km, %.1f min",
        float(d[0, 1]) / 1000.0,
        float(t[0, 1]) / 60.0,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build the global routed distance/duration matrix via OSRM. "
            "Walks every season under data/rugby/geocoded_teams/ by default."
        )
    )
    parser.add_argument(
        "--seasons",
        nargs="+",
        default=None,
        help="Limit collection to these seasons (default: all available).",
    )
    parser.add_argument("--osrm-url", default=DEFAULT_OSRM_URL)
    parser.add_argument("--chunk", type=int, default=DEFAULT_CHUNK)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--ping-only",
        action="store_true",
        help="Verify OSRM is reachable without building the matrix.",
    )
    args = parser.parse_args()

    setup_logging()
    _ping_osrm(args.osrm_url)
    if args.ping_only:
        return
    build_matrix(
        base_url=args.osrm_url,
        chunk=args.chunk,
        timeout=args.timeout,
        seasons=args.seasons,
    )


if __name__ == "__main__":
    main()
