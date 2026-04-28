"""Travel-optimised league splits for projected men's tiers 4–6.

Loads the pool from ``promotion_relegation`` (``next_tier`` = chosen tier) or
``--teams-file``, splits into *num_leagues* equal-sized divisions, minimising
within-division pairwise Haversine distance. Channel Islands clubs follow the
same rules as the National 2 tool (shared league, zeroed legs in objective).

If ``--bpr`` is omitted, the five BPR promotions are read from
``data/rugby/projected_<next_season>.md`` when it contains ``**BPR resolved**``
(``promotion_relegation`` output). Use ``--no-projected-md`` or ``--projected-md PATH`` to control this.

Usage:
    python -m rugby.analysis.tier_travel_partition --tier 4
    python -m rugby.analysis.tier_travel_partition --tier 5 --num-leagues 6
    python -m rugby.analysis.tier_travel_partition --tier 6 --num-leagues 12 --restarts 30
    python -m rugby.analysis.tier_travel_partition --tier 4 --teams-file names.txt
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

import numpy as np

from rugby import DATA_DIR
from rugby.analysis.projected_urls import BASE_URL as CUSTOM_MAP_BASE_URL
from rugby.analysis.projected_urls import build_tier_url
from rugby.analysis.promotion_relegation import (
    parse_bpr_teams_from_projected_md,
    projected_markdown_path,
)

GEOCODED_ROOT = DATA_DIR / "geocoded_teams"

TIER_MIN = 4
TIER_MAX = 6
DEFAULT_NUM_LEAGUES_BY_TIER: dict[int, int] = {4: 3, 5: 6, 6: 12}
EXPECTED_POOL_BY_TIER: dict[int, int] = {4: 42, 5: 72, 6: 144}


def is_channel_islands_club(team_name: str) -> bool:
    n = team_name.lower()
    return "guernsey" in n or "jersey" in n


def channel_islands_indices(team_names: list[str]) -> list[int]:
    return [i for i, name in enumerate(team_names) if is_channel_islands_club(name)]


def distance_matrix_for_objective(full_dist: np.ndarray, ci_indices: list[int]) -> np.ndarray:
    d = full_dist.copy()
    if not ci_indices:
        return d
    for i in ci_indices:
        d[i, :] = 0.0
        d[:, i] = 0.0
    return d


def repair_channel_islands_same_group(assignment: np.ndarray, ci_indices: list[int]) -> None:
    if len(ci_indices) < 2:
        return
    n = len(assignment)
    g_target = int(assignment[ci_indices[0]])
    ci_set = set(ci_indices)
    for ci in ci_indices[1:]:
        while int(assignment[ci]) != g_target:
            partner: int | None = None
            for j in range(n):
                if j in ci_set:
                    continue
                if int(assignment[j]) == g_target:
                    partner = j
                    break
            if partner is None:
                raise RuntimeError(
                    "Could not place Channel Islands clubs in the same league; check league sizes."
                )
            assignment[ci], assignment[partner] = assignment[partner], assignment[ci]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371.0 * c


def load_teams_from_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def load_projected_tier_teams(
    season: str,
    tier: int,
    *,
    use_cache: bool = True,
    bpr_teams: list[str] | None = None,
    quiet: bool = False,
) -> list[str]:
    from rugby.analysis.promotion_relegation import _SEASON_SURVIVAL_SWAPS, compute_assignments

    assignments = compute_assignments(
        season,
        bpr_teams=bpr_teams,
        survival_swaps=_SEASON_SURVIVAL_SWAPS.get(season),
        quiet=quiet,
        scrape_standings=not use_cache,
    )
    names = [a["team_name"] for a in assignments if a["next_tier"] == tier]
    return sorted(names)


def index_coordinates_for_season(season: str) -> dict[str, tuple[float, float]]:
    base = GEOCODED_ROOT / season
    if not base.is_dir():
        raise FileNotFoundError(f"No geocoded dir: {base}")
    out: dict[str, tuple[float, float]] = {}
    for json_path in sorted(base.rglob("*.json")):
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        for t in data.get("teams", []):
            name = t.get("name")
            lat = t.get("latitude")
            lon = t.get("longitude")
            if name is None or lat is None or lon is None:
                continue
            out[str(name)] = (float(lat), float(lon))
    return out


def build_distance_matrix(coords: np.ndarray) -> np.ndarray:
    n = coords.shape[0]
    d = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            km = haversine_km(coords[i, 0], coords[i, 1], coords[j, 0], coords[j, 1])
            d[i, j] = km
            d[j, i] = km
    return d


def total_within_league_pairwise(assignment: np.ndarray, dist: np.ndarray) -> float:
    n = len(assignment)
    cost = 0.0
    for i in range(n):
        ai = assignment[i]
        for j in range(i + 1, n):
            if assignment[j] == ai:
                cost += dist[i, j]
    return cost


def _swap_keeps_ci_together(assignment: np.ndarray, i: int, j: int, ci_indices: list[int]) -> bool:
    if len(ci_indices) < 2:
        return True
    ai, aj = int(assignment[i]), int(assignment[j])
    groups: set[int] = set()
    for ci in ci_indices:
        if ci == i:
            groups.add(aj)
        elif ci == j:
            groups.add(ai)
        else:
            groups.add(int(assignment[ci]))
    return len(groups) <= 1


def swap_delta(assignment: np.ndarray, dist: np.ndarray, i: int, j: int) -> float:
    gi = int(assignment[i])
    gj = int(assignment[j])
    if gi == gj:
        return 0.0
    n = len(assignment)

    def sum_to_group(team: int, group: int, exclude: int) -> float:
        s = 0.0
        for k in range(n):
            if k == exclude:
                continue
            if int(assignment[k]) == group:
                s += dist[team, k]
        return s

    return (
        -sum_to_group(i, gi, i)
        + sum_to_group(i, gj, j)
        - sum_to_group(j, gj, j)
        + sum_to_group(j, gi, i)
    )


def local_search(
    assignment: np.ndarray,
    dist: np.ndarray,
    ci_indices: list[int],
) -> np.ndarray:
    n = len(assignment)
    a = assignment.copy()
    improved = True
    while improved:
        improved = False
        best_delta = 0.0
        best_pair: tuple[int, int] | None = None
        for i in range(n):
            for j in range(i + 1, n):
                if int(a[i]) == int(a[j]):
                    continue
                if not _swap_keeps_ci_together(a, i, j, ci_indices):
                    continue
                dlt = swap_delta(a, dist, i, j)
                if dlt < best_delta - 1e-9:
                    best_delta = dlt
                    best_pair = (i, j)
        if best_pair is not None:
            bi, bj = best_pair
            a[bi], a[bj] = a[bj], a[bi]
            improved = True
    return a


def balanced_random_assignment(
    n: int,
    num_leagues: int,
    league_size: int,
    rng: random.Random,
    ci_indices: list[int],
) -> np.ndarray:
    a = np.zeros(n, dtype=np.int8)
    if not ci_indices:
        idx = list(range(n))
        rng.shuffle(idx)
        for g in range(num_leagues):
            for k in range(league_size):
                a[idx[g * league_size + k]] = g
        return a

    ci_set = set(ci_indices)
    others = [i for i in range(n) if i not in ci_set]
    rng.shuffle(others)
    g_ci = rng.randrange(num_leagues)
    need_mainland = league_size - len(ci_indices)
    for idx in ci_indices:
        a[idx] = g_ci
    take_ci_group = others[:need_mainland]
    rest = others[need_mainland:]
    for idx in take_ci_group:
        a[idx] = g_ci
    other_groups = [g for g in range(num_leagues) if g != g_ci]
    offset = 0
    for g in other_groups:
        for k in range(league_size):
            a[rest[offset + k]] = g
        offset += league_size
    return a


def longitude_band_assignment(
    coords: np.ndarray,
    num_leagues: int,
    league_size: int,
    ci_indices: list[int],
) -> np.ndarray:
    n = coords.shape[0]
    order = np.argsort(coords[:, 1])
    a = np.zeros(n, dtype=np.int8)
    for g in range(num_leagues):
        for k in range(league_size):
            a[int(order[g * league_size + k])] = g
    if ci_indices:
        repair_channel_islands_same_group(a, ci_indices)
    return a


def optimise_partition(
    dist_eff: np.ndarray,
    coords: np.ndarray,
    num_leagues: int,
    league_size: int,
    restarts: int,
    seed: int,
    ci_indices: list[int],
) -> tuple[np.ndarray, float]:
    rng = random.Random(seed)
    n = dist_eff.shape[0]
    best_a: np.ndarray | None = None
    best_c = float("inf")

    starters = [longitude_band_assignment(coords, num_leagues, league_size, ci_indices)]
    for _ in range(restarts):
        starters.append(balanced_random_assignment(n, num_leagues, league_size, rng, ci_indices))

    for init in starters:
        refined = local_search(init, dist_eff, ci_indices)
        c = total_within_league_pairwise(refined, dist_eff)
        if c < best_c:
            best_c = c
            best_a = refined

    assert best_a is not None
    return best_a, best_c


def rfu_national_two_labels(
    assignment: np.ndarray,
    coords: np.ndarray,
    ci_indices: list[int],
) -> dict[int, str]:
    num_leagues = 3
    ci_set = set(ci_indices)
    groups = {g: np.where(assignment == g)[0] for g in range(num_leagues)}
    mean_lat: dict[int, float] = {}
    mean_lon: dict[int, float] = {}
    for g, idx in groups.items():
        mainland = np.array([i for i in idx.tolist() if i not in ci_set], dtype=np.int64)
        if len(mainland) == 0:
            mainland = idx
        mean_lat[g] = float(coords[mainland, 0].mean())
        mean_lon[g] = float(coords[mainland, 1].mean())
    north_g = max(mean_lat, key=lambda x: mean_lat[x])
    others = [g for g in range(num_leagues) if g != north_g]
    west_g = min(others, key=lambda x: mean_lon[x])
    east_g = next(g for g in others if g != west_g)
    return {
        north_g: "National League 2 North",
        west_g: "National League 2 West",
        east_g: "National League 2 East",
    }


def geographic_group_labels(
    assignment: np.ndarray,
    coords: np.ndarray,
    ci_indices: list[int],
    num_leagues: int,
    tier: int,
) -> dict[int, str]:
    ci_set = set(ci_indices)
    by_lon: list[tuple[float, int]] = []
    for g in range(num_leagues):
        idx = np.where(assignment == g)[0]
        mainland = [i for i in idx.tolist() if i not in ci_set]
        use = mainland if mainland else idx.tolist()
        mlon = float(coords[np.array(use), 1].mean())
        by_lon.append((mlon, g))
    by_lon.sort(key=lambda x: x[0])
    labels: dict[int, str] = {}
    for i, (_, g) in enumerate(by_lon):
        labels[g] = f"Tier {tier} - League {i + 1} (west to east {i + 1}/{num_leagues})"
    return labels


def build_labels(
    assignment: np.ndarray,
    coords: np.ndarray,
    ci_indices: list[int],
    num_leagues: int,
    tier: int,
) -> dict[int, str]:
    if tier == 4 and num_leagues == 3:
        return rfu_national_two_labels(assignment, coords, ci_indices)
    return geographic_group_labels(assignment, coords, ci_indices, num_leagues, tier)


def league_order_west_to_east(
    assignment: np.ndarray,
    coords: np.ndarray,
    ci_indices: list[int],
    num_leagues: int,
) -> list[int]:
    ci_set = set(ci_indices)
    by_lon: list[tuple[float, int]] = []
    for g in range(num_leagues):
        idx = np.where(assignment == g)[0]
        mainland = [i for i in idx.tolist() if i not in ci_set]
        use = mainland if mainland else idx.tolist()
        mlon = float(coords[np.array(use), 1].mean())
        by_lon.append((mlon, g))
    by_lon.sort(key=lambda x: x[0])
    return [g for _, g in by_lon]


def custom_map_leagues_ordered(
    team_names: list[str],
    assignment: np.ndarray,
    labels_by_group: dict[int, str],
    pool_size: int,
    print_order: list[int],
) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for g in print_order:
        label = labels_by_group[g]
        members = sorted(team_names[i] for i in range(pool_size) if int(assignment[i]) == g)
        out.append((label, members))
    return out


def print_report(
    team_names: list[str],
    assignment: np.ndarray,
    dist_full: np.ndarray,
    dist_eff: np.ndarray,
    labels_by_group: dict[int, str],
    ci_indices: list[int],
    print_order: list[int],
) -> None:
    n = len(team_names)
    total_obj = total_within_league_pairwise(assignment, dist_eff)
    total_hav = total_within_league_pairwise(assignment, dist_full)
    print(f"\nOptimised objective (excl. pairs with Channel Islands): {total_obj:,.1f} km")
    if ci_indices:
        names = ", ".join(team_names[i] for i in ci_indices)
        print(f"  Channel Islands (same league; omitted from objective): {names}")
    print(f"All-pairs Haversine within leagues: {total_hav:,.1f} km")
    print(f"(Round-robin on optimised road legs ~ {2.0 * total_obj:,.1f} km)\n")

    for g in print_order:
        label = labels_by_group[g]
        members = [i for i in range(n) if int(assignment[i]) == g]
        sub = 0.0
        for ii, i in enumerate(members):
            for j in members[ii + 1 :]:
                sub += dist_full[i, j]
        sub_obj = 0.0
        for ii, i in enumerate(members):
            for j in members[ii + 1 :]:
                sub_obj += dist_eff[i, j]
        pairs = len(members) * (len(members) - 1) // 2
        print(f"## {label} ({len(members)} teams)")
        print(
            f"   Within-league pairwise sum: {sub:,.1f} km Haversine "
            f"({sub_obj:,.1f} km objective excl. island legs)  ({pairs} pairs)"
        )
        for i in sorted(members, key=lambda idx: team_names[idx]):
            print(f"   - {team_names[i]}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Partition projected tier 4–6 clubs into equal leagues for minimum travel.",
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=[4, 5, 6],
        default=4,
        help="Projected next_tier / level (default: 4 = National 2)",
    )
    parser.add_argument(
        "--num-leagues",
        type=int,
        default=None,
        metavar="N",
        help="Number of divisions (default: 3 for tier 4, 6 for tier 5, 12 for tier 6)",
    )
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Standings season + geocoded_teams/<season>/ (default: %(default)s)",
    )
    parser.add_argument(
        "--teams-file",
        type=Path,
        default=None,
        help="Optional: team names, one per line (skip projection; count must match leagues x size)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Fetch table order from RFU instead of geocoded_teams JSON",
    )
    parser.add_argument(
        "--bpr",
        nargs="+",
        metavar="TEAM",
        default=None,
        help="Counties 1 BPR winners (overrides BPR line in projected markdown)",
    )
    parser.add_argument(
        "--projected-md",
        type=Path,
        default=None,
        metavar="PATH",
        help="Projected markdown with **BPR resolved** (default: data/rugby/projected_<next_season>.md)",
    )
    parser.add_argument(
        "--no-projected-md",
        action="store_true",
        help="Do not read BPR from projected markdown when --bpr is omitted",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Silence per-league cache/scrape messages when loading projection",
    )
    parser.add_argument(
        "--restarts",
        type=int,
        default=80,
        help="Random starts (tier 6 is slow; try 20-40) (default: %(default)s)",
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default: %(default)s)")
    parser.add_argument("--json-out", type=Path, default=None, help="Write assignment JSON")
    parser.add_argument(
        "--custom-map-base",
        default=CUSTOM_MAP_BASE_URL,
        metavar="URL",
        help=f"Custom map site URL ending in / (default: {CUSTOM_MAP_BASE_URL})",
    )
    args = parser.parse_args()

    tier = args.tier
    num_leagues = (
        args.num_leagues if args.num_leagues is not None else DEFAULT_NUM_LEAGUES_BY_TIER[tier]
    )

    bpr_teams = args.bpr
    bpr_from_markdown: Path | None = None
    if args.teams_file is None and bpr_teams is None and not args.no_projected_md:
        md_path = (
            args.projected_md
            if args.projected_md is not None
            else projected_markdown_path(args.season)
        )
        if args.projected_md is not None and not md_path.is_file():
            parser.error(f"--projected-md not found: {md_path}")
        if md_path.is_file():
            parsed = parse_bpr_teams_from_projected_md(md_path)
            if parsed:
                bpr_teams = parsed
                bpr_from_markdown = md_path
                if not args.quiet:
                    print(f"BPR from {md_path.name}: {', '.join(bpr_teams)}")

    if args.teams_file is not None:
        team_names = load_teams_from_file(args.teams_file)
        from_file = True
    else:
        team_names = load_projected_tier_teams(
            args.season,
            tier,
            use_cache=not args.no_cache,
            bpr_teams=bpr_teams,
            quiet=args.quiet,
        )
        from_file = False

    team_names = sorted(team_names)

    pool_size = len(team_names)
    if pool_size == 0:
        raise ValueError(f"No teams projected to tier {tier}. Check season and projection inputs.")

    if (
        not from_file
        and tier == 6
        and bpr_teams is None
        and pool_size == EXPECTED_POOL_BY_TIER.get(6, 0) - 5
    ):
        print(
            "Note: Tier 6 has 139 teams: BPR not applied. "
            "Regenerate projected markdown with promotion_relegation --bpr, or pass --bpr here, "
            "or set --projected-md to a file that contains **BPR resolved**."
        )

    if pool_size % num_leagues != 0:
        exp = EXPECTED_POOL_BY_TIER.get(tier)
        hint = f" Expected {exp} for a full pyramid tier {tier}." if exp else ""
        bpr_hint = ""
        if tier == 6 and bpr_teams is None and exp == 144 and pool_size == 139:
            bpr_hint = " 139 teams: add BPR via projected .md or --bpr."
        raise ValueError(
            f"Pool size {pool_size} is not divisible by --num-leagues {num_leagues}.{hint}{bpr_hint} "
            "Use --teams-file or fix projection (e.g. --bpr)."
        )
    league_size = pool_size // num_leagues

    if not from_file and EXPECTED_POOL_BY_TIER.get(tier) not in (None, pool_size):
        print(
            f"Note: tier {tier} pool has {pool_size} teams "
            f"(expected {EXPECTED_POOL_BY_TIER[tier]} when complete)."
        )

    coord_map = index_coordinates_for_season(args.season)
    missing = [t for t in team_names if t not in coord_map]
    if missing:
        raise ValueError(
            f"No coordinates for {len(missing)} team(s): {', '.join(missing[:10])}"
            + (" …" if len(missing) > 10 else "")
        )

    coords = np.array([coord_map[t] for t in team_names], dtype=np.float64)
    ci_indices = channel_islands_indices(team_names)
    dist_full = build_distance_matrix(coords)
    dist_eff = distance_matrix_for_objective(dist_full, ci_indices)

    assignment, cost = optimise_partition(
        dist_eff,
        coords,
        num_leagues,
        league_size,
        args.restarts,
        args.seed,
        ci_indices,
    )

    labels_by_group = build_labels(assignment, coords, ci_indices, num_leagues, tier)
    print_order = league_order_west_to_east(assignment, coords, ci_indices, num_leagues)

    leagues_for_map = custom_map_leagues_ordered(
        team_names, assignment, labels_by_group, pool_size, print_order
    )
    custom_map_url = build_tier_url(leagues_for_map, args.custom_map_base, tier)

    src = "file" if from_file else f"projected tier {tier}"
    print(
        f"Season: {args.season}  |  tier: {tier}  |  {num_leagues} leagues x {league_size}  |  "
        f"pool: {src}  |  restarts: {args.restarts}  |  seed: {args.seed}"
    )
    print_report(
        team_names, assignment, dist_full, dist_eff, labels_by_group, ci_indices, print_order
    )
    print(f"Custom map: {custom_map_url}")

    if args.json_out is not None:
        leagues_out = []
        for g in print_order:
            members = sorted(
                [team_names[i] for i in range(pool_size) if int(assignment[i]) == g],
            )
            leagues_out.append({"label": labels_by_group[g], "teams": members})
        cost_full = total_within_league_pairwise(assignment, dist_full)
        payload: dict = {
            "season": args.season,
            "tier": tier,
            "num_leagues": num_leagues,
            "league_size": league_size,
            "pool_size": pool_size,
            "pool_source": "teams_file" if from_file else f"projected_next_tier_{tier}",
            "team_substitutions": {},
            "objective": "min_sum_within_league_pairwise_km_excluding_channel_islands_legs",
            "channel_islands_teams": [team_names[i] for i in ci_indices],
            "total_pairwise_km_optimised": round(cost, 3),
            "total_pairwise_km_haversine_full": round(cost_full, 3),
            "approx_round_robin_optimised_road_legs_km": round(2.0 * cost, 3),
            "custom_map_url": custom_map_url,
            "leagues": leagues_out,
        }
        if not from_file:
            payload["projection"] = {
                "standings_season": args.season,
                "bpr_teams": list(bpr_teams) if bpr_teams else [],
                "bpr_from_markdown": str(bpr_from_markdown) if bpr_from_markdown else None,
            }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Wrote {args.json_out}")


if __name__ == "__main__":
    main()
