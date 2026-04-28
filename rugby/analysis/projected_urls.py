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
from rugby.analysis.promotion_relegation import _PROMOTION_MAP
from rugby.maps import COLOR_PALETTE, UNASSIGNED_COLOR

PROJECTED_PATH = DATA_DIR / "projected_2026-2027.md"

BASE_URL = "https://rugbyunionmap.uk/custom-map/"


def _parse_projected_md(path: str | None = None) -> dict[int, list[tuple[str, list[str]]]]:
    """Parse the projected markdown into ``{tier: [(league_name, [team, ...]), ...]}``."""
    md_path = path or PROJECTED_PATH
    with open(md_path, encoding="utf-8") as f:
        lines = f.readlines()

    tiers: dict[int, list[tuple[str, list[str]]]] = {}
    team_sources: dict[str, str] = {}
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
                if current_section in ("Promoted", "Relegated"):
                    src = _extract_source_league(line)
                    if src:
                        team_sources[team] = src

    _flush()
    tiers = _merge_consecutive_league_sections(tiers)
    return _merge_promoted_relegated(tiers, team_sources)


def _merge_consecutive_league_sections(
    tiers: dict[int, list[tuple[str, list[str]]]],
) -> dict[int, list[tuple[str, list[str]]]]:
    """Merge duplicate league headings (e.g. Staying + From Regional 2) into one roster."""
    out: dict[int, list[tuple[str, list[str]]]] = {}
    for tier_num, leagues in tiers.items():
        merged: list[tuple[str, list[str]]] = []
        i = 0
        while i < len(leagues):
            name, teams = leagues[i]
            if name in ("Promoted", "Relegated"):
                merged.append((name, list(teams)))
                i += 1
                continue
            chunk = list(teams)
            j = i + 1
            while j < len(leagues) and leagues[j][0] == name:
                chunk.extend(leagues[j][1])
                j += 1
            merged.append((name, chunk))
            i = j
        out[tier_num] = merged
    return out


def _merge_promoted_relegated(
    tiers: dict[int, list[tuple[str, list[str]]]],
    team_sources: dict[str, str] | None = None,
) -> dict[int, list[tuple[str, list[str]]]]:
    """Distribute promoted/relegated teams into destination leagues where known.

    Uses the feeder-league mapping from ``promotion_relegation`` to assign
    promoted teams to their correct destination league.  Teams whose
    destination cannot be determined are collected into an "Unassigned" group.
    """
    team_sources = team_sources or {}
    league_names_by_tier: dict[int, set[str]] = {}
    for tier_num, leagues in tiers.items():
        league_names_by_tier[tier_num] = {
            name for name, _ in leagues if name not in ("Promoted", "Relegated")
        }

    merged: dict[int, list[tuple[str, list[str]]]] = {}
    for tier_num, leagues in tiers.items():
        league_extra: dict[str, list[str]] = {}
        unassigned: list[str] = []
        normal: list[tuple[str, list[str]]] = []

        for name, teams in leagues:
            if name not in ("Promoted", "Relegated"):
                normal.append((name, teams))
                continue
            for team in teams:
                src = team_sources.get(team, "")
                dest = _PROMOTION_MAP.get(src, "")
                if dest and dest in league_names_by_tier.get(tier_num, set()):
                    league_extra.setdefault(dest, []).append(team)
                else:
                    unassigned.append(team)

        if league_extra:
            normal = [
                (lg_name, lg_teams + sorted(league_extra.get(lg_name, [])))
                for lg_name, lg_teams in normal
            ]
        if unassigned:
            normal.append(("Unassigned", unassigned))
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


def _extract_source_league(line: str) -> str | None:
    """Pull the source league name from a promoted/relegated table row.

    Expects rows like ``| Team | Regional 1 Midlands (1st) | Auto-promotion |``
    and returns ``Regional 1 Midlands``.
    """
    cells = [c.strip() for c in line.split("|")]
    cells = [c for c in cells if c]
    if len(cells) < 2:
        return None
    return re.sub(r"\s*\(\d+\w*\)\s*$", "", cells[1]).strip() or None


def build_hash(leagues: list[tuple[str, list[str]]], tier_num: int = 1) -> str:
    """Build the URL hash string for the custom map page.

    Colours are assigned to match the regular tier maps: the palette start
    index is offset by ``tier_num - 1`` so that the same league always gets
    the same colour regardless of how the URL is generated.  "Unassigned"
    leagues receive a fixed neutral colour.
    """
    parts: list[str] = []
    league_idx = 0
    for name, teams in leagues:
        if name == "Unassigned":
            color = UNASSIGNED_COLOR
        else:
            color = COLOR_PALETTE[(tier_num - 1 + league_idx) % len(COLOR_PALETTE)]
            league_idx += 1
        team_str = ",".join(quote(t, safe="") for t in teams)
        parts.append(f"{quote(name, safe='')}:{quote(color, safe='')}:{team_str}")
    return "#" + ";".join(parts)


def build_tier_url(
    tier_leagues: list[tuple[str, list[str]]],
    base_url: str = BASE_URL,
    tier_num: int = 1,
) -> str:
    """Build a full custom-map URL for one tier's worth of leagues."""
    return base_url + build_hash(tier_leagues, tier_num)


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
            print(f"\n  {build_hash(leagues, tier_num)}")
        else:
            print(f"\n  {build_tier_url(leagues, args.base_url, tier_num)}")


if __name__ == "__main__":
    main()
