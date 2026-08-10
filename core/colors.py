"""Shared league colour palette for map territory shading."""

from __future__ import annotations

# Wong (2011)–style qualitative palette; extended for dense county-tier maps.
COLOR_PALETTE: list[str] = [
    "#e6194b",
    "#3cb44b",
    "#ffe119",
    "#0082c8",
    "#f58231",
    "#911eb4",
    "#46f0f0",
    "#f032e6",
    "#6a8f00",
    "#fabebe",
    "#008080",
    "#e6beff",
    "#aa6e28",
    "#fffac8",
    "#800000",
    "#008f5a",
    "#808000",
    "#2ecc71",
    "#000080",
    "#808080",
    "#ff6b6b",
    "#34495e",
    "#95e1d3",
    "#9b59b6",
    "#aa96da",
    "#fcbad3",
    "#d4a017",
    "#ffcfd2",
    "#5b2c6f",
    "#1a5276",
    "#b9441e",
    "#117a65",
    "#7d3c98",
    "#2e4053",
    "#c0392b",
    "#1f618d",
    "#884ea0",
    "#239b56",
    "#b7950b",
    "#6c3483",
    "#2874a6",
    "#ca6f1e",
    "#148f77",
    "#a04000",
    "#1b4f72",
    "#7b241c",
]

UNASSIGNED_COLOR = "#cccccc"


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(channel * 2 for channel in value)
    if len(value) != 6:
        raise ValueError(f"Expected a #rrggbb colour, got {color!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _perceived_brightness(color: str) -> float:
    """0-1 brightness weighted for how the eye reads each channel (ITU-R BT.601)."""
    red, green, blue = _rgb(color)
    return (0.299 * red + 0.587 * green + 0.114 * blue) / 255.0


def mix_hex(color: str, target: str, amount: float) -> str:
    """Blend *color* toward *target*; 0 leaves it alone, 1 returns *target*."""
    weight = min(max(amount, 0.0), 1.0)
    blended = (
        round(start + (end - start) * weight)
        for start, end in zip(_rgb(color), _rgb(target), strict=True)
    )
    return "#" + "".join(f"{channel:02x}" for channel in blended)


def contrasting_shade(color: str, amount: float = 0.45) -> str:
    """A visibly different sibling of *color*, for two-tone fills.

    Light hues darken and dark hues lighten, because always darkening turns the
    navy and maroon entries of the palette into indistinguishable black.
    """
    target = "#000000" if _perceived_brightness(color) > 0.45 else "#ffffff"
    return mix_hex(color, target, amount)
