"""Pyramid diagram tier margin labels and league short titles (rugby.pyramid_image)."""

import json

from rugby.pyramid_image import (
    LeagueData,
    _find_merit_parent_league,
    _merit_band_margin_primary_label,
    _merit_canvas_horizontal_weight_pyramid,
    _merit_equal_column_templates,
    _merit_pyramid_band_column_order,
    _strip_league_title_sponsors,
    _team_lower_xv_roman,
    compute_band_layout,
    league_short_display_name,
    merit_augment_skipped_parent_chains_for_pyramid,
    merit_pyramid_absolute_child_tier,
    pyramid_band_tier_label,
)


def test_merit_augment_skipped_parent_chain_inserts_placeholders() -> None:
    """Grandparent-style tier_mappings links gain synthetic tiers so layout can nest columns."""
    comp = "Demo_Comp"
    apex = LeagueData(
        1,
        "Demo_Comp 1",
        "Parent Apex",
        [],
        0,
        merit_geocoded_competition=comp,
        merit_local_tier=1,
    )
    deep = LeagueData(
        4,
        "Demo_Comp 4",
        "Deep Child",
        [],
        0,
        merit_geocoded_competition=comp,
        merit_local_tier=4,
    )
    lb = {1: [apex], 4: [deep]}
    ovs = {(4, "Deep Child"): ("Parent Apex",)}
    out_lb, out_ovs = merit_augment_skipped_parent_chains_for_pyramid(
        lb,
        ovs,
        season="2022-2023",
        merit_competition=comp,
        merit_local_offset=0,
    )
    assert len(out_lb[2]) == 1 and out_lb[2][0].merit_chain_placeholder
    assert len(out_lb[3]) == 1 and out_lb[3][0].merit_chain_placeholder
    mid2, mid3 = out_lb[2][0].league_name, out_lb[3][0].league_name
    assert out_ovs[(2, mid2)] == ("Parent Apex",)
    assert out_ovs[(3, mid3)] == (mid2,)
    assert out_ovs[(4, "Deep Child")] == (mid3,)


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


def test_merit_diagram_nowirul_championship_league_not_collapsed_at_band_2() -> None:
    """Visual band 2 is not national tier 2; ``… Championship League`` must not become ``League``."""
    assert (
        league_short_display_name(
            "Cotton Traders Championship League",
            2,
            "2018-2019",
            merit_geocoded_competition="NOWIRUL",
            strip_merit_tier_display_prefix=True,
            merit_tier_display_label="NOWIRUL 2",
        )
        == "Championship League"
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


def test_merit_parent_aligned_sparse_row_uses_parent_grid_column() -> None:
    """Child rows inherit the parent's column index from the band above, not sparse list index."""
    from rugby.pyramid_image import BandLayout, LeagueData, _merit_parent_aligned_band_placements

    # Wide band above: four columns; only columns 1 and 3 have leagues.
    lay4 = BandLayout(
        tier_num=4,
        band_top=0.0,
        band_bottom=80.0,
        band_center_y=40.0,
        avail_w=800.0,
        row_left_x=0.0,
        cell_w_raw=200.0,
        gap=8.0,
        cell_w=192.0,
        cell_h=60.0,
        row_top_y=10.0,
    )
    prev_ord = [
        LeagueData(4, "", "parent_b", [], 0),
        LeagueData(4, "", "parent_d", [], 0),
    ]
    prev_cols = {"parent_b": 1, "parent_d": 3}
    child_b = LeagueData(5, "", "child_b", [], 0)
    child_d = LeagueData(5, "", "child_d", [], 0)
    pl = _merit_parent_aligned_band_placements(
        5,
        [child_b, child_d],
        prev_ord,
        lay4,
        {(5, child_b.league_name): ("parent_b",), (5, child_d.league_name): ("parent_d",)},
        "NOWIRUL",
        season="2023-2024",
        merit_local_offset=0,
        prev_league_col_index=prev_cols,
    )
    assert pl is not None
    by_name = {lg.league_name: (col, cw) for lg, _x, cw, col in pl if not lg.merit_column_spacer}
    assert by_name["child_b"][0] == 1
    assert by_name["child_d"][0] == 3
    # Each child should be ~one quarter of the row, not half.
    assert by_name["child_b"][1] < lay4.avail_w * 0.3
    assert by_name["child_d"][1] < lay4.avail_w * 0.3


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
    pl = _merit_parent_aligned_band_placements(
        3,
        [child],
        prev,
        lay,
        ovs,
        "GRFU_District",
        season="2025-2026",
        merit_local_offset=0,
    )
    assert pl is not None and len(pl) == 2
    by_col = {col: (lg, x_rect, cw) for lg, x_rect, cw, col in pl}
    _lg, x_rect, _w = by_col[1]
    assert _lg.league_name == child.league_name
    assert by_col[0][0].merit_column_spacer is True
    assert x_rect >= lay.row_left_x + lay.cell_w_raw * 0.5


def _merit_tc_ld(tier: int, merit_local: int, league_name: str) -> LeagueData:
    """League fixture with explicit RFU merit local tier for geographic parent tests."""
    return LeagueData(
        tier_num=tier,
        tier_name="",
        league_name=league_name,
        teams=[],
        team_count=0,
        merit_local_tier=merit_local,
    )


def test_merit_parent_infer_cardinal_prefix_matches_unique_parent_tail() -> None:
    competition = "Demo"
    child = _merit_tc_ld(3, 3, "Demo 3 North Premier")
    north = _merit_tc_ld(2, 2, "Demo 2 North")
    south = _merit_tc_ld(2, 2, "Demo 2 South")
    picked = _find_merit_parent_league(child, [south, north], competition)
    assert picked is not None
    assert picked.league_name == north.league_name


def test_merit_parent_infer_cardinal_missing_matching_parent_returns_none() -> None:
    competition = "Demo"
    child = _merit_tc_ld(3, 3, "Demo 3 North")
    south = _merit_tc_ld(2, 2, "Demo 2 South")
    west = _merit_tc_ld(2, 2, "Demo 2 West")
    assert _find_merit_parent_league(child, [south, west], competition) is None


def test_merit_parent_infer_cardinal_rejects_remainder_that_has_other_direction() -> None:
    competition = "Demo"
    child = _merit_tc_ld(3, 3, "Demo 3 South East North")
    se = _merit_tc_ld(2, 2, "Demo 2 South East")
    nw = _merit_tc_ld(2, 2, "Demo 2 North West")
    picked = _find_merit_parent_league(child, [nw, se], competition)
    assert picked is None


def test_merit_parent_infer_cardinal_suffix_matches_unique_parent_tail() -> None:
    competition = "Demo"
    child = _merit_tc_ld(3, 3, "Demo 3 Counties North")
    north = _merit_tc_ld(2, 2, "Demo 2 North")
    south = _merit_tc_ld(2, 2, "Demo 2 South")
    picked = _find_merit_parent_league(child, [north, south], competition)
    assert picked is not None
    assert picked.league_name == north.league_name


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


def test_merit_band_margin_label_uses_longest_common_prefix_when_upper_single_or_empty() -> None:
    """Shallow merit slice: LCP margin per band; empty bands above do not affect this band."""
    by = {
        1: [_lg(1, "Counties 2 Tribute Ale Devon North & East")],
        2: [_lg(2, "Counties 3 Example")],
    }
    assert _merit_band_margin_primary_label(by, 1, "2025-2026", "mens") == (
        league_short_display_name(
            by[1][0].league_name,
            1,
            "2025-2026",
            gender="mens",
            strip_merit_tier_display_prefix=True,
            merit_tier_display_label=by[1][0].tier_name,
        )
    )
    assert _merit_band_margin_primary_label(by, 2, "2025-2026", "mens") == (
        league_short_display_name(
            by[2][0].league_name,
            2,
            "2025-2026",
            gender="mens",
            strip_merit_tier_display_prefix=True,
            merit_tier_display_label=by[2][0].tier_name,
        )
    )


def test_merit_band_margin_label_lcp_when_band_splinters_or_single_below() -> None:
    by = {
        1: [_lg(1, "Merit A"), _lg(1, "Merit B")],
        2: [_lg(2, "Merit C")],
    }
    assert _merit_band_margin_primary_label(by, 1, "2025-2026", "mens") == "Merit"
    assert _merit_band_margin_primary_label(by, 2, "2025-2026", "mens") == "Merit C"


def test_merit_band_margin_label_multi_league_shared_prefix() -> None:
    by = {
        3: [
            _lg(3, "Raging Bull Division 4 East"),
            _lg(3, "Raging Bull Division 4 West"),
        ],
    }
    assert _merit_band_margin_primary_label(by, 3, "2012-2013", "mens") == "Division 4"


def test_merit_band_margin_label_rejects_lcp_that_is_only_competition_name() -> None:
    """Shared prefix equal to the competition name is not a valid tier margin label."""
    by = {
        1: [_lg(1, "Sussex Alpha"), _lg(1, "Sussex Beta")],
    }
    assert (
        _merit_band_margin_primary_label(by, 1, "2025-2026", "mens", merit_competition="Sussex")
        is None
    )
    by_em = {
        1: [
            _lg(1, "East Midlands North"),
            _lg(1, "East Midlands South"),
        ],
    }
    assert (
        _merit_band_margin_primary_label(
            by_em, 1, "2025-2026", "mens", merit_competition="East_Midlands"
        )
        is None
    )


def test_pyramid_margin_tier1_championship_before_2009_premiership_pyramid() -> None:
    """Pre–Championship-tier era: apex margin says Championship (not Premiership)."""
    assert pyramid_band_tier_label(1, "2000-2001", "mens") == "Championship"
    assert pyramid_band_tier_label(1, "2008-2009", "mens") == "Championship"
    assert pyramid_band_tier_label(1, "2009-2010", "mens") == "Premiership"
    assert pyramid_band_tier_label(1, "2025-2026", "mens") == "Premiership"


def test_merit_absolute_tier_apex_east_midlands_nottinghamshire_2008_2009() -> None:
    """Bombardier (East Midlands) is absolute tier 11 with offset 10 through 2010-2011.

    Nottinghamshire Group 1 is absolute 11 with offset 10 whenever apex feeds Midlands 5 East
    (North) (2008-2009–2019-2020 in tier_mappings).
    """
    for season in ("2008-2009", "2009-2010", "2010-2011"):
        assert merit_pyramid_absolute_child_tier("East_Midlands", 1, season) == 11
    for season in ("2008-2009", "2009-2010", "2010-2011", "2019-2020"):
        assert merit_pyramid_absolute_child_tier("Nottinghamshire", 1, season) == 11


def test_east_midlands_pyramid_preserves_sponsor_in_title() -> None:
    """East Midlands merit pyramid labels keep sponsor wording (skip global sponsor stripping)."""
    assert (
        league_short_display_name(
            "Bombardier League",
            11,
            "2008-2009",
            merit_geocoded_competition="East_Midlands",
        )
        == "Bombardier League"
    )
    # East Midlands ladder sponsors are not in :data:`rugby.tiers._SPONSOR_PREFIXES`; they are
    # league identity (see 2011–2012 merit filenames) and match via ``_NAMED_MERIT_LEAGUES``.
    assert league_short_display_name("Bombardier League", 11, "2008-2009") == "Bombardier League"


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


def test_stem_parent_override_prefers_same_merit_competition() -> None:
    """Generic parent labels like 'Division 1' must not attach to another county's league."""
    from rugby.pyramid_image import (
        LeagueData,
        _find_merit_parent_league,
        _merit_counties_hint_matches,
        _resolve_stem_parents,
    )

    assert not _merit_counties_hint_matches("essex", "herts/middlesex 1")
    assert _merit_counties_hint_matches("essex", "essex (canterbury jack) league 1 premier")

    essex_d1 = LeagueData(
        10,
        "Level 10",
        "Division 1",
        [],
        0,
        merit_geocoded_competition="Essex",
        merit_local_tier=1,
    )
    middx_d1 = LeagueData(
        10,
        "Level 10",
        "Division 1",
        [],
        0,
        merit_geocoded_competition="Middlesex",
        merit_local_tier=1,
    )
    middx_d2 = LeagueData(
        11,
        "Level 11",
        "Division 2",
        [],
        0,
        merit_geocoded_competition="Middlesex",
        merit_local_tier=2,
    )
    parents = [essex_d1, middx_d1]
    assert (
        _find_merit_parent_league(middx_d2, parents, "Middlesex").merit_geocoded_competition
        == "Middlesex"
    )
    overrides = {(11, "Division 2"): ("Division 1",)}
    resolved = _resolve_stem_parents(
        middx_d2,
        parents,
        "2017-2018",
        overrides,
        merit_competition="Middlesex",
        leagues_by_tier={10: parents, 11: [middx_d2]},
    )
    assert len(resolved) == 1
    assert resolved[0].merit_geocoded_competition == "Middlesex"
    assert resolved[0].league_name == "Division 1"


def test_stem_forest_merit_division_name_collision() -> None:
    """Stem forest must link by (competition, name), not bare league name alone."""
    from rugby.pyramid_image import (
        _build_stem_forest,
        _stem_forest_registry_key,
        load_pyramid_leagues_with_merit,
        stem_parent_overrides_load,
        stem_parent_overrides_merge_merit_sections_for_absolute_tiers,
    )

    assert _stem_forest_registry_key(
        LeagueData(10, "L10", "Division 1", [], 0, merit_geocoded_competition="Essex")
    ) != _stem_forest_registry_key(
        LeagueData(10, "L10", "Division 1", [], 0, merit_geocoded_competition="Middlesex")
    )

    season = "2017-2018"
    leagues = load_pyramid_leagues_with_merit(season)
    leagues_by_tier: dict[int, list] = {}
    for lg in leagues:
        leagues_by_tier.setdefault(lg.tier_num, []).append(lg)
    merged = stem_parent_overrides_merge_merit_sections_for_absolute_tiers(
        season, dict(stem_parent_overrides_load(season) or {})
    )
    roots, _orphans = _build_stem_forest(leagues_by_tier, season, merged)

    parent_node: dict[int, object] = {}

    def walk(n) -> None:
        for ch in n.children:
            if ch.league.merit_geocoded_competition == "Middlesex":
                parent_node[id(ch.league)] = n.league
            walk(ch)

    for root in roots:
        walk(root)

    middx_d2 = next(
        lg
        for lg in leagues
        if lg.league_name == "Division 2" and lg.merit_geocoded_competition == "Middlesex"
    )
    par = parent_node.get(id(middx_d2))
    assert par is not None
    assert par.league_name == "Division 1"
    assert par.merit_geocoded_competition == "Middlesex"
