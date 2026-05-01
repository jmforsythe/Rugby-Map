"""Find geocoded teams whose coordinates don't fall in any LAD and/or ward.

For a given season this loads the ITL hierarchy used by the map pipeline,
runs ``preassign_itl_regions`` over every geocoded team, and reports the ones
whose ``lad`` or ``ward`` field came back empty.

Usage::

    python -m rugby.analysis.unmapped_teams --season 2025-2026
    python -m rugby.analysis.unmapped_teams --all-seasons
    python -m rugby.analysis.unmapped_teams --missing ward    # only ward-misses
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from dataclasses import dataclass

from core import setup_logging
from core.map_builder import ITLHierarchy, MarkerItem, load_itl_hierarchy, preassign_itl_regions
from rugby import DATA_DIR
from rugby.maps import BOUNDARY_PATHS

logger = logging.getLogger(__name__)

GEOCODED_DIR = DATA_DIR / "geocoded_teams"


@dataclass
class TeamRecord:
    """Result row for a single team that failed LAD/ward assignment."""

    season: str
    league: str
    team: str
    latitude: float
    longitude: float
    rel_path: str
    itl0: str | None
    itl1: str | None
    itl2: str | None
    itl3: str | None
    lad: str | None
    ward: str | None


def _collect_marker_items(season: str) -> list[tuple[MarkerItem, str, str]]:
    """Build (item, league_name, rel_path) tuples for every team in *season*.

    Uses MarkerItem so we can call the same ``preassign_itl_regions`` the map
    pipeline uses, ensuring assignments match exactly.
    """
    season_dir = GEOCODED_DIR / season
    out: list[tuple[MarkerItem, str, str]] = []
    if not season_dir.is_dir():
        return out

    for filepath in sorted(season_dir.rglob("*.json")):
        rel_path = filepath.relative_to(season_dir).as_posix()
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        league_name = data.get("league_name", filepath.stem)
        for team in data.get("teams", []):
            if "latitude" not in team or "longitude" not in team:
                continue
            item = MarkerItem(
                name=team["name"],
                latitude=team["latitude"],
                longitude=team["longitude"],
                group=league_name,
                tier="",
                tier_num=0,
            )
            out.append((item, league_name, rel_path))
    return out


def find_unmapped(
    season: str,
    itl_hierarchy: ITLHierarchy,
    missing: str,
) -> list[TeamRecord]:
    """Return teams in *season* missing a LAD and/or ward assignment.

    *missing* must be one of ``"lad"``, ``"ward"`` or ``"any"``.
    """
    triples = _collect_marker_items(season)
    if not triples:
        return []

    items = [t[0] for t in triples]
    preassign_itl_regions(items, itl_hierarchy)

    records: list[TeamRecord] = []
    for item, league, rel_path in triples:
        no_lad = item.lad is None
        no_ward = item.ward is None
        if missing == "lad" and not no_lad:
            continue
        if missing == "ward" and not no_ward:
            continue
        if missing == "any" and not (no_lad or no_ward):
            continue
        records.append(
            TeamRecord(
                season=season,
                league=league,
                team=item.name,
                latitude=item.latitude,
                longitude=item.longitude,
                rel_path=rel_path,
                itl0=item.itl0,
                itl1=item.itl1,
                itl2=item.itl2,
                itl3=item.itl3,
                lad=item.lad,
                ward=item.ward,
            )
        )
    return records


def _print_records(records: list[TeamRecord], lad_name_by_code: dict[str, str]) -> None:
    """Print the report grouped by season, with one row per unique team location."""
    if not records:
        print("  All teams mapped to a LAD and a ward.")
        return

    by_season: dict[str, list[TeamRecord]] = defaultdict(list)
    for r in records:
        by_season[r.season].append(r)

    for season in sorted(by_season):
        rows = by_season[season]
        seen: set[tuple[str, float, float]] = set()
        unique: list[TeamRecord] = []
        for r in rows:
            key = (r.team, r.latitude, r.longitude)
            if key in seen:
                continue
            seen.add(key)
            unique.append(r)

        no_lad = sum(1 for r in unique if r.lad is None)
        no_ward_only = sum(1 for r in unique if r.lad is not None and r.ward is None)

        print(f"\n{'=' * 80}")
        print(f"  {season}: {len(unique)} unique team(s) unmapped")
        print(f"  no LAD: {no_lad} | LAD but no ward: {no_ward_only}")
        print(f"{'=' * 80}")

        unique.sort(key=lambda r: (r.lad is not None, r.itl0 or "", r.team))
        for r in unique:
            lad_label = lad_name_by_code.get(r.lad, r.lad) if r.lad else "<none>"
            ward_label = r.ward if r.ward else "<none>"
            location = (
                " / ".join(p for p in (r.itl0, r.itl1, r.itl2, r.itl3) if p) or "<no ITL match>"
            )
            print(f"  {r.team}  ({r.latitude:.5f}, {r.longitude:.5f})")
            print(f"    league : {r.league}")
            print(f"    region : {location}")
            print(f"    LAD    : {lad_label}")
            print(f"    ward   : {ward_label}")
            print(f"    file   : {r.rel_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find geocoded teams that aren't mapped to any LAD or ward.",
    )
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to inspect (default: 2025-2026). Ignored when --all-seasons is set.",
    )
    parser.add_argument(
        "--all-seasons",
        action="store_true",
        help="Inspect every season under data/rugby/geocoded_teams/.",
    )
    parser.add_argument(
        "--missing",
        choices=("lad", "ward", "any"),
        default="any",
        help=(
            "Which assignment failure to report: "
            "'lad' = no LAD, 'ward' = no ward, 'any' = either (default)."
        ),
    )
    args = parser.parse_args()

    setup_logging()

    if args.all_seasons:
        seasons = sorted(d.name for d in GEOCODED_DIR.iterdir() if d.is_dir() and "-" in d.name)
    else:
        seasons = [args.season]

    logger.info("Loading ITL hierarchy (this can take ~30s)...")
    itl_hierarchy = load_itl_hierarchy(BOUNDARY_PATHS)
    lad_name_by_code = {code: r["name"] for code, r in itl_hierarchy["lad_regions"].items()}

    all_records: list[TeamRecord] = []
    for season in seasons:
        season_path = GEOCODED_DIR / season
        if not season_path.is_dir():
            logger.warning("No geocoded data for season %s, skipping", season)
            continue
        logger.info("Checking season %s ...", season)
        all_records.extend(find_unmapped(season, itl_hierarchy, args.missing))

    _print_records(all_records, lad_name_by_code)

    total_unique = len({(r.season, r.team, r.latitude, r.longitude) for r in all_records})
    print(f"\nTotal unique unmapped teams across {len(seasons)} season(s): {total_unique}")


if __name__ == "__main__":
    main()
