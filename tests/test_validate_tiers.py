"""Tests for movement-graph tier validation."""

from __future__ import annotations

from rugby.analysis.validate_tiers import (
    VALIDATE_TIERS_EARLIEST_SEASON,
    _anchor_specs,
    _league_names_in_season,
    analyse_pair,
    get_available_seasons,
)


def test_validate_tiers_earliest_season_includes_1999_2000() -> None:
    assert VALIDATE_TIERS_EARLIEST_SEASON == "1999-2000"
    assert VALIDATE_TIERS_EARLIEST_SEASON in get_available_seasons()


def test_anchor_specs_1999_2000_use_regional_apex_only() -> None:
    specs = _anchor_specs("1999-2000")
    labels = [names[0] for names, _tier in specs]
    assert labels == [
        "London 1",
        "Midlands 1",
        "North 1",
        "South West 1",
        "xNorth East 1",
        "xNorth West 1",
    ]
    assert all(tier == 5 for _names, tier in specs)


def test_anchor_specs_2000_2001_include_national_leagues() -> None:
    specs = _anchor_specs("2000-2001")
    flat = [name for names, _tier in specs for name in names]
    assert "National League One" in flat
    assert "London 1" not in flat


def test_analyse_pair_1999_2000_runs() -> None:
    names = _league_names_in_season("1999-2000")
    assert "London 1" in names
    assert "xBerks/Dorset/Wilts 1" in names

    result = analyse_pair("1999-2000", "2000-2001", focus_season_a=True)
    scoped = len(result.matches) + len(result.mismatches) + len(result.unconnected)
    # A few 1999-only leagues may sit outside the cross-season movement graph.
    assert scoped >= len(names) - 8
    assert len(result.matches) > 0
