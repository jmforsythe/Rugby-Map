"""Analyse merit competition pyramids for tier extraction issues.

Scans geocoded_teams/<season>/merit/ and reports:
- Competitions missing level 1 (no top-tier league)
- Files where the filename number doesn't match the computed local tier
- Gaps in the local tier sequence within a competition
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from tier_extraction import (
    _match_named_merit_leagues,
    _strip_sponsor_prefix,
    extract_tier,
    get_competition_offset,
    get_number_from_tier_name,
)

GEOCODED_DIR = Path(__file__).resolve().parent / "geocoded_teams"


def get_seasons() -> list[str]:
    """Discover available seasons from geocoded_teams/ subdirectories."""
    return sorted(d.name for d in GEOCODED_DIR.iterdir() if d.is_dir() and "-" in d.name)


@dataclass
class LeagueFile:
    filename: str
    rel_path: str
    local_tier: int
    tier_name: str
    filename_num: int
    is_named_override: bool
    team_count: int
    league_name: str


def analyse_competition(
    season: str,
    comp_name: str,
    comp_dir: Path,
    season_dir: Path,
) -> tuple[list[LeagueFile], list[str]]:
    """Analyse a single competition directory. Returns (league_files, issues)."""
    files: list[LeagueFile] = []
    issues: list[str] = []

    for filepath in sorted(comp_dir.glob("*.json")):
        rel = filepath.relative_to(season_dir).as_posix()
        local_tier, tier_name = extract_tier(rel, season)

        # Check if matched by named-league override
        normalized = rel.replace("\\", "/")
        is_named = _match_named_merit_leagues(normalized, season) is not None

        # Extract the raw number from the filename (after sponsor stripping)
        stripped = _strip_sponsor_prefix(filepath.name)
        filename_num = get_number_from_tier_name(stripped, "")

        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        team_count = len(data.get("teams", []))
        league_name = data.get("league_name", filepath.stem)

        files.append(
            LeagueFile(
                filename=filepath.name,
                rel_path=rel,
                local_tier=local_tier,
                tier_name=tier_name,
                filename_num=filename_num,
                is_named_override=is_named,
                team_count=team_count,
                league_name=league_name,
            )
        )

    if not files:
        return files, issues

    tiers = sorted({f.local_tier for f in files})

    # Check: unknown tier
    for f in files:
        if f.local_tier == 999:
            issues.append(f"UNKNOWN TIER: {f.filename}")

    # Check 1: no level 1
    if 1 not in tiers:
        issues.append(f"NO LEVEL 1: tiers present are {tiers}")

    # Check 2: filename number != local tier (skip named overrides and num=0)
    for f in files:
        if f.is_named_override:
            continue
        if f.filename_num > 0 and f.filename_num != f.local_tier:
            issues.append(
                f"NUMBER MISMATCH: {f.filename} — filename number {f.filename_num}, "
                f"local tier {f.local_tier}"
            )

    # Check 3: gaps in pyramid
    non_unknown = [t for t in tiers if t != 999]
    if len(non_unknown) > 1:
        expected = set(range(non_unknown[0], non_unknown[-1] + 1))
        missing = sorted(expected - set(non_unknown))
        if missing:
            issues.append(f"GAPS: missing tiers {missing} (present: {non_unknown})")

    return files, issues


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyse merit competition pyramids for tier extraction issues",
    )
    parser.add_argument("--season", help="Filter to a specific season")
    parser.add_argument("--competition", help="Filter to a specific competition")
    parser.add_argument(
        "--issues-only",
        action="store_true",
        help="Only show competitions with issues",
    )
    args = parser.parse_args()

    seasons = [args.season] if args.season else get_seasons()

    total_issues = 0
    total_comps = 0
    issue_comps = 0

    for season in seasons:
        season_dir = GEOCODED_DIR / season
        merit_dir = season_dir / "merit"
        if not merit_dir.is_dir():
            continue

        comps = sorted(d.name for d in merit_dir.iterdir() if d.is_dir())
        if args.competition:
            comps = [c for c in comps if c == args.competition]

        for comp_name in comps:
            comp_dir = merit_dir / comp_name
            files, issues = analyse_competition(
                season,
                comp_name,
                comp_dir,
                season_dir,
            )

            if not files:
                continue

            total_comps += 1
            total_issues += len(issues)
            if issues:
                issue_comps += 1

            if args.issues_only and not issues:
                continue

            offset = get_competition_offset(comp_name, season)
            comp_display = comp_name.replace("_", " ")

            print(f"\n{'=' * 78}")
            print(f"  {season}  |  {comp_display}  (offset {offset})")
            print(f"{'=' * 78}")

            for f in sorted(files, key=lambda x: (x.local_tier, x.filename)):
                named_tag = " [named]" if f.is_named_override else ""
                abs_tier = f.local_tier + offset
                num_str = f"num={f.filename_num}" if f.filename_num > 0 else "num=-"
                print(
                    f"  Local {f.local_tier:2d}  (abs {abs_tier:2d})  "
                    f"{num_str:6s}  "
                    f"{f.filename}{named_tag}  "
                    f"({f.team_count} teams)"
                )

            if issues:
                print()
                for issue in issues:
                    print(f"  *** {issue}")

    print(f"\n{'=' * 78}")
    print(f"  SUMMARY: {total_comps} competition/season combinations analysed")
    print(f"           {issue_comps} with issues, {total_issues} total issues")
    print(f"{'=' * 78}")


if __name__ == "__main__":
    main()
