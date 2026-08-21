"""
Generate Instagram-ready league maps (3:4 portrait), one per pyramid level.

One image per men's pyramid tier: England + Isle of Man + Channel Islands viewport,
league territory shading (same algorithm as interactive maps), and bold left-panel copy::

    Rugby Union
    2026-2027
    Level 7
    Counties 1
    19 leagues · 234 teams
    rugbyunionmap.uk

County merit ladders are drawn alongside the national ones at the level they
feed, filled with diagonal two-tone stripes so they read as merit at a glance;
pass ``--no-merit`` for pyramid-only images.

Outputs SVG by default; pass ``--png`` to rasterise via Playwright (see requirements-dev.txt).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import logging
import math
import re
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import lru_cache, partial
from html import escape
from pathlib import Path
from urllib.error import URLError

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core.boundaries import VALID_DETAIL_LEVELS, boundary_paths_for_detail
from core.colors import UNASSIGNED_COLOR, contrasting_shade
from core.config import CACHE_DIR, CURRENT_SEASON, DIST_DIR, REPO_ROOT, setup_logging
from core.map_builder import (
    ITLHierarchy,
    MapConfig,
    MarkerItem,
    _assign_items_to_itl_regions,
    _collect_group_geometries,
    _items_to_placed,
    _merge_territories,
    _pick_color,
    load_itl_hierarchy,
    preassign_itl_regions,
)
from core.patterns import stripe_pattern_svg
from rugby import DATA_DIR
from rugby.maps import (
    COLOR_PALETTE,
    COUNTRY_OUTLINES,
    PYRAMID_CATEGORY,
    RFU_FALLBACK_ICON,
    TIER_ENTRY_LEVELS,
    TIER_FLOOR_LEVELS,
    LoadedItems,
    _load_marker_items,
    _rotated_palette,
)
from rugby.pyramid_image import _rfu_crest_get_bytes, _valid_image_url
from rugby.seo import BASE_URL
from rugby.tiers import get_competition_offset, mens_current_tier_name

logger = logging.getLogger(__name__)

OUTPUT_ROOT = REPO_ROOT / "output" / "instagram" / "maps"

# 3:4 portrait — Instagram feed friendly.
IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1440
# Playwright device_scale_factor for PNG export (1080×1440 SVG → 3240×4320 px at 3×).
PNG_SCALE_DEFAULT = 3.0

# Fraction of the frame kept clear around the landmass. England + IoM + Channel
# Islands in Web Mercator is naturally ~3:4, so the map fills the canvas and the
# caption sits in the sea/Wales gap on the left rather than in a separate panel.
MAP_MARGIN = 0.02

SEA_BG = "#0d1117"
LAND_BG = "#1c232e"
OUTLINE_STROKE = "#39445a"
COUNTRY_OUTLINE_WIDTH = 1.0
TERRITORY_STROKE = "#0d1117"
TEXT_PRIMARY = "#ffffff"
TEXT_MUTED = "#8ea3c4"

# County merit ladders run alongside the national pyramid at the same level, so
# they are drawn in diagonal two-tone stripes: the league keeps its own palette
# colour and gains a second, contrasting band that marks it as merit at a glance.
MERIT_STRIPE_PERIOD = 12.0
MERIT_STRIPE_ANGLE = 45.0
MERIT_STRIPE_CONTRAST = 0.42
MERIT_LEGEND_SWATCH = 26
MERIT_LEGEND_GAP = 10
MERIT_LEGEND_BELOW_STATS = 46
MERIT_LEGEND_FONT_SIZE = 26
MERIT_LEGEND_TEXT = "Striped = merit leagues"

BADGE_FILL = "#ffffff"
# Largest crest diameter in canvas px at IMAGE_WIDTH, derived per tier from how
# far apart its clubs sit: the sparse upper tiers get bold badges, the crowded
# county tiers stay at the floor. Isolated clubs reach this size; clubs in dense
# clusters shrink toward BADGE_MIN_DIAMETER_RATIO of it.
BADGE_AUTO_SPACING_FACTOR = 1.5
BADGE_DIAMETER_FLOOR = 26
# Tier 1–3 (Premiership → National 2): sparse national leagues.
BADGE_UPPER_TIER_MAX = 3
BADGE_DIAMETER_CEILING_UPPER = 60
# Tier 4+ (Regional 1 downward): spacing-derived, capped lower — level 4 sits here
# with level 5, not with the sparse upper group despite wider club spacing.
BADGE_DIAMETER_CEILING_LOWER = 50
BADGE_DIAMETER_CEILING = max(BADGE_DIAMETER_CEILING_UPPER, BADGE_DIAMETER_CEILING_LOWER)
BADGE_MIN_DIAMETER_RATIO = 0.4
BADGE_RING_WIDTH = 2.0
# Badge radius as a fraction of the distance to the nearest other club. At 0.5
# neighbouring badges just touch before any spacing pass runs.
BADGE_NEIGHBOUR_FRACTION = 0.5
# Clear gap left between badge edges by the spacing pass.
BADGE_SPACING_GAP = 1.2
BADGE_RELAX_ITERATIONS = 80
# Cap on how far a badge may slide from its true ground, in multiples of its radius.
BADGE_MAX_SHIFT_RADII = 1.5
# Spacing and re-growth alternate this many times, each round attempting to
# enlarge every badge by BADGE_GROWTH_STEP and keeping only the increases that fit.
BADGE_FIT_ROUNDS = 10
BADGE_GROWTH_STEP = 1.2
BADGE_FIT_TOLERANCE = 0.05
# Cached crests are rasterised at the on-canvas display size (× PNG scale when
# exporting) so badges are not upscaled from a tiny inline bitmap.
CREST_CACHE_PX_MIN = 64
CREST_CACHE_SUBDIR = "boundary_graphic_crests_v1"

FONT_HEADING = "Oswald, system-ui, -apple-system, Segoe UI, sans-serif"
FONT_BODY = "Barlow, system-ui, -apple-system, Segoe UI, sans-serif"
SVG_SIMPLIFY_TOLERANCE = 0.003

# Caption anchor, as a fraction of the canvas. Sits in the open water west of
# England (Irish Sea / Wales / Atlantic), which is empty at every level.
CAPTION_X_RATIO = 0.05
CAPTION_Y_RATIO = 0.36
SITE_LOGO_SIZE = 30
SITE_URL_FONT_SIZE = 32
SITE_URL_GAP = 8
TIER_NAME_Y_OFFSET = 248
TIER_STATS_BELOW_NAME = 48
TIER_STATS_FONT_SIZE = 32
SITE_URL_BELOW_STATS = 62
SITE_HOST = BASE_URL.removeprefix("https://").removeprefix("http://")


def _season_start_year(season: str) -> int:
    return int(season.split("-", maxsplit=1)[0])


def _instagram_tier_name(tier_num: int, season: str) -> str:
    """Subtitle under ``Level N``; blank when it would repeat the heading (pyramid margin rule)."""
    if tier_num == 1 and season and season < "2009-2010":
        return "Championship"
    if season and _season_start_year(season) <= 2021 and tier_num >= 5:
        return ""
    name = mens_current_tier_name(tier_num, season)
    if not name or name == f"Level {tier_num}":
        return ""
    return name


def _tier_stats_line(league_count: int, total_teams: int) -> str:
    """Match :func:`rugby.pyramid_image._tier_band_stats_line` wording."""
    lw = "league" if league_count == 1 else "leagues"
    tw = "team" if total_teams == 1 else "teams"
    return f"{league_count} {lw} · {total_teams} {tw}"


def merit_items_at_pyramid_levels(loaded: LoadedItems, season: str) -> list[MarkerItem]:
    """Copy merit clubs onto the absolute pyramid level their competition feeds."""
    items: list[MarkerItem] = []
    for comp_key, comp_items in loaded.merit.items():
        offset = get_competition_offset(comp_key, season)
        for item in comp_items:
            level = item.tier_num + offset
            items.append(replace(item, tier_num=level, tier=mens_current_tier_name(level, season)))
    return items


def load_levels(season: str, *, include_merit: bool = True) -> dict[int, list[MarkerItem]]:
    """Men's clubs for *season*, keyed by absolute pyramid level.

    Merit clubs sit beside the national ladder at the level their competition
    feeds, which is also what puts the merit-only levels below Counties 5 on the
    map at all.
    """
    loaded = _load_marker_items(
        str(DATA_DIR / "league_data" / season), season, travel_distances=None
    )
    items = [it for it in loaded.pyramid if it.tier_num < 100]
    if include_merit:
        items += [it for it in merit_items_at_pyramid_levels(loaded, season) if it.tier_num < 100]

    by_level: dict[int, list[MarkerItem]] = {}
    for item in items:
        by_level.setdefault(item.tier_num, []).append(item)
    return by_level


def is_merit(item: MarkerItem) -> bool:
    """True for a club from a county merit competition rather than the pyramid."""
    return item.category is not None and item.category != PYRAMID_CATEGORY


def merit_group_names(items: Iterable[MarkerItem]) -> set[str]:
    """League names among *items* that belong to merit competitions."""
    return {item.group for item in items if is_merit(item)}


@lru_cache(maxsize=1)
def _site_logo_href() -> str:
    """Inline ``dist/favicon.svg`` so PNG export does not depend on network fetches."""
    raw = (DIST_DIR / "favicon.svg").read_bytes()
    return "data:image/svg+xml;base64," + base64.standard_b64encode(raw).decode("ascii")


def _crest_cache_path(url: str, px: int) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / CREST_CACHE_SUBDIR / f"{digest}_{px}.png"


def _crest_inline_px(*, badge_diameter: float | None, png_scale: float = 1.0) -> int:
    """Raster size for inlined crests — matches badge inner diameter × export scale."""
    badge = (
        float(badge_diameter)
        if badge_diameter is not None and badge_diameter > 0
        else float(BADGE_DIAMETER_CEILING)
    )
    display = badge - BADGE_RING_WIDTH * 1.5
    return max(CREST_CACHE_PX_MIN, int(math.ceil(display * max(1.0, png_scale))))


def _downscaled_crest_png(raw: bytes, px: int) -> bytes | None:
    """Resize crest bytes to fit *px* square, returning PNG bytes (None if unreadable)."""
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return None

    try:
        with Image.open(io.BytesIO(raw)) as im:
            rgba = im.convert("RGBA")
            rgba.thumbnail((px, px), Image.LANCZOS)
            buf = io.BytesIO()
            rgba.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except Exception:
        # Vector crests (SVG) and malformed images fall back to the remote URL.
        return None


def build_crest_href_map(
    urls: list[str],
    *,
    px: int | None = None,
    badge_diameter: float | None = None,
    png_scale: float = 1.0,
    max_workers: int = 12,
) -> dict[str, str]:
    """Map crest URL -> inline ``data:image/png;base64`` URI, cached on disk.

    URLs that cannot be downscaled locally are omitted; callers fall back to the
    remote URL so the crest still renders when the browser fetches it.
    """
    if px is None:
        px = _crest_inline_px(badge_diameter=badge_diameter, png_scale=png_scale)
    uniq = sorted({u for u in urls if u})
    if not uniq:
        return {}

    def worker(url: str) -> tuple[str, str | None]:
        cache_path = _crest_cache_path(url, px)
        try:
            if cache_path.is_file():
                png = cache_path.read_bytes()
            else:
                png_opt = _downscaled_crest_png(_rfu_crest_get_bytes(url), px)
                if png_opt is None:
                    return url, None
                png = png_opt
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(png)
            return url, "data:image/png;base64," + base64.standard_b64encode(png).decode("ascii")
        except (OSError, ValueError, URLError, TimeoutError) as exc:
            logger.debug("Crest inline skipped for %s: %s", url, exc)
            return url, None

    result: dict[str, str] = {}
    skipped = 0
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 32))) as pool:
        for url, href in pool.map(worker, uniq):
            if href is not None:
                result[url] = href
            else:
                skipped += 1

    logger.info(
        "Inlined %d of %d unique crests (%d unavailable; those badges show club names)",
        len(result),
        len(uniq),
        skipped,
    )
    return result


def _badge_diameter_ceiling(tier_num: int) -> float:
    """Largest badge diameter allowed for a pyramid level."""
    if tier_num <= BADGE_UPPER_TIER_MAX:
        return float(BADGE_DIAMETER_CEILING_UPPER)
    return float(BADGE_DIAMETER_CEILING_LOWER)


def _auto_badge_diameter(positions: np.ndarray, *, tier_num: int) -> float:
    """Pick a badge size for one tier from the typical gap between its clubs.

    Median nearest-neighbour distance falls steadily down the pyramid (roughly
    94px at National League 1 against 13px at Counties 3), so scaling by it gives
    the sparse upper tiers bold badges without swamping the crowded lower ones.
    Tier 4 uses the lower-tier ceiling even though its clubs are more spread out.
    """
    ceiling = _badge_diameter_ceiling(tier_num)
    if len(positions) < 2:
        return ceiling

    distances, _ = cKDTree(positions).query(positions, k=2)
    median_spacing = float(np.median(distances[:, 1]))
    return float(
        np.clip(
            median_spacing * BADGE_AUTO_SPACING_FACTOR,
            BADGE_DIAMETER_FLOOR,
            ceiling,
        )
    )


def _nearest_neighbour_radii(
    positions: np.ndarray, *, max_radius: float, min_radius: float
) -> np.ndarray:
    """Radius per badge, scaled by how far away its closest neighbour is."""
    if len(positions) < 2:
        return np.full(len(positions), max_radius)

    tree = cKDTree(positions)
    # k=2 because the first neighbour of every point is itself.
    distances, _ = tree.query(positions, k=2)
    nearest = distances[:, 1]
    return np.clip(nearest * BADGE_NEIGHBOUR_FRACTION, min_radius, max_radius)


def _crowded_indices(positions: np.ndarray, radii: np.ndarray) -> np.ndarray:
    """Indices of badges that still overlap a neighbour at the given radii."""
    if len(positions) < 2:
        return np.empty(0, dtype=int)

    pairs = cKDTree(positions).query_pairs(r=float(radii.max() * 2), output_type="ndarray")
    if len(pairs) == 0:
        return np.empty(0, dtype=int)

    left, right = pairs[:, 0], pairs[:, 1]
    distances = np.linalg.norm(positions[right] - positions[left], axis=1)
    clashing = distances < radii[left] + radii[right] - BADGE_FIT_TOLERANCE
    return np.unique(np.concatenate((left[clashing], right[clashing])))


def _layout_badges(
    positions: np.ndarray,
    *,
    max_radius: float,
    min_radius: float,
    gap: float = BADGE_SPACING_GAP,
) -> tuple[np.ndarray, np.ndarray]:
    """Settle badge positions and sizes together, returning (positions, radii).

    Crowding gives each badge a conservative starting size. Every round then tries
    to grow all badges a step, re-spaces from the original grounds, and keeps the
    increase only for badges that still fit. Growth is what drives the extra
    separation, so a club shrunk by one close neighbour can recover most of its
    size once the pair has been nudged apart.
    """
    radii = _nearest_neighbour_radii(positions, max_radius=max_radius, min_radius=min_radius)
    # Budget fixed up front from how crowded each club is, so growing a badge never
    # buys it permission to wander further from its ground.
    relax = partial(
        _relax_positions,
        gap=gap,
        iterations=BADGE_RELAX_ITERATIONS,
        anchors=positions,
        max_shift=radii * BADGE_MAX_SHIFT_RADII,
    )
    current = relax(positions, radii)

    for _ in range(BADGE_FIT_ROUNDS):
        candidate = np.minimum(radii * BADGE_GROWTH_STEP, max_radius)
        if np.allclose(candidate, radii, atol=BADGE_FIT_TOLERANCE):
            break

        trial = relax(positions, candidate)
        blocked = _crowded_indices(trial, candidate)
        candidate[blocked] = radii[blocked]
        if np.allclose(candidate, radii, atol=BADGE_FIT_TOLERANCE):
            break

        radii = candidate
        current = relax(positions, radii)

    return current, radii


def _separation_directions(delta: np.ndarray, pair_index: np.ndarray) -> np.ndarray:
    """Unit vectors pushing each pair apart, with a stable fallback for co-located clubs."""
    distances = np.linalg.norm(delta, axis=1)
    coincident = distances < 1e-9
    if coincident.any():
        # Deterministic angles so shared grounds fan out the same way every run.
        angles = pair_index[coincident] * 2.399963
        delta = delta.copy()
        delta[coincident] = np.column_stack((np.cos(angles), np.sin(angles)))
        distances = distances.copy()
        distances[coincident] = 1.0
    return delta / distances[:, None], distances


def _relax_positions(
    positions: np.ndarray,
    radii: np.ndarray,
    *,
    gap: float,
    iterations: int,
    anchors: np.ndarray | None = None,
    max_shift: np.ndarray | None = None,
) -> np.ndarray:
    """Nudge overlapping badges apart, anchored so none drifts far from its ground.

    *anchors* defaults to *positions*; pass the original projected points when
    relaxing repeatedly so the drift cap does not creep across rounds.

    *max_shift* is the per-badge movement budget. It must not be derived from the
    badge's current radius during a grow-and-fit loop, or growth and displacement
    feed each other and every badge ends up at full size far from its ground.
    """
    if len(positions) < 2:
        return positions

    anchors = positions.copy() if anchors is None else anchors
    current = positions.copy()
    if max_shift is None:
        max_shift = radii * BADGE_MAX_SHIFT_RADII
    search_radius = float(radii.max() * 2 + gap)

    for _ in range(iterations):
        pairs = cKDTree(current).query_pairs(r=search_radius, output_type="ndarray")
        if len(pairs) == 0:
            break

        left, right = pairs[:, 0], pairs[:, 1]
        delta = current[right] - current[left]
        directions, distances = _separation_directions(delta, left)
        overlap = (radii[left] + radii[right] + gap) - distances

        overlapping = overlap > 0
        if not overlapping.any():
            break

        left, right = left[overlapping], right[overlapping]
        push = directions[overlapping] * (overlap[overlapping] * 0.5)[:, None]

        displacement = np.zeros_like(current)
        np.add.at(displacement, left, -push)
        np.add.at(displacement, right, push)
        current += displacement

        # Rein each badge back toward its true position.
        offset = current - anchors
        drift = np.linalg.norm(offset, axis=1)
        pulled = drift > max_shift
        if pulled.any():
            current[pulled] = (
                anchors[pulled] + offset[pulled] / drift[pulled, None] * max_shift[pulled, None]
            )

    return current


def _usable_crest_url(url: str | None) -> bool:
    """True when *url* is a club crest we should embed (not the generic RFU fallback)."""
    return bool(url) and url != RFU_FALLBACK_ICON and _valid_image_url(url)


def _split_badge_label(name: str, *, max_lines: int = 2) -> list[str]:
    label = (name or "?").strip() or "?"
    words = label.split()
    if len(words) <= 1 or max_lines <= 1 or len(label) <= 14:
        return [label]
    mid = (len(words) + 1) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def _badge_name_label_svg(name: str, *, inner_radius: float) -> str:
    """Centre the club name inside a badge circle when no crest is available."""
    lines = _split_badge_label(name)
    inner_d = inner_radius * 2
    longest = max(len(line) for line in lines)
    font_size = inner_d * 0.22
    if longest > 16:
        font_size *= 0.72
    elif longest > 12:
        font_size *= 0.85
    if len(lines) > 1:
        font_size *= 0.88
    font_size = max(4.0, min(font_size, inner_d * 0.28))

    if len(lines) == 1:
        return (
            f'<text text-anchor="middle" dominant-baseline="central" x="0" y="0" '
            f'fill="#1c232e" font-family="Barlow,sans-serif" font-weight="600" '
            f'font-size="{font_size:.2f}">{escape(lines[0])}</text>'
        )

    line_height = font_size * 1.12
    first_dy = -line_height * (len(lines) - 1) / 2
    tspans: list[str] = []
    for index, line in enumerate(lines):
        if index == 0:
            tspans.append(f'<tspan x="0" dy="{first_dy:.2f}">{escape(line)}</tspan>')
        else:
            tspans.append(f'<tspan x="0" dy="{line_height:.2f}">{escape(line)}</tspan>')
    return (
        f'<text text-anchor="middle" fill="#1c232e" font-family="Barlow,sans-serif" '
        f'font-weight="600" font-size="{font_size:.2f}">{"".join(tspans)}</text>'
    )


def _render_badges(
    items: list[MarkerItem],
    colours: dict[str, str],
    project: Callable[[float, float], tuple[float, float]],
    crest_hrefs: dict[str, str],
    *,
    tier_num: int,
    diameter: float | None = None,
    min_diameter: float | None = None,
) -> str:
    """Circular crest markers sized by local crowding and spaced to limit overlap.

    *diameter* caps the badge size; pass ``None`` to derive it from this tier's
    club spacing, or ``0`` to omit badges entirely.
    """
    if not items or (diameter is not None and diameter <= 0):
        return ""

    projected = np.array([project(it.longitude, it.latitude) for it in items], dtype=float)
    if diameter is None:
        diameter = _auto_badge_diameter(projected, tier_num=tier_num)
        logger.debug(
            "Auto badge diameter for tier %d (%d clubs): %.1fpx", tier_num, len(items), diameter
        )

    max_radius = diameter / 2
    min_radius = (
        min_diameter / 2 if min_diameter is not None else max_radius * BADGE_MIN_DIAMETER_RATIO
    )
    min_radius = min(min_radius, max_radius)

    positions, radii = _layout_badges(projected, max_radius=max_radius, min_radius=min_radius)

    inner = max_radius - BADGE_RING_WIDTH * 0.75
    parts: list[str] = [
        f'<defs><clipPath id="crestClip"><circle cx="0" cy="0" r="{inner:.2f}"/></clipPath></defs>'
    ]

    # Biggest first so the smallest badges end up on top and stay readable.
    for index in sorted(range(len(items)), key=lambda i: -radii[i]):
        item = items[index]
        x, y = positions[index]
        scale = radii[index] / max_radius
        ring = escape(colours.get(item.group, OUTLINE_STROKE))
        icon_url = item.icon_url or ""
        inline_href = crest_hrefs.get(icon_url) if _usable_crest_url(icon_url) else None
        if inline_href:
            href = escape(inline_href, quote=True)
            badge_inner = (
                f'<image x="{-inner:.2f}" y="{-inner:.2f}" width="{inner * 2:.2f}" '
                f'height="{inner * 2:.2f}" href="{href}" preserveAspectRatio="xMidYMid meet" '
                f'clip-path="url(#crestClip)"/>'
            )
        else:
            badge_inner = _badge_name_label_svg(item.name, inner_radius=inner)
        parts.append(
            f'<g transform="translate({x:.2f},{y:.2f}) scale({scale:.4f})">'
            f'<circle r="{max_radius:.2f}" fill="{BADGE_FILL}" stroke="{ring}" '
            f'stroke-width="{BADGE_RING_WIDTH}"/>'
            f"{badge_inner}"
            f"</g>"
        )
    return "".join(parts)


def _font_import_style_svg() -> str:
    return (
        "<style>@import url('https://fonts.googleapis.com/css2?"
        "family=Oswald:wght@500;600;700&amp;family=Barlow:wght@400;500;600&amp;display=swap');"
        "</style>"
    )


def _country_mask(itl_hierarchy: ITLHierarchy, *, simplified: bool = True) -> BaseGeometry:
    key = "simplified" if simplified else "geom"
    geoms = [
        itl_hierarchy["itl0_regions"][name][key]
        for name in COUNTRY_OUTLINES
        if name in itl_hierarchy["itl0_regions"]
    ]
    if not geoms:
        raise RuntimeError("No country outline geometries found for viewport")
    return unary_union(geoms)


def _country_bounds(country_geom: BaseGeometry) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = country_geom.bounds
    return minx, miny, maxx, maxy


def _mercator_y(lat: float) -> float:
    """Web Mercator northing in degree-equivalent units."""
    clamped = max(min(lat, 85.0), -85.0)
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(clamped) / 2)))


def _make_projector(
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    *,
    margin: float = MAP_MARGIN,
) -> Callable[[float, float], tuple[float, float]]:
    """Web Mercator projection scaled to fit *width* x *height* without distortion.

    A single scale factor is applied to both axes so the landmass keeps its true
    shape; leftover space is split evenly as centring offsets.
    """
    minx, miny, maxx, maxy = bounds
    y_min = _mercator_y(miny)
    y_max = _mercator_y(maxy)

    span_x = maxx - minx
    span_y = y_max - y_min
    if span_x <= 0 or span_y <= 0:
        raise ValueError(f"Degenerate map bounds: {bounds}")

    usable_w = width * (1 - 2 * margin)
    usable_h = height * (1 - 2 * margin)
    scale = min(usable_w / span_x, usable_h / span_y)

    offset_x = (width - span_x * scale) / 2
    offset_y = (height - span_y * scale) / 2

    def project(lon: float, lat: float) -> tuple[float, float]:
        x = offset_x + (lon - minx) * scale
        y = height - offset_y - (_mercator_y(lat) - y_min) * scale
        return x, y

    return project


def _coords_to_svg_d(coords: list[tuple[float, float]]) -> str:
    if len(coords) < 3:
        return ""
    parts: list[str] = []
    for i, (x, y) in enumerate(coords):
        parts.append(f"{'M' if i == 0 else 'L'}{x:.2f},{y:.2f}")
    parts.append("Z")
    return " ".join(parts)


def _ring_coords(
    ring: list, project: Callable[[float, float], tuple[float, float]]
) -> list[tuple[float, float]]:
    return [project(lon, lat) for lon, lat in ring]


def _geom_to_svg_path(
    geom: BaseGeometry, project: Callable[[float, float], tuple[float, float]]
) -> str:
    if geom.is_empty:
        return ""
    if isinstance(geom, Polygon):
        paths: list[str] = []
        ext = _coords_to_svg_d(_ring_coords(list(geom.exterior.coords), project))
        if ext:
            paths.append(ext)
        for interior in geom.interiors:
            hole = _coords_to_svg_d(_ring_coords(list(interior.coords), project))
            if hole:
                paths.append(hole)
        return " ".join(paths)
    if hasattr(geom, "geoms"):
        return " ".join(
            _geom_to_svg_path(g, project)
            for g in geom.geoms
            if isinstance(g, Polygon | MultiPolygon)
        )
    return ""


def _polygonal_only(geom: BaseGeometry) -> BaseGeometry:
    """Drop non-areal parts so a GeometryCollection still renders as shading.

    ``make_valid`` and ``intersection`` routinely return a GeometryCollection
    mixing polygons with stray lines/points; keeping only the polygons is what
    makes every league show up.
    """
    if geom.is_empty or isinstance(geom, Polygon | MultiPolygon):
        return geom
    if not hasattr(geom, "geoms"):
        return geom
    polys: list[Polygon] = []
    for part in geom.geoms:
        if isinstance(part, Polygon):
            polys.append(part)
        elif isinstance(part, MultiPolygon):
            polys.extend(part.geoms)
    if not polys:
        return geom
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def _make_valid(geom: BaseGeometry) -> BaseGeometry:
    if geom.is_empty:
        return geom
    if geom.is_valid:
        return _polygonal_only(geom)
    try:
        from shapely.validation import make_valid  # noqa: PLC0415

        fixed = make_valid(geom)
    except Exception:
        fixed = geom.buffer(0)
    if fixed.is_empty:
        return geom
    return _polygonal_only(fixed)


def _clip_geojson_to_mask(geojson: dict, mask: BaseGeometry) -> BaseGeometry:
    geom = _make_valid(shape(geojson))
    safe_mask = _make_valid(mask)
    try:
        clipped = geom.intersection(safe_mask)
    except Exception:
        clipped = geom.buffer(0).intersection(safe_mask.buffer(0))
    if clipped.is_empty:
        return clipped
    simplified = _make_valid(clipped).simplify(SVG_SIMPLIFY_TOLERANCE, preserve_topology=True)
    return simplified if not simplified.is_empty else clipped


def compute_tier_territories(
    items: list[MarkerItem],
    itl_hierarchy: ITLHierarchy,
    *,
    palette: list[str] | None = None,
) -> tuple[dict[str, BaseGeometry], dict[str, str]]:
    """Return clipped territory geometries and league colours for one tier."""
    if not items:
        return {}, {}

    config = MapConfig(
        title="",
        tier_entry_level=TIER_ENTRY_LEVELS,
        default_tier_entry_level="itl2",
        tier_floor_level=TIER_FLOOR_LEVELS,
        default_tier_floor_level="itl3",
        color_palette=palette or COLOR_PALETTE,
    )

    items_by_tier, _ = _items_to_placed(items)
    region_to_items = _assign_items_to_itl_regions(items_by_tier, itl_hierarchy)
    all_placed = [it for placed in items_by_tier.values() for it in placed]

    group_names = sorted({it["group"] for it in all_placed})
    group_colors = {grp: _pick_color(config.color_palette, j) for j, grp in enumerate(group_names)}

    group_geoms = _collect_group_geometries(
        all_placed, region_to_items, itl_hierarchy, group_colors, config
    )
    merged = _merge_territories(group_geoms)

    mask = _country_mask(itl_hierarchy)
    territories: dict[str, BaseGeometry] = {}
    colours: dict[str, str] = {}
    for grp, geojson in merged.items():
        clipped = _clip_geojson_to_mask(geojson, mask)
        if clipped.is_empty:
            continue
        territories[grp] = clipped
        colours[grp] = group_colors[grp]

    return territories, colours


def _stripe_pattern_svg(pattern_id: str, colour: str) -> str:
    """A 45-degree two-tone stripe pattern built from *colour* and a contrasting shade.

    Opaque both sides, unlike the interactive maps' overlay stripes: a static
    image has nothing underneath for transparent gaps to reveal.
    """
    return stripe_pattern_svg(
        pattern_id,
        stripe=contrasting_shade(colour, MERIT_STRIPE_CONTRAST),
        background=colour,
        period=MERIT_STRIPE_PERIOD,
        angle=MERIT_STRIPE_ANGLE,
    )


def _territory_fills(colours: dict[str, str], merit_groups: set[str]) -> tuple[str, dict[str, str]]:
    """Return ``(defs markup, group -> SVG fill value)``.

    Pyramid leagues fill flat; merit leagues fill with their own stripe pattern.
    """
    defs: list[str] = []
    fills: dict[str, str] = {}
    for index, group in enumerate(sorted(colours)):
        colour = colours[group]
        if group not in merit_groups:
            fills[group] = escape(colour, quote=True)
            continue
        pattern_id = f"meritStripe{index}"
        defs.append(_stripe_pattern_svg(pattern_id, colour))
        fills[group] = f"url(#{pattern_id})"
    return (f"<defs>{''.join(defs)}</defs>" if defs else ""), fills


def _merit_legend_svg(x: float, y: float) -> str:
    """Swatch and caption explaining what the striped territories are."""
    pattern = _stripe_pattern_svg("meritLegendSwatch", TEXT_MUTED)
    swatch_y = y - MERIT_LEGEND_SWATCH / 2
    text_x = x + MERIT_LEGEND_SWATCH + MERIT_LEGEND_GAP
    return (
        f"<defs>{pattern}</defs>"
        f'<rect x="{x:.2f}" y="{swatch_y:.2f}" width="{MERIT_LEGEND_SWATCH}" '
        f'height="{MERIT_LEGEND_SWATCH}" rx="4" fill="url(#meritLegendSwatch)" '
        f'stroke="{SEA_BG}" stroke-width="2"/>'
        f'<text x="{text_x:.2f}" y="{y:.2f}" font-family="{FONT_BODY}" '
        f'font-size="{MERIT_LEGEND_FONT_SIZE}" font-weight="500" fill="{TEXT_MUTED}" '
        f'dominant-baseline="central" paint-order="stroke" stroke="{SEA_BG}" '
        f'stroke-width="8" stroke-linejoin="round">{escape(MERIT_LEGEND_TEXT)}</text>'
    )


def _parse_svg_dimensions(svg_text: str) -> tuple[int, int]:
    m = re.search(r'width="(\d+(?:\.\d+)?)"\s+height="(\d+(?:\.\d+)?)"', svg_text)
    if not m:
        raise ValueError("Could not parse SVG width/height")
    return int(float(m.group(1))), int(float(m.group(2)))


def _wait_for_crest_images(page, *, timeout_ms: float) -> None:
    """Poll until every crest ``<image>`` has decoded, so none are captured blank."""
    done_js = """(() => {
        const nodes = document.querySelectorAll('image');
        for (const n of nodes) {
            if (!(n.getBoundingClientRect().width > 0)) return false;
        }
        return document.readyState === 'complete';
    })()"""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            if page.evaluate(done_js):
                return
        except Exception:
            return
        page.wait_for_timeout(150)
    logger.warning("Crest images still loading after %.0f ms; capturing anyway", timeout_ms)


def rasterise_svg_to_png(
    svg_path: Path,
    png_path: Path,
    *,
    scale: float = PNG_SCALE_DEFAULT,
    crest_timeout_ms: float = 30_000.0,
) -> None:
    """Render an SVG file to PNG using Playwright (Chromium)."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Playwright is required for PNG output. "
            "Run: pip install -r requirements-dev.txt && python -m playwright install chromium"
        ) from exc

    svg_text = svg_path.read_text(encoding="utf-8")
    svg_w, svg_h = _parse_svg_dimensions(svg_text)
    svg_uri = svg_path.resolve().as_uri()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={"width": svg_w, "height": svg_h},
                device_scale_factor=scale,
            )
            page.goto(svg_uri, wait_until="domcontentloaded", timeout=60_000)
            _wait_for_crest_images(page, timeout_ms=crest_timeout_ms)
            png_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(png_path), type="png", omit_background=False)
        finally:
            browser.close()


def render_tier_svg(
    *,
    season: str,
    tier_num: int,
    territories: dict[str, BaseGeometry],
    colours: dict[str, str],
    itl_hierarchy: ITLHierarchy,
    items: list[MarkerItem] | None = None,
    merit_groups: set[str] | None = None,
    crest_hrefs: dict[str, str] | None = None,
    badge_diameter: float | None = None,
    badge_min_diameter: float | None = None,
    width: int = IMAGE_WIDTH,
    height: int = IMAGE_HEIGHT,
) -> str:
    """Build the full SVG document for one tier graphic."""
    country_geom = _country_mask(itl_hierarchy)
    bounds = _country_bounds(country_geom)
    project = _make_projector(bounds, width, height)

    striped = (merit_groups or set()) & set(territories)
    stripe_defs, fills = _territory_fills(colours, striped)

    # Draw larger leagues first so smaller ones stay visible on top.
    sorted_groups = sorted(
        territories.keys(),
        key=lambda g: territories[g].area,
        reverse=True,
    )

    territory_paths: list[str] = [stripe_defs]
    for grp in sorted_groups:
        geom = territories[grp]
        path_d = _geom_to_svg_path(geom, project)
        if not path_d:
            continue
        fill = fills.get(grp, escape(UNASSIGNED_COLOR, quote=True))
        territory_paths.append(
            f'<path d="{path_d}" fill="{fill}" stroke="{TERRITORY_STROKE}" '
            f'stroke-width="0.8" stroke-linejoin="round"/>'
        )

    outline_d = _geom_to_svg_path(country_geom, project)
    outline_svg = ""
    if outline_d:
        outline_svg = (
            f'<path d="{outline_d}" fill="{LAND_BG}" stroke="{OUTLINE_STROKE}" '
            f'stroke-width="{COUNTRY_OUTLINE_WIDTH}" fill-rule="evenodd"/>'
        )

    badges_svg = _render_badges(
        items or [],
        colours,
        project,
        crest_hrefs or {},
        tier_num=tier_num,
        diameter=badge_diameter,
        min_diameter=badge_min_diameter,
    )

    text_x = round(width * CAPTION_X_RATIO)
    text_y0 = round(height * CAPTION_Y_RATIO)

    tier_name = _instagram_tier_name(tier_num, season)
    tier_name_y = text_y0 + TIER_NAME_Y_OFFSET
    stats_y = tier_name_y + TIER_STATS_BELOW_NAME
    stats_line = _tier_stats_line(len(territories), len(items or []))

    legend_svg = ""
    legend_drop = 0
    if striped:
        legend_svg = _merit_legend_svg(text_x, stats_y + MERIT_LEGEND_BELOW_STATS)
        legend_drop = MERIT_LEGEND_BELOW_STATS
    site_line_y = stats_y + legend_drop + SITE_URL_BELOW_STATS

    # A white halo keeps the caption legible if a territory reaches under it.
    halo = f'paint-order="stroke" stroke="{SEA_BG}" stroke-width="8" stroke-linejoin="round"'

    site_logo_y = site_line_y - SITE_LOGO_SIZE / 2
    site_text_x = text_x + SITE_LOGO_SIZE + SITE_URL_GAP
    logo_href = escape(_site_logo_href(), quote=True)

    text_lines = f"""
    <text x="{text_x}" y="{text_y0}" font-family="{FONT_HEADING}" font-size="54"
          font-weight="600" fill="{TEXT_PRIMARY}" {halo}>Rugby Union</text>
    <text x="{text_x}" y="{text_y0 + 62}" font-family="{FONT_HEADING}" font-size="46"
          font-weight="500" fill="{TEXT_MUTED}" {halo}>{escape(season)}</text>
    <text x="{text_x}" y="{text_y0 + 190}" font-family="{FONT_HEADING}" font-size="108"
          font-weight="700" fill="{TEXT_PRIMARY}" {halo}>Level {tier_num}</text>
    <text x="{text_x}" y="{tier_name_y}" font-family="{FONT_BODY}" font-size="42"
          font-weight="500" fill="{TEXT_MUTED}" {halo}>{escape(tier_name)}</text>
    <text x="{text_x}" y="{stats_y}" font-family="{FONT_BODY}"
          font-size="{TIER_STATS_FONT_SIZE}" font-weight="500" fill="{TEXT_MUTED}" {halo}>
          {escape(stats_line)}</text>
    {legend_svg}
    <image x="{text_x}" y="{site_logo_y:.2f}" width="{SITE_LOGO_SIZE}" height="{SITE_LOGO_SIZE}"
           href="{logo_href}"/>
    <text x="{site_text_x}" y="{site_line_y}" font-family="{FONT_BODY}"
          font-size="{SITE_URL_FONT_SIZE}" font-weight="500" fill="{TEXT_MUTED}"
          dominant-baseline="central" {halo}>{escape(SITE_HOST)}</text>
    """

    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        f"{_font_import_style_svg()}\n"
        f'<rect width="{width}" height="{height}" fill="{SEA_BG}"/>\n'
        f"{outline_svg}\n"
        f"{''.join(territory_paths)}\n"
        f"{badges_svg}\n"
        f"{text_lines}\n"
        f"</svg>\n"
    )


def _tier_slug(tier_num: int, season: str) -> str:
    stem = f"level_{tier_num:02d}"
    name = mens_current_tier_name(tier_num, season)
    # Merit-only levels have no pyramid name, so the suffix would just repeat the number.
    if name == f"Level {tier_num}":
        return stem
    return f"{stem}_{name.replace(' ', '_').lower()}"


def generate_tier_graphics(
    season: str,
    output_dir: Path,
    *,
    tier_nums: list[int] | None = None,
    write_png: bool = False,
    png_scale: float = PNG_SCALE_DEFAULT,
    badge_diameter: float | None = None,
    badge_min_diameter: float | None = None,
    boundary_detail: str | None = None,
    include_merit: bool = True,
) -> list[Path]:
    """Generate an Instagram map for each requested men's pyramid tier.

    With *include_merit* the county merit ladders are drawn alongside the
    national ones at the level they feed, shaded in diagonal stripes.
    """
    by_level = load_levels(season, include_merit=include_merit)

    boundary_paths = boundary_paths_for_detail(boundary_detail)
    if boundary_detail:
        logger.info(
            "Using ONS boundary detail %s from %s",
            boundary_detail.upper(),
            boundary_paths["countries"],
        )
    itl_hierarchy = load_itl_hierarchy(boundary_paths)
    all_items = [it for items in by_level.values() for it in items]
    preassign_itl_regions(all_items, itl_hierarchy)

    levels = sorted(by_level)
    if tier_nums is not None:
        wanted = frozenset(tier_nums)
        levels = [level for level in levels if level in wanted]

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    crest_hrefs: dict[str, str] = {}
    if badge_diameter is None or badge_diameter > 0:
        crest_px = _crest_inline_px(
            badge_diameter=badge_diameter,
            png_scale=png_scale if write_png else 1.0,
        )
        crest_hrefs = build_crest_href_map(
            [it.icon_url or "" for level in levels for it in by_level[level]],
            px=crest_px,
        )
        logger.info("Inlining crests at %d px", crest_px)

    for tier_num in levels:
        tier_items = by_level[tier_num]
        palette = _rotated_palette(tier_num)

        territories, colours = compute_tier_territories(tier_items, itl_hierarchy, palette=palette)
        if not territories:
            logger.warning("No territories for tier %d, skipping", tier_num)
            continue

        svg_text = render_tier_svg(
            season=season,
            tier_num=tier_num,
            territories=territories,
            colours=colours,
            itl_hierarchy=itl_hierarchy,
            items=tier_items,
            merit_groups=merit_group_names(tier_items),
            crest_hrefs=crest_hrefs,
            badge_diameter=badge_diameter,
            badge_min_diameter=badge_min_diameter,
        )

        slug = _tier_slug(tier_num, season)
        svg_path = output_dir / f"{slug}.svg"
        svg_path.write_text(svg_text, encoding="utf-8")
        written.append(svg_path)
        logger.info("Wrote %s (%d leagues)", svg_path.name, len(territories))

        if write_png:
            png_path = output_dir / f"{slug}.png"
            rasterise_svg_to_png(svg_path, png_path, scale=png_scale)
            written.append(png_path)
            logger.info("Wrote %s", png_path.name)

    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Instagram league maps, one per pyramid level (3:4 portrait)"
    )
    parser.add_argument(
        "--season",
        default=CURRENT_SEASON,
        help=f"Season to process (default: {CURRENT_SEASON})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: output/instagram/maps/<season>/)",
    )
    parser.add_argument(
        "--levels",
        type=int,
        nargs="+",
        metavar="N",
        help="Only these pyramid levels (e.g. --levels 7 8). Default: all men's tiers.",
    )
    parser.add_argument(
        "--png",
        action="store_true",
        help="Also rasterise to PNG via Playwright (requires requirements-dev.txt)",
    )
    parser.add_argument(
        "--png-scale",
        type=float,
        default=PNG_SCALE_DEFAULT,
        help=(
            f"Device scale factor for PNG output (default: {PNG_SCALE_DEFAULT:g}, "
            f"giving {int(IMAGE_WIDTH * PNG_SCALE_DEFAULT)}x{int(IMAGE_HEIGHT * PNG_SCALE_DEFAULT)} px)"
        ),
    )
    parser.add_argument(
        "--badge-size",
        type=float,
        default=None,
        metavar="PX",
        help=(
            f"Largest club crest diameter in canvas px at {IMAGE_WIDTH}px wide. "
            f"Default derives it per level from club spacing "
            f"({BADGE_DIAMETER_FLOOR}-{BADGE_DIAMETER_CEILING_UPPER}px for levels "
            f"1-{BADGE_UPPER_TIER_MAX}, {BADGE_DIAMETER_FLOOR}-{BADGE_DIAMETER_CEILING_LOWER}px "
            f"from level {BADGE_UPPER_TIER_MAX + 1} down). Use 0 to omit crests."
        ),
    )
    parser.add_argument(
        "--badge-min-size",
        type=float,
        default=None,
        metavar="PX",
        help=(
            "Smallest crest diameter for clubs in dense clusters "
            f"(default: {BADGE_MIN_DIAMETER_RATIO:g}x the badge size)."
        ),
    )
    parser.add_argument(
        "--no-merit",
        dest="merit",
        action="store_false",
        help=(
            "Draw only the national pyramid. By default county merit ladders are "
            "included at the level they feed, shaded in diagonal stripes."
        ),
    )
    parser.add_argument(
        "--boundary-detail",
        choices=list(VALID_DETAIL_LEVELS),
        default=None,
        metavar="LEVEL",
        help=(
            "ONS boundary generalisation (BFE/BFC/BGC/BUC). "
            "Default uses data/boundaries/ (BGC). "
            "Coarser levels remove estuary/river coastline detail."
        ),
    )
    args = parser.parse_args()

    setup_logging()
    output_dir = args.output_dir or (OUTPUT_ROOT / args.season)
    generate_tier_graphics(
        args.season,
        output_dir,
        tier_nums=args.levels,
        write_png=args.png,
        png_scale=args.png_scale,
        badge_diameter=args.badge_size,
        badge_min_diameter=args.badge_min_size,
        boundary_detail=args.boundary_detail,
        include_merit=args.merit,
    )
    from rugby.analysis.instagram_gallery import write_season_carousel

    carousel = write_season_carousel(
        output_dir,
        args.season,
        boundary_detail=args.boundary_detail,
    )
    if carousel is not None:
        logger.info("Wrote season carousel %s", carousel)


if __name__ == "__main__":
    main()
