"""Tests for ONS boundary detail level fallback in core.boundaries."""

from pathlib import Path

import pytest

from core.boundaries import (
    _fallback_levels_for_layer,
    boundary_paths_for_detail,
    resolve_boundary_file,
)


@pytest.fixture
def boundaries_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("core.boundaries.BOUNDARIES_DIR", tmp_path)
    return tmp_path


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def test_fallback_levels_for_buc_wards_prefers_bgc(boundaries_root: Path) -> None:
    levels = _fallback_levels_for_layer("wards.geojson", "BUC")
    assert levels[0] == "BGC"
    assert "BUC" not in levels


def test_fallback_levels_for_bsc_itl_prefers_buc_then_bgc() -> None:
    levels = _fallback_levels_for_layer("ITL_1.geojson", "BSC")
    assert levels[0] == "BUC"
    assert levels[1] == "BGC"
    assert "BSC" not in levels


def test_resolve_boundary_file_uses_nearest_level_with_layer(
    boundaries_root: Path,
) -> None:
    _touch(boundaries_root / "BUC" / "countries.geojson")
    _touch(boundaries_root / "BGC" / "wards.geojson")

    resolved = resolve_boundary_file("BUC", "wards.geojson")
    assert resolved == boundaries_root / "BGC" / "wards.geojson"


def test_boundary_paths_for_detail_falls_back_per_layer(boundaries_root: Path) -> None:
    _touch(boundaries_root / "BUC" / "countries.geojson")
    _touch(boundaries_root / "BUC" / "ITL_1.geojson")
    _touch(boundaries_root / "BUC" / "ITL_2.geojson")
    _touch(boundaries_root / "BUC" / "ITL_3.geojson")
    _touch(boundaries_root / "BUC" / "local_authority_districts.geojson")
    _touch(boundaries_root / "BGC" / "wards.geojson")

    paths = boundary_paths_for_detail("BUC")

    assert Path(paths["countries"]) == boundaries_root / "BUC" / "countries.geojson"
    assert Path(paths["wards"]) == boundaries_root / "BGC" / "wards.geojson"
