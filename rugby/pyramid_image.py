"""Generate a hierarchical pyramid image of the English rugby pyramid.

By default this renders the **men's** pyramid (tiers 1–11, with a Counties stem under
tier 6). Pass ``--womens`` for the women's pyramid (Premiership → Championship →
National Challenge, no Counties stem; bands 1–4 use feeder-aware horizontal proportions when
resolution succeeds and bands 5–6 are equal-width rows; the loader re-stamps absolute tiers 101–106 down
to visual bands 1–6 so the same triangle geometry, palette, and crest cells render
unchanged).

Outputs an SVG to ``dist/<season>/pyramid.svg`` for the men's national pyramid (or
``pyramid_womens.svg`` with ``--womens``). After ``--merit``, an **all-leagues** men's diagram
(merit leagues merged at absolute tiers, same offsets as All Leagues maps) is written to
``dist/<season>/pyramid_All_Leagues.{svg,png}``, leaving ``pyramid.svg`` as national-only. With ``--png``, rasterised PNGs use Playwright (already a dev
dependency). PNG export polls ``<img>`` crest loads instead of waiting for
``networkidle`` so slow RFU hosts do not hang the run.

Cross-tier parent links, sibling ordering, and optional ``stem_slot_strips`` all live
in ``data/rugby/tier_mappings/<season>.json``; mappings from other seasons are merged
in when a league name matches (closest season first), and newly inferred links are
written back so later runs do not repeat inference. See the section banners further
down this file for the exact JSON schema and how each entry feeds layout.

Usage::

    python -m rugby.pyramid_image
    python -m rugby.pyramid_image --season 2024-2025
    python -m rugby.pyramid_image --png
    python -m rugby.pyramid_image --transparent-white-crest-backgrounds
    python -m rugby.pyramid_image --png --png-image-timeout-ms 45000
    python -m rugby.pyramid_image --output some/path.svg --png-output some/path.png
    python -m rugby.pyramid_image --interactive-stem-orphans
    python -m rugby.pyramid_image --ignore-saved-stem-parent-overrides
    python -m rugby.pyramid_image --ignore-stem-slot-strips
    python -m rugby.pyramid_image --womens
    python -m rugby.pyramid_image --womens --season 2024-2025 --png
    python -m rugby.pyramid_image --womens --interactive-stem-orphans  # TTY: bands 2–4 feeders
"""

from __future__ import annotations

import argparse
import base64
import colorsys
import contextlib
import contextvars
import hashlib
import html
import io
import json
import logging
import math
import re
import sys
import time
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape as xml_escape

from core import setup_logging
from core.config import CACHE_DIR, DIST_DIR
from core.http import get_headers
from rugby import DATA_DIR, short_season
from rugby.addresses import team_name_to_club_name
from rugby.tiers import (
    _strip_sponsor_prefix,
    extract_tier,
    get_competition_offset,
    mens_current_tier_name,
    womens_current_tier_name,
)

logger = logging.getLogger(__name__)

# ``"mens"`` renders the men's pyramid (tiers 1–11, with a Counties stem under tier 6).
# ``"womens"`` renders the women's pyramid (Premiership → Championship → National Challenge,
# absolute tiers 101–106 in JSON, re-stamped to visual bands 1–6 by the loader).
Gender = Literal["mens", "womens"]
DEFAULT_GENDER: Gender = "mens"

# Internally, both genders use **visual** band numbers 1–6 for layout/colour lookups.
# The men's stem (tiers 7+) only exists for ``gender == "mens"``.
_PYRAMID_TIER_NUM_MIN = 1
_PYRAMID_TIER_NUM_MAX_WOMENS = 6  # women's data only goes to NC 3 (visual band 6)

STEM_PARENT_OVERRIDE_SCHEMA_VERSION = 2

# Counties stem overrides: ``(child tier, child league_name) -> parent league names``.
# Empty tuple = explicitly unlinked (JSON ``"-"``). One string = single parent. JSON array =
# span one cell across the horizontal union of those parents' tier-(N−1) bands.
StemParentOverrides = dict[tuple[int, str], tuple[str, ...]]


@dataclass(frozen=True)
class StemSlotBand:
    """One stem tier row inside a :class:`StemSlotStrip` (left-to-right league order)."""

    tier: int
    leagues: tuple[str, ...]
    weights: tuple[float, ...]


@dataclass(frozen=True)
class StemSlotStrip:
    """Horizontal bands sharing one bbox (union of leagues before layout)."""

    bands: tuple[StemSlotBand, ...]


GEOCODED_DIR = DATA_DIR / "geocoded_teams"
TIER_MAPPINGS_DIR = DATA_DIR / "tier_mappings"

_SEASON_RE = re.compile(r"^[12]\d{3}-[12]\d{3}$")
_TIER_MAPPING_FILENAME_RE = re.compile(r"^(?P<season>[12]\d{3}-[12]\d{3})\.json$")

# ---------------------------------------------------------------------------
# Layout parameters
# ---------------------------------------------------------------------------

# Total image width in user units (SVG is scalable; this just sets proportions).
# 2800px canvas widens tier-6 base / Counties stem vs the original 2400 design so lower
# leagues gain horizontal room inside the silhouette.
IMAGE_WIDTH = 3200
# Inner playable chord scales from this baseline horizontal ``weight'' (widest men's pyramid band
# is ~26 leagues; merged merit raises tier‑7 stem demand via summed subtree footprints — see
# :func:`_canvas_horizontal_weight`).
REFERENCE_HORIZONTAL_WEIGHT_CAP = 26

_canvas_width_px_cv: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "_canvas_width_px_cv", default=None
)


def _effective_canvas_width_px() -> float:
    """Active SVG/page width during :func:`render_pyramid_svg` (defaults to :data:`IMAGE_WIDTH`)."""
    w = _canvas_width_px_cv.get()
    return float(IMAGE_WIDTH if w is None else w)


def _compute_canvas_width_px(canvas_horizontal_weight: float) -> int:
    """Expand canvas when horizontal demand exceeds :data:`REFERENCE_HORIZONTAL_WEIGHT_CAP`.

    Demand uses league counts on pyramid tiers 1–6 and summed :func:`_stem_branch_column_weight`
    values on stem rows (tier‑7 roots and orphan subtrees), so merged merit ladders widen the
    SVG even when the widest league count barely changes.
    """
    cap_w = max(1.0, float(canvas_horizontal_weight))
    inner_ref = IMAGE_WIDTH - 2 * PAGE_MARGIN_X
    slot_px = max(1, int(round(inner_ref / float(REFERENCE_HORIZONTAL_WEIGHT_CAP))))
    inner = slot_px * int(math.ceil(cap_w))
    total = PAGE_MARGIN_X * 2 + inner
    return max(IMAGE_WIDTH, total)


@contextlib.contextmanager
def _canvas_width_scope(width_px: float):
    tok = _canvas_width_px_cv.set(width_px)
    try:
        yield
    finally:
        _canvas_width_px_cv.reset(tok)


# Outer page margin (transparent gutter around the whole graphic).
PAGE_MARGIN_X = 60
PAGE_MARGIN_TOP = 90
PAGE_MARGIN_BOTTOM = 80
# Height of the title strip at the top (inside the page margin).
TITLE_STRIP_HEIGHT = 110

# Pyramid (tiers 1–6) geometry.
PYRAMID_BAND_HEIGHT = 360  # vertical extent of one tier row inside the triangle
PYRAMID_NUM_BANDS = 6
PYRAMID_HEIGHT = PYRAMID_BAND_HEIGHT * PYRAMID_NUM_BANDS

# The triangle apex is placed *above* the visible image area so that tier 1
# has a comfortably wide band (rather than collapsing toward a sharp point).
# Larger absolute values mean a less aggressive taper — i.e. wider top tiers.
PYRAMID_APEX_OFFSET = 1100

# Stem (tiers 7–11): same chord width as the tier‑6 base; divider line below Regional 2.
COUNTIES_ROW_HEIGHT = 220
COUNTIES_TIER_GAP = 30  # vertical gap between stem tier sections
STEM_INNER_MARGIN_H = 8.0  # horizontal inset matching :func:`compute_band_layout`
STEM_BOTTOM_MARGIN_Y = 20.0
# Solid band between pyramid tier 6 and Counties stem — inset inside the triangle outline;
# fill is a midpoint of tier 6 / 7 league cell colours (``TIER_COLORS``).
# One gutter thickness ``G`` (``TIER67_SEPARATOR_GAP_PX``) above, below, and inset each side of
# the bar; bar height is ``G * TIER67_SEPARATOR_BAR_GAP_MULT``.
TIER67_SEPARATOR_GAP_PX = 10.0
TIER67_SEPARATOR_BAR_GAP_MULT = 3.0
# Midpoint (#a8c8d8 + #d4d4d4) / 2 → hue between Counties and Regional 2 fills.
TIER67_SEPARATOR_BAR_FILL = "#beced6"
TIER67_SEPARATOR_BAR_STROKE_TOP = "#8eb4c6"
TIER67_SEPARATOR_BAR_STROKE_BOTTOM = "#d2dde4"
STEM_CHILD_GAP_PX = 5.0
STEM_ORPHAN_ROW_GAP_PX = 8.0
COUNTIES_ORPHAN_ROW_HEIGHT = 176  # second league row height when orphans need a fallback band
# Margin captions for tiers 7+ (vertical stem edge); SVG rotate(deg cx cy). Legacy was 90°;
# offset +180° so labels read flipped along that axis.
COUNTIES_MARGIN_TIER_LABEL_ROTATE_DEG = 270.0

# League cell appearance.
LEAGUE_CELL_PADDING_X = 6
LEAGUE_CELL_PADDING_Y = 4
LEAGUE_TITLE_HEIGHT = 26  # minimum title band height
LEAGUE_TITLE_FONT_MAX = 13.0
LEAGUE_TITLE_FONT_MIN = 9.0
LEAGUE_TITLE_LINE_HEIGHT_RATIO = 1.22
LEAGUE_TITLE_CHAR_WIDTH_EM = 0.52  # Latin sans-ish average em per character (wrapping heuristic)
# When a pyramid band has only one league we skip drawing its in-cell title (the margin tier
# label suffices). Still reserve ~the same vertical slice titled neighbours use so crest grids do
# not scale up purely from extra headroom.
LEAGUE_LOGO_GRID_TITLE_RESERVE_Y = float(LEAGUE_TITLE_HEIGHT) + LEAGUE_CELL_PADDING_Y + 6.0
# Women's Premiership is a lone band-1 cell with no in-cell title; less reserve yields crest scale
# closer to men's Prem without shrinking tier 2+ cells that align with titled neighbours.
LEAGUE_LOGO_GRID_TITLE_RESERVE_WOMENS_PREM = LEAGUE_CELL_PADDING_Y + 14.0
# Women's pyramid only: upper bound on crest tile edge (:func:`_womens_league_logo_cap_px`).
# Men's leagues rely on grid geometry alone so wide taper rows stay visually comparable to legacy output.
# Women's pyramid: allow larger crest tiles on lower bands (still bounded per cell height).
# Band 1 (Premiership): sized closer to men's Prem tier despite fewer squads / narrower apex cues.
_LEAGUE_LOGO_WOMENS_ABS_BY_BAND_MAX_TIER: tuple[tuple[int, float], ...] = (
    (1, 132.0),
    (2, 104.0),
    (4, 112.0),
    (6, 122.0),
)
_LEAGUE_LOGO_WOMENS_CELL_FRAC_BY_BAND_MAX_TIER: tuple[tuple[int, float], ...] = (
    (1, 0.48),
    (2, 0.40),
    (4, 0.43),
    (6, 0.46),
)
LEAGUE_LOGO_PADDING = 3
# Extra horizontal gap between team crests and the slanted pyramid edge for outer
# (trapezoid) cells, so logos don't crowd or get clipped by the pyramid silhouette.
LEAGUE_SLANT_GAP = 14
LEAGUE_CELL_STROKE_MENS = "#22324b"

# Per-tier styling (background tint, title text colour). Modelled loosely on
# the FabRugby reference image: top tiers in cool blues/teals, mid tiers in
# warmer hues, bottom tiers in greys.
TIER_COLORS: dict[int, tuple[str, str]] = {
    1: ("#1a3d6b", "#ffffff"),
    2: ("#2356a0", "#ffffff"),
    3: ("#2e7bb8", "#ffffff"),
    4: ("#4a9bc4", "#ffffff"),
    5: ("#7ab5cc", "#1a2a3a"),
    6: ("#a8c8d8", "#1a2a3a"),
    7: ("#d4d4d4", "#222222"),
    8: ("#cccccc", "#222222"),
    9: ("#c4c4c4", "#222222"),
    10: ("#bcbcbc", "#222222"),
    11: ("#b4b4b4", "#222222"),
}

# Men's pyramid_All_Leagues.svg: merit rows (merged from geocode merit/) use this fill vs tier blues/greys.
MERIT_MERGED_LEAGUE_CELL_BG_MENS = "#e8893a"
MERIT_MERGED_LEAGUE_CELL_TITLE_MENS = "#1f140a"

# ``pyramid_All_Leagues``: map merit geocode folder → lowercase substring(s) matched against RFU Counties
# league titles / geographic tails so apex merit rows nest under the correct stem league.
_MERGED_MERIT_COUNTIES_PARENT_SUBSTRINGS: dict[str, tuple[str, ...]] = {
    "CANDY": ("wales", "welsh"),
    "Devon": ("devon",),
    "East_Midlands": ("midlands east", "midlands west"),
    "Eastern_Counties": ("eastern counties",),
    "Essex": ("essex",),
    "GRFU_District": ("gloucester", "bristol", "district"),
    "Hampshire": ("hampshire",),
    "Herts_Middlesex": ("herts", "middx"),
    "Leicestershire": ("midlands east", "south north"),
    "Middlesex": ("middx", "middlesex"),
    "NOWIRUL": ("lancashire", "cheshire"),
    "Rural_Kent": ("kent",),
    "Surrey": ("surrey",),
    "Sussex": ("sussex",),
}


def _hex_to_rgb_norm(hex_color: str) -> tuple[float, float, float]:
    raw = hex_color.strip().lstrip("#")
    r = int(raw[0:2], 16) / 255.0
    g = int(raw[2:4], 16) / 255.0
    b = int(raw[4:6], 16) / 255.0
    return (r, g, b)


def _hex_relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance for sRGB hex ``#rrggbb`` (0 = black, 1 = white)."""
    r, g, b = _hex_to_rgb_norm(hex_color)

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    rl, gl, bl = lin(r), lin(g), lin(b)
    return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl


def _rgb_norm_to_hex(r: float, g: float, b: float) -> str:
    ri = int(round(max(0.0, min(1.0, r)) * 255))
    gi = int(round(max(0.0, min(1.0, g)) * 255))
    bi = int(round(max(0.0, min(1.0, b)) * 255))
    return f"#{ri:02x}{gi:02x}{bi:02x}"


def _mens_hex_to_womens_hsv_shifted(mens_hex: str, hue_shift: float) -> str:
    """Same HSV saturation and value as ``mens_hex``; hue rotated by ``hue_shift`` (0–1 turns)."""
    r, g, b = _hex_to_rgb_norm(mens_hex)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h_new = (h + hue_shift) % 1.0
    r2, g2, b2 = colorsys.hsv_to_rgb(h_new, s, v)
    return _rgb_norm_to_hex(r2, g2, b2)


def _build_womens_pyramid_tier_colors_from_mens(hue_shift: float) -> dict[int, tuple[str, str]]:
    """Bands 1–6: men's tier fills hue-shifted in HSV; same title colours per band as men's."""
    out: dict[int, tuple[str, str]] = {}
    for tier_num in range(1, 7):
        mens_bg, mens_fg = TIER_COLORS[tier_num]
        out[tier_num] = (_mens_hex_to_womens_hsv_shifted(mens_bg, hue_shift), mens_fg)
    return out


# Hue rotation from men's blues toward yellow; S and V match ``TIER_COLORS`` per tier.
WOMENS_HSV_HUE_SHIFT = 0.55
WOMENS_TIER_COLORS = _build_womens_pyramid_tier_colors_from_mens(WOMENS_HSV_HUE_SHIFT)

# Background colour for the surrounding page.
PAGE_BG = "#0e1726"
PAGE_BG_WOMENS = _mens_hex_to_womens_hsv_shifted(PAGE_BG, WOMENS_HSV_HUE_SHIFT)
TITLE_TEXT = "#f4f4f4"
SUBTITLE_FILL_MENS = "#aab8d8"
SUBTITLE_FILL_WOMENS = _mens_hex_to_womens_hsv_shifted(SUBTITLE_FILL_MENS, WOMENS_HSV_HUE_SHIFT)
TIER_LABEL_TEXT = "#dde6f0"
TIER_MARGIN_LABEL_TEXT_WOMENS = _mens_hex_to_womens_hsv_shifted(
    TIER_LABEL_TEXT, WOMENS_HSV_HUE_SHIFT
)
TRIANGLE_STROKE = "#ffffff"
TRIANGLE_STROKE_WIDTH = 6.0
PYRAMID_INTERIOR_INSET_PX = TRIANGLE_STROKE_WIDTH / 2 + 6.5
# Tier names sit parallel to the left pyramid boundary on the **exterior** (dark margin),
# this many px away from that edge along the perpendicular, measured outward from play.
EDGE_TIER_LABEL_OUTSET_PX = 26.0
# Second margin line (league/team counts) sits further out than the tier name.
EDGE_TIER_STATS_OUTSET_EXTRA_PX = 30.0
# Stem tiers (7+) use ``rotate_deg=270°``; the same perpendicular step reads tighter visually,
# so counts sit a bit farther out than on the tapered pyramid bands.
EDGE_TIER_STATS_OUTSET_EXTRA_STEM_PX = 42.0
TIER_STATS_LABEL_TEXT = "#b4c2d6"
TIER_STATS_LABEL_TEXT_WOMENS = _mens_hex_to_womens_hsv_shifted(
    TIER_STATS_LABEL_TEXT, WOMENS_HSV_HUE_SHIFT
)

LEAGUE_CELL_STROKE_WOMENS = _mens_hex_to_womens_hsv_shifted(
    LEAGUE_CELL_STROKE_MENS, WOMENS_HSV_HUE_SHIFT
)


def _tier_band_colors(tier_num: int, gender: Gender) -> tuple[str, str]:
    """League cell ``(background, title text)`` for one pyramid/stem tier."""
    if gender == "womens":
        return WOMENS_TIER_COLORS.get(tier_num, ("#cccccc", "#222222"))
    return TIER_COLORS.get(tier_num, ("#cccccc", "#222222"))


def _league_cell_tier_colors(league: LeagueData, tier_num: int, gender: Gender) -> tuple[str, str]:
    """Per-cell colours: orange tint for merit rows in the merged men's All Leagues diagram."""
    if gender == "mens" and league.merit_geocoded_competition is not None:
        return MERIT_MERGED_LEAGUE_CELL_BG_MENS, MERIT_MERGED_LEAGUE_CELL_TITLE_MENS
    return _tier_band_colors(tier_num, gender)


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TeamLogo:
    """One squad member; ``image_url`` is None when missing or not usable."""

    name: str
    image_url: str | None


@dataclass
class LeagueData:
    """A single league with its tier and teams (crest optional per team)."""

    tier_num: int
    tier_name: str
    league_name: str
    teams: list[TeamLogo]
    team_count: int
    #: Geocoded merit folder name when this row was merged into ``pyramid_All_Leagues``; ``None`` for RFU pyramid files.
    merit_geocoded_competition: str | None = None
    #: Merit competition **local** tier from RFU filenames (1-based within that competition). Used for parent matching when
    #: ``tier_num`` is re-stamped to a visible band or to an absolute pyramid tier.
    merit_local_tier: int | None = None


@dataclass
class PositionedLeague:
    """A league after layout has been computed."""

    data: LeagueData
    x: float  # left edge
    y: float  # top edge
    width: float
    height: float


@dataclass
class StemTreeNode:
    """Counties stem layout: subtree of leagues sharing geographical ancestry."""

    league: LeagueData
    children: list[StemTreeNode] = field(default_factory=list)
    layout_x: float = 0.0
    layout_w: float = 0.0
    # Multi-parent override: after partitioning, widen this node to the horizontal union of
    # these tier-(N−1) parents (one visual cell, not duplicated).
    layout_span_union_parent_names: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# RFU crest URL validation
# ---------------------------------------------------------------------------

_RFU_IMAGE_HOST = "images.englandrugby.com"


def _valid_image_url(url: object) -> bool:
    """True if ``url`` looks like a usable remote crest URL for SVG embedding."""
    if not isinstance(url, str):
        return False
    u = url.strip()
    if not u.startswith("https://"):
        return False
    try:
        host = u.split("/")[2].lower()
    except IndexError:
        return False
    return host == _RFU_IMAGE_HOST.lower()


# ---------------------------------------------------------------------------
# Optional crest preprocessing: flood white background from top-left corners
# ---------------------------------------------------------------------------

CREST_WHITE_BG_CACHE_SUBDIR = "crest_white_corner_bg_v1"
# Top-left 2×2 pixels must reach this lightness (each channel ≥ 255 − thresh).
CREST_CORNER_WHITE_CHANNEL_THRESH = 28
# Pillow :func:`~PIL.ImageDraw.floodfill` ``thresh`` — max channel delta from pixel (0,0).
CREST_FLOODFILL_MATCH_THRESH = 36


def _rfu_crest_get_bytes(url: str, *, timeout: float = 30.0) -> bytes:
    """HTTPS GET for RFU-hosted crest images only (:func:`_valid_image_url`)."""
    if not _valid_image_url(url):
        raise ValueError("refusing fetch: URL is not a trusted RFU crest host")
    req = Request(url, headers=get_headers())
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _crest_white_corner_cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / CREST_WHITE_BG_CACHE_SUBDIR / f"{digest}.png"


def _crest_top_left_2x2_near_white_rgba(im: object, *, channel_thresh: int) -> bool:
    from PIL import Image

    if not isinstance(im, Image.Image):
        return False
    w, h = im.size
    if w < 2 or h < 2:
        return False
    min_rgb = max(0, 255 - int(channel_thresh))
    min_alpha = 200
    for xy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        px = im.getpixel(xy)
        if not isinstance(px, tuple) or len(px) < 3:
            return False
        r, g, b = px[0], px[1], px[2]
        if not (r >= min_rgb and g >= min_rgb and b >= min_rgb):
            return False
        if len(px) >= 4 and px[3] < min_alpha:
            return False
    return True


def _crest_flood_corner_white_transparent_png(png_bytes: bytes) -> bytes | None:
    """If top-left 2×2 reads as white, replace the pixel (0,0) colour-connected region with transparency."""
    from PIL import Image, ImageDraw

    im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    if not _crest_top_left_2x2_near_white_rgba(
        im, channel_thresh=CREST_CORNER_WHITE_CHANNEL_THRESH
    ):
        return None
    ImageDraw.floodfill(
        im,
        (0, 0),
        (0, 0, 0, 0),
        thresh=int(CREST_FLOODFILL_MATCH_THRESH),
    )
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def build_crest_white_corner_transparent_href_map(
    leagues: list[LeagueData],
    *,
    max_workers: int = 12,
) -> dict[str, str]:
    """Return ``original RFU URL -> data:image/png base64 URI`` where the white-corner heuristic applied.

    Unchanged URLs are omitted — callers fall back to the remote ``https`` URL.

    Pillow (``requirements-dev.txt``) must be installed. Results are cached under
    :data:`~core.config.CACHE_DIR` / ``CREST_WHITE_BG_CACHE_SUBDIR``.
    """
    uniq = sorted({tm.image_url for lg in leagues for tm in lg.teams if tm.image_url})
    if not uniq:
        return {}
    try:
        import PIL  # noqa: F401
    except ImportError:
        logger.warning(
            "Skipping white-corner crest transparency: install Pillow "
            "(e.g. pip install -r requirements-dev.txt)."
        )
        return {}

    def worker(url: str) -> tuple[str, str | None]:
        cp = _crest_white_corner_cache_path(url)
        try:
            if cp.is_file():
                png_out = cp.read_bytes()
            else:
                raw = _rfu_crest_get_bytes(url)
                png_out_opt = _crest_flood_corner_white_transparent_png(raw)
                if png_out_opt is None:
                    return url, None
                png_out = png_out_opt
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_bytes(png_out)
            uri = "data:image/png;base64," + base64.standard_b64encode(png_out).decode("ascii")
            return url, uri
        except (OSError, ValueError, HTTPError, URLError, TimeoutError) as exc:
            logger.debug("crest white-bg pass skipped for %s: %s", url, exc)
            return url, None

    result: dict[str, str] = {}
    mw = max(1, min(int(max_workers), 32))
    with ThreadPoolExecutor(max_workers=mw) as pool:
        for url, mapped in pool.map(worker, uniq):
            if mapped is not None:
                result[url] = mapped

    logger.info(
        "White-corner crest transparency: inlined %d of %d unique crest URLs (cache %s).",
        len(result),
        len(uniq),
        CACHE_DIR / CREST_WHITE_BG_CACHE_SUBDIR,
    )
    return result


# ---------------------------------------------------------------------------
# League name normalisation, identity keys, and display helpers
# ---------------------------------------------------------------------------
#
# Stem matching strips RFU sponsor prefixes (same list as in tier filenames), removes
# ``Tribute Ale``, normalises ``/`` and ``&``, and falls back to prefix comparison with
# tier digits stripped so renamed subdivisions still nest. Identity-tail keys peel the
# canonical tier label off the front so that e.g. ``Counties 3 Hampshire`` reliably
# matches ``Counties 2 Hampshire`` across seasons; trailing division digits / words
# (``1``, ``One``, …) are removed before comparing tails.

# RFU Counties / Regional naming includes this sponsor slab in API league titles.
TRIBUTE_ALE_PATTERN = re.compile(r"\s*Tribute Ale\s*", re.IGNORECASE)
# Standalone ``Tribute`` (without ``Ale``) appears in some Regional league API titles.
TRIBUTE_WORD_PATTERN = re.compile(r"\s+Tribute\b\s*", re.IGNORECASE)


def _strip_league_title_sponsors(league_name: str) -> str:
    """Strip known RFU sponsor slabs from league titles.

    Removes a leading historical RFU ``x`` marker (same convention as ``x`` filenames in
    :mod:`rugby.tiers`), drops the obsolete women's ``RFUW`` brand prefix (RFU Women — the
    governing-body acronym used in pre-2012 league names; the body itself was merged into the
    RFU, so the label is just legacy noise on the pyramid), then applies ``Tribute Ale``
    removal, then standalone ``Tribute``, then leading tokens from
    :func:`rugby.tiers._strip_sponsor_prefix` on an underscore-normalised form so filename
    sponsor lists match API ``league_name`` strings (e.g. ``Cotton Traders Counties …``).
    """
    s = league_name.strip()
    if not s:
        return s
    while len(s) >= 2 and s.startswith("x"):
        s = s[1:].lstrip()
    if not s:
        return s
    while s.startswith("RFUW "):
        s = s[len("RFUW ") :].lstrip()
    if not s:
        return s
    s = TRIBUTE_ALE_PATTERN.sub(" ", s)
    s = TRIBUTE_WORD_PATTERN.sub(" ", s)
    compact = re.sub(r"\s+", "_", s)
    while True:
        stripped = _strip_sponsor_prefix(compact)
        if stripped == compact:
            break
        compact = stripped
    spaced = compact.replace("_", " ")
    return " ".join(spaced.split())


_TRAILING_SPACE_DIGITS_RE = re.compile(r"\s+\d+$")
# Longest tokens first so e.g. ``fourteen`` wins over ``four``.
_TRAILING_DIVISION_NUMBER_WORDS: tuple[str, ...] = (
    "seventeen",
    "eighteen",
    "nineteen",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "eleven",
    "twelve",
    "twenty",
    "eight",
    "three",
    "seven",
    "zero",
    "five",
    "four",
    "nine",
    "one",
    "six",
    "ten",
    "two",
)
_TRAILING_SPACE_NUMBER_WORD_RE = re.compile(
    r"\s+(?:" + "|".join(re.escape(w) for w in _TRAILING_DIVISION_NUMBER_WORDS) + r")\s*$",
    re.IGNORECASE,
)
# Whole-token removal (longest tokens first so ``fourteen`` beats ``four``).
_STEM_NUMBER_WORD_BOUNDARY_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(w) for w in sorted(_TRAILING_DIVISION_NUMBER_WORDS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


def _strip_trailing_numeric_suffix(s: str) -> str:
    """Remove trailing `` … <digits>`` or `` … <English number word>`` segments.

    Examples: ``North 1`` → ``North``; ``North One`` → ``North``; ``London 3 South East 2``
    → strips only the final division marker.

    Digits must be preceded by whitespace so tier labels like ``Regional 2`` stay intact when
    passed whole. Number words are matched as a final whitespace-delimited token only.

    Applied to geographic tails after the canonical tier prefix is removed in
    :func:`league_short_display_name`.
    """
    t = s.rstrip()
    if not t:
        return s
    original = t
    while True:
        new = _TRAILING_SPACE_DIGITS_RE.sub("", t).rstrip()
        new = _TRAILING_SPACE_NUMBER_WORD_RE.sub("", new).rstrip()
        if not new:
            return original
        if new == t:
            break
        t = new
    return t


_MENS_NATIONAL_LEAGUE_GEO_RE = re.compile(
    r"(?i)^National\s+League\s+(?:One|Two|Three|1|2|3)\s+(.+)$"
)


def _mens_short_after_national_league_division(league_name: str) -> str | None:
    """``National League …`` + geography → short region title (e.g. NL3 North → ``North``)."""
    m = _MENS_NATIONAL_LEAGUE_GEO_RE.match(league_name.strip())
    if not m:
        return None
    tail = m.group(1).strip()
    if not tail:
        return None
    out = _strip_trailing_numeric_suffix(tail)
    return out if out else tail


def _feeder_match_key(league_name: str) -> str:
    """Normalised league name for NL2 → Regional feeder lookups (sponsors stripped)."""
    return _strip_league_title_sponsors(league_name).strip().casefold()


def _stem_parent_relaxed_match_key(league_name: str) -> str:
    """Normalise league titles for Counties stem parent lookup.

    Strips sponsors via :func:`_strip_league_title_sponsors` (same token list as tier
    filenames / ``rugby.tiers._strip_sponsor_prefix``), folds ``/`` and ``&`` to
    whitespace, removes **all** digit runs and English number-word tokens (``zero`` …
    ``twenty``, longest-token-safe boundaries), then collapses whitespace.
    """
    s = _strip_league_title_sponsors(league_name).strip().casefold()
    s = s.replace("&", " ")
    s = re.sub(r"[/]", " ", s)
    s = " ".join(s.split())
    if not s:
        return ""
    while True:
        prev = s
        s = re.sub(r"\d+", "", s)
        s = _STEM_NUMBER_WORD_BOUNDARY_RE.sub(" ", s)
        s = " ".join(s.split())
        if s == prev:
            break
    return s


def _stem_identity_tail_key(league_name: str, tier_num: int, season: str) -> str:
    """Geographic tail after the canonical tier label; trailing division markers stripped then digits removed."""
    stripped = _strip_league_title_sponsors(league_name).strip()
    tier_label = mens_current_tier_name(tier_num, season)
    prefix = tier_label + " "
    stripped_cf = stripped.casefold()
    if stripped_cf.startswith(prefix.casefold()):
        tail = stripped[len(tier_label) + 1 :].strip()
    elif stripped_cf.startswith(tier_label.casefold()):
        tail = stripped[len(tier_label) :].strip().lstrip()
    else:
        tail = stripped
    tail = _strip_trailing_numeric_suffix(tail)
    tail_norm = tail.casefold().replace("&", " and ")
    tail_norm = re.sub(r"[/]", " ", tail_norm)
    tail_norm = re.sub(r"\d+", "", tail_norm)
    return " ".join(tail_norm.split())


def _season_start_year(season_label: str) -> int:
    return int(season_label.split("-", maxsplit=1)[0])


def league_short_display_name(
    league_name: str,
    tier_num: int,
    season: str,
    *,
    gender: Gender = DEFAULT_GENDER,
) -> str:
    """Strip sponsor prefixes and pyramid tier redundancy for diagram titles.

    Examples (men's): ``National League Three North`` → ``North``;
    ``National League 3 North`` → ``North``;
    ``Regional 2 Anglia`` → ``Anglia``;
    ``Regional 1 Tribute Ale South West`` → ``Regional 1 South West`` → ``South West``;
    ``Counties 1 Hampshire`` → ``Hampshire``.

    Examples (women's): ``Women's Championship North 1`` → ``North``;
    ``Women's NC 1 South East (South)`` → ``South East (South)``;
    ``Women's Premiership`` → ``Premiership``.

    Trailing division markers (space + digits at end, or a trailing English number word such
    as ``One`` / ``Two``) are stripped from the title **after** removing the redundant tier
    prefix — not from strings that are exactly the tier label.

    Men's ``National League [1–3] …`` titles are shortened using only the geographic tail
    (before the canonical ``Regional`` / ``Counties`` prefix rules apply).
    """
    if gender == "womens":
        return _womens_league_short_display_name(league_name, tier_num)
    league_name = _strip_league_title_sponsors(league_name)
    nl_geo = _mens_short_after_national_league_division(league_name)
    if nl_geo is not None:
        return nl_geo
    tier_label = mens_current_tier_name(tier_num, season)
    if league_name == tier_label:
        return league_name
    prefix = tier_label + " "
    if league_name.startswith(prefix):
        tail = league_name[len(prefix) :].strip() or league_name
        out = _strip_trailing_numeric_suffix(tail)
        return out or tail
    return league_name


# Women's league names use ``Women's NC <N>`` shorthand on disk even though the human tier
# label is ``National Challenge <N>``. Strip the on-disk prefix per visual band number.
_WOMENS_LEAGUE_PREFIXES_BY_VISIBLE_TIER: dict[int, tuple[str, ...]] = {
    1: ("Women's Premiership",),
    2: ("Women's Championship",),
    3: ("Women's Championship",),
    4: ("Women's NC 1",),
    5: ("Women's NC 2",),
    6: ("Women's NC 3",),
}


def _womens_league_short_display_name(league_name: str, visible_tier: int) -> str:
    """Strip ``Women's <tier shorthand>`` prefixes for women's pyramid cell titles."""
    name = _strip_league_title_sponsors(league_name).strip()
    for prefix in _WOMENS_LEAGUE_PREFIXES_BY_VISIBLE_TIER.get(visible_tier, ()):
        if name == prefix:
            return name
        full = prefix + " "
        if name.startswith(full):
            tail = name[len(full) :].strip() or name
            out = _strip_trailing_numeric_suffix(tail)
            return out or tail
    if name.startswith("Women's "):
        tail = name[len("Women's ") :].strip() or name
        out = _strip_trailing_numeric_suffix(tail)
        return out or tail
    out = _strip_trailing_numeric_suffix(name)
    return out or name


# Local merit tier spelled out after ``Division`` (Eastern Counties, etc.).
_MERIT_DIVISION_CARDINAL_WORD: dict[int, str] = {
    1: "One",
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
    11: "Eleven",
    12: "Twelve",
}


def _merit_division_geo_tail(stripped_title: str, local_tier: int) -> str | None:
    """Subdivision tail after ``Division Two`` / ``Division 2`` (word or digit tier).

    Returns ``None`` when this naming pattern does not apply. Matches Eastern Counties
    style titles where :func:`_merit_geo_tail` cannot see ``"<comp> N …"``.
    """
    n = stripped_title.strip()
    word = _MERIT_DIVISION_CARDINAL_WORD.get(local_tier)
    digits = str(local_tier)
    tier_union = rf"(?:{re.escape(word)}|{re.escape(digits)})" if word else re.escape(digits)
    m = re.search(rf"(?i)\bDivision\s+{tier_union}\b\s*(?P<tail>.*)$", n)
    if not m:
        return None
    return m.group("tail").strip()


_RE_MERIT_TAIL_DIGIT_ONLY_TIER = re.compile(r"^(.+)\s+(\d+)\s*$")


def _merit_stem_if_digit_suffixed_tier(league_name: str, local_tier: int) -> str | None:
    """Stem before a trailing tier integer when that integer matches ``local_tier``.

    Matches ``… Bristol & District 2`` vs ``… Bristol & District 1`` (GRFU District, etc.);
    skips names whose last token is non-numeric so ``CANDY 2 North`` continues to use
    geo-tail logic.
    """
    n = _strip_league_title_sponsors(league_name).strip()
    m = _RE_MERIT_TAIL_DIGIT_ONLY_TIER.match(n)
    if not m:
        return None
    suffix = int(m.group(2))
    if suffix != int(local_tier):
        return None
    stem = m.group(1).strip()
    return stem if stem else None


def _stem_league_core_suffix(league: LeagueData, season: str) -> str:
    """Text after the canonical tier title (e.g. ``Counties 2``) for stem matching."""
    n = _strip_league_title_sponsors(league.league_name).strip()
    prefix = mens_current_tier_name(league.tier_num, season) + " "
    if n.startswith(prefix):
        return n[len(prefix) :].strip() or n
    return n


def _merit_geo_tail(league_name: str, local_tier: int, competition: str) -> str | None:
    """Geographic / subdivision tail after competition name + ``local_tier``.

    Primary pattern: titles like ``"CANDY 2 North"`` or ``"East Midlands 3 Foo"`` —
    display competition (underscores→spaces), a space, the local merit tier integer,
    optional tail.

    Fallback: ``… Division Two North`` vs ``Division One North`` (Eastern Counties,
    Cheshire, etc.), using :func:`_merit_division_geo_tail`.

    Returns ``""`` when the apex has no subdivisions beyond the tier slug; ``None``
    only when neither pattern applies (non-standard naming).
    """
    comp_disp = competition.replace("_", " ")
    n = _strip_league_title_sponsors(league_name).strip()
    pattern = rf"^{re.escape(comp_disp)}\s+{int(local_tier)}\s*(.*?)\s*$"
    match = re.match(pattern, n, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    div = _merit_division_geo_tail(n, local_tier)
    return div


def _merit_local_tier_for_naming(league: LeagueData) -> int:
    """Local merit tier for geo/naming heuristics (visible band / absolute tier alone is wrong)."""
    return league.merit_local_tier if league.merit_local_tier is not None else league.tier_num


def _find_merit_parent_league(
    child: LeagueData,
    parents: list[LeagueData],
    competition: str,
) -> LeagueData | None:
    """Choose parent at tier-*N−1* for merit naming (competition + tier + geography).

    Mirrors the intent of Counties stem prefix nesting: same geographic subdivisions nest
    (``CANDY 3 South`` → ``CANDY 2 South``); ``Division Two North`` nests under ``Division One
    North``; digit-suffix stems (``… District 2`` → ``… District 1``); and a subdivided row
    nests under an apex league with no tail (``CANDY 2 North`` → ``CANDY 1``). Falls back to
    the longest sponsor-stripped literal parent prefix when ambiguous cases still share a
    substring.
    """
    if not parents:
        return None
    parents_u = sorted(parents, key=lambda lg: lg.league_name)
    if len(parents_u) == 1:
        return parents_u[0]

    child_loc = _merit_local_tier_for_naming(child)

    child_tail = _merit_geo_tail(child.league_name, child_loc, competition)

    exact_tail: list[LeagueData] = []
    for p in parents_u:
        pt = _merit_geo_tail(p.league_name, _merit_local_tier_for_naming(p), competition)
        if pt is None:
            continue
        if child_tail is not None and pt == child_tail:
            exact_tail.append(p)

    if len(exact_tail) == 1:
        return exact_tail[0]
    if len(exact_tail) > 1:
        return max(
            exact_tail,
            key=lambda lg: len(_strip_league_title_sponsors(lg.league_name)),
        )

    if child_tail:
        apex: list[LeagueData] = []
        for p in parents_u:
            pt = _merit_geo_tail(p.league_name, _merit_local_tier_for_naming(p), competition)
            if pt == "":
                apex.append(p)
        if len(apex) == 1:
            return apex[0]

    stem = _merit_stem_if_digit_suffixed_tier(child.league_name, child_loc)
    if stem is not None:
        stem_matches = [
            p
            for p in parents_u
            if _merit_stem_if_digit_suffixed_tier(p.league_name, _merit_local_tier_for_naming(p))
            == stem
        ]
        if len(stem_matches) == 1:
            return stem_matches[0]

    cn = _strip_league_title_sponsors(child.league_name).strip()
    literals: list[LeagueData] = []
    for p in parents_u:
        pn = _strip_league_title_sponsors(p.league_name).strip()
        if pn and cn.startswith(pn):
            literals.append(p)
    if literals:
        return max(literals, key=lambda lg: len(_strip_league_title_sponsors(lg.league_name)))

    return None


def merit_apply_parent_heuristics_local(
    leagues_by_local_tier: dict[int, list[LeagueData]],
    competition: str,
    overrides: StemParentOverrides,
    season: str,
) -> int:
    """Fill deterministic ``overrides[(local_tier, child)]`` using intra-merit and apex rules.

    The shallowest local tier attaches to men's pyramid tier
    :func:`merit_pyramid_absolute_parent_tier`; deeper rows use :func:`_find_merit_parent_league`.
    Existing keys are never overwritten. Returns the count of newly added tuples.
    """
    tiers_sorted = sorted(leagues_by_local_tier.keys())
    if not tiers_sorted:
        return 0
    apex_local = tiers_sorted[0]

    mens_by = _load_mens_pyramid_leagues_by_tier(season)

    n_added = 0
    pyramid_parents = mens_by.get(
        merit_pyramid_absolute_parent_tier(competition, apex_local, season), ()
    )
    if pyramid_parents:
        for lg in sorted(leagues_by_local_tier.get(apex_local, ()), key=lambda x: x.league_name):
            key = (apex_local, lg.league_name)
            if key in overrides:
                continue
            found_p = _merit_infer_pyramid_parent_for_apex(
                lg, list(pyramid_parents), competition, season, apex_local
            )
            if found_p is None:
                continue
            overrides[key] = (found_p.league_name,)
            n_added += 1
            logger.info(
                "Merit %s apex: inferred national parent for %r (local tier %d) "
                "→ men's %r (tier %d)",
                competition,
                lg.league_name,
                apex_local,
                found_p.league_name,
                found_p.tier_num,
            )

    if len(tiers_sorted) < 2:
        return n_added

    min_tier = apex_local
    for tier in tiers_sorted:
        if tier <= min_tier:
            continue
        parents_ld = leagues_by_local_tier.get(tier - 1, [])
        parents_list = list(parents_ld)
        if not parents_list:
            continue
        for lg in leagues_by_local_tier.get(tier, ()):
            key = (tier, lg.league_name)
            if key in overrides:
                continue
            found = _find_merit_parent_league(lg, parents_list, competition)
            if found is None:
                continue
            overrides[key] = (found.league_name,)
            n_added += 1
            logger.info(
                "Merit %s: inferred parent for %r tier %d → %r tier %d",
                competition,
                lg.league_name,
                lg.tier_num,
                found.league_name,
                found.tier_num,
            )
    return n_added


def _find_stem_parent_league(
    child: LeagueData,
    parents: list[LeagueData],
    season: str,
) -> LeagueData | None:
    """Prefer the longest tier‑(N−1) ``league_name`` that is a prefix of ``child``; failing
    that, longest core‑suffix prefix (stripped ``Counties N`` tier titles vs child) — RFU naming
    for ``Counties 2 Hampshire`` → ``Counties 3 Hampshire``.

    Sponsor slabs are stripped first (same tokens as tier filenames). If literals fail,
    prefix checks repeat under :func:`_stem_parent_relaxed_match_key` (sponsors removed; all
    digits and English number words stripped; whitespace normalised) so renames and
    divisions still nest — e.g. ``Durham/Northumberland Two`` under ``Durham/Northumberland One``.
    """
    cn = _strip_league_title_sponsors(child.league_name).strip()

    literals: list[LeagueData] = []
    for p in parents:
        pn = _strip_league_title_sponsors(p.league_name).strip()
        if pn and cn.startswith(pn):
            literals.append(p)
    if literals:
        return max(literals, key=lambda lg: len(_strip_league_title_sponsors(lg.league_name)))

    cn_rx = _stem_parent_relaxed_match_key(child.league_name)
    literals_rx: list[LeagueData] = []
    if cn_rx:
        for p in parents:
            pn_rx = _stem_parent_relaxed_match_key(p.league_name)
            if pn_rx and cn_rx.startswith(pn_rx):
                literals_rx.append(p)
    if literals_rx:
        return max(literals_rx, key=lambda lg: len(_stem_parent_relaxed_match_key(lg.league_name)))

    cc = _stem_league_core_suffix(child, season)
    tails: list[LeagueData] = []
    for p in parents:
        core = _stem_league_core_suffix(p, season)
        if core and cc.startswith(core):
            tails.append(p)
    if tails:
        return max(tails, key=lambda lg: len(_stem_league_core_suffix(lg, season)))

    cc_rx = _stem_parent_relaxed_match_key(cc)
    tails_rx: list[LeagueData] = []
    if cc_rx:
        for p in parents:
            core = _stem_league_core_suffix(p, season)
            core_rx = _stem_parent_relaxed_match_key(core)
            if core_rx and cc_rx.startswith(core_rx):
                tails_rx.append(p)
    if tails_rx:
        return max(
            tails_rx,
            key=lambda lg: len(
                _stem_parent_relaxed_match_key(_stem_league_core_suffix(lg, season))
            ),
        )

    return None


def _match_parent_override_label(
    parents: list[LeagueData],
    want_raw: str,
    ptier: int,
    season: str,
    child_league_name: str,
) -> LeagueData | None:
    """Resolve one override parent string against tier-(``ptier``) ``parents``."""
    want = _strip_league_title_sponsors(want_raw).strip().casefold()
    if not want:
        return None
    for p in parents:
        if _strip_league_title_sponsors(p.league_name).strip().casefold() == want:
            return p
    tail_key = _stem_identity_tail_key(want_raw, ptier, season)
    fuzzy = [
        p for p in parents if _stem_identity_tail_key(p.league_name, ptier, season) == tail_key
    ]
    if len(fuzzy) == 1:
        return fuzzy[0]
    logger.warning(
        "Stem parent override %r for %r does not match any tier-%d league; ignoring.",
        want_raw,
        child_league_name,
        ptier,
    )
    return None


def _merged_merit_counties_parent_hints(competition: str) -> tuple[str, ...]:
    """Lowercase substring hints for RFU Counties titles feeding ``competition``."""
    if competition in _MERGED_MERIT_COUNTIES_PARENT_SUBSTRINGS:
        return _MERGED_MERIT_COUNTIES_PARENT_SUBSTRINGS[competition]
    noise = frozenset({"merit", "table", "reserve"})
    parts = competition.replace("_", " ").split()
    out: list[str] = []
    for p in parts:
        cf = p.casefold()
        if len(cf) <= 2 or cf in noise:
            continue
        out.append(cf)
    return tuple(out)


def _find_merged_merit_counties_anchor_parent(
    child: LeagueData,
    parents: list[LeagueData],
    season: str,
) -> LeagueData | None:
    """Attach apex merit rows under the RFU Counties league for their region (merged diagram)."""
    comp = child.merit_geocoded_competition
    if comp is None or not parents:
        return None
    hints = _merged_merit_counties_parent_hints(comp)
    if not hints:
        return None

    nationals = [p for p in parents if p.merit_geocoded_competition is None]
    pool = nationals if nationals else list(parents)

    scored: list[tuple[int, LeagueData]] = []
    for p in pool:
        stripped = _strip_league_title_sponsors(p.league_name).strip().casefold()
        core = _stem_league_core_suffix(p, season).casefold()
        blob = f"{stripped} {core}"
        hits = sum(1 for h in hints if h in blob)
        if hits <= 0:
            continue
        scored.append((hits, p))

    if not scored:
        return None
    scored.sort(key=lambda hp: (-hp[0], len(hp[1].league_name)))
    top_hits = scored[0][0]
    tied = [lg for h, lg in scored if h == top_hits]
    if len(tied) == 1:
        return tied[0]
    return min(tied, key=lambda lg: len(lg.league_name))


def _resolve_stem_parents(
    child: LeagueData,
    parents: list[LeagueData],
    season: str,
    parent_overrides: StemParentOverrides | None,
    *,
    merit_competition: str | None = None,
) -> list[LeagueData]:
    """Heuristic parents unless ``parent_overrides`` fixes ``(tier, child) -> parent names``."""
    key = (child.tier_num, child.league_name)
    ovs = parent_overrides or {}
    if key in ovs:
        specs = ovs[key]
        if not specs:
            return []
        ptier = child.tier_num - 1
        matched: list[LeagueData] = []
        seen_pk: set[tuple[int, str]] = set()
        for want_raw in specs:
            p = _match_parent_override_label(parents, want_raw, ptier, season, child.league_name)
            if p is None:
                continue
            pk = (p.tier_num, p.league_name)
            if pk not in seen_pk:
                seen_pk.add(pk)
                matched.append(p)
        return matched
    if merit_competition is not None:
        found_m = _find_merit_parent_league(child, parents, merit_competition)
        if found_m is not None:
            return [found_m]

    if child.merit_geocoded_competition is not None and merit_competition is None:
        comp = child.merit_geocoded_competition
        cloc = child.merit_local_tier

        if cloc is not None and cloc > 1:
            ladder_parents = [
                p
                for p in parents
                if p.merit_geocoded_competition == comp
                and p.merit_local_tier is not None
                and p.merit_local_tier == cloc - 1
            ]
            if len(ladder_parents) == 1:
                return ladder_parents
            if len(ladder_parents) > 1:
                ladder_parents.sort(key=lambda lg: lg.league_name)
                return [ladder_parents[0]]

        found_m = _find_merit_parent_league(child, parents, comp)
        if found_m is not None:
            return [found_m]

        found_c = _find_merged_merit_counties_anchor_parent(child, parents, season)
        if found_c is not None:
            return [found_c]

    found = _find_stem_parent_league(child, parents, season)
    return [found] if found is not None else []


def _stem_resolve_league_identity(
    candidates: list[LeagueData],
    league_label: str,
    tier_num: int,
    season_here: str,
    season_foreign: str,
) -> LeagueData | None:
    """Pick the unique league in ``candidates`` matching ``league_label`` across seasons."""
    key_foreign = _stem_identity_tail_key(league_label, tier_num, season_foreign)
    matches = [
        lg
        for lg in candidates
        if lg.tier_num == tier_num
        and _stem_identity_tail_key(lg.league_name, tier_num, season_here) == key_foreign
    ]
    if len(matches) == 1:
        return matches[0]
    want_cf = _strip_league_title_sponsors(league_label).strip().casefold()
    exact = [
        lg
        for lg in candidates
        if lg.tier_num == tier_num
        and _strip_league_title_sponsors(lg.league_name).strip().casefold() == want_cf
    ]
    if len(exact) == 1:
        return exact[0]
    return None


def _womens_feeder_resolve_league_identity(
    candidates: list[LeagueData],
    league_label: str,
    visual_band: int,
    _season_here: str,
    _season_foreign: str,
) -> LeagueData | None:
    """Pick the unique women's pyramid league matching ``league_label`` from another season.

    Uses geography tails after band-specific ``Women's …`` prefixes (see :func:`_womens_geo_tail_raw`),
    then exact sponsor-stripped name match. Season parameters mirror :func:`_stem_resolve_league_identity`
    for API consistency (reserved for future era-specific rules).
    """
    tail_foreign = _womens_geo_tail_raw(league_label, visual_band).casefold()
    matches = [
        lg
        for lg in candidates
        if lg.tier_num == visual_band
        and _womens_geo_tail_raw(lg.league_name, visual_band).casefold() == tail_foreign
    ]
    if len(matches) == 1:
        return matches[0]
    want_cf = _strip_league_title_sponsors(league_label).strip().casefold()
    exact = [
        lg
        for lg in candidates
        if lg.tier_num == visual_band
        and _strip_league_title_sponsors(lg.league_name).strip().casefold() == want_cf
    ]
    if len(exact) == 1:
        return exact[0]
    return None


def pyramid_band_tier_label(
    visible_tier: int,
    season: str,
    gender: Gender,
    *,
    merit_competition: str | None = None,
    merit_local_offset: int = 0,
) -> str:
    """Human display label for visual tier band ``visible_tier`` (1-based).

    Internally both genders use bands 1..N. For women's, the underlying RFU tier numbers
    (101..) are recovered by adding 100 before consulting :func:`womens_current_tier_name`.

    Men's band 1 uses **Championship** on the diagram margin for seasons before 2009–2010 (the
    :func:`~rugby.tiers.mens_current_tier_name` pre-Championship era). That matches historical
    pyramid figures where the apex is not labelled Premiership. From 2009–10 onward band 1 is
    Premiership.

    Men's seasons before 2022–2023 use neutral ``Level N`` on the exterior margin from tier 5
    downward (below National League 2), instead of anachronistic Regional / Counties wording.

    For merit mode, ``merit_competition`` is the geocoded directory name (e.g. ``"Hampshire"``)
    and ``merit_local_offset`` is what :func:`load_merit_pyramid_leagues` returned so the
    margin label reads ``"Hampshire 5"`` for visible band 1 of a 5..6 comp.
    """
    if merit_competition is not None:
        comp_display = merit_competition.replace("_", " ")
        return f"{comp_display} {visible_tier + merit_local_offset}"
    if gender == "womens":
        return womens_current_tier_name(visible_tier + 100)
    if visible_tier == 1 and season and season < "2009-2010":
        return "Championship"
    if _season_start_year(season) <= 2021 and visible_tier >= 5:
        return f"Level {visible_tier}"
    return mens_current_tier_name(visible_tier, season)


# ---------------------------------------------------------------------------
# Data loading (geocoded league JSON -> LeagueData)
# ---------------------------------------------------------------------------
#
# Each league JSON in ``data/rugby/geocoded_teams/<season>/`` is loaded into a
# :class:`LeagueData` (men's tiers 1-11 or women's visual bands 1-6 from absolute tiers
# 101-106). Files that resolve to the other gender's pyramid are silently skipped so the
# same loader can handle both modes by switching the ``gender`` argument.


def _load_league_file(
    filepath: Path,
    season: str,
    *,
    gender: Gender = DEFAULT_GENDER,
    extract_rel: str | None = None,
) -> LeagueData | None:
    """Load a single geocoded league JSON, returning None for files outside this gender's pyramid.

    For ``gender == "womens"`` the absolute women's tier (101+) is **re-stamped down** to a
    visual band number (1..6) so the rest of the layout pipeline can treat both pyramids
    uniformly. The original display name from :mod:`rugby.tiers` is preserved as
    ``LeagueData.tier_name``.

    ``extract_rel`` overrides the path string handed to :func:`extract_tier`. The men's /
    women's loaders pass the bare filename (canonical pyramid file naming); the merit loader
    passes the full ``merit/<Competition>/<file>.json`` path so that
    :func:`rugby.tiers.extract_tier` triggers its merit branch and returns **local** tier
    numbers (1-based within the competition) plus a merit-qualified tier name.
    """
    rel = extract_rel if extract_rel is not None else filepath.name
    tier_num, tier_name = extract_tier(rel, season)

    if tier_num >= 999:
        return None
    if gender == "mens":
        if tier_num >= 100:
            return None
    else:
        if tier_num < 100 or tier_num >= 200:
            return None
        visual_tier = tier_num - 100
        if visual_tier < _PYRAMID_TIER_NUM_MIN or visual_tier > _PYRAMID_TIER_NUM_MAX_WOMENS:
            logger.debug(
                "Skipping women's league %s: visual tier %d outside supported bands (1..%d)",
                rel,
                visual_tier,
                _PYRAMID_TIER_NUM_MAX_WOMENS,
            )
            return None
        tier_num = visual_tier

    with filepath.open(encoding="utf-8") as fh:
        data = json.load(fh)

    teams_raw = data.get("teams") or []
    teams: list[TeamLogo] = []
    for t in teams_raw:
        name = t.get("name")
        if not name:
            continue
        raw_url = t.get("image_url")
        teams.append(
            TeamLogo(
                name=str(name),
                image_url=str(raw_url).strip() if _valid_image_url(raw_url) else None,
            )
        )

    tc = data.get("team_count")
    team_count = int(tc) if isinstance(tc, int) else len(teams_raw)

    return LeagueData(
        tier_num=tier_num,
        tier_name=tier_name,
        league_name=data.get("league_name", filepath.stem),
        teams=teams,
        team_count=team_count,
    )


def load_pyramid_leagues(
    season: str,
    *,
    gender: Gender = DEFAULT_GENDER,
) -> list[LeagueData]:
    """Load this gender's pyramid leagues from the geocoded_teams directory.

    Men's: tiers 1–11. Tiers 1–6 occupy the tapered section; tiers 7–11 continue in an
    integrated stem beneath tier 6. Women's leagues, merit competitions, and the county
    championship are skipped.

    Women's: tiers 101–106 (Premiership → NC 3) re-stamped to visual bands 1–6. No stem.
    Men's leagues, merit competitions, and the county championship are skipped.
    """
    season_dir = GEOCODED_DIR / season
    if not season_dir.is_dir():
        raise FileNotFoundError(f"No geocoded_teams data for season {season} at {season_dir}")

    leagues: list[LeagueData] = []
    # Only top-level files — merit/, county_championship/, etc. are skipped.
    for filepath in sorted(season_dir.glob("*.json")):
        league = _load_league_file(filepath, season, gender=gender)
        if league is None:
            continue
        leagues.append(league)

    return leagues


def load_merit_pyramid_leagues_raw(season: str, competition: str) -> list[LeagueData]:
    """Load one merit competition's leagues with **local** tier numbers (no re-stamping).

    Each :class:`LeagueData` carries the local tier as returned by
    :func:`rugby.tiers.extract_tier` (e.g. Hampshire 2025-2026 returns tiers 5 and 6).
    Used by the interactive linker, cross-season inference, and per-comp JSON I/O — all
    of which key off the local tier so the JSON file is robust across seasons with
    different comp ranges.

    Files that resolve to tier ``999`` (unknown) are skipped silently.
    """
    season_dir = GEOCODED_DIR / season
    if not season_dir.is_dir():
        raise FileNotFoundError(f"No geocoded_teams data for season {season} at {season_dir}")
    comp_dir = season_dir / "merit" / competition
    if not comp_dir.is_dir():
        raise FileNotFoundError(
            f"No merit data for competition {competition!r} in season {season} " f"at {comp_dir}"
        )

    raw: list[LeagueData] = []
    for filepath in sorted(comp_dir.glob("*.json")):
        rel = f"merit/{competition}/{filepath.name}"
        # ``gender="mens"`` ensures the women's tier-100+ filter is not applied; merit
        # files always return local tiers below 100 via the merit branch of extract_tier.
        league = _load_league_file(filepath, season, gender="mens", extract_rel=rel)
        if league is None:
            continue
        raw.append(replace(league, merit_local_tier=league.tier_num))
    return raw


def load_merit_pyramid_leagues(season: str, competition: str) -> tuple[list[LeagueData], int]:
    """Load one merit competition's leagues plus the local-tier offset for display.

    Some merit competitions (Hampshire 2025-2026 covers local tiers 5–6 only, Sussex covers
    3–5, …) do not start at local tier 1, so the loader **re-stamps** ``tier_num`` to a
    visible band 1..K based on the minimum local tier present. The original local tier name
    is preserved on ``LeagueData.tier_name`` so callers can still display it. The integer
    returned alongside the league list is ``min_local_tier - 1`` — add it back to a visible
    band number to recover the actual local tier (used by :func:`pyramid_band_tier_label`).

    Falls back to offset ``0`` (visible band == local tier) when the competition has no
    leagues at all.
    """
    raw = load_merit_pyramid_leagues_raw(season, competition)
    if not raw:
        return [], 0

    min_local = min(lg.tier_num for lg in raw)
    offset = max(0, min_local - 1)
    if offset == 0:
        return raw, 0
    leagues: list[LeagueData] = []
    for lg in raw:
        leagues.append(
            LeagueData(
                tier_num=lg.tier_num - offset,
                tier_name=lg.tier_name,
                league_name=lg.league_name,
                teams=lg.teams,
                team_count=lg.team_count,
                merit_geocoded_competition=lg.merit_geocoded_competition,
                merit_local_tier=lg.merit_local_tier,
            )
        )
    return leagues, offset


def merit_overrides_local_to_visible(
    overrides_local: StemParentOverrides,
    offset: int,
) -> StemParentOverrides:
    """Translate merit overrides from local-tier keys to visible-band keys.

    Pre-rendering step: the JSON section / interactive linker / cross-season inference all
    work on local tiers, but :func:`render_pyramid_svg` expects visible-band keys. Entries
    whose local tier is ``<= display_offset`` (not represented in this competition slice) are
    dropped; the shallowest merit row maps to visible band ``1``.
    """
    return {(t - offset, n): v for (t, n), v in overrides_local.items() if t > offset}


def merit_pyramid_absolute_child_tier(competition: str, local_child_tier: int, season: str) -> int:
    """Absolute men's pyramid tier occupied by merit ``local_child_tier``.

    Matches map/match-day conventions: ``local + competition_offset``.
    """
    return int(local_child_tier) + get_competition_offset(competition, season)


def merit_pyramid_absolute_parent_tier(competition: str, local_child_tier: int, season: str) -> int:
    """Men's pyramid tier immediately above this merit local tier."""
    return merit_pyramid_absolute_child_tier(competition, local_child_tier, season) - 1


def _load_mens_pyramid_leagues_by_tier(season: str) -> dict[int, list[LeagueData]]:
    by: dict[int, list[LeagueData]] = {}
    for lg in load_pyramid_leagues(season, gender="mens"):
        by.setdefault(lg.tier_num, []).append(lg)
    return by


def _merit_infer_pyramid_parent_for_apex(
    apex: LeagueData,
    mens_parents: list[LeagueData],
    competition: str,
    season: str,
    apex_local_tier: int,
) -> LeagueData | None:
    if not mens_parents:
        return None
    if len(mens_parents) == 1:
        return mens_parents[0]

    abs_child_tier = merit_pyramid_absolute_child_tier(competition, apex_local_tier, season)
    feed_tier = abs_child_tier - 1
    apex_key = _stem_identity_tail_key(apex.league_name, abs_child_tier, season)
    if apex_key.strip():
        tail_hit = [
            p
            for p in mens_parents
            if _stem_identity_tail_key(p.league_name, feed_tier, season) == apex_key
        ]
        if len(tail_hit) == 1:
            return tail_hit[0]

    cn_cf = _strip_league_title_sponsors(apex.league_name).casefold()
    subcontain = [
        p
        for p in mens_parents
        if (pn_cf := _strip_league_title_sponsors(p.league_name).casefold()) and pn_cf in cn_cf
    ]
    if len(subcontain) == 1:
        return subcontain[0]

    cn_rx = _stem_parent_relaxed_match_key(apex.league_name)
    if cn_rx:
        rxm = [
            p
            for p in mens_parents
            if (pn_rx := _stem_parent_relaxed_match_key(p.league_name))
            and (pn_rx in cn_rx or cn_rx in pn_rx)
        ]
        if len(rxm) == 1:
            return rxm[0]
    return None


def merit_overrides_visible_to_local(
    overrides_visible: StemParentOverrides,
    offset: int,
) -> StemParentOverrides:
    """Translate merit overrides from visible-band keys back to local-tier keys."""
    return {(b + offset, n): v for (b, n), v in overrides_visible.items()}


def discover_merit_competitions(season: str) -> list[str]:
    """Return every merit competition with a populated directory for ``season``.

    Used by ``--merit`` (without an explicit competition) to iterate every competition's
    pyramid in a single CLI invocation.
    """
    season_dir = GEOCODED_DIR / season / "merit"
    if not season_dir.is_dir():
        return []
    out: list[str] = []
    for d in sorted(season_dir.iterdir()):
        if not d.is_dir():
            continue
        if any(d.glob("*.json")):
            out.append(d.name)
    return out


def load_pyramid_leagues_with_merit(season: str) -> list[LeagueData]:
    """Men's national pyramid leagues plus merit leagues mapped to absolute pyramid tiers.

    Each merit row uses ``local_tier + get_competition_offset(competition, season)``, consistent
    with All Leagues maps. Used for ``pyramid_All_Leagues.svg`` so merit competitions appear in
    the taper (absolute tiers 1–6) and stem (7+) alongside RFU pyramid leagues.

    Absolute tiers may exceed 11 for deep local structures; stem styling falls back to default
    greys beyond :data:`TIER_COLORS`.
    """
    merged = list(load_pyramid_leagues(season, gender="mens"))
    n_nat = len(merged)
    comps = discover_merit_competitions(season)
    for competition in comps:
        raw = load_merit_pyramid_leagues_raw(season, competition)
        for lg in raw:
            abs_tier = merit_pyramid_absolute_child_tier(competition, lg.tier_num, season)
            merged.append(
                LeagueData(
                    tier_num=abs_tier,
                    tier_name=mens_current_tier_name(abs_tier, season),
                    league_name=lg.league_name,
                    teams=lg.teams,
                    team_count=lg.team_count,
                    merit_geocoded_competition=competition,
                    merit_local_tier=lg.merit_local_tier,
                )
            )
    logger.info(
        "Merged men's pyramid + merit: %d national leagues + %d merit league rows "
        "from %d competition(s) → %d total.",
        n_nat,
        len(merged) - n_nat,
        len(comps),
        len(merged),
    )
    return merged


# ---------------------------------------------------------------------------
# Tiers 4-6 nesting: leaf ordering driven by feeder hierarchy + JSON insertion order
# ---------------------------------------------------------------------------
#
# Tiers 4-6 nest using each parent's visible trapezoid span (pyramid slants plus
# vertical mid-gaps between neighbours). Tier-6 ``team_count`` drives proportional
# widths: leaf fractions along each band's ``avail_w`` aggregate upward so NL2,
# Regional 1, and Regional 2 dividers stay vertically aligned and heavier leagues gain
# horizontal space. Parent links for tiers 5 and 6 (Regional 1 -> NL2, Regional 2 ->
# Regional 1) come from ``data/rugby/tier_mappings/<season>.json`` (same file as the
# Counties stem). JSON insertion order within each tier dictates the left-to-right
# ordering of siblings under each parent, so the layout is fully driven by the JSON.


def _parents_for_child_tier(
    parent_overrides: StemParentOverrides | None,
    child_tier: int,
) -> dict[str, list[str]]:
    """Invert ``parent_overrides`` to ``{parent_match_key: [child league_name, ...]}``.

    ``parent_overrides`` maps ``(child_tier, child_name) -> (parent_names...)`` for every tier in
    the per-season ``data/rugby/tier_mappings/<season>.json`` file (the same source that drives
    Counties stem nesting). For tiers 5–6 it now also drives NL2 → Regional 1 → Regional 2
    nesting; previously these were hardcoded ``_TIER4_TO_TIER5`` / ``_TIER5_TO_TIER6`` dicts in
    this module.

    Children with multiple parents (JSON array) appear under each of those parents.
    Sponsor-stripped match keys (:func:`_feeder_match_key`) keep RFU sponsor renamings linked.
    Per-parent child order follows JSON insertion order so seasons can encode left-to-right
    visual ordering directly in the file.
    """
    out: dict[str, list[str]] = {}
    if not parent_overrides:
        return out
    for (t_child, child_name), parent_names in parent_overrides.items():
        if t_child != child_tier:
            continue
        for pn in parent_names:
            out.setdefault(_feeder_match_key(pn), []).append(child_name)
    return out


def _leagues_by_feeder_key(leagues: list[LeagueData]) -> dict[str, LeagueData]:
    """Map sponsor-normalised names to leagues (last wins on duplicate keys)."""
    out: dict[str, LeagueData] = {}
    for lg in leagues:
        fk = _feeder_match_key(lg.league_name)
        prev = out.get(fk)
        if prev is not None and prev.league_name != lg.league_name:
            logger.warning(
                "Duplicate feeder key %r for tier-%d leagues %r and %r",
                fk,
                prev.tier_num,
                prev.league_name,
                lg.league_name,
            )
        out[fk] = lg
    return out


def _ordered_tier4_leagues(leagues_at_tier_4: list[LeagueData]) -> list[LeagueData]:
    """Order tier 4 leagues left-to-right: West, North, East (geographic-ish)."""
    desired = ["National League 2 West", "National League 2 North", "National League 2 East"]
    by_name = {lg.league_name: lg for lg in leagues_at_tier_4}
    ordered: list[LeagueData] = []
    for name in desired:
        if name in by_name:
            ordered.append(by_name[name])
    # Append any unrecognised tier 4 leagues at the end (defensive).
    for lg in leagues_at_tier_4:
        if lg.league_name not in desired:
            ordered.append(lg)
    return ordered


def order_pyramid_leaves(
    leagues_by_tier: dict[int, list[LeagueData]],
    *,
    parent_overrides: StemParentOverrides | None = None,
) -> list[LeagueData]:
    """Return tier 6 leagues ordered such that descendants of the same parent
    (and grandparent) are adjacent.

    Walks the feeder tree top-down: NL2 (tier 4) ordered West→North→East; each NL2's R1
    children in JSON order; each R1's R2 children in JSON order. Parent assignments come
    from ``parent_overrides`` (the per-season ``tier_mappings`` JSON). Any tier 6 league
    missing from the feeder map is appended at the end so it still appears in the visual
    (with no parent linkage).
    """
    tier_6_by_feeder = _leagues_by_feeder_key(leagues_by_tier.get(6, []))
    tier_4_ordered = _ordered_tier4_leagues(leagues_by_tier.get(4, []))
    t4_to_t5 = _parents_for_child_tier(parent_overrides, 5)
    t5_to_t6 = _parents_for_child_tier(parent_overrides, 6)

    seen: set[str] = set()
    ordered: list[LeagueData] = []

    for nl2 in tier_4_ordered:
        for r1_name in t4_to_t5.get(_feeder_match_key(nl2.league_name), []):
            for r2_name in t5_to_t6.get(_feeder_match_key(r1_name), []):
                lg = tier_6_by_feeder.get(_feeder_match_key(r2_name))
                if lg is not None and lg.league_name not in seen:
                    ordered.append(lg)
                    seen.add(lg.league_name)

    # Catch any tier 6 leagues that weren't reachable through the feeder map.
    for lg in leagues_by_tier.get(6, []):
        if lg.league_name not in seen:
            logger.warning(
                "Tier 6 league %r has no parent mapping in tier_mappings JSON; "
                "appending at end of pyramid leaves.",
                lg.league_name,
            )
            ordered.append(lg)

    return ordered


# ---------------------------------------------------------------------------
# Slot-based layout: each league has a horizontal "slot" in [0, 1]
# ---------------------------------------------------------------------------


def compute_league_slots(
    leagues_by_tier: dict[int, list[LeagueData]],
    leaf_order: list[LeagueData],
    *,
    parent_overrides: StemParentOverrides | None = None,
) -> dict[tuple[int, str], float]:
    """Return ``{(tier_num, league_name): slot}`` where slot ∈ [0, 1].

    Tier 6 leaves are evenly spaced. Each higher-tier league's slot is the
    average slot of its tier 6 descendants — so a parent is centred above its
    children's collective horizontal range. Tiers 1–3 (each a single league)
    naturally collapse to slot 0.5.

    Parent → child relations for tiers 5/6 come from ``parent_overrides`` (the per-season
    ``tier_mappings`` JSON).
    """
    n_leaves = max(1, len(leaf_order))
    slots: dict[tuple[int, str], float] = {}

    leaf_slot_by_feeder: dict[str, float] = {}
    for i, lg in enumerate(leaf_order):
        s = (i + 0.5) / n_leaves
        slots[(lg.tier_num, lg.league_name)] = s
        leaf_slot_by_feeder[_feeder_match_key(lg.league_name)] = s

    t4_to_t5 = _parents_for_child_tier(parent_overrides, 5)
    t5_to_t6 = _parents_for_child_tier(parent_overrides, 6)

    # Tier 5: average of its R2 descendants' slots
    for r1 in leagues_by_tier.get(5, []):
        children = t5_to_t6.get(_feeder_match_key(r1.league_name), [])
        child_slots = [
            leaf_slot_by_feeder[_feeder_match_key(c)]
            for c in children
            if _feeder_match_key(c) in leaf_slot_by_feeder
        ]
        if not child_slots:
            child_slots = [0.5]
            logger.warning(
                "Regional 1 league %r has no Regional 2 children in tier_mappings JSON; "
                "centering at 0.5.",
                r1.league_name,
            )
        slots[(r1.tier_num, r1.league_name)] = sum(child_slots) / len(child_slots)

    # Tier 4: average of its R1 descendants' slots
    for nl2 in leagues_by_tier.get(4, []):
        r1_children = t4_to_t5.get(_feeder_match_key(nl2.league_name), [])
        descendant_slots: list[float] = []
        for r1 in r1_children:
            for r2 in t5_to_t6.get(_feeder_match_key(r1), []):
                fk = _feeder_match_key(r2)
                if fk in leaf_slot_by_feeder:
                    descendant_slots.append(leaf_slot_by_feeder[fk])
        if not descendant_slots:
            descendant_slots = [0.5]
        slots[(nl2.tier_num, nl2.league_name)] = sum(descendant_slots) / len(descendant_slots)

    # Tiers 1–3: a single league centred over the whole pyramid.
    for t in (1, 2, 3):
        for lg in leagues_by_tier.get(t, []):
            slots[(t, lg.league_name)] = 0.5

    return slots


# ---------------------------------------------------------------------------
# Triangle silhouette helpers
# ---------------------------------------------------------------------------


def _pyramid_top_y() -> float:
    return PAGE_MARGIN_TOP + TITLE_STRIP_HEIGHT


def _pyramid_bottom_y() -> float:
    return _pyramid_top_y() + PYRAMID_HEIGHT


def _triangle_apex_y() -> float:
    """Logical apex y of the linear width function. Above the visible top so
    that tier 1's row has a usable width."""
    return _pyramid_top_y() - PYRAMID_APEX_OFFSET


def _triangle_base_y() -> float:
    return _pyramid_bottom_y()


def _pyramid_center_x() -> float:
    """Horizontal centre of the pyramid trapezoid / triangle."""
    return _effective_canvas_width_px() / 2


def _triangle_base_width() -> float:
    """Chord width at the pyramid base (widest horizontal extent)."""
    return _effective_canvas_width_px() - 2 * PAGE_MARGIN_X


def _triangle_width_at(y: float) -> float:
    """Width of the linear-taper triangle at vertical coordinate ``y`` (px)."""
    apex = _triangle_apex_y()
    base = _triangle_base_y()
    base_w = _triangle_base_width()
    if base <= apex:
        return base_w
    ratio = (y - apex) / (base - apex)
    return max(0.0, base_w * ratio)


def _triangle_left_x(y: float) -> float:
    """Left boundary of the pyramid outline at vertical ``y``."""
    return _pyramid_center_x() - _triangle_width_at(y) / 2


def _outline_left_x_at_y(y: float) -> float:
    """Left silhouette at ``y``: linear taper up to tier 6, then vertical (stem) at tier‑6 chord."""
    yb = _pyramid_bottom_y()
    if y <= yb:
        return _triangle_left_x(y)
    return _triangle_left_x(yb)


def _triangle_right_x(y: float) -> float:
    """Right boundary of the pyramid outline at vertical ``y``."""
    return _pyramid_center_x() + _triangle_width_at(y) / 2


def _parallel_line_from_left_pyramid_edge(
    band_top: float,
    band_bottom: float,
    perpendicular_px: float,
) -> tuple[float, float, float, float]:
    """Segment parallel to the left pyramid boundary, shifted perpendicular to it.

    ``perpendicular_px`` advances along the **inward** normal (into coloured play). Negative
    values move into the exterior margin — where tier captions sit.

    Uses :func:`_outline_left_x_at_y` so stem rows (below tier 6) track the vertical sides
    rather than an extrapolated taper.

    Returned as ``(x0, y0, x1, y1)`` with ``y0`` at ``band_top`` and ``y1`` at ``band_bottom``.
    """
    x0_raw = _outline_left_x_at_y(band_top)
    x1_raw = _outline_left_x_at_y(band_bottom)
    dx = x1_raw - x0_raw
    dy = band_bottom - band_top
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return x0_raw, band_top, x1_raw, band_bottom
    nx, ny = -dy / length, dx / length
    midx = (x0_raw + x1_raw) / 2
    midy = (band_top + band_bottom) / 2
    cx = _pyramid_center_x()
    ref_y = (_pyramid_top_y() + _pyramid_bottom_y()) / 2
    if nx * (cx - midx) + ny * (ref_y - midy) < 0:
        nx, ny = -nx, -ny
    ox = nx * perpendicular_px
    oy = ny * perpendicular_px
    return x0_raw + ox, band_top + oy, x1_raw + oy, band_bottom + oy


def _triangle_left_x_interior(y: float) -> float:
    """Inside edge of playable fill: pyramid left slant offset perpendicularly into the silhouette."""
    inset = PYRAMID_INTERIOR_INSET_PX
    if inset <= 1e-9:
        return _triangle_left_x(y)
    yt = _pyramid_top_y()
    yb = _pyramid_bottom_y()
    x_top, y_top, x_bot, y_bot = _parallel_line_from_left_pyramid_edge(yt, yb, inset)
    denom = y_bot - y_top
    if abs(denom) < 1e-9:
        return x_top
    return x_top + (y - y_top) * (x_bot - x_top) / denom


def _triangle_right_x_interior(y: float) -> float:
    """Mirror of :func:`_triangle_left_x_interior` for the right slant (symmetric about centre x)."""
    if PYRAMID_INTERIOR_INSET_PX <= 1e-9:
        return _triangle_right_x(y)
    return 2 * _pyramid_center_x() - _triangle_left_x_interior(y)


def _triangle_interior_width_at(y: float) -> float:
    """Chord width between inset left and right silhouettes."""
    return _triangle_right_x_interior(y) - _triangle_left_x_interior(y)


def _stem_inner_playfield() -> tuple[float, float]:
    """``(inner_left_x, inner_width)`` for tier 7+ at the tier 6 pyramid base chord."""
    yb = _pyramid_bottom_y()
    iw = max(40.0, _triangle_interior_width_at(yb) - 2 * STEM_INNER_MARGIN_H)
    return _pyramid_center_x() - iw / 2, iw


def _stem_content_top_y() -> float:
    """Y for the tier‑7 stem row cursor: gap + separator bar + matching gap below."""
    y6 = _pyramid_bottom_y()
    g = TIER67_SEPARATOR_GAP_PX
    bar_h = g * TIER67_SEPARATOR_BAR_GAP_MULT
    return y6 + g + bar_h + g


def _pyramid_left_edge_angle_deg_bottom_to_top() -> float:
    """Clockwise SVG ``rotate`` angle so horizontal text aligns with base→apex on the visible left slant.

    Uses the tapered left edge from the apex down to the tier‑6 baseline so captions
    match the stroke above the stem.
    """
    yt = _pyramid_top_y()
    yb = _pyramid_bottom_y()
    x_lt = _triangle_left_x(yt)
    x_lb = _triangle_left_x(yb)
    return math.degrees(math.atan2(yt - yb, x_lt - x_lb))


def _trapezoid_left_points(y0: float, y1: float, x_inner_right: float) -> list[tuple[float, float]]:
    """Trapezium for the leftmost league cell: slanted left (inset pyramid edge), vertical right."""
    return [
        (_triangle_left_x_interior(y0), y0),
        (_triangle_left_x_interior(y1), y1),
        (x_inner_right, y1),
        (x_inner_right, y0),
    ]


def _trapezoid_right_points(y0: float, y1: float, x_inner_left: float) -> list[tuple[float, float]]:
    """Trapezium for the rightmost league cell: vertical left, slanted right (inset pyramid edge)."""
    return [
        (x_inner_left, y0),
        (x_inner_left, y1),
        (_triangle_right_x_interior(y1), y1),
        (_triangle_right_x_interior(y0), y0),
    ]


def _trapezoid_both_points(y0: float, y1: float) -> list[tuple[float, float]]:
    """Trapezium when a single league spans the full tier (both inset pyramid slants)."""
    return [
        (_triangle_left_x_interior(y0), y0),
        (_triangle_left_x_interior(y1), y1),
        (_triangle_right_x_interior(y1), y1),
        (_triangle_right_x_interior(y0), y0),
    ]


@dataclass(frozen=True)
class BandLayout:
    """Horizontal strip geometry for one pyramid tier band."""

    tier_num: int
    band_top: float
    band_bottom: float
    band_center_y: float
    avail_w: float
    row_left_x: float
    cell_w_raw: float
    gap: float
    cell_w: float
    cell_h: float
    row_top_y: float


def compute_band_layout(tier_num: int, n: int) -> BandLayout | None:
    """Shared layout for tier ``tier_num`` with ``n`` equal league slots."""
    if n <= 0:
        return None
    band_top = _pyramid_top_y() + (tier_num - 1) * PYRAMID_BAND_HEIGHT
    band_bottom = band_top + PYRAMID_BAND_HEIGHT
    band_center_y = (band_top + band_bottom) / 2
    inset = 8.0
    safe_w_top = _triangle_interior_width_at(band_top)
    safe_w_bottom = _triangle_interior_width_at(band_bottom)
    avail_w = max(40.0, min(safe_w_top, safe_w_bottom) - 2 * inset)
    cell_w_raw = avail_w / n
    gap = min(8.0, cell_w_raw * 0.06)
    cell_w = cell_w_raw - gap
    cell_h = PYRAMID_BAND_HEIGHT - 16
    row_left_x = _pyramid_center_x() - avail_w / 2
    row_top_y = band_center_y - cell_h / 2
    return BandLayout(
        tier_num=tier_num,
        band_top=band_top,
        band_bottom=band_bottom,
        band_center_y=band_center_y,
        avail_w=avail_w,
        row_left_x=row_left_x,
        cell_w_raw=cell_w_raw,
        gap=gap,
        cell_w=cell_w,
        cell_h=cell_h,
        row_top_y=row_top_y,
    )


def cell_horizontal_extent(layout: BandLayout, index: int) -> tuple[float, float]:
    """Left / right x of league slot ``index`` (rectangular interior)."""
    cx_cell = layout.row_left_x + (index + 0.5) * layout.cell_w_raw
    left = cx_cell - layout.cell_w / 2
    return left, left + layout.cell_w


def _divide_span_into_cells(
    span_left: float, span_right: float, n: int
) -> list[tuple[float, float]]:
    """Split ``[span_left, span_right]`` into ``n`` equal slots using the same gap
    ratios as :func:`compute_band_layout` (for nested tiers 5–6 under a parent).
    """
    if n <= 0:
        return []
    avail_w = max(40.0, span_right - span_left)
    cell_w_raw = avail_w / n
    gap = min(8.0, cell_w_raw * 0.06)
    cell_w = cell_w_raw - gap
    cells: list[tuple[float, float]] = []
    for i in range(n):
        cx = span_left + (i + 0.5) * cell_w_raw
        x_rect = cx - cell_w / 2
        cells.append((x_rect, cell_w))
    return cells


def _apply_interior_column_gaps(
    cells: list[tuple[float, float]], gap: float
) -> list[tuple[float, float]]:
    """Inset shared boundaries between adjacent columns by ``gap / 2`` per side."""
    if len(cells) <= 1:
        return cells
    out: list[tuple[float, float]] = []
    for i, (x, w) in enumerate(cells):
        xl, xr = x, x + w
        if i > 0:
            xl += gap / 2
        if i < len(cells) - 1:
            xr -= gap / 2
        out.append((xl, max(40.0, xr - xl)))
    return out


def _divide_span_weighted(
    span_left: float,
    span_right: float,
    weights: list[float],
) -> list[tuple[float, float]]:
    """Split ``[span_left, span_right]`` proportionally with the same outer inset and
    inter-cell gaps as :func:`_divide_span_into_cells` (symmetric ``gap/2`` margins)."""
    n = len(weights)
    avail_w = max(40.0, span_right - span_left)
    if n == 0:
        return []
    if n == 1:
        gap = min(8.0, avail_w * 0.06)
        inner_w = max(40.0, avail_w - gap)
        return [(span_left + gap / 2, inner_w)]
    sum_w = sum(weights)
    if sum_w <= 0:
        return _divide_span_into_cells(span_left, span_right, n)
    cell_w_raw_equiv = avail_w / n
    gap = min(8.0, cell_w_raw_equiv * 0.06)
    inner_seg_budget = avail_w - n * gap
    raw_segments = [inner_seg_budget * (weights[i] / sum_w) for i in range(n)]
    cells: list[tuple[float, float]] = []
    x_cursor = span_left + gap / 2
    for i, seg_w in enumerate(raw_segments):
        cells.append((x_cursor, seg_w))
        x_cursor += seg_w
        if i < n - 1:
            x_cursor += gap
    return cells


def _outer_span_for_cell(
    index: int,
    n: int,
    cells: list[tuple[float, float]],
    y_ref: float,
) -> tuple[float, float]:
    """Return ``(x_min, x_max)`` for a league cell's **clipped trapezoid** footprint.

    ``cells[i]`` is ``(x_rect, cell_w)`` from the rectangular row layout (same as
    used for tier band rendering). Outer columns use :func:`_triangle_left_x_interior` /
    :func:`_triangle_right_x_interior` at ``y_ref``; interior boundaries use the vertical
    midline between adjacent rectangular interiors.
    """
    if n <= 0 or index < 0 or index >= n:
        return 0.0, 0.0
    x_rect, cell_w = cells[index]
    xr = x_rect + cell_w
    if n == 1:
        return _triangle_left_x_interior(y_ref), _triangle_right_x_interior(y_ref)
    if index == 0:
        left = _triangle_left_x_interior(y_ref)
        x_next = cells[1][0]
        right = (xr + x_next) / 2
        return left, right
    if index == n - 1:
        x_prev, w_prev = cells[index - 1]
        left = (x_prev + w_prev + x_rect) / 2
        right = _triangle_right_x_interior(y_ref)
        return left, right
    x_prev, w_prev = cells[index - 1]
    x_next = cells[index + 1][0]
    left = (x_prev + w_prev + x_rect) / 2
    right = (xr + x_next) / 2
    return left, right


@dataclass(frozen=True)
class NestedTier56Layout:
    """Tier 4–6 horizontal geometry: proportional NL2 columns from tier‑6 weights."""

    tier4_order: tuple[LeagueData, ...]
    tier4_rects: dict[str, tuple[float, float]]
    tier5_order: tuple[LeagueData, ...]
    tier5_rects: dict[str, tuple[float, float]]
    tier6_order: tuple[LeagueData, ...]
    tier6_rects: dict[str, tuple[float, float]]


def _tier6_leaf_horizontal_weight(lg: LeagueData) -> float:
    """Positive weight for proportional column widths (broader leagues → wider slices)."""
    return float(max(8, lg.team_count))


def compute_nested_tier56_layout(
    leagues_by_tier: dict[int, list[LeagueData]],
    slots: dict[tuple[int, str], float],
    *,
    parent_overrides: StemParentOverrides | None = None,
) -> NestedTier56Layout | None:
    """Allocate tiers 4–6 horizontally from tier‑6 leaf weights + feeder tree.

    Tier 6 bands define fractional widths along each row's ``avail_w``; tier 5 splits
    each NL2 column among Regional 1 leagues weighted by descendant tier‑6 totals;
    tier 4 NL2 columns span the union of those fractions. Falls back (returns ``None``)
    when feeder coverage does not match every tier‑5 / tier‑6 league on disk.
    """
    t4_ordered = _ordered_tier_leagues(4, leagues_by_tier, slots)
    if not t4_ordered:
        return None

    n4 = len(t4_ordered)

    t4_to_t5 = _parents_for_child_tier(parent_overrides, 5)
    t5_to_t6 = _parents_for_child_tier(parent_overrides, 6)

    tier6_by_feeder = _leagues_by_feeder_key(leagues_by_tier.get(6, []))
    tier5_by_feeder = _leagues_by_feeder_key(leagues_by_tier.get(5, []))

    tier6_ordered: list[LeagueData] = []
    tier6_parent_nl2: list[str] = []
    tier6_parent_r1: list[str] = []

    for nl2 in t4_ordered:
        nk = nl2.league_name
        for r1_name in t4_to_t5.get(_feeder_match_key(nk), []):
            for r2_name in t5_to_t6.get(_feeder_match_key(r1_name), []):
                lg = tier6_by_feeder.get(_feeder_match_key(r2_name))
                if lg is not None:
                    tier6_ordered.append(lg)
                    tier6_parent_nl2.append(nk)
                    tier6_parent_r1.append(r1_name)

    mapped_t6_names = {lg.league_name for lg in tier6_ordered}
    all_t6 = {lg.league_name for lg in leagues_by_tier.get(6, [])}
    if mapped_t6_names != all_t6:
        logger.warning(
            "Tier 6 leagues missing from tier_mappings JSON %s — using equal-width pyramid.",
            sorted(all_t6 - mapped_t6_names),
        )
        return None

    tier5_expected_names: list[str] = []
    for nl2 in t4_ordered:
        for r1_name in t4_to_t5.get(_feeder_match_key(nl2.league_name), []):
            lg5 = tier5_by_feeder.get(_feeder_match_key(r1_name))
            if lg5 is not None:
                tier5_expected_names.append(lg5.league_name)
    mapped_t5_names = set(tier5_expected_names)
    all_t5 = {lg.league_name for lg in leagues_by_tier.get(5, [])}
    if mapped_t5_names != all_t5:
        logger.warning(
            "Tier 5 leagues missing from tier_mappings JSON %s — using equal-width pyramid.",
            sorted(all_t5 - mapped_t5_names),
        )
        return None

    weights = [_tier6_leaf_horizontal_weight(lg) for lg in tier6_ordered]
    tw = sum(weights)
    cumfrac = [0.0]
    for w in weights:
        cumfrac.append(cumfrac[-1] + w / tw)

    lay4 = compute_band_layout(4, 1)
    lay4_slots = compute_band_layout(4, n4)
    lay5_ref = compute_band_layout(5, 1)
    lay6_ref = compute_band_layout(6, 1)
    if lay4 is None or lay4_slots is None or lay5_ref is None or lay6_ref is None:
        return None

    gap_t4 = lay4_slots.gap
    inner_left_t4 = lay4.row_left_x + gap_t4 / 2
    inner_avail_t4 = lay4.avail_w - gap_t4

    tier4_cells_list: list[tuple[float, float]] = []
    for nl2 in t4_ordered:
        nk = nl2.league_name
        idxs = [i for i, nm in enumerate(tier6_parent_nl2) if nm == nk]
        if not idxs:
            logger.warning("No tier 6 leaves under NL2 %r — using equal-width pyramid.", nk)
            return None
        lam_a = cumfrac[idxs[0]]
        lam_b = cumfrac[idxs[-1] + 1]
        xl = inner_left_t4 + lam_a * inner_avail_t4
        xr = inner_left_t4 + lam_b * inner_avail_t4
        tier4_cells_list.append((xl, xr - xl))

    tier4_cells_list = _apply_interior_column_gaps(tier4_cells_list, gap_t4)
    tier4_rects = {nl2.league_name: tier4_cells_list[i] for i, nl2 in enumerate(t4_ordered)}

    y_alloc5 = lay5_ref.band_center_y
    y_alloc6 = lay6_ref.band_center_y

    tier5_order_list: list[LeagueData] = []
    tier5_rects: dict[str, tuple[float, float]] = {}

    for i_nl2, nl2 in enumerate(t4_ordered):
        outer_l, outer_r = _outer_span_for_cell(i_nl2, n4, tier4_cells_list, y_alloc5)
        r1_feed = t4_to_t5.get(_feeder_match_key(nl2.league_name), [])
        r1_kids: list[LeagueData] = []
        for r1_name in r1_feed:
            lg = tier5_by_feeder.get(_feeder_match_key(r1_name))
            if lg is None:
                logger.warning("Missing tier 5 league %r under NL2 %r.", r1_name, nl2.league_name)
                return None
            r1_kids.append(lg)
        child_weights = [
            sum(
                weights[i]
                for i in range(len(tier6_ordered))
                if _feeder_match_key(tier6_parent_r1[i]) == _feeder_match_key(rlg.league_name)
            )
            for rlg in r1_kids
        ]
        child_weights = [max(1.0, w) for w in child_weights]
        cells = _divide_span_weighted(outer_l, outer_r, child_weights)
        if len(cells) != len(r1_kids):
            return None
        for lg, cell in zip(r1_kids, cells, strict=False):
            tier5_rects[lg.league_name] = cell
            tier5_order_list.append(lg)

    if [lg.league_name for lg in tier5_order_list] != tier5_expected_names:
        return None

    tier5_cells_ordered = [tier5_rects[lg.league_name] for lg in tier5_order_list]
    n5 = len(tier5_cells_ordered)

    tier6_order_list: list[LeagueData] = []
    tier6_rects: dict[str, tuple[float, float]] = {}

    name_to_idx = {lg.league_name: i for i, lg in enumerate(tier6_ordered)}
    for j, r1 in enumerate(tier5_order_list):
        outer_l, outer_r = _outer_span_for_cell(j, n5, tier5_cells_ordered, y_alloc6)
        r2_feed = t5_to_t6.get(_feeder_match_key(r1.league_name), [])
        kids: list[LeagueData] = []
        for r2_name in r2_feed:
            lg = tier6_by_feeder.get(_feeder_match_key(r2_name))
            # JSON parent maps may list alternate RFU namesakes for the same branch (e.g.
            # Midlands West vs West Midlands); absent slots are skipped so each season only
            # uses the entries that actually appear on disk.
            if lg is not None:
                kids.append(lg)
        if not kids:
            logger.warning(
                "No tier 6 leagues on disk for feeder children of %r — using equal-width pyramid.",
                r1.league_name,
            )
            return None
        cw = [max(1.0, weights[name_to_idx[lg.league_name]]) for lg in kids]
        cells = _divide_span_weighted(outer_l, outer_r, cw)
        if len(cells) != len(kids):
            return None
        for lg, cell in zip(kids, cells, strict=False):
            tier6_rects[lg.league_name] = cell
            tier6_order_list.append(lg)

    if [lg.league_name for lg in tier6_order_list] != [lg.league_name for lg in tier6_ordered]:
        return None

    return NestedTier56Layout(
        tier4_order=tuple(t4_ordered),
        tier4_rects=tier4_rects,
        tier5_order=tuple(tier5_order_list),
        tier5_rects=tier5_rects,
        tier6_order=tuple(tier6_order_list),
        tier6_rects=tier6_rects,
    )


@dataclass(frozen=True)
class WomensNestedLayout:
    """Visual bands 1–6 geometry for ``--womens`` (tier 7+ stem omitted)."""

    tier_orders: dict[int, tuple[LeagueData, ...]]
    tier_rects: dict[int, dict[str, tuple[float, float]]]


def _womens_geo_tail_raw(league_name: str, tier_band: int) -> str:
    """RFU geography after stripping this visual band's ``Women's …`` title prefix."""
    name = _strip_league_title_sponsors(league_name).strip()
    remainder: str | None = None
    for prefix in _WOMENS_LEAGUE_PREFIXES_BY_VISIBLE_TIER.get(tier_band, ()):
        if name.casefold() == prefix.casefold():
            return ""
        full = prefix + " "
        if name.casefold().startswith(full.casefold()):
            remainder = name[len(prefix) + 1 :].strip()
            break
    if remainder is None:
        lowered = name.casefold()
        if lowered.startswith("women's"):
            remainder = name.partition("women's")[2].lstrip().strip()
        else:
            remainder = name
    return _strip_trailing_numeric_suffix(remainder.strip())


def _womens_remainder_prefix_anchor_ok(parent_cf: str, child_cf: str) -> bool:
    """True when ``parent_cf`` prefixes ``child_cf`` before a finer subdivision."""
    if not parent_cf:
        return False
    if not child_cf.startswith(parent_cf):
        return False
    if len(child_cf) == len(parent_cf):
        return True
    boundary = child_cf[len(parent_cf)]
    return boundary.isspace() or boundary == "("


def _womens_normalize_label_for_band(name: str, tier_band: int) -> str:
    """Comparable tail for JSON parent shorthand (digits stripped after prefix removal)."""
    return _womens_geo_tail_raw(name, tier_band).casefold()


def _match_womens_parent_override_label(
    parents: list[LeagueData],
    want_raw: str,
    ptier: int,
    *,
    child_league_name: str,
) -> LeagueData | None:
    """Resolve one ``women`` section parent entry against tier-(``ptier``) leagues."""
    want_raw = _strip_league_title_sponsors(want_raw).strip()
    if not want_raw:
        return None
    want_cf = want_raw.casefold()
    exact = [
        lg
        for lg in parents
        if _strip_league_title_sponsors(lg.league_name).strip().casefold() == want_cf
    ]
    if len(exact) == 1:
        return exact[0]
    elif len(exact) > 1:
        logger.warning(
            "Ambiguous women's parent override %r for %r (tier %d); skipping entry.",
            want_raw,
            child_league_name,
            ptier,
        )
        return None
    want_tail = _womens_normalize_label_for_band(want_raw, ptier)
    tails: list[tuple[str, LeagueData]] = []
    for p in parents:
        tails.append((_womens_normalize_label_for_band(p.league_name, p.tier_num), p))
    if want_tail != "":
        by_tail = [(t, lg) for t, lg in tails if want_tail == t]
        if len(by_tail) == 1:
            return by_tail[0][1]
        if len(by_tail) > 1:
            by_tail_sorted = sorted(by_tail, key=lambda it: (-len(it[0]), it[1].league_name))
            return by_tail_sorted[0][1]
        suff = [(t, lg) for t, lg in tails if t.endswith(want_tail) or want_tail.endswith(t)]
        suff = [(t, lg) for t, lg in suff if t and want_tail]
        if len(suff) == 1:
            return suff[0][1]
        if len(suff) > 1:
            suff.sort(key=lambda it: (-min(len(it[0]), len(want_tail)), it[1].league_name))
            return suff[0][1]
    logger.warning(
        "Women's parent override %r for %r does not match any tier-%d league; ignoring.",
        want_raw,
        child_league_name,
        ptier,
    )
    return None


def _womens_infer_parent_leagues(
    child: LeagueData,
    candidates: list[LeagueData],
) -> list[LeagueData]:
    """Infer feeder parent row by longest anchored geographic prefix on tails."""
    if not candidates:
        return []
    child_cf = _womens_geo_tail_raw(child.league_name, child.tier_num).casefold()
    if not child_cf:
        return []
    best_score = -1
    winners: list[LeagueData] = []
    for p in candidates:
        p_cf = _womens_geo_tail_raw(p.league_name, p.tier_num).casefold()
        if not _womens_remainder_prefix_anchor_ok(p_cf, child_cf):
            continue
        sc = len(p_cf)
        if sc > best_score:
            best_score = sc
            winners = [p]
        elif sc == best_score:
            winners.append(p)
    if winners:
        if len(winners) > 1:
            winners.sort(key=lambda lg: lg.league_name)
            return [winners[0]]
        return winners

    wc = child_cf.strip()
    if wc == "east" or wc.startswith("east ") or wc.startswith("east("):
        mids: list[tuple[int, LeagueData]] = []
        for p in candidates:
            p_cf_all = _womens_geo_tail_raw(p.league_name, p.tier_num).casefold()
            if "midlands" in p_cf_all:
                mids.append((len(p_cf_all), p))
        if mids:
            mids.sort(key=lambda kv: (-kv[0], kv[1].league_name))
            return [mids[0][1]]

    fc_head = child_cf.split("(", maxsplit=1)[0].strip()
    if fc_head.startswith("midlands"):
        mids2: list[tuple[int, LeagueData]] = []
        for p in candidates:
            p_cf_all = _womens_geo_tail_raw(p.league_name, p.tier_num).casefold()
            p_head = p_cf_all.split("(", maxsplit=1)[0].strip()
            if p_head.startswith("midlands"):
                mids2.append((len(p_cf_all), p))
        if mids2:
            mids2.sort(key=lambda kv: (-kv[0], kv[1].league_name))
            return [mids2[0][1]]

    if " " in fc_head:
        cl: list[tuple[int, LeagueData]] = []
        for p in candidates:
            p_cf_all = _womens_geo_tail_raw(p.league_name, p.tier_num).casefold()
            p_head = p_cf_all.split("(", maxsplit=1)[0].strip()
            if p_head == fc_head:
                cl.append((len(p_cf_all), p))
        if cl:
            cl.sort(key=lambda kv: (-kv[0], kv[1].league_name))
            return [cl[0][1]]

    return []


def _bridge_womens_midlands_championship_to_north_band2(
    child: LeagueData,
    tier_2_parents: list[LeagueData],
) -> list[LeagueData]:
    """Tier-3 Midlands Championship nests under tier-2 North (RFU pyramid split convention)."""
    if child.tier_num != 3 or not tier_2_parents:
        return []
    cf = _womens_geo_tail_raw(child.league_name, child.tier_num).casefold()
    if not cf.startswith("midlands"):
        return []
    north_parents = sorted(
        (
            lg
            for lg in tier_2_parents
            if _womens_geo_tail_raw(lg.league_name, lg.tier_num).casefold().startswith("north")
        ),
        key=lambda lg: lg.league_name,
    )
    return [north_parents[0]] if north_parents else []


def _resolve_womens_feeder_parents(
    child: LeagueData,
    leagues_by_tier: dict[int, list[LeagueData]],
    womens_overrides: StemParentOverrides | None,
) -> list[LeagueData]:
    """JSON ``women`` section wins; else prefix inference on tier-(N−1) row.

    Used only for feeder bands ``2``–``4`` (Championship / NC1); bands ``5``–``6`` do not call this.
    """
    ptier = child.tier_num - 1
    if ptier < 1:
        return []
    parents_here = list(leagues_by_tier.get(ptier, ()))
    key = (child.tier_num, child.league_name)
    ovs = womens_overrides or {}
    if key in ovs:
        specs = ovs[key]
        if not specs:
            return []
        matched: list[LeagueData] = []
        seen: set[tuple[int, str]] = set()
        for want_raw in specs:
            p = _match_womens_parent_override_label(
                parents_here,
                want_raw,
                ptier,
                child_league_name=child.league_name,
            )
            if p is None:
                continue
            pk = (p.tier_num, p.league_name)
            if pk not in seen:
                seen.add(pk)
                matched.append(p)
        return matched
    if ptier == 1:
        return parents_here
    inferred = _womens_infer_parent_leagues(child, parents_here)
    if inferred:
        return inferred
    if child.tier_num == 3:
        bridged = _bridge_womens_midlands_championship_to_north_band2(child, parents_here)
        if bridged:
            return bridged
    return []


def compute_womens_nested_layout(
    leagues_by_tier: dict[int, list[LeagueData]],
    womens_overrides: StemParentOverrides | None,
) -> WomensNestedLayout | None:
    """Taper women's bands 1–4 only: Premiership → Championship → NC1.

    Uses :func:`_outer_span_for_cell` and :func:`_divide_span_weighted` under inferred or JSON
    feeder links for bands ``2``–``4``. Bands ``5``–``6`` (NC2, NC3) are equal-width rows with
    no parent–child geometry — RFU promotion mapping between NC1 and NC2 is not encoded here.
    """

    parent_map: dict[tuple[int, str], tuple[LeagueData, ...]] = {}
    for tier in range(2, 5):
        for lg in leagues_by_tier.get(tier, ()):
            plist = _resolve_womens_feeder_parents(lg, leagues_by_tier, womens_overrides)
            if not plist:
                logger.warning(
                    "Women's pyramid: no feeder parent for %r (band %d) — using equal-width layout.",
                    lg.league_name,
                    tier,
                )
                return None
            parent_map[(tier, lg.league_name)] = tuple(plist)

    children_by: dict[tuple[int, str], list[LeagueData]] = defaultdict(list)
    for tier in range(2, 5):
        for lg in leagues_by_tier.get(tier, ()):
            p0 = parent_map[(tier, lg.league_name)][0]
            children_by[(p0.tier_num, p0.league_name)].append(lg)
    for k in children_by:
        children_by[k].sort(key=lambda x: x.league_name)

    sw_memo: dict[tuple[int, str], float] = {}

    def subtree_w(lg: LeagueData) -> float:
        key = (lg.tier_num, lg.league_name)
        if key in sw_memo:
            return sw_memo[key]
        kids = children_by.get((lg.tier_num, lg.league_name), ())
        if not kids:
            sw_memo[key] = 1.0
            return 1.0
        v = sum(subtree_w(c) for c in kids)
        sw_memo[key] = v
        return v

    tier_orders_acc: dict[int, list[LeagueData]] = {t: [] for t in range(1, 7)}
    tier_rects_acc: dict[int, dict[str, tuple[float, float]]] = {}

    tier1_parents = sorted(leagues_by_tier.get(1, ()), key=lambda lg: lg.league_name)
    if not tier1_parents:
        logger.warning("Women's pyramid: no tier-1 league — using equal-width layout.")
        return None

    n1 = len(tier1_parents)
    lay1 = compute_band_layout(1, n1)
    if lay1 is None:
        return None
    rects1: dict[str, tuple[float, float]] = {}
    for i, lg in enumerate(tier1_parents):
        cx_cell = lay1.row_left_x + (i + 0.5) * lay1.cell_w_raw
        x_rect = cx_cell - lay1.cell_w / 2
        rects1[lg.league_name] = (x_rect, lay1.cell_w)
    tier_orders_acc[1] = list(tier1_parents)
    tier_rects_acc[1] = rects1

    parent_row = tier1_parents
    parent_rects = rects1
    parent_tier = 1

    for child_tier in range(2, 5):
        lay_child = compute_band_layout(child_tier, 1)
        if lay_child is None:
            return None
        y_child = lay_child.band_center_y

        npc = len(parent_row)
        parent_cells_list = [parent_rects[lg.league_name] for lg in parent_row]

        child_order: list[LeagueData] = []
        rects_next: dict[str, tuple[float, float]] = {}

        for j, p in enumerate(parent_row):
            outer_l, outer_r = _outer_span_for_cell(j, npc, parent_cells_list, y_child)
            kids = children_by.get((parent_tier, p.league_name), [])
            if not kids:
                logger.warning(
                    "Women's pyramid: no children under %r (band %d) — using equal-width layout.",
                    p.league_name,
                    parent_tier,
                )
                return None
            weights = [max(1.0, subtree_w(c)) for c in kids]
            cells = _divide_span_weighted(outer_l, outer_r, weights)
            if len(cells) != len(kids):
                return None
            for c, cell in zip(kids, cells, strict=False):
                child_order.append(c)
                rects_next[c.league_name] = cell

        parent_row = child_order
        parent_rects = rects_next
        parent_tier = child_tier
        tier_orders_acc[child_tier] = child_order
        tier_rects_acc[child_tier] = rects_next

    for lower_tier in (5, 6):
        leagues_low = sorted(
            leagues_by_tier.get(lower_tier, ()),
            key=lambda lg: lg.league_name,
        )
        if not leagues_low:
            logger.warning(
                "Women's pyramid: no tier-%d leagues — using equal-width layout.",
                lower_tier,
            )
            return None
        lay_low = compute_band_layout(lower_tier, len(leagues_low))
        if lay_low is None:
            return None
        rects_low: dict[str, tuple[float, float]] = {}
        for i, lg in enumerate(leagues_low):
            cx_cell = lay_low.row_left_x + (i + 0.5) * lay_low.cell_w_raw
            x_rect = cx_cell - lay_low.cell_w / 2
            rects_low[lg.league_name] = (x_rect, lay_low.cell_w)
        tier_orders_acc[lower_tier] = leagues_low
        tier_rects_acc[lower_tier] = rects_low

    tier_orders = {t: tuple(tier_orders_acc[t]) for t in range(1, 7)}
    return WomensNestedLayout(tier_orders=tier_orders, tier_rects=tier_rects_acc)


def _ordered_tier_leagues(
    tier_num: int,
    leagues_by_tier: dict[int, list[LeagueData]],
    slots: dict[tuple[int, str], float],
) -> list[LeagueData]:
    leagues = leagues_by_tier.get(tier_num, [])
    return sorted(leagues, key=lambda lg: slots.get((lg.tier_num, lg.league_name), 0.5))


# ---------------------------------------------------------------------------
# Logo grid sizing within a league cell
# ---------------------------------------------------------------------------


def _crest_slot_team_name_font_scale(tier_num: int) -> float:
    """Scale crest-slot team labels down for dense Counties (tier 7+) grids."""
    if tier_num <= 6:
        return 1.0
    return max(0.2, 0.38 - (tier_num - 7) * 0.058)


def _best_grid(n: int, box_w: float, box_h: float) -> tuple[int, int, float]:
    """Pick (cols, rows, logo_size) maximising logo_size while covering ``n``
    items within a box of ``box_w × box_h``."""
    if n <= 0:
        return (1, 1, 0.0)

    best_cols, best_rows, best_size = 1, n, 0.0
    # Try a sensible range of column counts; the optimum is near
    # ``sqrt(n * box_w / box_h)`` but we sweep widely to be safe.
    max_cols = max(1, n)
    for cols in range(1, max_cols + 1):
        rows = math.ceil(n / cols)
        size = min(box_w / cols, box_h / rows)
        if size > best_size:
            best_size = size
            best_cols, best_rows = cols, rows
    return (best_cols, best_rows, best_size)


def _womens_league_logo_cap_px(cell_h: float, visible_tier: int) -> float:
    """Upper bound on crest tile edge for women's pyramid league cells."""
    abs_cap = _LEAGUE_LOGO_WOMENS_ABS_BY_BAND_MAX_TIER[-1][1]
    frac_cap = _LEAGUE_LOGO_WOMENS_CELL_FRAC_BY_BAND_MAX_TIER[-1][1]
    for mx, cap in _LEAGUE_LOGO_WOMENS_ABS_BY_BAND_MAX_TIER:
        if visible_tier <= mx:
            abs_cap = cap
            break
    for mx, fc in _LEAGUE_LOGO_WOMENS_CELL_FRAC_BY_BAND_MAX_TIER:
        if visible_tier <= mx:
            frac_cap = fc
            break
    return min(abs_cap, cell_h * frac_cap)


# ---------------------------------------------------------------------------
# SVG primitives
# ---------------------------------------------------------------------------


def _svg_text(
    text: str,
    x: float,
    y: float,
    *,
    fill: str = "#000000",
    size: float = 14.0,
    weight: str = "normal",
    anchor: str = "start",
    family: str = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
) -> str:
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" '
        f'font-family="{family}" font-size="{size:.2f}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" '
        f'dominant-baseline="middle">{xml_escape(text)}</text>'
    )


def _svg_rotated_centred_text(
    text: str,
    cx: float,
    cy: float,
    rotate_deg: float,
    *,
    fill: str = TIER_LABEL_TEXT,
    size: float = 16.0,
    weight: str = "600",
    family: str = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
) -> str:
    esc = xml_escape(text)
    return (
        f'<text x="{cx:.3f}" y="{cy:.3f}" '
        f'transform="rotate({rotate_deg:.5f} {cx:.3f} {cy:.3f})" '
        f'font-family="{family}" font-size="{size:.2f}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="middle" dominant-baseline="middle" '
        f'text-rendering="geometricPrecision">{esc}</text>'
    )


def _svg_rect(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str = "none",
    stroke: str = "none",
    stroke_width: float = 1.0,
    rx: float = 0.0,
) -> str:
    corner = f' rx="{rx:.2f}"' if rx > 0 else ""
    return (
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}"{corner} '
        f'fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{stroke_width:.2f}"/>'
    )


def _svg_line_horizontal(
    x0: float,
    x1: float,
    y: float,
    *,
    stroke: str,
    stroke_width: float,
) -> str:
    return (
        f'<line x1="{x0:.2f}" y1="{y:.2f}" x2="{x1:.2f}" y2="{y:.2f}" '
        f'stroke="{stroke}" stroke-width="{stroke_width:.2f}" stroke-linecap="butt"/>'
    )


def _crest_club_label(team_name: str) -> str:
    """Prefer club name over RFU XV suffixes ("II", "2nd XV", …); matches address dedup rules."""
    raw = (team_name or "").strip()
    if not raw:
        return "?"
    cand = team_name_to_club_name(raw).strip()
    return cand or raw


def _team_lower_xv_roman(team_name: str) -> str | None:
    """Roman ordinal for reserve XVs (RFU ``II`` / ``2nd XV`` style); ``None`` for principal sides."""
    raw = (team_name or "").strip()
    if not raw:
        return None
    parts = raw.split()
    if len(parts) < 2:
        return None
    last = parts[-1]
    if last in ("II", "III", "IV", "V"):
        return last
    last_two = f"{parts[-2]} {parts[-1]}"
    if last_two == "2nd XV":
        return "II"
    if last_two == "3rd XV":
        return "III"
    if last_two == "4th XV":
        return "IV"
    if last_two == "5th XV":
        return "V"
    if last_two == "6th XV":
        return "VI"
    return None


def _svg_lower_xv_roman_corner(
    roman: str,
    bx: float,
    by_slot: float,
    inner_sz: float,
    *,
    fill: str,
) -> str:
    """Small serif Roman badge, top-right inside the crest square (stroke halo for contrast)."""
    size = max(8.0, min(15.0, inner_sz * 0.26))
    tx = bx + inner_sz - max(1.5, size * 0.12)
    ty = by_slot + size * 0.58
    esc = xml_escape(roman)
    sw = max(0.75, size * 0.13)
    # Tier bands use either light or dark league titles; halo must contrast with both fill and crests.
    halo = "#1a2330" if _hex_relative_luminance(fill) > 0.55 else "#ffffff"
    hop = "0.88" if halo == "#ffffff" else "0.92"
    return (
        f'<text x="{tx:.2f}" y="{ty:.2f}" '
        f'font-family="Times New Roman, Times, serif" font-size="{size:.2f}" '
        f'font-weight="700" fill="{fill}" text-anchor="end" dominant-baseline="middle" '
        f'stroke="{halo}" stroke-opacity="{hop}" stroke-width="{sw:.2f}" '
        f'paint-order="stroke fill" text-rendering="geometricPrecision">{esc}</text>'
    )


# Extra padding around each crest tile so embedded HTML/CSS can draw club labels past the crest box.
_LABEL_EXPAND_INNER_RATIO = 0.70

# Fallback SVG text wrapping: refuse unbounded stacking for pathological RFU strings.
_FALLBACK_CLUB_LABEL_MAX_LINES = 18


def _svg_crest_foreign_object_slot(
    x: float,
    y: float,
    inner_sz: float,
    href: str,
    club_label: str,
    *,
    text_fill: str,
    font_scale: float = 1.0,
) -> str:
    """Crest tile: remote ``<img>`` plus hidden HTML club label on ``onerror`` (404, etc.).

    The ``foreignObject`` is enlarged around the crest box (``overflow:visible`` on SVG and
    XHTML sides) so a long fallback name can spill past the crest square.

    SVG ``<image>`` alone cannot flip to text when remote PNG fails; Chromium and other
    browsers handle ``onerror`` for HTML ``img``, including Playwright rasterisation.
    """
    nm = (club_label or "").strip() or "?"
    fz = max(5, min(12, int(inner_sz * 0.21 * font_scale)))
    name_body = html.escape(nm, quote=False)
    alt_h = html.escape(nm, quote=True)
    src_h = html.escape(href, quote=True)

    expand = max(14.0, inner_sz * _LABEL_EXPAND_INNER_RATIO)
    side = inner_sz + 2.0 * expand
    fox = x - expand
    foy = y - expand

    div_body = (
        f'<div xmlns="http://www.w3.org/1999/xhtml" '
        f'style="box-sizing:border-box;margin:0;padding:{expand:.2f}px;width:100%;height:100%;'
        'display:flex;flex-direction:column;align-items:center;justify-content:flex-start;overflow:visible">'
        f'<div style="flex:0 0 auto;box-sizing:border-box;width:{inner_sz:.2f}px;min-height:{inner_sz:.2f}px;'
        'display:flex;flex-direction:column;align-items:center;justify-content:center;overflow:visible">'
        '<img referrerpolicy="no-referrer" '
        "onerror=\"this.style.display='none';var n=this.nextElementSibling;if(n)n.style.display='flex';\""
        ' style="max-width:100%;max-height:100%;object-fit:contain;display:block" '
        f'src="{src_h}" alt="{alt_h}" />'
        '<div style="display:none;box-sizing:border-box;width:100%;flex:1 1 auto;min-height:0;'
        "flex-wrap:wrap;align-content:center;justify-content:center;text-align:center;"
        "line-height:1.12;"
        f"color:{text_fill};font-size:{fz}px;font-weight:600;"
        "font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;overflow:visible;"
        'word-wrap:break-word;hyphens:auto">'
        f"{name_body}</div></div></div>"
    )
    return (
        f'<foreignObject overflow="visible" x="{fox:.2f}" y="{foy:.2f}" '
        f'width="{side:.2f}" height="{side:.2f}">{div_body}</foreignObject>'
    )


def _shorten(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "\u2026"


def _split_oversized_token(token: str, max_chars_per_line: int) -> list[str]:
    if max_chars_per_line < 1:
        max_chars_per_line = 1
    if len(token) <= max_chars_per_line:
        return [token]
    return [token[i : i + max_chars_per_line] for i in range(0, len(token), max_chars_per_line)]


def _wrap_league_title_lines(text: str, max_width_px: float, font_size: float) -> list[str]:
    """Greedy word-wrap; splits long tokens so each line stays within estimated width."""
    char_w = max(font_size * LEAGUE_TITLE_CHAR_WIDTH_EM, 2.5)
    max_chars = max(4, int(max_width_px / char_w))

    segments: list[str] = []
    for w in text.split():
        if len(w) <= max_chars:
            segments.append(w)
        else:
            segments.extend(_split_oversized_token(w, max_chars))

    lines: list[str] = []
    current: list[str] = []
    for seg in segments:
        trial = " ".join(current + [seg]) if current else seg
        if current and len(trial) * char_w > max_width_px:
            lines.append(" ".join(current))
            current = [seg]
        elif current:
            current.append(seg)
        else:
            current = [seg]
    if current:
        lines.append(" ".join(current))
    return lines


def _crest_slot_fallback_name_svg(
    bx: float,
    by: float,
    inner_sz: float,
    club_label: str,
    *,
    fill: str,
    font_scale: float = 1.0,
    label_expand_px: float = 0.0,
    wrap_width_px: float | None = None,
) -> str:
    """Render a club label for a crest slot when no usable ``image_url`` is available."""
    nm = (club_label or "").strip() or "?"
    margin = 3.0
    mw = wrap_width_px
    if mw is None:
        mw = max(24.0, inner_sz - 2 * margin + 2.0 * label_expand_px)

    font_sz = min(11.0 * font_scale, max(4.8, inner_sz * 0.22 * font_scale))
    line_h = font_sz * LEAGUE_TITLE_LINE_HEIGHT_RATIO

    lines = _wrap_league_title_lines(nm, max(6.0, mw), font_sz)
    if len(lines) > _FALLBACK_CLUB_LABEL_MAX_LINES:
        lines = lines[:_FALLBACK_CLUB_LABEL_MAX_LINES]
        lines[-1] = _shorten(
            lines[-1],
            max(4, int(mw / (font_sz * LEAGUE_TITLE_CHAR_WIDTH_EM))),
        )

    cx = bx + inner_sz / 2.0
    cy_mid = by + inner_sz / 2.0
    y0 = cy_mid - (len(lines) - 1) * line_h / 2.0
    return "\n".join(
        _svg_text(
            line,
            cx,
            y0 + i * line_h,
            fill=fill,
            size=font_sz,
            weight="600",
            anchor="middle",
        )
        for i, line in enumerate(lines)
    )


# ---------------------------------------------------------------------------
# Pyramid rendering (tiers 1–6)
# ---------------------------------------------------------------------------


def _svg_polygon_points_attr(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def _render_league_cell(
    league: LeagueData,
    x: float,
    y: float,
    w: float,
    h: float,
    bg: str,
    title_color: str,
    season: str,
    *,
    trapezoid_points: list[tuple[float, float]] | None = None,
    clip_id: str | None = None,
    safe_left_x: float | None = None,
    safe_right_x: float | None = None,
    show_league_title: bool = True,
    crest_href_remap: dict[str, str] | None = None,
    gender: Gender = DEFAULT_GENDER,
) -> str:
    """SVG fragment for one league cell: optional title strip + grid of crests or name fallbacks
    (``+X`` badge only when ``team_count`` exceeds loaded team rows).

    When ``trapezoid_points`` is set (four corners), league titles and the polygon fill use
    the clip path; crest tiles (**foreignObject**, SVG name fallbacks, ``+X`` badges) render in
    an overlay group **without** that clip so long club labels can extend past cells.

    When ``safe_left_x`` / ``safe_right_x`` are provided they bound the logo grid to a
    rectangle inscribed inside the trapezium so placements do not crowd the slanted pyramid
    edge.
    """
    team_rows = list(league.teams)
    extra_vs_count = max(0, league.team_count - len(team_rows))
    place_extra_badge = extra_vs_count > 0
    n_slots = len(team_rows) + (1 if place_extra_badge else 0)
    crest_name_font_scale = _crest_slot_team_name_font_scale(league.tier_num)
    cell_stroke = LEAGUE_CELL_STROKE_WOMENS if gender == "womens" else LEAGUE_CELL_STROKE_MENS

    grid_left = x + LEAGUE_CELL_PADDING_X
    grid_right = x + w - LEAGUE_CELL_PADDING_X
    if safe_left_x is not None:
        grid_left = max(grid_left, safe_left_x)
    if safe_right_x is not None:
        grid_right = min(grid_right, safe_right_x)
    logo_area_x = grid_left
    logo_area_w = max(0.0, grid_right - grid_left)

    title_parts: list[str] = []
    crest_parts: list[str] = []

    if show_league_title:
        short_name = league_short_display_name(
            league.league_name, league.tier_num, season, gender=gender
        )
        inner_w_title = max(24.0, w - 2 * LEAGUE_CELL_PADDING_X)
        min_logo_reserve = max(52.0, min(h * 0.26, logo_area_w * 0.45)) if n_slots > 0 else 8.0
        max_title_px = max(
            float(LEAGUE_TITLE_HEIGHT),
            min(h - LEAGUE_CELL_PADDING_Y - min_logo_reserve, h * 0.62),
        )

        font_sz = LEAGUE_TITLE_FONT_MAX
        lines = _wrap_league_title_lines(short_name, inner_w_title, font_sz)
        for _ in range(int(LEAGUE_TITLE_FONT_MAX - LEAGUE_TITLE_FONT_MIN) + 1):
            line_h_try = font_sz * LEAGUE_TITLE_LINE_HEIGHT_RATIO
            max_lines_allowed = max(1, int(max_title_px / line_h_try))
            lines = _wrap_league_title_lines(short_name, inner_w_title, font_sz)
            if len(lines) <= max_lines_allowed:
                break
            font_sz -= 1.0

        line_h = font_sz * LEAGUE_TITLE_LINE_HEIGHT_RATIO
        max_lines_allowed = max(1, int(max_title_px / line_h))
        if len(lines) > max_lines_allowed:
            lines = lines[:max_lines_allowed]
            est_chars = max(6, int(inner_w_title / (font_sz * LEAGUE_TITLE_CHAR_WIDTH_EM)))
            lines[-1] = _shorten(lines[-1], est_chars)
        if not lines:
            lines = [short_name]

        title_area_h = min(max_title_px, len(lines) * line_h + 4.0)
        title_area_h = max(title_area_h, min(line_h + 4.0, float(LEAGUE_TITLE_HEIGHT)))

        cx = x + w / 2
        cy_mid = y + title_area_h / 2
        y_line0 = cy_mid - (len(lines) - 1) * line_h / 2
        for i, line in enumerate(lines):
            title_parts.append(
                _svg_text(
                    line,
                    cx,
                    y_line0 + i * line_h,
                    fill=title_color,
                    size=font_sz,
                    weight="600",
                    anchor="middle",
                )
            )

        logo_area_y = y + title_area_h
        logo_area_h = max(0.0, h - title_area_h - LEAGUE_CELL_PADDING_Y)
    else:
        if gender == "mens":
            logo_area_y = y + LEAGUE_CELL_PADDING_Y
            logo_area_h = max(0.0, h - 2 * LEAGUE_CELL_PADDING_Y)
        else:
            reserve_top = (
                LEAGUE_LOGO_GRID_TITLE_RESERVE_WOMENS_PREM
                if league.tier_num == 1
                else LEAGUE_LOGO_GRID_TITLE_RESERVE_Y
            )
            logo_area_y = y + reserve_top
            logo_area_h = max(0.0, h - reserve_top - LEAGUE_CELL_PADDING_Y)

    cols, rows, logo_size = _best_grid(n_slots, logo_area_w, logo_area_h)
    if gender == "womens":
        logo_cap = _womens_league_logo_cap_px(h, league.tier_num)
        if logo_size > logo_cap > 0:
            logo_size = logo_cap
    if logo_size > 0 and n_slots > 0:
        grid_w = cols * logo_size
        grid_h = rows * logo_size
        grid_x0 = logo_area_x + (logo_area_w - grid_w) / 2
        grid_y0 = logo_area_y + (logo_area_h - grid_h) / 2

        pad = min(LEAGUE_LOGO_PADDING, logo_size * 0.08)
        inner_sz = logo_size - 2 * pad

        crest_label_expand = max(14.0, inner_sz * _LABEL_EXPAND_INNER_RATIO)
        crest_wrap_w = max(
            24.0,
            inner_sz + 2.0 * crest_label_expand,
            logo_size * 1.08,
        )

        idx = 0
        for tm in team_rows:
            r = idx // cols
            c = idx % cols
            cell_x = grid_x0 + c * logo_size
            cell_y = grid_y0 + r * logo_size
            bx = cell_x + pad
            by_slot = cell_y + pad
            club_nm = _crest_club_label(tm.name)
            if tm.image_url:
                href = (
                    crest_href_remap.get(tm.image_url, tm.image_url)
                    if crest_href_remap
                    else tm.image_url
                )
                crest_parts.append(
                    _svg_crest_foreign_object_slot(
                        bx,
                        by_slot,
                        inner_sz,
                        href,
                        club_nm,
                        text_fill=title_color,
                        font_scale=crest_name_font_scale,
                    )
                )
            else:
                crest_parts.append(
                    _crest_slot_fallback_name_svg(
                        bx,
                        by_slot,
                        inner_sz,
                        club_nm,
                        fill=title_color,
                        font_scale=crest_name_font_scale,
                        label_expand_px=crest_label_expand,
                        wrap_width_px=crest_wrap_w,
                    )
                )
            roman_badge = _team_lower_xv_roman(tm.name)
            if roman_badge:
                crest_parts.append(
                    _svg_lower_xv_roman_corner(
                        roman_badge,
                        bx,
                        by_slot,
                        inner_sz,
                        fill=title_color,
                    )
                )
            idx += 1

        if place_extra_badge:
            r = idx // cols
            c = idx % cols
            cell_x = grid_x0 + c * logo_size
            cell_y = grid_y0 + r * logo_size
            bx = cell_x + pad
            by_slot = cell_y + pad
            if gender == "womens":
                badge_bg = "#3d282c"
                badge_border = "#6d5256"
                badge_text_fill = "#fdf7f6"
            else:
                badge_bg = "#2a3548"
                badge_border = "#5a6a85"
                badge_text_fill = "#eef3fa"
            crest_parts.append(
                _svg_rect(
                    bx,
                    by_slot,
                    inner_sz,
                    inner_sz,
                    fill=badge_bg,
                    stroke=badge_border,
                    stroke_width=1.5,
                )
            )
            crest_parts.append(
                _svg_text(
                    f"+{extra_vs_count}",
                    bx + inner_sz / 2,
                    by_slot + inner_sz / 2,
                    fill=badge_text_fill,
                    size=max(11.0, min(inner_sz * 0.42, 22.0)),
                    weight="700",
                    anchor="middle",
                )
            )

    clipped_body = "\n".join(title_parts)
    crest_layer_xml = "\n".join(crest_parts)

    if trapezoid_points is not None and clip_id is not None:
        pts = _svg_polygon_points_attr(trapezoid_points)
        poly_bg = (
            f'<polygon points="{pts}" fill="{bg}" stroke="{cell_stroke}" stroke-width="1.00"/>'
        )
        return (
            f'<defs><clipPath id="{xml_escape(clip_id)}">'
            f'<polygon points="{pts}"/></clipPath></defs>\n'
            f'<g clip-path="url(#{xml_escape(clip_id)})">\n'
            f"{poly_bg}\n"
            f"{clipped_body}\n"
            "</g>\n"
            f"<g>\n{crest_layer_xml}\n</g>"
        )

    outline = _svg_rect(x, y, w, h, fill=bg, stroke=cell_stroke, stroke_width=1.0)
    return f"{outline}\n{clipped_body}\n{crest_layer_xml}"


def _tier_band_stats_line(league_count: int, total_teams: int) -> str:
    lw = "league" if league_count == 1 else "leagues"
    tw = "team" if total_teams == 1 else "teams"
    return f"{league_count} {lw} · {total_teams} {tw}"


def _tier_margin_label_svg(
    tier_num: int,
    season: str,
    band_top: float,
    band_bottom: float,
    *,
    rotate_deg: float | None = None,
    league_count: int | None = None,
    total_teams: int | None = None,
    gender: Gender = DEFAULT_GENDER,
    merit_competition: str | None = None,
    merit_local_offset: int = 0,
) -> str:
    """Tier caption parallel to the left pyramid/stem silhouette in the exterior margin.

    Optionally appends a second line (farther outward) giving league and team totals for that
    tier band when ``league_count`` and ``total_teams`` are set.

    ``rotate_deg`` defaults to the tapered pyramid edge angle (tiers 1–6).
    Stem tiers pass :data:`COUNTIES_MARGIN_TIER_LABEL_ROTATE_DEG` (vertical + 180° flip vs 90°).

    ``tier_num`` is the **visual** band number (1..6 for women's, 1..11 for men's). The
    human-readable tier name is derived from ``gender`` so women's bands read e.g.
    ``Premiership Women's`` and ``National Challenge 1``.
    """
    tier_human = pyramid_band_tier_label(
        tier_num,
        season,
        gender,
        merit_competition=merit_competition,
        merit_local_offset=merit_local_offset,
    )
    x_top, y_top, x_bottom, y_bottom = _parallel_line_from_left_pyramid_edge(
        band_top, band_bottom, -EDGE_TIER_LABEL_OUTSET_PX
    )
    path_len = math.hypot(x_top - x_bottom, y_bottom - y_top)
    max_chars = max(10, min(52, int(path_len / 9.8)))
    label = _shorten(tier_human, max_chars)
    font_sz = min(19.5, max(11.8, path_len / max(len(label) * 0.58, 1.0)))
    mx = (x_top + x_bottom) / 2
    my = (y_top + y_bottom) / 2
    angle = _pyramid_left_edge_angle_deg_bottom_to_top() if rotate_deg is None else rotate_deg
    tier_label_fill = TIER_MARGIN_LABEL_TEXT_WOMENS if gender == "womens" else TIER_LABEL_TEXT
    chunks: list[str] = [
        _svg_rotated_centred_text(label, mx, my, angle, size=font_sz, fill=tier_label_fill)
    ]

    if league_count is not None and total_teams is not None:
        stem_margin_rotate = rotate_deg is not None and math.isclose(
            rotate_deg, COUNTIES_MARGIN_TIER_LABEL_ROTATE_DEG
        )
        stats_extra = (
            EDGE_TIER_STATS_OUTSET_EXTRA_STEM_PX
            if stem_margin_rotate
            else EDGE_TIER_STATS_OUTSET_EXTRA_PX
        )
        sx_top, sy_top, sx_bottom, sy_bottom = _parallel_line_from_left_pyramid_edge(
            band_top,
            band_bottom,
            -(EDGE_TIER_LABEL_OUTSET_PX + stats_extra),
        )
        smx = (sx_top + sx_bottom) / 2
        smy = (sy_top + sy_bottom) / 2
        path_s = math.hypot(sx_top - sx_bottom, sy_bottom - sy_top)
        cap = _tier_band_stats_line(league_count, total_teams)
        stat_sz = min(13.8, max(9.8, path_s / max(len(cap) * 0.52, 1.0)))
        stats_fill = TIER_STATS_LABEL_TEXT_WOMENS if gender == "womens" else TIER_STATS_LABEL_TEXT
        chunks.append(
            _svg_rotated_centred_text(
                cap,
                smx,
                smy,
                angle,
                fill=stats_fill,
                size=stat_sz,
                weight="500",
            )
        )

    return "\n".join(chunks)


def _render_pyramid_band(
    tier_num: int,
    leagues: list[LeagueData],
    slots: dict[tuple[int, str], float],
    season: str,
    *,
    nested: NestedTier56Layout | None = None,
    womens_nested: WomensNestedLayout | None = None,
    crest_href_remap: dict[str, str] | None = None,
    gender: Gender = DEFAULT_GENDER,
    merit_competition: str | None = None,
    merit_local_offset: int = 0,
) -> str:
    """Render one tier band of the pyramid triangle (tiers 1–6).

    Tier 4–6 use proportional widths derived from tier‑6 weights when feeder data is
    complete (men's only); otherwise equal-width slots per band.

    For ``gender == "womens"``, :class:`WomensNestedLayout` applies the tier 1–4 taper when feeder
    resolution succeeds (prefix inference plus optional ``women`` section on bands ``2–4``)
    and lays out NC2/NC3 as equal-width rows; otherwise every band is equal-width alphabetical.
    """
    if not leagues:
        return ""

    use_nested_t4 = (
        gender == "mens"
        and tier_num == 4
        and nested is not None
        and len(leagues) == len(nested.tier4_order)
        and all(lg.league_name in nested.tier4_rects for lg in leagues)
    )
    use_nested_t5 = (
        gender == "mens"
        and tier_num == 5
        and nested is not None
        and len(leagues) == len(nested.tier5_order)
        and all(lg.league_name in nested.tier5_rects for lg in leagues)
    )
    use_nested_t6 = (
        gender == "mens"
        and tier_num == 6
        and nested is not None
        and len(leagues) == len(nested.tier6_order)
        and all(lg.league_name in nested.tier6_rects for lg in leagues)
    )

    w_order = womens_nested.tier_orders.get(tier_num, ()) if womens_nested is not None else ()
    w_rects = womens_nested.tier_rects.get(tier_num, {}) if womens_nested is not None else {}
    use_womens_nested = (
        gender == "womens"
        and w_order
        and len(w_order) == len(leagues)
        and frozenset(lg.league_name for lg in w_order)
        == frozenset(lg.league_name for lg in leagues)
        and all(lg.league_name in w_rects for lg in leagues)
    )

    if use_nested_t4:
        assert nested is not None
        leagues_ordered = list(nested.tier4_order)
        rects_map = nested.tier4_rects
    elif use_nested_t5:
        assert nested is not None
        leagues_ordered = list(nested.tier5_order)
        rects_map = nested.tier5_rects
    elif use_nested_t6:
        assert nested is not None
        leagues_ordered = list(nested.tier6_order)
        rects_map = nested.tier6_rects
    elif use_womens_nested:
        assert womens_nested is not None
        leagues_ordered = list(w_order)
        rects_map = w_rects
    elif gender == "womens" or merit_competition is not None:
        # Merit comps don't follow men's NL2/Regional naming so the slot table is meaningless;
        # alphabetical equal-width matches the women's fallback at the same band.
        leagues_ordered = sorted(leagues, key=lambda lg: lg.league_name)
        rects_map = None
    else:
        leagues_ordered = sorted(
            leagues, key=lambda lg: slots.get((lg.tier_num, lg.league_name), 0.5)
        )
        rects_map = None

    n = len(leagues_ordered)
    lay_vertical = compute_band_layout(tier_num, 1)
    if lay_vertical is None:
        return ""

    lay_equal: BandLayout | None = None
    if rects_map is None:
        lay_equal = compute_band_layout(tier_num, n)
        if lay_equal is None:
            return ""

    y0 = lay_vertical.row_top_y
    y1 = lay_vertical.row_top_y + lay_vertical.cell_h

    parts: list[str] = []
    for i, lg in enumerate(leagues_ordered):
        bg, title_color = _league_cell_tier_colors(lg, tier_num, gender)
        if rects_map is not None:
            x_rect, cell_w = rects_map[lg.league_name]
        else:
            assert lay_equal is not None
            cx_cell = lay_equal.row_left_x + (i + 0.5) * lay_equal.cell_w_raw
            x_rect = cx_cell - lay_equal.cell_w / 2
            cell_w = lay_equal.cell_w

        trap_pts: list[tuple[float, float]] | None = None
        clip_id: str | None = None
        safe_left_x: float | None = None
        safe_right_x: float | None = None
        if n == 1:
            trap_pts = _trapezoid_both_points(y0, y1)
            clip_id = f"pyramidT{tier_num}both"
            if gender == "womens":
                # Mid-band chord — avoids over-narrow crest grids at the apex when mirroring men's Prem scale.
                y_safe = (y0 + y1) / 2
                safe_left_x = _triangle_left_x_interior(y_safe) + LEAGUE_SLANT_GAP
                safe_right_x = _triangle_right_x_interior(y_safe) - LEAGUE_SLANT_GAP
            else:
                safe_left_x = _triangle_left_x_interior(y0) + LEAGUE_SLANT_GAP
                safe_right_x = _triangle_right_x_interior(y0) - LEAGUE_SLANT_GAP
        elif i == 0:
            trap_pts = _trapezoid_left_points(y0, y1, x_rect + cell_w)
            clip_id = f"pyramidT{tier_num}L{i}"
            safe_left_x = _triangle_left_x_interior(y0) + LEAGUE_SLANT_GAP
        elif i == n - 1:
            trap_pts = _trapezoid_right_points(y0, y1, x_rect)
            clip_id = f"pyramidT{tier_num}R{i}"
            safe_right_x = _triangle_right_x_interior(y0) - LEAGUE_SLANT_GAP

        parts.append(
            _render_league_cell(
                lg,
                x_rect,
                lay_vertical.row_top_y,
                cell_w,
                lay_vertical.cell_h,
                bg,
                title_color,
                season,
                trapezoid_points=trap_pts,
                clip_id=clip_id,
                safe_left_x=safe_left_x,
                safe_right_x=safe_right_x,
                show_league_title=n > 1,
                crest_href_remap=crest_href_remap,
                gender=gender,
            )
        )

    bt = lay_vertical.band_top
    bb = lay_vertical.band_bottom
    tier_total_teams = sum(lg.team_count for lg in leagues_ordered)
    parts.append(
        _tier_margin_label_svg(
            tier_num,
            season,
            bt,
            bb,
            league_count=len(leagues_ordered),
            total_teams=tier_total_teams,
            gender=gender,
            merit_competition=merit_competition,
            merit_local_offset=merit_local_offset,
        )
    )

    return "\n".join(parts)


def _extended_pyramid_outline_points(stem_bottom_y: float) -> list[tuple[float, float]]:
    """Taper plus straight stem matching tier‑6 base width."""
    apex_y = _pyramid_top_y()
    y6 = _pyramid_bottom_y()
    cx = _pyramid_center_x()
    w_top = _triangle_width_at(apex_y)
    w6 = _triangle_width_at(y6)
    tl = (cx - w_top / 2, apex_y)
    tr = (cx + w_top / 2, apex_y)
    br = (cx + w6 / 2, y6)
    bl = (cx - w6 / 2, y6)
    if stem_bottom_y <= y6 + 0.05:
        return [tl, tr, br, bl]
    return [
        tl,
        tr,
        br,
        (cx + w6 / 2, stem_bottom_y),
        (cx - w6 / 2, stem_bottom_y),
        bl,
    ]


def _render_pyramid_outline(stem_bottom_y: float) -> str:
    pts_str = _svg_polygon_points_attr(_extended_pyramid_outline_points(stem_bottom_y))
    return (
        f'<polygon points="{pts_str}" fill="none" stroke="{TRIANGLE_STROKE}" '
        f'stroke-width="{TRIANGLE_STROKE_WIDTH:.1f}" '
        f'stroke-linejoin="miter" shape-rendering="geometricPrecision"/>'
    )


def _tier67_separator_bar_svg() -> str:
    """Band between tier 6 and Counties — uniform inset ``G``, bar height ``G * mult``."""
    y6 = _pyramid_bottom_y()
    g = TIER67_SEPARATOR_GAP_PX
    bar_h = g * TIER67_SEPARATOR_BAR_GAP_MULT
    xl = _triangle_left_x(y6) + g
    xr = _triangle_right_x(y6) - g
    w = max(0.0, xr - xl)
    y_top = y6 + g
    body = _svg_rect(
        xl, y_top, w, bar_h, fill=TIER67_SEPARATOR_BAR_FILL, stroke="none", stroke_width=0.0
    )
    y_edge = y_top + bar_h
    cap = _svg_line_horizontal(
        xl,
        xr,
        y_top,
        stroke=TIER67_SEPARATOR_BAR_STROKE_TOP,
        stroke_width=1.25,
    )
    cup = _svg_line_horizontal(
        xl,
        xr,
        y_edge,
        stroke=TIER67_SEPARATOR_BAR_STROKE_BOTTOM,
        stroke_width=1.25,
    )
    return "\n".join([body, cap, cup])


# ---------------------------------------------------------------------------
# Counties stem (tier 7+): forest, partitioning, autolayouts
# ---------------------------------------------------------------------------
#
# Counties leagues (tier 7+) continue below Regional 2 inside a straight-sided stem of
# the same chord width as tier 6. A filled separator band between tier 6 and tier 7
# sits inset inside the triangle outline with equal top, bottom, and side margins; slab
# height scales with that margin. The fill hue is a midpoint between tier-6 and tier-7
# league fills (there is no RFU promotion mapping).
#
# Stem rows nest by name: a league at tier *N+1* is placed under the widest matching
# tier-*N* neighbour - first by literal ``child.league_name.startswith(parent...)``,
# else by the geographical tail after the canonical tier title is stripped (so
# ``Counties 3 Hampshire`` nests under ``Counties 2 Hampshire``). Branch widths: sibling
# columns split proportionally to each child's recursive horizontal footprint — the sum of
# child footprints under plain (single-parent) edges — so nested merit ladders widen ancestor
# bands instead of collapsing when breadth sits on different absolute tiers (the older
# ``max descendants on one tier`` heuristic missed stacked merit depth).
#
# Tier 7 (Counties 1) league columns follow a fixed geographic-ish left-to-right order
# (see :data:`_TIER7_TAIL_SORT_ORDER`); tiers 8-11 remain alphabetically sorted.
#
# Parent override values in ``data/rugby/tier_mappings/<season>.json`` may be a single
# string (one parent) or a JSON array (one stem cell stretched across the horizontal
# union of those tier-(N-1) parent bands; the subtree attaches under the sort-first
# parent for layout bookkeeping). When exactly three child leagues sit under two of
# those parents (two single-parent cells plus one two-parent span), the SVG layout
# places the outers under their respective parents and the spanning league in the
# middle (see :func:`_stem_autolayout_spanning_middle_three_feeders`). When two leagues
# at tier *N* share the same two-parent span, they are placed side by side across that
# union (2->2 grid) instead of each spanning the full width (see
# :func:`_stem_autolayout_two_into_two_dual_spans`).


def _alpha_sort_leagues(leagues: list[LeagueData]) -> list[LeagueData]:
    """Sort by league_name with natural number ordering (so '10' follows '9')."""

    def key(lg: LeagueData) -> tuple[object, ...]:
        parts = re.split(r"(\d+)", lg.league_name)
        return tuple(int(p) if p.isdigit() else p.lower() for p in parts)

    return sorted(leagues, key=key)


# Counties 1 row (tier 7): Western → Southern → Midlands blocks, then northern blocks,
# home counties roughly clockwise — overrides alphabetical stem ordering for this tier only.
_TIER7_TAIL_SORT_ORDER: tuple[str, ...] = (
    "western west",
    "western north",
    "southern south",
    "southern north",
    "midlands west (south)",
    "midlands west (north)",
    "midlands east (south)",
    "midlands east (north)",
    "adm lancashire and cheshire",
    "cumbria",
    "durham and northumberland",
    "yorkshire",
    "eastern counties",
    "essex",
    "herts",
    "middx",
    "kent",
    "surrey sussex",
    "hampshire",
)


def _tier7_normalized_tail(league_name: str, season: str) -> str:
    """Lowercase tail after ``Counties 1 `` with sponsors stripped; ``&`` → ``and``, ``/`` → space."""
    stripped = _strip_league_title_sponsors(league_name).strip()
    prefix = mens_current_tier_name(7, season) + " "
    if stripped.lower().startswith(prefix.lower()):
        tail = stripped[len(prefix) :].strip()
    else:
        tail = stripped
    tail_cf = tail.casefold().replace("&", " and ")
    tail_cf = re.sub(r"[/]", " ", tail_cf)
    return " ".join(tail_cf.split())


def _tier7_ordered_leagues(leagues: list[LeagueData], season: str) -> list[LeagueData]:
    """Tier‑7 stem leagues left‑to‑right per :data:`_TIER7_TAIL_SORT_ORDER`; unknown tails sort last."""
    order = {name: i for i, name in enumerate(_TIER7_TAIL_SORT_ORDER)}

    def sort_key(lg: LeagueData) -> tuple[int, tuple[object, ...]]:
        nt = _tier7_normalized_tail(lg.league_name, season)
        idx = order.get(nt, len(_TIER7_TAIL_SORT_ORDER))
        return idx, _stem_sort_key_league_name(lg.league_name)

    return sorted(leagues, key=sort_key)


def _sorted_stem_leagues_at_tier(
    tier_num: int,
    leagues: list[LeagueData],
    season: str,
) -> list[LeagueData]:
    if tier_num == 7:
        return _tier7_ordered_leagues(leagues, season)
    return _alpha_sort_leagues(leagues)


def _stem_sort_key_league_name(name: str) -> tuple[object, ...]:
    parts = re.split(r"(\d+)", name)
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)


def _stem_sort_children(nodes: list[StemTreeNode]) -> list[StemTreeNode]:
    return sorted(nodes, key=lambda sn: _stem_sort_key_league_name(sn.league.league_name))


def _build_stem_forest(
    leagues_by_tier: dict[int, list[LeagueData]],
    season: str,
    parent_overrides: StemParentOverrides | None = None,
    *,
    log_unlinked: bool = True,
    merit_competition: str | None = None,
) -> tuple[list[StemTreeNode], dict[int, list[StemTreeNode]]]:
    stem_tiers = sorted(t for t in leagues_by_tier if t >= 7)
    orphans: dict[int, list[StemTreeNode]] = {}

    tier7_sorted = _sorted_stem_leagues_at_tier(7, leagues_by_tier.get(7, []), season)
    roots: list[StemTreeNode] = []
    by_league: dict[tuple[int, str], list[StemTreeNode]] = defaultdict(list)
    for lg in tier7_sorted:
        n = StemTreeNode(lg)
        roots.append(n)
        by_league[(lg.tier_num, lg.league_name)].append(n)

    for t in stem_tiers:
        if t <= 7:
            continue
        parents_ld = leagues_by_tier.get(t - 1, [])
        for lg in _sorted_stem_leagues_at_tier(t, leagues_by_tier.get(t, []), season):
            parent_lds = _resolve_stem_parents(
                lg,
                parents_ld,
                season,
                parent_overrides,
                merit_competition=merit_competition,
            )
            if not parent_lds:
                orphan_node = StemTreeNode(lg)
                orphans.setdefault(t, []).append(orphan_node)
                if log_unlinked:
                    logger.info(
                        "Stem tier %d: no tier-%d parent for %r — extra layout row.",
                        t,
                        t - 1,
                        lg.league_name,
                    )
                continue
            parent_lds_sorted = sorted(
                parent_lds,
                key=lambda p: _stem_sort_key_league_name(p.league_name),
            )
            primary = parent_lds_sorted[0]
            pk = (primary.tier_num, primary.league_name)
            parent_nodes = by_league.get(pk, [])
            if not parent_nodes:
                orphans.setdefault(t, []).append(StemTreeNode(lg))
                if log_unlinked:
                    logger.info(
                        "Stem tier %d: parent tier-%d league missing from stem data for %r — extra layout row.",
                        t,
                        t - 1,
                        lg.league_name,
                    )
                continue
            child_node = StemTreeNode(lg)
            if len(parent_lds_sorted) > 1:
                child_node.layout_span_union_parent_names = tuple(
                    p.league_name for p in parent_lds_sorted
                )
            parent_nodes[0].children.append(child_node)
            by_league[(lg.tier_num, lg.league_name)].append(child_node)

    uniq_nodes: dict[int, StemTreeNode] = {}
    for r in roots:
        uniq_nodes[id(r)] = r
    for lst in by_league.values():
        for sn in lst:
            uniq_nodes[id(sn)] = sn
    for n in uniq_nodes.values():
        n.children = _stem_sort_children(n.children)

    return roots, orphans


def _stem_subtree_horizontal_footprint(node: StemTreeNode) -> int:
    """Rough horizontal slice needed under ``node`` for nested counties + merit ladders.

    Each plain child contributes at least one unit; grandchildren add recursively so parallel
    merit tiers stacked under different intermediate parents widen higher ancestors (merged
    ``pyramid_All_Leagues`` diagram).
    """
    if not node.children:
        return 1
    total = 0
    for ch in node.children:
        total += max(1, _stem_subtree_horizontal_footprint(ch))
    return max(1, total)


def _stem_branch_column_weight(node: StemTreeNode) -> float:
    """Sibling share from cumulative subtree footprint (national + merit descendants)."""
    return float(max(1, _stem_subtree_horizontal_footprint(node)))


def _stem_child_parent_map(roots: list[StemTreeNode]) -> dict[int, StemTreeNode | None]:
    """Map ``id(node)`` → immediate parent :class:`StemTreeNode` (roots map to ``None``)."""
    out: dict[int, StemTreeNode | None] = {}

    def walk(par: StemTreeNode | None, n: StemTreeNode) -> None:
        out[id(n)] = par
        for ch in n.children:
            walk(n, ch)

    for r in roots:
        walk(None, r)
    return out


def _iter_stem_subtree(node: StemTreeNode) -> Iterator[StemTreeNode]:
    """Yield ``node`` and all its descendants in DFS pre-order (child list order)."""
    yield node
    for ch in node.children:
        yield from _iter_stem_subtree(ch)


def _iter_stem_forest(roots: list[StemTreeNode]) -> Iterator[StemTreeNode]:
    """Yield every node in the stem forest in DFS pre-order (root list, then per-root subtree)."""
    for r in roots:
        yield from _iter_stem_subtree(r)


def _stem_build_stem_node_index(roots: list[StemTreeNode]) -> dict[tuple[int, str], StemTreeNode]:
    return {(n.league.tier_num, n.league.league_name): n for n in _iter_stem_forest(roots)}


def _stem_three_under_two_try_match(
    span_child: StemTreeNode,
    index: dict[tuple[int, str], StemTreeNode],
    child_parent: dict[int, StemTreeNode | None],
) -> tuple[StemTreeNode, StemTreeNode, StemTreeNode, StemTreeNode] | None:
    """If ``span_child`` is the middle of a 3→2 row, return ``(p_a, p_b, child_under_a, child_under_b)``.

    ``child_under_a`` / ``child_under_b`` are the sole non-span tier-``tc`` children under ``p_a`` /
    ``p_b``. Callers map these onto ``layout_x``-sorted parents for left/middle/right columns.

    ``p_a`` and ``p_b`` need not share an immediate parent (e.g. Berks Counties 3 North/South hang
    under different Counties 2 leagues); they must share the **same grandparent** stem node so both
    divisions belong to one regional column under the pyramid.
    """
    names = span_child.layout_span_union_parent_names
    if not names or len(names) != 2:
        return None
    tc = span_child.league.tier_num
    tp = tc - 1
    span_names = frozenset(names)
    if len(span_names) != 2:
        return None

    pnodes: list[StemTreeNode] = []
    for pname in names:
        pn = index.get((tp, pname))
        if pn is None:
            return None
        pnodes.append(pn)

    pa, pb = pnodes[0], pnodes[1]
    parent_pa = child_parent.get(id(pa))
    parent_pb = child_parent.get(id(pb))
    if parent_pa is None or parent_pb is None:
        return None
    if parent_pa is not parent_pb:
        grand_pa = child_parent.get(id(parent_pa))
        grand_pb = child_parent.get(id(parent_pb))
        if grand_pa is None or grand_pa is not grand_pb:
            return None

    want = frozenset({pa.league.league_name, pb.league.league_name})
    if span_names != want:
        return None

    def singles_at(parent: StemTreeNode, child_tier: int = tc) -> list[StemTreeNode]:
        return [
            ch
            for ch in parent.children
            if ch.league.tier_num == child_tier and not ch.layout_span_union_parent_names
        ]

    sa = singles_at(pa)
    sb = singles_at(pb)
    if len(sa) != 1 or len(sb) != 1:
        return None

    if not (span_child in pa.children) ^ (span_child in pb.children):
        return None

    return (pa, pb, sa[0], sb[0])


def _stem_dual_parent_equal_band_pairs(roots: list[StemTreeNode]) -> set[frozenset[int]]:
    """Sibling bands forced equal wherever a league spans exactly two named parents.

    Every dual-parent override identifies two tier-(N−1) anchors ``pa`` / ``pb``. Horizontal weight
    must treat those anchors' partition siblings equally before union-span passes run — otherwise a
    subtree that hosts extra tiers under only one anchor (e.g. Midlands East NE/NW under North while
    Central spans North+South) inflates that anchor and breaks the span geometry.

    Resolution:

    * If both anchors share one immediate parent (common case below Regional roots), average ``pa``
      and ``pb``.
    * If their immediate parents differ (e.g. Berks Counties 3 North vs South → different Counties
      2 parents), average those parents instead.
    * If both anchors are stem roots (parents ``None``), average the anchors themselves — needed
      when a Counties 2 league spans two Counties 1 roots (e.g. Midlands West East/West).
    """
    index = _stem_build_stem_node_index(roots)
    parent_map = _stem_child_parent_map(roots)
    pairs: set[frozenset[int]] = set()

    def walk(n: StemTreeNode) -> None:
        names = n.layout_span_union_parent_names
        if names and len(names) == 2 and len(frozenset(names)) == 2:
            tc = n.league.tier_num
            tp = tc - 1
            ordered = sorted(names, key=lambda s: _stem_sort_key_league_name(s))
            pa = index.get((tp, ordered[0]))
            pb = index.get((tp, ordered[1]))
            if pa is not None and pb is not None and pa is not pb:
                p_par = parent_map.get(id(pa))
                q_par = parent_map.get(id(pb))
                if p_par is not None and q_par is not None:
                    if p_par is not q_par:
                        pairs.add(frozenset({id(p_par), id(q_par)}))
                    else:
                        pairs.add(frozenset({id(pa), id(pb)}))
                elif p_par is None and q_par is None:
                    pairs.add(frozenset({id(pa), id(pb)}))
        for ch in n.children:
            walk(ch)

    for r in roots:
        walk(r)
    return pairs


def _stem_two_into_two_dual_span_pairs(
    roots: list[StemTreeNode],
) -> list[tuple[StemTreeNode, StemTreeNode, StemTreeNode, StemTreeNode]]:
    """Sibling leagues that share the exact same two-parent span → lay out 2×2 under those parents.

    Returns tuples ``(child_left, child_right, parent_left, parent_right)`` with ``*_left`` slots
    ordered by :func:`_stem_sort_key_league_name` (no layout pass required).

    Callers should add ``frozenset({id(parent_left), id(parent_right)})`` to equal-weight sibling
    pairs (``_stem_build_layout`` does this) so the tier-(N−1) columns split 50/50 and line up with
    this row's 50/50 split.
    """
    index = _stem_build_stem_node_index(roots)
    parent_map = _stem_child_parent_map(roots)

    span_children: list[StemTreeNode] = []

    def walk(n: StemTreeNode) -> None:
        if len(n.layout_span_union_parent_names) == 2:
            span_children.append(n)
        for ch in n.children:
            walk(ch)

    for r in roots:
        walk(r)

    groups: dict[tuple[int, frozenset[str]], list[StemTreeNode]] = defaultdict(list)
    for n in span_children:
        groups[(n.league.tier_num, frozenset(n.layout_span_union_parent_names))].append(n)

    out: list[tuple[StemTreeNode, StemTreeNode, StemTreeNode, StemTreeNode]] = []
    for members in groups.values():
        if len(members) != 2:
            continue
        a, b = members[0], members[1]
        if frozenset(a.layout_span_union_parent_names) != frozenset(
            b.layout_span_union_parent_names
        ):
            continue
        tc = a.league.tier_num
        tp = tc - 1
        sorted_names = sorted(
            a.layout_span_union_parent_names,
            key=lambda s: _stem_sort_key_league_name(s),
        )
        pa = index.get((tp, sorted_names[0]))
        pb = index.get((tp, sorted_names[1]))
        if pa is None or pb is None:
            continue
        p_left, p_right = sorted(
            (pa, pb),
            key=lambda p: _stem_sort_key_league_name(p.league.league_name),
        )
        if parent_map.get(id(a)) is None or parent_map.get(id(a)) is not parent_map.get(id(b)):
            continue
        c_left, c_right = sorted(
            (a, b),
            key=lambda sn: _stem_sort_key_league_name(sn.league.league_name),
        )
        out.append((c_left, c_right, p_left, p_right))

    return out


def _stem_resolve_equal_pair_weights(
    children: list[StemTreeNode],
    weights: list[float],
    equal_weight_pairs: set[frozenset[int]],
) -> None:
    """Average weights for sibling pairs in ``equal_weight_pairs`` (preserves their combined share)."""
    if not equal_weight_pairs:
        return
    ids = [id(c) for c in children]
    n = len(children)
    for i in range(n):
        for j in range(i + 1, n):
            if frozenset({ids[i], ids[j]}) in equal_weight_pairs:
                avg = (weights[i] + weights[j]) / 2.0
                weights[i] = avg
                weights[j] = avg


def _stem_partition_subtree(
    node: StemTreeNode,
    x0: float,
    w: float,
    *,
    equal_weight_pairs: set[frozenset[int]],
) -> None:
    node.layout_x = x0
    node.layout_w = max(1e-6, w)
    if not node.children:
        return

    children = node.children
    plain = [c for c in children if not c.layout_span_union_parent_names]
    spanning = [c for c in children if c.layout_span_union_parent_names]

    if not plain:
        for ch in children:
            _stem_partition_subtree(ch, x0, max(1e-6, w), equal_weight_pairs=equal_weight_pairs)
        return

    weights = [_stem_branch_column_weight(c) for c in plain]
    _stem_resolve_equal_pair_weights(plain, weights, equal_weight_pairs)
    tw = sum(weights)
    gap = STEM_CHILD_GAP_PX
    nch = len(plain)
    inner = max(0.0, w - gap * max(0, nch - 1))
    pos = x0
    accrued = 0.0

    for i, ch in enumerate(plain):
        if i == nch - 1:
            cw = max(1e-6, inner - accrued)
        else:
            cw = inner * ((weights[i] / tw) if tw > 0 else 1.0 / nch)
            accrued += cw
        _stem_partition_subtree(ch, pos, cw, equal_weight_pairs=equal_weight_pairs)
        pos += cw
        if i < nch - 1:
            pos += gap

    # Multi-parent leagues take their band from _stem_apply_multi_parent_span_layouts (and related
    # autolayout passes). Including them in this horizontal split steals width from real siblings
    # (e.g. Midlands East NE/NW squeezed beside Counties 3 Central).
    strip = max(1e-6, STEM_CHILD_GAP_PX)
    for sc in spanning:
        sc.layout_x = x0
        sc.layout_w = strip


def _stem_partition_roots(
    roots: list[StemTreeNode],
    stem_inner_w: float,
    *,
    equal_weight_pairs: set[frozenset[int]],
) -> None:
    if not roots:
        return
    weights = [_stem_branch_column_weight(r) for r in roots]
    _stem_resolve_equal_pair_weights(list(roots), weights, equal_weight_pairs)
    tw = sum(weights)
    gap = STEM_CHILD_GAP_PX
    n = len(roots)
    inner = max(0.0, stem_inner_w - gap * max(0, n - 1))
    pos = 0.0
    accrued = 0.0

    for i, r in enumerate(roots):
        if i == n - 1:
            w_blk = max(1e-6, inner - accrued)
        else:
            w_blk = inner * ((weights[i] / tw) if tw > 0 else 1.0 / n)
            accrued += w_blk
        _stem_partition_subtree(r, pos, w_blk, equal_weight_pairs=equal_weight_pairs)
        pos += w_blk
        if i < n - 1:
            pos += gap


def _stem_apply_multi_parent_span_layouts(
    roots: list[StemTreeNode],
    *,
    equal_weight_pairs: set[frozenset[int]],
    dual_span_skip_ids: frozenset[int] = frozenset(),
) -> None:
    """Stretch nodes with multi-parent overrides across the union of parent tier bands."""

    index = _stem_build_stem_node_index(roots)
    span_nodes = [n for n in _iter_stem_forest(roots) if n.layout_span_union_parent_names]
    span_nodes.sort(key=lambda sn: sn.league.tier_num, reverse=True)

    for n in span_nodes:
        if id(n) in dual_span_skip_ids:
            continue
        ptier = n.league.tier_num - 1
        rects: list[tuple[float, float]] = []
        for pname in n.layout_span_union_parent_names:
            pnode = index.get((ptier, pname))
            if pnode is None:
                continue
            rects.append((pnode.layout_x, pnode.layout_x + pnode.layout_w))
        if len(rects) < 2:
            continue
        x0 = min(a for a, _ in rects)
        x1 = max(b for _, b in rects)
        _stem_partition_subtree(n, x0, max(1e-6, x1 - x0), equal_weight_pairs=equal_weight_pairs)


def _stem_autolayout_two_into_two_dual_spans(
    pairs: list[tuple[StemTreeNode, StemTreeNode, StemTreeNode, StemTreeNode]],
    *,
    equal_weight_pairs: set[frozenset[int]],
) -> None:
    """Split the union of two parent bands evenly between two sibling dual-span leagues.

    Used when overrides give **two** tier-*N* leagues the **same** ``[parent₁, parent₂]`` span so
    both draw from the same Counties pair (e.g. Midlands West East/West each → North+South): draw
    them as **two columns** instead of stacking two full-width spans.
    """
    if not pairs:
        return
    gap = STEM_CHILD_GAP_PX
    for c_left, c_right, p_left, p_right in pairs:
        union_x0 = min(p_left.layout_x, p_right.layout_x)
        union_x1 = max(
            p_left.layout_x + p_left.layout_w,
            p_right.layout_x + p_right.layout_w,
        )
        band_w = max(1e-6, union_x1 - union_x0)
        inner = max(0.0, band_w - gap)
        if inner <= 0:
            continue
        w_first = inner / 2.0
        pos = union_x0
        _stem_partition_subtree(c_left, pos, w_first, equal_weight_pairs=equal_weight_pairs)
        pos += w_first + gap
        w_second = max(1e-6, union_x0 + band_w - pos)
        _stem_partition_subtree(c_right, pos, w_second, equal_weight_pairs=equal_weight_pairs)

        logger.debug(
            "Stem auto 2→2 dual-span tier %d: %r | %r under %r / %r",
            c_left.league.tier_num,
            c_left.league.league_name,
            c_right.league.league_name,
            p_left.league.league_name,
            p_right.league.league_name,
        )


def _stem_autolayout_spanning_middle_three_feeders(
    roots: list[StemTreeNode],
    *,
    equal_weight_pairs: set[frozenset[int]],
) -> None:
    """Place one row under two parents when exactly three children exist: two single-parent + one span.

    Structural pattern (child tier ``tc``, parents at ``tc - 1``):

    * One league lists two parents (``layout_span_union_parent_names``) — drawn **between** the outers.
    * Exactly one **non-span** child under each parent at ``tc``.

    The two parent columns use **equal sibling weights** during :func:`_stem_partition_roots` (see
    :func:`_stem_dual_parent_equal_band_pairs`) so the spanning league, stored under only one
    parent in the tree, does not inflate that parent's column.

    Horizontal space is the union of the two parent bands. Lower-row widths use
    ``w_left, (w_left + w_right) / 2, w_right`` (equal parents ⇒ equal thirds).
    Optional ``stem_slot_strips`` run later and may override.
    """
    index = _stem_build_stem_node_index(roots)
    parent_map = _stem_child_parent_map(roots)
    gap = STEM_CHILD_GAP_PX

    span_nodes = [
        n
        for n in _iter_stem_forest(roots)
        if n.layout_span_union_parent_names and len(n.layout_span_union_parent_names) == 2
    ]
    span_nodes.sort(key=lambda sn: sn.league.tier_num, reverse=True)

    for span_child in span_nodes:
        tc = span_child.league.tier_num
        m = _stem_three_under_two_try_match(span_child, index, parent_map)
        if m is None:
            continue
        pa, pb, l_node, r_node = m
        p_left, p_right = sorted((pa, pb), key=lambda p: p.layout_x)
        union_x0 = min(p_left.layout_x, p_right.layout_x)
        union_x1 = max(
            p_left.layout_x + p_left.layout_w,
            p_right.layout_x + p_right.layout_w,
        )
        band_w = max(1e-6, union_x1 - union_x0)

        inner = max(0.0, band_w - gap * 2.0)
        wl = float(p_left.layout_w)
        wr = float(p_right.layout_w)
        wm = (wl + wr) / 2.0
        tw = wl + wm + wr
        if tw <= 0 or inner <= 0:
            continue

        w_l = inner * (wl / tw)
        w_m = inner * (wm / tw)

        pos = union_x0
        _stem_partition_subtree(l_node, pos, w_l, equal_weight_pairs=equal_weight_pairs)
        pos += w_l + gap
        _stem_partition_subtree(span_child, pos, w_m, equal_weight_pairs=equal_weight_pairs)
        pos += w_m + gap
        w_r_tail = max(1e-6, union_x0 + band_w - pos)
        _stem_partition_subtree(r_node, pos, w_r_tail, equal_weight_pairs=equal_weight_pairs)

        logger.debug(
            "Stem auto 3→2 layout tier %d: %r | %r | %r under %r / %r",
            tc,
            l_node.league.league_name,
            span_child.league.league_name,
            r_node.league.league_name,
            p_left.league.league_name,
            p_right.league.league_name,
        )


def _stem_apply_slot_strips(
    roots: list[StemTreeNode],
    strips: tuple[StemSlotStrip, ...],
    *,
    equal_weight_pairs: set[frozenset[int]],
) -> None:
    """Re-layout named leagues into weighted columns sharing one horizontal span."""
    if not strips:
        return
    index = _stem_build_stem_node_index(roots)
    gap = STEM_CHILD_GAP_PX

    for strip in strips:
        strip_boxes: list[tuple[float, float]] = []
        skip_strip = False
        for band in strip.bands:
            for nm in band.leagues:
                node = index.get((band.tier, nm))
                if node is None:
                    logger.warning(
                        "stem_slot_strips: missing league tier %d %r — skipping strip",
                        band.tier,
                        nm,
                    )
                    skip_strip = True
                    break
                strip_boxes.append((node.layout_x, node.layout_x + node.layout_w))
            if skip_strip:
                break
        if skip_strip or len(strip_boxes) < 2:
            continue

        strip_x0 = min(a for a, _ in strip_boxes)
        strip_x1 = max(b for _, b in strip_boxes)
        strip_width = strip_x1 - strip_x0

        for band in strip.bands:
            n_slots = len(band.leagues)
            if n_slots != len(band.weights) or n_slots == 0:
                continue
            tw = sum(band.weights)
            if tw <= 0:
                continue
            inner = max(0.0, strip_width - gap * max(0, n_slots - 1))
            pos = strip_x0
            for i, nm in enumerate(band.leagues):
                node = index.get((band.tier, nm))
                if node is None:
                    continue
                if i == n_slots - 1:
                    cw = max(1e-6, strip_x0 + strip_width - pos)
                else:
                    cw = inner * (band.weights[i] / tw)
                _stem_partition_subtree(node, pos, cw, equal_weight_pairs=equal_weight_pairs)
                pos += cw
                if i < n_slots - 1:
                    pos += gap


def _stem_collect_hierarchical_placements(
    roots: list[StemTreeNode],
) -> dict[int, list[tuple[LeagueData, float, float]]]:
    out: dict[int, list[tuple[LeagueData, float, float]]] = {}

    def walk(n: StemTreeNode) -> None:
        t = n.league.tier_num
        out.setdefault(t, []).append((n.league, n.layout_x, n.layout_w))
        for ch in n.children:
            walk(ch)

    for r in roots:
        walk(r)
    for cells in out.values():
        cells.sort(key=lambda row: row[1])
    return out


def _stem_orphan_root_geometry(
    orphan_nodes: list[StemTreeNode],
    stem_inner_w: float,
) -> list[tuple[StemTreeNode, float, float]]:
    """Equal-width slots spanning the stem interior for unattached subtree roots."""
    if not orphan_nodes:
        return []
    ordered = sorted(
        orphan_nodes,
        key=lambda sn: _stem_sort_key_league_name(sn.league.league_name),
    )
    n = len(ordered)
    cell_w_raw = stem_inner_w / n
    gap = min(8.0, cell_w_raw * 0.06)
    cell_w = cell_w_raw - gap

    placements: list[tuple[StemTreeNode, float, float]] = []
    for idx, sn in enumerate(ordered):
        cell_cx = (idx + 0.5) * cell_w_raw
        x_left = cell_cx - cell_w / 2
        placements.append((sn, x_left, cell_w))

    return placements


def _canvas_horizontal_weight(
    leagues_by_tier: dict[int, list[LeagueData]],
    stem_forest: tuple[list[StemTreeNode], dict[int, list[StemTreeNode]]] | None,
) -> float:
    """Horizontal packing demand: widest pyramid band vs stem footprint sums vs widest stem row."""
    pyramid_w = max((len(leagues_by_tier.get(t, ())) for t in range(1, 7)), default=0)
    if stem_forest is None:
        return float(max(pyramid_w, 1))

    roots, orphans = stem_forest
    stem_roots_w = math.fsum(_stem_branch_column_weight(r) for r in roots)
    orphan_band_max = 0.0
    for lst in orphans.values():
        if lst:
            orphan_band_max = max(
                orphan_band_max,
                math.fsum(_stem_branch_column_weight(o) for o in lst),
            )

    stem_row_cells_m = 0
    stem_tiers = sorted(t for t in leagues_by_tier if t >= 7)
    for t in stem_tiers:
        leagues_here = leagues_by_tier.get(t, ())
        if not leagues_here:
            continue
        oc = len(orphans.get(t, ()))
        pc = len(leagues_here) - oc
        if pc > 0 and oc > 0:
            row_m = max(pc, oc)
        else:
            row_m = pc + oc
        stem_row_cells_m = max(stem_row_cells_m, row_m)

    return float(max(pyramid_w, stem_roots_w, orphan_band_max, stem_row_cells_m, 1.0))


@dataclass
class StemLayout:
    roots: list[StemTreeNode]
    orphans_by_tier: dict[int, list[StemTreeNode]]
    pure_tree_placements: dict[int, list[tuple[LeagueData, float, float]]]
    orphan_row_positions: dict[int, list[tuple[LeagueData, float, float]]]
    stem_inner_w: float


def _stem_build_layout(
    leagues_by_tier: dict[int, list[LeagueData]],
    season: str,
    parent_overrides: StemParentOverrides | None = None,
    *,
    stem_slot_strips: tuple[StemSlotStrip, ...] = (),
    log_stem_orphans: bool = True,
    merit_competition: str | None = None,
    stem_forest: tuple[list[StemTreeNode], dict[int, list[StemTreeNode]]] | None = None,
) -> StemLayout | None:
    stem_tiers = sorted(t for t in leagues_by_tier if t >= 7)
    if not stem_tiers or not any(leagues_by_tier.get(t) for t in stem_tiers):
        return None

    if stem_forest is None:
        roots, orphans = _build_stem_forest(
            leagues_by_tier,
            season,
            parent_overrides=parent_overrides,
            log_unlinked=log_stem_orphans,
            merit_competition=merit_competition,
        )
    else:
        roots, orphans = stem_forest
    eq_pairs = _stem_dual_parent_equal_band_pairs(roots)
    two_into_two = _stem_two_into_two_dual_span_pairs(roots)
    for _cl, _cr, pa, pb in two_into_two:
        eq_pairs.add(frozenset({id(pa), id(pb)}))
    _, stem_inner_w = _stem_inner_playfield()
    _stem_partition_roots(roots, stem_inner_w, equal_weight_pairs=eq_pairs)
    dual_skip = frozenset(nid for cl, cr, _, _ in two_into_two for nid in (id(cl), id(cr)))
    _stem_apply_multi_parent_span_layouts(
        roots, equal_weight_pairs=eq_pairs, dual_span_skip_ids=dual_skip
    )
    _stem_autolayout_two_into_two_dual_spans(two_into_two, equal_weight_pairs=eq_pairs)
    _stem_autolayout_spanning_middle_three_feeders(roots, equal_weight_pairs=eq_pairs)
    _stem_apply_slot_strips(roots, stem_slot_strips, equal_weight_pairs=eq_pairs)

    pure_tree_placements = _stem_collect_hierarchical_placements(roots)

    orphan_row_positions: dict[int, list[tuple[LeagueData, float, float]]] = {}
    for t in sorted(orphans.keys()):
        ons = orphans[t]
        if not ons:
            continue
        row: list[tuple[LeagueData, float, float]] = []
        for node, lx, lw in _stem_orphan_root_geometry(ons, stem_inner_w):
            _stem_partition_subtree(node, lx, lw, equal_weight_pairs=eq_pairs)
            row.append((node.league, lx, lw))
        row.sort(key=lambda r: r[1])
        orphan_row_positions[t] = row

    return StemLayout(
        roots=roots,
        orphans_by_tier=orphans,
        pure_tree_placements=pure_tree_placements,
        orphan_row_positions=orphan_row_positions,
        stem_inner_w=stem_inner_w,
    )


def _stem_extension_bottom_y(
    leagues_by_tier: dict[int, list[LeagueData]], layout: StemLayout | None
) -> float:
    if layout is None:
        return _pyramid_bottom_y()
    stem_tiers = sorted(t for t in leagues_by_tier if t >= 7)
    cursor_y = _stem_content_top_y()
    for tier_num in stem_tiers:
        leagues = leagues_by_tier.get(tier_num, [])
        if not leagues:
            continue
        orphan_band = 0.0
        if layout.orphan_row_positions.get(tier_num) and layout.pure_tree_placements.get(tier_num):
            orphan_band = STEM_ORPHAN_ROW_GAP_PX + COUNTIES_ORPHAN_ROW_HEIGHT
        cursor_y += COUNTIES_ROW_HEIGHT + orphan_band + COUNTIES_TIER_GAP
    return cursor_y + STEM_BOTTOM_MARGIN_Y


@dataclass
class StemExtensionLayout:
    stem_bottom_y: float
    parts: list[str]


def _render_stem_extension(
    leagues_by_tier: dict[int, list[LeagueData]],
    season: str,
    stem_bottom_y: float,
    layout: StemLayout | None,
    *,
    crest_href_remap: dict[str, str] | None = None,
    merit_competition: str | None = None,
    merit_local_offset: int = 0,
) -> StemExtensionLayout:
    stem_tiers = sorted(t for t in leagues_by_tier if t >= 7)
    if (
        layout is None
        or stem_bottom_y <= _pyramid_bottom_y() + 0.05
        or not stem_tiers
        or not any(leagues_by_tier.get(t) for t in stem_tiers)
    ):
        return StemExtensionLayout(stem_bottom_y=_pyramid_bottom_y(), parts=[])

    parts: list[str] = []
    content_top = _stem_content_top_y()

    parts.append(_tier67_separator_bar_svg())

    stem_left_x, stem_inner_w = _stem_inner_playfield()
    cursor_y = content_top

    for tier_num in stem_tiers:
        leagues = _sorted_stem_leagues_at_tier(tier_num, leagues_by_tier[tier_num], season)
        if not leagues:
            continue

        band_top = cursor_y

        pure_cells = layout.pure_tree_placements.get(tier_num, [])
        orphan_cells = layout.orphan_row_positions.get(tier_num, [])
        cell_h = COUNTIES_ROW_HEIGHT - 14
        row_top = cursor_y

        if pure_cells:
            for lg, lx, lw in pure_cells:
                bg, title_color = _league_cell_tier_colors(lg, tier_num, "mens")
                parts.append(
                    _render_league_cell(
                        lg,
                        stem_left_x + lx,
                        row_top,
                        lw,
                        cell_h,
                        bg,
                        title_color,
                        season,
                        crest_href_remap=crest_href_remap,
                    )
                )
            cursor_y += COUNTIES_ROW_HEIGHT
            if orphan_cells:
                cursor_y += STEM_ORPHAN_ROW_GAP_PX
                oph_h = COUNTIES_ORPHAN_ROW_HEIGHT - 14
                oph_top = cursor_y
                for lg, lx, lw in orphan_cells:
                    bg, title_color = _league_cell_tier_colors(lg, tier_num, "mens")
                    parts.append(
                        _render_league_cell(
                            lg,
                            stem_left_x + lx,
                            oph_top,
                            lw,
                            oph_h,
                            bg,
                            title_color,
                            season,
                            crest_href_remap=crest_href_remap,
                        )
                    )
                cursor_y += COUNTIES_ORPHAN_ROW_HEIGHT
        elif orphan_cells:
            for lg, lx, lw in orphan_cells:
                bg, title_color = _league_cell_tier_colors(lg, tier_num, "mens")
                parts.append(
                    _render_league_cell(
                        lg,
                        stem_left_x + lx,
                        row_top,
                        lw,
                        cell_h,
                        bg,
                        title_color,
                        season,
                        crest_href_remap=crest_href_remap,
                    )
                )
            cursor_y += COUNTIES_ROW_HEIGHT

        band_bottom = cursor_y
        tier_team_sum = sum(lg.team_count for lg in leagues)
        parts.append(
            _tier_margin_label_svg(
                tier_num,
                season,
                band_top,
                band_bottom,
                rotate_deg=COUNTIES_MARGIN_TIER_LABEL_ROTATE_DEG,
                league_count=len(leagues),
                total_teams=tier_team_sum,
                merit_competition=merit_competition,
                merit_local_offset=merit_local_offset,
            )
        )

        cursor_y += COUNTIES_TIER_GAP

    return StemExtensionLayout(stem_bottom_y=stem_bottom_y, parts=parts)


# ---------------------------------------------------------------------------
# Top-level SVG assembly
# ---------------------------------------------------------------------------


def _format_merit_pyramid_feeder_note(
    merit_parent_overrides_visible: StemParentOverrides | None,
) -> str:
    """Subtitle line naming men's pyramid feeders for merit visible band ``1`` (apex row)."""
    if not merit_parent_overrides_visible:
        return ""
    bits: list[str] = []
    for tier_vis, child_nm in sorted(
        merit_parent_overrides_visible.keys(), key=lambda k: (k[0], k[1])
    ):
        if tier_vis != 1:
            continue
        pspec = merit_parent_overrides_visible[(tier_vis, child_nm)]
        if not pspec:
            continue
        pname = pspec[0] if len(pspec) == 1 else " / ".join(pspec)
        bits.append(f"{child_nm}: {pname}")
    if not bits:
        return ""
    merged = "  ·  ".join(bits)
    return _shorten(f"Feeds national pyramid — {merged}", 220)


def render_pyramid_svg(
    season: str,
    leagues: list[LeagueData],
    *,
    gender: Gender = DEFAULT_GENDER,
    parent_overrides: StemParentOverrides | None = None,
    womens_parent_overrides: StemParentOverrides | None = None,
    stem_slot_strips: tuple[StemSlotStrip, ...] = (),
    transparent_white_crest_backgrounds: bool = False,
    crest_transparency_workers: int = 12,
    merit_competition: str | None = None,
    merit_local_offset: int = 0,
    mens_merge_merit_leagues: bool = False,
) -> str:
    """Render the full pyramid: tiers 1–6 taper plus integrated Counties stem (tier 7–11).

    ``parent_overrides`` maps ``(tier, child league_name) -> tuple of parent league names``.
    An empty tuple marks explicit unlink (JSON ``"-"``). One legacy string loads as a
    one-element tuple.     Multiple parents (JSON array) yield **one** stem cell spanning the
    horizontal union of those parents' bands at tier ``N−1``. Optional
    ``stem_slot_strips`` in the same JSON file then re-grid those rows to explicit
    relative widths within that union (e.g. upper 1.5+1.5 vs lower 1+1+1); the CLI reloads
    those strips from disk immediately before rendering.

    For ``gender == "womens"``, ``womens_parent_overrides`` carries optional ``(child_band,
    league_name)`` keys from the JSON ``women`` section for bands ``2``–``4`` only, layered on
    geographic prefix inference for the Premiership→Championship→NC1 taper.

    For merit mode, pass ``merit_competition`` (the geocoded directory name) and
    ``merit_local_offset`` (returned by :func:`load_merit_pyramid_leagues`). The men's tier
    1–6 weighted nesting helpers are skipped (merit comps don't follow NL2/Regional naming);
    bands fall back to equal-width alphabetical order, and the stem path renders local
    tier 7+ leagues exactly as for the men's pyramid. ``parent_overrides`` for merit are
    keyed by **visible band** (after offset translation by the caller).

    When ``mens_merge_merit_leagues`` is True (men's national diagram only), tier bands 1–6 use
    equal-width alphabetical layout instead of NL2/Regional/Counties proportional nesting so merit
    leagues at absolute tiers 1–6 can appear beside pyramid leagues. The Counties stem still
    builds from the combined tier map (tiers 7+); ``merit_competition`` must remain ``None``.

    When ``transparent_white_crest_backgrounds`` is True, unique RFU ``image_url`` values are
    optionally remapped to inlined transparent-background PNG data URIs (see
    :func:`build_crest_white_corner_transparent_href_map`).
    """
    is_merit = merit_competition is not None

    leagues_by_tier: dict[int, list[LeagueData]] = {}
    for lg in leagues:
        leagues_by_tier.setdefault(lg.tier_num, []).append(lg)

    stem_forest_prebuilt: tuple[list[StemTreeNode], dict[int, list[StemTreeNode]]] | None = None
    if gender == "mens" or is_merit:
        stem_tiers_chk = sorted(t for t in leagues_by_tier if t >= 7)
        if stem_tiers_chk and any(leagues_by_tier.get(t) for t in stem_tiers_chk):
            stem_forest_prebuilt = _build_stem_forest(
                leagues_by_tier,
                season,
                parent_overrides=parent_overrides,
                log_unlinked=parent_overrides is None,
                merit_competition=merit_competition if is_merit else None,
            )

    canvas_horizontal_weight = _canvas_horizontal_weight(leagues_by_tier, stem_forest_prebuilt)
    canvas_w = _compute_canvas_width_px(canvas_horizontal_weight)

    with _canvas_width_scope(float(canvas_w)):
        nested_layout: NestedTier56Layout | None
        stem_layout: StemLayout | None
        womens_nested_layout: WomensNestedLayout | None = None
        if gender == "mens" and not is_merit and mens_merge_merit_leagues:
            slots = {}
            nested_layout = None
            logger.info(
                "Men's pyramid + merit: tiers 1–6 use equal-width bands (merged merit rows)."
            )
            log_stem_orphans = parent_overrides is None
            stem_layout = _stem_build_layout(
                leagues_by_tier,
                season,
                parent_overrides=parent_overrides,
                stem_slot_strips=stem_slot_strips,
                log_stem_orphans=log_stem_orphans,
                merit_competition=None,
                stem_forest=stem_forest_prebuilt,
            )
            stem_bottom_y = _stem_extension_bottom_y(leagues_by_tier, stem_layout)
        elif gender == "mens" and not is_merit:
            leaf_order = order_pyramid_leaves(leagues_by_tier, parent_overrides=parent_overrides)
            slots = compute_league_slots(
                leagues_by_tier, leaf_order, parent_overrides=parent_overrides
            )
            nested_layout = compute_nested_tier56_layout(
                leagues_by_tier, slots, parent_overrides=parent_overrides
            )

            log_stem_orphans = parent_overrides is None
            stem_layout = _stem_build_layout(
                leagues_by_tier,
                season,
                parent_overrides=parent_overrides,
                stem_slot_strips=stem_slot_strips,
                log_stem_orphans=log_stem_orphans,
                merit_competition=None,
                stem_forest=stem_forest_prebuilt,
            )
            stem_bottom_y = _stem_extension_bottom_y(leagues_by_tier, stem_layout)
        elif is_merit:
            # Merit pyramid: equal-width tier bands 1–6 (alphabetical), plus the stem layout
            # (men's-style geographic nesting) for any local tier 7+ leagues. The men's NL2 /
            # Regional ordering helpers don't apply here.
            slots = {}
            nested_layout = None
            log_stem_orphans = parent_overrides is None
            stem_layout = _stem_build_layout(
                leagues_by_tier,
                season,
                parent_overrides=parent_overrides,
                stem_slot_strips=stem_slot_strips,
                log_stem_orphans=log_stem_orphans,
                merit_competition=merit_competition,
                stem_forest=stem_forest_prebuilt,
            )
            stem_bottom_y = _stem_extension_bottom_y(leagues_by_tier, stem_layout)
        else:
            # Women's pyramid: taper bands 1–4 via prefixes + optional ``women`` section;
            # NC2/NC3 are equal-width rows. No stem.
            slots = {}
            nested_layout = None
            stem_layout = None
            stem_bottom_y = _pyramid_bottom_y()
            womens_nested_layout = compute_womens_nested_layout(
                leagues_by_tier, womens_parent_overrides
            )
            if womens_nested_layout is None:
                logger.info("Women's pyramid: feeder nesting unavailable — equal-width tier bands.")

        crest_href_remap: dict[str, str] | None = None
        if transparent_white_crest_backgrounds:
            crest_href_remap = build_crest_white_corner_transparent_href_map(
                leagues,
                max_workers=max(1, min(int(crest_transparency_workers), 32)),
            )
            if not crest_href_remap:
                crest_href_remap = None

        parts: list[str] = []
        parts.append(_render_pyramid_outline(stem_bottom_y))
        for tier_num in range(1, 7):
            parts.append(
                _render_pyramid_band(
                    tier_num,
                    leagues_by_tier.get(tier_num, []),
                    slots,
                    season,
                    nested=nested_layout,
                    womens_nested=womens_nested_layout,
                    crest_href_remap=crest_href_remap,
                    gender=gender,
                    merit_competition=merit_competition,
                    merit_local_offset=merit_local_offset,
                )
            )

        if (gender == "mens" and not is_merit) or is_merit:
            # No tier 7+ data → ``_stem_build_layout`` returns None; stem render is a no-op.
            stem = _render_stem_extension(
                leagues_by_tier,
                season,
                stem_bottom_y,
                stem_layout,
                crest_href_remap=crest_href_remap,
                merit_competition=merit_competition,
                merit_local_offset=merit_local_offset,
            )
            parts.extend(stem.parts)

        image_height = int(stem_bottom_y + PAGE_MARGIN_BOTTOM)

        title_y = PAGE_MARGIN_TOP + TITLE_STRIP_HEIGHT / 2 - 14
        subtitle_y = PAGE_MARGIN_TOP + TITLE_STRIP_HEIGHT / 2 + 18
        page_bg = PAGE_BG_WOMENS if gender == "womens" else PAGE_BG
        subtitle_fill = SUBTITLE_FILL_WOMENS if gender == "womens" else SUBTITLE_FILL_MENS
        apex_feed_line = ""
        if is_merit:
            comp_display = (merit_competition or "").replace("_", " ")
            main_title = f"{comp_display.upper()} MERIT PYRAMID".strip()
            subtitle_text = f"{comp_display}, {short_season(season)}"
            apex_feed_line = _format_merit_pyramid_feeder_note(parent_overrides)
        else:
            main_title = "ENGLISH RUGBY PYRAMID"
            if gender == "womens":
                subtitle_text = f"Women's leagues, {short_season(season)}"
            elif mens_merge_merit_leagues:
                subtitle_text = f"Men's pyramid + merit leagues, {short_season(season)}"
            else:
                subtitle_text = f"Men's leagues, {short_season(season)}"
        cx_title = canvas_w / 2
        title_parts = [
            _svg_text(
                main_title,
                cx_title,
                title_y,
                fill=TITLE_TEXT,
                size=34.0,
                weight="800",
                anchor="middle",
            ),
            _svg_text(
                subtitle_text,
                cx_title,
                subtitle_y,
                fill=subtitle_fill,
                size=16.0,
                weight="500",
                anchor="middle",
            ),
        ]
        if apex_feed_line:
            title_parts.append(
                _svg_text(
                    apex_feed_line,
                    cx_title,
                    subtitle_y + 36.0,
                    fill=subtitle_fill,
                    size=13.5,
                    weight="500",
                    anchor="middle",
                )
            )

        body = "\n".join(title_parts + parts)

        svg = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'viewBox="0 0 {canvas_w} {image_height}" '
            f'width="{canvas_w}" height="{image_height}">\n'
            f'<rect x="0" y="0" width="{canvas_w}" height="{image_height}" fill="{page_bg}"/>\n'
            f"{body}\n"
            f"</svg>\n"
        )
        return svg


# ---------------------------------------------------------------------------
# Interactive parent linker: pyramid tiers 5–6 + Counties stem (TTY only)
# ---------------------------------------------------------------------------
#
# Each interactive session starts from the saved tier-mapping file (when present) so
# partial work accumulates across runs. Choices are merged into
# ``data/rugby/tier_mappings/<season>.json`` and applied on subsequent non-interactive
# renders unless ``--ignore-saved-stem-parent-overrides`` is passed.


def stem_interactive_parent_overrides(
    leagues_by_tier: dict[int, list[LeagueData]],
    season: str,
    *,
    seed_overrides: StemParentOverrides | None = None,
) -> StemParentOverrides:
    """TTY-only: prompt for missing tier 5→4 and tier 6→5 mappings, then Counties stem orphans.

    Returns ``(child tier, child league_name) -> (parent league_name, ...)`` for links; an empty
    tuple marks explicitly unlinked children so heuristics stay off. ``s`` / ``stop`` exits early.

    ``seed_overrides`` (e.g. from ``data/rugby/tier_mappings/<season>.json``) is copied
    and updated so multi-session linking does not drop earlier choices.
    """
    if not sys.stdin.isatty():
        raise RuntimeError(
            "--interactive-stem-orphans requires an interactive terminal (stdin is not a TTY)."
        )

    overrides: StemParentOverrides = dict(seed_overrides) if seed_overrides else {}
    print(
        "\nInteractive parent linker (men's pyramid)\n"
        "  — First: tier 5 (Regional 1) → tier 4 (NL2), then tier 6 (Regional 2) → tier 5\n"
        "  — Then: Counties stem orphans (tier 8+)\n"
        "  blank or 0 — leave this league without a parent mapping (explicit unlinked)\n"
        "  number — single parent from the list below\n"
        "  comma-separated numbers — multiple parents (e.g. 1,2 stretches one stem cell)\n"
        "  s / stop — stop prompting (remaining work unchanged)\n"
    )

    while True:
        nxt = _interactive_next_missing_pyramid_feeder_prompt(leagues_by_tier, overrides)
        if nxt is None:
            nxt = _stem_next_orphan_for_prompt(leagues_by_tier, season, overrides)
        if nxt is None:
            n_linked_children = sum(1 for v in overrides.values() if v)
            nexplicit = sum(1 for v in overrides.values() if not v)
            logger.info(
                "Interactive parent linker finished (%d child league(s) linked; %d explicitly unlinked).",
                n_linked_children,
                nexplicit,
            )
            return overrides

        tier, child, candidates = nxt
        choice = _stem_prompt_parent_pick(tier, child, candidates)
        if choice is None:
            logger.info(
                "Interactive parent linker stopped early (%d choice(s) recorded).", len(overrides)
            )
            return overrides

        overrides[(child.tier_num, child.league_name)] = choice


def womens_interactive_feeder_overrides(
    leagues_by_tier: dict[int, list[LeagueData]],
    *,
    seed_overrides: StemParentOverrides | None = None,
) -> StemParentOverrides:
    """TTY-only: prompt for missing women's feeder links for visual bands 2→1, 3→2, 4→3.

    Writes choices into the same flat shape as :func:`womens_parent_overrides_load`
    (keys ``(band, child league_name)`` with ``2 ≤ band ≤ 4``). Blank / ``0`` sets explicit
    unlinked (``"-"`` on save); ``s`` / ``stop`` exits early.

    ``seed_overrides`` is merged so partial JSON from prior runs is preserved until each
    child is confirmed or skipped.
    """
    if not sys.stdin.isatty():
        raise RuntimeError(
            "--interactive-stem-orphans requires an interactive terminal (stdin is not a TTY)."
        )

    overrides: StemParentOverrides = dict(seed_overrides) if seed_overrides else {}
    print(
        "\nInteractive women's feeder linker (bands 2–4)\n"
        "  Band 2 — Championship → Premiership\n"
        "  Band 3 — Championship → Championship (feeder row above)\n"
        "  Band 4 — National Challenge 1 → Championship\n"
        "  blank or 0 — explicit unlinked for this league\n"
        "  number — single parent from the list below\n"
        "  comma-separated numbers — multiple parents\n"
        "  s / stop — stop prompting (remaining bands unchanged)\n"
    )

    while True:
        nxt = _interactive_next_missing_womens_feeder_prompt(leagues_by_tier, overrides)
        if nxt is None:
            wo = {k: v for k, v in overrides.items() if isinstance(k[0], int) and 2 <= k[0] <= 4}
            n_linked = sum(1 for v in wo.values() if v)
            n_explicit = sum(1 for v in wo.values() if not v)
            logger.info(
                "Women's interactive feeder linker finished (%d linked; %d explicitly unlinked).",
                n_linked,
                n_explicit,
            )
            return overrides

        band, child, candidates = nxt
        tp = band - 1
        choice = _stem_prompt_parent_pick(
            band,
            child,
            candidates,
            banner=(
                "Women's pyramid feeder — assign parent "
                f"(band {band} child → band {tp}; Premiership → Championship → NC1 taper)"
            ),
        )
        if choice is None:
            wo = {k: v for k, v in overrides.items() if isinstance(k[0], int) and 2 <= k[0] <= 4}
            logger.info(
                "Women's interactive feeder linker stopped early (%d feeder entries recorded).",
                len(wo),
            )
            return overrides

        overrides[(band, child.league_name)] = choice


def _interactive_next_missing_womens_feeder_prompt(
    leagues_by_tier: dict[int, list[LeagueData]],
    overrides: StemParentOverrides,
) -> tuple[int, LeagueData, list[LeagueData]] | None:
    """Next band 2–4 league with no ``(band, league_name)`` entry in ``overrides``."""
    for band in (2, 3, 4):
        candidates = list(leagues_by_tier.get(band - 1, ()))
        candidates.sort(key=lambda lg: lg.league_name)
        for lg in sorted(leagues_by_tier.get(band, ()), key=lambda lg: lg.league_name):
            if (band, lg.league_name) not in overrides:
                return (band, lg, candidates)
    return None


def merit_interactive_feeder_overrides(
    leagues_by_local_tier: dict[int, list[LeagueData]],
    competition: str,
    season: str,
    *,
    seed_overrides_local: StemParentOverrides | None = None,
) -> StemParentOverrides:
    """TTY-only: prompt for missing intra-merit links and apex → men's pyramid feeders.

    Operates on **local** merit tier numbers (JSON / disk convention). Apex rows (minimum local
    tier present) pick parents from the men's pyramid row at tier
    :func:`merit_pyramid_absolute_parent_tier`. Deeper rows pick from merit tier-(N−1).

    Blank / ``0`` records explicit unlink (``"-"``). Comma-separated indices assign multiple parents.
    ``s`` / ``stop`` exits early. ``seed_overrides_local`` seeds prior JSON work.
    """
    if not sys.stdin.isatty():
        raise RuntimeError(
            "--interactive-stem-orphans requires an interactive terminal (stdin is not a TTY)."
        )

    overrides: StemParentOverrides = dict(seed_overrides_local) if seed_overrides_local else {}
    merit_apply_parent_heuristics_local(leagues_by_local_tier, competition, overrides, season)
    comp_display = competition.replace("_", " ")
    tiers_present = sorted(leagues_by_local_tier.keys())
    if not tiers_present:
        logger.info("Merit %r has no local tiers — nothing to prompt.", competition)
        return overrides
    min_tier = tiers_present[0]

    apex_abs_parent = merit_pyramid_absolute_parent_tier(competition, min_tier, season)
    apex_parent_label = mens_current_tier_name(apex_abs_parent, season)
    mens_by_abs = _load_mens_pyramid_leagues_by_tier(season)
    apex_parent_count = len(mens_by_abs.get(apex_abs_parent, ()))

    extra_tier_note = ""
    if len(tiers_present) >= 2:
        extra_tier_note = (
            f"\n  Then intra-merit tiers {min_tier + 1} .. {tiers_present[-1]} "
            "(local child → merit tier-(N−1))."
        )

    print(
        f"\nInteractive parent linker — merit competition {comp_display!r}\n"
        f"  Apex (local tier {min_tier}): attach to men's {apex_parent_label!r} "
        f"(pyramid tier {apex_abs_parent}; {apex_parent_count} candidate league(s))\n"
        f"{extra_tier_note}\n"
        "  blank or 0 — explicit unlinked for this league\n"
        "  number — pick from the numbered list below\n"
        "  comma-separated numbers — multi-parent apex links\n"
        "  s / stop — stop prompting (remaining links unchanged)\n"
    )

    while True:
        nxt = _merit_next_missing_prompt(
            leagues_by_local_tier,
            overrides,
            min_tier,
            competition,
            season,
            mens_by_abs,
        )
        if nxt is None:
            n_linked = sum(1 for v in overrides.values() if v)
            n_explicit = sum(1 for v in overrides.values() if not v)
            logger.info(
                "Merit interactive linker %r finished (%d linked; %d explicitly unlinked).",
                competition,
                n_linked,
                n_explicit,
            )
            return overrides

        local_tier, child, candidates = nxt
        if local_tier == min_tier:
            banner = (
                f"Merit pyramid {comp_display!r} — apex → men's pyramid "
                f"(local tier {local_tier}, target {mens_current_tier_name(apex_abs_parent, season)})"
            )
        else:
            banner = (
                f"Merit pyramid {comp_display!r} — assign parent(s) "
                f"(local tier {local_tier} child → local tier {local_tier - 1})"
            )
        choice = _stem_prompt_parent_pick(
            local_tier,
            child,
            candidates,
            banner=banner,
            prompt_parent_numeric_tier=apex_abs_parent if local_tier == min_tier else None,
        )
        if choice is None:
            logger.info(
                "Merit interactive linker %r stopped early (%d choice(s) recorded).",
                competition,
                len(overrides),
            )
            return overrides

        overrides[(local_tier, child.league_name)] = choice


def _merit_next_missing_prompt(
    leagues_by_local_tier: dict[int, list[LeagueData]],
    overrides: StemParentOverrides,
    min_tier: int,
    competition: str,
    season: str,
    mens_by_pyramid_abs_tier: dict[int, list[LeagueData]],
) -> tuple[int, LeagueData, list[LeagueData]] | None:
    """Next merit league missing overrides: apex → men's pyramid, then intra-merit feeders."""
    feed_abs = merit_pyramid_absolute_parent_tier(competition, min_tier, season)
    pyramid_parents = sorted(
        mens_by_pyramid_abs_tier.get(feed_abs, ()), key=lambda lg: lg.league_name
    )
    if pyramid_parents:
        for lg in sorted(leagues_by_local_tier.get(min_tier, ()), key=lambda x: x.league_name):
            if (min_tier, lg.league_name) not in overrides:
                return (min_tier, lg, pyramid_parents)

    for tier in sorted(leagues_by_local_tier.keys()):
        if tier <= min_tier:
            continue
        candidates = list(leagues_by_local_tier.get(tier - 1, ()))
        candidates.sort(key=lambda lg: lg.league_name)
        for lg in sorted(leagues_by_local_tier.get(tier, ()), key=lambda lg: lg.league_name):
            if (tier, lg.league_name) not in overrides:
                return (tier, lg, candidates)
    return None


def _interactive_next_missing_pyramid_feeder_prompt(
    leagues_by_tier: dict[int, list[LeagueData]],
    overrides: StemParentOverrides,
) -> tuple[int, LeagueData, list[LeagueData]] | None:
    """Next tier 5 or 6 league with no ``(tier, league_name)`` entry in ``overrides``."""
    tier4 = _ordered_tier4_leagues(leagues_by_tier.get(4, []))
    for lg in _alpha_sort_leagues(leagues_by_tier.get(5, [])):
        if (5, lg.league_name) not in overrides:
            return (5, lg, tier4)

    tier5 = _alpha_sort_leagues(leagues_by_tier.get(5, []))
    for lg in _alpha_sort_leagues(leagues_by_tier.get(6, [])):
        if (6, lg.league_name) not in overrides:
            return (6, lg, tier5)

    return None


def _stem_next_orphan_for_prompt(
    leagues_by_tier: dict[int, list[LeagueData]],
    season: str,
    overrides: StemParentOverrides,
) -> tuple[int, LeagueData, list[LeagueData]] | None:
    orphans = _build_stem_forest(
        leagues_by_tier,
        season,
        parent_overrides=overrides,
        log_unlinked=False,
    )[1]
    flat: list[tuple[int, LeagueData, list[LeagueData]]] = []
    for t in sorted(orphans.keys()):
        plist = list(_sorted_stem_leagues_at_tier(t - 1, leagues_by_tier.get(t - 1, []), season))
        for sn in sorted(
            orphans[t], key=lambda n: _stem_sort_key_league_name(n.league.league_name)
        ):
            flat.append((t, sn.league, plist))
    return flat[0] if flat else None


def _stem_prompt_parent_pick(
    tier: int,
    child: LeagueData,
    candidates: list[LeagueData],
    *,
    banner: str | None = None,
    prompt_parent_numeric_tier: int | None = None,
) -> tuple[str, ...] | None:
    """``None`` stops further prompts; ``()`` means explicit unlink; otherwise parent name(s).

    Multi-parent tuples preserve pick order when several indices are given (e.g. ``1,2``).

    Use ``prompt_parent_numeric_tier`` when the parent's band is not ``tier - 1`` (merit apex→men).
    """
    tier_parent_show = (
        tier - 1 if prompt_parent_numeric_tier is None else prompt_parent_numeric_tier
    )
    if banner is not None:
        heading = banner
    elif tier <= 6:
        heading = "Pyramid feeder — assign Regional / NL2 parent"
    else:
        heading = "Counties stem — assign parent"
    print(
        f"\n--- {heading}\n"
        f"    Tier {tier} child: {child.league_name!r}\n"
        f"    Pick pyramid tier-{tier_parent_show} parent(s):\n",
        flush=True,
    )
    upper = len(candidates)
    if upper == 0:
        print(
            "    (no leagues loaded at that tier)\n",
            flush=True,
        )
        raw = input("[Enter=leave unlinked, s=stop prompting]> ").strip().lower()
        if raw in {"s", "stop"}:
            return None
        return ()

    for i, p in enumerate(candidates, start=1):
        display = _strip_league_title_sponsors(p.league_name)
        print(f"  [{i:2d}] {display}", flush=True)
    print("  [ 0] Leave unlinked", flush=True)
    print("       comma-separated picks for multiple parents (e.g. 1,2).", flush=True)
    print("       s / stop — stop prompting for other orphans.\n", flush=True)

    while True:
        raw = input(f"Choose 0-{upper} or comma-separated (blank=0, s=stop)> ").strip()
        lowered = raw.lower()
        if lowered in {"s", "stop"}:
            return None
        if raw == "" or raw == "0":
            return ()
        chunks = [c.strip() for c in raw.split(",")]
        if any(c == "" for c in chunks):
            print("  Invalid — empty chunk in list (use «1,2» not «1,,2»).")
            continue
        indices_raw: list[int] = []
        bad = False
        for c in chunks:
            if not c.isdigit():
                print(f"  Invalid — every entry must be a whole number in 0-{upper}.")
                bad = True
                break
            indices_raw.append(int(c))
        if bad:
            continue
        nonzero = [i for i in indices_raw if i != 0]
        if 0 in indices_raw and nonzero:
            print("  Invalid — cannot mix 0 (unlinked) with other selections.")
            continue
        if not nonzero:
            return ()

        picks: list[str] = []
        seen_slot: set[int] = set()
        for idx in indices_raw:
            if not (1 <= idx <= upper):
                print(f"  Invalid — each pick must be 1-{upper} (or bare 0 to unlink).")
                bad = True
                break
            if idx in seen_slot:
                continue
            seen_slot.add(idx)
            picks.append(candidates[idx - 1].league_name)
        if bad:
            continue
        return tuple(picks)


# ---------------------------------------------------------------------------
# Tier-mapping JSON I/O (data/rugby/tier_mappings/<season>.json)
# ---------------------------------------------------------------------------
#
# Schema v2 (read by :func:`stem_parent_json_load`):
#
#   {
#     "schema_version": 2,
#     "season": "2025-2026",
#     "men": {
#       "5": {"<child>": "<parent>" | ["<p1>", "<p2>"] | "-"},
#       "6": {...},
#       "7": {...}, ...
#     },
#     "women": {
#       "2": {"<Women's Championship …>": "<Women's Premiership …>"},
#       "3": {"<Women's Championship …>": "<Women's Championship …>"},
#       "4": {"<Women's NC 1 …>": "<Women's Championship …>" | ["<p1>", "<p2>"]}
#     },
#     "<MeritCompetition>": {
#       "1": {"<merit apex>": "<men's pyramid parent at offset tier>" | [...]},
#       "2": {"<merit child>": "<merit parent>" | [...] | "-"},
#       "3": {...}, ...
#     },
#     "stem_slot_strips": [
#       {"bands": [{"tier": N, "leagues": [...], "weights": [...]}, ...]}
#     ]
#   }
#
# - ``men`` keys ``"5"`` and ``"6"`` drive Regional 1 → NL2 and Regional 2 → Regional 1
#   nesting; ``"7"`` and below drive the Counties stem.
# - ``women`` keys are feeder **child visual bands** ``2``–``4`` only (bands ``5``–``6``
#   are equal-width rows with no feeder geometry). Omit the object for prefix-only
#   nesting on those three transitions.
# - Per-merit sections are keyed by the merit competition's geocoded directory name
#   (e.g. ``"Hampshire"``, ``"East_Midlands"`` — matches ``rugby.tiers.COMPETITION_OFFSETS``).
#   Keys are **local** merit tier numbers (1-based within the competition). The shallowest tier
#   present (apex) maps onto the men's pyramid feeder row at
#   ``local_tier + get_competition_offset(comp, season) - 1``; deeper tiers map onto merit tier
#   ``local - 1`` as before. Same string / array / ``"-"`` convention as ``men`` / ``women``.
# - String value = single parent. JSON array stretches one cell across the horizontal union at
#   the parent tier. ``"-"`` marks explicit unlink.
# - Saved entries are applied automatically unless
#   ``--ignore-saved-stem-parent-overrides`` (under ``--womens`` it suppresses
#   ``women`` only; under ``--merit <Comp>`` it suppresses that comp's section only).
# - ``stem_slot_strips`` reapportions stem cells within the horizontal union of
#   leagues listed on each band: integer ``tier`` (same as stem rows), ``leagues``
#   (ordered names), parallel ``weights`` (positive floats). Several bands in one
#   strip share one bbox so tier *N* and tier *N+1* columns can align (e.g. upper
#   1.5+1.5 over lower 1+1+1). Loaded in a fresh read immediately before rendering so
#   interactive saves and hand-edited strips apply on the same run. Skip with
#   ``--ignore-stem-slot-strips``.
# - Cross-season merge (:func:`stem_parent_overrides_merge_cross_season`,
#   :func:`womens_parent_overrides_merge_cross_season`,
#   :func:`merit_parent_overrides_merge_cross_season`) folds in parent links from
#   other seasons' tier-mapping files when both leagues resolve uniquely in the current
#   season; newly inferred links are written back so later runs do not repeat the inference.


def stem_parent_overrides_store_path(season: str) -> Path:
    """Path to Counties stem linker JSON for ``season``.

    Stored as ``tier_mappings/<season>.json`` under :data:`~rugby.DATA_DIR`.
    """
    return TIER_MAPPINGS_DIR / f"{season}.json"


def _parse_stem_slot_strips(payload: object) -> tuple[StemSlotStrip, ...]:
    """Parse optional ``stem_slot_strips`` from ``tier_mappings/<season>.json``."""
    if not isinstance(payload, dict):
        return ()
    raw = payload.get("stem_slot_strips")
    if not isinstance(raw, list) or not raw:
        return ()
    strips: list[StemSlotStrip] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        bands_raw = item.get("bands")
        if not isinstance(bands_raw, list) or not bands_raw:
            continue
        bands_out: list[StemSlotBand] = []
        bad = False
        for band_obj in bands_raw:
            if not isinstance(band_obj, dict):
                bad = True
                break
            tier_v = band_obj.get("tier")
            if isinstance(tier_v, bool) or not isinstance(tier_v, int):
                bad = True
                break
            leagues_v = band_obj.get("leagues")
            weights_v = band_obj.get("weights")
            if (
                not isinstance(leagues_v, list)
                or not leagues_v
                or not isinstance(weights_v, list)
                or len(weights_v) != len(leagues_v)
            ):
                bad = True
                break
            if not all(isinstance(x, str) and x.strip() for x in leagues_v):
                bad = True
                break
            ws: list[float] = []
            for w in weights_v:
                if isinstance(w, bool) or not isinstance(w, int | float):
                    bad = True
                    break
                wf = float(w)
                if wf <= 0:
                    bad = True
                    break
                ws.append(wf)
            if bad:
                break
            bands_out.append(
                StemSlotBand(
                    tier=int(tier_v),
                    leagues=tuple(str(x).strip() for x in leagues_v),
                    weights=tuple(ws),
                )
            )
        if bad:
            logger.warning("stem_slot_strips: skipped invalid strip entry")
            continue
        if bands_out:
            strips.append(StemSlotStrip(bands=tuple(bands_out)))
    return tuple(strips)


def _stem_parent_override_read_payload(season: str) -> dict | None:
    """Return parsed stem JSON object for ``season``, or ``None`` if missing/unreadable."""
    path = stem_parent_overrides_store_path(season)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not parse stem parent overrides %s (%s)", path, exc)
        return None
    if not isinstance(payload, dict):
        logger.warning("Stem override file %s is not a JSON object", path)
        return None
    return payload


def stem_slot_strips_load(season: str) -> tuple[StemSlotStrip, ...]:
    """Load ``stem_slot_strips`` from disk (always re-reads the file path).

    Call this **after** :func:`stem_parent_overrides_save` so the SVG matches what was just
    written, including preserved ``stem_slot_strips`` and any edits made while interactive ran.
    """
    payload = _stem_parent_override_read_payload(season)
    if payload is None:
        return ()
    return _parse_stem_slot_strips(payload)


def stem_parent_json_load(
    season: str,
) -> tuple[StemParentOverrides | None, tuple[StemSlotStrip, ...]]:
    """Load the ``men`` section and optional ``stem_slot_strips`` for ``season``."""
    path = stem_parent_overrides_store_path(season)
    payload = _stem_parent_override_read_payload(season)
    if payload is None:
        return None, ()

    strips = _parse_stem_slot_strips(payload)
    file_season = payload.get("season") if isinstance(payload, dict) else None
    flat = _stem_payload_flat(payload)
    if flat is None:
        logger.warning("Invalid `men` structure in %s", path)
        return None, strips

    stored_schema = payload.get("schema_version") if isinstance(payload, dict) else None
    if stored_schema is not None:
        try:
            stored_sv = int(stored_schema)
        except (TypeError, ValueError):
            stored_sv = -1
        if stored_sv != STEM_PARENT_OVERRIDE_SCHEMA_VERSION:
            logger.warning(
                "Stem override file %s has schema_version %s (expected %s); reading anyway.",
                path,
                stored_schema,
                STEM_PARENT_OVERRIDE_SCHEMA_VERSION,
            )

    if file_season != season:
        logger.warning(
            "Stem override file claims season %r but CLI season is %r — applying anyway.",
            file_season,
            season,
        )

    return (flat if flat else None, strips)


def womens_parent_overrides_from_payload(payload: object) -> StemParentOverrides | None:
    """Parse the ``women`` section from tier-mapping JSON (women's bands 2–4 feeders)."""
    if not isinstance(payload, dict):
        return None
    nested = payload.get("women")
    if nested is None:
        return None
    flat = stem_parent_overrides_flatten_nested(nested)
    if flat is None:
        return None
    filtered = {(t, n): v for (t, n), v in flat.items() if 2 <= t <= 4}
    return filtered if filtered else None


def merit_parent_overrides_from_payload(
    payload: object,
    competition: str,
) -> StemParentOverrides | None:
    """Parse a per-merit ``<competition>`` section from tier-mapping JSON.

    Returns ``StemParentOverrides`` keyed by ``(local_tier, child_name)`` for the named
    merit competition, or ``None`` when the section is absent or malformed. Rows from local tier
    ``1`` onward are kept — tier ``1`` (or whichever is the apex for this competition after
    data loads) may map to leagues on the **men's pyramid** feeder row immediately above this
    competition's shallowest merit band.
    """
    if not isinstance(payload, dict):
        return None
    nested = payload.get(competition)
    if nested is None:
        return None
    flat = stem_parent_overrides_flatten_nested(nested)
    if flat is None:
        return None
    filtered = {(t, n): v for (t, n), v in flat.items() if t >= 1}
    return filtered if filtered else None


def womens_parent_overrides_load(season: str) -> StemParentOverrides | None:
    """Read women's feeder overrides for :func:`compute_womens_nested_layout`, if any."""
    payload = _stem_parent_override_read_payload(season)
    if payload is None:
        return None
    return womens_parent_overrides_from_payload(payload)


def merit_parent_overrides_load(
    season: str,
    competition: str,
) -> StemParentOverrides | None:
    """Read merit-pyramid parent overrides for ``competition`` in ``season``, if any."""
    payload = _stem_parent_override_read_payload(season)
    if payload is None:
        return None
    return merit_parent_overrides_from_payload(payload, competition)


def _stem_override_parents_from_json_value(pn: object) -> tuple[str, ...] | None:
    """Parse JSON override value: string, list of strings, or invalid."""
    if isinstance(pn, str):
        s = pn.strip()
        if not s:
            return None
        low = s.lower()
        if low in {"-", "skip", "none"}:
            return ()
        return (s,)
    if isinstance(pn, list):
        acc: list[str] = []
        for item in pn:
            if not isinstance(item, str):
                return None
            t = item.strip()
            if not t:
                continue
            low = t.lower()
            if low in {"-", "skip", "none"}:
                return ()
            acc.append(t)
        return tuple(acc)
    return None


def stem_parent_overrides_flatten_nested(
    section: object,
) -> StemParentOverrides | None:
    """Translate ``{\"8\": {\"child\": \"parent\" | [\"p1\",\"p2\"]}}`` into keyed tuples."""
    if not isinstance(section, dict):
        return None
    out: StemParentOverrides = {}
    for tier_raw, cmap in section.items():
        if not isinstance(cmap, dict):
            return None
        try:
            tier_num = int(tier_raw)
        except (TypeError, ValueError):
            return None
        for cn, pn in cmap.items():
            if not isinstance(cn, str):
                return None
            tup = _stem_override_parents_from_json_value(pn)
            if tup is None:
                return None
            out[(tier_num, cn)] = tup
    return out


def _stem_payload_flat(payload: object) -> StemParentOverrides | None:
    if not isinstance(payload, dict):
        return None
    nested = payload.get("men")
    return stem_parent_overrides_flatten_nested(nested)


def stem_parent_overrides_merge_cross_season(
    season: str,
    leagues_by_tier: dict[int, list[LeagueData]],
    base: StemParentOverrides,
) -> StemParentOverrides:
    """Augment ``base`` with parent links inferred from other seasons' tier mapping files.

    Entries already present in ``base`` are never replaced. Other files are tried in order
    of calendar proximity to ``season``. Each foreign ``(child tier, child name) → parent``
    pair is copied when both leagues resolve uniquely in the current season via
    :func:`_stem_identity_tail_key`.
    """
    merged = dict(base)
    current_file = stem_parent_overrides_store_path(season).resolve()
    bundles: list[tuple[int, str, StemParentOverrides, Path]] = []

    tier_map_paths = sorted(TIER_MAPPINGS_DIR.glob("*.json")) if TIER_MAPPINGS_DIR.is_dir() else []
    for path in tier_map_paths:
        m = _TIER_MAPPING_FILENAME_RE.match(path.name)
        if not m:
            continue
        if path.resolve() == current_file:
            continue
        fn_season = m.group("season")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        flat = _stem_payload_flat(payload)
        if not flat:
            continue
        file_season = payload.get("season") if isinstance(payload, dict) else None
        if isinstance(file_season, str) and _SEASON_RE.fullmatch(file_season):
            eff_foreign = file_season
        elif fn_season:
            eff_foreign = fn_season
        else:
            continue
        gap = abs(_season_start_year(eff_foreign) - _season_start_year(season))
        bundles.append((gap, eff_foreign, flat, path))

    bundles.sort(key=lambda b: (b[0], str(b[3])))

    for gap, eff_foreign, flat, path in bundles:
        for (t_child, child_foreign), parent_specs_foreign in flat.items():
            children_tier = leagues_by_tier.get(t_child, [])
            parents_tier = leagues_by_tier.get(t_child - 1, [])
            child_here = _stem_resolve_league_identity(
                children_tier,
                child_foreign,
                t_child,
                season,
                eff_foreign,
            )
            if child_here is None:
                continue
            key_new = (t_child, child_here.league_name)
            if key_new in merged:
                continue
            resolved_names: list[str] = []
            seen_rn: set[str] = set()
            for pf in parent_specs_foreign:
                parent_here = _stem_resolve_league_identity(
                    parents_tier,
                    pf,
                    t_child - 1,
                    season,
                    eff_foreign,
                )
                if parent_here is None:
                    continue
                nm = parent_here.league_name
                if nm not in seen_rn:
                    seen_rn.add(nm)
                    resolved_names.append(nm)
            if not resolved_names:
                continue
            merged[key_new] = tuple(resolved_names)
            logger.info(
                "Stem parent link(s) inferred from %s (Δseason-years=%d): %r → %r",
                path.name,
                gap,
                child_here.league_name,
                merged[key_new],
            )

    return merged


def womens_parent_overrides_merge_cross_season(
    season: str,
    leagues_by_tier: dict[int, list[LeagueData]],
    base: StemParentOverrides,
) -> StemParentOverrides:
    """Augment ``base`` with ``women`` section entries from other seasons' JSON files.

    Same calendar-distance ordering as :func:`stem_parent_overrides_merge_cross_season`. Only
    visual feeder bands ``2``–``4`` are considered; child/parent names are mapped into the
    current season via :func:`_womens_feeder_resolve_league_identity`. Entries already in
    ``base`` are never replaced.
    """
    merged = dict(base)
    current_file = stem_parent_overrides_store_path(season).resolve()
    bundles: list[tuple[int, str, StemParentOverrides, Path]] = []

    tier_map_paths = sorted(TIER_MAPPINGS_DIR.glob("*.json")) if TIER_MAPPINGS_DIR.is_dir() else []
    for path in tier_map_paths:
        m = _TIER_MAPPING_FILENAME_RE.match(path.name)
        if not m:
            continue
        if path.resolve() == current_file:
            continue
        fn_season = m.group("season")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        flat_w = womens_parent_overrides_from_payload(payload)
        if not flat_w:
            continue
        file_season = payload.get("season") if isinstance(payload, dict) else None
        if isinstance(file_season, str) and _SEASON_RE.fullmatch(file_season):
            eff_foreign = file_season
        elif fn_season:
            eff_foreign = fn_season
        else:
            continue
        gap = abs(_season_start_year(eff_foreign) - _season_start_year(season))
        bundles.append((gap, eff_foreign, flat_w, path))

    bundles.sort(key=lambda b: (b[0], str(b[3])))

    for gap, eff_foreign, flat, path in bundles:
        for (t_child, child_foreign), parent_specs_foreign in flat.items():
            if not isinstance(t_child, int) or not (2 <= t_child <= 4):
                continue
            children_tier = leagues_by_tier.get(t_child, [])
            parents_tier = leagues_by_tier.get(t_child - 1, [])
            child_here = _womens_feeder_resolve_league_identity(
                children_tier,
                child_foreign,
                t_child,
                season,
                eff_foreign,
            )
            if child_here is None:
                continue
            key_new = (t_child, child_here.league_name)
            if key_new in merged:
                continue
            resolved_names: list[str] = []
            seen_rn: set[str] = set()
            for pf in parent_specs_foreign:
                parent_here = _womens_feeder_resolve_league_identity(
                    parents_tier,
                    pf,
                    t_child - 1,
                    season,
                    eff_foreign,
                )
                if parent_here is None:
                    continue
                nm = parent_here.league_name
                if nm not in seen_rn:
                    seen_rn.add(nm)
                    resolved_names.append(nm)
            if not resolved_names:
                continue
            merged[key_new] = tuple(resolved_names)
            logger.info(
                "Women's feeder link(s) inferred from %s (Δseason-years=%d): %r → %r",
                path.name,
                gap,
                child_here.league_name,
                merged[key_new],
            )

    return merged


def merit_parent_overrides_merge_cross_season(
    season: str,
    competition: str,
    leagues_by_local_tier: dict[int, list[LeagueData]],
    base: StemParentOverrides,
) -> StemParentOverrides:
    """Augment ``base`` with per-merit section entries from other seasons' JSON files.

    Same calendar-distance ordering as :func:`stem_parent_overrides_merge_cross_season`.
    Keys are **local** merit tier numbers (matches the on-disk JSON convention) so seasons
    with different comp ranges (e.g. Hampshire 4–6 one year, 5–6 the next) still line up.
    Child apex rows resolve parent names against **men's** pyramid leagues at tier
    :func:`merit_pyramid_absolute_parent_tier`; intra-merit rows resolve tier-(local N−1) as before.
    """
    merged = dict(base)
    current_file = stem_parent_overrides_store_path(season).resolve()
    bundles: list[tuple[int, str, StemParentOverrides, Path]] = []

    tier_map_paths = sorted(TIER_MAPPINGS_DIR.glob("*.json")) if TIER_MAPPINGS_DIR.is_dir() else []
    for path in tier_map_paths:
        m = _TIER_MAPPING_FILENAME_RE.match(path.name)
        if not m:
            continue
        if path.resolve() == current_file:
            continue
        fn_season = m.group("season")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        flat_m = merit_parent_overrides_from_payload(payload, competition)
        if not flat_m:
            continue
        file_season = payload.get("season") if isinstance(payload, dict) else None
        if isinstance(file_season, str) and _SEASON_RE.fullmatch(file_season):
            eff_foreign = file_season
        elif fn_season:
            eff_foreign = fn_season
        else:
            continue
        gap = abs(_season_start_year(eff_foreign) - _season_start_year(season))
        bundles.append((gap, eff_foreign, flat_m, path))

    bundles.sort(key=lambda b: (b[0], str(b[3])))

    mens_by_abs = _load_mens_pyramid_leagues_by_tier(season)
    apex_local_here = min(leagues_by_local_tier) if leagues_by_local_tier else None

    for gap, eff_foreign, flat, path in bundles:
        for (t_child, child_foreign), parent_specs_foreign in flat.items():
            children_tier = leagues_by_local_tier.get(t_child, [])
            apex_row = apex_local_here is not None and t_child == apex_local_here
            if apex_row:
                parent_feed_pyramid_tier = merit_pyramid_absolute_parent_tier(
                    competition, t_child, season
                )
                parents_tier_ld = list(mens_by_abs.get(parent_feed_pyramid_tier) or ())
                parent_resolve_tier = parent_feed_pyramid_tier
            else:
                parents_tier_ld = list(leagues_by_local_tier.get(t_child - 1) or ())
                parent_resolve_tier = t_child - 1
            child_here = _stem_resolve_league_identity(
                children_tier,
                child_foreign,
                t_child,
                season,
                eff_foreign,
            )
            if child_here is None:
                continue
            key_new = (t_child, child_here.league_name)
            if key_new in merged:
                continue
            resolved_names: list[str] = []
            seen_rn: set[str] = set()
            for pf in parent_specs_foreign:
                parent_here = _stem_resolve_league_identity(
                    parents_tier_ld,
                    pf,
                    parent_resolve_tier,
                    season,
                    eff_foreign,
                )
                if parent_here is None:
                    continue
                nm = parent_here.league_name
                if nm not in seen_rn:
                    seen_rn.add(nm)
                    resolved_names.append(nm)
            if not resolved_names:
                continue
            merged[key_new] = tuple(resolved_names)
            logger.info(
                "Merit %s parent link(s) inferred from %s (Δseason-years=%d): %r → %r",
                competition,
                path.name,
                gap,
                child_here.league_name,
                merged[key_new],
            )

    return merged


def stem_parent_overrides_load(season: str) -> StemParentOverrides | None:
    """Return saved interactive stem links, or ``None`` if absent / unreadable."""
    overrides, _strips = stem_parent_json_load(season)
    return overrides


def stem_parent_overrides_save(season: str, overrides: StemParentOverrides) -> Path | None:
    """Persist men's overrides (interactive choices and/or merged inferred links).

    Round-trips every unknown top-level section on disk (women's, per-merit, stem strips)
    so concurrent edits to other render targets are never lost.
    """
    if not overrides:
        return None
    path = stem_parent_overrides_store_path(season)
    preserved_other: dict[str, object] = {}
    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(prev, dict):
                for k, v in prev.items():
                    if k in {"schema_version", "season", "men"}:
                        continue
                    preserved_other[k] = v
        except (OSError, json.JSONDecodeError):
            pass
    ordered_tiers = _encode_overrides_for_json(overrides)
    blob: dict[str, object] = {
        "schema_version": STEM_PARENT_OVERRIDE_SCHEMA_VERSION,
        "season": season,
        "men": ordered_tiers,
    }
    for k, v in preserved_other.items():
        blob[k] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")
    return path


def _encode_overrides_for_json(
    overrides: StemParentOverrides,
) -> dict[str, dict[str, str | list[str]]]:
    """Flatten ``(tier, child) -> parents`` into nested JSON form with stable ordering.

    Tier 5/6 (and merit child tiers 2–6) layout reads JSON insertion order for left-to-right
    placement, so insertion order within each tier is preserved as-is. Stem-style tiers
    (tier 7+) are sorted alphabetically (the stem code re-sorts internally; saved order is
    purely cosmetic for those rows).
    """
    by_tier: dict[str, dict[str, str | list[str]]] = {}
    for (t_num, child_name), parents_tuple in overrides.items():
        if not parents_tuple:
            enc: str | list[str] = "-"
        elif len(parents_tuple) == 1:
            enc = parents_tuple[0]
        else:
            enc = list(parents_tuple)
        by_tier.setdefault(str(t_num), {})[child_name] = enc
    ordered_tiers: dict[str, dict[str, str | list[str]]] = {}
    for k in sorted(by_tier.keys(), key=lambda tk: int(tk)):
        items = list(by_tier[k].items())
        if int(k) >= 7:
            items.sort(key=lambda it: _stem_sort_key_league_name(it[0]))
        ordered_tiers[k] = dict(items)
    return ordered_tiers


def _womens_flat_feeder_overrides_to_nested_json(
    flat: StemParentOverrides,
) -> dict[str, dict[str, str | list[str]]]:
    """Flat ``(band 2–4, child) → parents`` into JSON ``women`` section shape."""
    by_band: dict[int, dict[str, str | list[str]]] = {}
    for (t_num, child_name), parents_tuple in flat.items():
        if not isinstance(t_num, int) or not (2 <= t_num <= 4):
            continue
        if not parents_tuple:
            enc: str | list[str] = "-"
        elif len(parents_tuple) == 1:
            enc = parents_tuple[0]
        else:
            enc = list(parents_tuple)
        by_band.setdefault(t_num, {})[child_name] = enc
    out: dict[str, dict[str, str | list[str]]] = {}
    for k in sorted(by_band.keys()):
        items = sorted(by_band[k].items(), key=lambda it: _stem_sort_key_league_name(it[0]))
        out[str(k)] = dict(items)
    return out


def womens_parent_overrides_save(season: str, overrides: StemParentOverrides) -> Path | None:
    """Persist women's feeder overrides.

    Round-trips every unknown top-level section on disk (men's, per-merit, stem strips)
    so concurrent edits to other render targets are never lost.
    """
    subset = {k: v for k, v in overrides.items() if isinstance(k[0], int) and 2 <= k[0] <= 4}
    nested = _womens_flat_feeder_overrides_to_nested_json(subset)
    if not nested:
        return None

    path = stem_parent_overrides_store_path(season)
    preserved_other: dict[str, object] = {}
    schema_version: int = STEM_PARENT_OVERRIDE_SCHEMA_VERSION

    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(prev, dict):
                for k, v in prev.items():
                    if k in {"schema_version", "season", "women"}:
                        continue
                    preserved_other[k] = v
                sv = prev.get("schema_version")
                if sv is not None:
                    with contextlib.suppress(TypeError, ValueError):
                        schema_version = int(sv)
        except (OSError, json.JSONDecodeError):
            pass

    blob: dict[str, object] = {
        "schema_version": schema_version,
        "season": season,
    }
    if "men" in preserved_other:
        blob["men"] = preserved_other.pop("men")
    blob["women"] = nested
    for k, v in preserved_other.items():
        blob[k] = v

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")
    return path


def merit_parent_overrides_save(
    season: str,
    competition: str,
    overrides: StemParentOverrides,
) -> Path | None:
    """Persist per-merit-competition parent overrides.

    Round-trips every unknown top-level section on disk (men's, women's, other merit
    competitions, stem strips) so concurrent edits to other render targets are never lost.
    """
    if not overrides:
        return None

    nested = _encode_overrides_for_json(overrides)
    if not nested:
        return None

    path = stem_parent_overrides_store_path(season)
    preserved_other: dict[str, object] = {}
    schema_version: int = STEM_PARENT_OVERRIDE_SCHEMA_VERSION

    if path.is_file():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(prev, dict):
                for k, v in prev.items():
                    if k in {"schema_version", "season", competition}:
                        continue
                    preserved_other[k] = v
                sv = prev.get("schema_version")
                if sv is not None:
                    with contextlib.suppress(TypeError, ValueError):
                        schema_version = int(sv)
        except (OSError, json.JSONDecodeError):
            pass

    blob: dict[str, object] = {
        "schema_version": schema_version,
        "season": season,
    }
    # Keep men/women near the top for readability; other sections (other merit comps,
    # stem_slot_strips) follow in their original on-disk order, then the competition
    # we just wrote.
    for k in ("men", "women"):
        if k in preserved_other:
            blob[k] = preserved_other.pop(k)
    for k, v in preserved_other.items():
        blob[k] = v
    blob[competition] = nested

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# PNG rasterisation (Playwright)
# ---------------------------------------------------------------------------


def rasterise_svg_to_png(
    svg_path: Path,
    png_path: Path,
    *,
    scale: float = 1.0,
    image_poll_timeout_ms: float = 120_000.0,
) -> None:
    """Render ``svg_path`` to ``png_path`` using Playwright (Chromium).

    Requires ``pip install playwright && playwright install chromium`` (already
    documented in ``requirements-dev.txt``).

    Crests load inside ``foreignObject`` ``<img>`` nodes. Navigation uses ``domcontentloaded``
    only — ``load`` would block until every crest URL finishes (which can hang indefinitely).
    We poll until each ``img`` is ``complete`` or ``image_poll_timeout_ms`` elapses, then shoot.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - convenience guard
        raise RuntimeError(
            "Playwright is required for PNG output. "
            "Run: pip install -r requirements-dev.txt && python -m playwright install chromium"
        ) from exc

    # Read intrinsic SVG dimensions from the file so we can size the viewport
    # large enough to capture the whole image without scrolling artefacts.
    svg_text = svg_path.read_text(encoding="utf-8")
    width_match = re.search(r'<svg[^>]*\swidth="(\d+(?:\.\d+)?)"', svg_text)
    height_match = re.search(r'<svg[^>]*\sheight="(\d+(?:\.\d+)?)"', svg_text)
    if not width_match or not height_match:
        raise RuntimeError(f"Could not parse width/height from SVG header in {svg_path}")
    svg_w = int(float(width_match.group(1)))
    svg_h = int(float(height_match.group(1)))

    svg_uri = svg_path.resolve().as_uri()

    timeout_ms = float(image_poll_timeout_ms)
    if timeout_ms <= 0:
        timeout_ms = 1_000.0

    # True when every HTMLImageElement has left the "still loading" state (success or error).
    imgs_done_js = """\
(() => {
    for (const im of document.images) {
        if (!im.complete) {
            return false;
        }
    }
    return true;
})()
"""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, timeout=120_000)
        try:
            ctx = browser.new_context(
                viewport={"width": svg_w, "height": svg_h},
                device_scale_factor=scale,
            )
            page = ctx.new_page()
            png_path.parent.mkdir(parents=True, exist_ok=True)
            page.goto(svg_uri, wait_until="domcontentloaded", timeout=180_000)

            t0 = time.monotonic()
            while (time.monotonic() - t0) * 1000.0 < timeout_ms:
                if page.evaluate(imgs_done_js):
                    break
                page.wait_for_timeout(150)

            if not page.evaluate(imgs_done_js):
                incomplete = page.evaluate("""(() => {
                        let n = 0;
                        for (const im of document.images) {
                            if (!im.complete) n++;
                        }
                        return n;
                    })()""")
                logger.warning(
                    "PNG: %d crest <img> nodes still loading after %.0f ms; "
                    "capturing anyway (some crests may be incomplete).",
                    int(incomplete),
                    timeout_ms,
                )

            page.wait_for_timeout(400)
            # ``full_page=True`` can hang or take extreme time on wide SVGs with hundreds of
            # ``foreignObject`` crests; viewport already matches the SVG width/height.
            page.screenshot(
                path=str(png_path),
                full_page=False,
                omit_background=True,
                timeout=240_000,
            )
        finally:
            browser.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _validate_season(value: str) -> str:
    if not _SEASON_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"season must be YYYY-YYYY, got {value!r}")
    return value


def _default_svg_path(
    season: str,
    gender: Gender = DEFAULT_GENDER,
    *,
    merit_competition: str | None = None,
) -> Path:
    if merit_competition is not None:
        return DIST_DIR / season / f"pyramid_merit_{merit_competition}.svg"
    stem = "pyramid_womens" if gender == "womens" else "pyramid"
    return DIST_DIR / season / f"{stem}.svg"


def _default_png_path(
    season: str,
    gender: Gender = DEFAULT_GENDER,
    *,
    merit_competition: str | None = None,
) -> Path:
    if merit_competition is not None:
        return DIST_DIR / season / f"pyramid_merit_{merit_competition}.png"
    stem = "pyramid_womens" if gender == "womens" else "pyramid"
    return DIST_DIR / season / f"{stem}.png"


def _render_mens_standard_pyramid(
    season: str, args: argparse.Namespace, cw: int, *, all_leagues: bool = False
) -> int:
    """Write men's pyramid from geocode data + tier_mappings to fixed dist paths.

    Uses ``dist/<season>/pyramid.{svg,png}`` when ``all_leagues`` is false (normal men's run).
    When true (after ``--merit``), uses ``dist/<season>/pyramid_All_Leagues.{svg,png}`` so the
    national-only diagram is not overwritten.

    Ignores ``--output`` / ``--png-output`` so merit-specific paths are untouched.
    """
    gender: Gender = "mens"
    stem = "pyramid_All_Leagues" if all_leagues else "pyramid"
    svg_path = DIST_DIR / season / f"{stem}.svg"
    png_path = DIST_DIR / season / f"{stem}.png"

    logger.info(
        "Rendering men's pyramid (%s, %s) → %s …",
        season,
        stem,
        svg_path,
    )
    national_leagues = load_pyramid_leagues(season, gender=gender)
    national_by_tier: dict[int, list[LeagueData]] = {}
    for lg in national_leagues:
        national_by_tier.setdefault(lg.tier_num, []).append(lg)

    leagues = load_pyramid_leagues_with_merit(season) if all_leagues else national_leagues

    parent_overrides: StemParentOverrides | None = None
    stem_slot_strips: tuple[StemSlotStrip, ...] = ()

    if not args.ignore_saved_stem_parent_overrides:
        base_ov = stem_parent_overrides_load(season) or {}
        parent_overrides = stem_parent_overrides_merge_cross_season(
            season, national_by_tier, base_ov
        )
        if parent_overrides:
            n_base = len(base_ov)
            n_extra = len(parent_overrides) - n_base
            logger.info(
                "Men's pyramid (%s): %d stem override(s) (%d from %s%s)",
                stem,
                len(parent_overrides),
                n_base,
                stem_parent_overrides_store_path(season),
                f"; +{n_extra} inferred from other seasons" if n_extra else "",
            )
            if n_extra > 0:
                persisted = stem_parent_overrides_save(season, parent_overrides)
                if persisted is not None:
                    logger.info(
                        "Persisted inferred Counties stem links to %s (+%d new, %d total).",
                        persisted,
                        n_extra,
                        len(parent_overrides),
                    )

    if not args.ignore_stem_slot_strips:
        stem_slot_strips = stem_slot_strips_load(season)

    if stem_slot_strips:
        logger.info(
            "Applying %d stem_slot_strip(s) from %s (men's refresh).",
            len(stem_slot_strips),
            stem_parent_overrides_store_path(season),
        )

    svg = render_pyramid_svg(
        season,
        leagues,
        gender=gender,
        parent_overrides=parent_overrides,
        womens_parent_overrides=None,
        stem_slot_strips=stem_slot_strips,
        transparent_white_crest_backgrounds=args.transparent_white_crest_backgrounds,
        crest_transparency_workers=cw,
        mens_merge_merit_leagues=all_leagues,
    )

    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg, encoding="utf-8")
    logger.info("Wrote %s", svg_path)

    if args.png:
        logger.info(
            "Rasterising men's pyramid SVG to PNG (scale=%.2f) …",
            args.png_scale,
        )
        try:
            rasterise_svg_to_png(
                svg_path,
                png_path,
                scale=args.png_scale,
                image_poll_timeout_ms=args.png_image_timeout_ms,
            )
        except RuntimeError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Wrote %s", png_path)

    return 0


def _render_one_merit_pyramid(
    season: str,
    competition: str,
    args: argparse.Namespace,
    crest_bg_workers: int,
) -> int:
    """Load + render + (optionally) rasterise one merit competition's pyramid.

    Mirrors the men's branch of :func:`main` but keyed off the per-competition section
    in ``data/rugby/tier_mappings/<season>.json``. Returns ``0`` on success, non-zero on
    fatal errors (missing data, raster failure).
    """
    try:
        leagues_visible, offset = load_merit_pyramid_leagues(season, competition)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1
    if not leagues_visible:
        logger.warning(
            "Merit competition %r in %s has no loadable leagues — skipping.",
            competition,
            season,
        )
        return 0

    raw_local = load_merit_pyramid_leagues_raw(season, competition)
    leagues_by_local_tier: dict[int, list[LeagueData]] = {}
    for lg in raw_local:
        leagues_by_local_tier.setdefault(lg.tier_num, []).append(lg)

    logger.info(
        "Loaded %d %s merit leagues across local tiers %s (offset=%d)",
        len(leagues_visible),
        competition,
        sorted(leagues_by_local_tier.keys()),
        offset,
    )

    overrides_local: StemParentOverrides | None = None
    if args.interactive_stem_orphans:
        seed_local: StemParentOverrides | None = None
        if not args.ignore_saved_stem_parent_overrides:
            base_local = merit_parent_overrides_load(season, competition) or {}
            seed_local = merit_parent_overrides_merge_cross_season(
                season, competition, leagues_by_local_tier, dict(base_local)
            )
        overrides_local = merit_interactive_feeder_overrides(
            leagues_by_local_tier,
            competition,
            season,
            seed_overrides_local=seed_local,
        )
        saved = merit_parent_overrides_save(season, competition, overrides_local)
        if saved is not None:
            logger.info(
                "Saved merit %s overrides (%d entries) to %s",
                competition,
                len(overrides_local),
                saved,
            )
    else:
        base_loaded = merit_parent_overrides_load(season, competition) or {}
        base_keys = frozenset(base_loaded.keys())
        if args.ignore_saved_stem_parent_overrides:
            overrides_local = {}
        else:
            overrides_local = merit_parent_overrides_merge_cross_season(
                season, competition, leagues_by_local_tier, dict(base_loaded)
            )

        keys_after_cross = frozenset(overrides_local.keys())
        n_cross = len(keys_after_cross - base_keys)

        n_heur = merit_apply_parent_heuristics_local(
            leagues_by_local_tier, competition, overrides_local, season
        )

        if overrides_local:
            logger.info(
                "Merit %s parent overrides: %d loaded; +%d from other seasons; "
                "+%d from name heuristics (%d total)",
                competition,
                len(base_keys),
                n_cross,
                n_heur,
                len(overrides_local),
            )
        elif not args.ignore_saved_stem_parent_overrides:
            logger.info(
                "Merit %s: no saved parent overrides in %s",
                competition,
                stem_parent_overrides_store_path(season),
            )

        if not args.ignore_saved_stem_parent_overrides and (n_cross > 0 or n_heur > 0):
            persisted = merit_parent_overrides_save(season, competition, overrides_local)
            if persisted is not None:
                logger.info(
                    "Persisted merit %s overrides to %s (+%d cross-season, +%d heuristic; "
                    "%d total).",
                    competition,
                    persisted,
                    n_cross,
                    n_heur,
                    len(overrides_local),
                )

    overrides_visible: StemParentOverrides | None = None
    if overrides_local:
        overrides_visible = merit_overrides_local_to_visible(overrides_local, offset)
        if not overrides_visible:
            overrides_visible = None

    svg = render_pyramid_svg(
        season,
        leagues_visible,
        gender="mens",
        parent_overrides=overrides_visible,
        transparent_white_crest_backgrounds=args.transparent_white_crest_backgrounds,
        crest_transparency_workers=crest_bg_workers,
        merit_competition=competition,
        merit_local_offset=offset,
    )

    svg_path = args.output or _default_svg_path(season, "mens", merit_competition=competition)
    png_path = args.png_output or _default_png_path(season, "mens", merit_competition=competition)

    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg, encoding="utf-8")
    logger.info("Wrote %s", svg_path)

    if args.png:
        logger.info(
            "Rasterising merit %s SVG to PNG (scale=%.2f) — this requires Playwright …",
            competition,
            args.png_scale,
        )
        try:
            rasterise_svg_to_png(
                svg_path,
                png_path,
                scale=args.png_scale,
                image_poll_timeout_ms=args.png_image_timeout_ms,
            )
        except RuntimeError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Wrote %s", png_path)

    return 0


def main() -> int:
    setup_logging()

    parser = argparse.ArgumentParser(
        description=(
            "Generate a hierarchical pyramid image (SVG + optional PNG) of the "
            "English rugby pyramid for a given season. Defaults to the men's pyramid; "
            "pass --womens for the women's pyramid (Premiership → NC 3, no Counties stem), "
            "or --merit [COMPETITION] for one or all merit competition pyramids."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--season",
        type=_validate_season,
        default="2025-2026",
        help="Season to render (e.g. 2025-2026).",
    )
    parser.add_argument(
        "--womens",
        action="store_true",
        help=(
            "Render the women's pyramid (Premiership → Championship → National Challenge). "
            "Outputs default to dist/<season>/pyramid_womens.{svg,png}. "
            "Optional `women` section (bands 2–4 only) in tier_mappings loads unless "
            "--ignore-saved-stem-parent-overrides is set. "
            "Men's interactive parent linker (--interactive-stem-orphans) and "
            "--ignore-stem-slot-strips have no effect with --womens."
        ),
    )
    parser.add_argument(
        "--merit",
        nargs="?",
        const="",
        default=None,
        metavar="COMPETITION",
        help=(
            "Render one or more merit competition pyramids. Pass a competition name "
            "(e.g. --merit Hampshire) to render just that competition, or --merit with "
            "no value to iterate every merit competition under "
            "data/rugby/geocoded_teams/<season>/merit/. Outputs default to "
            "dist/<season>/pyramid_merit_<Competition>.{svg,png}. Mutually exclusive "
            "with --womens. The per-merit section in tier_mappings JSON is read / "
            "written using the competition name as the key; --interactive-stem-orphans "
            "and --ignore-saved-stem-parent-overrides scope to that section. After merit "
            "SVGs are written, the men's merged pyramid (national + merit at absolute tiers) "
            "is regenerated at "
            "dist/<season>/pyramid_All_Leagues.{svg,png} (default paths; --output/--png-output "
            "apply only to merit outputs). Run without --merit for "
            "dist/<season>/pyramid.{svg,png}."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "SVG output path (default: dist/<season>/pyramid.svg, "
            "or pyramid_womens.svg with --womens)."
        ),
    )
    parser.add_argument(
        "--png",
        action="store_true",
        help="Also rasterise the SVG to a PNG using Playwright.",
    )
    parser.add_argument(
        "--png-output",
        type=Path,
        default=None,
        help=(
            "PNG output path when --png is set (default: dist/<season>/pyramid.png, "
            "or pyramid_womens.png with --womens)."
        ),
    )
    parser.add_argument(
        "--png-scale",
        type=float,
        default=1.0,
        help="Device-scale-factor for the PNG (>1 for higher-DPI output).",
    )
    parser.add_argument(
        "--png-image-timeout-ms",
        type=float,
        default=120_000.0,
        help=(
            "Maximum time to poll for <img> crest loads before taking the PNG screenshot "
            "(avoids Playwright networkidle hangs on slow RFU URLs)."
        ),
    )
    parser.add_argument(
        "--transparent-white-crest-backgrounds",
        action="store_true",
        help=(
            "Fetch RFU crest PNGs: if the top-left 2×2 pixels are near-white, flood-fill the "
            "(0,0)-connected region into transparency; cache results and inline data: PNG URIs "
            "(enlarges SVG; requires Pillow)."
        ),
    )
    parser.add_argument(
        "--crest-bg-workers",
        type=int,
        default=12,
        help=(
            "Parallel RFU fetches for --transparent-white-crest-backgrounds (effective range 1–32)."
        ),
    )
    parser.add_argument(
        "--interactive-stem-orphans",
        action="store_true",
        help=(
            "TTY only: interactively edit data/rugby/tier_mappings/<season>.json. "
            "Default (men's): tier 5→4 and tier 6→5 feeders, then Counties stem orphans. "
            "With --womens: feeder links for women's visual bands 2→1, 3→2, and 4→3 only. "
            "With --merit: merit feeder prompts (then the run still writes merit + men's "
            "pyramid outputs as usual). "
            "blank/0 = explicit unlinked; s stops prompting."
        ),
    )
    parser.add_argument(
        "--ignore-saved-stem-parent-overrides",
        action="store_true",
        help=(
            "Skip loading parent_overrides from data/rugby/tier_mappings/<season>.json. This "
            "covers the tier 5/6 NL2-Regional nesting and the Counties stem links; men's "
            "tiers 4–6 fall back to equal-width and the stem uses its name-matching heuristic. "
            "With --womens, skips the ``women`` section only (prefix inference for feeders). "
            "stem_slot_strips still apply unless --ignore-stem-slot-strips."
        ),
    )
    parser.add_argument(
        "--ignore-stem-slot-strips",
        action="store_true",
        help=(
            "Skip stem_slot_strips from data/rugby/tier_mappings/<season>.json (pure default stem "
            "column widths)."
        ),
    )
    args = parser.parse_args()

    season: str = args.season

    cw = int(args.crest_bg_workers)
    if cw < 1 or cw > 32:
        logger.error("--crest-bg-workers must be between 1 and 32")
        return 1

    if args.png_scale <= 0 or args.png_scale > 4:
        logger.error("--png-scale must be > 0 and ≤ 4")
        return 1

    if args.womens and args.merit is not None:
        logger.error("--womens and --merit are mutually exclusive")
        return 1

    if args.merit is not None:
        if args.merit:
            comps = [args.merit]
        else:
            comps = discover_merit_competitions(season)
            if not comps:
                logger.error(
                    "No merit competitions found under data/rugby/geocoded_teams/%s/merit/",
                    season,
                )
                return 1
            logger.info(
                "Rendering merit pyramids for %d competition(s): %s",
                len(comps),
                ", ".join(comps),
            )
        if (args.output is not None or args.png_output is not None) and len(comps) > 1:
            logger.error(
                "--output / --png-output cannot be combined with --merit covering "
                "multiple competitions; pass a single competition name instead."
            )
            return 1
        rc = 0
        for comp in comps:
            r = _render_one_merit_pyramid(season, comp, args, cw)
            if r != 0:
                rc = r
        rm = _render_mens_standard_pyramid(season, args, cw, all_leagues=True)
        if rm != 0:
            rc = rm
        return rc

    gender: Gender = "womens" if args.womens else "mens"
    svg_path: Path = args.output or _default_svg_path(season, gender)
    png_path: Path = args.png_output or _default_png_path(season, gender)

    logger.info("Loading geocoded leagues for season %s (%s pyramid) …", season, gender)
    leagues = load_pyramid_leagues(season, gender=gender)
    if gender == "womens":
        logger.info("Loaded %d women's pyramid leagues across tiers 1–6", len(leagues))
    else:
        logger.info("Loaded %d men's pyramid leagues across tiers 1–11", len(leagues))

    leagues_by_tier: dict[int, list[LeagueData]] = {}
    for lg in leagues:
        leagues_by_tier.setdefault(lg.tier_num, []).append(lg)
    for t in sorted(leagues_by_tier):
        logger.debug("  Tier %d: %d league(s)", t, len(leagues_by_tier[t]))

    parent_overrides: StemParentOverrides | None = None
    womens_parent_overrides: StemParentOverrides | None = None
    stem_slot_strips: tuple[StemSlotStrip, ...] = ()

    if gender == "mens":
        if args.interactive_stem_orphans:
            seed: StemParentOverrides | None = None
            if not args.ignore_saved_stem_parent_overrides:
                base_ov = stem_parent_overrides_load(season) or {}
                seed = stem_parent_overrides_merge_cross_season(season, leagues_by_tier, base_ov)
                seed = seed if seed else None
            parent_overrides = stem_interactive_parent_overrides(
                leagues_by_tier, season, seed_overrides=seed
            )
            saved = stem_parent_overrides_save(season, parent_overrides)
            if saved is not None:
                logger.info(
                    "Saved interactive parent overrides (%d entries) to %s",
                    len(parent_overrides),
                    saved,
                )
        elif not args.ignore_saved_stem_parent_overrides:
            base_ov = stem_parent_overrides_load(season) or {}
            parent_overrides = stem_parent_overrides_merge_cross_season(
                season, leagues_by_tier, base_ov
            )
            if parent_overrides:
                n_base = len(base_ov)
                n_extra = len(parent_overrides) - n_base
                logger.info(
                    "Loaded %d Counties stem parent override(s) (%d from %s%s)",
                    len(parent_overrides),
                    n_base,
                    stem_parent_overrides_store_path(season),
                    f"; +{n_extra} inferred from other seasons" if n_extra else "",
                )
                if n_extra > 0:
                    persisted = stem_parent_overrides_save(season, parent_overrides)
                    if persisted is not None:
                        logger.info(
                            "Persisted inferred Counties stem links to %s (+%d new, %d total).",
                            persisted,
                            n_extra,
                            len(parent_overrides),
                        )

        if not args.ignore_stem_slot_strips:
            stem_slot_strips = stem_slot_strips_load(season)

        if stem_slot_strips:
            logger.info(
                "Applying %d stem_slot_strip(s) from %s.",
                len(stem_slot_strips),
                stem_parent_overrides_store_path(season),
            )
    else:
        if args.interactive_stem_orphans:
            seed_w: StemParentOverrides | None = None
            if not args.ignore_saved_stem_parent_overrides:
                base_w = womens_parent_overrides_load(season) or {}
                merged_w = womens_parent_overrides_merge_cross_season(
                    season, leagues_by_tier, base_w
                )
                seed_w = merged_w if merged_w else None
            womens_parent_overrides = womens_interactive_feeder_overrides(
                leagues_by_tier,
                seed_overrides=seed_w,
            )
            persisted = womens_parent_overrides_save(season, womens_parent_overrides)
            if persisted is not None:
                n_w = sum(
                    1 for (tb, _) in womens_parent_overrides if isinstance(tb, int) and 2 <= tb <= 4
                )
                logger.info(
                    "Saved women's feeder overrides (%d entries) to %s",
                    n_w,
                    persisted,
                )
        elif not args.ignore_saved_stem_parent_overrides:
            base_w = womens_parent_overrides_load(season) or {}
            womens_parent_overrides = womens_parent_overrides_merge_cross_season(
                season, leagues_by_tier, base_w
            )
            if womens_parent_overrides:
                n_base_w = len(base_w)
                n_extra_w = len(womens_parent_overrides) - n_base_w
                logger.info(
                    "Loaded %d women's feeder override(s) (%d from %s%s)",
                    len(womens_parent_overrides),
                    n_base_w,
                    stem_parent_overrides_store_path(season),
                    f"; +{n_extra_w} inferred from other seasons" if n_extra_w else "",
                )
                if n_extra_w > 0:
                    persisted_w = womens_parent_overrides_save(season, womens_parent_overrides)
                    if persisted_w is not None:
                        logger.info(
                            "Persisted inferred women's feeder links to %s (+%d new, %d total).",
                            persisted_w,
                            n_extra_w,
                            len(womens_parent_overrides),
                        )
        if args.ignore_stem_slot_strips:
            logger.info("--ignore-stem-slot-strips has no effect with --womens.")

    svg = render_pyramid_svg(
        season,
        leagues,
        gender=gender,
        parent_overrides=parent_overrides,
        womens_parent_overrides=womens_parent_overrides,
        stem_slot_strips=stem_slot_strips,
        transparent_white_crest_backgrounds=args.transparent_white_crest_backgrounds,
        crest_transparency_workers=cw,
    )

    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg, encoding="utf-8")
    logger.info("Wrote %s", svg_path)

    if args.png:
        logger.info(
            "Rasterising SVG to PNG (scale=%.2f) — this requires Playwright …", args.png_scale
        )
        try:
            rasterise_svg_to_png(
                svg_path,
                png_path,
                scale=args.png_scale,
                image_poll_timeout_ms=args.png_image_timeout_ms,
            )
        except RuntimeError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Wrote %s", png_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
