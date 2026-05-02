"""Project next-season tier assignments from league table order.

Reads tiers 2-8 from ``data/rugby/geocoded_teams/<season>/``: team order in each
league JSON is taken as the table order. Optionally ``--scrape-standings`` can
fetch live RFU pages instead (no disk cache).

Applies promotion/relegation rules and writes projected leagues markdown.

Usage:
    python -m rugby.analysis.promotion_relegation
    python -m rugby.analysis.promotion_relegation --season 2025-2026
    python -m rugby.analysis.promotion_relegation --scrape-standings
    python -m rugby.analysis.promotion_relegation --interactive-r2-c1
    python -m rugby.analysis.promotion_relegation --prompt-missing-r2-c1
    python -m rugby.analysis.promotion_relegation --interactive-c2-c1
    python -m rugby.analysis.promotion_relegation --bpr-md path/to/with_bpr_line.md

County 2 intake per Counties 1 league is optional JSON
``data/rugby/c1_c2_promotion_quotas_<next-season>.json`` (default_quota + per-league overrides);
when present, total inbound slots split across Counties 2 leagues mapped to each destination.
Built-in default BPR promotion names are used when ``--bpr`` and ``--bpr-md`` are omitted
(override with ``--no-bpr``). See ``DEFAULT_BPR_TEAM_NAMES`` in this module.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from core import make_request
from rugby import DATA_DIR
from rugby.tiers import extract_tier, mens_current_tier_name

GEOCODED_DIR = DATA_DIR / "geocoded_teams"

_BPR_LINE_FRAGMENT = "promoted via Best Playing Record are "

# Default Counties 1 BPR promotions when `--bpr`, `--bpr-md`, and `--no-bpr` are not used.
DEFAULT_BPR_TEAM_NAMES: tuple[str, ...] = (
    "Dinnington",
    "Finchley",
    "Ryton",
    "Warrington",
    "Weybridge Vandals",
)


def projected_markdown_path(source_season: str) -> Path:
    """Path to projected-leagues markdown for the season after ``source_season``."""
    next_start = int(source_season.split("-")[0]) + 1
    next_season = f"{next_start}-{next_start + 1}"
    return DATA_DIR / f"projected_{next_season}.md"


def _split_bpr_name_list(fragment: str) -> list[str]:
    fragment = fragment.strip()
    if not fragment:
        return []
    if ", and " in fragment:
        head, last = fragment.rsplit(", and ", 1)
        parts = [p.strip() for p in head.split(",") if p.strip()] + [last.strip()]
    elif " and " in fragment and "," not in fragment:
        parts = [p.strip() for p in fragment.split(" and ") if p.strip()]
    else:
        parts = [p.strip() for p in fragment.split(",") if p.strip()]
    return [p for p in parts if p]


def parse_bpr_teams_from_projected_md(path: Path) -> list[str] | None:
    """Read **BPR resolved** line from ``promotion_relegation`` output. Returns None if unresolved."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if "BPR data unavailable" in text:
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if "**BPR resolved**" not in line or _BPR_LINE_FRAGMENT not in line:
            continue
        idx = line.index(_BPR_LINE_FRAGMENT) + len(_BPR_LINE_FRAGMENT)
        tail = line[idx:].strip()
        if tail.endswith("."):
            tail = tail[:-1].strip()
        names = _split_bpr_name_list(tail)
        return names if names else None
    return None


def r2_to_c1_map_path(next_season: str) -> Path:
    """JSON mapping Regional 2 relegants → Counties 1 league display names."""
    return DATA_DIR / f"r2_to_c1_{next_season}.json"


def c2_to_c1_map_path(next_season: str) -> Path:
    """JSON mapping Counties 2 league names → Counties 1 league display names."""
    return DATA_DIR / f"c2_to_c1_{next_season}.json"


def c1_c2_promotion_quotas_path(next_season: str) -> Path:
    """JSON: Counties 1 league → how many sides promote in from Counties 2 (split across feeder C2)."""
    return DATA_DIR / f"c1_c2_promotion_quotas_{next_season}.json"


def load_r2_c1_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("assignments", data)
    return {str(k): str(v) for k, v in raw.items() if v}


def save_r2_c1_map(path: Path, source_season: str, assignments_map: dict[str, str]) -> None:
    payload = {
        "source_season": source_season,
        "assignments": dict(sorted(assignments_map.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_c2_to_c1_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("assignments", data)
    return {str(k): str(v) for k, v in raw.items() if v}


def save_c2_to_c1_map(path: Path, source_season: str, assignments_map: dict[str, str]) -> None:
    payload = {
        "source_season": source_season,
        "assignments": dict(sorted(assignments_map.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


_COUNTIES_TWO_PREFIX = "Counties 2 "
_COUNTIES_ONE_PREFIX = "Counties 1 "


def matching_counties_one_for_two(
    counties_two_league_name: str,
    counties_one_labels: set[str],
) -> str | None:
    """If ``Counties 2 X`` has a counterpart ``Counties 1 X`` in the pyramid, return ``Counties 1 X``."""
    if not counties_two_league_name.startswith(_COUNTIES_TWO_PREFIX):
        return None
    candidate = _COUNTIES_ONE_PREFIX + counties_two_league_name[len(_COUNTIES_TWO_PREFIX) :]
    return candidate if candidate in counties_one_labels else None


def merge_c2_to_c1_auto_by_suffix(
    base: dict[str, str],
    counties_two_leagues: list[dict],
    counties_one_labels: set[str],
) -> tuple[dict[str, str], int]:
    """Prefer explicit JSON entries when valid; else set ``Counties 2 X`` → ``Counties 1 X`` when that C1 league exists."""
    out = dict(base)
    filled = 0
    for lg in counties_two_leagues:
        key = lg["league_name"]
        existing = out.get(key, "")
        if existing in counties_one_labels:
            continue
        auto = matching_counties_one_for_two(key, counties_one_labels)
        if auto:
            out[key] = auto
            filled += 1
    return out, filled


def load_c2_promotion_quotas(path: Path) -> tuple[dict[str, int], int] | None:
    """Load per–Counties 1 inbound slot counts.

    Returns ``(quotas_for_named_leagues, default_quota_for_all_else)`` when the file
    exists with the expected shape; otherwise ``None``.
    """
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    default_q = int(payload.get("default_quota", 2))
    raw = payload.get("by_counties_one_league", payload.get("by_counties_one", {}))
    quotas = {str(k): int(v) for k, v in raw.items()}
    return quotas, default_q


def allocate_c2_promotion_slots(
    c2_to_c1_map: dict[str, str],
    counties_two_labels: frozenset[str],
    counties_one_labels: set[str],
    quotas_by_c1: dict[str, int],
    default_quota: int,
) -> dict[str, int]:
    """Split each Counties 1 inbound quota among feeder Counties 2 leagues (table slots)."""
    inbound: defaultdict[str, list[str]] = defaultdict(list)
    for c2_league_name, dest in sorted(c2_to_c1_map.items()):
        if not dest or dest not in counties_one_labels:
            continue
        if c2_league_name not in counties_two_labels:
            continue
        inbound[dest].append(c2_league_name)

    slots: dict[str, int] = {}

    for c1_name in sorted(counties_one_labels):
        feeders = inbound.get(c1_name, [])
        if not feeders:
            continue
        q = quotas_by_c1.get(c1_name, default_quota)
        if q <= 0:
            for fdr in feeders:
                slots[fdr] = 0
            continue
        n = len(feeders)
        base = q // n
        rem = q % n
        for i, feeder in enumerate(feeders):
            slots[feeder] = base + (1 if i < rem else 0)

    return slots


def _valid_r2_c1_destination(
    team_name: str,
    r2_to_c1: dict[str, str],
    counties_one_labels: frozenset[str],
) -> bool:
    dest = (r2_to_c1.get(team_name, "") or "").strip()
    return bool(dest and dest in counties_one_labels)


def prompt_r2_c1_assignments(
    relegated: list[dict],
    counties_one_leagues: list[str],
    existing: dict[str, str],
    *,
    only_prompt_unmapped: bool = False,
) -> dict[str, str]:
    """Prompt for each R2→C1 team; return full map (existing + new).

    When ``only_prompt_unmapped`` is True, only teams without a valid saved destination
    (missing key, empty, or not a real Counties 1 league name) are prompted.
    """
    result = dict(existing)
    leagues_sorted = sorted(counties_one_leagues)
    leagues_set = frozenset(leagues_sorted)

    if not only_prompt_unmapped:
        for a in relegated:
            if a["league_name"] in _R2_MIDLANDS_NO_C1_LEAGUES:
                result.pop(a["team_name"], None)

    non_midlands = [a for a in relegated if a["league_name"] not in _R2_MIDLANDS_NO_C1_LEAGUES]
    midlands_n = len(relegated) - len(non_midlands)

    if only_prompt_unmapped:
        need = sorted(
            [
                a
                for a in non_midlands
                if not _valid_r2_c1_destination(a["team_name"], result, leagues_set)
            ],
            key=lambda x: x["team_name"],
        )
    else:
        need = sorted(non_midlands, key=lambda x: x["team_name"])

    if midlands_n and not only_prompt_unmapped:
        print(
            f"\n{midlands_n} side(s) from Regional 2 Midlands East/North/West stay "
            "unassigned (pooled Relegated to Tier 7 only — no Counties 1 pick)."
        )
    if only_prompt_unmapped:
        if not need:
            print(
                "\nAll non-Midlands Regional 2 relegants already have a Counties 1 pick in the map."
            )
            return result
        print(
            f"\n{len(need)} Regional 2 relegant(s) have no valid Counties 1 destination in "
            f"`r2_to_c1` — assign each (Enter = leave unassigned for markdown pooled list)."
        )
    else:
        print(
            f"\n{len(need)} team(s) to assign to a Counties 1 league "
            "(or Enter per team to leave unassigned in the pooled table)."
        )

    for idx, a in enumerate(need, 1):
        name = a["team_name"]
        if (
            not only_prompt_unmapped
            and name in result
            and _valid_r2_c1_destination(name, result, leagues_set)
        ):
            print(
                f"  [{idx}/{len(need)}] {name}: using saved -> {result[name]}",
            )
            continue

        print(
            f"\n  [{idx}/{len(need)}] {name}\n"
            f"      from {a['league_name']}, {_ordinal(a['position'])} — {a['mechanism']}"
        )
        for j, lg in enumerate(leagues_sorted, 1):
            print(f"      {j:2}. {lg}")
        choice = input("      League # or exact name (Enter = unassigned): ").strip()
        if not choice:
            result.pop(name, None)
            continue
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(leagues_sorted):
                result[name] = leagues_sorted[n - 1]
            else:
                print("      Invalid number; leaving unassigned.")
                result.pop(name, None)
        else:
            matches = [lg for lg in leagues_sorted if choice.lower() in lg.lower()]
            if len(matches) == 1:
                result[name] = matches[0]
            elif len(matches) > 1:
                print(f"      Ambiguous ({len(matches)} matches); leaving unassigned.")
                result.pop(name, None)
            else:
                exact = [lg for lg in leagues_sorted if lg == choice]
                if exact:
                    result[name] = exact[0]
                else:
                    print("      No match; leaving unassigned.")
                    result.pop(name, None)

    return result


def prompt_c2_c1_assignments(
    counties_two_leagues: list[dict],
    counties_one_names: list[str],
    standings: dict[str, list[str]],
    existing: dict[str, str],
) -> dict[str, str]:
    """Prompt for Counties 2 league → Counties 1 destination (one promoter per league)."""
    result = dict(existing)
    leagues_sorted = sorted(counties_two_leagues, key=lambda lg: lg["league_name"])
    c1_sorted = sorted(counties_one_names)

    print(
        f"\n{len(leagues_sorted)} Counties 2 league(s): assign each feeder to a Counties 1 league.\n"
        "Promotion counts come from `c1_c2_promotion_quotas_<next>.json` when present "
        "(split across feeders). Table order fills slots; II/III names are skipped only when "
        "that promotion would hit Regional 1 or national leagues, or place the side at the "
        "same tier or above its principal XV (from geocoded squads).\n"
        "Otherwise each mapped league promotes one side by default."
    )
    for idx, lg in enumerate(leagues_sorted, 1):
        lname = lg["league_name"]
        teams = standings.get(lg["filename"], [])
        first = teams[0] if teams else "(no teams)"

        saved = result.get(lname)
        if saved and saved in counties_one_names:
            print(f"  [{idx}/{len(leagues_sorted)}] {lname}: using map -> {saved}")
            continue

        print(f"\n  [{idx}/{len(leagues_sorted)}] {lname}\n" f"      Table-leader (1st): {first}")
        for j, c1 in enumerate(c1_sorted, 1):
            print(f"      {j:2}. {c1}")
        choice = input(
            "      Promote into Counties 1 league # or exact name "
            "(Enter = leave unmapped for pooled Tier 7 list): "
        ).strip()
        if not choice:
            result.pop(lname, None)
            continue
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(c1_sorted):
                result[lname] = c1_sorted[n - 1]
            else:
                print("      Invalid number; leaving unmapped.")
                result.pop(lname, None)
        else:
            matches = [x for x in c1_sorted if choice.lower() in x.lower()]
            if len(matches) == 1:
                result[lname] = matches[0]
            elif len(matches) > 1:
                print(f"      Ambiguous ({len(matches)} matches); leaving unmapped.")
                result.pop(lname, None)
            elif choice in c1_sorted:
                result[lname] = choice
            else:
                print("      No match; leaving unmapped.")
                result.pop(lname, None)

    return result


_SECOND_XV_RE = re.compile(r"\s+(II|III|IV|2nd XV|3rd XV|4th XV)\s*$")
# Men's tier numbering from ``extract_tier``: Regional 1 = tier 5; smaller = higher pyramid.
_REGIONAL_ONE_OR_ABOVE_MAX_TIER = 5


def _is_second_xv(name: str) -> bool:
    return bool(_SECOND_XV_RE.search(name))


def _principal_xv_club_label(second_style_name: str) -> str:
    """Club label after stripping trailing II / 2nd XV etc.; pairs with principal side name."""
    return _SECOND_XV_RE.sub("", second_style_name).strip()


def build_principal_xv_best_tier(
    leagues: list[dict],
    standings: dict[str, list[str]],
) -> dict[str, int]:
    """Best (smallest tier number) for each non-second-XV team name in geocoded squads."""
    best: dict[str, int] = {}
    for lg in leagues:
        t = lg["tier_num"]
        fname = lg["filename"]
        for nm in standings.get(fname, []):
            if _is_second_xv(nm):
                continue
            prev = best.get(nm)
            if prev is None or t < prev:
                best[nm] = t
    return best


def second_xv_promotion_blocked(
    team_name: str,
    target_tier: int,
    principal_best_tier_by_label: dict[str, int],
) -> bool:
    """True if promotion to ``target_tier`` violates second-XV pyramid rules."""
    if not _is_second_xv(team_name):
        return False
    if target_tier <= _REGIONAL_ONE_OR_ABOVE_MAX_TIER:
        return True
    principal = _principal_xv_club_label(team_name)
    ft = principal_best_tier_by_label.get(principal)
    if ft is None:
        return False
    return target_tier <= ft


def _second_xv_stay_reason(
    team_name: str,
    target_tier: int,
    principal_best_tier_by_label: dict[str, int],
) -> str:
    """Explanation when a blocked second-XV stays after another side takes the promotion slot."""
    if target_tier <= _REGIONAL_ONE_OR_ABOVE_MAX_TIER:
        return "Stay (second XV: promotion would reach Regional 1 or national leagues)"
    principal = _principal_xv_club_label(team_name)
    ft = principal_best_tier_by_label.get(principal)
    if ft is not None:
        return (
            "Stay (second XV: promotion would reach tier "
            f"{target_tier} at/above principal XV at tier {ft})"
        )
    return "Stay (second XV: tier rule)"


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


def _tier8_promotable_positions(
    team_names_ordered: list[str],
    k: int,
    principal_best_tier_by_label: dict[str, int],
) -> frozenset[int]:
    """1-based positions of first ``k`` sides allowed a Counties 2 → Counties 1 slot by II/tier rules."""
    picks: list[int] = []
    for i, nm in enumerate(team_names_ordered):
        if len(picks) >= k:
            break
        if not second_xv_promotion_blocked(nm, 7, principal_best_tier_by_label):
            picks.append(i + 1)
    return frozenset(picks)


def build_tier8_promotion_positions_by_filename(
    counties_two_league_rows: list[dict],
    standings: dict[str, list[str]],
    slots_by_counties_two_name: dict[str, int],
    *,
    default_unmapped_slots: int = 1,
    principal_best_tier_by_label: dict[str, int] | None = None,
) -> dict[str, frozenset[int]]:
    """Map Counties 2 filename → promotion table positions given per-league slot counts."""
    principal_best = principal_best_tier_by_label or {}
    out: dict[str, frozenset[int]] = {}
    for lg in counties_two_league_rows:
        fname = lg["filename"]
        lname = lg["league_name"]
        teams = standings.get(fname, [])
        k = slots_by_counties_two_name.get(lname, default_unmapped_slots)
        out[fname] = _tier8_promotable_positions(teams, max(0, k), principal_best)
    return out


# ---------------------------------------------------------------------------
# Feeder relationships: which lower-tier leagues feed each upper-tier league
# ---------------------------------------------------------------------------

_TIER5_TO_TIER4: dict[str, str] = {
    "Regional 1 North West": "National League 2 North",
    "Regional 1 North East": "National League 2 North",
    "Regional 1 South Central": "National League 2 East",
    "Regional 1 South East": "National League 2 East",
    "Regional 1 Midlands": "National League 2 West",
    "Regional 1 Tribute Ale South West": "National League 2 West",
}

_TIER6_TO_TIER5: dict[str, str] = {
    "Regional 2 North": "Regional 1 North East",
    "Regional 2 North East": "Regional 1 North East",
    "Regional 2 North West": "Regional 1 North West",
    "Regional 2 Midlands North": "Regional 1 North West",
    "Regional 2 Midlands West": "Regional 1 Midlands",
    "Regional 2 Midlands East": "Regional 1 Midlands",
    "Regional 2 Tribute Ale South West": "Regional 1 Tribute Ale South West",
    "Regional 2 Tribute Ale Severn": "Regional 1 Tribute Ale South West",
    "Regional 2 South Central": "Regional 1 South Central",
    "Regional 2 South East": "Regional 1 South Central",
    "Regional 2 Anglia": "Regional 1 South East",
    "Regional 2 Thames": "Regional 1 South East",
}

_PROMOTION_MAP: dict[str, str] = {**_TIER5_TO_TIER4, **_TIER6_TO_TIER5}

_FEEDERS: dict[str, list[str]] = {}
for _src, _dst in _PROMOTION_MAP.items():
    _FEEDERS.setdefault(_dst, []).append(_src)

# Regional 2 Midlands: no single natural Counties 1 league — keep relegants pooled.
_R2_MIDLANDS_NO_C1_LEAGUES: frozenset[str] = frozenset(
    {
        "Regional 2 Midlands East",
        "Regional 2 Midlands North",
        "Regional 2 Midlands West",
    }
)

# Extra Counties 1 → Counties 2 relegations (after auto-promotions / BPR), by table bottom
# within each division. Names must match ``league_name`` in geocoded_teams Counties 1 JSON.
_COUNTIES_ONE_SCHEDULED_DOWNS: dict[str, int] = {
    # 3 down
    "Counties 1 Tribute Ale Western North": 3,
    "Counties 1 Tribute Ale Southern North": 3,
    "Counties 1 Hampshire": 3,
    "Counties 1 Eastern Counties": 3,
    # 2 down
    "Counties 1 Tribute Ale Western West": 2,
    "Counties 1 Tribute Ale Southern South": 2,
    "Counties 1 Kent": 2,
    "Counties 1 Midlands East (South)": 2,
    "Counties 1 Midlands West (South)": 2,
    "Counties 1 Midlands East (North)": 2,
    "Counties 1 Midlands West (North)": 2,
    "Counties 1 adm Lancashire & Cheshire": 2,
    "Counties 1 Yorkshire": 2,
    # 1 down
    "Counties 1 Surrey/Sussex": 1,
    "Counties 1 Herts": 1,
}

# When a Regional 2 survival play-off result differs from the default heuristic
# (10th beats 11th), swap projected outcomes for the two clubs (same league, tier 6).
_SEASON_SURVIVAL_SWAPS: dict[str, list[tuple[str, str]]] = {
    "2025-2026": [
        ("North Dorset", "Royal Wootton Bassett II"),  # R2 Tribute Ale Severn
        ("Dartfordians", "Canterbury II"),  # R2 South East
    ],
}


def _apply_survival_swaps(assignments: list[dict], pairs: list[tuple[str, str]] | None) -> None:
    """Swap next_tier + mechanism for each pair (e.g. 10th/11th after a PO upset)."""
    if not pairs:
        return
    by_name = {a["team_name"]: a for a in assignments}
    applied = 0
    for name_a, name_b in pairs:
        rec_a = by_name.get(name_a)
        rec_b = by_name.get(name_b)
        if rec_a is None or rec_b is None:
            missing = name_a if rec_a is None else name_b
            print(f"  WARNING: survival swap skipped — team not found: {missing}")
            continue
        if rec_a["league_name"] != rec_b["league_name"]:
            print(
                f"  WARNING: survival swap skipped — {name_a} and {name_b} "
                f"not in same league ({rec_a['league_name']} vs {rec_b['league_name']})"
            )
            continue
        if rec_a["current_tier"] != 6 or rec_b["current_tier"] != 6:
            print("  WARNING: survival swap skipped — expected Regional 2 (tier 6)")
            continue
        rec_a["next_tier"], rec_b["next_tier"] = rec_b["next_tier"], rec_a["next_tier"]
        rec_a["mechanism"], rec_b["mechanism"] = rec_b["mechanism"], rec_a["mechanism"]
        applied += 1
    if applied:
        print(f"  Applied {applied} Regional 2 survival play-off outcome swap(s).")


# ---------------------------------------------------------------------------
# Step 1 — discover leagues
# ---------------------------------------------------------------------------


def load_tier_leagues(season: str) -> list[dict]:
    """Load geocoded league files for tiers 2-8 (men's Counties 2 and tiers 2-7 pyramid)."""
    season_dir = GEOCODED_DIR / season
    leagues: list[dict] = []
    for path in sorted(season_dir.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        tier_num, tier_name = extract_tier(path.name, season)
        if tier_num != 8 and not (2 <= tier_num <= 7):
            continue
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
# Step 2 — table order: geocoded JSON (default) or live RFU scrape
# ---------------------------------------------------------------------------


def load_standings_order_from_geocoded(season: str, league_filename: str) -> list[str]:
    """Return team names in table order from a geocoded league JSON file."""
    path = GEOCODED_DIR / season / league_filename
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    teams = data.get("teams") or []
    return [str(t["name"]) for t in teams if t.get("name")]


def _scrape_standings(league_url: str, league_name: str, *, quiet: bool = False) -> list[str]:
    """Scrape team names in standing order from an RFU league table page."""
    if not quiet:
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


def compute_assignments(
    season: str,
    *,
    bpr_teams: list[str] | None = None,
    survival_swaps: list[tuple[str, str]] | None = None,
    quiet: bool = False,
    scrape_standings: bool = False,
    counties_two_promotion_slots: dict[str, int] | None = None,
    counties_one_scheduled_downs: bool = False,
) -> list[dict]:
    """Load tier 2–8 leagues, table order from geocoded JSON (or RFU if scraping)."""
    leagues = load_tier_leagues(season)
    standings: dict[str, list[str]] = {}
    for league in leagues:
        if scrape_standings:
            teams = _scrape_standings(league["league_url"], league["league_name"], quiet=quiet)
        else:
            teams = load_standings_order_from_geocoded(season, league["filename"])
        standings[league["filename"]] = teams
        if not teams and not quiet:
            print(f"  WARNING: No teams found for {league['league_name']}")
    principal_best_tier_by_label = build_principal_xv_best_tier(leagues, standings)
    tier8_pf = None
    if counties_two_promotion_slots is not None:
        c2_rows = [lg for lg in leagues if lg["tier_num"] == 8]
        tier8_pf = build_tier8_promotion_positions_by_filename(
            c2_rows,
            standings,
            counties_two_promotion_slots,
            principal_best_tier_by_label=principal_best_tier_by_label,
        )
    out = assign_teams(
        leagues,
        standings,
        bpr_teams=bpr_teams,
        survival_swaps=survival_swaps,
        tier8_promote_positions_by_filename=tier8_pf,
        principal_best_tier_by_label=principal_best_tier_by_label,
    )
    if counties_one_scheduled_downs:
        c1fz = frozenset(lg["league_name"] for lg in leagues if lg["tier_num"] == 7)
        nb = _apply_counties_one_scheduled_downs(out, c1fz, quiet=quiet)
        if nb:
            _assign_dest_leagues(out)
            if not quiet:
                print(
                    f"  Counties 1 scheduled downs: {nb} demotion(s) to Tier 8 "
                    "(beyond standard rules)."
                )
    return out


# ---------------------------------------------------------------------------
# Step 3 — apply promotion / relegation rules
# ---------------------------------------------------------------------------


def _apply_rules(
    tier: int,
    position: int,
    total: int,
    team_name: str,
    *,
    tier8_promote_positions_by_filename: dict[str, frozenset[int]] | None = None,
    fname: str = "",
) -> tuple[int, str]:
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

    if tier == 8:
        if tier8_promote_positions_by_filename is None:
            if position == 1:
                return (7, "Auto-promotion")
            return (8, "Stay")
        promoted_at = tier8_promote_positions_by_filename[fname]
        if position in promoted_at:
            return (7, f"Auto-promotion ({_ordinal(position)} in Counties 2)")
        return (8, "Stay")

    return (tier, "Stay")


def _handle_second_xv_blocks(
    assignments: list[dict],
    principal_best_tier_by_label: dict[str, int],
    *,
    tiers: tuple[int, ...] = (8, 6, 5),
) -> None:
    """Block illegal II/III promotions and give the promotion slot to the next legal side."""
    for check_tier, ceiling in ((8, 7), (6, 5), (5, 4)):
        if check_tier not in tiers:
            continue
        by_league: dict[str, list[dict]] = {}
        for a in assignments:
            if a["current_tier"] == check_tier:
                by_league.setdefault(a["filename"], []).append(a)

        for league_teams in by_league.values():
            league_teams.sort(key=lambda x: x["position"])
            promoted = [t for t in league_teams if t["next_tier"] == ceiling]
            if not promoted:
                continue
            top = promoted[0]
            if not second_xv_promotion_blocked(
                top["team_name"], ceiling, principal_best_tier_by_label
            ):
                continue

            top["next_tier"] = check_tier
            top["mechanism"] = _second_xv_stay_reason(
                top["team_name"], ceiling, principal_best_tier_by_label
            )
            for t in league_teams:
                if (
                    t["position"] > top["position"]
                    and t["next_tier"] == check_tier
                    and t["mechanism"] == "Stay"
                    and not second_xv_promotion_blocked(
                        t["team_name"], ceiling, principal_best_tier_by_label
                    )
                ):
                    t["next_tier"] = ceiling
                    t["mechanism"] = "Auto-promotion (next eligible after second XV block)"
                    break


def _assign_dest_leagues(assignments: list[dict]) -> None:
    """Set dest_league for teams crossing tier boundaries with known feeders."""
    for a in assignments:
        if a["next_tier"] < a["current_tier"]:
            a["dest_league"] = _PROMOTION_MAP.get(a["league_name"], "")
        elif a["next_tier"] > a["current_tier"]:
            feeders = _FEEDERS.get(a["league_name"], [])
            a["dest_league"] = " / ".join(sorted(feeders)) if feeders else ""
        else:
            a["dest_league"] = ""


_COUNTIES_ONE_SCHEDULED_DOWN_MECH = "Auto-relegation (scheduled Counties 1 downs)"


def _apply_counties_one_scheduled_downs(
    assignments: list[dict],
    tier7_league_names: frozenset[str],
    *,
    quiet: bool = False,
    schedule: dict[str, int] | None = None,
) -> int:
    """Relegate worst finishers within each Counties 1 division according to fixed slot counts."""
    sched = schedule or _COUNTIES_ONE_SCHEDULED_DOWNS
    ndem = 0
    for league_name, n_down in sorted(sched.items()):
        if league_name not in tier7_league_names or n_down <= 0:
            continue
        pool = [
            a
            for a in assignments
            if a["current_tier"] == 7 and a["league_name"] == league_name and a["next_tier"] == 7
        ]
        pool.sort(key=lambda x: -x["position"])
        take = min(n_down, len(pool))
        if take < n_down and not quiet:
            print(
                f"  WARNING: Counties 1 downs for {league_name}: scheduled {n_down}, "
                f"only {len(pool)} tier-7 club(s) still projected to stay"
            )
        for victim in pool[:take]:
            victim["next_tier"] = 8
            victim["mechanism"] = _COUNTIES_ONE_SCHEDULED_DOWN_MECH
            ndem += 1
    return ndem


def _apply_bpr(assignments: list[dict], bpr_teams: list[str]) -> None:
    """Promote BPR winners from Tier 7 to Tier 6."""
    bpr_set = {name.strip() for name in bpr_teams}
    for a in assignments:
        if a["team_name"] in bpr_set and a["current_tier"] == 7:
            a["next_tier"] = 6
            a["mechanism"] = "BPR"
            bpr_set.discard(a["team_name"])
    if bpr_set:
        print(f"  WARNING: BPR teams not found in Tier 7: {', '.join(sorted(bpr_set))}")


def assign_teams(
    leagues: list[dict],
    standings: dict[str, list[str]],
    bpr_teams: list[str] | None = None,
    survival_swaps: list[tuple[str, str]] | None = None,
    *,
    tier8_promote_positions_by_filename: dict[str, frozenset[int]] | None = None,
    principal_best_tier_by_label: dict[str, int] | None = None,
) -> list[dict]:
    """Apply promotion/relegation rules for tiers 2-8 and return one record per team."""
    assignments: list[dict] = []

    for league in leagues:
        fname = league["filename"]
        lname = league["league_name"]
        tier = league["tier_num"]
        teams = standings[fname]
        total = len(teams)

        for pos_idx, team_name in enumerate(teams):
            position = pos_idx + 1
            next_tier, mechanism = _apply_rules(
                tier,
                position,
                total,
                team_name,
                tier8_promote_positions_by_filename=tier8_promote_positions_by_filename,
                fname=fname,
            )
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

    _handle_second_xv_blocks(
        assignments,
        principal_best_tier_by_label or {},
    )
    if bpr_teams:
        _apply_bpr(assignments, bpr_teams)
    _apply_survival_swaps(assignments, survival_swaps)
    _assign_dest_leagues(assignments)
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
    8: ("Counties 2", None),
}

_TIER_BASE_NAMES: dict[int, str] = {
    2: "Championship",
    3: "National League 1",
    4: "National League 2",
    5: "Regional 1",
    6: "Regional 2",
    7: "Counties 1",
    8: "Counties 2",
}


def _tier8_source_md_columns(
    from_league: str, mechanism: str, *, was_tier8: bool
) -> tuple[str, str]:
    """Do not spell out Counties 2 league names in markdown (tier 8 predictions)."""
    if not was_tier8:
        return from_league, mechanism
    disp_mech = mechanism.replace(" in Counties 2)", ")")
    return "Tier 8", disp_mech


def build_markdown(
    assignments: list[dict[str, Any]],
    season: str,
    *,
    bpr_teams: list[str] | None = None,
    r2_to_c1: dict[str, str] | None = None,
    c2_to_c1: dict[str, str] | None = None,
) -> str:
    """Build projected-leagues markdown matching the existing format."""
    next_start = int(season.split("-")[0]) + 1
    next_season = f"{next_start}-{next_start + 1}"

    lines: list[str] = []

    # ---- header ----
    lines.append(f"# Projected {next_season} English Rugby Men's League Assignments")
    lines.append("")
    lines.append(
        f"Generated automatically from {season} league table order in "
        "`data/rugby/geocoded_teams/` (same order as teams in each league JSON)."
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
    c2_map = c2_to_c1 or {}
    if c2_map:
        n_c2_mapped = sum(1 for v in c2_map.values() if v)
        lines.append(
            f"- **Counties 2 → Counties 1 (data only)** — {n_c2_mapped} feeder mapping(s) "
            "in `c2_to_c1_<next-season>.json`; optional `c1_c2_promotion_quotas_<next>.json`. "
            "**This document does not list Counties 2 (tier 8) league tables.** "
            "Promotions from tier 8 appear under the destination Counties 1 league when "
            "the `c2_to_c1` map resolves; otherwise they stay in the pooled "
            '"Promoted to Tier 7" section with source labelled Tier 8 only.'
        )
    else:
        lines.append(
            "- **Counties 2 → Counties 1** — no `c2_to_c1_<next-season>.json`; "
            "Counties 2 promotions into Counties 1 (if any) appear in pooled Tier 7 lists "
            "with source labelled Tier 8 only."
        )
    if bpr_teams:
        sorted_names = sorted(bpr_teams)
        names = ", ".join(sorted_names[:-1]) + ", and " + sorted_names[-1]
        lines.append(
            f"- **BPR resolved** — the {len(bpr_teams)} Counties 1 runners-up "
            f"promoted via Best Playing Record are {names}."
        )
    else:
        lines.append(
            "- **BPR data unavailable** — the 5 Counties 1 runners-up promotions "
            "via Best Playing Record cannot be resolved. 5 Tier 6 slots remain "
            "unfilled."
        )
    lines.append(
        "- **Counties 1 scheduled downs** — after standard promotion rules and BPR, "
        "extra relegations to Counties 2 are applied by division using fixed slot counts "
        f"(bottom of table within each league); shown as *{_COUNTIES_ONE_SCHEDULED_DOWN_MECH}*."
    )
    lines.append("")

    # ---- flags ----
    second_xv_flags = [
        a for a in assignments if "second XV" in a.get("mechanism", "") and a["current_tier"] != 8
    ]
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

    # ---- per-tier sections (tier 8 / Counties 2 emitted only elsewhere if needed) ----
    tiers_present = sorted({a["current_tier"] for a in assignments if a["current_tier"] != 8})

    for tier_num in tiers_present:
        tier_name = mens_current_tier_name(tier_num)
        base_name = _TIER_BASE_NAMES.get(tier_num, tier_name)
        tier_teams = [a for a in assignments if a["current_tier"] == tier_num]

        staying = [a for a in tier_teams if a["next_tier"] == tier_num]
        relegated_out = [a for a in tier_teams if a["next_tier"] > tier_num]

        promoted_in: list[dict[str, Any]] = sorted(
            [a for a in assignments if a["next_tier"] == tier_num and a["current_tier"] > tier_num],
            key=lambda x: x["team_name"],
        )
        relegated_in: list[dict[str, Any]] = sorted(
            [a for a in assignments if a["next_tier"] == tier_num and a["current_tier"] < tier_num],
            key=lambda x: x["team_name"],
        )
        total_next = len(staying) + len(promoted_in) + len(relegated_in)
        lines.append(f"## Tier {tier_num} — {tier_name} ({total_next} teams)")
        lines.append("")

        # Per-league staying sections
        league_names_in_tier = sorted({a["league_name"] for a in tier_teams})
        is_single_league = len(league_names_in_tier) == 1

        r2_c1_map = r2_to_c1 or {}
        c2_c1_map = c2_to_c1 or {}
        from_r2_by_league: dict[str, list[dict]] = defaultdict(list)
        from_c2_by_league: dict[str, list[dict]] = defaultdict(list)
        relegated_in_pooled: list[dict] = []
        if tier_num == 7 and relegated_in:
            for a in relegated_in:
                if a["league_name"] in _R2_MIDLANDS_NO_C1_LEAGUES:
                    relegated_in_pooled.append(a)
                    continue
                dest = r2_c1_map.get(a["team_name"], "")
                if dest and dest in league_names_in_tier:
                    from_r2_by_league[dest].append(a)
                else:
                    relegated_in_pooled.append(a)
        else:
            relegated_in_pooled = list(relegated_in)

        if tier_num == 7:
            for a in promoted_in:
                if a["current_tier"] != 8:
                    continue
                dest = (c2_c1_map.get(a["league_name"], "") or "").strip()
                if dest and dest in league_names_in_tier:
                    from_c2_by_league[dest].append(a)

        promoted_in_for_pooled_section = promoted_in
        promoted_bpr_to_tier: list[dict] = []
        if tier_num == 7:
            c2_placed_ids = frozenset(id(a) for rows in from_c2_by_league.values() for a in rows)
            pooled_t7_t8_left: list[dict[str, Any]] = [
                a for a in promoted_in if id(a) not in c2_placed_ids
            ]
            promoted_in_for_pooled_section = sorted(
                pooled_t7_t8_left,
                key=lambda x: x["team_name"],
            )
        if tier_num == 6:
            promoted_bpr_to_tier = sorted(
                [a for a in promoted_in if a["mechanism"] == "BPR"],
                key=lambda x: (x["league_name"], x["team_name"]),
            )
            pooled_t6_left: list[dict[str, Any]] = [
                a for a in promoted_in if a["mechanism"] != "BPR"
            ]
            promoted_in_for_pooled_section = sorted(
                pooled_t6_left,
                key=lambda x: x["team_name"],
            )

        if tier_num == 7:
            leagues_for_sections = sorted(
                frozenset(league_names_in_tier)
                | frozenset(from_r2_by_league.keys())
                | frozenset(from_c2_by_league.keys())
            )
        else:
            leagues_for_sections = sorted(league_names_in_tier)

        for league_name in leagues_for_sections:
            league_staying = sorted(
                [a for a in staying if a["league_name"] == league_name],
                key=lambda x: x["position"],
            )
            join_r2 = sorted(from_r2_by_league.get(league_name, []), key=lambda x: x["team_name"])
            join_c2 = sorted(from_c2_by_league.get(league_name, []), key=lambda x: x["team_name"])
            if not league_staying and not join_r2 and not join_c2:
                continue

            if league_staying:
                if is_single_league:
                    lines.append(f"### Staying in {league_name} ({len(league_staying)} teams)")
                else:
                    lines.append(f"### {league_name} — Staying ({len(league_staying)} teams)")
                lines.append("")
                lines.append(f"| # | Team | {season} Position |")
                lines.append("|---|------|-----------------|")
                for i, a in enumerate(league_staying, 1):
                    lines.append(f"| {i} | {a['team_name']} | {_ordinal(a['position'])} |")
                lines.append("")

            if join_r2:
                lines.append(f"### {league_name} — From Regional 2 ({len(join_r2)} teams)")
                lines.append("")
                lines.append("| Team | From League | Mechanism |")
                lines.append("|------|-------------|-----------|")
                for a in join_r2:
                    lines.append(
                        f"| {a['team_name']} | {a['league_name']} "
                        f"({_ordinal(a['position'])}) | {a['mechanism']} |"
                    )
                lines.append("")

            if join_c2:
                lines.append(f"### {league_name} — From Tier 8 ({len(join_c2)} teams)")
                lines.append("")
                lines.append("| Team | From League | Mechanism |")
                lines.append("|------|-------------|-----------|")
                for a in join_c2:
                    lg_disp, mech_disp = _tier8_source_md_columns(
                        a["league_name"],
                        a["mechanism"],
                        was_tier8=True,
                    )
                    lines.append(
                        f"| {a['team_name']} | {lg_disp} ({_ordinal(a['position'])}) | "
                        f"{mech_disp} |"
                    )
                lines.append("")

        if promoted_bpr_to_tier:
            lines.append(
                f"### Best Playing Record into Tier {tier_num} ({len(promoted_bpr_to_tier)} teams)"
            )
            lines.append("")
            lines.append("| Team | Counties 1 league | Position |")
            lines.append("|------|-------------------|---------|")
            for a in promoted_bpr_to_tier:
                lines.append(
                    f"| {a['team_name']} | {a['league_name']} | {_ordinal(a['position'])} |"
                )
            lines.append("")

        # Promoted into this tier (pooled)
        if promoted_in_for_pooled_section:
            lines.append(
                f"### Promoted to Tier {tier_num} ({len(promoted_in_for_pooled_section)} teams)"
                f' — holding league "{base_name} Promoted"'
            )
            lines.append("")
            lines.append("| Team | From League | Mechanism |")
            lines.append("|------|-------------|-----------|")
            for a in promoted_in_for_pooled_section:
                lg_disp, mech_disp = _tier8_source_md_columns(
                    a["league_name"],
                    a["mechanism"],
                    was_tier8=a["current_tier"] == 8,
                )
                lines.append(
                    f"| {a['team_name']} | {lg_disp} "
                    f"({_ordinal(a['position'])}) | {mech_disp} |"
                )
            lines.append("")

        # Relegated into this tier (pooled — tier 7 may omit teams assigned via r2_to_c1)
        if relegated_in_pooled:
            lines.append(
                f"### Relegated to Tier {tier_num} ({len(relegated_in_pooled)} teams)"
                f' — holding league "{base_name} Relegated"'
            )
            lines.append("")
            lines.append("| Team | From League | Mechanism |")
            lines.append("|------|-------------|-----------|")
            for a in relegated_in_pooled:
                lines.append(
                    f"| {a['team_name']} | {a['league_name']} "
                    f"({_ordinal(a['position'])}) | {a['mechanism']} |"
                )
            lines.append("")

        # Relegated from this tier (pooled per destination tier)
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
            if 8 in (a["current_tier"], a["next_tier"]):
                continue
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
        description="Project next-season tiers from geocoded league table order (or live RFU)."
    )
    parser.add_argument(
        "--season",
        default="2025-2026",
        help="Season (default: %(default)s)",
    )
    parser.add_argument(
        "--scrape-standings",
        action="store_true",
        help="Fetch table order from RFU website instead of geocoded_teams JSON",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--output",
        help="Output file path (default: data/rugby/projected_<next>.md)",
    )
    parser.add_argument(
        "--bpr",
        nargs="+",
        metavar="TEAM",
        help="Counties 1 BPR promotion winners (team names)",
    )
    parser.add_argument(
        "--bpr-md",
        metavar="PATH",
        help=(
            "Read BPR winners from markdown (**BPR resolved** line); "
            "used when --bpr omitted (overrides built-in default names unless --no-bpr)"
        ),
    )
    parser.add_argument(
        "--no-bpr",
        action="store_true",
        help="Do not promote BPR winners (skip built-in default and --bpr-md)",
    )
    parser.add_argument(
        "--interactive-r2-c1",
        action="store_true",
        help="Prompt for Counties 1 destination for each Regional 2 relegant; saves JSON map",
    )
    parser.add_argument(
        "--prompt-missing-r2-c1",
        action="store_true",
        help=(
            "After projection (with quotas/scheduled downs), prompt only relegants lacking a valid "
            "Counties 1 in the saved map — then rewrite markdown (needs a TTY)"
        ),
    )
    parser.add_argument(
        "--interactive-c2-c1",
        action="store_true",
        help="Prompt Counties 2 league→Counties 1 destination for each auto-promoter; saves JSON map",
    )
    parser.add_argument(
        "--r2-c1-map",
        type=str,
        metavar="PATH",
        help="JSON map team→Counties 1 league (default: data/rugby/r2_to_c1_<next>.json)",
    )
    parser.add_argument(
        "--c2-c1-map",
        type=str,
        metavar="PATH",
        help="JSON map Counties 2 league→Counties 1 league (default: data/rugby/c2_to_c1_<next>.json)",
    )
    parser.add_argument(
        "--promotion-quotas",
        type=str,
        metavar="PATH",
        help="Inbound Counties 2 promotion slots per Counties 1 league "
        "(default: data/rugby/c1_c2_promotion_quotas_<next>.json if present)",
    )
    args = parser.parse_args()

    season: str = args.season
    scrape_standings: bool = bool(args.scrape_standings or args.no_cache)

    next_start = int(season.split("-")[0]) + 1
    next_season = f"{next_start}-{next_start + 1}"

    bpr_teams: list[str] | None = None
    if args.bpr and args.no_bpr:
        print("WARNING: --bpr and --no-bpr both set; using --bpr team list.")
    if args.bpr:
        bpr_teams = list(args.bpr)
        if args.bpr_md:
            print("WARNING: --bpr overrides --bpr-md (both supplied). Using --bpr team list.")
    elif args.no_bpr:
        bpr_teams = None
        if args.bpr_md:
            print("WARNING: --no-bpr skips --bpr-md and built-in defaults.")
    elif args.bpr_md:
        pbpr = Path(args.bpr_md)
        parsed_bpr = parse_bpr_teams_from_projected_md(pbpr)
        if parsed_bpr:
            bpr_teams = parsed_bpr
            print(f"  BPR winners read from {pbpr.name}: {len(bpr_teams)} names")
        else:
            print(
                f"  WARNING: Could not parse **BPR resolved** teams from "
                f"{pbpr}; using built-in default BPR names."
            )
            bpr_teams = list(DEFAULT_BPR_TEAM_NAMES)
    else:
        bpr_teams = list(DEFAULT_BPR_TEAM_NAMES)
        print(f"  BPR: built-in defaults ({len(bpr_teams)} teams)")
    output_path = Path(args.output) if args.output else projected_markdown_path(season)
    r2_c1_file = Path(args.r2_c1_map) if args.r2_c1_map else r2_to_c1_map_path(next_season)
    c2_c1_file = Path(args.c2_c1_map) if args.c2_c1_map else c2_to_c1_map_path(next_season)
    quotas_path_cli = Path(args.promotion_quotas) if args.promotion_quotas else None

    print(f"Loading leagues for season {season}...")
    leagues = load_tier_leagues(season)
    print(f"Found {len(leagues)} leagues in tiers 2-8")
    for tier in sorted({lg["tier_num"] for lg in leagues}):
        tier_leagues = [lg for lg in leagues if lg["tier_num"] == tier]
        print(f"  Tier {tier}: {len(tier_leagues)} league(s)")

    print(
        f"\nTable order: {'RFU scrape' if scrape_standings else 'geocoded_teams'} + "
        f"promotion/relegation rules for {season}..."
    )
    survival_swaps = _SEASON_SURVIVAL_SWAPS.get(season)
    promo_quota_file = quotas_path_cli or c1_c2_promotion_quotas_path(next_season)

    r2c1 = load_r2_c1_map(r2_c1_file)
    c2c1 = load_c2_to_c1_map(c2_c1_file)
    c2_leagues_list = [lg for lg in leagues if lg["tier_num"] == 8]
    c1_labels = {lg["league_name"] for lg in leagues if lg["tier_num"] == 7}
    c2c1, n_c2_suffix = merge_c2_to_c1_auto_by_suffix(c2c1, c2_leagues_list, c1_labels)
    if n_c2_suffix:
        print(
            f"  Counties 2->Counties 1: auto-linked {n_c2_suffix} league(s) where "
            '"Counties 2 X" matches "Counties 1 X".'
        )

    assignments: list[dict] | None = None
    if args.interactive_r2_c1:
        assignments = compute_assignments(
            season,
            bpr_teams=bpr_teams,
            survival_swaps=survival_swaps,
            quiet=False,
            scrape_standings=scrape_standings,
            counties_two_promotion_slots=None,
        )
        r6_down = [a for a in assignments if a["current_tier"] == 6 and a["next_tier"] == 7]
        c1_leagues = sorted({lg["league_name"] for lg in leagues if lg["tier_num"] == 7})
        r2c1 = prompt_r2_c1_assignments(r6_down, c1_leagues, r2c1)
        save_r2_c1_map(r2_c1_file, season, r2c1)
        print(f"\nSaved R2->C1 map to {r2_c1_file}")

    if args.interactive_c2_c1:
        c1_names = sorted(c1_labels)
        st_c2: dict[str, list[str]] = {}
        for lg in c2_leagues_list:
            if scrape_standings:
                st_c2[lg["filename"]] = _scrape_standings(
                    lg["league_url"], lg["league_name"], quiet=False
                )
            else:
                st_c2[lg["filename"]] = load_standings_order_from_geocoded(season, lg["filename"])
        c2c1 = prompt_c2_c1_assignments(c2_leagues_list, c1_names, st_c2, c2c1)
        save_c2_to_c1_map(c2_c1_file, season, c2c1)
        print(f"\nSaved Counties 2->Counties 1 map to {c2_c1_file}")

    quotas_loaded = load_c2_promotion_quotas(promo_quota_file)
    counties_two_slots: dict[str, int] | None = None
    if quotas_loaded is not None:
        quotas_ov, def_q = quotas_loaded
        counties_two_slots = allocate_c2_promotion_slots(
            c2c1,
            frozenset(lg["league_name"] for lg in c2_leagues_list),
            c1_labels,
            quotas_ov,
            def_q,
        )
        print(
            f"  Counties 2->Counties 1 promotions: tier 8 slots from {promo_quota_file.name} "
            "(quotas split across feeder Counties 2 leagues)."
        )

    if quotas_loaded is not None:
        assignments = compute_assignments(
            season,
            bpr_teams=bpr_teams,
            survival_swaps=survival_swaps,
            quiet=False,
            scrape_standings=scrape_standings,
            counties_two_promotion_slots=counties_two_slots,
            counties_one_scheduled_downs=True,
        )
    elif assignments is None:
        assignments = compute_assignments(
            season,
            bpr_teams=bpr_teams,
            survival_swaps=survival_swaps,
            quiet=False,
            scrape_standings=scrape_standings,
            counties_two_promotion_slots=None,
            counties_one_scheduled_downs=True,
        )
    else:
        assignments = compute_assignments(
            season,
            bpr_teams=bpr_teams,
            survival_swaps=survival_swaps,
            quiet=False,
            scrape_standings=scrape_standings,
            counties_two_promotion_slots=None,
            counties_one_scheduled_downs=True,
        )

    if args.prompt_missing_r2_c1:
        if not sys.stdin.isatty():
            print(
                "\nWARNING: --prompt-missing-r2-c1 requires an interactive terminal; "
                "skipping prompts. Assign teams in "
                f"{r2_c1_file} or rerun in a terminal with stdin attached."
            )
        else:
            r6_down = [a for a in assignments if a["current_tier"] == 6 and a["next_tier"] == 7]
            c1_leagues = sorted({lg["league_name"] for lg in leagues if lg["tier_num"] == 7})
            r2c1 = prompt_r2_c1_assignments(r6_down, c1_leagues, r2c1, only_prompt_unmapped=True)
            save_r2_c1_map(r2_c1_file, season, r2c1)
            print(f"\nSaved R2->C1 map to {r2_c1_file}")

    print("Generating markdown...")
    md = build_markdown(
        assignments,
        season,
        bpr_teams=bpr_teams,
        r2_to_c1=r2c1,
        c2_to_c1=c2c1,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"\nOutput written to {output_path}")

    movers = [a for a in assignments if a["next_tier"] != a["current_tier"]]
    print(f"Total teams: {len(assignments)}")
    print(f"Teams moving: {len(movers)}")


if __name__ == "__main__":
    main()
