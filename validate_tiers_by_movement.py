"""
Validate league tiers by analysing team movement between adjacent seasons.

Builds a weighted graph where nodes are leagues and edges represent team
movements between them. Each team that moves from league A to league B (or
vice-versa) adds 1 to the edge weight. Tier is then inferred by calculating
the shortest path (hop count) from known anchor leagues (Premiership,
Championship, National League 1).

Sponsor name changes between seasons are detected and collapsed so they don't
add spurious hops to the graph.

Usage:
    python validate_tiers_by_movement.py 2024-2025 2025-2026
    python validate_tiers_by_movement.py --all
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import NamedTuple

from tier_extraction import extract_tier, get_competition_offset
from utils import setup_logging

logger = logging.getLogger(__name__)

GEOCODED_DIR = Path("geocoded_teams")

WOMENS_MIN_TIER = 100
UNKNOWN_TIER = 999

RENAME_THRESHOLD = 0.6


class LeagueInfo(NamedTuple):
    league_name: str
    tier_num: int
    tier_name: str
    rel_path: str


class Movement(NamedTuple):
    team: str
    from_league: str
    to_league: str


class PairResult(NamedTuple):
    season_a: str
    season_b: str
    num_edges: int
    num_moved: int
    num_renames: int
    matches: list[tuple[str, int]]
    mismatches: list[tuple[str, int, int, str]]
    unconnected: list[tuple[str, int]]


def load_season(season: str) -> dict[str, list[LeagueInfo]]:
    """Return {team_name: [LeagueInfo, ...]} for a single season."""
    season_dir = GEOCODED_DIR / season
    if not season_dir.is_dir():
        logger.error("Season directory not found: %s", season_dir)
        sys.exit(1)

    teams: dict[str, list[LeagueInfo]] = defaultdict(list)

    for filepath in sorted(season_dir.rglob("*.json")):
        rel = filepath.relative_to(season_dir).as_posix()
        tier_num, tier_name = extract_tier(rel, season)

        parts = rel.split("/")
        if len(parts) >= 3 and parts[0] == "merit":
            tier_num += get_competition_offset(parts[1], season)

        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        league_name = data.get("league_name", filepath.stem)
        info = LeagueInfo(league_name, tier_num, tier_name, rel)
        for team in data.get("teams", []):
            teams[team["name"]].append(info)

    return teams


def _is_mens_pyramid(entry: LeagueInfo) -> bool:
    return entry.tier_num < WOMENS_MIN_TIER and entry.tier_num != UNKNOWN_TIER


def _league_sizes(
    teams: dict[str, list[LeagueInfo]],
) -> dict[str, int]:
    """Count teams per men's pyramid league."""
    sizes: dict[str, int] = defaultdict(int)
    for entries in teams.values():
        for e in entries:
            if _is_mens_pyramid(e):
                sizes[e.league_name] += 1
    return sizes


def detect_renames(
    teams_a: dict[str, list[LeagueInfo]],
    teams_b: dict[str, list[LeagueInfo]],
    edges: dict[tuple[str, str], int],
) -> dict[str, str]:
    """Detect league renames between seasons.

    A rename is suspected when both endpoints only exist in one season and the
    edge weight accounts for a large fraction of the smaller league's size.

    Returns {old_name: new_name}.
    """
    sizes_a = _league_sizes(teams_a)
    sizes_b = _league_sizes(teams_b)

    only_a = set(sizes_a) - set(sizes_b)
    only_b = set(sizes_b) - set(sizes_a)

    renames: dict[str, str] = {}
    used_new: set[str] = set()

    for (la, lb), weight in sorted(edges.items(), key=lambda x: -x[1]):
        if la in only_a and lb in only_b:
            old, new = la, lb
        elif lb in only_a and la in only_b:
            old, new = lb, la
        else:
            continue

        if old in renames or new in used_new:
            continue

        size_old = sizes_a.get(old, sizes_b.get(old, 0))
        size_new = sizes_b.get(new, sizes_a.get(new, 0))
        min_size = min(size_old, size_new)

        if min_size > 0 and weight >= RENAME_THRESHOLD * min_size:
            renames[old] = new
            used_new.add(new)

    return renames


def _apply_renames(
    edges: dict[tuple[str, str], int],
    renames: dict[str, str],
) -> dict[tuple[str, str], int]:
    """Rebuild edges with renamed leagues collapsed into canonical names."""
    merged: dict[tuple[str, str], int] = defaultdict(int)
    for (a, b), weight in edges.items():
        ca = renames.get(a, a)
        cb = renames.get(b, b)
        if ca == cb:
            continue
        key = (min(ca, cb), max(ca, cb))
        merged[key] += weight
    return merged


def build_movement_graph(
    teams_a: dict[str, list[LeagueInfo]],
    teams_b: dict[str, list[LeagueInfo]],
) -> tuple[dict[tuple[str, str], int], list[Movement]]:
    """Build a weighted movement graph from two seasons.

    Returns:
        edges: {(league_a, league_b): weight} with canonical key ordering
        movements: list of individual team movements
    """
    edges: dict[tuple[str, str], int] = defaultdict(int)
    movements: list[Movement] = []

    for team in sorted(set(teams_a) & set(teams_b)):
        leagues_a = {e.league_name for e in teams_a[team] if _is_mens_pyramid(e)}
        leagues_b = {e.league_name for e in teams_b[team] if _is_mens_pyramid(e)}

        for la in leagues_a:
            for lb in leagues_b:
                if la != lb:
                    key = (min(la, lb), max(la, lb))
                    edges[key] += 1
                    movements.append(Movement(team, la, lb))

    return edges, movements


def build_adjacency(edges: dict[tuple[str, str], int]) -> dict[str, dict[str, int]]:
    """Convert edge dict to adjacency list."""
    adj: dict[str, dict[str, int]] = defaultdict(dict)
    for (a, b), weight in edges.items():
        adj[a][b] = weight
        adj[b][a] = weight
    return adj


def bfs_distances(adj: dict[str, dict[str, int]], start: str) -> dict[str, int]:
    """BFS shortest-path hop counts from *start*."""
    if start not in adj:
        return {}
    distances: dict[str, int] = {start: 0}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in adj[node]:
            if neighbor not in distances:
                distances[neighbor] = distances[node] + 1
                queue.append(neighbor)
    return distances


def infer_tiers(adj: dict[str, dict[str, int]], season: str) -> dict[str, tuple[int, str]]:
    """Infer tiers via shortest path from anchor leagues.

    Returns {league_name: (inferred_tier, anchor_used)}.
    """
    anchor_specs: list[tuple[list[str], int]] = [
        (["Premiership"], 1),
        (["Championship"], 2),
        (["National League 1", "National League One"], 2 if season < "2009-2010" else 3),
    ]

    anchors: list[tuple[str, int]] = []
    for candidates, base_tier in anchor_specs:
        for name in candidates:
            if name in adj:
                anchors.append((name, base_tier))
                break

    inferred: dict[str, tuple[int, str]] = {}
    for anchor_name, anchor_tier in anchors:
        distances = bfs_distances(adj, anchor_name)
        for league, dist in distances.items():
            tier = anchor_tier + dist
            if league not in inferred or tier < inferred[league][0]:
                inferred[league] = (tier, anchor_name)

    return inferred


def get_available_seasons() -> list[str]:
    """Return sorted list of available season directories."""
    if not GEOCODED_DIR.is_dir():
        return []
    return sorted(d.name for d in GEOCODED_DIR.iterdir() if d.is_dir() and "-" in d.name)


def _collect_file_tiers(
    *season_data: dict[str, list[LeagueInfo]],
) -> dict[str, tuple[int, str]]:
    """Collect filename-derived tiers for all men's pyramid leagues.

    Returns {league_name: (tier_num, rel_path)}.
    """
    tiers: dict[str, tuple[int, str]] = {}
    for teams in season_data:
        for entries in teams.values():
            for entry in entries:
                if _is_mens_pyramid(entry) and entry.league_name not in tiers:
                    tiers[entry.league_name] = (entry.tier_num, entry.rel_path)
    return tiers


def analyse_pair(
    season_a: str,
    season_b: str,
    *,
    verbose: bool = False,
    show_movements: bool = False,
) -> PairResult:
    """Analyse one pair of adjacent seasons. Optionally print detailed output."""
    teams_a = load_season(season_a)
    teams_b = load_season(season_b)

    raw_edges, movements = build_movement_graph(teams_a, teams_b)
    renames = detect_renames(teams_a, teams_b, raw_edges)
    edges = _apply_renames(raw_edges, renames)
    adj = build_adjacency(edges)

    # Also rename keys in file_tiers so they match the collapsed graph
    file_tiers_raw = _collect_file_tiers(teams_a, teams_b)
    file_tiers: dict[str, tuple[int, str]] = {}
    for name, (tier, rel) in file_tiers_raw.items():
        canonical = renames.get(name, name)
        if canonical not in file_tiers:
            file_tiers[canonical] = (tier, rel)

    inferred = infer_tiers(adj, season_a)
    common = set(teams_a) & set(teams_b)
    moved_teams = {m.team for m in movements}

    all_leagues = sorted(set(file_tiers) | set(inferred))
    mismatches: list[tuple[str, int, int, str]] = []
    matches: list[tuple[str, int]] = []
    unconnected: list[tuple[str, int]] = []

    for league in all_leagues:
        ft_entry = file_tiers.get(league)
        gt = inferred.get(league)
        if ft_entry is not None and gt is not None:
            ft = ft_entry[0]
            if ft == gt[0]:
                matches.append((league, ft))
            else:
                mismatches.append((league, ft, gt[0], gt[1]))
        elif ft_entry is not None:
            unconnected.append((league, ft_entry[0]))

    if verbose:
        print(f"\n{'=' * 80}")
        print(f"MOVEMENT GRAPH: {season_a} -> {season_b}")
        print(f"{'=' * 80}")
        print(f"  Leagues (nodes):           {len(adj)}")
        print(f"  Connections (edges):       {len(edges)}")
        print(f"  Teams in both seasons:     {len(common)}")
        print(f"  Teams that changed league: {len(moved_teams)}")
        print(f"  Renames detected:          {len(renames)}")

        if renames:
            print(f"\n  {'DETECTED RENAMES':-^78}")
            for old, new in sorted(renames.items()):
                w = raw_edges.get((min(old, new), max(old, new)), 0)
                print(f"  {old} -> {new}  (weight {w})")

        if show_movements and movements:
            print(f"\n{'=' * 80}")
            print("INDIVIDUAL TEAM MOVEMENTS")
            print(f"{'=' * 80}")
            for m in sorted(movements, key=lambda m: m.team):
                print(f"  {m.team:<35} {m.from_league} -> {m.to_league}")

        print(f"\n{'=' * 80}")
        print("EDGES (sorted by weight)")
        print(f"{'=' * 80}")
        sorted_edges = sorted(edges.items(), key=lambda x: (-x[1], x[0]))
        print(f"  {'League A':<38} {'League B':<38} {'Weight':>6}")
        print(f"  {'-' * 84}")
        for (a, b), weight in sorted_edges:
            print(f"  {a:<38} {b:<38} {weight:>6}")

        print(f"\n{'=' * 80}")
        print("TIER COMPARISON: graph-inferred vs filename-derived")
        print(f"{'=' * 80}")
        print(f"  Matches:     {len(matches)}")
        print(f"  Mismatches:  {len(mismatches)}")
        print(f"  Unconnected: {len(unconnected)}  (no path to any anchor league)")

        if mismatches:
            mismatches_sorted = sorted(mismatches, key=lambda x: (-abs(x[2] - x[1]), x[0]))
            print(f"\n  {'MISMATCHES':-^78}")
            print(f"  {'League':<40} {'File':>5} {'Graph':>6} {'Diff':>5}  {'Anchor'}")
            print(f"  {'-' * 78}")
            for league, ft, gt, anchor in mismatches_sorted:
                diff = gt - ft
                sign = "+" if diff > 0 else ""
                print(f"  {league:<40} {ft:>5} {gt:>6} {sign}{diff:>4}  (via {anchor})")

        if matches:
            matches_sorted = sorted(matches, key=lambda x: x[1])
            print(f"\n  {'MATCHES':-^78}")
            print(f"  {'League':<40} {'Tier':>5}")
            print(f"  {'-' * 46}")
            for league, ft in matches_sorted:
                print(f"  {league:<40} {ft:>5}")

        if unconnected:
            unconnected_sorted = sorted(unconnected, key=lambda x: x[1])
            print(f"\n  {'UNCONNECTED LEAGUES':-^78}")
            print(f"  {'League':<40} {'File Tier':>10}")
            print(f"  {'-' * 52}")
            for league, ft in unconnected_sorted:
                print(f"  {league:<40} {ft:>10}")

    return PairResult(
        season_a=season_a,
        season_b=season_b,
        num_edges=len(edges),
        num_moved=len(moved_teams),
        num_renames=len(renames),
        matches=matches,
        mismatches=mismatches,
        unconnected=unconnected,
    )


def analyse_all(seasons: list[str]) -> None:
    """Run every adjacent season pair and aggregate results."""
    pairs = list(zip(seasons, seasons[1:], strict=False))
    logger.info("Analysing %d adjacent season pairs ...", len(pairs))

    results: list[PairResult] = []
    # (league, file_tier, graph_tier, anchor, season_pair)
    all_mismatches: list[tuple[str, int, int, str, str]] = []

    for sa, sb in pairs:
        logger.info("  %s -> %s", sa, sb)
        r = analyse_pair(sa, sb, verbose=False)
        results.append(r)
        pair_label = f"{sa} -> {sb}"
        for league, ft, gt, anchor in r.mismatches:
            all_mismatches.append((league, ft, gt, anchor, pair_label))

    # ── Per-pair summary ─────────────────────────────────────────────────
    print(f"\n{'=' * 100}")
    print("SEASON PAIR SUMMARY")
    print(f"{'=' * 100}")
    print(
        f"  {'Pair':<27} {'Edges':>6} {'Moved':>6} {'Renames':>8}"
        f" {'Match':>6} {'Mis':>6} {'Unconn':>7} {'Rate':>6}"
    )
    print(f"  {'-' * 94}")
    for r in results:
        total = len(r.matches) + len(r.mismatches)
        rate = f"{len(r.matches)/total*100:.0f}%" if total else "n/a"
        print(
            f"  {r.season_a} -> {r.season_b:<10} {r.num_edges:>6} {r.num_moved:>6}"
            f" {r.num_renames:>8} {len(r.matches):>6} {len(r.mismatches):>6}"
            f" {len(r.unconnected):>7} {rate:>6}"
        )

    if not all_mismatches:
        print("\nNo mismatches found across any season pair.")
        return

    # ── Aggregate: group mismatches by league ────────────────────────────
    # For each league, collect all (file_tier, graph_tier, pair) observations
    league_obs: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for league, ft, gt, _anchor, pair in all_mismatches:
        league_obs[league].append((ft, gt, pair))

    # ── Aggregate: group by (file_tier, diff) to find systematic offsets ─
    tier_diff_counts: dict[tuple[int, int], int] = defaultdict(int)
    tier_diff_leagues: dict[tuple[int, int], set[str]] = defaultdict(set)
    for league, ft, gt, _anchor, _pair in all_mismatches:
        diff = gt - ft
        tier_diff_counts[(ft, diff)] += 1
        tier_diff_leagues[(ft, diff)].add(league)

    print(f"\n{'=' * 100}")
    print("SYSTEMATIC TIER OFFSETS")
    print("(file_tier, diff) combinations seen across all pairs, sorted by frequency")
    print(f"{'=' * 100}")
    print(
        f"  {'File Tier':>10} {'Graph Tier':>11} {'Diff':>5} {'Occurrences':>12}"
        f" {'Leagues':>8}  Examples"
    )
    print(f"  {'-' * 96}")
    for (ft, diff), count in sorted(
        tier_diff_counts.items(),
        key=lambda x: -x[1],
    ):
        leagues = tier_diff_leagues[(ft, diff)]
        examples = ", ".join(sorted(leagues)[:3])
        if len(leagues) > 3:
            examples += f" (+{len(leagues)-3} more)"
        sign = "+" if diff > 0 else ""
        print(
            f"  {ft:>10} {ft+diff:>11} {sign}{diff:>4} {count:>12}"
            f" {len(leagues):>8}  {examples}"
        )

    # ── Leagues with most persistent mismatches ──────────────────────────
    persistent = [(league, obs) for league, obs in league_obs.items() if len(obs) >= 3]
    persistent.sort(key=lambda x: -len(x[1]))

    print(f"\n{'=' * 100}")
    print(f"PERSISTENT MISMATCHES (leagues wrong in >=3 season pairs)  [{len(persistent)} found]")
    print(f"{'=' * 100}")

    if not persistent:
        print("  (none)")
    else:
        print(
            f"  {'League':<50} {'Count':>6} {'Typical File':>13} {'Typical Graph':>14} {'Diff':>5}"
        )
        print(f"  {'-' * 90}")
        for league, obs in persistent:
            ft_mode = max(
                {ft for ft, _, _ in obs}, key=lambda x: sum(1 for ft2, _, _ in obs if ft2 == x)
            )
            gt_mode = max(
                {gt for _, gt, _ in obs}, key=lambda x: sum(1 for _, gt2, _ in obs if gt2 == x)
            )
            diff = gt_mode - ft_mode
            sign = "+" if diff > 0 else ""
            print(f"  {league:<50} {len(obs):>6} {ft_mode:>13} {gt_mode:>14} {sign}{diff:>4}")

    # ── Suspected incorrect tiers ────────────────────────────────────────
    # A tier assignment is "suspected incorrect" if:
    # - The league has been a mismatch in >= 2 season pairs
    # - The diff is consistent (same sign) across observations
    # - |diff| >= 2
    print(f"\n{'=' * 100}")
    print("SUSPECTED INCORRECT TIER ASSIGNMENTS")
    print("(wrong in >=2 pairs, consistent direction, |diff| >= 2)")
    print(f"{'=' * 100}")

    suspects: list[tuple[str, int, int, int, list[str]]] = []
    for league, obs in league_obs.items():
        if len(obs) < 2:
            continue
        diffs = [gt - ft for ft, gt, _ in obs]
        if all(d > 0 for d in diffs) or all(d < 0 for d in diffs):
            median_diff = sorted(diffs)[len(diffs) // 2]
            if abs(median_diff) >= 2:
                ft_mode = max(
                    {ft for ft, _, _ in obs},
                    key=lambda x: sum(1 for ft2, _, _ in obs if ft2 == x),
                )
                pairs_affected = [p for _, _, p in obs]
                suspects.append((league, ft_mode, ft_mode + median_diff, len(obs), pairs_affected))

    suspects.sort(key=lambda x: (-x[3], -abs(x[2] - x[1]), x[0]))

    if not suspects:
        print("  (none)")
    else:
        print(f"  {'League':<50} {'File':>5} {'Suggested':>10} {'Pairs':>6}")
        print(f"  {'-' * 73}")
        for league, ft, suggested, count, _pairs_list in suspects:
            print(f"  {league:<50} {ft:>5} {suggested:>10} {count:>6}")


def main() -> None:
    setup_logging()

    available = get_available_seasons()

    parser = argparse.ArgumentParser(
        description="Validate league tiers using team movement between adjacent seasons.",
    )
    parser.add_argument("season_a", nargs="?", help="First season (e.g. 2024-2025)")
    parser.add_argument("season_b", nargs="?", help="Second season (e.g. 2025-2026)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run every adjacent season pair and show aggregate results",
    )
    parser.add_argument(
        "--show-movements",
        action="store_true",
        help="Print every individual team movement (single-pair mode only)",
    )
    args = parser.parse_args()

    if args.all:
        if len(available) < 2:
            logger.error("Need at least 2 seasons. Found: %d", len(available))
            sys.exit(1)
        analyse_all(available)
        return

    if not args.season_a or not args.season_b:
        parser.error("Provide two seasons, or use --all")

    for season in (args.season_a, args.season_b):
        if season not in available:
            logger.error(
                "Season %s not found. Available: %s",
                season,
                ", ".join(available),
            )
            sys.exit(1)

    result = analyse_pair(
        args.season_a,
        args.season_b,
        verbose=True,
        show_movements=args.show_movements,
    )
    total = len(result.matches) + len(result.mismatches)
    if total:
        print(
            f"\nOverall match rate: {len(result.matches)}/{total}"
            f" ({len(result.matches)/total*100:.1f}%)"
        )


if __name__ == "__main__":
    main()
