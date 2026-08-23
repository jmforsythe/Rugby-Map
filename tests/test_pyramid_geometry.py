"""Rectangle-geometry invariants for pyramid diagram layout (rugby.pyramid_image).

Two invariants matter for every set of league cells that share a tier band:

1. No two rectangles in the same tier overlap horizontally (checking is restricted to
   same-tier groups — e.g. one nested-layout ``tier4_rects`` dict at a time — since
   different tiers stack vertically and never need to be compared against each other).
2. No rectangle is degenerate (near-zero width or height), which would render as an
   invisible or unreadable league cell.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from rugby.analysis.inspect_pyramid_svgs import scan_svg_overlaps_text
from rugby.pyramid_image import (
    LEAGUE_DATA_DIR,
    BandLayout,
    LeagueData,
    _apply_interior_column_gaps,
    _divide_span_into_cells,
    _divide_span_weighted,
    _merit_parent_aligned_band_placements,
    _outer_span_for_cell,
    cell_horizontal_extent,
    compute_band_layout,
    compute_league_slots,
    compute_nested_tier56_layout,
    compute_womens_nested_layout,
    load_pyramid_leagues,
    load_pyramid_leagues_with_merit,
    order_pyramid_leaves,
    render_pyramid_svg,
    stem_parent_overrides_load_merged,
    stem_parent_overrides_merge_merit_sections_for_absolute_tiers,
    stem_slot_strips_load,
    womens_parent_overrides_load,
)

MIN_SANE_WIDTH_PX = 1.0
MIN_SANE_HEIGHT_PX = 1.0


def _assert_no_overlap_and_not_degenerate(
    rects: dict[str, tuple[float, float]],
    *,
    context: str,
    min_width: float = MIN_SANE_WIDTH_PX,
) -> None:
    """``rects`` must be x/width pairs for cells that all sit in the *same* tier band."""
    items = sorted(rects.items(), key=lambda kv: kv[1][0])
    for name, (_x, w) in items:
        assert w > min_width, f"{context}: degenerate width {w!r} for league {name!r}"
    for (name_a, (xa, wa)), (name_b, (xb, wb)) in zip(items, items[1:], strict=False):
        assert xa + wa <= xb + 1e-6, (
            f"{context}: {name_a!r} [{xa:.3f}, {xa + wa:.3f}] overlaps "
            f"{name_b!r} [{xb:.3f}, {xb + wb:.3f}]"
        )


# ---------------------------------------------------------------------------
# Primitive row-dividing helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier_num", [1, 4, 6, 7, 9, 11])
@pytest.mark.parametrize("n", [1, 2, 3, 5, 8, 12])
def test_band_layout_equal_slots_do_not_overlap_and_are_not_degenerate(
    tier_num: int, n: int
) -> None:
    lay = compute_band_layout(tier_num, n)
    assert lay is not None
    assert lay.cell_h > MIN_SANE_HEIGHT_PX, f"tier {tier_num}: degenerate band height"
    rects = {}
    for i in range(n):
        left, right = cell_horizontal_extent(lay, i)
        rects[f"slot-{i}"] = (left, right - left)
    _assert_no_overlap_and_not_degenerate(
        rects, context=f"compute_band_layout tier={tier_num} n={n}"
    )


@pytest.mark.parametrize("tier_num", range(1, 12))
def test_band_layout_height_is_never_degenerate(tier_num: int) -> None:
    lay = compute_band_layout(tier_num, 1)
    assert lay is not None
    assert lay.cell_h > MIN_SANE_HEIGHT_PX


@pytest.mark.parametrize("n", [1, 2, 3, 5, 10])
def test_divide_span_into_cells_no_overlap_or_degenerate(n: int) -> None:
    cells = _divide_span_into_cells(100.0, 1400.0, n)
    cells = _apply_interior_column_gaps(cells, 8.0)
    rects = {f"cell-{i}": c for i, c in enumerate(cells)}
    _assert_no_overlap_and_not_degenerate(rects, context=f"_divide_span_into_cells n={n}")


@pytest.mark.parametrize(
    "weights",
    [
        [1.0, 1.0],
        [1.0, 1.0, 1.0],
        [1.0, 50.0, 1.0],  # one dominant league among small neighbours
        [1.0] * 10,
        [1.0, 1.0, 1.0, 1.0, 100.0],  # extreme skew, e.g. one huge feeder vs several tiny ones
    ],
)
def test_divide_span_weighted_no_overlap_or_degenerate(weights: list[float]) -> None:
    cells = _divide_span_weighted(0.0, 1500.0, weights)
    assert len(cells) == len(weights)
    rects = {f"cell-{i}": c for i, c in enumerate(cells)}
    _assert_no_overlap_and_not_degenerate(rects, context=f"_divide_span_weighted weights={weights}")


def test_outer_span_for_cell_partitions_without_overlap_or_degenerate() -> None:
    lay = compute_band_layout(4, 4)
    assert lay is not None
    cells = [cell_horizontal_extent(lay, i) for i in range(4)]
    cells_pairs = [(left, right - left) for left, right in cells]
    y_ref = lay.band_bottom
    spans = [_outer_span_for_cell(i, 4, cells_pairs, y_ref) for i in range(4)]
    for i, (left, right) in enumerate(spans):
        assert right - left > MIN_SANE_WIDTH_PX, f"outer span {i} is degenerate"
    for (_l0, r0), (l1, _r1) in zip(spans, spans[1:], strict=False):
        assert r0 <= l1 + 1e-6, "adjacent outer spans overlap"


def test_merit_parent_aligned_placements_many_children_in_one_narrow_column() -> None:
    """A single parent column feeding several children must still split into sane sub-cells."""
    lay4 = BandLayout(
        tier_num=4,
        band_top=0.0,
        band_bottom=80.0,
        band_center_y=40.0,
        avail_w=1200.0,
        row_left_x=0.0,
        cell_w_raw=200.0,
        gap=8.0,
        cell_w=192.0,
        cell_h=60.0,
        row_top_y=10.0,
    )
    # Six parent columns so the total child count (crowded column + two singles) stays
    # within the parent grid — ``_merit_parent_aligned_band_placements`` bails to equal-width
    # slots whenever there are strictly more leagues than parent columns.
    prev_ord = [LeagueData(4, "", f"parent_{c}", [], 0) for c in "abcdef"]
    crowded_children = [LeagueData(5, "", f"child_b{i}", [], 0) for i in range(4)]
    single_children = [
        LeagueData(5, "", "child_a0", [], 0),
        LeagueData(5, "", "child_c0", [], 0),
    ]
    children = [*crowded_children, *single_children]
    ovs = {(5, lg.league_name): ("parent_b",) for lg in crowded_children}
    ovs[(5, "child_a0")] = ("parent_a",)
    ovs[(5, "child_c0")] = ("parent_c",)
    placements = _merit_parent_aligned_band_placements(
        5,
        children,
        prev_ord,
        lay4,
        ovs,
        "Demo",
        season="2025-2026",
        merit_local_offset=0,
    )
    assert placements is not None
    rects = {
        lg.league_name: (x_rect, cw)
        for lg, x_rect, cw, _col in placements
        if not lg.merit_column_spacer
    }
    assert len(rects) == len(children)
    _assert_no_overlap_and_not_degenerate(rects, context="merit narrow-column split", min_width=0.5)


# ---------------------------------------------------------------------------
# Real season data — men's tier 4-6 nested layout, women's tier 1-6 nested layout.
# Overlap checking stays scoped to one tier's rects dict at a time (same-tier groups only).
# ---------------------------------------------------------------------------


def _available_seasons() -> list[str]:
    return sorted(p.name for p in LEAGUE_DATA_DIR.iterdir() if p.is_dir())


def test_mens_nested_tier456_layout_real_seasons_no_overlap_or_degenerate() -> None:
    checked = 0
    for season in _available_seasons():
        leagues = load_pyramid_leagues(season, gender="mens")
        by_tier: dict[int, list[LeagueData]] = defaultdict(list)
        for lg in leagues:
            by_tier[lg.tier_num].append(lg)
        parent_overrides = stem_parent_overrides_load_merged(season, by_tier) or {}
        leaf_order = order_pyramid_leaves(by_tier, parent_overrides=parent_overrides)
        slots = compute_league_slots(by_tier, leaf_order, parent_overrides=parent_overrides)
        nested = compute_nested_tier56_layout(by_tier, slots, parent_overrides=parent_overrides)
        if nested is None:
            continue
        checked += 1
        for label, rects in (
            ("tier4", nested.tier4_rects),
            ("tier5", nested.tier5_rects),
            ("tier6", nested.tier6_rects),
        ):
            _assert_no_overlap_and_not_degenerate(rects, context=f"{season} men's {label}")
    assert checked > 0, "no season produced a men's nested tier 4-6 layout to validate"


def test_womens_nested_layout_real_seasons_no_overlap_or_degenerate() -> None:
    checked = 0
    for season in _available_seasons():
        leagues = load_pyramid_leagues(season, gender="womens")
        by_tier: dict[int, list[LeagueData]] = defaultdict(list)
        for lg in leagues:
            by_tier[lg.tier_num].append(lg)
        womens_overrides = womens_parent_overrides_load(season)
        nested = compute_womens_nested_layout(by_tier, womens_overrides)
        if nested is None:
            continue
        checked += 1
        for tier_num, rects in nested.tier_rects.items():
            _assert_no_overlap_and_not_degenerate(rects, context=f"{season} women's tier{tier_num}")
    assert checked > 0, "no season produced a women's nested layout to validate"


# ---------------------------------------------------------------------------
# Men's ``pyramid_All_Leagues`` (national + merit merge) — full SVG render.
#
# Some conflicts here don't show up as league-cell rect overlap at all: a merit competition
# can get merged into the same tier band as national leagues and draw its own team crests
# (SVG ``foreignObject`` tiles) inside a "spanning" background rect that already nests one or
# more *other* leagues' cells. The rects never collide, so the plain rect-overlap check above
# can't see it — only rendering the real SVG and checking crest placement against nested cells
# catches it. See ``rugby.analysis.inspect_pyramid_svgs._find_orphan_span_crest_conflicts``.
# ---------------------------------------------------------------------------


def _render_all_leagues_svg(season: str) -> str | None:
    """Mirror ``rugby.pyramid_image._render_mens_standard_pyramid(all_leagues=True)``."""
    try:
        national_leagues = load_pyramid_leagues(season, gender="mens")
        leagues = load_pyramid_leagues_with_merit(season)
    except FileNotFoundError:
        return None
    if not leagues:
        return None
    national_by_tier: dict[int, list[LeagueData]] = defaultdict(list)
    for lg in national_leagues:
        national_by_tier[lg.tier_num].append(lg)
    parent_overrides = stem_parent_overrides_load_merged(season, national_by_tier)
    parent_overrides = stem_parent_overrides_merge_merit_sections_for_absolute_tiers(
        season, dict(parent_overrides or {})
    )
    return render_pyramid_svg(
        season,
        leagues,
        gender="mens",
        parent_overrides=parent_overrides or None,
        womens_parent_overrides=None,
        stem_slot_strips=stem_slot_strips_load(season),
        transparent_white_crest_backgrounds=False,
        crest_transparency_workers=1,
        mens_merge_merit_leagues=True,
    )


def test_mens_all_leagues_real_seasons_no_orphan_span_crest_conflicts() -> None:
    checked = 0
    for season in _available_seasons():
        svg = _render_all_leagues_svg(season)
        if svg is None:
            continue
        checked += 1
        partial, _containment, orphan_span = scan_svg_overlaps_text(svg)
        assert (
            not partial
        ), f"{season} pyramid_All_Leagues: {len(partial)} partial rect overlap(s): {partial}"
        assert not orphan_span, (
            f"{season} pyramid_All_Leagues: {len(orphan_span)} orphan-span crest conflict(s) — "
            "a merit competition (or multi-parent span) drew its own team crests over a "
            "distinct nested league's cell in the same tier band"
        )
    assert checked > 0, "no season produced a pyramid_All_Leagues render to validate"
