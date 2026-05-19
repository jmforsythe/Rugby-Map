"""Static checks on generated pyramid SVGs under ``dist/``.

1. **Overlap** — axis-aligned bbox overlap between league cell outlines (polygons and rects
   with the standard league-cell stroke). Ignores adjacent edges (small epsilon).
2. **Stem orphan rows** — for men's ``pyramid.svg`` / ``pyramid_All_Leagues.svg``, replays
   :func:`rugby.pyramid_image._stem_build_layout` with the same override loading as the
   renderer and reports tiers where unattached subtree roots occupy the fallback band
   (full-width split row beneath the linked row).

Run: ``python -m rugby.analysis.inspect_pyramid_svgs``

Orphans: pass ``--purge-orphan-json`` to remove stem child rows in ``data/rugby/tier_mappings``
that still lay out as orphan geometry (add ``--dry-run`` to preview only).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from core.config import DIST_DIR
from rugby.pyramid_image import (
    LEAGUE_CELL_STROKE_MENS,
    LEAGUE_CELL_STROKE_WOMENS,
    TIER_MAPPINGS_DIR,
    TIER_MAPPINGS_RESERVED_TOP_KEYS,
    LeagueData,
    StemLayout,
    StemParentOverrides,
    StemSlotStrip,
    _build_stem_forest,
    _stem_build_layout,
    _stem_parent_override_read_payload,
    load_pyramid_leagues,
    load_pyramid_leagues_with_merit,
    stem_parent_overrides_load,
    stem_parent_overrides_merge_cross_season,
    stem_parent_overrides_merge_merit_sections_for_absolute_tiers,
    stem_parent_overrides_store_path,
    stem_slot_strips_load,
    write_tier_mappings_json,
)
from rugby.tiers import get_competition_offset

EPS_OVERLAP = 3.0  # px; ignore touching / numerical noise
CONTAIN_EPS = 2.0  # generous: stroke-aligned siblings may sit just inside a strip
MIN_LEAGUE_W = 24.0
MIN_LEAGUE_H = 24.0

_TRANSLATE_RE = re.compile(
    r"translate\s*\(\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?:\s*,\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?))?\s*\)"
)


def _local_tag(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[-1]
    return tag


def _parse_translate(transform: str | None) -> tuple[float, float]:
    if not transform:
        return (0.0, 0.0)
    m = _TRANSLATE_RE.search(transform.strip())
    if not m:
        return (0.0, 0.0)
    tx = float(m.group(1))
    ty = float(m.group(2)) if m.group(2) is not None else 0.0
    return (tx, ty)


@dataclass(frozen=True)
class LeagueShape:
    kind: str  # "rect" | "poly"
    x0: float
    y0: float
    x1: float
    y1: float


def _parse_points_bbox(
    points_attr: str, tx: float, ty: float
) -> tuple[float, float, float, float] | None:
    pts = points_attr.replace(",", " ").split()
    if len(pts) < 4:
        return None
    xs: list[float] = []
    ys: list[float] = []
    try:
        for i in range(0, len(pts), 2):
            xs.append(float(pts[i]) + tx)
            ys.append(float(pts[i + 1]) + ty)
    except (ValueError, IndexError):
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _collect_league_shapes(
    el: ElementTree.Element,
    ox: float,
    oy: float,
    in_clip_path: bool,
    stroke_ok: frozenset[str],
    out: list[LeagueShape],
) -> None:
    tag = _local_tag(el.tag)
    ch_clip = in_clip_path
    nx, ny = ox, oy
    if tag == "clipPath":
        ch_clip = True
    elif tag == "g" and not ch_clip:
        tfn = el.get("transform")
        dx, dy = _parse_translate(tfn)
        nx, ny = ox + dx, oy + dy

    if not ch_clip and tag == "rect":
        stroke = el.get("stroke", "").strip().lower()
        w = float(el.get("width", "0"))
        h = float(el.get("height", "0"))
        if stroke in stroke_ok and w >= MIN_LEAGUE_W and h >= MIN_LEAGUE_H:
            x = float(el.get("x", "0")) + nx
            y = float(el.get("y", "0")) + ny
            out.append(LeagueShape("rect", x, y, x + w, y + h))

    if not ch_clip and tag == "polygon":
        stroke = el.get("stroke", "").strip().lower()
        fill = el.get("fill", "")
        if stroke in stroke_ok and fill and fill != "none":
            pa = el.get("points", "")
            bb = _parse_points_bbox(pa, nx, ny)
            if bb is not None:
                x0, y0, x1, y1 = bb
                if (x1 - x0) >= MIN_LEAGUE_W and (y1 - y0) >= MIN_LEAGUE_H:
                    out.append(LeagueShape("poly", x0, y0, x1, y1))

    for ch in el:
        _collect_league_shapes(ch, nx, ny, ch_clip, stroke_ok, out)


def _overlap_area(a: LeagueShape, b: LeagueShape) -> float:
    ix0 = max(a.x0, b.x0)
    iy0 = max(a.y0, b.y0)
    ix1 = min(a.x1, b.x1)
    iy1 = min(a.y1, b.y1)
    w = ix1 - ix0
    h = iy1 - iy0
    if w <= EPS_OVERLAP or h <= EPS_OVERLAP:
        return 0.0
    return float(w * h)


def _bbox_area(s: LeagueShape) -> float:
    return max(0.0, s.x1 - s.x0) * max(0.0, s.y1 - s.y0)


def _a_contains_b(outer: LeagueShape, inner: LeagueShape) -> bool:
    return (
        outer.x0 <= inner.x0 + CONTAIN_EPS
        and outer.y0 <= inner.y0 + CONTAIN_EPS
        and outer.x1 + CONTAIN_EPS >= inner.x1
        and outer.y1 + CONTAIN_EPS >= inner.y1
    )


def _dedupe_shapes(shapes: list[LeagueShape]) -> list[LeagueShape]:
    seen: set[tuple[str, int, int, int, int]] = set()
    out: list[LeagueShape] = []
    for s in shapes:
        key = (
            s.kind,
            int(round(s.x0 * 100)),
            int(round(s.y0 * 100)),
            int(round(s.x1 * 100)),
            int(round(s.y1 * 100)),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def scan_svg_overlaps(
    path: Path,
) -> tuple[
    list[tuple[LeagueShape, LeagueShape, float, str]], list[tuple[LeagueShape, LeagueShape]]
]:
    """Return (partial_overlaps_with_area, containment_pairs).

    Containment (one league outline strictly inside another) is reported separately: it usually
    means a spanning cell plus column cells in ``pyramid_All_Leagues`` stem rows, not two
    unrelated polygons on the taper.
    """
    stroke_ok = frozenset({LEAGUE_CELL_STROKE_MENS.lower(), LEAGUE_CELL_STROKE_WOMENS.lower()})
    text = path.read_text(encoding="utf-8")
    root = ElementTree.fromstring(text)
    shapes: list[LeagueShape] = []
    _collect_league_shapes(root, 0.0, 0.0, False, stroke_ok, shapes)
    shapes = _dedupe_shapes(shapes)
    partial: list[tuple[LeagueShape, LeagueShape, float, str]] = []
    containment: list[tuple[LeagueShape, LeagueShape]] = []
    for i, sa in enumerate(shapes):
        for sb in shapes[i + 1 :]:
            ar = _overlap_area(sa, sb)
            if ar <= 0:
                continue
            aa, ab = _bbox_area(sa), _bbox_area(sb)
            if aa <= 0 or ab <= 0:
                continue
            if _a_contains_b(sa, sb) or _a_contains_b(sb, sa):
                # Ignore near-equal duplicates (already deduped); tiny noise inside strip.
                ratio = min(aa, ab) / max(aa, ab)
                if ratio < 0.92:
                    containment.append((sa, sb))
                continue
            # Partial overlap: neither contains the other
            iou = ar / (aa + ab - ar)
            partial.append((sa, sb, ar, f"IoU={iou:.2f}"))
    return partial, containment


def _leagues_by_tier(leagues: Iterable[LeagueData]) -> dict[int, list[LeagueData]]:
    d: dict[int, list[LeagueData]] = {}
    for lg in leagues:
        d.setdefault(lg.tier_num, []).append(lg)
    return d


STEM_JSON_RESERVED = TIER_MAPPINGS_RESERVED_TOP_KEYS


def stem_layout_for_season(season: str, *, all_leagues: bool) -> StemLayout | None:
    """Replay stem packing for national-only or merged All Leagues (same inputs as rendering)."""
    try:
        national = load_pyramid_leagues(season, gender="mens")
    except FileNotFoundError:
        return None
    national_by_tier = _leagues_by_tier(national)
    leagues = load_pyramid_leagues_with_merit(season) if all_leagues else national
    leagues_by_tier = _leagues_by_tier(leagues)

    base = stem_parent_overrides_load(season) or {}
    parent_overrides: StemParentOverrides | None = stem_parent_overrides_merge_cross_season(
        season, national_by_tier, base
    )
    if not parent_overrides:
        parent_overrides = None
    if all_leagues:
        po2 = stem_parent_overrides_merge_merit_sections_for_absolute_tiers(
            season, dict(parent_overrides or {})
        )
        parent_overrides = po2 if po2 else None

    stem_slot_strips: tuple[StemSlotStrip, ...] = stem_slot_strips_load(season)

    stem_forest = _build_stem_forest(
        leagues_by_tier,
        season,
        parent_overrides=parent_overrides,
        log_unlinked=False,
        merit_competition=None,
    )
    return _stem_build_layout(
        leagues_by_tier,
        season,
        parent_overrides=parent_overrides,
        stem_slot_strips=stem_slot_strips,
        log_stem_orphans=False,
        merit_competition=None,
        stem_forest=stem_forest,
    )


def orphan_league_keys_for_season(season: str) -> set[tuple[int, str]]:
    """All (absolute tier, league name) cells that use orphan-row geometry (either layout mode)."""
    keys: set[tuple[int, str]] = set()
    for all_merit in (False, True):
        layout = stem_layout_for_season(season, all_leagues=all_merit)
        if layout is None:
            continue
        for tier, row in layout.orphan_row_positions.items():
            if not row:
                continue
            for lg, _, _ in row:
                keys.add((tier, lg.league_name))
    return keys


def _prune_empty_tier_bands(section: object) -> None:
    if not isinstance(section, dict):
        return
    dead = [tk for tk, band in list(section.items()) if isinstance(band, dict) and not band]
    for tk in dead:
        del section[tk]


def _stem_strip_league_refs(payload: dict) -> set[str]:
    names: set[str] = set()
    raw = payload.get("stem_slot_strips")
    if not isinstance(raw, list):
        return names
    for item in raw:
        if not isinstance(item, dict):
            continue
        for band in item.get("bands") or ():
            if not isinstance(band, dict):
                continue
            for nm in band.get("leagues") or ():
                if isinstance(nm, str) and nm.strip():
                    names.add(nm.strip())
    return names


def purge_orphan_entries_from_tier_mapping_json(
    season: str,
    *,
    dry_run: bool,
) -> tuple[list[str], int]:
    """Remove ``men`` / per-merit child rows for leagues that still pack as stem orphans.

    Returns ``(report lines, number of child keys removed)``.
    """
    lines: list[str] = []
    keys = orphan_league_keys_for_season(season)
    if not keys:
        lines.append(f"{season}: no orphan geometry in layout replay; nothing to purge.")
        return lines, 0

    path = stem_parent_overrides_store_path(season)
    if not path.is_file():
        lines.append(f"{season}: no {path.name}; skipped purge ({len(keys)} orphan key(s)).")
        return lines, 0

    payload = _stem_parent_override_read_payload(season)
    if not isinstance(payload, dict):
        lines.append(f"{season}: unreadable JSON; skipped.")
        return lines, 0

    strip_refs = _stem_strip_league_refs(payload)
    removed = 0
    removed_names: set[str] = set()
    for tier, name in sorted(keys, key=lambda t: (t[0], t[1].lower())):
        men = payload.get("men")
        if isinstance(men, dict):
            band = men.get(str(tier))
            if isinstance(band, dict) and name in band:
                lines.append(f"  remove men[{tier!r}][{name!r}]")
                removed_names.add(name)
                if not dry_run:
                    del band[name]
                removed += 1

        for comp, section in list(payload.items()):
            if comp in STEM_JSON_RESERVED or not isinstance(section, dict):
                continue
            comp_s = str(comp)
            try:
                off = int(get_competition_offset(comp_s, season))
            except (TypeError, ValueError):
                off = 0
            if off <= 0:
                continue
            local_t = tier - off
            if local_t < 0:
                continue
            band = section.get(str(local_t))
            if isinstance(band, dict) and name in band:
                lines.append(f"  remove {comp_s}[{local_t!r}][{name!r}] (absolute tier {tier})")
                removed_names.add(name)
                if not dry_run:
                    del band[name]
                removed += 1

    if not dry_run:
        men_obj = payload.get("men")
        if isinstance(men_obj, dict):
            _prune_empty_tier_bands(men_obj)
        for comp, section in list(payload.items()):
            if comp in STEM_JSON_RESERVED or not isinstance(section, dict):
                continue
            _prune_empty_tier_bands(section)

        write_tier_mappings_json(path, payload)
        lines.insert(0, f"{season}: wrote {path} ({removed} mapping(s) removed).")
    else:
        lines.insert(0, f"{season}: dry-run - would remove {removed} mapping(s).")

    if removed == 0 and keys:
        lines.append("  (no JSON entries matched those orphan keys)")

    orphans_in_strip = sorted(strip_refs & removed_names)
    if orphans_in_strip:
        lines.append(
            f"  warning: removed name(s) still referenced in stem_slot_strips: {orphans_in_strip}"
        )

    return lines, removed


def stem_orphan_report_for_season(season: str, *, all_leagues: bool) -> list[str]:
    """Human-readable lines for tiers with a stem orphan fallback row."""
    layout = stem_layout_for_season(season, all_leagues=all_leagues)
    if layout is None:
        return []

    lines: list[str] = []
    for tier in sorted(layout.orphan_row_positions.keys()):
        orphans = layout.orphan_row_positions.get(tier, [])
        pure_names = {lg.league_name for lg, _, _ in layout.pure_tree_placements.get(tier, ())}
        if not orphans:
            continue
        if pure_names:
            onames = [lg.league_name for lg, _, _ in orphans]
            lines.append(
                f"  tier {tier}: orphan row below linked row - "
                f"{len(onames)} league(s): {', '.join(sorted(onames))}"
            )
        else:
            onames = [lg.league_name for lg, _, _ in orphans]
            lines.append(
                f"  tier {tier}: no parent links; full-width row(s) - "
                f"{', '.join(sorted(onames))}"
            )
    return lines


_SEASON_JSON_NAME = re.compile(r"^[12]\d{3}-[12]\d{3}\.json$")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pyramid SVG overlap checks and tier_mapping orphan purge."
    )
    parser.add_argument(
        "--purge-orphan-json",
        action="store_true",
        help="Delete men / merit child keys for leagues that still use stem orphan geometry.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --purge-orphan-json, print removals only; do not write JSON.",
    )
    args = parser.parse_args()

    logging.disable(logging.CRITICAL)

    dist = DIST_DIR
    if not dist.is_dir():
        print(f"No dist directory at {dist}", file=sys.stderr)
        return 1

    svg_paths = sorted(dist.glob("**/pyramid*.svg"))
    if not svg_paths:
        print(f"No pyramid*.svg under {dist}", file=sys.stderr)
        return 1

    partial_by_file: list[tuple[Path, list[tuple[LeagueShape, LeagueShape, float, str]]]] = []
    contain_by_file: list[tuple[Path, list[tuple[LeagueShape, LeagueShape]]]] = []
    for p in svg_paths:
        part, cont = scan_svg_overlaps(p)
        if part:
            partial_by_file.append((p, part))
        if cont:
            contain_by_file.append((p, cont))

    if args.purge_orphan_json:
        print("=== Purge orphan stem mappings (tier_mappings JSON) ===\n")
        total_removed = 0
        if not TIER_MAPPINGS_DIR.is_dir():
            print(f"No tier_mappings dir at {TIER_MAPPINGS_DIR}", file=sys.stderr)
        else:
            for jpath in sorted(TIER_MAPPINGS_DIR.glob("*.json")):
                if not _SEASON_JSON_NAME.match(jpath.name):
                    continue
                season = jpath.stem
                try:
                    lines, n = purge_orphan_entries_from_tier_mapping_json(
                        season, dry_run=args.dry_run
                    )
                except FileNotFoundError:
                    print(f"{season}: no geocoded data; skipped.\n")
                    continue
                print("\n".join(lines))
                print()
                total_removed += n
        mode = "would remove" if args.dry_run else "removed"
        print(f"Total {mode} child mapping(s): {total_removed}\n")

    print(f"Scanned {len(svg_paths)} SVG(s) under {dist}")
    print()

    if partial_by_file:
        print(
            "=== Partial overlap (neither outline contains the other; likely draw bugs) — full list ==="
        )
        for p, details in partial_by_file:
            print(f"{p.relative_to(dist)}: {len(details)} pair(s)")
            for sa, sb, area, tag in details:
                print(
                    f"    {tag} area={area:.0f}  "
                    f"{sa.kind}({sa.x0:.0f},{sa.y0:.0f})-({sa.x1:.0f},{sa.y1:.0f})"
                    f" x {sb.kind}({sb.x0:.0f},{sb.y0:.0f})-({sb.x1:.0f},{sb.y1:.0f})"
                )
        print()
    else:
        print("=== Partial overlaps === none detected\n")

    if contain_by_file:
        print(
            "=== Spanning vs column outlines (inner bbox inside outer - typical of "
            "``pyramid_All_Leagues`` stem + merit strip geometry; verify visually if suspicious) ==="
        )
        for p, pairs in contain_by_file:
            print(f"{p.relative_to(dist)}: {len(pairs)} containment pair(s)")
        print()

    seasons = sorted({p.parent.name for p in svg_paths})
    orphan_sections: list[str] = []
    for season in seasons:
        bits: list[str] = []
        base = dist / season
        if (base / "pyramid.svg").is_file():
            r = stem_orphan_report_for_season(season, all_leagues=False)
            if r:
                bits.append(f"{season} pyramid.svg (men's national)")
                bits.extend(r)
        if (base / "pyramid_All_Leagues.svg").is_file():
            r = stem_orphan_report_for_season(season, all_leagues=True)
            if r:
                bits.append(f"{season} pyramid_All_Leagues.svg (men's + merit)")
                bits.extend(r)
        if bits:
            orphan_sections.append("\n".join(bits))

    if orphan_sections:
        print(
            "=== Counties stem: leagues in 'no parent' / orphan band (after purge, re-render to refresh) ==="
        )
        print(
            "(Tier numbers are absolute men's tiers; orphan row sits under the linked row when "
            "both exist; otherwise a single full-width band.)\n"
        )
        print("\n\n".join(orphan_sections))
        print()
    else:
        print("=== Stem orphan rows === none (no unattached stem roots in layout replay)\n")

    if partial_by_file:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
