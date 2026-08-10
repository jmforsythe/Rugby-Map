"""Tests for rugby.analysis.instagram_gallery."""

from pathlib import Path

from core.config import CURRENT_SEASON
from rugby.analysis.instagram_gallery import (
    SEASON_CAROUSEL_NAME,
    collect_legacy_slides,
    collect_level_slides,
    collect_season_slides,
    write_season_carousel,
)
from rugby.analysis.pyramid_gallery import build_html


def test_collect_level_slides_prefers_png_and_sorts_tiers(tmp_path: Path) -> None:
    season_dir = tmp_path / CURRENT_SEASON
    season_dir.mkdir(parents=True)
    (season_dir / "level_02_championship.svg").write_text("<svg/>", encoding="utf-8")
    (season_dir / "level_02_championship.png").write_bytes(b"png")
    (season_dir / "level_01_premiership.png").write_bytes(b"png")

    slides = collect_level_slides(tmp_path, seasons=[CURRENT_SEASON])

    assert len(slides) == 2
    assert slides[0]["href"] == f"{CURRENT_SEASON}/level_01_premiership.png"
    assert slides[1]["href"] == f"{CURRENT_SEASON}/level_02_championship.png"
    assert "Level 1" in slides[0]["label"]
    assert CURRENT_SEASON in slides[0]["label"]


def test_collect_level_slides_boundary_detail_subdir(tmp_path: Path) -> None:
    detail_dir = tmp_path / CURRENT_SEASON / "boundary-detail" / "BGC"
    detail_dir.mkdir(parents=True)
    (detail_dir / "level_04_national_league_2.png").write_bytes(b"png")

    slides = collect_level_slides(
        tmp_path,
        seasons=[CURRENT_SEASON],
        boundary_detail="BGC",
    )

    assert len(slides) == 1
    assert (
        slides[0]["href"] == f"{CURRENT_SEASON}/boundary-detail/BGC/level_04_national_league_2.png"
    )


def test_collect_legacy_slides_skips_level_files(tmp_path: Path) -> None:
    season_dir = tmp_path / CURRENT_SEASON
    season_dir.mkdir(parents=True)
    (season_dir / "01_tiers1-4_1of3.png").write_bytes(b"png")
    (season_dir / "level_01_premiership.png").write_bytes(b"png")

    slides = collect_legacy_slides(tmp_path, seasons=[CURRENT_SEASON])

    assert len(slides) == 1
    assert slides[0]["href"] == f"{CURRENT_SEASON}/01_tiers1-4_1of3.png"


def test_collect_season_slides_uses_local_hrefs(tmp_path: Path) -> None:
    season_dir = tmp_path / CURRENT_SEASON
    season_dir.mkdir(parents=True)
    (season_dir / "level_01_premiership.png").write_bytes(b"png")
    (season_dir / "level_02_championship.svg").write_text("<svg/>", encoding="utf-8")

    slides = collect_season_slides(season_dir, CURRENT_SEASON)

    assert len(slides) == 2
    assert slides[0]["href"] == "level_01_premiership.png"
    assert slides[1]["href"] == "level_02_championship.svg"


def test_write_season_carousel_uses_default_zoom(tmp_path: Path) -> None:
    season_dir = tmp_path / CURRENT_SEASON
    season_dir.mkdir(parents=True)
    (season_dir / "level_01_premiership.png").write_bytes(b"png")

    out = write_season_carousel(season_dir, CURRENT_SEASON)
    assert out == season_dir / SEASON_CAROUSEL_NAME

    html = out.read_text(encoding="utf-8")
    assert "let zoom = 0.3;" in html
    assert ">30%</span>" in html


def test_build_gallery_html_from_slides(tmp_path: Path) -> None:
    season_dir = tmp_path / CURRENT_SEASON
    season_dir.mkdir(parents=True)
    (season_dir / "level_01_premiership.png").write_bytes(b"png")
    out = tmp_path / "gallery.html"

    slides = collect_level_slides(tmp_path, seasons=[CURRENT_SEASON])
    out.write_text(
        build_html(
            slides,
            page_title="Instagram maps gallery",
            image_alt="Instagram map",
            empty_message="No Instagram maps — run instagram_maps first.",
        ),
        encoding="utf-8",
    )

    html = out.read_text(encoding="utf-8")
    assert "Instagram maps gallery" in html
    assert f"{CURRENT_SEASON}/level_01_premiership.png" in html
