"""Tests for core.colors."""

import pytest

from core.colors import COLOR_PALETTE, contrasting_shade, mix_hex


def test_mix_hex_interpolates_between_endpoints() -> None:
    assert mix_hex("#000000", "#ffffff", 0.0) == "#000000"
    assert mix_hex("#000000", "#ffffff", 1.0) == "#ffffff"
    assert mix_hex("#000000", "#ffffff", 0.5) == "#808080"


def test_mix_hex_accepts_shorthand_and_clamps() -> None:
    assert mix_hex("#fff", "#000", 0.0) == "#ffffff"
    assert mix_hex("#fff", "#000", 2.0) == "#000000"


def test_mix_hex_rejects_malformed_colours() -> None:
    with pytest.raises(ValueError, match="rrggbb"):
        mix_hex("nonsense", "#000000", 0.5)


def test_contrasting_shade_darkens_light_and_lightens_dark() -> None:
    """Always darkening would turn the navy palette entry into near-black."""
    assert contrasting_shade("#ffe119") == "#8c7c0e"
    assert contrasting_shade("#000080") == "#7373b9"


def test_contrasting_shade_is_visible_against_every_palette_colour() -> None:
    from rugby.analysis.palette_distances import delta_e

    for colour in COLOR_PALETTE:
        distance = delta_e(colour, contrasting_shade(colour))
        assert distance >= 15.0, f"{colour} shade is too close to it: Delta E {distance:.1f}"
