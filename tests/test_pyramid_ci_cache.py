"""Tests for per-season pyramid raster CI cache."""

from __future__ import annotations

from pathlib import Path

from rugby import pyramid_ci_cache as cache


def _stub_code_paths(tmp_path: Path) -> None:
    """Give the digest stable code inputs, refusing to touch anything outside ``tmp_path``."""
    for code in cache.pyramid_code_paths():
        assert tmp_path in code.parents, f"would write to real source file {code}"
        code.parent.mkdir(parents=True, exist_ok=True)
        code.write_text("# stub\n", encoding="utf-8")


def test_digest_changes_when_geocoded_data_changes(tmp_path: Path, monkeypatch) -> None:
    season = "2099-2099"
    geo = tmp_path / "data" / "rugby" / "geocoded_teams" / season
    geo.mkdir(parents=True)
    league = geo / "National_1.json"
    league.write_text('{"league_name": "x", "teams": []}', encoding="utf-8")

    monkeypatch.setattr(cache, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cache, "DIST_DIR", tmp_path / "dist")
    monkeypatch.setattr(cache, "PYRAMID_RASTER_CACHE_ROOT", tmp_path / "_pyramid_raster_cache")
    _stub_code_paths(tmp_path)

    d1 = cache.pyramid_raster_inputs_digest(season)
    league.write_text('{"league_name": "y", "teams": []}', encoding="utf-8")
    d2 = cache.pyramid_raster_inputs_digest(season)
    assert d1 != d2


def test_save_restore_round_trip(tmp_path: Path, monkeypatch) -> None:
    season = "2098-2098"
    monkeypatch.setattr(cache, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cache, "DIST_DIR", tmp_path / "dist")
    monkeypatch.setattr(cache, "PYRAMID_RASTER_CACHE_ROOT", tmp_path / "_pyramid_raster_cache")
    _stub_code_paths(tmp_path)

    geo = tmp_path / "data" / "rugby" / "geocoded_teams" / season
    geo.mkdir(parents=True)
    (geo / "National_1.json").write_text("{}", encoding="utf-8")

    dist = tmp_path / "dist" / season
    dist.mkdir(parents=True)
    (dist / "pyramid.svg").write_text("<svg></svg>", encoding="utf-8")
    (dist / "pyramid.preview.png").write_bytes(b"preview")
    (dist / "pyramid.png").write_bytes(b"full")

    assert cache.save_pyramid_raster_cache(season) == 0
    (dist / "pyramid.png").unlink()
    assert cache.cache_is_valid(season)
    assert cache.restore_pyramid_raster_cache(season) == 0
    assert (dist / "pyramid.png").is_file()
