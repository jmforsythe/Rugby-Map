"""Tests for rugby.instagram_maps."""

from pathlib import Path

import numpy as np
from shapely.geometry import GeometryCollection, LineString, Polygon
from shapely.ops import unary_union

from core.config import CURRENT_SEASON
from core.map_builder import MarkerItem, load_itl_hierarchy, preassign_itl_regions
from rugby import DATA_DIR
from rugby.instagram_maps import (
    BADGE_DIAMETER_CEILING_LOWER,
    BADGE_DIAMETER_CEILING_UPPER,
    BADGE_DIAMETER_FLOOR,
    BADGE_MAX_SHIFT_RADII,
    BADGE_RELAX_ITERATIONS,
    BADGE_UPPER_TIER_MAX,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    _auto_badge_diameter,
    _country_bounds,
    _country_mask,
    _crest_inline_px,
    _geom_to_svg_path,
    _layout_badges,
    _make_projector,
    _nearest_neighbour_radii,
    _polygonal_only,
    _relax_positions,
    _render_badges,
    compute_tier_territories,
    generate_tier_graphics,
    render_tier_svg,
)
from rugby.maps import BOUNDARY_PATHS, RFU_FALLBACK_ICON, _load_marker_items


def test_render_tier7_svg_contains_labels() -> None:
    season = CURRENT_SEASON
    geocoded_dir = str(DATA_DIR / "geocoded_teams" / season)
    loaded = _load_marker_items(geocoded_dir, season, travel_distances=None)
    tier7_items = [it for it in loaded.pyramid if it.tier_num == 7]
    assert tier7_items, "expected tier 7 pyramid teams in geocoded data"

    itl = load_itl_hierarchy(BOUNDARY_PATHS)
    preassign_itl_regions(tier7_items, itl)
    territories, colours = compute_tier_territories(tier7_items, itl)

    svg = render_tier_svg(
        season=season,
        tier_num=7,
        territories=territories,
        colours=colours,
        itl_hierarchy=itl,
    )
    assert "Rugby Union" in svg
    assert "rugbyunionmap.uk" in svg
    assert 'href="data:image/svg+xml;base64,' in svg
    assert season in svg
    assert "Level 7" in svg
    assert "Counties 1" in svg
    assert f'width="{IMAGE_WIDTH}"' in svg
    assert f'height="{IMAGE_HEIGHT}"' in svg
    assert len(territories) >= 10


def test_generate_single_level(tmp_path: Path) -> None:
    out = tmp_path / "graphics"
    paths = generate_tier_graphics(CURRENT_SEASON, out, tier_nums=[1])
    assert len(paths) == 1
    assert paths[0].name == "level_01_premiership.svg"
    text = paths[0].read_text(encoding="utf-8")
    assert "Level 1" in text
    assert "Premiership" in text


def test_geometry_collection_renders_as_path() -> None:
    """Regression: make_valid/intersection return GeometryCollections that were dropped."""
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    collection = GeometryCollection([poly, LineString([(0, 0), (2, 2)])])

    assert _geom_to_svg_path(collection, lambda lon, lat: (lon, lat))
    assert _polygonal_only(collection).geom_type == "Polygon"


def test_every_league_is_shaded_at_level_7() -> None:
    """All 19 Counties 1 leagues must produce a fill, not just a territory geometry."""
    season = CURRENT_SEASON
    geocoded_dir = str(DATA_DIR / "geocoded_teams" / season)
    loaded = _load_marker_items(geocoded_dir, season, travel_distances=None)
    tier7_items = [it for it in loaded.pyramid if it.tier_num == 7]

    itl = load_itl_hierarchy(BOUNDARY_PATHS)
    preassign_itl_regions(tier7_items, itl)
    territories, colours = compute_tier_territories(tier7_items, itl)

    leagues = {it.group for it in tier7_items}
    assert set(territories) == leagues

    svg = render_tier_svg(
        season=season,
        tier_num=7,
        territories=territories,
        colours=colours,
        itl_hierarchy=itl,
    )
    for league, colour in colours.items():
        assert f'fill="{colour}"' in svg, f"{league} has no shading in the SVG"


def test_empty_sibling_itl3_is_shaded_at_level_10() -> None:
    """Regression: York ITL3 has no clubs but sits beside North Yorkshire in one league."""
    season = CURRENT_SEASON
    geocoded_dir = str(DATA_DIR / "geocoded_teams" / season)
    loaded = _load_marker_items(geocoded_dir, season, travel_distances=None)
    tier10_items = [it for it in loaded.pyramid if it.tier_num == 10]
    assert tier10_items, "expected tier 10 pyramid teams in geocoded data"

    itl = load_itl_hierarchy(BOUNDARY_PATHS)
    preassign_itl_regions(tier10_items, itl)
    territories, _colours = compute_tier_territories(tier10_items, itl)

    york_geom = itl["itl3_regions"]["York"]["simplified"]
    merged = unary_union(list(territories.values()))
    coverage = merged.intersection(york_geom).area / york_geom.area
    assert coverage > 0.95, f"York ITL3 should be shaded, got {coverage:.1%} coverage"


def test_multiple_empty_sibling_itl3_regions_are_not_filled() -> None:
    """Swindon and Wiltshire are empty ITL3 siblings; neither should be shaded."""
    season = CURRENT_SEASON
    geocoded_dir = str(DATA_DIR / "geocoded_teams" / season)
    loaded = _load_marker_items(geocoded_dir, season, travel_distances=None)
    tier10_items = [it for it in loaded.pyramid if it.tier_num == 10]
    itl = load_itl_hierarchy(BOUNDARY_PATHS)
    preassign_itl_regions(tier10_items, itl)
    territories, _colours = compute_tier_territories(tier10_items, itl)

    north = "Counties 4 Tribute Ale Gloucestershire North"
    assert north in territories
    north_geom = territories[north]
    for name in ("Swindon", "Wiltshire"):
        sibling = itl["itl3_regions"][name]["simplified"]
        overlap = north_geom.intersection(sibling).area / sibling.area
        assert overlap < 0.05, f"{name} should stay unshaded, got {overlap:.1%} North overlap"


def test_level_10_known_colour_collisions_are_separated() -> None:
    """Adjacent L10 league pairs that collided should stay perceptually distinct."""
    from rugby.analysis.palette_distances import delta_e

    season = CURRENT_SEASON
    geocoded_dir = str(DATA_DIR / "geocoded_teams" / season)
    loaded = _load_marker_items(geocoded_dir, season, travel_distances=None)
    tier10_items = [it for it in loaded.pyramid if it.tier_num == 10]
    assert tier10_items

    itl = load_itl_hierarchy(BOUNDARY_PATHS)
    preassign_itl_regions(tier10_items, itl)
    _territories, colours = compute_tier_territories(tier10_items, itl)

    collision_pairs = [
        ("Counties 4 Hampshire", "Counties 4 Surrey"),
        (
            "Counties 4 Tribute Ale Gloucestershire South",
            "Counties 4 Tribute Ale Somerset North",
        ),
        (
            "Counties 4 Tribute Ale Gloucestershire North",
            "Counties 4 Tribute Ale Somerset South",
        ),
        ("Counties 4 Tribute Ale Somerset North", "Counties 4 Yorkshire C"),
    ]
    min_delta_e = 25.0
    for league_a, league_b in collision_pairs:
        distance = delta_e(colours[league_a], colours[league_b])
        assert (
            distance >= min_delta_e
        ), f"{league_a} vs {league_b}: Delta E {distance:.1f} < {min_delta_e}"


def test_auto_badge_diameter_tracks_club_spacing() -> None:
    """Sparse levels should get bigger badges than crowded ones, within the clamps."""
    sparse = np.array([[float(i) * 200.0, 0.0] for i in range(10)])
    crowded = np.array([[float(i) * 8.0, 0.0] for i in range(200)])
    moderate = np.array([[float(i) * 26.0, 0.0] for i in range(60)])

    assert _auto_badge_diameter(sparse, tier_num=1) == BADGE_DIAMETER_CEILING_UPPER
    assert _auto_badge_diameter(crowded, tier_num=7) == BADGE_DIAMETER_FLOOR
    assert (
        BADGE_DIAMETER_FLOOR
        < _auto_badge_diameter(moderate, tier_num=5)
        < BADGE_DIAMETER_CEILING_LOWER
    )
    assert _auto_badge_diameter(sparse, tier_num=1) > _auto_badge_diameter(crowded, tier_num=7)


def test_auto_badge_sizing_across_real_levels() -> None:
    """Level 3 clubs are far apart and level 9 clubs are not; badges should reflect that."""
    season = CURRENT_SEASON
    loaded = _load_marker_items(
        str(DATA_DIR / "geocoded_teams" / season), season, travel_distances=None
    )
    itl = load_itl_hierarchy(BOUNDARY_PATHS)
    project = _make_projector(_country_bounds(_country_mask(itl)), IMAGE_WIDTH, IMAGE_HEIGHT)

    def diameter_for(tier_num: int) -> float:
        items = [it for it in loaded.pyramid if it.tier_num == tier_num]
        assert items, f"expected clubs at tier {tier_num}"
        return _auto_badge_diameter(
            np.array([project(it.longitude, it.latitude) for it in items]),
            tier_num=tier_num,
        )

    assert diameter_for(3) > diameter_for(BADGE_UPPER_TIER_MAX + 1)
    assert diameter_for(BADGE_UPPER_TIER_MAX + 1) <= BADGE_DIAMETER_CEILING_LOWER + 1e-6
    assert diameter_for(5) > diameter_for(6) >= diameter_for(9)
    assert diameter_for(9) == BADGE_DIAMETER_FLOOR


def test_badge_radius_shrinks_in_crowded_areas() -> None:
    positions = np.array([[0.0, 0.0], [500.0, 500.0], [505.0, 500.0]])
    radii = _nearest_neighbour_radii(positions, max_radius=15.0, min_radius=6.0)

    assert radii[0] == 15.0, "isolated club should keep the full badge size"
    assert radii[1] == radii[2] == 6.0, "clustered clubs should shrink to the minimum"


def test_relaxation_separates_and_limits_drift() -> None:
    """Clubs sharing a ground must fan out without wandering off it."""
    positions = np.zeros((4, 2))
    radii = np.full(4, 8.0)

    relaxed = _relax_positions(positions, radii, gap=1.0, iterations=BADGE_RELAX_ITERATIONS)

    separations = [
        float(np.linalg.norm(relaxed[i] - relaxed[j])) for i in range(4) for j in range(i + 1, 4)
    ]
    assert min(separations) > 0.0

    drift = np.linalg.norm(relaxed - positions, axis=1)
    assert drift.max() <= 8.0 * BADGE_MAX_SHIFT_RADII + 1e-6


def test_layout_regrows_badges_after_spacing() -> None:
    """Spacing should buy back size that the initial crowding estimate gave away."""
    positions = np.array([[float(i) * 20.0, 0.0] for i in range(8)])

    shrunk = _nearest_neighbour_radii(positions, max_radius=15.0, min_radius=6.0)
    _, radii = _layout_badges(positions, max_radius=15.0, min_radius=6.0)

    assert radii.mean() > shrunk.mean()
    assert radii.max() <= 15.0 + 1e-6
    assert radii.min() >= 6.0 - 1e-6


def test_layout_respects_the_size_ceiling_and_is_deterministic() -> None:
    positions = np.array([[0.0, 0.0], [0.0, 0.0], [4.0, 3.0], [300.0, 400.0]])

    first_pos, first_radii = _layout_badges(positions, max_radius=12.0, min_radius=5.0)
    second_pos, second_radii = _layout_badges(positions, max_radius=12.0, min_radius=5.0)

    assert np.allclose(first_pos, second_pos)
    assert np.allclose(first_radii, second_radii)
    assert first_radii.max() <= 12.0 + 1e-6
    # The remote club is unconstrained, so it should reach full size.
    assert first_radii[3] == 12.0


def test_relaxation_leaves_spaced_badges_alone() -> None:
    positions = np.array([[0.0, 0.0], [200.0, 0.0]])
    radii = np.full(2, 10.0)

    relaxed = _relax_positions(positions, radii, gap=1.0, iterations=10)

    assert np.allclose(relaxed, positions)


def test_country_mask_covers_england() -> None:
    itl = load_itl_hierarchy(BOUNDARY_PATHS)
    mask = _country_mask(itl)
    assert not mask.is_empty
    minx, miny, maxx, maxy = mask.bounds
    assert minx < -6 and maxx > 1.5
    assert miny < 49.2 and maxy > 55.5


def _sample_badge_item(*, icon_url: str | None) -> MarkerItem:
    return MarkerItem(
        name="Example Rugby Club",
        latitude=51.5,
        longitude=-1.0,
        group="Test League",
        tier="Counties 1",
        tier_num=7,
        icon_url=icon_url,
        popup_html=None,
    )


def test_crest_inline_px_matches_badge_display_at_png_scale() -> None:
    """Crest cache must cover badge inner size × PNG scale to avoid upscaling blur."""
    assert _crest_inline_px(badge_diameter=80.0, png_scale=2.0) == 154
    assert _crest_inline_px(badge_diameter=None, png_scale=1.0) == 64


def test_render_badges_shows_name_when_no_crest() -> None:
    svg = _render_badges(
        [_sample_badge_item(icon_url=None)],
        {"Test League": "#e6194b"},
        lambda lon, lat: (100.0, 100.0),
        {},
        tier_num=7,
        diameter=40.0,
    )
    assert "Example Rugby" in svg
    assert "Club" in svg
    assert "<image" not in svg


def test_render_badges_treats_rfu_fallback_as_missing_crest() -> None:
    svg = _render_badges(
        [_sample_badge_item(icon_url=RFU_FALLBACK_ICON)],
        {"Test League": "#e6194b"},
        lambda lon, lat: (100.0, 100.0),
        {},
        tier_num=7,
        diameter=40.0,
    )
    assert "Example Rugby" in svg
    assert "Club" in svg
    assert RFU_FALLBACK_ICON not in svg
    assert "<image" not in svg


def test_render_badges_renders_crest_when_available() -> None:
    crest_url = "https://images.englandrugby.com/clubs/example.png"
    href = "data:image/png;base64,abc"
    svg = _render_badges(
        [_sample_badge_item(icon_url=crest_url)],
        {"Test League": "#e6194b"},
        lambda lon, lat: (100.0, 100.0),
        {crest_url: href},
        tier_num=7,
        diameter=40.0,
    )
    assert f'href="{href}"' in svg
    assert "Example Rugby Club" not in svg


def test_render_badges_name_when_crest_not_inlined() -> None:
    """Blocked or unreadable crest URLs must not fall back to a remote SVG image."""
    crest_url = "https://images.englandrugby.com/club_images/16502.png"
    svg = _render_badges(
        [_sample_badge_item(icon_url=crest_url)],
        {"Test League": "#e6194b"},
        lambda lon, lat: (100.0, 100.0),
        {},
        tier_num=9,
        diameter=40.0,
    )
    assert "Example Rugby" in svg
    assert crest_url not in svg
    assert "<image" not in svg
