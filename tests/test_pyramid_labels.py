"""Pyramid diagram tier margin labels and league short titles (rugby.pyramid_image)."""

import json

from rugby.pyramid_image import (
    LeagueData,
    _merit_canvas_horizontal_weight_pyramid,
    _merit_chain_single_league_margin_label,
    _merit_equal_column_templates,
    _merit_pyramid_band_column_order,
    _strip_league_title_sponsors,
    _team_lower_xv_roman,
    compute_band_layout,
    league_short_display_name,
    pyramid_band_tier_label,
)


def test_team_lower_xv_roman_reserve_suffixes() -> None:
    assert _team_lower_xv_roman("Avon RFC II") == "II"
    assert _team_lower_xv_roman("Somewhere III") == "III"
    assert _team_lower_xv_roman("Club 2nd XV") == "II"
    assert _team_lower_xv_roman("Club 4th XV") == "IV"
    assert _team_lower_xv_roman("Club 6th XV") == "VI"


def test_team_lower_xv_roman_principal_or_unknown() -> None:
    assert _team_lower_xv_roman("Club 1st XV") is None
    assert _team_lower_xv_roman("Club") is None
    assert _team_lower_xv_roman("") is None


def test_strip_league_title_sponsors_removes_leading_x_marker() -> None:
    assert _strip_league_title_sponsors("xDerbys/N Leics") == "Derbys/N Leics"
    assert _strip_league_title_sponsors("xxNorth Premier") == "North Premier"


def test_strip_league_title_sponsors_drops_rfuw_prefix() -> None:
    """Legacy ``RFUW …`` branding (defunct governing body) is hidden on pyramid titles."""
    assert _strip_league_title_sponsors("RFUW Premiership") == "Premiership"
    assert _strip_league_title_sponsors("RFUW Championship North 1") == "Championship North 1"
    assert _strip_league_title_sponsors("RFUW NC South East South 2") == "NC South East South 2"
    # ``x`` marker still wins as the outermost prefix before RFUW is stripped.
    assert _strip_league_title_sponsors("xRFUW Premiership") == "Premiership"


def test_national_league_short_title_strips_x_before_geo_parse() -> None:
    assert league_short_display_name("xNational League 3 North", 5, "2018-2019") == "North"


def test_merged_merit_league_title_prefixes_competition_when_not_in_name() -> None:
    assert (
        league_short_display_name(
            "Table 1",
            10,
            "2025-2026",
            gender="mens",
            merit_geocoded_competition="Herts_Middlesex",
            prefix_merit_competition_if_absent=True,
        )
        == "Herts Middlesex: Table 1"
    )


def test_merged_merit_league_title_skips_prefix_when_name_contains_competition() -> None:
    assert (
        league_short_display_name(
            "NOWIRUL BAINES PLUMBING DIVISION 1",
            10,
            "2025-2026",
            gender="mens",
            merit_geocoded_competition="NOWIRUL",
            prefix_merit_competition_if_absent=True,
        )
        == "NOWIRUL BAINES PLUMBING DIVISION 1"
    )


def test_strip_league_title_sponsors_removes_inline_greene_king() -> None:
    assert (
        _strip_league_title_sponsors(
            "Eastern Counties Greene King Division One North",
        )
        == "Eastern Counties Division One North"
    )
    assert (
        _strip_league_title_sponsors(
            "Counties 2 Greene King IPA Hampshire Bowl",
        )
        == "Counties 2 Hampshire Bowl"
    )


def test_merit_diagram_strips_leading_hyphen_after_east_midlands_tier_prefix() -> None:
    """RFU uses ``tier - geo``; removing ``East Midlands 2`` must not leave ``- Northants``."""
    assert (
        league_short_display_name(
            "East Midlands 2 - Northants A",
            2,
            "2025-2026",
            strip_merit_tier_display_prefix=True,
            merit_tier_display_label="East Midlands 2",
        )
        == "Northants A"
    )


def test_merit_diagram_strips_local_tier_label_prefix_from_title() -> None:
    """Standalone merit cells use ``tier_name`` as the margin label; drop that prefix from the RFU name."""
    assert (
        league_short_display_name(
            "CANDY 2 North",
            2,
            "2025-2026",
            strip_merit_tier_display_prefix=True,
            merit_tier_display_label="CANDY 2",
        )
        == "North"
    )
    assert (
        league_short_display_name(
            "Eastern Counties Greene King Division One North",
            1,
            "2025-2026",
            strip_merit_tier_display_prefix=True,
            merit_tier_display_label="Eastern Counties 1",
        )
        == "Division One North"
    )


def test_national_and_all_leagues_titles_do_not_strip_merit_tier_label_by_default() -> None:
    assert league_short_display_name("CANDY 2 North", 2, "2025-2026") == "CANDY 2 North"


def test_pyramid_margin_uses_level_below_nl2_before_2022() -> None:
    assert pyramid_band_tier_label(4, "2021-2022", "mens") == "National League 2"
    assert pyramid_band_tier_label(5, "2021-2022", "mens") == "Level 5"
    assert pyramid_band_tier_label(6, "2008-2009", "mens") == "Level 6"
    assert pyramid_band_tier_label(9, "2018-2019", "mens") == "Level 9"


def test_pyramid_margin_uses_modern_names_from_2022() -> None:
    assert pyramid_band_tier_label(5, "2022-2023", "mens") == "Regional 1"
    assert pyramid_band_tier_label(7, "2025-2026", "mens") == "Counties 1"


def _lg(tier: int, name: str) -> LeagueData:
    return LeagueData(tier_num=tier, tier_name="", league_name=name, teams=[], team_count=0)


def test_merit_canvas_weight_covers_widest_band_league_count() -> None:
    """Apex rows with several leagues must not collapse horizontal demand below that count."""
    by = {
        1: [_lg(1, "a"), _lg(1, "b"), _lg(1, "c")],
        2: [_lg(2, "d"), _lg(2, "e"), _lg(2, "f")],
    }
    w = _merit_canvas_horizontal_weight_pyramid(by)
    raw = max(len(by.get(t, ())) for t in range(1, max(by) + 1))
    assert w >= float(raw)


def test_merit_canvas_widens_for_multi_league_widest_band() -> None:
    from rugby.pyramid_image import (
        MERIT_CANVAS_EXTRA_WIDTH_PER_WIDEST_LEAGUE,
        MERIT_CANVAS_MIN_WIDTH,
        _compute_canvas_width_px,
    )

    one = _compute_canvas_width_px(2.0, for_merit=True, merit_widest_band=1)
    three = _compute_canvas_width_px(2.0, for_merit=True, merit_widest_band=3)
    assert one == MERIT_CANVAS_MIN_WIDTH
    assert three == MERIT_CANVAS_MIN_WIDTH + 2 * MERIT_CANVAS_EXTRA_WIDTH_PER_WIDEST_LEAGUE


def test_merit_canvas_weight_deep_single_tier_sits_at_national_base() -> None:
    """Tall merit-only ladder uses shorter rows so the deepest band sits at tier‑6 baseline width."""
    by = {8: [_lg(8, "a"), _lg(8, "b"), _lg(8, "c"), _lg(8, "d")]}
    w = _merit_canvas_horizontal_weight_pyramid(by)
    assert abs(w - 4.0) < 1e-9


def test_merit_canvas_min_width_below_mens_image_width() -> None:
    from rugby.pyramid_image import IMAGE_WIDTH, MERIT_CANVAS_MIN_WIDTH, _compute_canvas_width_px

    assert MERIT_CANVAS_MIN_WIDTH < IMAGE_WIDTH
    small_merit = _compute_canvas_width_px(2.0, for_merit=True)
    mens_floor = _compute_canvas_width_px(2.0, for_merit=False)
    assert small_merit == MERIT_CANVAS_MIN_WIDTH
    assert mens_floor == IMAGE_WIDTH


def test_merit_interior_floor_matches_band_grid_under_merit_canvas() -> None:
    """Merit outline bottom must use band height from the active (narrow) canvas, not IMAGE_WIDTH."""
    from rugby.pyramid_image import (
        MERIT_CANVAS_MIN_WIDTH,
        _canvas_width_scope,
        _merit_pyramid_band_row_divisor_scope,
        _pyramid_band_height_px,
        _pyramid_top_y,
        compute_band_layout,
    )

    with (
        _canvas_width_scope(float(MERIT_CANVAS_MIN_WIDTH)),
        _merit_pyramid_band_row_divisor_scope(3),
    ):
        merit_max_tier = 3
        floor_y = _pyramid_top_y() + merit_max_tier * _pyramid_band_height_px()
        lay3 = compute_band_layout(3, 1)
        assert lay3 is not None
        assert abs(floor_y - lay3.band_bottom) < 1e-6


def test_merit_deep_stack_closes_at_pyramid_base() -> None:
    """More than six merit tiers divide row height so the last band is at tier‑6 baseline."""
    from rugby.pyramid_image import (
        MERIT_CANVAS_MIN_WIDTH,
        _canvas_width_scope,
        _merit_pyramid_band_row_divisor_scope,
        _pyramid_band_height_px,
        _pyramid_bottom_y,
        _pyramid_top_y,
        compute_band_layout,
    )

    with (
        _canvas_width_scope(float(MERIT_CANVAS_MIN_WIDTH)),
        _merit_pyramid_band_row_divisor_scope(7),
    ):
        bh = _pyramid_band_height_px()
        floor_y = _pyramid_top_y() + 7 * bh
        assert abs(floor_y - _pyramid_bottom_y()) < 1e-6
        lay7 = compute_band_layout(7, 1)
        assert lay7 is not None
        assert abs(lay7.band_bottom - _pyramid_bottom_y()) < 1e-6


def test_merit_equal_column_templates_from_shallowest_tier_with_n() -> None:
    by = {
        1: [_lg(1, "a"), _lg(1, "b"), _lg(1, "c")],
        2: [_lg(2, "d"), _lg(2, "e"), _lg(2, "f")],
    }
    tpl = _merit_equal_column_templates(by)
    lay1 = compute_band_layout(1, 1)
    assert lay1 is not None
    ref = compute_band_layout(1, 3, interior_width_y=lay1.band_bottom)
    assert ref is not None
    assert tpl[3].row_left_x == ref.row_left_x
    assert tpl[3].cell_w_raw == ref.cell_w_raw
    narrow = compute_band_layout(1, 3)
    assert narrow is not None
    assert ref.avail_w > narrow.avail_w
    below = compute_band_layout(2, 3)
    assert below is not None
    # Tier 2 interior at its top equals tier 1 band bottom, so default pitch matches ref.
    assert below.row_left_x == ref.row_left_x
    assert below.avail_w == ref.avail_w


def test_merit_parent_aligned_band_gloucester_only_column() -> None:
    """Sparse merit row: one child under two parents uses the parent's column, not full chord."""
    from rugby.pyramid_image import BandLayout, LeagueData, _merit_parent_aligned_band_placements

    prev = [
        LeagueData(2, "", "Bristol & District 2", [], 0),
        LeagueData(2, "", "Gloucester & District 2", [], 0),
    ]
    child = LeagueData(3, "", "Gloucester & District 3", [], 0)
    ovs = {(3, child.league_name): ("Gloucester & District 2",)}
    lay = BandLayout(
        tier_num=3,
        band_top=0.0,
        band_bottom=80.0,
        band_center_y=40.0,
        avail_w=400.0,
        row_left_x=100.0,
        cell_w_raw=200.0,
        gap=10.0,
        cell_w=190.0,
        cell_h=60.0,
        row_top_y=10.0,
    )
    pl = _merit_parent_aligned_band_placements(3, [child], prev, lay, ovs, "GRFU_District")
    assert pl is not None and len(pl) == 1
    _lg, x_rect, _w, col = pl[0]
    assert _lg.league_name == child.league_name
    assert col == 1
    assert x_rect >= lay.row_left_x + lay.cell_w_raw * 0.5


def test_merit_pyramid_band_column_order_candy_2_3_pattern() -> None:
    """Dual-feed child sorts between single-feed columns (tier_mappings parent indices)."""
    t2 = [
        _lg(2, "CANDY 2 North"),
        _lg(2, "CANDY 2 South"),
    ]
    t2_order = _merit_pyramid_band_column_order(2, list(t2), ("CANDY 1",), {})
    assert [x.league_name for x in t2_order] == ["CANDY 2 North", "CANDY 2 South"]

    t3_leagues = [
        _lg(3, "CANDY 3 Central"),
        _lg(3, "CANDY 3 North"),
        _lg(3, "CANDY 3 South"),
    ]
    parents = ("CANDY 2 North", "CANDY 2 South")
    ov = {
        (3, "CANDY 3 North"): ("CANDY 2 North",),
        (3, "CANDY 3 South"): ("CANDY 2 South",),
        (3, "CANDY 3 Central"): ("CANDY 2 North", "CANDY 2 South"),
    }
    t3_order = _merit_pyramid_band_column_order(3, t3_leagues, parents, ov)
    assert [x.league_name for x in t3_order] == [
        "CANDY 3 North",
        "CANDY 3 Central",
        "CANDY 3 South",
    ]


def test_merit_chain_margin_label_uses_league_when_upper_single_or_empty() -> None:
    """Shallow merit slice: empty diagram bands above do not break the ladder."""
    by = {
        1: [_lg(1, "Counties 2 Tribute Ale Devon North & East")],
        2: [_lg(2, "Counties 3 Example")],
    }
    assert _merit_chain_single_league_margin_label(by, 1, "2025-2026", "mens") == (
        league_short_display_name(
            by[1][0].league_name,
            1,
            "2025-2026",
            gender="mens",
            strip_merit_tier_display_prefix=True,
            merit_tier_display_label=by[1][0].tier_name,
        )
    )
    assert _merit_chain_single_league_margin_label(by, 2, "2025-2026", "mens") == (
        league_short_display_name(
            by[2][0].league_name,
            2,
            "2025-2026",
            gender="mens",
            strip_merit_tier_display_prefix=True,
            merit_tier_display_label=by[2][0].tier_name,
        )
    )


def test_merit_chain_margin_label_none_when_upper_tier_splints() -> None:
    by = {
        1: [_lg(1, "Merit A"), _lg(1, "Merit B")],
        2: [_lg(2, "Merit C")],
    }
    assert _merit_chain_single_league_margin_label(by, 1, "2025-2026", "mens") is None
    assert _merit_chain_single_league_margin_label(by, 2, "2025-2026", "mens") is None


def test_pyramid_margin_tier1_championship_before_2009_premiership_pyramid() -> None:
    """Pre–Championship-tier era: apex margin says Championship (not Premiership)."""
    assert pyramid_band_tier_label(1, "2000-2001", "mens") == "Championship"
    assert pyramid_band_tier_label(1, "2008-2009", "mens") == "Championship"
    assert pyramid_band_tier_label(1, "2009-2010", "mens") == "Premiership"
    assert pyramid_band_tier_label(1, "2025-2026", "mens") == "Premiership"


def test_national_league_division_short_title() -> None:
    assert league_short_display_name("National League Three North", 5, "2018-2019") == "North"
    assert league_short_display_name("National League 3 North", 5, "2018-2019") == "North"
    assert league_short_display_name("National League Two South West", 4, "2010-2011") == (
        "South West"
    )


def test_womens_flat_feeder_overrides_json_roundtrip() -> None:
    from rugby.pyramid_image import (
        _womens_flat_feeder_overrides_to_nested_json,
        stem_parent_overrides_flatten_nested,
    )

    flat = {
        (2, "Women's Championship North 1"): ("Women's Premiership",),
        (4, "Women's NC 1 West"): (),
    }
    nested = _womens_flat_feeder_overrides_to_nested_json(flat)
    back = stem_parent_overrides_flatten_nested(nested)
    assert back == flat


def test_womens_parent_overrides_merge_cross_season(tmp_path, monkeypatch) -> None:
    """Infer band-3→2 feeder from another season's tier_mappings JSON when names align."""
    import rugby.pyramid_image as pi

    monkeypatch.setattr(pi, "TIER_MAPPINGS_DIR", tmp_path)
    foreign = {
        "season": "2023-2024",
        "women": {"3": {"Women's Championship North 2": "Women's Championship North 1"}},
    }
    (tmp_path / "2023-2024.json").write_text(json.dumps(foreign), encoding="utf-8")
    (tmp_path / "2024-2025.json").write_text(json.dumps({"season": "2024-2025"}), encoding="utf-8")

    def lg(band: int, name: str) -> pi.LeagueData:
        return pi.LeagueData(tier_num=band, tier_name="", league_name=name, teams=[], team_count=0)

    leagues_by_tier = {
        2: [lg(2, "Women's Championship North 1")],
        3: [lg(3, "Women's Championship North 2")],
    }
    merged = pi.womens_parent_overrides_merge_cross_season("2024-2025", leagues_by_tier, {})
    assert merged[(3, "Women's Championship North 2")] == ("Women's Championship North 1",)

    base_override = {
        (3, "Women's Championship North 2"): ("Women's Premiership",),
    }
    merged2 = pi.womens_parent_overrides_merge_cross_season(
        "2024-2025", leagues_by_tier, dict(base_override)
    )
    assert merged2[(3, "Women's Championship North 2")] == ("Women's Premiership",)
