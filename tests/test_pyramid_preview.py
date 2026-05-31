"""Tests for pyramid index-page preview PNGs and season index wiring."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from rugby import pyramid_image as pi
from rugby.webpages import _build_pyramid_diagram_preview_html, _detect_pyramid_diagram_pair


def test_write_pyramid_preview_png_downscales(tmp_path: Path) -> None:
    full = tmp_path / "pyramid.png"
    preview = tmp_path / "pyramid.preview.png"
    Image.new("RGB", (1520, 800), color=(240, 240, 240)).save(full)

    assert pi.write_pyramid_preview_png(full, preview, max_width=760) is True
    assert preview.is_file()
    with Image.open(preview) as im:
        assert im.size[0] == 760
        assert im.size[1] == 400


def test_detect_pyramid_diagram_pair_prefers_preview(tmp_path: Path) -> None:
    (tmp_path / "pyramid.svg").write_text("<svg></svg>", encoding="utf-8")
    (tmp_path / "pyramid.png").write_bytes(b"full")
    (tmp_path / "pyramid.preview.png").write_bytes(b"preview")

    pair = _detect_pyramid_diagram_pair(tmp_path, "pyramid")
    assert pair["thumb_src"] == "pyramid.preview.png"
    assert pair["full_href"] == "pyramid.svg"
    assert pair["full_png_href"] == "pyramid.png"


def test_parse_svg_dimensions() -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800"></svg>'
    assert pi._parse_svg_dimensions(svg) == (1200, 800)


def test_stitch_png_tiles(tmp_path: Path) -> None:
    red = Image.new("RGBA", (10, 4), (255, 0, 0, 255))
    blue = Image.new("RGBA", (10, 6), (0, 0, 255, 255))
    out = tmp_path / "out.png"
    pi._stitch_png_tiles([red, blue], out)
    merged = Image.open(out)
    assert merged.size == (10, 10)
    assert merged.getpixel((5, 2)) == (255, 0, 0, 255)
    assert merged.getpixel((5, 7)) == (0, 0, 255, 255)


def test_stem_wants_full_png_only_national_and_all_leagues() -> None:
    assert pi.stem_wants_full_png(Path("pyramid.png"))
    assert pi.stem_wants_full_png(Path("pyramid_Labels.png"))
    assert pi.stem_wants_full_png(Path("pyramid_All_Leagues.png"))
    assert pi.stem_wants_full_png(Path("pyramid_All_Leagues_Labels.png"))
    assert not pi.stem_wants_full_png(Path("pyramid_womens.png"))
    assert not pi.stem_wants_full_png(Path("pyramid_merit_Hampshire.png"))
    assert not pi.stem_wants_full_png(Path("pyramid_merit_Hampshire_Labels.png"))


def test_pyramid_png_output_mode_preview_only_for_merit() -> None:
    args = argparse.Namespace(
        png=True,
        png_force_full=False,
        output=None,
        png_output=None,
    )
    assert pi._pyramid_png_output_mode(Path("pyramid.png"), args) == "full"
    assert pi._pyramid_png_output_mode(Path("pyramid_merit_Essex.png"), args) == "preview_only"
    args.png = False
    assert pi._pyramid_png_output_mode(Path("pyramid.png"), args) == "none"


def test_effective_preview_raster_scale() -> None:
    assert pi._effective_preview_raster_scale(760, 760) == 1.0
    assert pi._effective_preview_raster_scale(1520, 760) == 0.5


def test_preview_html_links_svg_and_swaps_png_on_contextmenu() -> None:
    html = _build_pyramid_diagram_preview_html(
        thumb_src="pyramid.preview.png",
        full_href="pyramid.svg",
        full_png_href="pyramid.png",
        alt="alt",
        aria_label="Open pyramid",
    )
    assert 'href="pyramid.svg"' in html
    assert 'src="pyramid.preview.png"' in html
    assert 'data-full-png="pyramid.png"' in html
    assert "oncontextmenu" in html
    assert "dataset.fullPng" in html
