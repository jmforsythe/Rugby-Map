"""Pyramid diagram tier margin labels and league short titles (rugby.pyramid_image)."""

import json

from rugby.pyramid_image import (
    _strip_league_title_sponsors,
    _team_lower_xv_roman,
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


def test_pyramid_margin_uses_level_below_nl2_before_2022() -> None:
    assert pyramid_band_tier_label(4, "2021-2022", "mens") == "National League 2"
    assert pyramid_band_tier_label(5, "2021-2022", "mens") == "Level 5"
    assert pyramid_band_tier_label(6, "2008-2009", "mens") == "Level 6"
    assert pyramid_band_tier_label(9, "2018-2019", "mens") == "Level 9"


def test_pyramid_margin_uses_modern_names_from_2022() -> None:
    assert pyramid_band_tier_label(5, "2022-2023", "mens") == "Regional 1"
    assert pyramid_band_tier_label(7, "2025-2026", "mens") == "Counties 1"


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
