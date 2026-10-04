"""Measure and fit single lines of text for SVG social graphics.

SVG text doesn't wrap or shrink, so overlong names spill into neighbouring
columns. Lines are measured with the real web fonts the cards use (Oswald /
Barlow, fetched once from the Google Fonts repo into ``data/caches/fonts``) and
fitted by, in order:

1. leaving them alone if they fit;
2. shrinking the font size, down to ``MIN_SCALE`` of the design size;
3. truncating with an ellipsis at that minimum size.

If the fonts can't be fetched (offline), widths fall back to a per-font
average-glyph estimate, which errs on the wide side.
"""

from __future__ import annotations

import contextlib
import logging
import urllib.request
from functools import cache
from pathlib import Path
from typing import Any

from core.config import CACHE_DIR

logger = logging.getLogger(__name__)

FONT_CACHE_DIR = CACHE_DIR / "fonts"

# kind -> (cache filename, source URL). Oswald is a variable font (weight axis);
# Barlow is fetched as the static Medium (500) the cards use.
_FONT_SOURCES: dict[str, tuple[str, str]] = {
    "heading": (
        "Oswald[wght].ttf",
        "https://github.com/google/fonts/raw/main/ofl/oswald/Oswald%5Bwght%5D.ttf",
    ),
    "body": (
        "Barlow-Medium.ttf",
        "https://github.com/google/fonts/raw/main/ofl/barlow/Barlow-Medium.ttf",
    ),
}
# Average glyph width / font-size when the real font is unavailable (slightly
# wide of Oswald 600 at ~0.39 and Barlow 500 at ~0.43, so text never overflows).
_FALLBACK_WIDTH_FACTOR = {"heading": 0.45, "body": 0.48}

# Smallest font size, as a fraction of the design size, before truncating:
# much below this a name stops reading as the same "row" as its neighbours.
MIN_SCALE = 0.8
ELLIPSIS = "…"
_MEASURE_SIZE = 100


@cache
def _font_path(kind: str) -> Path | None:
    filename, url = _FONT_SOURCES[kind]
    path = FONT_CACHE_DIR / filename
    if path.is_file():
        return path
    try:
        FONT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (fixed https URL)
            path.write_bytes(response.read())
        return path
    except OSError as exc:
        logger.warning("Could not fetch %s font (%s); using estimated text widths", kind, exc)
        return None


@cache
def _font(kind: str, weight: int) -> Any | None:
    path = _font_path(kind)
    if path is None:
        return None
    try:
        from PIL import ImageFont  # noqa: PLC0415

        font = ImageFont.truetype(str(path), _MEASURE_SIZE)
        # Static fonts have no weight axis: their one weight is what we measure.
        with contextlib.suppress(OSError):
            font.set_variation_by_axes([weight])
        return font
    except OSError as exc:
        logger.warning("Could not load %s font (%s); using estimated text widths", kind, exc)
        return None


def text_width(text: str, *, kind: str, weight: int, size: float) -> float:
    """Rendered width in px of ``text`` at font-size ``size``."""
    font = _font(kind, weight)
    if font is None:
        return len(text) * size * _FALLBACK_WIDTH_FACTOR[kind]
    return float(font.getlength(text)) * size / _MEASURE_SIZE


def _truncate(text: str, *, kind: str, weight: int, size: float, max_width: float) -> str:
    """Longest prefix of ``text`` + ellipsis that fits (trailing separators trimmed)."""
    for cut in range(len(text) - 1, 0, -1):
        candidate = text[:cut].rstrip(" ·-–,&/(") + ELLIPSIS
        if text_width(candidate, kind=kind, weight=weight, size=size) <= max_width:
            return candidate
    return ELLIPSIS


def fit_line(
    text: str, *, kind: str, weight: int, size: float, max_width: float
) -> tuple[str, float]:
    """``(text, font_size)`` that fits ``max_width``: shrink first, then truncate."""
    width = text_width(text, kind=kind, weight=weight, size=size)
    if width <= max_width:
        return text, size
    scaled = size * max_width / width
    if scaled >= size * MIN_SCALE:
        return text, scaled
    smallest = size * MIN_SCALE
    return _truncate(text, kind=kind, weight=weight, size=smallest, max_width=max_width), smallest


def fit_pair(
    first: str,
    second: str,
    *,
    kind: str,
    weight: int,
    size: float,
    max_width: float,
    separator: str = " v ",
) -> tuple[str, float]:
    """Fit ``"first v second"``, truncating the longer name so both stay visible."""
    joined = f"{first}{separator}{second}"
    text, fitted_size = fit_line(joined, kind=kind, weight=weight, size=size, max_width=max_width)
    if text == joined:
        return text, fitted_size

    def width(s: str) -> float:
        return text_width(s, kind=kind, weight=weight, size=fitted_size)

    a, b = first, second
    while width(f"{a}{separator}{b}") > max_width and (len(a) > 1 or len(b) > 1):
        if len(a.rstrip(ELLIPSIS)) >= len(b.rstrip(ELLIPSIS)):
            a = a.rstrip(ELLIPSIS)[:-1].rstrip(" ·-–,&/(") + ELLIPSIS
        else:
            b = b.rstrip(ELLIPSIS)[:-1].rstrip(" ·-–,&/(") + ELLIPSIS
    return f"{a}{separator}{b}", fitted_size
