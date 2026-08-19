"""Interactively review every merit competition's apex parent, season by season.

For each (season, competition) pair, shows the competition's real RFU Constituent
Body (from data/rugby/club_cb_mapping.json via rugby.constituent_bodies) and offers
a menu of that CB's own pyramid leagues -- across every tier they appear at -- as
candidate parents for the merit ladder's apex.

Picking a candidate immediately writes the parent link to
data/rugby/tier_mappings/<season>.json (the same field rugby.pyramid_image reads)
and appends the implied rugby.tiers offset to data/rugby/_merit_review_offsets.json
for later merging into rugby/tiers.py's _SEASON_OFFSETS (edited by hand, since this
script only ever touches JSON, never .py source).

A pair is auto-skipped once its apex already has a parent set in tier_mappings (the
source of truth), regardless of whether this script or something else set it, and
regardless of the separate data/rugby/_merit_review_state.json (which only tracks
explicit Enter/k skips of *unset* pairs so those get skipped too on the next run).
Pass --redo to re-visit pairs that already have a parent.

When a nearby season for the same competition already has a resolved apex parent
whose league name(s) still exist this season, it's offered as a quick "[0]" pick.

Usage:
    python scripts/interactive_merit_parent_review.py
    python scripts/interactive_merit_parent_review.py --season 2025-2026 --season 2026-2027
    python scripts/interactive_merit_parent_review.py --comp Sussex
    python scripts/interactive_merit_parent_review.py --redo --season 2022-2023
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rugby import DATA_DIR  # noqa: E402
from rugby.analysis.merit_pyramid_crossovers import SEASONS, load_dataset  # noqa: E402
from rugby.constituent_bodies import get_constituent_body  # noqa: E402
from rugby.tiers import get_competition_offset  # noqa: E402

TIER_MAPPINGS_DIR = DATA_DIR / "tier_mappings"
STATE_PATH = DATA_DIR / "_merit_review_state.json"
OFFSETS_PATH = DATA_DIR / "_merit_review_offsets.json"


def load_state() -> set[str]:
    if STATE_PATH.exists():
        return set(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    return set()


def save_state(done: set[str]) -> None:
    STATE_PATH.write_text(json.dumps(sorted(done), indent=2), encoding="utf-8")


def append_offset_record(record: dict) -> None:
    records = []
    if OFFSETS_PATH.exists():
        records = json.loads(OFFSETS_PATH.read_text(encoding="utf-8"))
    records = [
        r for r in records if not (r["season"] == record["season"] and r["comp"] == record["comp"])
    ]
    records.append(record)
    records.sort(key=lambda r: (r["comp"], r["season"]))
    OFFSETS_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")


def write_tier_mappings_parent(
    season: str, comp: str, apex_local_tier: int, apex_league: str, parent: str | list[str]
) -> dict:
    """Write the parent link and return the updated tier_mappings dict for *season*,
    so callers can refresh their in-memory cache instead of it going stale."""
    path = TIER_MAPPINGS_DIR / f"{season}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    section = data.setdefault(comp, {})
    tier_section = section.setdefault(str(apex_local_tier), {})
    tier_section[apex_league] = parent
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def cb_votes_for_clubs(teams: list[str]) -> Counter:
    votes = Counter()
    for t in teams:
        cb = get_constituent_body(t)
        if cb:
            votes[cb] += 1
    return votes


def build_pyramid_cb_index(placements: list) -> dict[str, list]:
    """Map CB name -> sorted list of (abs_tier, league_name) pyramid leagues that season.

    A pyramid league's CB is the majority vote of its own member clubs.
    """
    by_league: dict[tuple[int, str], list[str]] = defaultdict(list)
    for p in placements:
        if not p.is_merit:
            by_league[(p.abs_tier, p.league)].append(p.team)

    index: dict[str, list] = defaultdict(list)
    seen: set[tuple[int, str]] = set()
    for (abs_tier, league), teams in by_league.items():
        if (abs_tier, league) in seen:
            continue
        seen.add((abs_tier, league))
        votes = cb_votes_for_clubs(teams)
        if not votes:
            continue
        top_cb, _ = votes.most_common(1)[0]
        index[top_cb].append((abs_tier, league))

    for cb in index:
        index[cb].sort()
    return index


def prompt_choice(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "q"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--season", action="append", help="Restrict to this season (repeatable)")
    parser.add_argument("--comp", action="append", help="Restrict to this competition (repeatable)")
    parser.add_argument(
        "--redo",
        action="store_true",
        help="Re-visit pairs that already have a tier-1 parent set (default: auto-skip them)",
    )
    args = parser.parse_args()

    seasons = args.season or SEASONS
    comp_filter = set(args.comp) if args.comp else None

    ds = load_dataset(seasons)
    done = load_state()

    items: list[tuple[str, str]] = []
    for season in seasons:
        placements = ds.placements.get(season, [])
        comps = sorted({p.comp for p in placements if p.is_merit and p.comp})
        for comp in comps:
            if comp_filter and comp not in comp_filter:
                continue
            items.append((season, comp))

    all_mappings_preview: dict[str, dict] = {}
    for season in seasons:
        path = TIER_MAPPINGS_DIR / f"{season}.json"
        if path.exists():
            all_mappings_preview[season] = json.loads(path.read_text(encoding="utf-8"))

    n_already_set = 0
    for season, comp in items:
        placements = ds.placements.get(season, [])
        members = [p for p in placements if p.is_merit and p.comp == comp]
        if not members:
            continue
        apex_local_tier = min(p.local_tier for p in members)
        apex_leagues = sorted({p.league for p in members if p.local_tier == apex_local_tier})
        section = all_mappings_preview.get(season, {}).get(comp, {}).get(str(apex_local_tier), {})
        if section and all(a in section and section[a] for a in apex_leagues):
            n_already_set += 1

    print(
        f"{len(items)} (season, competition) pairs in scope; "
        f"{n_already_set} already have a parent set (auto-skipped unless --redo)."
    )
    if done:
        print(
            f"{len(done)} more marked reviewed in a previous run (data/rugby/_merit_review_state.json)."
        )
    print()

    # league name -> non-merit pyramid abs tier, per season, for checking whether a
    # carried-forward parent name still exists this season (naming eras differ).
    pyramid_league_tiers_by_season: dict[str, dict[str, int]] = {}
    for season in seasons:
        pyramid_league_tiers_by_season[season] = {
            p.league: p.abs_tier for p in ds.placements.get(season, []) if not p.is_merit
        }

    # Every season's tier_mappings, loaded once, so carry-forward can look at any
    # season's already-decided parent without re-reading files in the inner loop.
    all_mappings: dict[str, dict] = {}
    for season in SEASONS:
        path = TIER_MAPPINGS_DIR / f"{season}.json"
        if path.exists():
            all_mappings[season] = json.loads(path.read_text(encoding="utf-8"))

    def carried_forward_parent(
        season: str, comp: str, apex_leagues: list[str]
    ) -> tuple[str, str | list[str]] | None:
        """Nearest other season's apex parent for *comp*, if its league name(s) still
        exist as real pyramid leagues this season (this also keeps it from ever
        suggesting a pre-restructure name across a naming-era boundary).

        The other season's apex tier key isn't necessarily "1" -- some competitions'
        merit folders skip low local tiers -- so it's read as that season's own lowest
        populated tier for this competition, not assumed.
        """
        by_distance = sorted(SEASONS, key=lambda s: abs(SEASONS.index(s) - SEASONS.index(season)))
        this_season_leagues = pyramid_league_tiers_by_season.get(season, {})
        for other in by_distance:
            if other == season:
                continue
            comp_section = all_mappings.get(other, {}).get(comp, {})
            if not comp_section:
                continue
            try:
                other_apex_tier = min(int(k) for k in comp_section)
            except ValueError:
                continue
            tier_section = comp_section.get(str(other_apex_tier), {})
            for parent in tier_section.values():
                if not parent:
                    continue
                names = parent if isinstance(parent, list) else [parent]
                if all(n in this_season_leagues for n in names):
                    return other, parent
        return None

    for i, (season, comp) in enumerate(items, 1):
        key = f"{season}|{comp}"
        if key in done and not args.redo:
            continue

        placements = ds.placements[season]
        members = [p for p in placements if p.is_merit and p.comp == comp]
        if not members:
            continue
        apex_local_tier = min(p.local_tier for p in members)
        apex_placements = [p for p in members if p.local_tier == apex_local_tier]
        apex_leagues = sorted({p.league for p in apex_placements})

        mapping_path = TIER_MAPPINGS_DIR / f"{season}.json"
        mapping_data = json.loads(mapping_path.read_text(encoding="utf-8"))
        current_section = mapping_data.get(comp, {}).get(str(apex_local_tier), {})

        already_set = current_section and all(
            a in current_section and current_section[a] for a in apex_leagues
        )
        if already_set and not args.redo:
            done.add(key)
            save_state(done)
            print(f"[{i}/{len(items)}] {season} -- {comp}: parent already set, skipping.")
            continue

        cb_votes = cb_votes_for_clubs([p.team for p in members])
        n_members = len(members)
        n_matched = sum(cb_votes.values())
        n_unmatched = n_members - n_matched
        current_offset = get_competition_offset(comp, season)

        print("=" * 78)
        print(f"[{i}/{len(items)}] {season} -- {comp}")
        if cb_votes:
            cb_desc = ", ".join(
                f"{cb} ({n}/{n_members}, {n / n_members:.0%})" for cb, n in cb_votes.most_common()
            )
            print(f"  CB votes: {cb_desc}")
            if n_unmatched:
                print(f"  Unmatched: {n_unmatched}/{n_members} ({n_unmatched / n_members:.0%})")
        else:
            print("  CB votes: none matched")
        print(f"  Apex (local tier {apex_local_tier}): {apex_leagues}")
        print(f"  Current offset: {current_offset} -> abs {apex_local_tier + current_offset}")
        if current_section:
            print(f"  Current tier_mappings parent(s): {current_section}")
        else:
            print("  Current tier_mappings parent: (none set)")

        cb_pyramid = build_pyramid_cb_index(placements)
        primary_cb = cb_votes.most_common(1)[0][0] if cb_votes else None
        candidates: list[tuple[int, str, str]] = []  # (abs_tier, league, cb)
        for cb, _n in cb_votes.most_common():
            for abs_tier, league in cb_pyramid.get(cb, []):
                candidates.append((abs_tier, league, cb))

        suggestion = carried_forward_parent(season, comp, apex_leagues)
        if suggestion:
            other_season, other_parent = suggestion
            print(
                f"  Suggestion: same as {other_season} -> {other_parent!r} (still exists this season)"
            )

        print("-" * 78)
        if not candidates:
            print("  (no candidate pyramid leagues found for these CBs)")
        for idx, (abs_tier, league, cb) in enumerate(candidates, 1):
            marker = " *" if cb == primary_cb else "  "
            cb_pct = cb_votes[cb] / n_members if n_members else 0.0
            print(f"  {idx:>2}){marker} tier {abs_tier:>2}  {league}   [{cb}, {cb_pct:.0%}]")
        print("-" * 78)
        lines = ["  [number] pick candidate   [n1,n2,...] pick multiple parents"]
        if suggestion:
            lines.append("  [0] accept suggestion above")
        lines.append("  [Enter] skip (keep current, mark reviewed)")
        lines.append(
            "  [k] mark explicitly unlinked   [c] enter custom parent name   [q] quit & save"
        )
        print("\n".join(lines))

        choice = prompt_choice("> ")

        if choice.lower() == "q":
            save_state(done)
            print(f"\nSaved. Reviewed {len(done)} pairs so far.")
            return 0

        if choice == "":
            done.add(key)
            save_state(done)
            continue

        if choice == "0" and suggestion:
            other_season, other_parent = suggestion
            for apex_league in apex_leagues:
                all_mappings[season] = write_tier_mappings_parent(
                    season, comp, apex_local_tier, apex_league, other_parent
                )
            parent_tiers = pyramid_league_tiers_by_season.get(season, {})
            names = other_parent if isinstance(other_parent, list) else [other_parent]
            anchor_tier = min(parent_tiers[n] for n in names)
            new_offset = anchor_tier + 1 - apex_local_tier
            append_offset_record(
                {
                    "season": season,
                    "comp": comp,
                    "apex_local_tier": apex_local_tier,
                    "parent_league": other_parent,
                    "parent_abs_tier": anchor_tier,
                    "new_offset": new_offset,
                }
            )
            print(
                f"  -> wrote parent(s) {other_parent!r} carried forward from {other_season} (offset {new_offset})."
            )
            done.add(key)
            save_state(done)
            continue

        if choice.lower() == "k":
            for apex_league in apex_leagues:
                all_mappings[season] = write_tier_mappings_parent(
                    season, comp, apex_local_tier, apex_league, ""
                )
            done.add(key)
            save_state(done)
            print("  -> marked unlinked.")
            continue

        if choice.lower() == "c":
            custom = prompt_choice("  enter parent league name: ")
            if custom:
                for apex_league in apex_leagues:
                    all_mappings[season] = write_tier_mappings_parent(
                        season, comp, apex_local_tier, apex_league, custom
                    )
                print(f"  -> wrote custom parent {custom!r}.")
            done.add(key)
            save_state(done)
            continue

        picks = [p.strip() for p in choice.split(",") if p.strip()]
        if picks and all(p.isdigit() and 1 <= int(p) <= len(candidates) for p in picks):
            selected = [candidates[int(p) - 1] for p in picks]
            abs_tiers = {abs_tier for abs_tier, _league, _cb in selected}
            if len(abs_tiers) > 1:
                print(
                    f"  Note: selected parents span tiers {sorted(abs_tiers)}; "
                    f"using the shallowest (tier {min(abs_tiers)}) for the offset."
                )
            anchor_tier = min(abs_tiers)
            new_offset = anchor_tier + 1 - apex_local_tier
            leagues = [league for _abs_tier, league, _cb in selected]
            parent_value: str | list[str] = leagues[0] if len(leagues) == 1 else leagues
            for apex_league in apex_leagues:
                all_mappings[season] = write_tier_mappings_parent(
                    season, comp, apex_local_tier, apex_league, parent_value
                )
            append_offset_record(
                {
                    "season": season,
                    "comp": comp,
                    "apex_local_tier": apex_local_tier,
                    "parent_league": parent_value,
                    "parent_abs_tier": anchor_tier,
                    "new_offset": new_offset,
                }
            )
            print(f"  -> wrote parent(s) {parent_value!r} (offset {new_offset}).")
            done.add(key)
            save_state(done)
            continue

        print("  (not understood, skipping without marking reviewed)")

    save_state(done)
    print(
        f"\nAll {len(items)} pairs in scope reviewed. See {OFFSETS_PATH} for offsets to merge into tiers.py."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
