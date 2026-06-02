"""
Interactive parent linker and tier_mappings I/O for the English football pyramid.

Parent links drive column nesting in :mod:`football.pyramid_image` (same
``(child_tier, child_name) -> parent_name(s)`` shape as rugby merit pyramids).

Saved to ``data/football/tier_mappings/<season>.json`` under the ``pyramid`` key.

Run:
  python -m football.pyramid_parents --season 2025-2026 --interactive
  python -m football.pyramid_image --season 2025-2026 --interactive-stem-orphans
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
from pathlib import Path

from core import setup_logging
from football import DATA_DIR
from football.league_names import find_football_parent_name
from rugby.pyramid_image import (
    STEM_PARENT_OVERRIDE_SCHEMA_VERSION,
    LeagueData,
    StemParentOverrides,
    _encode_overrides_for_json,
    _find_merit_parent_league,
    _merit_intra_parent_candidates,
    _stem_prompt_parent_pick,
    merit_parent_overrides_from_payload,
    write_tier_mappings_json,
)

logger = logging.getLogger(__name__)

FOOTBALL_PYRAMID_SECTION = "pyramid"
FOOTBALL_HEURISTIC_COMPETITION = "Football"
TIER_MAPPINGS_DIR = DATA_DIR / "tier_mappings"


def tier_mappings_path(season: str) -> Path:
    return TIER_MAPPINGS_DIR / f"{season}.json"


def _read_payload(season: str) -> dict[str, object] | None:
    path = tier_mappings_path(season)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def parent_overrides_load(season: str) -> StemParentOverrides | None:
    """Read football pyramid parent overrides for ``season``, if any."""
    payload = _read_payload(season)
    if payload is None:
        return None
    return merit_parent_overrides_from_payload(payload, FOOTBALL_PYRAMID_SECTION)


def parent_overrides_save(season: str, overrides: StemParentOverrides) -> Path | None:
    """Persist football pyramid parent overrides."""
    if not overrides:
        return None

    nested = _encode_overrides_for_json(overrides)
    if not nested:
        return None

    path = tier_mappings_path(season)
    preserved_other: dict[str, object] = {}
    schema_version = STEM_PARENT_OVERRIDE_SCHEMA_VERSION

    prev = _read_payload(season)
    if prev is not None:
        for key, value in prev.items():
            if key in {"schema_version", "season", FOOTBALL_PYRAMID_SECTION}:
                continue
            preserved_other[key] = value
        sv = prev.get("schema_version")
        if sv is not None:
            with contextlib.suppress(TypeError, ValueError):
                schema_version = int(sv)

    blob: dict[str, object] = dict(preserved_other)
    blob["schema_version"] = schema_version
    blob["season"] = season
    blob[FOOTBALL_PYRAMID_SECTION] = nested
    write_tier_mappings_json(path, blob)
    return path


def leagues_by_tier(leagues: list[LeagueData]) -> dict[int, list[LeagueData]]:
    out: dict[int, list[LeagueData]] = {}
    for lg in leagues:
        out.setdefault(lg.tier_num, []).append(lg)
    return out


def _find_football_parent_league(
    child: LeagueData,
    parents: list[LeagueData],
) -> LeagueData | None:
    """Match child to parent using football stem/geo heuristics, then rugby merit fallback."""
    parent_names = [p.league_name for p in parents]
    matched = find_football_parent_name(child.league_name, parent_names)
    if matched is not None:
        for p in parents:
            if p.league_name == matched:
                return p
    return _find_merit_parent_league(child, parents, FOOTBALL_HEURISTIC_COMPETITION)


def football_apply_parent_heuristics(
    leagues_by_local_tier: dict[int, list[LeagueData]],
    overrides: StemParentOverrides,
) -> int:
    """Fill intra-pyramid parent links using division-name heuristics."""
    tiers_sorted = sorted(leagues_by_local_tier.keys())
    if len(tiers_sorted) < 2:
        return 0

    min_tier = tiers_sorted[0]
    n_added = 0
    for tier in tiers_sorted:
        if tier <= min_tier:
            continue
        parents_list = list(leagues_by_local_tier.get(tier - 1, ()))
        if not parents_list:
            continue
        for lg in leagues_by_local_tier.get(tier, ()):
            key = (tier, lg.league_name)
            if key in overrides:
                continue
            found = _find_football_parent_league(lg, parents_list)
            if found is None:
                continue
            overrides[key] = (found.league_name,)
            n_added += 1
            logger.info(
                "Football pyramid: inferred parent for %r tier %d → %r tier %d",
                lg.league_name,
                tier,
                found.league_name,
                found.tier_num,
            )
    return n_added


def _next_missing_prompt(
    leagues_by_tier: dict[int, list[LeagueData]],
    overrides: StemParentOverrides,
) -> tuple[int, LeagueData, list[LeagueData], bool] | None:
    """Next league missing a parent link (tier 2+)."""
    tiers_present = sorted(leagues_by_tier.keys())
    if not tiers_present:
        return None
    min_tier = tiers_present[0]

    for tier in tiers_present:
        if tier <= min_tier:
            continue
        candidates, expanded = _merit_intra_parent_candidates(leagues_by_tier, tier, min_tier)
        for lg in sorted(leagues_by_tier.get(tier, ()), key=lambda x: x.league_name):
            if (tier, lg.league_name) not in overrides:
                return (tier, lg, candidates, expanded)
    return None


def football_interactive_parent_overrides(
    leagues_by_local_tier: dict[int, list[LeagueData]],
    *,
    seed_overrides: StemParentOverrides | None = None,
) -> StemParentOverrides:
    """TTY-only: prompt for missing intra-pyramid parent links (tier N → tier N−1)."""
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Interactive parent linker requires an interactive terminal (stdin is not a TTY)."
        )

    overrides: StemParentOverrides = dict(seed_overrides) if seed_overrides else {}
    football_apply_parent_heuristics(leagues_by_local_tier, overrides)

    tiers_present = sorted(leagues_by_local_tier.keys())
    if not tiers_present:
        logger.info("Football pyramid has no tiers — nothing to prompt.")
        return overrides

    min_tier = tiers_present[0]
    extra_tier_note = ""
    if len(tiers_present) >= 2:
        extra_tier_note = (
            f"\n  Intra-pyramid tiers {min_tier + 1} .. {tiers_present[-1]} "
            "(prefer tier-(N−1); if empty, choose any higher tier)."
        )

    print(
        "\nInteractive parent linker — English football pyramid\n"
        f"  Apex (tier {min_tier}): no parent prompt — top of pyramid."
        f"{extra_tier_note}\n"
        "  blank or 0 — explicit unlinked for this league\n"
        "  number — pick from the numbered list below\n"
        "  comma-separated numbers — multi-parent links\n"
        "  s / stop — stop prompting (remaining links unchanged)\n"
    )

    while True:
        nxt = _next_missing_prompt(leagues_by_local_tier, overrides)
        if nxt is None:
            n_linked = sum(1 for v in overrides.values() if v)
            n_explicit = sum(1 for v in overrides.values() if not v)
            logger.info(
                "Football interactive linker finished (%d linked; %d explicitly unlinked).",
                n_linked,
                n_explicit,
            )
            return overrides

        local_tier, child, candidates, intra_expanded = nxt
        if intra_expanded:
            banner = (
                "English football pyramid — assign parent(s) "
                f"(tier {local_tier} child; no leagues at tier {local_tier - 1})"
            )
            pick_instruction = (
                "Pick parent league(s) from any higher tier "
                f"(tier ≤ {local_tier - 2}); nearest tier listed first:"
            )
        else:
            banner = (
                "English football pyramid — assign parent(s) "
                f"(tier {local_tier} child → tier {local_tier - 1})"
            )
            pick_instruction = f"Pick tier {local_tier - 1} parent(s):"
        choice = _stem_prompt_parent_pick(
            local_tier,
            child,
            candidates,
            banner=banner,
            pick_instruction=pick_instruction,
        )
        if choice is None:
            logger.info(
                "Football interactive linker stopped early (%d choice(s) recorded).",
                len(overrides),
            )
            return overrides

        overrides[(local_tier, child.league_name)] = choice


def resolve_parent_overrides(
    season: str,
    leagues: list[LeagueData],
    *,
    interactive: bool,
    ignore_saved: bool,
) -> StemParentOverrides:
    """Load, infer, and optionally interactively assign pyramid parent overrides."""
    by_tier = leagues_by_tier(leagues)

    if interactive:
        seed: StemParentOverrides | None = None
        if not ignore_saved:
            seed = parent_overrides_load(season) or {}
        overrides = football_interactive_parent_overrides(by_tier, seed_overrides=seed)
        saved = parent_overrides_save(season, overrides)
        if saved is not None:
            logger.info(
                "Saved football pyramid overrides (%d entries) to %s",
                len(overrides),
                saved,
            )
        return overrides

    overrides: StemParentOverrides = {}
    if not ignore_saved:
        overrides = dict(parent_overrides_load(season) or {})

    base_keys = frozenset(overrides.keys())
    n_heur = football_apply_parent_heuristics(by_tier, overrides)

    if overrides:
        logger.info(
            "Football parent overrides: %d loaded; +%d from name heuristics (%d total)",
            len(base_keys),
            n_heur,
            len(overrides),
        )
    elif not ignore_saved:
        logger.info(
            "Football pyramid: no saved parent overrides in %s",
            tier_mappings_path(season),
        )

    if not ignore_saved and n_heur > 0:
        persisted = parent_overrides_save(season, overrides)
        if persisted is not None:
            logger.info(
                "Persisted football overrides to %s (+%d heuristic; %d total).",
                persisted,
                n_heur,
                len(overrides),
            )

    return overrides


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assign parent links for English football pyramid column nesting"
    )
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument(
        "--interactive",
        "--interactive-stem-orphans",
        dest="interactive",
        action="store_true",
        help="TTY: prompt for missing parent links and save to tier_mappings JSON",
    )
    parser.add_argument(
        "--ignore-saved-stem-parent-overrides",
        action="store_true",
        help="Do not load existing tier_mappings before prompting or heuristics",
    )
    args = parser.parse_args()

    setup_logging()
    from football.pyramid_image import load_football_pyramid_leagues

    leagues = load_football_pyramid_leagues(args.season)
    if not leagues:
        logger.error("No pyramid leagues found for %s", args.season)
        return 1

    overrides = resolve_parent_overrides(
        args.season,
        leagues,
        interactive=args.interactive,
        ignore_saved=args.ignore_saved_stem_parent_overrides,
    )
    logger.info("Football pyramid parent overrides: %d entries", len(overrides))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
