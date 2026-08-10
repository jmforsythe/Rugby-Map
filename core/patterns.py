"""Diagonal stripe fills shared by the SVG and Leaflet map renderers.

Parallel league structures occupy the same ground, so they are drawn stacked
rather than carved up between them. The upper structure is striped: with
transparent gaps the territory underneath still reads through.
"""

from __future__ import annotations

from html import escape

STRIPE_PERIOD = 12.0
"""Width of one stripe cycle, in the renderer's own units (SVG user space / CSS px)."""

STRIPE_ANGLE = 45.0

SWATCH_STRIPE_PERIOD = 6.0
"""Tighter cycle for legend swatches, which are only a dozen or so pixels wide."""


def stripe_pattern_svg(
    pattern_id: str,
    *,
    stripe: str,
    background: str | None = None,
    period: float = STRIPE_PERIOD,
    angle: float = STRIPE_ANGLE,
) -> str:
    """An SVG ``<pattern>`` of diagonal *stripe* bands over *background*.

    A *background* of ``None`` leaves the gaps transparent, which is what an
    overlaid structure needs. Pass a colour for a self-contained two-tone fill.
    """
    bands = ""
    if background is not None:
        bands += (
            f'<rect width="{period:g}" height="{period:g}" '
            f'fill="{escape(background, quote=True)}"/>'
        )
    bands += (
        f'<rect width="{period / 2:g}" height="{period:g}" fill="{escape(stripe, quote=True)}"/>'
    )
    return (
        f'<pattern id="{escape(pattern_id, quote=True)}" patternUnits="userSpaceOnUse" '
        f'width="{period:g}" height="{period:g}" '
        f'patternTransform="rotate({angle:g})">'
        f"{bands}"
        f"</pattern>"
    )


def stripe_css_gradient(
    stripe: str,
    *,
    period: float = SWATCH_STRIPE_PERIOD,
    angle: float = STRIPE_ANGLE,
) -> str:
    """A CSS twin of :func:`stripe_pattern_svg` for HTML legend swatches."""
    half = period / 2
    return (
        f"repeating-linear-gradient({angle:g}deg, {stripe} 0 {half:g}px, "
        f"transparent {half:g}px {period:g}px)"
    )
