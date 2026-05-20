"""Tests for pyramid index-page preview PNGs and season index wiring."""

from __future__ import annotations

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
