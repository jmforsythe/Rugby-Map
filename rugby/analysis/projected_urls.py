"""Generate custom-map URLs from projected league assignments.

Parses a projected-leagues markdown file (e.g. data/rugby/projected_2026-2027.md)
to extract the projected league compositions (staying + promoted + relegated-in
teams) and builds per-tier URL hashes compatible with the custom map builder at
/custom-map/.

Usage:
    python -m rugby.analysis.projected_urls
    python -m rugby.analysis.projected_urls --tier 5
    python -m rugby.analysis.projected_urls --file data/rugby/projected_2026_2027.md
"""

from __future__ import annotations

import argparse
import re
from urllib.parse import quote

from rugby import DATA_DIR

PROJECTED_PATH = DATA_DIR / "projected_2026-2027.md"

BASE_URL = "https://rugbyunionmap.uk/custom-map/"

COLOR_PALETTE = [
    "#e6194b",
    "#3cb44b",
    "#ffe119",
    "#0082c8",
    "#f58231",
    "#911eb4",
    "#46f0f0",
    "#f032e6",
    "#6a8f00",
    "#fabebe",
    "#008080",
    "#e6beff",
    "#aa6e28",
    "#fffac8",
    "#800000",
    "#008f5a",
    "#808000",
    "#ffd8b1",
    "#000080",
    "#808080",
    "#ff6b6b",
    "#4ecdc4",
    "#95e1d3",
    "#f38181",
    "#aa96da",
    "#fcbad3",
    "#a8d8ea",
    "#ffcfd2",
    "#5b2c6f",
]


def _parse_projected_md(path: str | None = None) -> dict[int, list[tuple[str, list[str]]]]:
    """Parse the projected markdown into ``{tier: [(league_name, [team, ...]), ...]}``."""
    md_path = path or PROJECTED_PATH
    with open(md_path, encoding="utf-8") as f:
        lines = f.readlines()

    tiers: dict[int, list[tuple[str, list[str]]]] = {}
    current_tier: int | None = None
    current_section: str | None = None
    current_teams: list[str] = []

    tier_re = re.compile(r"^## Tier (\d+)")
    end_re = re.compile(r"^## (Validation|Movement)")
    section_re = re.compile(r"^### (.+)")
    skip_sections = re.compile(
        r"Relegated from Tier|BPR Runners-Up|UNRESOLVED|Validation|Movement|Assumptions|Flags"
    )

    def _flush() -> None:
        nonlocal current_section, current_teams
        if current_tier is not None and current_section and current_teams:
            tiers.setdefault(current_tier, []).append((current_section, list(current_teams)))
        current_section = None
        current_teams = []

    for line in lines:
        line = line.rstrip("\n")

        if end_re.match(line):
            _flush()
            current_tier = None
            continue

        tier_match = tier_re.match(line)
        if tier_match:
            _flush()
            current_tier = int(tier_match.group(1))
            continue

        if current_tier is None:
            continue

        section_match = section_re.match(line)
        if section_match:
            _flush()
            raw = section_match.group(1).strip()

            if skip_sections.search(raw):
                current_section = None
                continue

            current_section = _clean_section_name(raw, current_tier)
            continue

        if current_section is None:
            continue

        if line.startswith("|") and "---" not in line and "Team" not in line and "#" not in line:
            team = _extract_team_from_row(line)
            if team:
                current_teams.append(team)

    _flush()
    return _merge_promoted_relegated(tiers)


def _merge_promoted_relegated(
    tiers: dict[int, list[tuple[str, list[str]]]],
) -> dict[int, list[tuple[str, list[str]]]]:
    """Combine 'Promoted' and 'Relegated' entries into a single 'Unassigned' league per tier."""
    merged: dict[int, list[tuple[str, list[str]]]] = {}
    for tier_num, leagues in tiers.items():
        normal: list[tuple[str, list[str]]] = []
        holding_teams: list[str] = []
        for name, teams in leagues:
            if name in ("Promoted", "Relegated"):
                holding_teams.extend(teams)
            else:
                normal.append((name, teams))
        if holding_teams:
            normal.append(("Unassigned", holding_teams))
        merged[tier_num] = normal
    return merged


def _clean_section_name(raw: str, tier: int) -> str:
    """Normalise an h3 heading into a short league name."""
    raw = re.sub(r"\s*—\s*.*$", "", raw)

    raw = re.sub(r"\s*\(\d+ teams?\)", "", raw)

    replacements = [
        (r"Staying in ", ""),
        (r" — Remaining$", ""),
        (r" — Staying$", ""),
        (r"Promoted to Tier \d+", "Promoted"),
        (r"Relegated to Tier \d+", "Relegated"),
    ]
    for pattern, repl in replacements:
        raw = re.sub(pattern, repl, raw).strip()

    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _extract_team_from_row(line: str) -> str | None:
    """Pull the team name from a markdown table row."""
    cells = [c.strip() for c in line.split("|")]
    cells = [c for c in cells if c]
    if not cells:
        return None

    if len(cells) >= 3 and cells[0].isdigit():
        return cells[1].strip()

    return cells[0].strip() if cells[0] and not cells[0].startswith("---") else None


def build_hash(leagues: list[tuple[str, list[str]]]) -> str:
    """Build the URL hash string for the custom map page."""
    parts: list[str] = []
    for i, (name, teams) in enumerate(leagues):
        color = COLOR_PALETTE[i % len(COLOR_PALETTE)]
        team_str = ",".join(quote(t, safe="") for t in teams)
        parts.append(f"{quote(name, safe='')}:{quote(color, safe='')}:{team_str}")
    return "#" + ";".join(parts)


def build_tier_url(
    tier_leagues: list[tuple[str, list[str]]],
    base_url: str = BASE_URL,
) -> str:
    """Build a full custom-map URL for one tier's worth of leagues."""
    return base_url + build_hash(tier_leagues)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate custom-map URLs from projected league data."
    )
    parser.add_argument("--tier", type=int, help="Show only this tier number.")
    parser.add_argument(
        "--file",
        help="Path to projected markdown file (default: %(default)s).",
        default=str(PROJECTED_PATH),
    )
    parser.add_argument(
        "--base-url",
        default=BASE_URL,
        help="Base URL for the custom map page (default: %(default)s).",
    )
    parser.add_argument(
        "--hash-only",
        action="store_true",
        help="Print only the hash fragment, not the full URL.",
    )
    args = parser.parse_args()

    tiers = _parse_projected_md(args.file)

    show_tiers = [args.tier] if args.tier else sorted(tiers)

    for tier_num in show_tiers:
        if tier_num not in tiers:
            print(f"No data for tier {tier_num}")
            continue

        leagues = tiers[tier_num]
        total_teams = sum(len(teams) for _, teams in leagues)

        print(f"\n{'=' * 70}")
        print(f"  Tier {tier_num} — {len(leagues)} league(s), {total_teams} teams")
        print(f"{'=' * 70}")

        for name, teams in leagues:
            print(f"  {name}: {len(teams)} teams")

        if args.hash_only:
            print(f"\n  {build_hash(leagues)}")
        else:
            print(f"\n  {build_tier_url(leagues, args.base_url)}")


if __name__ == "__main__":
    main()
