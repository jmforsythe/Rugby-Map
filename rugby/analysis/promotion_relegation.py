"""Scrape current league standings and project next-season tier assignments.

Reads geocoded_teams data for tiers 2-7, scrapes live league tables from the
RFU website (with disk cache), applies the promotion/relegation rules from
tier_assignment_rules.md, and writes a projected leagues markdown file.

Usage:
    python -m rugby.analysis.promotion_relegation
    python -m rugby.analysis.promotion_relegation --season 2025-2026
    python -m rugby.analysis.promotion_relegation --no-cache
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from core import make_request
from rugby import DATA_DIR
from rugby.tiers import extract_tier, mens_current_tier_name

GEOCODED_DIR = DATA_DIR / "geocoded_teams"
CACHE_DIR = DATA_DIR / "standings_cache"

_SECOND_XV_RE = re.compile(r"\s+(II|III|IV|2nd XV|3rd XV|4th XV)\s*$")


def _is_second_xv(name: str) -> bool:
    return bool(_SECOND_XV_RE.search(name))


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        suffix = "th"
    elif n % 10 == 1:
        suffix = "st"
    elif n % 10 == 2:
        suffix = "nd"
    elif n % 10 == 3:
        suffix = "rd"
    else:
        suffix = "th"
    return f"{n}{suffix}"


# ---------------------------------------------------------------------------
# Step 1 — discover leagues
# ---------------------------------------------------------------------------


def load_tier_leagues(season: str) -> list[dict]:
    """Load geocoded league files for tiers 2-7 (men's pyramid only)."""
    season_dir = GEOCODED_DIR / season
    leagues: list[dict] = []
    for path in sorted(season_dir.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        tier_num, tier_name = extract_tier(path.name, season)
        if 2 <= tier_num <= 7:
            leagues.append(
                {
                    "filename": path.name,
                    "league_name": data["league_name"],
                    "league_url": data["league_url"],
                    "tier_num": tier_num,
                    "tier_name": tier_name,
                    "team_count": data["team_count"],
                }
            )
    return leagues


# ---------------------------------------------------------------------------
# Step 2 — scrape / cache standings
# ---------------------------------------------------------------------------


def _scrape_standings(league_url: str, league_name: str) -> list[str]:
    """Scrape team names in standing order from an RFU league table page."""
    print(f"  Scraping: {league_name}")
    response = make_request(league_url, delay_seconds=1)
    soup = BeautifulSoup(response.content, "html.parser")

    team_cells = soup.find_all(
        "td",
        class_=lambda x: isinstance(x, str) and "coh-style-team-name" in x,
    )

    teams: list[str] = []
    for cell in team_cells:
        link = cell.find("a", href=True)
        if link:
            name = link.get_text(strip=True)
            if re.match(r"^w[A-Z]", name):
                name = name[1:]
            teams.append(name)
    return teams


def get_standings(league: dict, season: str, *, use_cache: bool = True) -> list[str]:
    """Return team names in standing order, using a per-league disk cache."""
    cache_file = CACHE_DIR / season / league["filename"]

    if use_cache and cache_file.exists():
        with open(cache_file, encoding="utf-8") as f:
            teams: list[str] = json.load(f)
        print(f"  Cached: {league['league_name']} ({len(teams)} teams)")
        return teams

    teams = _scrape_standings(league["league_url"], league["league_name"])

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(teams, f, indent=2, ensure_ascii=False)

    return teams


# ---------------------------------------------------------------------------
# Step 3 — apply promotion / relegation rules
# ---------------------------------------------------------------------------


def _apply_rules(tier: int, position: int, total: int, team_name: str) -> tuple[int, str]:
    """Return (next_tier, mechanism) for one team."""

    if tier == 2:
        # Championship — no Premiership data so nobody promotes out.
        # Last place relegated to Tier 3.
        if position == total:
            return (3, "Auto-relegation")
        return (2, "Stay")

    if tier == 3:
        if position == 1:
            return (2, "Auto-promotion (subject to MOS)")
        if 2 <= position <= 11:
            return (3, "Stay")
        if position >= 12:
            return (4, "Auto-relegation")

    if tier == 4:
        if position == 1:
            return (3, "Auto-promotion")
        if 2 <= position <= 12:
            return (4, "Stay")
        if position >= 13:
            return (5, "Auto-relegation")

    if tier == 5:
        if position == 1:
            return (4, "Auto-promotion")
        if 2 <= position <= 10:
            return (5, "Stay")
        if position >= 11:
            return (6, "Auto-relegation")

    if tier == 6:
        if position == 1:
            return (5, "Auto-promotion")
        if 2 <= position <= 10:
            return (6, "Stay")
        if position == 11:
            return (7, "Survival PO loser")
        if position >= 12:
            return (7, "Auto-relegation")

    if tier == 7:
        if position == 1:
            return (6, "Auto-promotion")
        return (7, "Stay")

    return (tier, "Stay")


def _handle_second_xv_blocks(assignments: list[dict]) -> None:
    """Block second-XV promotions above Regional 2 and cascade to next eligible."""
    for check_tier in (6, 5):
        ceiling = 5 if check_tier == 6 else 4
        by_league: dict[str, list[dict]] = {}
        for a in assignments:
            if a["current_tier"] == check_tier:
                by_league.setdefault(a["filename"], []).append(a)

        for league_teams in by_league.values():
            league_teams.sort(key=lambda x: x["position"])
            promoted = [t for t in league_teams if t["next_tier"] == ceiling]
            if not promoted or not _is_second_xv(promoted[0]["team_name"]):
                continue

            promoted[0]["next_tier"] = check_tier
            promoted[0]["mechanism"] = "Stay (second XV cannot promote above Tier 6)"
            for t in league_teams:
                if (
                    t["position"] > promoted[0]["position"]
                    and t["next_tier"] == check_tier
                    and t["mechanism"] == "Stay"
                    and not _is_second_xv(t["team_name"])
                ):
                    t["next_tier"] = ceiling
                    t["mechanism"] = "Auto-promotion (next eligible after second XV block)"
                    break


def assign_teams(leagues: list[dict], standings: dict[str, list[str]]) -> list[dict]:
    """Apply promotion/relegation rules and return one record per team."""
    assignments: list[dict] = []

    for league in leagues:
        fname = league["filename"]
        lname = league["league_name"]
        tier = league["tier_num"]
        teams = standings[fname]
        total = len(teams)

        for pos_idx, team_name in enumerate(teams):
            position = pos_idx + 1
            next_tier, mechanism = _apply_rules(tier, position, total, team_name)
            assignments.append(
                {
                    "team_name": team_name,
                    "league_name": lname,
                    "filename": fname,
                    "current_tier": tier,
                    "position": position,
                    "total_in_league": total,
                    "next_tier": next_tier,
                    "mechanism": mechanism,
                }
            )

    _handle_second_xv_blocks(assignments)
    return assignments


# ---------------------------------------------------------------------------
# Step 4 — markdown output
# ---------------------------------------------------------------------------

_TIER_TARGETS: dict[int, tuple[str, int | None]] = {
    2: ("Championship", 14),
    3: ("National 1", 14),
    4: ("National 2 (×3)", 42),
    5: ("Regional 1 (×6)", 72),
    6: ("Regional 2 (×12)", 144),
    7: ("Counties 1 (×19)", None),
}


def build_markdown(assignments: list[dict], season: str) -> str:
    """Build projected-leagues markdown matching the existing format."""
    next_start = int(season.split("-")[0]) + 1
    next_season = f"{next_start}-{next_start + 1}"

    lines: list[str] = []

    # ---- header ----
    lines.append(f"# Projected {next_season} English Rugby Men's League Assignments")
    lines.append("")
    lines.append(
        f"Generated automatically from {season} league standings " "scraped from the RFU website."
    )
    lines.append("Rules applied from `tier_assignment_rules.md`.")
    lines.append("")

    # ---- assumptions ----
    lines.append("## Assumptions")
    lines.append("")
    lines.append(
        "- **Premiership data unavailable** — Championship 1st place stays at "
        "Tier 2. No team promoted from Championship to Premiership; no team "
        "relegated from Premiership to Championship."
    )
    lines.append(
        "- **Play-off default heuristic** — every play-off participant remains "
        "at their current tier (the statistically most likely individual outcome)."
    )
    lines.append(
        "- **Regional 2 Survival Play-Off** — 10th beats 11th (higher position "
        "wins); 11th is relegated."
    )
    lines.append(
        "- **BPR data unavailable** — the 5 Counties 1 runners-up promotions "
        "via Best Playing Record cannot be resolved. 5 Tier 6 slots remain "
        "unfilled."
    )
    lines.append("")

    # ---- flags ----
    second_xv_flags = [a for a in assignments if "second XV" in a.get("mechanism", "")]
    if second_xv_flags:
        lines.append("## Flags")
        lines.append("")
        lines.append("| Flag | Detail |")
        lines.append("|------|--------|")
        for a in second_xv_flags:
            lines.append(
                f"| Second XV | {a['team_name']} ({a['league_name']}, "
                f"{_ordinal(a['position'])}) — {a['mechanism']} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")

    # ---- per-tier sections ----
    tiers_present = sorted({a["current_tier"] for a in assignments})

    for tier_num in tiers_present:
        tier_name = mens_current_tier_name(tier_num)
        tier_teams = [a for a in assignments if a["current_tier"] == tier_num]

        staying = [a for a in tier_teams if a["next_tier"] == tier_num]
        relegated_out = [a for a in tier_teams if a["next_tier"] > tier_num]

        promoted_in = [
            a for a in assignments if a["next_tier"] == tier_num and a["current_tier"] > tier_num
        ]
        relegated_in = [
            a for a in assignments if a["next_tier"] == tier_num and a["current_tier"] < tier_num
        ]

        total_next = len(staying) + len(promoted_in) + len(relegated_in)

        lines.append(f"## Tier {tier_num} — {tier_name} ({total_next} teams)")
        lines.append("")

        # staying, grouped by league
        league_names_in_tier = sorted({a["league_name"] for a in tier_teams})
        single_league = len(league_names_in_tier) == 1

        for league_name in league_names_in_tier:
            league_staying = sorted(
                [a for a in staying if a["league_name"] == league_name],
                key=lambda x: x["position"],
            )
            if not league_staying:
                continue
            if single_league:
                heading = f"Staying in {league_name} ({len(league_staying)} teams)"
            else:
                heading = f"{league_name} — Staying ({len(league_staying)} teams)"
            lines.append(f"### {heading}")
            lines.append("")
            lines.append(f"| # | Team | {season} Position |")
            lines.append("|---|------|-----------------|")
            for i, a in enumerate(league_staying, 1):
                lines.append(f"| {i} | {a['team_name']} | {_ordinal(a['position'])} |")
            lines.append("")

        # promoted to this tier (from tier below)
        if promoted_in:
            holding = f"{tier_name} Promoted"
            lines.append(
                f"### Promoted to Tier {tier_num} ({len(promoted_in)} teams) "
                f'— holding league "{holding}"'
            )
            lines.append("")
            lines.append("| Team | From League | Mechanism |")
            lines.append("|------|-------------|-----------|")
            for a in sorted(promoted_in, key=lambda x: x["team_name"]):
                lines.append(
                    f"| {a['team_name']} | {a['league_name']} "
                    f"({_ordinal(a['position'])}) | {a['mechanism']} |"
                )
            lines.append("")

        # relegated to this tier (from tier above)
        if relegated_in:
            holding = f"{tier_name} Relegated"
            lines.append(
                f"### Relegated to Tier {tier_num} ({len(relegated_in)} teams) "
                f'— holding league "{holding}"'
            )
            lines.append("")
            lines.append("| Team | From League | Mechanism |")
            lines.append("|------|-------------|-----------|")
            for a in sorted(relegated_in, key=lambda x: x["team_name"]):
                lines.append(
                    f"| {a['team_name']} | {a['league_name']} "
                    f"({_ordinal(a['position'])}) | {a['mechanism']} |"
                )
            lines.append("")

        # relegated from this tier (going down)
        if relegated_out:
            for dest in sorted({a["next_tier"] for a in relegated_out}):
                dest_teams = sorted(
                    [a for a in relegated_out if a["next_tier"] == dest],
                    key=lambda x: (x["league_name"], x["position"]),
                )
                lines.append(
                    f"### Relegated from Tier {tier_num} "
                    f"({len(dest_teams)} teams) → Tier {dest}"
                )
                lines.append("")
                lines.append("| Team | From League | Mechanism |")
                lines.append("|------|-------------|-----------|")
                for a in dest_teams:
                    lines.append(
                        f"| {a['team_name']} | {a['league_name']} "
                        f"({_ordinal(a['position'])}) | {a['mechanism']} |"
                    )
                lines.append("")

        # total check line
        parts = [f"{len(staying)} staying"]
        if promoted_in:
            parts.append(f"{len(promoted_in)} promoted in")
        if relegated_in:
            parts.append(f"{len(relegated_in)} relegated in")
        lines.append(f"**{tier_name} total: {' + '.join(parts)} = {total_next}**")
        lines.append("")
        lines.append("---")
        lines.append("")

    # ---- validation summary ----
    lines.append("## Validation Summary")
    lines.append("")
    lines.append("| Tier | Level | Target | Confirmed | Notes |")
    lines.append("|------|-------|--------|-----------|-------|")

    for tier_num in tiers_present:
        n_stay = len(
            [a for a in assignments if a["current_tier"] == tier_num and a["next_tier"] == tier_num]
        )
        n_prom = len(
            [a for a in assignments if a["next_tier"] == tier_num and a["current_tier"] > tier_num]
        )
        n_rel = len(
            [a for a in assignments if a["next_tier"] == tier_num and a["current_tier"] < tier_num]
        )
        total = n_stay + n_prom + n_rel
        name, target = _TIER_TARGETS.get(tier_num, (mens_current_tier_name(tier_num), None))

        detail = f"{n_stay} staying + {n_prom} promoted + {n_rel} relegated in"
        if target is not None:
            check = "✓" if total == target else f"({total - target:+d})"
            lines.append(f"| {tier_num} | {name} | {target} | **{total}** | {detail}. {check} |")
        else:
            lines.append(f"| {tier_num} | {name} | varies | **{total}** | {detail} |")

    lines.append("")

    # ---- movement totals ----
    lines.append("### Movement Totals")
    lines.append("")
    lines.append("| Direction | Count | Teams |")
    lines.append("|-----------|-------|-------|")

    moves: dict[tuple[int, int], list[str]] = {}
    for a in assignments:
        if a["next_tier"] != a["current_tier"]:
            key = (a["current_tier"], a["next_tier"])
            moves.setdefault(key, []).append(a["team_name"])

    for (from_t, to_t), teams in sorted(moves.items()):
        team_list = ", ".join(sorted(teams))
        if len(team_list) > 120:
            team_list = f"{len(teams)} teams"
        lines.append(f"| Tier {from_t} → Tier {to_t} | {len(teams)} | {team_list} |")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape league standings and project next-season tier assignments."
    )
    parser.add_argument(
        "--season",
        default="2025-2026",
        help="Season (default: %(default)s)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore disk cache and re-scrape all standings",
    )
    parser.add_argument(
        "--output",
        help="Output file path (default: data/rugby/projected_<next>.md)",
    )
    args = parser.parse_args()

    season: str = args.season
    use_cache: bool = not args.no_cache

    next_start = int(season.split("-")[0]) + 1
    next_season = f"{next_start}-{next_start + 1}"
    output_path = Path(args.output or str(DATA_DIR / f"projected_{next_season}.md"))

    print(f"Loading leagues for season {season}...")
    leagues = load_tier_leagues(season)
    print(f"Found {len(leagues)} leagues in tiers 2-7")
    for tier in sorted({lg["tier_num"] for lg in leagues}):
        tier_leagues = [lg for lg in leagues if lg["tier_num"] == tier]
        print(f"  Tier {tier}: {len(tier_leagues)} league(s)")

    print(f"\nScraping standings (cache={'on' if use_cache else 'off'})...")
    standings: dict[str, list[str]] = {}
    for league in leagues:
        teams = get_standings(league, season, use_cache=use_cache)
        standings[league["filename"]] = teams
        if not teams:
            print(f"  WARNING: No teams found for {league['league_name']}")

    print("\nApplying promotion/relegation rules...")
    assignments = assign_teams(leagues, standings)

    print("Generating markdown...")
    md = build_markdown(assignments, season)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"\nOutput written to {output_path}")

    movers = [a for a in assignments if a["next_tier"] != a["current_tier"]]
    print(f"Total teams: {len(assignments)}")
    print(f"Teams moving: {len(movers)}")


if __name__ == "__main__":
    main()
