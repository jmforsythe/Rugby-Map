"""Regenerate README ``example.png`` from a generated map HTML (default: Counties 1).

Simple pipeline:

1. Open ``dist/<season>/<map-file>`` in headless Chromium at high resolution (``--render-scale`` × output size).
2. Zoom with Leaflet (``--leaflet-zoom-delta``): script forces ``zoomSnap=0`` so fractional
   deltas (e.g. 0.5 vs 0.6) apply; Folium defaults would snap both to integer steps.
3. Hide header, legend, and Leaflet chrome only — same Positron ``light_all`` tiles as the live page.
4. Re-centre the map (default) on mainland England: longitude = box midpoint (WGS84); latitude =
   midpoint in Web Mercator space between the N/S parallels (fractionally north of the degree
   mean). ``--no-center-england-mainland`` keeps Folium’s centre.
5. Screenshot ``.folium-map`` with a symmetric ``--margin`` trim on each edge.
6. Downscale with Pillow to ``--width`` × ``--height``.

Requires::

    pip install -r requirements.txt
    pip install playwright Pillow
    python -m playwright install chromium

"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import logging
import math
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image

from core import setup_logging
from core.config import REPO_ROOT

logger = logging.getLogger(__name__)

_SEASON_PATTERN = re.compile(r"^[12]\d{3}-[12]\d{3}$")
_SAFE_HTML_BASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.html$")

DEFAULT_WIDTH = 709
DEFAULT_HEIGHT = 901
DEFAULT_RENDER_SCALE = 1.45
DEFAULT_LEAFLET_ZOOM_DELTA = 0.45

# Symmetric inset on ``.folium-map`` screenshot (fraction of panel width / height).
DEFAULT_MARGIN_FRAC = 0.022

# Mainland England WGS84 bounding box (approx. published extreme points; excludes islands).
# View centre latitude uses the midpoint in Web Mercator y between N/S parallels (projection),
# plus arithmetic mean longitude.
_ENG_MAINLAND_SOUTH = 49.957  # Lizard area (S mainland)
_ENG_MAINLAND_NORTH = 55.811  # Scottish border (N mainland)
_ENG_MAINLAND_WEST = -5.720  # W Cornwall
_ENG_MAINLAND_EAST = 1.774  # NE coast (e.g. Ness Point area)

_CHROME_CSS = """
#mapHeader,
.map-header,
.rugby-theme-float {
  display: none !important;
  visibility: hidden !important;
}
.map-legend {
  display: none !important;
  visibility: hidden !important;
}
.leaflet-control-container {
  display: none !important;
}
.leaflet-top.leaflet-left,
.leaflet-top.leaflet-right {
  top: 0 !important;
}
.leaflet-bottom.leaflet-left,
.leaflet-bottom.leaflet-right {
  bottom: 0 !important;
}
"""

_INVALIDATE_LEAFLET_JS = """() => {
  for (const k of Object.keys(window)) {
    if (!k.startsWith("map_")) {
      continue;
    }
    const v = window[k];
    if (v && typeof v.invalidateSize === "function" &&
        typeof v.getCenter === "function") {
      v.invalidateSize({ animate: false });
      break;
    }
  }
}"""

_TILES_READY_JS = """() => {
  const imgs = document.querySelectorAll('.leaflet-tile-pane img.leaflet-tile');
  if (imgs.length < 6) {
    return false;
  }
  return Array.from(imgs).every(
    (img) => img.complete && img.naturalWidth > 0
  );
}"""

_DISABLE_LEAFLET_ZOOM_SNAP_JS = """() => {
  for (const k of Object.keys(window)) {
    if (!k.startsWith("map_")) {
      continue;
    }
    const m = window[k];
    if (!m || !m.options) {
      continue;
    }
    m.options.zoomSnap = 0;
    return true;
  }
  return false;
}"""


def _mercator_vertical_mid_latitude(lat_south: float, lat_north: float) -> float:
    """Latitude whose spherical-Mercator y lies midway between the two parallels (degrees)."""

    phi_s = math.radians(lat_south)
    phi_n = math.radians(lat_north)
    y_s = math.log(math.tan(math.pi / 4 + phi_s / 2))
    y_n = math.log(math.tan(math.pi / 4 + phi_n / 2))
    y_mid = (y_s + y_n) / 2
    return math.degrees(2 * math.atan(math.exp(y_mid)) - math.pi / 2)


def _england_mainland_box_midpoint() -> tuple[float, float]:
    lat = _mercator_vertical_mid_latitude(_ENG_MAINLAND_SOUTH, _ENG_MAINLAND_NORTH)
    lng = (_ENG_MAINLAND_WEST + _ENG_MAINLAND_EAST) / 2.0
    return lat, lng


def _leaflet_set_center_keep_zoom_js(lat: float, lng: float) -> str:
    plat = json.dumps(float(lat))
    plng = json.dumps(float(lng))
    return f"""() => {{
  for (const k of Object.keys(window)) {{
    if (!k.startsWith("map_")) {{
      continue;
    }}
    const m = window[k];
    if (m && typeof m.getZoom === "function" && typeof m.setView === "function") {{
      const z = m.getZoom();
      m.setView([{plat}, {plng}], z, {{ animate: false }});
      return true;
    }}
  }}
  return false;
}}"""


def _leaflet_zoom_by_js(delta: float) -> str:
    n = json.dumps(float(delta))
    return f"""() => {{
  for (const k of Object.keys(window)) {{
    if (!k.startsWith("map_")) {{
      continue;
    }}
    const m = window[k];
    if (m && typeof m.setZoom === "function" && typeof m.getZoom === "function") {{
      m.setZoom(m.getZoom() + {n}, {{ animate: false }});
      break;
    }}
  }}
}}"""


def _parse_zoom_delta(s: str) -> float:
    try:
        d = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if abs(d) > 24:
        raise argparse.ArgumentTypeError("|delta| must be ≤ 24")
    return d


def _validate_season(value: str) -> str:
    if not _SEASON_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(f"season must match YYYY-YYYY, got {value!r}")
    return value


def _validate_map_file(value: str) -> str:
    if not _SAFE_HTML_BASE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"disallowed map filename {value!r}")
    return value


def _clamp_clip_viewport(
    x: float, y: float, w: float, h: float, vw: int, vh: int
) -> dict[str, int]:
    nx = max(0, min(x, vw - 1))
    ny = max(0, min(y, vh - 1))
    nw = max(1, min(w, vw - nx))
    nh = max(1, min(h, vh - ny))
    return {"x": int(nx), "y": int(ny), "width": int(round(nw)), "height": int(round(nh))}


def main() -> int:
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Screenshot a Folium rugby map HTML (default Counties 1) to example PNG size.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--season", type=_validate_season, default="2025-2026")
    parser.add_argument("--map-file", type=_validate_map_file, default="Counties_1.html")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument(
        "--repo-root",
        type=lambda s: Path(s).expanduser().resolve(strict=False),
        default=None,
    )
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument(
        "--render-scale",
        type=float,
        default=DEFAULT_RENDER_SCALE,
        help="High-res viewport multiplier vs output (width × scale, etc.).",
    )
    parser.add_argument(
        "--device-scale-factor",
        type=float,
        default=2.0,
        metavar="N",
        help="Chromium device pixel ratio before Pillow downscale.",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=DEFAULT_MARGIN_FRAC,
        metavar="FRAC",
        help=(
            "Symmetric fractional crop on each side of the map panel after zoom "
            "(tightens frame on England; try 0.015–0.03)."
        ),
    )
    parser.add_argument(
        "--leaflet-zoom-delta",
        type=_parse_zoom_delta,
        default=DEFAULT_LEAFLET_ZOOM_DELTA,
        help=(
            "Added to Leaflet zoom after load (fractions honoured: zoomSnap patched to 0 for capture)."
        ),
    )
    parser.add_argument("--zoom-settle-ms", type=int, default=1600)
    parser.add_argument("--settle-ms", type=int, default=2800)
    parser.add_argument(
        "--center-england-mainland",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "After zoom, set view centre: lng = mainland box midpoint (WGS84); lat = Web Mercator "
            "vertical midpoint between N/S box parallels. Disable with --no-center-england-mainland."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    opts = parser.parse_args()

    repo_root = Path(opts.repo_root or REPO_ROOT).resolve(strict=False)
    html_path = (repo_root / "dist" / opts.season / opts.map_file).resolve(strict=False)
    if not html_path.is_file():
        logger.error("Missing HTML: %s (build maps first)", html_path)
        return 1

    dest = (
        Path(opts.output).expanduser().resolve(strict=False)
        if opts.output
        else repo_root / "example.png"
    )
    url = html_path.resolve().as_uri()

    vw = max(480, int(round(opts.width * opts.render_scale)))
    vh = max(480, int(round(opts.height * opts.render_scale)))
    dpf = opts.device_scale_factor
    mrg = opts.margin

    if opts.render_scale <= 0 or dpf <= 0:
        logger.error("--render-scale and --device-scale-factor must be positive")
        return 1
    if not 0.0 <= mrg <= 0.42:
        logger.error("--margin must be between 0 and 0.42")
        return 1

    elat, elng = _england_mainland_box_midpoint()
    logger.info(
        "Screenshot %s -> viewport %dx%d dpr=%s zoom=%+g england_centre=%s margin=%.4f -> %dx%d",
        html_path.name,
        vw,
        vh,
        dpf,
        opts.leaflet_zoom_delta,
        opts.center_england_mainland,
        mrg,
        opts.width,
        opts.height,
    )

    if opts.dry_run:
        print(url)
        return 0

    try:
        _pw_sync = importlib.import_module("playwright.sync_api")
    except ImportError:
        logger.error("Install Playwright and run: python -m playwright install chromium")
        return 2

    playwright_timeout = _pw_sync.TimeoutError
    sync_playwright = _pw_sync.sync_playwright

    init_js = """\
try { localStorage.setItem('rugbyMapTheme', 'light'); } catch (e) {}
document.documentElement.setAttribute('data-rugby-effective', 'light');\
"""

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=dpf)
            page = ctx.new_page()
            page.add_init_script(init_js)

            page.goto(url, wait_until="domcontentloaded", timeout=180_000)
            page.add_style_tag(content=_CHROME_CSS)

            try:
                page.wait_for_function(_TILES_READY_JS, timeout=120_000)
            except playwright_timeout:
                logger.warning("Tile wait timed out; continuing")

            if opts.settle_ms > 0:
                page.wait_for_timeout(opts.settle_ms)

            page.evaluate(_INVALIDATE_LEAFLET_JS)
            page.wait_for_timeout(400)

            if opts.leaflet_zoom_delta != 0:
                snapped = page.evaluate(_DISABLE_LEAFLET_ZOOM_SNAP_JS)
                if not snapped:
                    logger.warning(
                        "Could not patch Leaflet zoomSnap; fractional zoom may snap to integers"
                    )
                page.evaluate(_leaflet_zoom_by_js(opts.leaflet_zoom_delta))
                page.wait_for_timeout(250)
                try:
                    page.wait_for_function(_TILES_READY_JS, timeout=120_000)
                except playwright_timeout:
                    logger.warning("Post-zoom tile wait timed out; continuing")
                if opts.zoom_settle_ms > 0:
                    page.wait_for_timeout(opts.zoom_settle_ms)

            if opts.center_england_mainland:
                cen_js = _leaflet_set_center_keep_zoom_js(elat, elng)
                if page.evaluate(cen_js):
                    logger.info(
                        "View centre -> England lng midpoint, Mercator N/S midpoint lat %.4f, lng %.4f",
                        elat,
                        elng,
                    )
                else:
                    logger.warning("Could not set view centre on Leaflet map")
                page.wait_for_timeout(300)
                try:
                    page.wait_for_function(_TILES_READY_JS, timeout=120_000)
                except playwright_timeout:
                    logger.warning("Post-centre tile wait timed out; continuing")
                if opts.zoom_settle_ms > 0:
                    page.wait_for_timeout(opts.zoom_settle_ms)

            map_el = page.locator(".folium-map").first
            map_el.wait_for(state="visible", timeout=60_000)
            box = map_el.bounding_box()
            if box is None:
                logger.error(".folium-map bounding box missing")
                return 3

            x0 = box["x"] + mrg * box["width"]
            y0 = box["y"] + mrg * box["height"]
            ww = box["width"] * (1.0 - 2.0 * mrg)
            hh = box["height"] * (1.0 - 2.0 * mrg)

            clip = _clamp_clip_viewport(x0, y0, ww, hh, vw, vh)
            dest.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(tmp_path), clip=clip)
            browser.close()

        with Image.open(tmp_path) as im:
            im.resize((opts.width, opts.height), Image.Resampling.LANCZOS).save(
                dest, format="PNG", optimize=True
            )

    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()

    logger.info("Wrote %s", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
