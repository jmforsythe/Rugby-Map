#!/usr/bin/env python3
"""Build the normalized club_names.json / club_addresses.json / club_geocodes.json
from the existing committed geocoded_teams/ tree.

geocoded_teams/<season>/**/*.json already has, for every team ever seen, a
resolved address and (usually) a geocode — but duplicated across every
season and league file a club has appeared in. This script consolidates
that into three club-keyed files under data/rugby/:

- club_names.json:     derived club/team name -> canonical RFU club name
                        (data/caches/club_canonical_name_cache.json,
                        promoted; falls back to the derived name itself for
                        the ~45% of clubs with no scraped canonical name yet)
- club_addresses.json: canonical club name -> address string | null
- club_geocodes.json:  canonical club name -> {latitude, longitude,
                        formatted_address, place_id}

For each derived club name, the latest season with a non-null value wins
(consistent with rugby/custom_map.py's existing "most recent season"
precedence). Where multiple derived names collapse onto one canonical name
(e.g. "Tamworth" and "Tamworth 3rds" both -> "Tamworth RUFC") and they
disagree, the latest-season value is chosen and the disagreement is written
to data/rugby/_club_migration_conflicts.json for manual review rather than
silently picked.

Usage:
    python scripts/migrate_geocoded_to_club_maps.py
    python scripts/migrate_geocoded_to_club_maps.py --geocoded-root data/rugby/geocoded_teams --out-dir data/rugby
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import CACHE_DIR  # noqa: E402
from rugby import DATA_DIR  # noqa: E402
from rugby.addresses import team_name_to_club_name  # noqa: E402

CANONICAL_NAME_CACHE_FILE = CACHE_DIR / "club_canonical_name_cache.json"

GEOCODE_FIELDS = ("latitude", "longitude", "formatted_address", "place_id")


def _season_dirs(geocoded_root: Path) -> list[Path]:
    return sorted((p for p in geocoded_root.iterdir() if p.is_dir()), key=lambda p: p.name)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def collect_latest_by_derived_name(
    geocoded_root: Path,
) -> tuple[dict[str, tuple[str, str | None]], dict[str, tuple[str, dict]]]:
    """Walk geocoded_teams/<season>/**/*.json in season order.

    Returns (addresses, geocodes), each mapping derived club name ->
    (source_season, value), keeping only the latest season with a non-null
    value for that derived name.
    """
    addresses: dict[str, tuple[str, str | None]] = {}
    geocodes: dict[str, tuple[str, dict]] = {}

    for season_dir in _season_dirs(geocoded_root):
        season = season_dir.name
        for league_file in sorted(season_dir.rglob("*.json")):
            if league_file.name.startswith("_"):
                continue
            league = _load_json(league_file)
            for team in league.get("teams", []):
                name = team.get("name", "")
                if name.startswith("To be arranged") or name.startswith("TBC"):
                    continue
                derived = team_name_to_club_name(name)

                address = team.get("address")
                if address:
                    addresses[derived] = (season, address)

                if "latitude" in team and "longitude" in team:
                    geocodes[derived] = (season, {k: team[k] for k in GEOCODE_FIELDS if k in team})

    return addresses, geocodes


def build_canonical_maps(
    canonical_name_cache: dict[str, str],
    addresses_by_derived: dict[str, tuple[str, str | None]],
    geocodes_by_derived: dict[str, tuple[str, dict]],
) -> tuple[dict[str, str], dict[str, str | None], dict[str, dict], list[dict]]:
    """Consolidate derived-name-keyed data onto canonical club names.

    Returns (club_names, club_addresses, club_geocodes, conflicts).
    """
    all_derived = set(addresses_by_derived) | set(geocodes_by_derived) | set(canonical_name_cache)
    club_names = {derived: canonical_name_cache.get(derived, derived) for derived in all_derived}

    groups: dict[str, list[str]] = {}
    for derived, canonical in club_names.items():
        groups.setdefault(canonical, []).append(derived)

    club_addresses: dict[str, str | None] = {}
    club_geocodes: dict[str, dict] = {}
    conflicts: list[dict] = []

    for canonical, derived_members in sorted(groups.items()):
        addr_entries = {
            d: addresses_by_derived[d] for d in derived_members if d in addresses_by_derived
        }
        if addr_entries:
            best_derived = max(addr_entries, key=lambda d: addr_entries[d][0])
            club_addresses[canonical] = addr_entries[best_derived][1]
            distinct_values = {v for _season, v in addr_entries.values()}
            if len(distinct_values) > 1:
                conflicts.append(
                    {
                        "canonical": canonical,
                        "field": "address",
                        "members": {
                            d: {"season": s, "value": v} for d, (s, v) in addr_entries.items()
                        },
                        "chosen": club_addresses[canonical],
                    }
                )
        else:
            club_addresses[canonical] = None

        geo_entries = {
            d: geocodes_by_derived[d] for d in derived_members if d in geocodes_by_derived
        }
        if geo_entries:
            best_derived = max(geo_entries, key=lambda d: geo_entries[d][0])
            club_geocodes[canonical] = geo_entries[best_derived][1]
            distinct_coords = {
                (round(v["latitude"], 6), round(v["longitude"], 6))
                for _season, v in geo_entries.values()
            }
            if len(distinct_coords) > 1:
                conflicts.append(
                    {
                        "canonical": canonical,
                        "field": "geocode",
                        "members": {
                            d: {"season": s, "value": v} for d, (s, v) in geo_entries.items()
                        },
                        "chosen": club_geocodes[canonical],
                    }
                )

    return club_names, club_addresses, club_geocodes, conflicts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build normalized club_names/club_addresses/club_geocodes.json from geocoded_teams/"
    )
    parser.add_argument("--geocoded-root", type=Path, default=DATA_DIR / "geocoded_teams")
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--canonical-name-cache", type=Path, default=CANONICAL_NAME_CACHE_FILE)
    args = parser.parse_args()

    if not args.geocoded_root.exists():
        print(f"Error: {args.geocoded_root} not found")
        return

    canonical_name_cache = _load_json(args.canonical_name_cache)
    print(f"Loaded {len(canonical_name_cache)} scraped canonical club names")

    print(f"Scanning {args.geocoded_root} ...")
    addresses_by_derived, geocodes_by_derived = collect_latest_by_derived_name(args.geocoded_root)
    print(f"Found {len(addresses_by_derived)} derived club names with an address")
    print(f"Found {len(geocodes_by_derived)} derived club names with a geocode")

    club_names, club_addresses, club_geocodes, conflicts = build_canonical_maps(
        canonical_name_cache, addresses_by_derived, geocodes_by_derived
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)

    with open(args.out_dir / "club_names.json", "w", encoding="utf-8") as f:
        json.dump(club_names, f, indent=2, ensure_ascii=False, sort_keys=True)
    with open(args.out_dir / "club_addresses.json", "w", encoding="utf-8") as f:
        json.dump(club_addresses, f, indent=2, ensure_ascii=False, sort_keys=True)
    with open(args.out_dir / "club_geocodes.json", "w", encoding="utf-8") as f:
        json.dump(club_geocodes, f, indent=2, ensure_ascii=False, sort_keys=True)

    conflicts_path = args.out_dir / "_club_migration_conflicts.json"
    with open(conflicts_path, "w", encoding="utf-8") as f:
        json.dump(conflicts, f, indent=2, ensure_ascii=False)

    print(f"{'='*80}")
    print(f"Wrote {len(club_names)} club_names entries -> {args.out_dir / 'club_names.json'}")
    print(
        f"Wrote {len(club_addresses)} club_addresses entries -> {args.out_dir / 'club_addresses.json'}"
    )
    print(
        f"Wrote {len(club_geocodes)} club_geocodes entries -> {args.out_dir / 'club_geocodes.json'}"
    )
    print(f"Wrote {len(conflicts)} conflicts -> {conflicts_path}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
