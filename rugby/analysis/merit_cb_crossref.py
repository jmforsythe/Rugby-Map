"""Cross-reference merit competitions against RFU Constituent Body club affiliation.

For every (season, competition) pair, computes the majority CB of member clubs
(ground truth, from data/rugby/club_cb_mapping.json) and the empirical absolute
pyramid tier those same clubs' *principal* XVs actually occupy (ground truth, from
geocoded pyramid data). Compares both against the currently configured offset in
rugby.tiers to flag anchors that may be wrong.

Usage: python -m rugby.analysis.merit_cb_crossref [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from rugby.analysis.merit_pyramid_crossovers import SEASONS, load_dataset
from rugby.constituent_bodies import get_constituent_body
from rugby.tiers import get_competition_offset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args()

    ds = load_dataset(SEASONS)
    rows: list[dict] = []

    for season in SEASONS:
        placements = ds.placements.get(season, [])
        by_comp: dict[str, list] = defaultdict(list)
        for p in placements:
            if p.is_merit and p.comp:
                by_comp[p.comp].append(p)

        principal = ds.principal_tier.get(season, {})

        for comp, members in sorted(by_comp.items()):
            cb_votes = Counter()
            cb_misses = 0
            principal_tiers: list[int] = []
            for p in members:
                cb = get_constituent_body(p.team)
                if cb:
                    cb_votes[cb] += 1
                else:
                    cb_misses += 1
                pt = principal.get(p.club)
                if pt is not None:
                    principal_tiers.append(pt)

            top_cb, top_n = (cb_votes.most_common(1) or [(None, 0)])[0]
            total_votes = sum(cb_votes.values()) + cb_misses
            cb_share = top_n / total_votes if total_votes else 0.0

            apex_local = min((p.local_tier for p in members), default=None)
            offset = get_competition_offset(comp, season)
            apex_abs_current = apex_local + offset if apex_local is not None else None

            principal_mode = statistics.mode(principal_tiers) if principal_tiers else None
            principal_min = min(principal_tiers, default=None)

            rows.append(
                {
                    "season": season,
                    "comp": comp,
                    "n_members": len(members),
                    "top_cb": top_cb,
                    "cb_votes": dict(cb_votes.most_common(3)),
                    "cb_share": round(cb_share, 2),
                    "cb_misses": cb_misses,
                    "apex_local_tier": apex_local,
                    "current_offset": offset,
                    "apex_abs_current": apex_abs_current,
                    "principal_tiers_seen": len(principal_tiers),
                    "principal_mode_abs_tier": principal_mode,
                    "principal_min_abs_tier": principal_min,
                }
            )
            print(
                f"{season:10s} {comp:18s} apex_abs={apex_abs_current!s:>4s} "
                f"principal_mode={principal_mode!s:>4s} principal_min={principal_min!s:>4s} "
                f"CB={top_cb!r} ({cb_share:.0%}, n={len(members)})"
            )

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
