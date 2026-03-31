"""Diagnostic script to detect merit league tier misplacements.

For each season/competition, compares the merit top tier against the pyramid
bottom tier in the same geographical region. Reports overlaps (gap <= 0),
gaps (gap >= 2), and Counties_N filename mismatches. Suggests ideal offsets.

Usage:
    python check_offsets.py [--season 2025-2026] [--fix]

The --fix flag prints the Python dict literal for season-aware offsets that
can be pasted into tier_extraction.py.
"""

import argparse
import re
from collections import defaultdict
from pathlib import Path

from tier_extraction import (
    _strip_sponsor_prefix,
    extract_tier,
    get_competition_offset,
    mens_current_tier_name,
)

COMP_TO_PYRAMID_PATTERN: dict[str, list[str]] = {
    "CANDY": ["Midlands_West"],
    "Devon": ["Devon"],
    "East_Midlands": ["Midlands_East"],
    "Eastern_Counties": ["Eastern_Counties"],
    "Essex": ["Essex"],
    "GRFU_District": ["Gloucester"],
    "Hampshire": ["Hampshire"],
    "Herts_Middlesex": ["Herts", "Middx"],
    "Leicestershire": ["Midlands_East"],
    "Middlesex": ["Middx"],
    "NOWIRUL": ["Lancashire", "Lancs_Cheshire", "Cumbria", "North_Lancs"],
    "Nottinghamshire": ["Midlands_East"],
    "Rural_Kent": ["Kent"],
    "Surrey": ["Surrey"],
    "Sussex": ["Sussex"],
}

EXCLUDED_COMPETITIONS = {"Midlands_Reserve"}


def _find_pyramid_bottom(season_dir: Path, season: str, comp: str) -> int | None:
    """Return the max pyramid tier number matching this competition's region."""
    patterns = COMP_TO_PYRAMID_PATTERN.get(comp)
    if not patterns:
        return None
    tiers: set[int] = set()
    for f in season_dir.glob("*.json"):
        tier_num, _ = extract_tier(f.name, season)
        if tier_num >= 100 or tier_num == 999:
            continue
        for pat in patterns:
            if pat in f.stem:
                tiers.add(tier_num)
    return max(tiers) if tiers else None


def _find_merit_tiers(season_dir: Path, season: str, comp: str) -> dict[int, list[str]]:
    """Return {abs_tier: [filenames]} for all merit files in a competition."""
    comp_dir = season_dir / "merit" / comp
    if not comp_dir.is_dir():
        return {}
    result: dict[int, list[str]] = defaultdict(list)
    for f in sorted(comp_dir.rglob("*.json")):
        rel = f.relative_to(season_dir).as_posix()
        local_tier, _ = extract_tier(rel, season)
        offset = get_competition_offset(comp, season)
        abs_tier = local_tier + offset
        result[abs_tier].append(f.name)
    return dict(result)


def _check_counties_mismatches(season_dir: Path, season: str, comp: str) -> list[str]:
    """Check merit files with Counties_N in the name for tier mismatches."""
    comp_dir = season_dir / "merit" / comp
    if not comp_dir.is_dir():
        return []
    issues = []
    for f in sorted(comp_dir.rglob("*.json")):
        stripped = _strip_sponsor_prefix(f.name)
        m = re.search(r"Counties_(\d+)", stripped)
        if not m:
            continue
        expected_counties = int(m.group(1))
        expected_abs = expected_counties + 6

        rel = f.relative_to(season_dir).as_posix()
        local_tier, _ = extract_tier(rel, season)
        offset = get_competition_offset(comp, season)
        abs_tier = local_tier + offset

        if abs_tier != expected_abs:
            actual_name = mens_current_tier_name(abs_tier, season)
            issues.append(
                f"  {f.name}: computed tier {abs_tier} ({actual_name}), "
                f"expected tier {expected_abs} (Counties {expected_counties})"
            )
    return issues


def analyse_season(season_dir: Path, season: str, filter_comp: str | None = None) -> list[dict]:
    """Analyse all competitions for a single season."""
    results = []
    merit_dir = season_dir / "merit"
    if not merit_dir.is_dir():
        return results

    for comp_dir in sorted(merit_dir.iterdir()):
        if not comp_dir.is_dir():
            continue
        comp = comp_dir.name
        if comp in EXCLUDED_COMPETITIONS:
            continue
        if filter_comp and comp != filter_comp:
            continue

        pyramid_bottom = _find_pyramid_bottom(season_dir, season, comp)
        if pyramid_bottom is None:
            continue

        merit_tiers = _find_merit_tiers(season_dir, season, comp)
        if not merit_tiers:
            continue

        merit_top = min(merit_tiers)
        merit_bottom = max(merit_tiers)
        gap = merit_top - pyramid_bottom
        current_offset = get_competition_offset(comp, season)
        ideal_offset = current_offset + (1 - gap)

        counties_issues = _check_counties_mismatches(season_dir, season, comp)

        results.append(
            {
                "season": season,
                "comp": comp,
                "pyramid_bottom": pyramid_bottom,
                "merit_top": merit_top,
                "merit_bottom": merit_bottom,
                "gap": gap,
                "current_offset": current_offset,
                "ideal_offset": ideal_offset,
                "counties_issues": counties_issues,
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Check merit competition offsets")
    parser.add_argument("--season", help="Check only this season")
    parser.add_argument("--comp", help="Check only this competition")
    parser.add_argument(
        "--all", action="store_true", help="Show all competitions (including gap=1)"
    )
    args = parser.parse_args()

    geocoded = Path("geocoded_teams")
    all_results: list[dict] = []

    for season_dir in sorted(geocoded.iterdir()):
        if not season_dir.is_dir():
            continue
        season = season_dir.name
        if args.season and season != args.season:
            continue
        all_results.extend(analyse_season(season_dir, season, args.comp))

    problems = [r for r in all_results if r["gap"] != 1]
    ok = [r for r in all_results if r["gap"] == 1]

    if problems:
        print("=" * 90)
        print("OFFSET ISSUES (gap != 1)")
        print("=" * 90)
        by_comp: dict[str, list[dict]] = defaultdict(list)
        for r in problems:
            by_comp[r["comp"]].append(r)

        for comp in sorted(by_comp):
            print(f"\n--- {comp} ---")
            for r in by_comp[comp]:
                pb_name = mens_current_tier_name(r["pyramid_bottom"], r["season"])
                mt_name = mens_current_tier_name(r["merit_top"], r["season"])
                tag = "OVERLAP" if r["gap"] <= 0 else "GAP"
                print(
                    f"  {r['season']}: pyr_bottom={r['pyramid_bottom']:2d} ({pb_name:15s}) "
                    f"merit_top={r['merit_top']:2d} ({mt_name:15s}) "
                    f"gap={r['gap']:+d} [{tag:7s}] "
                    f"offset {r['current_offset']}->{r['ideal_offset']}"
                )
                for issue in r["counties_issues"]:
                    print(f"    Counties mismatch: {issue}")

    if args.all and ok:
        print(f"\n{'=' * 90}")
        print("OK (gap=1)")
        print("=" * 90)
        for r in ok:
            print(f"  {r['season']} {r['comp']:20s} offset={r['current_offset']}")

    print(f"\nSummary: {len(problems)} issues, {len(ok)} OK")


if __name__ == "__main__":
    main()
