"""Bonus import packs for the custom map builder.

Each file in ``data/rugby/custom_map_imports/*.json`` describes one importable
set of tier/league rosters (e.g. a season projection). The custom map build
reads every valid file and writes ``dist/custom-map/bonus_imports.js``.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from rugby import DATA_DIR
from rugby.analysis.projected_urls import normalize_import_spec_leagues, normalize_parsed_tiers
from rugby.maps import COLOR_PALETTE, UNASSIGNED_COLOR
from rugby.tiers import mens_current_tier_name

logger = logging.getLogger(__name__)

IMPORTS_DIR = DATA_DIR / "custom_map_imports"
SCHEMA_VERSION = 1


def season_from_projected_markdown(path: Path) -> str:
    """Extract ``YYYY-YYYY`` from ``projected_2026-2027.md`` style names."""
    match = re.search(r"projected[_-](\d{4}[_-]\d{4})", path.stem)
    if match:
        return match.group(1).replace("_", "-")
    return "unknown"


def tiers_dict_to_spec(
    tiers_dict: dict[int, list[tuple[str, list[str]]]],
    *,
    import_id: str,
    label: str,
    season: str = "",
) -> dict[str, Any]:
    """Build a version-1 import spec dict from parsed markdown tiers."""
    tiers_dict = normalize_parsed_tiers(tiers_dict)
    tiers: list[dict[str, Any]] = []
    for tier_num in sorted(tiers_dict):
        tier_name = mens_current_tier_name(tier_num)
        leagues = [
            {"name": league_name, "teams": list(team_names)}
            for league_name, team_names in tiers_dict[tier_num]
        ]
        tiers.append(
            {
                "tier": tier_num,
                "name": f"{label} — {tier_name}",
                "leagues": leagues,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "id": import_id,
        "label": label,
        "season": season,
        "tiers": tiers,
    }


def _map_color_league_sort_key(league: dict[str, Any]) -> str:
    """Sort key matching ``sorted(group_names)`` in ``core.map_builder``."""
    return league.get("name", "")


def assign_league_colors(
    leagues: list[dict[str, Any]],
    tier_num: int,
) -> None:
    """Attach ``color`` to each league in place (matches pyramid map palette)."""
    league_idx = 0
    for league in sorted(leagues, key=_map_color_league_sort_key):
        name = league.get("name", "")
        if name == "Unassigned":
            league["color"] = UNASSIGNED_COLOR
        else:
            league["color"] = COLOR_PALETTE[(tier_num - 1 + league_idx) % len(COLOR_PALETTE)]
            league_idx += 1


def assign_all_leagues_colors(
    leagues: list[dict[str, Any]],
    tier_num: int,
    pyramid_leagues: list[dict[str, Any]],
) -> None:
    """Colour pyramid+merit import: pyramid leagues match the pyramid tier; merit-only after."""
    pyramid_colors = {lg["name"]: lg["color"] for lg in pyramid_leagues if lg.get("color")}
    pyramid_count = sum(1 for lg in pyramid_leagues if lg.get("name") != "Unassigned")
    merit_idx = 0
    for league in sorted(leagues, key=_map_color_league_sort_key):
        name = league.get("name", "")
        if name in pyramid_colors:
            league["color"] = pyramid_colors[name]
        elif name == "Unassigned":
            league["color"] = UNASSIGNED_COLOR
        else:
            league["color"] = COLOR_PALETTE[
                (tier_num - 1 + pyramid_count + merit_idx) % len(COLOR_PALETTE)
            ]
            merit_idx += 1


def _tier_js_id(
    import_id: str,
    tier: dict[str, Any],
    tier_idx: int,
    *,
    duplicate_tier_nums: set[int],
) -> str:
    """Stable import-modal id; disambiguate when one pack has multiple rows at the same tier."""
    explicit = tier.get("id")
    if explicit:
        return str(explicit)
    tier_num = int(tier["tier"])
    if tier_num in duplicate_tier_nums:
        return f"{import_id}_{tier_num}_{tier_idx}"
    return f"{import_id}_{tier_num}"


def spec_to_js_pack(spec: dict[str, Any]) -> dict[str, Any]:
    """Add league colours for the assembled JS payload."""
    import_id = spec["id"]
    label = spec.get("label", import_id)
    season = spec.get("season", "")
    tiers_in = list(spec.get("tiers", []))
    tier_nums = [int(t["tier"]) for t in tiers_in]
    duplicate_tier_nums = {n for n in tier_nums if tier_nums.count(n) > 1}
    tiers_out: list[dict[str, Any]] = []
    for tier_idx, tier in enumerate(tiers_in):
        tier_num = int(tier["tier"])
        leagues = sorted(
            [dict(lg) for lg in tier.get("leagues", [])],
            key=_map_color_league_sort_key,
        )
        assign_league_colors(leagues, tier_num)
        tier_name = tier.get("name") or f"{label} — {mens_current_tier_name(tier_num)}"
        tiers_out.append(
            {
                "id": _tier_js_id(
                    import_id, tier, tier_idx, duplicate_tier_nums=duplicate_tier_nums
                ),
                "tier": tier_num,
                "name": tier_name,
                "leagues": leagues,
            }
        )
    return {
        "id": import_id,
        "label": label,
        "season": season,
        "tiers": tiers_out,
    }


def validate_import_spec(spec: dict[str, Any], *, source: str) -> None:
    """Raise ``ValueError`` if *spec* is not a usable import pack."""
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{source}: schema_version must be {SCHEMA_VERSION}")
    if not spec.get("id"):
        raise ValueError(f"{source}: missing id")
    if not spec.get("label"):
        raise ValueError(f"{source}: missing label")
    tiers = spec.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise ValueError(f"{source}: tiers must be a non-empty list")
    tier_ids_seen: set[str] = set()
    for tier in tiers:
        if "tier" not in tier:
            raise ValueError(f"{source}: tier entry missing tier number")
        if tier.get("id") is not None:
            tid = str(tier["id"])
            if not tid:
                raise ValueError(f"{source}: tier id must be non-empty when set")
            if tid in tier_ids_seen:
                raise ValueError(f"{source}: duplicate tier id {tid!r}")
            tier_ids_seen.add(tid)
        leagues = tier.get("leagues")
        if not isinstance(leagues, list) or not leagues:
            raise ValueError(f"{source}: tier {tier.get('tier')} has no leagues")
        for league in leagues:
            if not league.get("name"):
                raise ValueError(f"{source}: league missing name in tier {tier.get('tier')}")
            teams = league.get("teams")
            if not isinstance(teams, list):
                raise ValueError(f"{source}: teams must be a list for {league.get('name')!r}")


def load_import_file(path: Path) -> dict[str, Any]:
    """Load and validate one import JSON file."""
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    if not isinstance(spec, dict):
        raise ValueError(f"{path.name}: root must be a JSON object")
    validate_import_spec(spec, source=path.name)
    return normalize_import_spec_leagues(spec)


def load_all_imports(directory: Path | None = None) -> list[dict[str, Any]]:
    """Load every ``*.json`` import pack in *directory* (sorted by filename)."""
    root = directory or IMPORTS_DIR
    if not root.is_dir():
        return []
    specs: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            specs.append(load_import_file(path))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Skipping import %s: %s", path.name, exc)
    return specs


def build_js_payload(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert validated specs to the structure embedded in bonus_imports.js."""
    return [spec_to_js_pack(spec) for spec in specs]


def write_import_spec(path: Path, spec: dict[str, Any]) -> None:
    """Write an import spec JSON file (pretty-printed for manual editing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)
        f.write("\n")


def write_bonus_imports_js(output_dir: Path, directory: Path | None = None) -> None:
    """Load import packs and write ``bonus_imports.js`` to *output_dir*."""
    specs = load_all_imports(directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "bonus_imports.js"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by rugby.custom_map — do not edit\n")
        if specs:
            packs = build_js_payload(specs)
            payload = json.dumps(packs, separators=(",", ":"), ensure_ascii=False)
            f.write("var BONUS_IMPORTS = ")
            f.write(payload)
            f.write(";\n")
            total_teams = sum(
                len(lg.get("teams", []))
                for spec in specs
                for tier in spec.get("tiers", [])
                for lg in tier.get("leagues", [])
            )
            logger.info(
                "Wrote %s (%d pack(s), %d teams)",
                output_path,
                len(specs),
                total_teams,
            )
        else:
            f.write("var BONUS_IMPORTS = [];\n")
            logger.info(
                "No bonus import packs in %s — wrote empty %s",
                directory or IMPORTS_DIR,
                output_path.name,
            )
