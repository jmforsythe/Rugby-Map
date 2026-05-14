"""Compare coordinates from ``club_address_cache`` + ``geocode_cache`` vs RFU Google coords.

For each club name present in ``data/caches/club_address_cache.json``, looks up the
cached address string in ``data/caches/geocode_cache.json`` (Nominatim pipeline).
Compares resulting latitude/longitude to ``data/caches/rfu_club_coords_cache.json``
when both sides have a coordinate pair.

Run: ``python -m rugby.analysis.compare_address_vs_rfu_coords``

Use ``--list-above-m`` to print clubs whose great-circle distance exceeds a metre
threshold (default lists none; set e.g. ``500`` for half-kilometre outliers). Each
line includes a representative RFU ``team_url`` (from ``geocoded_teams``, same
club dedup as ``rugby.addresses``), the scraped address, Nominatim
``formatted_address``, and both coordinate pairs.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.config import CACHE_DIR
from rugby import DATA_DIR
from rugby.addresses import team_name_to_club_name
from rugby.distances import distance as haversine_km

CLUB_ADDRESS_CACHE = CACHE_DIR / "club_address_cache.json"
GEOCODE_CACHE = CACHE_DIR / "geocode_cache.json"
RFU_CLUB_COORDS_CACHE = CACHE_DIR / "rfu_club_coords_cache.json"
GEOCODED_ROOT = DATA_DIR / "geocoded_teams"


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _nominatim_coords(
    club: str, club_addresses: dict[str, str], geocode: dict[str, object]
) -> tuple[float, float, str | None] | None:
    addr = club_addresses.get(club)
    if not addr or not isinstance(addr, str):
        return None
    hit = geocode.get(addr)
    if not isinstance(hit, dict):
        return None
    lat, lon = hit.get("latitude"), hit.get("longitude")
    if lat is None or lon is None:
        return None
    formatted = hit.get("formatted_address")
    formatted_s = formatted.strip() if isinstance(formatted, str) else None
    return float(lat), float(lon), formatted_s


def _extract_team_id(team_url: str) -> str | None:
    queries = parse_qs(urlparse(team_url).query)
    raw = queries.get("team", [None])[0]
    return raw


def discover_club_rep_team_url() -> dict[str, str]:
    """Map normalized club name -> one ``team`` URL (min numeric RFU team id)."""
    club_tid_url: dict[str, list[tuple[int, str]]] = defaultdict(list)

    for path in sorted(GEOCODED_ROOT.rglob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for team in data.get("teams", []):
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
            tid = _extract_team_id(url)
            if not tid or (tid.isdigit() and int(tid) <= 0):
                continue
            club = team_name_to_club_name(team_name if isinstance(team_name, str) else "")
            if not club.strip():
                continue
            club_tid_url[club].append((int(tid), url))

    return {club: min(pairs, key=lambda p: p[0])[1] for club, pairs in club_tid_url.items()}


def _rfu_ll(entry: object) -> tuple[float, float] | None:
    if not isinstance(entry, list) or len(entry) < 2:
        return None
    try:
        return float(entry[0]), float(entry[1])
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--list-above-m",
        type=float,
        metavar="M",
        default=None,
        help="Print clubs with separation greater than M metres (great-circle).",
    )
    parser.add_argument(
        "--max-list",
        type=int,
        default=50,
        metavar="N",
        help="Cap lines printed by --list-above-m (default 50).",
    )
    args = parser.parse_args()

    club_addresses = _load_json(CLUB_ADDRESS_CACHE)
    geocode = _load_json(GEOCODE_CACHE)
    rfu_coords = _load_json(RFU_CLUB_COORDS_CACHE)
    club_rep_url = discover_club_rep_team_url()

    if not isinstance(club_addresses, dict) or not isinstance(geocode, dict):
        raise SystemExit("club_address_cache or geocode_cache is not a JSON object")

    clubs_with_address = {str(k) for k in club_addresses}
    rfu_clubs = {str(k) for k in rfu_coords}

    address_but_no_geocode_hit = 0
    nominatim_pairs: dict[str, tuple[float, float, str | None]] = {}
    for club in sorted(clubs_with_address):
        resolved = _nominatim_coords(club, club_addresses, geocode)
        if resolved is None:
            addr = club_addresses.get(club)
            if isinstance(addr, str) and addr and addr not in geocode:
                address_but_no_geocode_hit += 1
        else:
            lat_n, lon_n, _formatted = resolved
            nominatim_pairs[club] = (lat_n, lon_n, _formatted)

    both: list[tuple[str, float]] = []
    overlap_rows: list[tuple[str, float, str, str | None, float, float, float, float, str]] = []
    for club, (lat_n, lon_n, formatted) in nominatim_pairs.items():
        rfu_ll = _rfu_ll(rfu_coords.get(club))
        if rfu_ll is None:
            continue
        lat_r, lon_r = rfu_ll
        km = haversine_km(lat_n, lon_n, lat_r, lon_r)
        metres = km * 1000.0
        both.append((club, metres))
        addr_raw = club_addresses.get(club)
        addr_s = addr_raw.strip() if isinstance(addr_raw, str) else ""
        team_url = club_rep_url.get(club, "")
        overlap_rows.append((club, metres, addr_s, formatted, lat_n, lon_n, lat_r, lon_r, team_url))

    nominatim_only = set(nominatim_pairs) - rfu_clubs
    rfu_only = rfu_clubs - set(nominatim_pairs)
    in_club_cache_no_nominatim = clubs_with_address - set(nominatim_pairs)

    metres = [m for _, m in both]

    print(f"club_address_cache clubs:              {len(clubs_with_address)}")
    print(f"rfu_club_coords_cache clubs:           {len(rfu_clubs)}")
    print(f"nominatim coords resolved (via addr):  {len(nominatim_pairs)}")
    print("club has address string missing from geocode_cache: " f"{address_but_no_geocode_hit}")
    print(f"clubs in club cache, no Nominatim ll:  {len(in_club_cache_no_nominatim)}")
    print()
    print(f"both Nominatim + RFU coords:           {len(both)}")
    print(f"nominatim_only (no RFU club key):      {len(nominatim_only)}")
    print(f"rfu_only (no Nominatim-resolved club): {len(rfu_only)}")
    print()

    if metres:
        print(
            "Great-circle distance Nominatim vs RFU (metres), " "for clubs present on both sides:"
        )
        print(f"  min:    {min(metres):.1f}")
        print(f"  median: {statistics.median(metres):.1f}")
        print(f"  mean:   {statistics.mean(metres):.1f}")
        if len(metres) > 1:
            print(f"  stdev:  {statistics.stdev(metres):.1f}")
        print(f"  max:    {max(metres):.1f}")
    else:
        print("No overlapping clubs with coordinates on both sides.")

    if args.list_above_m is not None and overlap_rows:
        thresh = args.list_above_m
        flagged = sorted(
            (row for row in overlap_rows if row[1] > thresh),
            key=lambda r: r[1],
            reverse=True,
        )
        print()
        print(f"Clubs with distance > {thresh:g} m ({len(flagged)} total):")
        for row in flagged[: args.max_list]:
            club, m, addr_s, formatted, lat_n, lon_n, lat_r, lon_r, team_url = row
            print(f"  {m:,.1f} m  {club}")
            print(
                "    team_url:          "
                f"{team_url if team_url else '(not found in geocoded_teams)'}"
            )
            print(f"    address:           {addr_s}")
            print(
                "    formatted_address: " f"{formatted if formatted is not None else '(missing)'}"
            )
            print(f"    nominatim:         {lat_n:.6f}, {lon_n:.6f}")
            print(f"    rfu (Google):      {lat_r:.6f}, {lon_r:.6f}")
        if len(flagged) > args.max_list:
            print(f"  ... and {len(flagged) - args.max_list} more")


if __name__ == "__main__":
    main()
