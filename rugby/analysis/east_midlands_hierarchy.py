"""Analyse the East Midlands league hierarchy from shared-club evidence.

For each season, if a club has their Nth XV in league A and a higher-numbered
XV in league B, we can confirm A > B (A is a higher-ranking league).

Relationships are keyed by the actual league name (sponsor name / division name),
not by the tier numbers assigned in tiers.py.
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from rugby.tiers import _SPONSOR_PREFIXES  # noqa: E402, PLC2701

GEOCODED_DIR = Path(__file__).parents[2] / "data" / "rugby" / "geocoded_teams"

_YEAR_LIKE = re.compile(r"^\d{4}$")

type RelEntry = tuple[str, str, int, int, str, str]

# Suffixes that are boilerplate rather than part of the league identity.
_STRIP_SUFFIXES = re.compile(r"[\s_]*(Merit[_ ]Table|Merit|League)$", re.IGNORECASE)


def clean_league_name(stem: str) -> str:
    """Turn a filename stem into a readable league name.

    Strips leading sponsor prefixes (e.g. 'Webb_Ellis_', 'Alban_Wise_Insurance_')
    and trailing boilerplate ('_League', '_Merit_Table'), then replaces
    underscores with spaces.
    """
    s = stem
    for prefix in _SPONSOR_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    s = _STRIP_SUFFIXES.sub("", s)
    return s.replace("_", " ").strip()


def extract_team_rank(name: str) -> tuple[str, int]:
    """Return (club_name, rank) for a team name.

    rank=1 means first XV / only known team (best).  rank>1 means a reserve XV.
    """
    name = name.strip()

    # "(Nth XV)" at the end, e.g. "Ampthill Extras (4th XV)"
    m = re.search(r"\s+\((\d+)(?:st|nd|rd|th)\s+XV\)\s*$", name, re.IGNORECASE)
    if m:
        return name[: m.start()].strip(), int(m.group(1))

    # "Nth XV" at the end, e.g. "Kettering 2nd XV" or "Northampton Outlaws 1st XV"
    m = re.search(r"\s+(\d+)(?:st|nd|rd|th)\s+XV\s*$", name, re.IGNORECASE)
    if m:
        return name[: m.start()].strip(), int(m.group(1))

    # Roman numeral suffix II..V, e.g. "Shelford III"
    m = re.search(r"\s+(II|III|IV|V)\s*$", name)
    if m:
        ranks = {"II": 2, "III": 3, "IV": 4, "V": 5}
        return name[: m.start()].strip(), ranks[m.group(1)]

    # Bare " XV" at the end: only strip when the preceding token is not a year
    # (e.g. "Ampthill 1881 XV" keeps the year as part of the club identity).
    if name.endswith(" XV"):
        before_xv = name[:-3].rstrip()
        last_token = before_xv.split()[-1] if before_xv.split() else ""
        if not _YEAR_LIKE.match(last_token):
            return before_xv, 1

    return name, 1


def load_season(season: str) -> dict[str, list[tuple[str, int]]]:
    """Return {league_stem: [(club_name, rank), ...]} for one season."""
    em_dir = GEOCODED_DIR / season / "merit" / "East_Midlands"
    if not em_dir.is_dir():
        return {}
    leagues: dict[str, list[tuple[str, int]]] = {}
    for f in sorted(em_dir.iterdir()):
        if f.suffix != ".json":
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        teams = [extract_team_rank(t["name"]) for t in data.get("teams", [])]
        leagues[f.stem] = teams
    return leagues


def find_season_relationships(
    leagues: dict[str, list[tuple[str, int]]],
) -> list[tuple[str, str, str, int, int]]:
    """Return confirmed ordering relationships within a single season.

    Each element is (higher_league_stem, lower_league_stem, club, higher_rank, lower_rank)
    where higher_rank < lower_rank (a lower XV number means a better team).
    """
    club_to_appearances: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for league, teams in leagues.items():
        for club, rank in teams:
            club_to_appearances[club].append((league, rank))

    relationships: list[tuple[str, str, str, int, int]] = []
    for club, appearances in club_to_appearances.items():
        if len(appearances) < 2:
            continue
        appearances.sort(key=lambda x: x[1])
        for i in range(len(appearances)):
            for j in range(i + 1, len(appearances)):
                league_a, rank_a = appearances[i]
                league_b, rank_b = appearances[j]
                if rank_a < rank_b and league_a != league_b:
                    relationships.append((league_a, league_b, club, rank_a, rank_b))
    return relationships


def main() -> None:
    seasons = sorted(
        d.name
        for d in GEOCODED_DIR.iterdir()
        if d.is_dir() and (d / "merit" / "East_Midlands").is_dir()
    )

    print(f"Seasons with East Midlands data: {seasons[0]} to {seasons[-1]} ({len(seasons)} total)")
    print()

    # Key: (clean higher league name, clean lower league name)
    # Value: list of (season, club, rank_h, rank_l, raw_stem_h, raw_stem_l)
    all_relationships: dict[tuple[str, str], list[RelEntry]] = defaultdict(list)

    for season in seasons:
        leagues = load_season(season)
        if not leagues:
            continue
        for higher_stem, lower_stem, club, rank_h, rank_l in find_season_relationships(leagues):
            key = (clean_league_name(higher_stem), clean_league_name(lower_stem))
            all_relationships[key].append((season, club, rank_h, rank_l, higher_stem, lower_stem))

    if not all_relationships:
        print("No relationships found.")
        return

    # Sort by observation count descending, then alphabetically
    sorted_rels = sorted(
        all_relationships.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )

    print("=" * 70)
    print("CONFIRMED HIERARCHY RELATIONSHIPS  (A > B = A is a higher league)")
    print("=" * 70)
    print()

    for (higher, lower), evidence in sorted_rels:
        seasons_seen = sorted({e[0] for e in evidence})
        print(f"  {higher!r} > {lower!r}" f"  [{len(evidence)} obs, {len(seasons_seen)} season(s)]")

        by_season: dict[str, list[RelEntry]] = defaultdict(list)
        for entry in evidence:
            by_season[entry[0]].append(entry)

        for season in seasons_seen:
            entries = by_season[season]
            clubs_str = ", ".join(
                f"{e[1]} ({e[2]}v{e[3]})" for e in sorted(entries, key=lambda x: (x[1], x[2]))[:4]
            )
            print(f"    {season}:  {clubs_str}")
        print()

    # ------------------------------------------------------------------
    # Per-season tier reassignment using the global relationship graph.
    # ------------------------------------------------------------------
    edge_weight: dict[tuple[str, str], int] = {
        pair: len(ev) for pair, ev in all_relationships.items()
    }
    resolved_above = _resolve_global_edges(edge_weight)

    print("=" * 70)
    print("PROPOSED PER-SEASON TIERS  (derived from global shared-club graph)")
    print("=" * 70)
    print()

    for season in seasons:
        leagues = load_season(season)
        if not leagues:
            continue
        names = sorted({clean_league_name(stem) for stem in leagues})
        tiers = _assign_tiers(names, resolved_above)
        print(f"{season}:")
        for tier in sorted(set(tiers.values())):
            row = sorted([n for n, t2 in tiers.items() if t2 == tier])
            print(f"  Tier {tier}:  {', '.join(row)}")
        print()


def _resolve_global_edges(edge_weight: dict[tuple[str, str], int]) -> dict[str, set[str]]:
    """For each unordered pair, pick the dominant direction.

    Returns ``above[league] = {leagues confirmed above it}``.
    Ties (equal evidence both directions) produce no edge.
    """
    seen: set[frozenset[str]] = set()
    above: dict[str, set[str]] = defaultdict(set)
    for a, b in edge_weight:
        key = frozenset((a, b))
        if key in seen:
            continue
        seen.add(key)
        ab = edge_weight.get((a, b), 0)
        ba = edge_weight.get((b, a), 0)
        if ab > ba:
            above[b].add(a)
        elif ba > ab:
            above[a].add(b)
    return above


def _assign_tiers(names: list[str], above: dict[str, set[str]]) -> dict[str, int]:
    """Level-based topo-sort restricted to ``names``.

    Tier 1 = leagues with no other season-league above them in the global
    graph.  Tiers increment by 1 with no gaps.  Cycles (or absence of
    incoming-edge progress) collapse the remaining leagues into one tier.
    """
    remaining = set(names)
    tier_of: dict[str, int] = {}
    current_tier = 1
    while remaining:
        top = {n for n in remaining if not (above.get(n, set()) & remaining)}
        if not top:
            top = set(remaining)
        for n in top:
            tier_of[n] = current_tier
        remaining -= top
        current_tier += 1
    return tier_of


if __name__ == "__main__":
    main()
