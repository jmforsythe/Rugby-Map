"""
Generic geographic map generation module.

Plots groups of geocoded points on Folium/Leaflet maps with territory shading,
marker clustering, legends, and layer controls. Has no knowledge of any specific
sport, league structure, or data source -- the caller provides pre-built
MarkerItem objects and a MapConfig with all project-specific settings.
"""

import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, TypedDict, cast

import folium
import numpy as np
from branca.element import MacroElement
from folium.plugins import FeatureGroupSubGroup, MarkerCluster
from folium.template import Template as FoliumTemplate
from scipy.spatial import Voronoi
from shapely.affinity import scale as affine_scale
from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import PreparedGeometry, prep

from core.asset_utils import rewrite_cdn_urls_in_html
from core.basemap_tiles import (
    CARTO_THEME_MARK_DARK,
    CARTO_THEME_MARK_LIGHT,
    CARTO_TILE_URL_LIGHT,
    folium_carto_attribution,
)
from core.config import get_config, get_resource_hints_html
from core.json_utils import write_compact_json
from core.patterns import stripe_css_gradient, stripe_pattern_svg

logger = logging.getLogger(__name__)

# Leaflet zoom granularity: quarter steps via init options + runtime patch + stepper UI.
MAP_ZOOM_SNAP = 0.25
MAP_ZOOM_DELTA = 0.25

PRIMARY_STRUCTURE = "primary"
"""Structure name for items that belong to the main league pyramid."""

TERRITORY_FILL_OPACITY = 0.6
HATCHED_FILL_OPACITY = 0.8
"""Stripes carry less ink than a solid fill, so they need a touch more opacity
to read at the same weight as the structure beneath them."""

HATCH_PANE = "territoryHatch"
HATCH_PANE_Z_INDEX = 450
"""Above Leaflet's overlayPane (400) and below its markerPane (600), so hatched
territories always stay on top of solid ones however the user toggles layers."""

CREST_PLACEHOLDER_SRC = (
    "data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 "
    "width=%2730%27 height=%2730%27 viewBox=%270 0 30 30%27/%3E"
)
PRESENTATION_READY_FALLBACK_MS = 15_000
"""Unblock deferred crest loading if territory fetch/render never completes."""


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (WGS84 sphere, R=6371)."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371.0 * c


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass
class MarkerItem:
    """A single point to place on the map."""

    name: str
    latitude: float
    longitude: float
    group: str
    tier: str
    tier_num: int
    icon_url: str | None = None
    popup_html: str | None = None
    category: str | None = None
    extra: dict[str, Any] | None = None
    itl0: str | None = None
    itl1: str | None = None
    itl2: str | None = None
    itl3: str | None = None
    lad: str | None = None
    ward: str | None = None
    # Which parallel league structure this item belongs to. Territory shading is
    # computed independently per structure, so leagues in different structures
    # are allowed to cover the same ground instead of carving it up.
    structure: str = PRIMARY_STRUCTURE


@dataclass
class MapConfig:
    """Project-specific settings passed by the caller."""

    title: str
    color_palette: list[str]
    center: tuple[float, float] = (52.5, -1.5)
    zoom: int = 7
    show_debug: bool = True
    # Optional override for the document <title>. When None, `title` is used.
    # Lets callers keep `title` short (for legend/header) while giving the HTML
    # <title> a longer SEO-friendly form.
    html_title: str | None = None
    tier_entry_level: dict[int, str] = field(default_factory=dict)
    default_tier_entry_level: str = "itl2"
    tier_floor_level: dict[int, str] = field(default_factory=dict)
    default_tier_floor_level: str = "itl3"
    use_inline_boundaries: bool = True
    inline_boundaries_file: str = "dist/shared/boundaries.json"
    shared_boundaries_path: str = "../shared"
    # When True, territory shading GeoJSON is written to a per-map sidecar file
    # (territories_sidecar_name, saved alongside output_path) and fetched on
    # demand instead of being embedded inline in the map HTML.
    external_territories: bool = False
    territories_sidecar_name: str = "territories.json"
    fallback_icon_url: str | None = None
    header_elements: list[str] = field(default_factory=list)
    body_elements: list[str] = field(default_factory=list)
    # Structures drawn as diagonal stripes over the solid ones, for maps showing
    # two league structures that run in parallel over the same territory.
    hatched_structures: tuple[str, ...] = ()
    # Whether the marker layer starts toggled on. Maps whose main draw is the
    # territory shading (e.g. the Constituent Body map) start with markers
    # hidden so the shading reads clearly; per-item detail is still a click away.
    markers_shown_by_default: bool = True
    # Draw each territory's group name as a text label at its centroid. Suited
    # to maps with few, large regions (e.g. Constituent Bodies) -- would be
    # unreadable clutter on maps with many small tier/league territories.
    label_territories: bool = False
    # Append "- <item count>" to the legend title. Off for maps where the raw
    # club count isn't a meaningful headline figure (e.g. Constituent Bodies).
    show_legend_item_count: bool = True


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


class _PlacedItem(TypedDict):
    """Internal wrapper that adds ITL region assignments to a marker."""

    name: str
    latitude: float
    longitude: float
    group: str
    tier: str
    tier_num: int
    icon_url: str | None
    popup_html: str | None
    category: str | None
    structure: str
    itl0: str | None
    itl1: str | None
    itl2: str | None
    itl3: str | None
    lad: str | None
    ward: str | None


class ITLRegionGeom(TypedDict):
    """ITL region with geospatial data"""

    name: str
    code: str | None
    geom: BaseGeometry
    simplified: BaseGeometry
    prepared: PreparedGeometry
    centroid: Point


class ITLHierarchy(TypedDict):
    itl3_regions: dict[str, ITLRegionGeom]
    itl2_regions: dict[str, ITLRegionGeom]
    itl1_regions: dict[str, ITLRegionGeom]
    itl0_regions: dict[str, ITLRegionGeom]
    lad_regions: dict[str, ITLRegionGeom]
    ward_regions: dict[str, ITLRegionGeom]
    itl3_to_itl2: dict[str, str]
    itl2_to_itl1: dict[str, str]
    lad_to_itl3: dict[str, str]
    ward_to_lad: dict[str, str | None]
    itl1_to_itl2s: dict[str, list[str]]
    itl0_to_itl1s: dict[str, list[str]]
    itl2_to_itl3s: dict[str, list[str]]
    itl3_to_lads: dict[str, list[str]]
    lad_to_wards: dict[str, list[str]]


class _RegionToItems(TypedDict):
    itl0: dict[str, list[_PlacedItem]]
    itl1: dict[str, list[_PlacedItem]]
    itl2: dict[str, list[_PlacedItem]]
    itl3: dict[str, list[_PlacedItem]]
    lad: dict[str, list[_PlacedItem]]
    ward: dict[str, list[_PlacedItem]]


# ---------------------------------------------------------------------------
# Boundary loading
# ---------------------------------------------------------------------------


def _load_geojson(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_SIMPLIFY_TOLERANCE = 0.001


def _load_lookup_rows(path: str | Path) -> list[dict[str, str]]:
    """Load a saved ONS lookup table (list of attribute dicts)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Lookup file {path} must contain a JSON array")
    return data


def load_itl_hierarchy(paths: dict[str, str]) -> ITLHierarchy:
    """Load GeoJSON boundaries and compute hierarchy links.

    *paths* maps level names to file paths::

        {"itl3": "...", "itl2": "...", "itl1": "...",
         "countries": "...", "lad": "...", "wards": "...",
         "lad_to_itl_lookup": "...",   # optional, ONS authoritative lookup
         "ward_to_lad_lookup": "..."}  # optional, ONS authoritative lookup

    The two lookup files come from
    :func:`core.boundaries.download_arcgis_table` and are the preferred source
    for LAD<->ITL3 and Ward<->LAD relationships.  When a lookup file is
    missing or absent, this function falls back to the legacy centroid /
    feature-property heuristics so older deployments keep working.
    """
    itl3_data = _load_geojson(paths["itl3"])
    itl2_data = _load_geojson(paths["itl2"])
    itl1_data = _load_geojson(paths["itl1"])
    itl0_data = _load_geojson(paths["countries"])
    lad_data = _load_geojson(paths["lad"])
    wards_path = Path(paths["wards"])
    if wards_path.exists():
        ward_data = _load_geojson(wards_path)
    else:
        logger.warning("Wards file %s not found, skipping ward-level hierarchy", wards_path)
        ward_data = {"features": []}

    itl3_regions: dict[str, ITLRegionGeom] = {}
    for feat in itl3_data["features"]:
        geom = shape(feat["geometry"])
        itl3_regions[feat["properties"]["ITL325NM"]] = {
            "name": feat["properties"]["ITL325NM"],
            "code": feat["properties"].get("ITL325CD"),
            "geom": geom,
            "simplified": geom.simplify(_SIMPLIFY_TOLERANCE, preserve_topology=True),
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }

    itl2_regions: dict[str, ITLRegionGeom] = {}
    for feat in itl2_data["features"]:
        geom = shape(feat["geometry"])
        itl2_regions[feat["properties"]["ITL225NM"]] = {
            "name": feat["properties"]["ITL225NM"],
            "code": feat["properties"].get("ITL225CD"),
            "geom": geom,
            "simplified": geom.simplify(_SIMPLIFY_TOLERANCE, preserve_topology=True),
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }

    itl1_regions: dict[str, ITLRegionGeom] = {}
    for feat in itl1_data["features"]:
        geom = shape(feat["geometry"])
        itl1_regions[feat["properties"]["ITL125NM"]] = {
            "name": feat["properties"]["ITL125NM"],
            "code": feat["properties"].get("ITL125CD"),
            "geom": geom,
            "simplified": geom.simplify(_SIMPLIFY_TOLERANCE, preserve_topology=True),
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }

    itl0_regions: dict[str, ITLRegionGeom] = {}
    for feat in itl0_data["features"]:
        geom = shape(feat["geometry"])
        itl0_regions[feat["properties"]["CTRY24NM"]] = {
            "name": feat["properties"]["CTRY24NM"],
            "code": feat["properties"].get("CTRY24CD"),
            "geom": geom,
            "simplified": geom.simplify(_SIMPLIFY_TOLERANCE, preserve_topology=True),
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }

    lad_regions: dict[str, ITLRegionGeom] = {}
    for feat in lad_data["features"]:
        props = feat["properties"]
        lad_code = props.get("LAD25CD")
        if not lad_code:
            continue
        geom = shape(feat["geometry"])
        lad_regions[lad_code] = {
            "name": props["LAD25NM"],
            "code": lad_code,
            "geom": geom,
            "simplified": geom.simplify(_SIMPLIFY_TOLERANCE, preserve_topology=True),
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }

    ward_regions: dict[str, ITLRegionGeom] = {}
    ward_to_lad_geojson: dict[str, str | None] = {}
    for feat in ward_data["features"]:
        props = feat["properties"]
        ward_code = props.get("WD25CD")
        if not ward_code:
            continue
        geom = shape(feat["geometry"])
        ward_regions[ward_code] = {
            "name": props["WD25NM"],
            "code": ward_code,
            "geom": geom,
            "simplified": geom.simplify(_SIMPLIFY_TOLERANCE, preserve_topology=True),
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }
        ward_to_lad_geojson[ward_code] = props.get("LAD25CD")

    itl1_by_code = {r["code"]: r["name"] for r in itl1_regions.values() if r["code"]}
    itl2_by_code = {r["code"]: r["name"] for r in itl2_regions.values() if r["code"]}

    itl3_to_itl2: dict[str, str] = {}
    for itl3 in itl3_regions.values():
        if itl3["code"] and len(itl3["code"]) >= 4:
            parent = itl3["code"][:4]
            if parent in itl2_by_code:
                itl3_to_itl2[itl3["name"]] = itl2_by_code[parent]

    itl2_to_itl1: dict[str, str] = {}
    for itl2 in itl2_regions.values():
        if itl2["code"] and len(itl2["code"]) >= 3:
            parent = itl2["code"][:3]
            if parent in itl1_by_code:
                itl2_to_itl1[itl2["name"]] = itl1_by_code[parent]

    itl1_to_itl2s: dict[str, list[str]] = {}
    for itl2_name, itl1_name in itl2_to_itl1.items():
        itl1_to_itl2s.setdefault(itl1_name, []).append(itl2_name)

    itl0_to_itl1s: dict[str, list[str]] = {}
    for itl1_name, itl1 in itl1_regions.items():
        for itl0_name, itl0 in itl0_regions.items():
            if itl0["prepared"].contains(itl1["centroid"]):
                itl0_to_itl1s.setdefault(itl0_name, []).append(itl1_name)
                break

    itl2_to_itl3s: dict[str, list[str]] = {}
    for itl3_name, itl2_name in itl3_to_itl2.items():
        itl2_to_itl3s.setdefault(itl2_name, []).append(itl3_name)

    # ------------------------------------------------------------------
    # LAD <-> ITL3 mapping
    #
    # Preferred source: the ONS "LAD (April 2025) to LAU1 to ITL3 to ITL2 to
    # ITL1" lookup table. This is authoritative and avoids the centroid-based
    # bug where coastal LADs (Torbay, Sefton, Maldon, ...) have offshore
    # geometric centroids that don't fall in any ITL polygon.
    # ------------------------------------------------------------------
    lad_to_itl_lookup_path = paths.get("lad_to_itl_lookup")
    lad_to_itl3: dict[str, str] = {}
    itl3_to_lads: dict[str, list[str]] = {}
    lookup_hits = 0

    if lad_to_itl_lookup_path and Path(lad_to_itl_lookup_path).exists():
        logger.debug("Assigning LADs to ITL regions from ONS lookup...")
        rows = _load_lookup_rows(lad_to_itl_lookup_path)
        for row in rows:
            lad_code = row.get("LAD25CD")
            itl3_name = row.get("ITL325NM")
            if not lad_code or not itl3_name:
                continue
            if lad_code not in lad_regions:
                # Lookup covers all UK LADs, but our LAD GeoJSON may have been
                # filtered (or the LAD was retired); silently skip.
                continue
            if itl3_name not in itl3_regions:
                # Authoritative ITL3 name we don't have geometry for; record
                # the link anyway so item-level assignment still succeeds.
                pass
            lad_to_itl3[lad_code] = itl3_name
            itl3_to_lads.setdefault(itl3_name, []).append(lad_code)
            lookup_hits += 1
        logger.debug("  ONS lookup matched %d LADs", lookup_hits)
    else:
        logger.debug("LAD->ITL lookup not provided, falling back to centroid logic")

    # Fallback for any LADs not in the lookup (e.g. injected Isle of Man,
    # Jersey, Guernsey synthetic features, or future LADs the lookup hasn't
    # caught up with).
    missing_lads = [code for code in lad_regions if code not in lad_to_itl3]
    if missing_lads:
        logger.debug("  Centroid fallback for %d LAD(s)", len(missing_lads))
        for lad_code in missing_lads:
            lad = lad_regions[lad_code]
            centroid = lad["centroid"]
            found_itl1 = None
            for itl1 in itl1_regions.values():
                if itl1["prepared"].contains(centroid):
                    found_itl1 = itl1["name"]
                    break
            if not found_itl1:
                continue
            found_itl2 = None
            for itl2_name in itl1_to_itl2s.get(found_itl1, []):
                if itl2_regions[itl2_name]["prepared"].contains(centroid):
                    found_itl2 = itl2_name
                    break
            if not found_itl2:
                continue
            for itl3_name in itl2_to_itl3s.get(found_itl2, []):
                if itl3_regions[itl3_name]["prepared"].contains(centroid):
                    lad_to_itl3[lad_code] = itl3_name
                    itl3_to_lads.setdefault(itl3_name, []).append(lad_code)
                    break

    logger.debug("  Assigned %d of %d LADs to ITL3 regions", len(lad_to_itl3), len(lad_regions))
    logger.debug("  %d ITL3 regions contain LADs", len(itl3_to_lads))

    # ------------------------------------------------------------------
    # Ward <-> LAD mapping
    #
    # Preferred source: ONS "Ward to Registration District to LAD" lookup.
    # Covers England + Wales; for Scottish/NI wards or injected island wards
    # we fall back to the LAD25CD attribute on the ward GeoJSON feature.
    # ------------------------------------------------------------------
    ward_to_lad_lookup_path = paths.get("ward_to_lad_lookup")
    ward_to_lad: dict[str, str | None] = {}

    if ward_to_lad_lookup_path and Path(ward_to_lad_lookup_path).exists():
        logger.debug("Assigning wards to LADs from ONS lookup...")
        rows = _load_lookup_rows(ward_to_lad_lookup_path)
        for row in rows:
            ward_code = row.get("WD25CD")
            lad_code = row.get("LAD25CD")
            if not ward_code:
                continue
            ward_to_lad[ward_code] = lad_code or None
    else:
        logger.debug("Ward->LAD lookup not provided, using GeoJSON properties")

    # Fill gaps from GeoJSON ward properties for wards the EW lookup doesn't
    # cover (Scotland, NI, injected islands).
    for ward_code, lad_code in ward_to_lad_geojson.items():
        ward_to_lad.setdefault(ward_code, lad_code)

    logger.debug("Building LAD->wards index...")
    lad_to_wards: dict[str, list[str]] = {}
    for ward_code in ward_regions:
        parent = ward_to_lad.get(ward_code)
        if parent and parent in lad_regions:
            lad_to_wards.setdefault(parent, []).append(ward_code)

    logger.debug("  Assigned %d of %d wards to LADs", len(ward_to_lad), len(ward_regions))
    logger.debug("  %d LADs contain wards", len(lad_to_wards))

    return {
        "itl3_regions": itl3_regions,
        "itl2_regions": itl2_regions,
        "itl1_regions": itl1_regions,
        "itl0_regions": itl0_regions,
        "lad_regions": lad_regions,
        "ward_regions": ward_regions,
        "itl3_to_itl2": itl3_to_itl2,
        "itl2_to_itl1": itl2_to_itl1,
        "lad_to_itl3": lad_to_itl3,
        "ward_to_lad": ward_to_lad,
        "itl1_to_itl2s": itl1_to_itl2s,
        "itl0_to_itl1s": itl0_to_itl1s,
        "itl2_to_itl3s": itl2_to_itl3s,
        "itl3_to_lads": itl3_to_lads,
        "lad_to_wards": lad_to_wards,
    }


def preassign_itl_regions(items: list[MarkerItem], itl_hierarchy: ITLHierarchy) -> None:
    """Pre-compute ITL region assignments for all items in a single pass.

    Mutates each item's ``itl0``–``ward`` fields so that subsequent calls to
    :func:`generate_single_group_map` / :func:`generate_multi_group_map` can
    skip per-map spatial queries.  Identical ``(latitude, longitude)`` pairs
    are only queried once.
    """
    itl0_regions = itl_hierarchy["itl0_regions"]
    itl1_regions = itl_hierarchy["itl1_regions"]
    itl2_regions = itl_hierarchy["itl2_regions"]
    itl3_regions = itl_hierarchy["itl3_regions"]
    lad_regions = itl_hierarchy["lad_regions"]
    ward_regions = itl_hierarchy["ward_regions"]
    itl1_to_itl2s = itl_hierarchy["itl1_to_itl2s"]
    itl2_to_itl3s = itl_hierarchy["itl2_to_itl3s"]
    itl3_to_lads = itl_hierarchy["itl3_to_lads"]
    lad_to_wards = itl_hierarchy["lad_to_wards"]

    seen: dict[tuple[float, float], tuple[str | None, ...]] = {}

    for item in items:
        key = (item.latitude, item.longitude)
        if key in seen:
            item.itl0, item.itl1, item.itl2, item.itl3, item.lad, item.ward = seen[key]
            continue

        point = Point(item.longitude, item.latitude)
        _itl0 = _itl1 = _itl2 = _itl3 = _lad = _ward = None

        for r in itl0_regions.values():
            if r["prepared"].contains(point):
                _itl0 = r["name"]
                break

        for r in itl1_regions.values():
            if r["prepared"].contains(point):
                _itl1 = r["name"]
                break

        if _itl1:
            for name in itl1_to_itl2s.get(_itl1, []):
                if itl2_regions[name]["prepared"].contains(point):
                    _itl2 = name
                    break

        if _itl2:
            for name in itl2_to_itl3s.get(_itl2, []):
                if itl3_regions[name]["prepared"].contains(point):
                    _itl3 = name
                    break

        if _itl3:
            for code in itl3_to_lads.get(_itl3, []):
                lad = lad_regions.get(code)
                if lad and lad["prepared"].contains(point):
                    _lad = code
                    break

        if _lad and ward_regions:
            for code in lad_to_wards.get(_lad, []):
                ward = ward_regions.get(code)
                if ward and ward["prepared"].contains(point):
                    _ward = code
                    break

        item.itl0, item.itl1, item.itl2, item.itl3, item.lad, item.ward = (
            _itl0,
            _itl1,
            _itl2,
            _itl3,
            _lad,
            _ward,
        )
        seen[key] = (_itl0, _itl1, _itl2, _itl3, _lad, _ward)

    logger.debug(
        "Pre-assigned ITL regions for %d items (%d unique locations)",
        len(items),
        len(seen),
    )


def export_shared_boundaries(
    paths: dict[str, str],
    output_dir: str = "dist/shared",
    country_names: list[str] | None = None,
    skip_if_exists: bool = False,
    itl_hierarchy: ITLHierarchy | None = None,
) -> None:
    """Export simplified boundary data to a shared JSON file for client-side use.

    *paths* uses the same format as :func:`load_itl_hierarchy`.
    *country_names* lists country features to include in the outline layer.
    When provided, ITL/LAD/ward boundaries are also filtered to only include
    features whose centroid falls within those countries.

    If *itl_hierarchy* is supplied, pre-simplified geometries are used directly
    instead of re-loading and simplifying the raw GeoJSON files.
    """
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)
    output_path = output_dir_path / "boundaries.json"
    if skip_if_exists and output_path.exists():
        logger.debug("Shared boundary file already exists at %s, skipping export.", output_path)
        return

    boundary_data: dict[str, Any] = {
        "countries": {},
        "itl1": None,
        "itl2": None,
        "itl3": None,
        "lad": None,
        "wards": None,
    }

    if itl_hierarchy is not None:
        # Use pre-simplified geometries from the already-loaded hierarchy.
        # Build country filter from the itl0 (country) regions.
        country_set = set(country_names or [])
        country_geoms: list[BaseGeometry] = []
        for name, region in itl_hierarchy["itl0_regions"].items():
            if name in country_set:
                boundary_data["countries"][name] = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": mapping(region["simplified"]),
                            "properties": {"CTRY24NM": name, "CTRY24CD": region["code"]},
                        }
                    ],
                }
                country_geoms.append(region["geom"])

        country_filter: PreparedGeometry | None = None
        if country_geoms:
            country_filter = prep(unary_union(country_geoms))

        def _in_countries(centroid: Point) -> bool:
            if country_filter is None:
                return True
            return country_filter.contains(centroid)

        level_map = {
            "itl_1": itl_hierarchy["itl1_regions"],
            "itl_2": itl_hierarchy["itl2_regions"],
            "itl_3": itl_hierarchy["itl3_regions"],
        }
        for bd_key, regions in level_map.items():
            feats = []
            for region in regions.values():
                if not _in_countries(region["centroid"]):
                    continue
                feats.append(
                    {
                        "type": "Feature",
                        "geometry": mapping(region["simplified"]),
                        "properties": {
                            f"{bd_key.upper().replace('_', '')}25NM": region["name"],
                            f"{bd_key.upper().replace('_', '')}25CD": region["code"],
                        },
                    }
                )
            boundary_data[bd_key] = {"type": "FeatureCollection", "features": feats}

        lad_feats = []
        for region in itl_hierarchy["lad_regions"].values():
            if not _in_countries(region["centroid"]):
                continue
            lad_feats.append(
                {
                    "type": "Feature",
                    "geometry": mapping(region["simplified"]),
                    "properties": {"LAD25NM": region["name"], "LAD25CD": region["code"]},
                }
            )
        boundary_data["lad"] = {"type": "FeatureCollection", "features": lad_feats}

        ward_feats = []
        for wcode, region in itl_hierarchy["ward_regions"].items():
            if not _in_countries(region["centroid"]):
                continue
            ward_feats.append(
                {
                    "type": "Feature",
                    "geometry": mapping(region["simplified"]),
                    "properties": {
                        "WD25NM": region["name"],
                        "WD25CD": region["code"],
                        "LAD25CD": itl_hierarchy["ward_to_lad"].get(wcode),
                    },
                }
            )
        boundary_data["wards"] = {"type": "FeatureCollection", "features": ward_feats}
    else:
        # Fallback: load raw GeoJSON files and simplify on the fly.
        country_filter_fb: PreparedGeometry | None = None
        countries_path = Path(paths["countries"])
        if countries_path.exists():
            countries_data = _load_geojson(countries_path)
            country_geoms_fb: list[BaseGeometry] = []
            for name in country_names or []:
                feats = [
                    f for f in countries_data["features"] if f["properties"].get("CTRY24NM") == name
                ]
                if feats:
                    boundary_data["countries"][name] = {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": mapping(
                                    shape(f["geometry"]).simplify(0.001, preserve_topology=True)
                                ),
                                "properties": f.get("properties", {}),
                            }
                            for f in feats
                        ],
                    }
                    country_geoms_fb.extend(shape(f["geometry"]) for f in feats)
            if country_geoms_fb:
                country_filter_fb = prep(unary_union(country_geoms_fb))

        def _feature_in_countries(feat: dict[str, Any]) -> bool:
            if country_filter_fb is None:
                return True
            return country_filter_fb.contains(shape(feat["geometry"]).centroid)

        for level, key in [("ITL_1", "itl1"), ("ITL_2", "itl2"), ("ITL_3", "itl3")]:
            gp = Path(paths.get(key, f"boundaries/{level}.geojson"))
            if gp.exists():
                data = _load_geojson(gp)
                boundary_data[level.lower()] = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": mapping(
                                shape(f["geometry"]).simplify(0.001, preserve_topology=True)
                            ),
                            "properties": f.get("properties", {}),
                        }
                        for f in data["features"]
                        if _feature_in_countries(f)
                    ],
                }

        lad_path = Path(paths["lad"])
        if lad_path.exists():
            data = _load_geojson(lad_path)
            boundary_data["lad"] = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": mapping(
                            shape(f["geometry"]).simplify(0.001, preserve_topology=True)
                        ),
                        "properties": f.get("properties", {}),
                    }
                    for f in data["features"]
                    if _feature_in_countries(f)
                ],
            }

        wards_path = Path(paths["wards"])
        if wards_path.exists():
            data = _load_geojson(wards_path)
            boundary_data["wards"] = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": mapping(
                            shape(f["geometry"]).simplify(0.001, preserve_topology=True)
                        ),
                        "properties": f.get("properties", {}),
                    }
                    for f in data["features"]
                    if _feature_in_countries(f)
                ],
            }

    write_compact_json(output_path, boundary_data)

    logger.debug("Exported shared boundary data to: %s", output_path)


# ---------------------------------------------------------------------------
# Internal helpers – data conversion
# ---------------------------------------------------------------------------


def _items_to_placed(
    items: list[MarkerItem],
) -> tuple[dict[str, list[_PlacedItem]], dict[str, int]]:
    """Convert a flat list of MarkerItem into grouped _PlacedItem dicts."""
    by_tier: dict[str, list[_PlacedItem]] = {}
    tier_numbers: dict[str, int] = {}

    for item in items:
        placed: _PlacedItem = {
            "name": item.name,
            "latitude": item.latitude,
            "longitude": item.longitude,
            "group": item.group,
            "tier": item.tier,
            "tier_num": item.tier_num,
            "icon_url": item.icon_url,
            "popup_html": item.popup_html,
            "category": item.category,
            "structure": item.structure,
            "itl0": item.itl0,
            "itl1": item.itl1,
            "itl2": item.itl2,
            "itl3": item.itl3,
            "lad": item.lad,
            "ward": item.ward,
        }
        by_tier.setdefault(item.tier, []).append(placed)
        tier_numbers.setdefault(item.tier, item.tier_num)

    return by_tier, tier_numbers


# ---------------------------------------------------------------------------
# Region assignment
# ---------------------------------------------------------------------------


def _assign_items_to_itl_regions(
    items_by_tier: dict[str, list[_PlacedItem]], itl_hierarchy: ITLHierarchy
) -> _RegionToItems:
    """Assign each item to all supported boundary levels via hierarchical containment.

    If items already carry pre-computed ITL assignments (via
    :func:`preassign_itl_regions`), spatial queries are skipped and only the
    region-to-items grouping is built.
    """

    itl0_to_items: dict[str, list[_PlacedItem]] = {}
    itl1_to_items: dict[str, list[_PlacedItem]] = {}
    itl2_to_items: dict[str, list[_PlacedItem]] = {}
    itl3_to_items: dict[str, list[_PlacedItem]] = {}
    lad_to_items: dict[str, list[_PlacedItem]] = {}
    ward_to_items: dict[str, list[_PlacedItem]] = {}

    total_assigned = 0
    total_items = 0

    first_item = next((it for items in items_by_tier.values() for it in items), None)
    pre_assigned = first_item is not None and first_item["itl0"] is not None

    if pre_assigned:
        for items in items_by_tier.values():
            for item in items:
                total_items += 1
                if item["itl0"]:
                    itl0_to_items.setdefault(item["itl0"], []).append(item)
                if item["itl1"]:
                    itl1_to_items.setdefault(item["itl1"], []).append(item)
                if item["itl2"]:
                    itl2_to_items.setdefault(item["itl2"], []).append(item)
                if item["itl3"]:
                    itl3_to_items.setdefault(item["itl3"], []).append(item)
                    total_assigned += 1
                if item["lad"]:
                    lad_to_items.setdefault(item["lad"], []).append(item)
                if item["ward"]:
                    ward_to_items.setdefault(item["ward"], []).append(item)
    else:
        itl0_regions = itl_hierarchy["itl0_regions"]
        itl1_regions = itl_hierarchy["itl1_regions"]
        itl2_regions = itl_hierarchy["itl2_regions"]
        itl3_regions = itl_hierarchy["itl3_regions"]
        lad_regions = itl_hierarchy["lad_regions"]
        ward_regions = itl_hierarchy["ward_regions"]
        itl1_to_itl2s = itl_hierarchy["itl1_to_itl2s"]
        itl2_to_itl3s = itl_hierarchy["itl2_to_itl3s"]
        itl3_to_lads = itl_hierarchy["itl3_to_lads"]
        lad_to_wards = itl_hierarchy["lad_to_wards"]

        for _, items in items_by_tier.items():
            for item in items:
                total_items += 1
                point = Point(item.get("longitude", 0.0), item.get("latitude", 0.0))

                item["itl0"] = None
                item["itl1"] = None
                item["itl2"] = None
                item["itl3"] = None
                item["lad"] = None
                item["ward"] = None

                for itl0 in itl0_regions.values():
                    if itl0["prepared"].contains(point):
                        item["itl0"] = itl0["name"]
                        itl0_to_items.setdefault(itl0["name"], []).append(item)
                        break

                found_itl1 = None
                for itl1 in itl1_regions.values():
                    if itl1["prepared"].contains(point):
                        found_itl1 = itl1["name"]
                        item["itl1"] = found_itl1
                        itl1_to_items.setdefault(found_itl1, []).append(item)
                        break
                if not found_itl1:
                    continue

                found_itl2 = None
                for itl2_name in itl1_to_itl2s.get(found_itl1, []):
                    if itl2_regions[itl2_name]["prepared"].contains(point):
                        found_itl2 = itl2_name
                        item["itl2"] = found_itl2
                        itl2_to_items.setdefault(found_itl2, []).append(item)
                        break
                if not found_itl2:
                    continue

                found_itl3 = None
                for itl3_name in itl2_to_itl3s.get(found_itl2, []):
                    if itl3_regions[itl3_name]["prepared"].contains(point):
                        found_itl3 = itl3_name
                        item["itl3"] = itl3_name
                        itl3_to_items.setdefault(itl3_name, []).append(item)
                        total_assigned += 1
                        break
                if not found_itl3:
                    continue

                found_lad = None
                for lad_code in itl3_to_lads.get(found_itl3, []):
                    lad = lad_regions.get(lad_code)
                    if lad and lad["prepared"].contains(point):
                        found_lad = lad_code
                        item["lad"] = lad_code
                        lad_to_items.setdefault(lad_code, []).append(item)
                        break

                if found_lad and ward_regions:
                    for ward_code in lad_to_wards.get(found_lad, []):
                        ward = ward_regions.get(ward_code)
                        if ward and ward["prepared"].contains(point):
                            item["ward"] = ward_code
                            ward_to_items.setdefault(ward_code, []).append(item)
                            break

    logger.debug("ITL Region Assignment%s:", " (pre-assigned)" if pre_assigned else "")
    logger.debug("  Assigned %d of %d items to ITL regions", total_assigned, total_items)
    logger.debug("  ITL1: %d regions have items", len(itl1_to_items))
    logger.debug("  ITL2: %d regions have items", len(itl2_to_items))
    logger.debug("  ITL3: %d regions have items", len(itl3_to_items))
    logger.debug("  LAD: %d regions have items", len(lad_to_items))
    if not pre_assigned and itl_hierarchy["ward_regions"]:
        logger.debug("  Wards: %d regions have items", len(ward_to_items))

    for region_name in sorted(itl1_to_items.keys())[:3]:
        logger.debug("  ITL1 %s: %d items", region_name, len(itl1_to_items[region_name]))

    return {
        "itl0": itl0_to_items,
        "itl1": itl1_to_items,
        "itl2": itl2_to_items,
        "itl3": itl3_to_items,
        "lad": lad_to_items,
        "ward": ward_to_items,
    }


def _pick_color(palette: list[str], index: int) -> str:
    return palette[index % len(palette)]


# ---------------------------------------------------------------------------
# Territory / Voronoi computation
# ---------------------------------------------------------------------------


def _create_bounded_voronoi(
    items: list[_PlacedItem], boundary_geom: BaseGeometry, group_colors: dict[str, str]
) -> list[dict[str, Any]]:
    """Voronoi diagram bounded and clipped to *boundary_geom*, merged by group."""
    if len(items) < 2:
        return []

    points = np.array([[it["latitude"], it["longitude"]] for it in items])
    minx, miny, maxx, maxy = boundary_geom.bounds
    padding = max(maxx - minx, maxy - miny) * 2
    corners = np.array(
        [
            [miny - padding, minx - padding],
            [miny - padding, maxx + padding],
            [maxy + padding, maxx + padding],
            [maxy + padding, minx - padding],
        ]
    )
    all_points = np.vstack([points, corners])
    vor = Voronoi(all_points)

    cells_by_group: dict[str, list[Any]] = defaultdict(list)
    for idx in range(len(items)):
        region_idx = vor.point_region[idx]
        region_vertices = vor.regions[region_idx]
        if not region_vertices or -1 in region_vertices:
            continue
        vertices = [(vor.vertices[i][1], vor.vertices[i][0]) for i in region_vertices]
        if len(vertices) < 3:
            continue
        clipped = Polygon(vertices).intersection(boundary_geom)
        if not clipped.is_empty and hasattr(clipped, "area") and clipped.area > 0:
            cells_by_group[items[idx]["group"]].append(clipped)

    result = []
    for grp, cells in cells_by_group.items():
        if cells:
            result.append({"geom": unary_union(cells), "color": group_colors[grp], "group": grp})
    return result


def _collect_group_geometries(
    items: list[_PlacedItem],
    region_to_items: _RegionToItems,
    itl_hierarchy: ITLHierarchy,
    group_colors: dict[str, str],
    config: MapConfig,
) -> dict[str, list[BaseGeometry]]:
    """Compute territory geometries per group for a set of items sharing one tier."""
    if not items:
        return {}

    all_levels = ["itl0", "itl1", "itl2", "itl3", "lad", "ward"]
    next_level: dict[str, str] = {
        "itl0": "itl1",
        "itl1": "itl2",
        "itl2": "itl3",
        "itl3": "lad",
        "lad": "ward",
    }
    child_map_by_level: dict[str, dict[str, list[str]]] = {
        "itl0": itl_hierarchy["itl0_to_itl1s"],
        "itl1": itl_hierarchy["itl1_to_itl2s"],
        "itl2": itl_hierarchy["itl2_to_itl3s"],
        "itl3": itl_hierarchy["itl3_to_lads"],
        "lad": itl_hierarchy["lad_to_wards"],
    }
    regions_by_level: dict[str, dict[str, ITLRegionGeom]] = {
        "itl0": itl_hierarchy["itl0_regions"],
        "itl1": itl_hierarchy["itl1_regions"],
        "itl2": itl_hierarchy["itl2_regions"],
        "itl3": itl_hierarchy["itl3_regions"],
        "lad": itl_hierarchy["lad_regions"],
        "ward": itl_hierarchy["ward_regions"],
    }
    lad_to_itl3 = itl_hierarchy["lad_to_itl3"]

    item_ids = {id(it) for it in items}
    filtered: dict[str, dict[str, list[_PlacedItem]]] = {}
    for level in all_levels:
        level_map = region_to_items.get(level, {})
        filtered[level] = {
            rk: [it for it in rk_items if id(it) in item_ids] for rk, rk_items in level_map.items()
        }

    tier_num = items[0].get("tier_num", 999)
    if config.tier_entry_level and tier_num in config.tier_entry_level:
        entry_level = config.tier_entry_level[tier_num]
    else:
        entry_level = config.default_tier_entry_level

    if config.tier_floor_level and tier_num in config.tier_floor_level:
        floor_level = config.tier_floor_level[tier_num]
    else:
        floor_level = config.default_tier_floor_level

    level_index = {lv: i for i, lv in enumerate(all_levels)}
    floor_idx = level_index[floor_level]

    group_geometries: dict[str, list[BaseGeometry]] = {}

    def closest_group(parent_items: list[_PlacedItem], centroid: Point) -> str | None:
        if not parent_items:
            return None
        clat, clon = centroid.y, centroid.x
        best = min(
            parent_items,
            key=lambda it: _haversine_km(clat, clon, it["latitude"], it["longitude"]),
        )
        return best["group"]

    def split_region(
        level: str, region_key: str, parent_items: list[_PlacedItem]
    ) -> list[dict[str, Any]]:
        region = regions_by_level[level].get(region_key)
        if not region:
            return []

        items_here = filtered[level].get(region_key, [])
        if not items_here:
            fb = closest_group(parent_items, region["centroid"])
            if not fb:
                return []
            return [{"geom": region["simplified"], "group": fb, "color": group_colors[fb]}]

        groups_here = {it["group"] for it in items_here}
        if len(groups_here) == 1:
            grp = next(iter(groups_here))
            child_level_check = next_level.get(level)
            if child_level_check and level_index[child_level_check] <= floor_idx:
                occupied = sum(
                    1
                    for ck in child_map_by_level.get(level, {}).get(region_key, [])
                    if filtered[child_level_check].get(ck)
                )
                if occupied <= 1:
                    children_with = [
                        ck
                        for ck in child_map_by_level.get(level, {}).get(region_key, [])
                        if ck in regions_by_level.get(child_level_check, {})
                        and filtered[child_level_check].get(ck)
                    ]
                    if children_with:
                        narrow: list[dict[str, Any]] = []
                        child_regions_narrow = regions_by_level[child_level_check]
                        all_child_keys = [
                            ck
                            for ck in child_map_by_level.get(level, {}).get(region_key, [])
                            if ck in child_regions_narrow
                        ]
                        for ck in children_with:
                            narrow.extend(split_region(child_level_check, ck, items_here))
                        # Fill a lone empty sibling only when every other child at this
                        # level already has teams (e.g. York beside North Yorkshire).
                        # Skip when multiple empty siblings exist (e.g. Swindon +
                        # Wiltshire beside Gloucestershire CC).
                        empty_siblings = [
                            ck for ck in all_child_keys if not filtered[child_level_check].get(ck)
                        ]
                        if len(empty_siblings) == 1:
                            eck = empty_siblings[0]
                            fb = closest_group(items_here, child_regions_narrow[eck]["centroid"])
                            if fb:
                                narrow.append(
                                    {
                                        "geom": child_regions_narrow[eck]["simplified"],
                                        "group": fb,
                                        "color": group_colors[fb],
                                    }
                                )
                        return narrow
            return [{"geom": region["simplified"], "group": grp, "color": group_colors[grp]}]

        child_level = next_level.get(level)
        if not child_level:
            vcells = _create_bounded_voronoi(items_here, region["simplified"], group_colors)
            if vcells:
                return vcells
            fb = closest_group(items_here, region["centroid"])
            if not fb:
                return []
            return [{"geom": region["simplified"], "group": fb, "color": group_colors[fb]}]

        child_regions = regions_by_level[child_level]
        child_keys = [
            ck
            for ck in child_map_by_level.get(level, {}).get(region_key, [])
            if ck in child_regions
        ]

        if not child_keys:
            vcells = _create_bounded_voronoi(items_here, region["simplified"], group_colors)
            if vcells:
                return vcells
            fb = closest_group(items_here, region["centroid"])
            if not fb:
                return []
            return [{"geom": region["simplified"], "group": fb, "color": group_colors[fb]}]

        result_cells: list[dict[str, Any]] = []
        empty_children: list[str] = []
        for ck in child_keys:
            items_in_child = filtered[child_level].get(ck, [])
            if not items_in_child:
                empty_children.append(ck)
                continue
            child_groups = {it["group"] for it in items_in_child}
            if len(child_groups) == 1:
                grp = next(iter(child_groups))
                result_cells.append(
                    {
                        "geom": child_regions[ck]["simplified"],
                        "group": grp,
                        "color": group_colors[grp],
                    }
                )
            else:
                result_cells.extend(split_region(child_level, ck, items_in_child))

        for eck in empty_children:
            pool = items_here
            if level == "lad":
                itl3_key = lad_to_itl3.get(region_key)
                if itl3_key:
                    pool_itl3 = filtered["itl3"].get(itl3_key, [])
                    if pool_itl3:
                        pool = pool_itl3
            fb = closest_group(pool, child_regions[eck]["centroid"])
            if fb:
                result_cells.append(
                    {
                        "geom": child_regions[eck]["simplified"],
                        "group": fb,
                        "color": group_colors[fb],
                    }
                )
        return result_cells

    for rk, rk_items in filtered[entry_level].items():
        for cell in split_region(entry_level, rk, rk_items):
            group_geometries.setdefault(cell["group"], []).append(cell["geom"])

    return group_geometries


_TerritoryMerged = dict[str, dict[str, Any]]
"""Per-group merged GeoJSON mapping: ``{group_name: geojson_dict}``."""

TerritoryCache = dict[tuple[Any, ...], _TerritoryMerged]
"""Cache of territory results keyed by ``(entry_level, floor_level, frozenset(item_names))``."""


def _merge_territories(
    group_geometries: dict[str, list[BaseGeometry]],
) -> _TerritoryMerged:
    """Union + hole-removal for each group, returning GeoJSON mapping dicts."""
    min_hole_area = 1e-4

    def remove_small_holes(geom: BaseGeometry) -> BaseGeometry:
        if geom.is_empty:
            return geom
        if geom.geom_type == "Polygon":
            poly = cast(Polygon, geom)
            if not poly.interiors:
                return geom
            holes = [r for r in poly.interiors if Polygon(r).area >= min_hole_area]
            if len(holes) == len(poly.interiors):
                return geom
            return Polygon(poly.exterior, holes)
        if geom.geom_type == "MultiPolygon":
            multi = cast(MultiPolygon, geom)
            if not any(p.interiors for p in multi.geoms):
                return geom
            return MultiPolygon(
                [
                    (
                        Polygon(
                            p.exterior, [r for r in p.interiors if Polygon(r).area >= min_hole_area]
                        )
                        if p.interiors
                        else p
                    )
                    for p in multi.geoms
                ]
            )
        return geom

    result: _TerritoryMerged = {}
    for grp, geometries in group_geometries.items():
        if not geometries:
            continue
        merged = unary_union(geometries)
        merged = remove_small_holes(merged)
        result[grp] = mapping(merged)
    return result


_TerritoryStyle = dict[str, Any]
"""Leaflet path options for one group, plus an optional ``pane`` layer option."""


def _territory_styles(
    group_colors: dict[str, str], hatched_groups: set[str]
) -> tuple[str, dict[str, _TerritoryStyle]]:
    """Return (inline SVG pattern definitions, per-group Leaflet options).

    Hatched groups fill with a stripe pattern whose gaps are transparent, so the
    structure shaded beneath them still reads through between the bands.
    """
    patterns: list[str] = []
    styles: dict[str, _TerritoryStyle] = {}
    for index, grp in enumerate(sorted(group_colors)):
        color = group_colors[grp]
        if grp not in hatched_groups:
            styles[grp] = {
                "fillColor": color,
                "color": color,
                "weight": 1,
                "fillOpacity": TERRITORY_FILL_OPACITY,
                "opacity": TERRITORY_FILL_OPACITY,
            }
            continue
        pattern_id = f"{HATCH_PANE}{index}"
        patterns.append(stripe_pattern_svg(pattern_id, stripe=color))
        styles[grp] = {
            "fillColor": f"url(#{pattern_id})",
            "color": color,
            "weight": 1.5,
            "fillOpacity": HATCHED_FILL_OPACITY,
            "opacity": 0.9,
            "pane": HATCH_PANE,
        }
    return _hatch_defs_html(patterns), styles


def _hatch_defs_html(patterns: list[str]) -> str:
    """A zero-size inline SVG holding *patterns*.

    Leaflet builds its own SVG for the overlay pane, but a fragment-only
    ``url(#id)`` fill resolves against the whole document, so the definitions can
    sit anywhere in the body.
    """
    if not patterns:
        return ""
    return (
        '<svg aria-hidden="true" width="0" height="0" '
        'style="position:absolute; width:0; height:0; overflow:hidden;">'
        f"<defs>{''.join(patterns)}</defs>"
        "</svg>"
    )


def _split_pane(style: _TerritoryStyle) -> tuple[dict[str, Any], str | None]:
    """Leaflet takes ``pane`` as a layer option rather than a path style property."""
    return {k: v for k, v in style.items() if k != "pane"}, style.get("pane")


def _render_territories(
    feature_group: folium.FeatureGroup,
    merged_geojson: _TerritoryMerged,
    styles: dict[str, _TerritoryStyle],
) -> None:
    """Add pre-merged GeoJSON territory layers to *feature_group*."""
    for grp, geojson_dict in merged_geojson.items():
        path_style, pane = _split_pane(styles[grp])

        def style_fn(feature: Any, s: dict[str, Any] = path_style) -> dict[str, Any]:
            return s

        pane_kwargs = {"pane": pane} if pane else {}
        folium.GeoJson(geojson_dict, style_function=style_fn, **pane_kwargs).add_to(feature_group)


#: Web Mercator meters-per-pixel at zoom 0, halving with each zoom level. Used
#: to convert a territory's real-world footprint into a label size in pixels.
_WEB_MERCATOR_BASE_METERS_PER_PIXEL = 156_543.03392
_METERS_PER_DEGREE_LAT = 111_320

#: Simplification tolerance (degrees, ~1km) used only when sizing/placing a
#: territory's label -- far coarser than ``_SIMPLIFY_TOLERANCE``, which
#: preserves the shape actually drawn on the map. A territory can be a union
#: of thousands of ITL-boundary vertices, and the repeated erosion below
#: (``buffer(-x)``, run in a binary search) gets dramatically slower as vertex
#: count grows; label sizing doesn't need anywhere near that precision.
_LABEL_SIMPLIFY_TOLERANCE = 0.01

#: Morphological "closing" distance (degrees, ~1.7km) applied before sizing a
#: label: dilate then erode by this much to fill in narrow notches -- a river
#: mouth, an estuary, a boundary that hugs a river -- that would otherwise
#: pinch off the inscribed circle without meaningfully changing the
#: territory's overall shape. Labels are fine sitting over that kind of
#: feature, so it shouldn't count against the space available to them.
_LABEL_CLOSING_TOLERANCE = 0.015


def _largest_inscribed_square(geom: BaseGeometry, iterations: int = 12) -> tuple[Point, float]:
    """A well-centered anchor point inside *geom*, and the half-diagonal (in
    meters) of the largest square that roughly fits inside it.

    Approximated via binary search on inward-buffering ("erosion"): the
    largest distance *geom* can be shrunk by before it disappears is the
    radius of its largest inscribed circle, and the point that survives
    longest is a point deep inside the shape rather than merely its
    bounding-box or centroid (which can fall in a bay or outside a concave
    territory like a merged county shape). Longitude is rescaled by
    cos(latitude) first so a degree of x and a degree of y cover roughly the
    same real-world distance -- otherwise erosion would eat north-south
    faster than east-west at this latitude, biasing the inscribed circle.
    """
    geom = geom.simplify(_LABEL_SIMPLIFY_TOLERANCE, preserve_topology=True)
    geom = geom.buffer(_LABEL_CLOSING_TOLERANCE).buffer(-_LABEL_CLOSING_TOLERANCE)
    lat0 = geom.centroid.y
    lon_scale = math.cos(math.radians(lat0)) or 1e-9
    scaled = affine_scale(geom, xfact=lon_scale, yfact=1.0, origin=(0, 0))

    minx, miny, maxx, maxy = scaled.bounds
    lo, hi = 0.0, math.hypot(maxx - minx, maxy - miny) / 2
    best_center = scaled.representative_point()
    for _ in range(iterations):
        mid = (lo + hi) / 2
        eroded = scaled.buffer(-mid)
        if not eroded.is_empty:
            lo = mid
            best_center = eroded.representative_point()
        else:
            hi = mid

    anchor = Point(best_center.x / lon_scale, best_center.y)
    radius_m = lo * _METERS_PER_DEGREE_LAT
    return anchor, radius_m * math.sqrt(2)


def _add_territory_labels(
    shading_groups: dict[str, folium.FeatureGroup],
    merged_geojson: _TerritoryMerged,
    zoom: int,
) -> None:
    """Add a text label at each territory's centroid, into its own shading group
    so it toggles on and off with that territory's shading.

    The label box is sized (both max-width and font-size) to roughly fit
    the largest square that fits inside the territory's footprint (see
    ``_largest_inscribed_square``), and centered on its anchor point via a
    CSS transform -- which requires the box to be ``inline-block`` so its
    width shrinks to fit the (possibly wrapped) text rather than stretching
    to fill its zero-size marker parent, which would make the ``-50%``
    centering offset zero and leave the label hanging off to the right of
    its anchor point instead of centered on it.

    Sizes are computed for *zoom* (the map's initial zoom) and marked up with
    ``data-base-*`` attributes; ``_get_territory_label_zoom_script`` rescales
    them to match on every subsequent zoom change, since plain HTML/CSS text
    doesn't grow or shrink with the map like the territory shading itself does.
    """
    for grp, fg in shading_groups.items():
        geojson_dict = merged_geojson.get(grp)
        if geojson_dict is None:
            continue
        geom = shape(geojson_dict)
        anchor, half_diagonal_m = _largest_inscribed_square(geom)
        meters_per_pixel = (
            _WEB_MERCATOR_BASE_METERS_PER_PIXEL * math.cos(math.radians(anchor.y)) / (2**zoom)
        )
        side_px = half_diagonal_m / meters_per_pixel

        max_width_px = max(30, round(side_px * 0.9))
        font_size_px = max(8, min(16, round(side_px / 5)))
        # Words never break mid-word, so a single long word (e.g.
        # "Buckinghamshire") can still overflow max-width at this font size
        # even though the territory has plenty of room overall -- widen the
        # box to fit that word rather than shrinking type to the point of
        # being unreadable while still overflowing anyway.
        longest_word = max(grp.split(), key=len, default=grp)
        needed_width_px = math.ceil(len(longest_word) * font_size_px * 0.62)
        max_width_px = max(max_width_px, needed_width_px)

        label_html = (
            f'<div class="rugby-territory-label" data-base-zoom="{zoom}" '
            f'data-base-font="{font_size_px}" data-base-width="{max_width_px}" '
            f'style="display:inline-block; max-width:{max_width_px}px; '
            "white-space:normal; text-align:center; line-height:1.15; "
            "transform:translate(-50%,-50%); "
            # Matches Leaflet's own .leaflet-zoom-anim transform transition
            # (duration and easing), so the label resizes in step with the
            # territory shading's zoom animation instead of jumping to the
            # new size only once the animation has already finished.
            "transition: font-size 0.25s cubic-bezier(0,0,0.25,1), "
            "max-width 0.25s cubic-bezier(0,0,0.25,1); "
            f"font-weight:bold; font-size:{font_size_px}px; color:#000; text-shadow:"
            "-1px -1px 0 #fff, 1px -1px 0 #fff, -1px 1px 0 #fff, 1px 1px 0 #fff; "
            f'pointer-events:none;">{escape(grp)}</div>'
        )
        folium.Marker(
            location=[anchor.y, anchor.x],
            icon=folium.DivIcon(html=label_html, icon_size=(0, 0), icon_anchor=(0, 0)),
        ).add_to(fg)


def _get_territory_label_zoom_script() -> str:
    """Rescale ``.rugby-territory-label`` font-size/max-width to track zoom.

    They're plain HTML text sized in pixels for one reference zoom (see
    ``_add_territory_labels``), so without this they'd stay a fixed on-screen
    size while the territory shading beneath them grows and shrinks.

    Resizing is triggered on Leaflet's ``zoomanim`` event -- fired with the
    *target* zoom right as the zoom transform animation starts -- rather than
    ``zoomend``, combined with the CSS transition on the label (matching
    Leaflet's own ``.leaflet-zoom-anim`` transition timing) so the label
    resizes smoothly in step with the shading's zoom animation instead of
    sitting frozen for its whole duration and then snapping to size.
    ``zoomend`` is also hooked as a resync, since large zoom jumps and
    non-animated zooms don't fire ``zoomanim``.
    """
    return """
    <script>
    (function() {
        function rescale() {
            var mapKey = Object.keys(window).find(function (k) {
                return k.indexOf('map_') === 0 && window[k] instanceof L.Map;
            });
            var map = mapKey ? window[mapKey] : null;
            if (!map) { setTimeout(rescale, 100); return; }
            function apply(z) {
                document.querySelectorAll('.rugby-territory-label').forEach(function (el) {
                    var baseZoom = parseFloat(el.dataset.baseZoom);
                    var baseFont = parseFloat(el.dataset.baseFont);
                    var baseWidth = parseFloat(el.dataset.baseWidth);
                    var scale = Math.pow(2, z - baseZoom);
                    el.style.fontSize = (baseFont * scale) + 'px';
                    el.style.maxWidth = (baseWidth * scale) + 'px';
                });
            }
            map.on('zoomanim', function (e) { apply(e.zoom); });
            map.on('zoomend', function () { apply(map.getZoom()); });
            apply(map.getZoom());
        }
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', rescale);
        else rescale();
    })();
    </script>
    """


def _collect_territory_export(
    feature_group: folium.FeatureGroup,
    merged_geojson: _TerritoryMerged,
    styles: dict[str, _TerritoryStyle],
) -> tuple[str, dict[str, Any]] | None:
    """Return (folium JS variable name, {group: {geometry, style, pane}}) for *feature_group*.

    Used instead of :func:`_render_territories` when ``MapConfig.external_territories``
    is set, so the caller can write the data to a sidecar file rather than embedding it.
    Returns ``None`` when there is nothing to export (empty shading group).
    """
    if not merged_geojson:
        return None
    groups: dict[str, Any] = {}
    for grp, geojson_dict in merged_geojson.items():
        path_style, pane = _split_pane(styles[grp])
        entry: dict[str, Any] = {"geometry": geojson_dict, "style": path_style}
        if pane:
            entry["pane"] = pane
        groups[grp] = entry
    return feature_group.get_name(), {"groups": groups}


def _write_territories_sidecar(
    output_path: Path, sidecar_name: str, layers: dict[str, Any]
) -> None:
    """Write the collected territory export data as JSON beside *output_path*."""
    sidecar_path = output_path.parent / sidecar_name
    write_compact_json(sidecar_path, layers)


def _inject_territory_boot_hook(output_path: Path) -> None:
    """Inject a call to apply territory shading after Folium feature groups exist.

    Folium emits empty territory ``FeatureGroup`` layers, then builds every
    marker cluster. Booting territory apply in between lets shading paint while
    marker DOM is still being constructed instead of after the full script.
    """
    text = output_path.read_text(encoding="utf-8")
    hook = "if (window.rugbyTryApplyTerritories) window.rugbyTryApplyTerritories();"
    if hook in text:
        return
    pos = text.find("var marker_cluster_")
    if pos == -1:
        pos = text.find("L.markerClusterGroup(")
    if pos == -1:
        return
    output_path.write_text(text[:pos] + hook + "\n            " + text[pos:], encoding="utf-8")


def _inject_presentation_ready_hook(output_path: Path) -> None:
    """Append presentation-ready dispatch at the end of Folium's boot script.

    ``root.script`` children are emitted at the *start* of that block, which
    runs before inline territory GeoJSON is added — so post-save injection is
    required for maps that embed territories in the HTML.
    """
    text = output_path.read_text(encoding="utf-8")
    hook = _signal_presentation_ready_script()
    if hook in text:
        return
    pos = text.rfind("</script>")
    if pos == -1:
        return
    output_path.write_text(
        text[:pos] + "\n            " + hook + "\n" + text[pos:], encoding="utf-8"
    )


def _finalize_map_html(output_path: Path, *, territory_export: bool) -> None:
    """Post-save hooks that must run after Folium has written the page."""
    if territory_export:
        _inject_territory_boot_hook(output_path)
    else:
        _inject_presentation_ready_hook(output_path)


def _get_territories_preload_link(sidecar_name: str) -> str:
    """Hint the browser to fetch territory GeoJSON in parallel with page parse."""
    return f'<link rel="preload" href="{escape(sidecar_name)}" as="fetch">'


def _signal_presentation_ready_script() -> str:
    """Notify deferred crest loading that territory shading has been painted."""
    return "document.dispatchEvent(new Event('rugby-map-presentation-ready'));"


def _get_territory_loader_script(sidecar_name: str) -> str:
    """Client-side loader that fetches the territories sidecar and populates
    the (already-created, empty) territory FeatureGroups by their Folium JS
    variable name, matching the ``_get_boundary_loader_script`` pattern.

    Fetch starts in ``<head>`` so it runs in parallel with Folium's boot
    script. :func:`_inject_territory_boot_hook` calls
    ``rugbyTryApplyTerritories()`` after feature groups are created but
    before marker clusters, so shading can paint without waiting for every
    marker. Groups are added progressively across animation frames rather
    than in one blocking batch.

    Retries the fetch on failure (a transient network blip or cold CDN edge
    can take longer than a couple of seconds to clear) and, if a controlling
    service worker is present, also asks it to precache the sidecar in the
    background -- so a *second* tab/reload hitting the same cold edge has a
    cached copy to fall back on instead of racing the network again.
    """
    return f"""
    <script>
    (function() {{
        var MAX_ATTEMPTS = 6;
        var MAX_RENDER_ATTEMPTS = 60;
        var GROUPS_PER_FRAME = 2;
        var PRESENTATION_FALLBACK_MS = {PRESENTATION_READY_FALLBACK_MS};
        var territoryDataPromise = null;
        var cachedLayers = null;
        var territoryApplyStarted = false;
        var presentationReadySent = false;
        function requestPrecache() {{
            if (navigator.serviceWorker && navigator.serviceWorker.controller) {{
                navigator.serviceWorker.controller.postMessage({{
                    type: 'PRECACHE_JSON',
                    url: '{sidecar_name}',
                }});
            }}
        }}
        function signalPresentationReady() {{
            if (presentationReadySent) return;
            presentationReadySent = true;
            document.dispatchEvent(new Event('rugby-map-presentation-ready'));
        }}
        function finishPresentationReady() {{
            if (window.requestAnimationFrame) {{
                window.requestAnimationFrame(signalPresentationReady);
            }} else {{
                setTimeout(signalPresentationReady, 0);
            }}
        }}
        function layersReady(layers) {{
            var varNames = Object.keys(layers);
            if (!varNames.length) return false;
            return varNames.every(function(varName) {{ return window[varName]; }});
        }}
        function buildRenderQueue(layers) {{
            var queue = [];
            Object.keys(layers).forEach(function(varName) {{
                var groups = layers[varName].groups || {{}};
                Object.keys(groups).forEach(function(grp) {{
                    queue.push({{ varName: varName, grp: grp, data: groups[grp] }});
                }});
            }});
            return queue;
        }}
        function renderQueueItem(item) {{
            var fg = window[item.varName];
            if (!fg) return false;
            try {{
                var style = item.data.style || {{}};
                var opts = {{ style: function() {{ return style; }} }};
                if (item.data.pane) opts.pane = item.data.pane;
                L.geoJson(item.data.geometry, opts).addTo(fg);
                return true;
            }} catch (e) {{
                console.warn('Could not render territory group', item.varName, item.grp, e);
                return false;
            }}
        }}
        function applyTerritoriesProgressive(layers, attempt) {{
            if (territoryApplyStarted) return;
            if (!layersReady(layers)) {{
                if (attempt < MAX_RENDER_ATTEMPTS) {{
                    setTimeout(function() {{ applyTerritoriesProgressive(layers, attempt + 1); }}, 50);
                    return;
                }}
                console.warn('Territory layer variables were not ready in time');
                finishPresentationReady();
                return;
            }}
            territoryApplyStarted = true;
            var queue = buildRenderQueue(layers);
            if (!queue.length) {{
                finishPresentationReady();
                return;
            }}
            var index = 0;
            function flushFrame() {{
                var end = Math.min(index + GROUPS_PER_FRAME, queue.length);
                for (; index < end; index++) {{
                    renderQueueItem(queue[index]);
                }}
                if (index < queue.length) {{
                    if (window.requestAnimationFrame) {{
                        window.requestAnimationFrame(flushFrame);
                    }} else {{
                        setTimeout(flushFrame, 16);
                    }}
                }} else {{
                    finishPresentationReady();
                }}
            }}
            flushFrame();
        }}
        function scheduleTerritoryRender() {{
            if (!cachedLayers) return;
            applyTerritoriesProgressive(cachedLayers, 0);
        }}
        window.rugbyTryApplyTerritories = scheduleTerritoryRender;
        function fetchTerritories(attempt) {{
            if (!territoryDataPromise) {{
                territoryDataPromise = fetch('{sidecar_name}', {{ cache: 'no-store' }}).then(function(r) {{
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return r.json();
                }});
            }}
            return territoryDataPromise.catch(function(e) {{
                if (attempt < MAX_ATTEMPTS) {{
                    territoryDataPromise = null;
                    return new Promise(function(resolve) {{
                        setTimeout(function() {{ resolve(fetchTerritories(attempt + 1)); }}, 500 * Math.pow(2, attempt));
                    }});
                }}
                console.warn('Could not load territories:', e);
                finishPresentationReady();
                return null;
            }});
        }}
        function onTerritoryData(layers) {{
            if (!layers) return;
            cachedLayers = layers;
            scheduleTerritoryRender();
        }}
        requestPrecache();
        fetchTerritories(0).then(onTerritoryData);
        setTimeout(function() {{
            if (!presentationReadySent) {{
                console.warn('Territory presentation timed out; loading crest images anyway');
                finishPresentationReady();
            }}
        }}, PRESENTATION_FALLBACK_MS);
    }})();
    </script>
    """


# ---------------------------------------------------------------------------
# Folium map components
# ---------------------------------------------------------------------------


POPUP_CSS = """
<style>
/* Hide cluster crest placeholders until the real image has loaded. */
.folium-map .marker-cluster-custom img[data-real-src] {
  opacity: 0;
}
.folium-map .leaflet-popup-content {
  margin: 6px 10px !important;
  line-height: 1.3;
}
.folium-map .leaflet-popup-content-wrapper,
.folium-map .leaflet-popup-tip {
  background: #fff;
  color: #222;
  box-shadow: 0 3px 14px rgba(0, 0, 0, 0.35);
}
.folium-map .leaflet-popup-close-button {
  color: #555;
}
.folium-map .rugby-popup {
  font-family: 'Barlow', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  width: 220px;
  font-size: 13px;
}
.folium-map .rugby-popup h4 {
  margin: 0 0 4px 0;
  font-size: 15px;
  line-height: 1.2;
}
.folium-map:not(.rugby-map-dark) .rugby-popup .popup-title {
  text-shadow: 0 0 1px #fff, 0 0 3px #fff;
}
.folium-map .rugby-popup hr {
  margin: 6px 0;
  border: 0;
  border-top: 1px solid #ccc;
}
.folium-map .rugby-popup p {
  margin: 0 0 3px 0;
}
.folium-map .rugby-popup p:last-child {
  margin-bottom: 0;
}
.folium-map .rugby-popup .popup-label {
  font-weight: bold;
}
.folium-map .rugby-popup .island-travel-label {
  cursor: help;
}
.folium-map .rugby-popup .island-travel-hint {
  font-size: 0.85em;
  opacity: 0.75;
  font-weight: normal;
}
.folium-map .rugby-popup .island-travel-note {
  font-size: 0.92em;
  opacity: 0.85;
  margin: 0 0 6px 0 !important;
}
.folium-map.rugby-map-dark .rugby-popup .island-travel-note {
  opacity: 0.78;
}
.folium-map .rugby-popup .island-stat-group {
  display: block;
}
.folium-map .rugby-popup .island-stat-group .island-travel-label {
  display: inline;
  margin: 0;
}
.folium-map .rugby-popup .island-stat-group p {
  margin: 0 0 2px 0;
}
.folium-map .rugby-popup .island-stat-group p:last-child {
  margin-bottom: 0;
}
.folium-map .rugby-popup .island-stat-group--spaced {
  margin-top: 0.55em;
}
.folium-map .rugby-popup .popup-regions {
  margin: 0 0 3px 0;
}
.folium-map .rugby-popup a {
  color: #0066cc;
}
.folium-map.rugby-map-dark .leaflet-popup-content-wrapper,
.folium-map.rugby-map-dark .leaflet-popup-tip {
  background: #16213e;
  color: #e0e0e0;
}
.folium-map.rugby-map-dark .leaflet-popup-close-button {
  color: #c0c0c0;
}
.folium-map.rugby-map-dark .rugby-popup hr {
  border-top-color: #3d4f73;
}
.folium-map.rugby-map-dark .rugby-popup a {
  color: #7eb8ff;
}
.folium-map.rugby-map-dark .leaflet-control-layers {
  background: #16213e;
  color: #e0e0e0;
}
.folium-map.rugby-map-dark .leaflet-control-layers-separator {
  border-top-color: #2a2a4a;
}
.folium-map.rugby-map-dark .leaflet-bar a {
  background: #16213e;
  color: #e0e0e0;
  border-color: #2a2a4a;
}
.folium-map.rugby-map-dark .leaflet-bar a:hover {
  background: #1e2a45;
}

/* Floating theme toggle (maps without breadcrumb header, e.g. Scotland) */
.rugby-theme-float {
  position: fixed;
  top: 10px;
  right: 10px;
  z-index: 1001;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-radius: 6px;
  font-family: 'Barlow', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #e0e0e0;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12);
}
html[data-rugby-effective="dark"] .rugby-theme-float {
  background: rgba(22, 33, 62, 0.92);
  border-color: #2a2a4a;
}
.rugby-theme-float__label {
  cursor: pointer;
  opacity: 0.85;
  white-space: nowrap;
}
html[data-rugby-effective="light"] .rugby-theme-float__label {
  color: #444;
}
html[data-rugby-effective="dark"] .rugby-theme-float__label {
  color: #b0c0df;
}
.rugby-theme-float select {
  padding: 3px 6px;
  border-radius: 4px;
  border: 1px solid #ccc;
  font-size: 13px;
  background: #fff;
  color: #333;
  cursor: pointer;
}
html[data-rugby-effective="dark"] .rugby-theme-float select {
  background: #1e2a45;
  color: #e0e0e0;
  border-color: #2a2a4a;
}
@media (max-width: 520px) {
  .rugby-theme-float .rugby-theme-float__label {
    display: none;
  }
}

/* ── Leaflet layer control — align with map header / site chrome (all Folium rugby maps) ── */
.folium-map .leaflet-control-layers {
  border-radius: 8px;
  overflow: visible;
}
.folium-map:not(.rugby-map-dark) .leaflet-control-layers {
  background: #fff;
  color: #333;
  border: 1px solid #e0e0e0;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
}
.folium-map:not(.rugby-map-dark) .leaflet-control-layers-toggle {
  background-color: #fff;
  border: 1px solid #ddd;
}
.folium-map:not(.rugby-map-dark) .leaflet-control-layers-toggle:hover {
  background-color: #f5f8fc;
}
.folium-map .leaflet-control-layers-expanded {
  font-family: 'Barlow', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13px;
  line-height: 1.35;
}
.folium-map:not(.rugby-map-dark) .leaflet-control-layers-expanded {
  padding: 5px;
}
.folium-map.rugby-map-dark .leaflet-control-layers-expanded {
  padding: 5px;
}
.folium-map .leaflet-control-layers-expanded .leaflet-control-layers-list {
  padding: 2px;
}
/* Scroll long overlay lists — shared max-height; typography stays consistent (no oversized mobile fonts). */
.folium-map .leaflet-control-layers-list {
  overflow-y: auto !important;
  max-height: min(70vh, 480px);
}
@media only screen and (max-width: 768px) {
  .folium-map .leaflet-control-layers-list {
    max-height: min(55vh, 360px);
    font-size: 13px;
  }
}
.folium-map:not(.rugby-map-dark) .leaflet-control-layers-separator {
  border-top-color: #e8e8e8;
}

/* Bulk overlay actions (shown when an overlays section exists) */
.folium-map .rugby-layers-bulk-row-wrap {
  padding: 0 3px 6px 3px;
  margin: 2px -2px 4px -2px;
  border-bottom: 1px solid #eaeaea;
}
.folium-map.rugby-map-dark .rugby-layers-bulk-row-wrap {
  border-bottom-color: #2a354f;
}
.folium-map .rugby-layers-bulk-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.folium-map .rugby-layers-bulk-hint {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #666;
  flex: 1 1 100%;
}
.folium-map.rugby-map-dark .rugby-layers-bulk-hint {
  color: #9aaccc;
}
.folium-map .rugby-layers-bulk-btn {
  padding: 4px 10px;
  font-size: 12px;
  font-family: inherit;
  border-radius: 4px;
  border: 1px solid #ccc;
  background: linear-gradient(#fff, #f2f2f2);
  color: #333;
  cursor: pointer;
}
.folium-map .rugby-layers-bulk-btn:hover {
  background: #e9eef5;
}
.folium-map.rugby-map-dark .rugby-layers-bulk-btn {
  background: linear-gradient(#243352, #1e2a45);
  border-color: #3d4f73;
  color: #e0e8f0;
}
.folium-map.rugby-map-dark .rugby-layers-bulk-btn:hover {
  background: #2f4165;
}

/* Tier maps: territory vs markers bulk rows */
.folium-map .rugby-layers-bulk-split {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid #e8e8e8;
}
.folium-map.rugby-map-dark .rugby-layers-bulk-split {
  border-top-color: #2a354f;
}
.folium-map .rugby-layers-bulk-subrow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.folium-map .rugby-layers-bulk-tag {
  min-width: 4.5em;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #555;
}
.folium-map.rugby-map-dark .rugby-layers-bulk-tag {
  color: #9eb6d8;
}

/* ── Zoom stepper (top-left; replaces Leaflet +/- control) ── */
.folium-map .leaflet-control-zoom {
  display: none !important;
}
.rugby-zoom-stepper {
  position: fixed;
  top: var(--rugby-map-chrome-top, 56px);
  left: 10px;
  margin-top: 10px;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0;
  padding: 0;
  border-radius: 4px;
  overflow: hidden;
  font-family: 'Barlow', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13px;
  background: rgba(255, 255, 255, 0.92);
  border: 2px solid rgba(0, 0, 0, 0.2);
  box-shadow: none;
}
html[data-rugby-effective="dark"] .rugby-zoom-stepper {
  background: rgba(22, 33, 62, 0.92);
  border-color: rgba(255, 255, 255, 0.2);
  color: #e0e0e0;
}
.rugby-zoom-stepper__btn {
  width: 30px;
  height: 30px;
  padding: 0;
  border-radius: 0;
  border: none;
  border-bottom: 1px solid #ccc;
  background: #fff;
  color: #333;
  font-size: 18px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-family: inherit;
  box-sizing: border-box;
}
.rugby-zoom-stepper__btn:last-child {
  border-bottom: none;
}
.rugby-zoom-stepper__btn:hover {
  background: #f4f4f4;
}
html[data-rugby-effective="dark"] .rugby-zoom-stepper__btn {
  border-bottom-color: #3a4a66;
  background: #1e2a45;
  color: #e0e8f0;
}
html[data-rugby-effective="dark"] .rugby-zoom-stepper__btn:hover {
  background: #243049;
}
.rugby-zoom-stepper__label {
  width: 30px;
  padding: 0;
  text-align: center;
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  font-weight: 600;
  line-height: 30px;
  user-select: none;
  border-bottom: 1px solid #ccc;
  background: #fff;
  box-sizing: border-box;
}
html[data-rugby-effective="dark"] .rugby-zoom-stepper__label {
  border-bottom-color: #3a4a66;
  background: #1e2a45;
}
@media (max-width: 480px) {
  .rugby-zoom-stepper {
    left: 10px;
  }
  .rugby-zoom-stepper__btn {
    width: 28px;
    height: 28px;
    font-size: 16px;
  }
  .rugby-zoom-stepper__label {
    width: 28px;
    line-height: 28px;
    font-size: 10px;
  }
}

</style>
"""

DARK_MODE_JS = """
<script>
(function() {
    var STORAGE_KEY = "rugbyMapTheme";
    var mq = window.matchMedia("(prefers-color-scheme: dark)");

    function getStoredThemeMode() {
        try {
            var v = localStorage.getItem(STORAGE_KEY);
            if (v === "light" || v === "dark" || v === "system") {
                return v;
            }
        } catch (e) {}
        return "system";
    }

    function isEffectiveDark(mode) {
        if (mode === "dark") {
            return true;
        }
        if (mode === "light") {
            return false;
        }
        return mq.matches;
    }

    var RUGBY_ZOOM_STEP = __RUGBY_ZOOM_STEP__;

    function findMap() {
        var el = document.querySelector(".folium-map");
        if (!el || !el._leaflet_id) {
            return null;
        }
        var map = window[Object.keys(window).find(function(k) {
            return k.startsWith("map_") && window[k] instanceof L.Map;
        })];
        return map || null;
    }

    function rugbyLimitZoom(map, zoom) {
        var min = map.getMinZoom();
        var max = map.getMaxZoom();
        var snap = RUGBY_ZOOM_STEP;
        if (snap) {
            zoom = Math.round(zoom / snap) * snap;
        }
        return Math.max(min, Math.min(max, zoom));
    }

    function applyMapZoomOptions(map) {
        if (!map || !map.options) {
            return;
        }
        map.options.zoomSnap = RUGBY_ZOOM_STEP;
        map.options.zoomDelta = RUGBY_ZOOM_STEP;
    }

    function formatRugbyZoom(zoom) {
        var rounded = Math.round(zoom * 100) / 100;
        var text = rounded.toFixed(2);
        return text.replace(/\\.?0+$/, "") + "x";
    }

    function updateZoomStepperLabel(map) {
        var label = document.getElementById("rugbyZoomStepperLabel");
        if (label && map) {
            label.textContent = formatRugbyZoom(map.getZoom());
        }
    }

    function rugbyStepZoom(delta) {
        var map = findMap();
        if (!map) {
            return;
        }
        applyMapZoomOptions(map);
        map.setZoom(rugbyLimitZoom(map, map.getZoom() + delta));
        updateZoomStepperLabel(map);
    }

    function ensureZoomStepper() {
        if (document.getElementById("rugbyZoomStepper")) {
            return;
        }
        var wrap = document.createElement("div");
        wrap.id = "rugbyZoomStepper";
        wrap.className = "rugby-zoom-stepper";
        wrap.setAttribute("role", "group");
        wrap.setAttribute("aria-label", "Map zoom");
        wrap.innerHTML =
            '<button type="button" class="rugby-zoom-stepper__btn" id="rugbyZoomIn" ' +
            'title="Zoom in" aria-label="Zoom in">+</button>' +
            '<span class="rugby-zoom-stepper__label" id="rugbyZoomStepperLabel">7x</span>' +
            '<button type="button" class="rugby-zoom-stepper__btn" id="rugbyZoomOut" ' +
            'title="Zoom out" aria-label="Zoom out">&minus;</button>';
        document.body.appendChild(wrap);
        document.getElementById("rugbyZoomIn").addEventListener("click", function() {
            rugbyStepZoom(RUGBY_ZOOM_STEP);
        });
        document.getElementById("rugbyZoomOut").addEventListener("click", function() {
            rugbyStepZoom(-RUGBY_ZOOM_STEP);
        });
    }

    function initZoomStepper() {
        var map = findMap();
        if (!map) {
            setTimeout(initZoomStepper, 100);
            return;
        }
        applyMapZoomOptions(map);
        ensureZoomStepper();
        updateZoomStepperLabel(map);
        if (map.__rugbyZoomStepperHooked) {
            return;
        }
        map.__rugbyZoomStepperHooked = true;
        map.on("zoomend", function() {
            updateZoomStepperLabel(map);
        });
    }

    function setMapDarkClass(dark) {
        var el = document.querySelector(".folium-map");
        if (el) {
            el.classList.toggle("rugby-map-dark", dark);
        }
    }

    function applyBasemapTheme() {
        var mode = getStoredThemeMode();
        var dark = isEffectiveDark(mode);
        document.documentElement.setAttribute(
            "data-rugby-effective",
            dark ? "dark" : "light"
        );
        setMapDarkClass(dark);
        var map = findMap();
        if (!map) {
            setTimeout(applyBasemapTheme, 100);
            return;
        }
        applyMapZoomOptions(map);
        map.eachLayer(function(layer) {
            if (!layer._url) {
                return;
            }
            if (dark && layer._url.indexOf("__JM_LIGHT__") !== -1) {
                layer.setUrl(layer._url.replace("__JM_LIGHT__", "__JM_DARK__"));
            } else if (!dark && layer._url.indexOf("__JM_DARK__") !== -1) {
                layer.setUrl(layer._url.replace("__JM_DARK__", "__JM_LIGHT__"));
            }
        });
        if (window.updateBoundaryStyles) {
            window.updateBoundaryStyles(dark);
        }
    }

    function syncThemeSelect() {
        var sel = document.getElementById("rugbyMapThemeSelect");
        if (!sel) {
            return;
        }
        var mode = getStoredThemeMode();
        if (sel.value !== mode) {
            sel.value = mode;
        }
    }

    function ensureFloatingThemeToggle() {
        if (document.getElementById("rugbyMapThemeSelect")) {
            return;
        }
        var wrap = document.createElement("div");
        wrap.className = "rugby-theme-float";
        wrap.innerHTML =
            '<label class="rugby-theme-float__label" for="rugbyMapThemeSelect">Appearance</label>' +
            '<select id="rugbyMapThemeSelect" aria-label="Map color theme">' +
            '<option value="light">Light</option>' +
            '<option value="system">System</option>' +
            '<option value="dark">Dark</option>' +
            "</select>";
        document.body.appendChild(wrap);
    }

    function bindThemeSelectOnce() {
        var sel = document.getElementById("rugbyMapThemeSelect");
        if (!sel || sel.dataset.rugbyThemeBound === "1") {
            return;
        }
        sel.dataset.rugbyThemeBound = "1";
        sel.addEventListener("change", function() {
            try {
                localStorage.setItem(STORAGE_KEY, sel.value);
            } catch (e) {}
            applyBasemapTheme();
        });
    }

    function onPreferColorSchemeChange() {
        if (getStoredThemeMode() === "system") {
            applyBasemapTheme();
        }
    }

    if (mq.addEventListener) {
        mq.addEventListener("change", onPreferColorSchemeChange);
    } else if (mq.addListener) {
        mq.addListener(onPreferColorSchemeChange);
    }

    function initChrome() {
        ensureFloatingThemeToggle();
        bindThemeSelectOnce();
        syncThemeSelect();
        initZoomStepper();
    }

    applyBasemapTheme();
    initZoomStepper();
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initChrome);
    } else {
        initChrome();
    }

    /** @expose for debugging */
    window.setRugbyMapThemeMode = function(mode) {
        if (mode !== "light" && mode !== "dark" && mode !== "system") {
            return;
        }
        try {
            localStorage.setItem(STORAGE_KEY, mode);
        } catch (e) {}
        applyBasemapTheme();
        syncThemeSelect();
    };
})();
</script>
"""

DARK_MODE_JS = (
    DARK_MODE_JS.replace("__JM_LIGHT__", CARTO_THEME_MARK_LIGHT)
    .replace("__JM_DARK__", CARTO_THEME_MARK_DARK)
    .replace("__RUGBY_ZOOM_STEP__", repr(MAP_ZOOM_SNAP))
)


_HATCH_PANE_JS = f"""
    {{{{ this._parent.get_name() }}}}.createPane('{HATCH_PANE}');
    {{{{ this._parent.get_name() }}}}.getPane('{HATCH_PANE}').style.zIndex = {HATCH_PANE_Z_INDEX};
"""


class HatchPane(MacroElement):
    """Creates the Leaflet pane that hatched territories draw into.

    Added while the map is being built so the pane exists before any layer names
    it; Leaflet resolves a layer's pane the moment it is added to the map.
    """

    _template = FoliumTemplate(
        "{% macro script(this, kwargs) %}" + _HATCH_PANE_JS + "{% endmacro %}"
    )

    def __init__(self) -> None:
        super().__init__()
        self._name = "HatchPane"


def _deferred_image_loader_script() -> str:
    """Swap in real crest URLs after territory shading has painted.

    Using a neutral placeholder first prevents the browser from blocking on a
    long list of remote team logos while the map is still being built and
    displayed. Crest fetches are intentionally held until
    ``rugby-map-presentation-ready`` so they do not compete with territory
    GeoJSON on the network or main thread.

    Loaded URLs are tracked in ``rugbyLoadedCrests`` so MarkerCluster can reuse
    cached ``src`` values when it rebuilds cluster icons on zoom instead of
    re-queueing every crest through ``data-real-src``.
    """
    return f"""
    <script>
    (function() {{
        window.RUGBY_CREST_PLACEHOLDER = "{CREST_PLACEHOLDER_SRC}";
        window.rugbyLoadedCrests = window.rugbyLoadedCrests || new Set();
        window.rugbyCrestWarm = window.rugbyCrestWarm || {{}};
        var deferredImagesStarted = false;
        function warmCrest(url) {{
            if (!url || window.rugbyCrestWarm[url]) return;
            var img = new Image();
            img.src = url;
            window.rugbyCrestWarm[url] = img;
        }}
        function markCrestLoaded(src) {{
            if (!src) return;
            window.rugbyLoadedCrests.add(src);
            warmCrest(src);
        }}
        window.rugbyCrestClusterInner = function(imageUrl, onerrorJs) {{
            if (!imageUrl) return "";
            if (window.rugbyLoadedCrests.has(imageUrl)) {{
                var esc = imageUrl.replace(/\\\\/g, "\\\\\\\\").replace(/'/g, "\\\\'");
                return '<div style="width:30px;height:30px;border-radius:50%;background:url(\\'' + esc + '\\') center/cover no-repeat"></div>';
            }}
            var ph = window.RUGBY_CREST_PLACEHOLDER;
            var attr = imageUrl.replace(/"/g, "&quot;");
            return '<img data-real-src="' + attr + '" src="' + ph + '" style="width:30px;height:30px;border-radius:50%;" onerror="' + onerrorJs + '">';
        }};
        function promoteCrestToBackground(img, src) {{
            if (!img || !img.parentNode || !src) return;
            var box = document.createElement("div");
            box.style.cssText = img.style.cssText;
            box.style.background = "url('" + src.replace(/'/g, "%27") + "') center/cover no-repeat";
            img.parentNode.replaceChild(box, img);
        }}
        function activateOne(img) {{
            var src = img.getAttribute("data-real-src");
            if (!src) return;
            var inCluster = img.closest && img.closest(".marker-cluster-custom");
            if (inCluster) {{
                img.loading = "eager";
                if ("decoding" in img) {{
                    img.decoding = "sync";
                }}
            }} else {{
                img.loading = "lazy";
                img.decoding = "async";
                if ("fetchPriority" in img) {{
                    img.fetchPriority = "low";
                }}
            }}
            function finish() {{
                markCrestLoaded(src);
                promoteCrestToBackground(img, src);
            }}
            img.addEventListener("load", finish, {{ once: true }});
            img.setAttribute("src", src);
            img.removeAttribute("data-real-src");
            if (img.complete && img.naturalWidth > 0) {{
                finish();
            }}
        }}
        function activateDeferredImages() {{
            var images = Array.from(document.querySelectorAll("img[data-real-src]"));
            if (!images.length) return;
            var batchSize = 12;
            var index = 0;
            function flushBatch() {{
                var batch = images.slice(index, index + batchSize);
                if (!batch.length) return;
                for (var i = 0; i < batch.length; i++) {{
                    activateOne(batch[i]);
                }}
                index += batch.length;
                if (index < images.length) {{
                    if (window.requestAnimationFrame) {{
                        window.requestAnimationFrame(flushBatch);
                    }} else {{
                        setTimeout(flushBatch, 16);
                    }}
                }}
            }}
            flushBatch();
        }}
        function scheduleDeferredImages() {{
            if (window.requestAnimationFrame) {{
                window.requestAnimationFrame(activateDeferredImages);
                return;
            }}
            setTimeout(activateDeferredImages, 50);
        }}
        function startObserver() {{
            if (!window.MutationObserver || !document.body) return;
            var observer = new MutationObserver(function() {{
                if (window.requestAnimationFrame) {{
                    window.requestAnimationFrame(activateDeferredImages);
                }} else {{
                    setTimeout(activateDeferredImages, 50);
                }}
            }});
            observer.observe(document.body, {{ childList: true, subtree: true }});
        }}
        function startDeferredImages() {{
            if (deferredImagesStarted) return;
            deferredImagesStarted = true;
            scheduleDeferredImages();
            startObserver();
        }}
        document.addEventListener("rugby-map-presentation-ready", startDeferredImages);
    }})();
    </script>
    """


def _build_base_map(config: MapConfig) -> folium.Map:
    m = folium.Map(
        location=list(config.center),
        zoom_start=config.zoom,
        tiles=None,
        zoom_control=False,
        zoom_snap=MAP_ZOOM_SNAP,
        zoom_delta=MAP_ZOOM_DELTA,
        # Canvas repaints the whole territory layer as one bitmap instead of
        # patching hundreds of individual SVG <path> nodes, so it keeps up
        # during fast pans/zooms instead of leaving unrendered gaps until the
        # browser catches up on DOM updates.
        prefer_canvas=True,
    )
    # Leaflet's vector renderer only paints the current viewport plus a small
    # buffer (10% of the map's size by default) and repaints on `moveend` --
    # so panning/zooming faster than that redraw can leave territories
    # unpainted until the gesture settles and the buffer catches up. Widening
    # the buffer to several viewports makes that far less likely to be
    # visible. Must run after leaflet.js loads (head) but before any
    # GeoJson/territory layer is added (body), which is where this script
    # child lands relative to the ones queued below.
    m.get_root().script.add_child(  # type: ignore[attr-defined]
        folium.Element("L.SVG.mergeOptions({padding: 3}); L.Canvas.mergeOptions({padding: 3});")
    )
    m.add_child(HatchPane())
    folium.TileLayer(
        tiles=CARTO_TILE_URL_LIGHT,
        attr=folium_carto_attribution(),
        control=False,
    ).add_to(m)
    header = m.get_root().header  # type: ignore[attr-defined]
    header.add_child(folium.Element(get_resource_hints_html()))
    header.add_child(folium.Element(POPUP_CSS))
    header.add_child(folium.Element(DARK_MODE_JS))
    header.add_child(folium.Element(_deferred_image_loader_script()))
    return m


def _add_marker(
    marker_group: FeatureGroupSubGroup | folium.FeatureGroup,
    item: _PlacedItem,
    color: str,
    tier_order: int | None = None,
    fallback_icon_url: str | None = None,
    league_border: bool = False,
) -> None:
    name_esc = escape(item["name"])
    popup_content = item.get("popup_html") or f'<div class="rugby-popup"><b>{name_esc}</b></div>'
    popup_content = popup_content.replace(
        '<h4 class="popup-title">',
        f'<h4 class="popup-title" style="color: {color};">',
        1,
    )

    itl1 = item.get("itl1") or ""
    itl2 = item.get("itl2") or ""
    itl3 = item.get("itl3") or ""
    if itl1:
        region_html = (
            f'<p class="popup-regions">'
            f"<b>{escape(itl1)}</b> | {escape(itl2)} | <i>{escape(itl3)}</i>"
            f"</p>"
        )
    else:
        region_html = ""
    popup_content = popup_content.replace("__ITL_REGIONS__", region_html)

    icon_size = 30
    icon_url = item.get("icon_url")
    border_css = f"border: 2px solid {color}; " if league_border else ""
    if icon_url:
        if fallback_icon_url:
            onerror = f"this.onerror=null; this.src='{escape(fallback_icon_url)}'"
        else:
            onerror = "this.style.display='none'"
        icon_html = (
            f'<div style="text-align: center;">'
            f'<img data-real-src="{escape(icon_url)}" src="{CREST_PLACEHOLDER_SRC}" '
            f'style="width: {icon_size}px; height: {icon_size}px; border-radius: 50%; '
            f'{border_css}box-shadow: 0 0 3px rgba(0,0,0,0.3); opacity: 0.9;" '
            f'onerror="{onerror}">'
            f"</div>"
        )
    else:
        icon_html = (
            f'<div style="text-align: center;">'
            f'<div style="width: {icon_size}px; height: {icon_size}px; border-radius: 50%; '
            f"background: {color}; border: 2px solid white; "
            f'box-shadow: 0 0 3px rgba(0,0,0,0.3);"></div>'
            f"</div>"
        )

    icon = folium.DivIcon(html=icon_html, icon_size=(icon_size, icon_size), icon_anchor=(15, 15))

    marker = folium.Marker(
        location=[item["latitude"], item["longitude"]],
        popup=folium.Popup(popup_content, max_width=250),
        icon=icon,
        tooltip=name_esc,
    )
    marker.options["tierOrder"] = tier_order  # type: ignore[index]
    marker.options["imageUrl"] = icon_url or ""  # type: ignore[index]
    marker.options["itemName"] = item["name"]  # type: ignore[index]
    marker.add_to(marker_group)


def _add_marker_cluster(m: folium.Map, fallback_icon_url: str | None = None) -> MarkerCluster:
    if fallback_icon_url:
        escaped_fallback = escape(fallback_icon_url)
        onerror_js = f"this.onerror=null; this.src=\\'{escaped_fallback}\\'"
    else:
        onerror_js = "this.style.display=\\'none\\'"
    icon_create_function = f"""
    function(cluster) {{
        var markers = cluster.getAllChildMarkers();
        var bestMarker = null;
        var bestTier = Infinity;
        var names = [];
        for (var i = 0; i < markers.length; i++) {{
            var mk = markers[i];
            if (mk.options.tierOrder !== undefined && mk.options.tierOrder !== null && mk.options.tierOrder < bestTier) {{
                bestTier = mk.options.tierOrder;
                bestMarker = mk;
            }}
            if (mk.options.itemName) {{ names.push(mk.options.itemName); }}
        }}
        names.sort();
        var imageUrl = bestMarker && bestMarker.options.imageUrl ? bestMarker.options.imageUrl : '';
        var count = cluster.getChildCount();
        var tooltipText = names.length > 0 ? names.slice(0, 5).join('\\n') : count + ' items';
        if (imageUrl) {{
            var crestInner = window.rugbyCrestClusterInner
                ? window.rugbyCrestClusterInner(imageUrl, "{onerror_js}")
                : ('<img data-real-src="' + imageUrl + '" src="' + (window.RUGBY_CREST_PLACEHOLDER || '{CREST_PLACEHOLDER_SRC}') + '" style="width:30px;height:30px;border-radius:50%;" onerror="{onerror_js}">');
            return L.divIcon({{
                html: '<div style="text-align:center;position:relative;" title="' + tooltipText.replace(/"/g,'&quot;') + '">' +
                      crestInner +
                      '<span style="position:absolute;bottom:-5px;right:-5px;background:#333;color:white;border-radius:50%;width:16px;height:16px;font-size:10px;line-height:16px;text-align:center;">' + count + '</span></div>',
                className: 'marker-cluster-custom',
                iconSize: L.point(30, 30),
                iconAnchor: L.point(15, 15)
            }});
        }} else {{
            return L.divIcon({{
                html: '<div style="text-align:center;" title="' + tooltipText.replace(/"/g,'&quot;') + '">' +
                      '<div style="width:30px;height:30px;border-radius:50%;background:#666;color:white;font-size:12px;line-height:30px;text-align:center;border:2px solid white;box-shadow:0 0 3px rgba(0,0,0,0.3);">' + count + '</div></div>',
                className: 'marker-cluster-custom',
                iconSize: L.point(30, 30),
                iconAnchor: L.point(15, 15)
            }});
        }}
    }}
    """
    parent_cluster = MarkerCluster(
        control=False,
        options={
            "maxClusterRadius": 1,
            "disableClusteringAtZoom": None,
            "spiderfyOnMaxZoom": True,
            "spiderfyDistanceMultiplier": 2,
            "showCoverageOnHover": False,
            "zoomToBoundsOnClick": False,
            "animate": False,
            "animateAddingMarkers": False,
        },
        icon_create_function=icon_create_function,
    )
    m.add_child(parent_cluster)
    return parent_cluster


def _legend(
    title: str,
    items_by_tier: dict[str, list[_PlacedItem]],
    tier_order: list[str],
    group_colors: dict[str, str],
    hatched_groups: set[str] | None = None,
) -> folium.Element:
    html = f"""
    <style>
    .legend-toggle {{ cursor:pointer; user-select:none; display:inline-block; float:right; font-weight:bold; font-size:18px; }}
    .legend-content.collapsed {{ display:none; }}
    @media only screen and (max-width: 768px) {{
        .map-legend {{ bottom:10px !important; right:10px !important; width:200px !important; max-height:300px !important; font-size:11px !important; padding:8px !important; }}
        .map-legend h4 {{ font-size:13px !important; }}
        .map-legend i {{ width:12px !important; height:12px !important; }}
        .legend-content {{ max-height:250px !important; }}
    }}
    html[data-rugby-effective="dark"] .map-legend {{
        background-color:#16213e !important;
        color:#e0e0e0 !important;
        border-color:#444 !important;
    }}
    html[data-rugby-effective="dark"] .map-legend h4 {{
        color:#e0e8f0;
    }}
    html[data-rugby-effective="dark"] .map-legend b {{
        color:#e0e8f0;
    }}
    </style>
    <div class="map-legend" style="position:fixed; bottom:50px; right:50px; width:300px;
                background-color:white; z-index:999; font-size:14px;
                border:2px solid grey; border-radius:5px; padding:10px">
    <h4 style="margin-top:0;">{escape(title)}
        <span class="legend-toggle" onclick="toggleLegend()" title="Toggle legend">\u2212</span>
    </h4>
    <div class="legend-content" style="overflow-y:auto; max-height:500px;">
    """

    for tier in tier_order:
        if tier not in items_by_tier:
            continue
        tier_items = items_by_tier[tier]
        html += f'<p style="margin:10px 0 5px 0;"><b>{escape(tier)}</b> ({len(tier_items)})</p>'

        by_category: dict[str | None, list[_PlacedItem]] = {}
        for it in tier_items:
            by_category.setdefault(it.get("category"), []).append(it)
        show_sub = len(by_category) > 1

        def _cat_key(c: str | None) -> tuple[int, str]:
            if c is None:
                return (2, "")
            if c.lower() == "pyramid":
                return (0, "")
            return (1, c)

        for cat in sorted(by_category, key=_cat_key):
            cat_items = by_category[cat]
            if show_sub:
                label = escape(cat) if cat else "Other"
                html += f'<p style="margin:6px 0 2px 8px;"><i>{label}</i> ({len(cat_items)})</p>'
            indent = "23px" if show_sub else "15px"
            for grp in sorted({it["group"] for it in cat_items}):
                color = group_colors[grp]
                # Match the map: striped swatch for structures drawn as an overlay.
                swatch = stripe_css_gradient(color) if grp in (hatched_groups or set()) else color
                count = sum(1 for it in cat_items if it["group"] == grp)
                html += (
                    f'<p style="margin:2px 0 2px {indent};">'
                    f'<i style="background:{swatch}; width:16px; height:16px; '
                    f'display:inline-block; border-radius:50%; border:1px solid black;"></i> '
                    f"{escape(grp)} ({count})</p>"
                )

    html += """</div></div>
    <script>
    function toggleLegend() {
        var c = document.querySelector(".legend-content");
        var t = document.querySelector(".legend-toggle");
        if (c.classList.contains("collapsed")) { c.classList.remove("collapsed"); t.textContent = "\u2212"; }
        else { c.classList.add("collapsed"); t.textContent = "+"; }
    }
    (function() {
        if (window.innerWidth <= 768) {
            var c = document.querySelector(".legend-content");
            var t = document.querySelector(".legend-toggle");
            if (c) { c.classList.add("collapsed"); }
            if (t) { t.textContent = "+"; }
        }
    })();
    </script>
    """
    return folium.Element(html)


# Injected into ``Figure.script`` (bottom of page) *before* ``L.control.layers().addTo`` — not
# ``<head>``. A head script runs before Leaflet loads and races the page's layer ``addTo``, so the
# ``L.Control.Layers`` prototype hook never applies and bulk buttons never appear.
_LAYER_CONTROL_HOOK_JS = r"""
(function() {
    window.RUGBY_MERIT_GROUPS = {{ this.merit_groups_json }};
    /** Matches ``FeatureGroup`` names from ``generate_*_group_map`` (territory shading vs crest markers). */
    function rugbyLayerEntryName(ent) {
        return typeof ent.name === 'string' ? ent.name : '';
    }
    function rugbyIsTerritoryOverlay(ent) {
        return rugbyLayerEntryName(ent).indexOf(' - Territory') !== -1;
    }
    function rugbyIsMarkersOverlay(ent) {
        return rugbyLayerEntryName(ent).indexOf(' - Markers') !== -1;
    }
    /** Group name with the trailing ``ent - Territory``/``ent - Markers`` suffix stripped. */
    function rugbyGroupNameFromEntry(ent) {
        var name = rugbyLayerEntryName(ent);
        return name.replace(/ - (Territory|Markers)$/, '');
    }
    function rugbyIsMeritOverlay(ent) {
        return window.RUGBY_MERIT_GROUPS
            ? window.RUGBY_MERIT_GROUPS.indexOf(rugbyGroupNameFromEntry(ent)) !== -1
            : false;
    }
    function rugbyIsNonMeritOverlay(ent) {
        return (rugbyIsTerritoryOverlay(ent) || rugbyIsMarkersOverlay(ent)) && !rugbyIsMeritOverlay(ent);
    }
    function rugbyHasTerritoryMarkerSplit(ctrl) {
        if (!ctrl || typeof ctrl._layers !== 'object') {
            return false;
        }
        var hasT = false, hasM = false, lid;
        for (lid in ctrl._layers) {
            if (!Object.prototype.hasOwnProperty.call(ctrl._layers, lid)) continue;
            var ent = ctrl._layers[lid];
            if (!ent || !ent.overlay) continue;
            if (rugbyIsTerritoryOverlay(ent)) hasT = true;
            if (rugbyIsMarkersOverlay(ent)) hasM = true;
        }
        return hasT && hasM;
    }
    function rugbyHasMeritSplit(ctrl) {
        if (!ctrl || typeof ctrl._layers !== 'object' || !window.RUGBY_MERIT_GROUPS ||
            !window.RUGBY_MERIT_GROUPS.length) {
            return false;
        }
        var hasMerit = false, hasNonMerit = false, lid;
        for (lid in ctrl._layers) {
            if (!Object.prototype.hasOwnProperty.call(ctrl._layers, lid)) continue;
            var ent = ctrl._layers[lid];
            if (!ent || !ent.overlay) continue;
            if (rugbyIsMeritOverlay(ent)) hasMerit = true;
            else if (rugbyIsNonMeritOverlay(ent)) hasNonMerit = true;
        }
        return hasMerit && hasNonMerit;
    }
    function rugbyApplyOverlayBulkFiltered(map, enable, predicate) {
        var ctrl = window.layerControl;
        if (!ctrl || !map || typeof ctrl._layers !== 'object') {
            return;
        }
        var lid;
        for (lid in ctrl._layers) {
            if (!Object.prototype.hasOwnProperty.call(ctrl._layers, lid)) continue;
            var ent = ctrl._layers[lid];
            if (!ent || !ent.overlay || !ent.layer) continue;
            if (predicate && !predicate(ent)) continue;
            if (enable) {
                if (!map.hasLayer(ent.layer)) map.addLayer(ent.layer);
            } else if (map.hasLayer(ent.layer)) {
                map.removeLayer(ent.layer);
            }
        }
    }
    function rugbyInstallBulkToolbar() {
        var ctrl = window.layerControl;
        if (!ctrl || !ctrl._container) return false;
        // Leaflet 1.9+ uses ``<section class="leaflet-control-layers-list">`` (not ``<form>``).
        var panel =
            (ctrl._section && ctrl._section.classList.contains("leaflet-control-layers-list")
                ? ctrl._section
                : null) ||
            ctrl._container.querySelector("section.leaflet-control-layers-list") ||
            ctrl._container.querySelector("form.leaflet-control-layers-list") ||
            ctrl._container.querySelector(".leaflet-control-layers-list");
        if (!panel || panel.dataset.rugbyBulkBar === "1") return true;
        var overlaySec = panel.querySelector(".leaflet-control-layers-overlays");
        if (!overlaySec) return false;
        var hasSplit = rugbyHasTerritoryMarkerSplit(ctrl);
        var hasMeritSplit = rugbyHasMeritSplit(ctrl);
        var globalRow =
            '<div class="rugby-layers-bulk-row rugby-layers-bulk-row--global">' +
            '<button type="button" class="rugby-layers-bulk-btn" data-rugby-bulk-act="all-on">All on</button>' +
            '<button type="button" class="rugby-layers-bulk-btn" data-rugby-bulk-act="all-off">' +
            'All off</button></div>';
        var splitBlock = '';
        if (hasSplit) {
            splitBlock =
                '<div class="rugby-layers-bulk-split">' +
                '<div class="rugby-layers-bulk-subrow">' +
                '<span class="rugby-layers-bulk-tag">Territory</span>' +
                '<button type="button" class="rugby-layers-bulk-btn" data-rugby-bulk-act="terr-on">' +
                'All on</button>' +
                '<button type="button" class="rugby-layers-bulk-btn" data-rugby-bulk-act="terr-off">' +
                'All off</button></div>' +
                '<div class="rugby-layers-bulk-subrow">' +
                '<span class="rugby-layers-bulk-tag">Markers</span>' +
                '<button type="button" class="rugby-layers-bulk-btn" data-rugby-bulk-act="mrk-on">' +
                'All on</button>' +
                '<button type="button" class="rugby-layers-bulk-btn" data-rugby-bulk-act="mrk-off">' +
                'All off</button></div></div>';
        }
        var meritBlock = '';
        if (hasMeritSplit) {
            meritBlock =
                '<div class="rugby-layers-bulk-split">' +
                '<div class="rugby-layers-bulk-subrow">' +
                '<span class="rugby-layers-bulk-tag">Merit</span>' +
                '<button type="button" class="rugby-layers-bulk-btn" data-rugby-bulk-act="merit-on">' +
                'All on</button>' +
                '<button type="button" class="rugby-layers-bulk-btn" data-rugby-bulk-act="merit-off">' +
                'All off</button></div>' +
                '<div class="rugby-layers-bulk-subrow">' +
                '<span class="rugby-layers-bulk-tag">Non-merit</span>' +
                '<button type="button" class="rugby-layers-bulk-btn" data-rugby-bulk-act="nonmerit-on">' +
                'All on</button>' +
                '<button type="button" class="rugby-layers-bulk-btn" data-rugby-bulk-act="nonmerit-off">' +
                'All off</button></div></div>';
        }
        var wrapper = document.createElement('div');
        wrapper.className = 'rugby-layers-bulk-row-wrap';
        wrapper.innerHTML =
            '<div class="rugby-layers-bulk-hint">Overlay layers</div>' + globalRow + splitBlock +
            meritBlock;
        overlaySec.parentNode.insertBefore(wrapper, overlaySec);
        panel.dataset.rugbyBulkBar = "1";
        wrapper.addEventListener(
            'click',
            function (ev) {
                var btn = ev.target.closest('button[data-rugby-bulk-act]');
                if (!btn) return;
                var mapInst = ctrl._map;
                if (!mapInst) return;
                ev.preventDefault();
                ev.stopPropagation();
                var act = btn.getAttribute('data-rugby-bulk-act');
                if (act === 'all-on') rugbyApplyOverlayBulkFiltered(mapInst, true, null);
                else if (act === 'all-off') rugbyApplyOverlayBulkFiltered(mapInst, false, null);
                else if (act === 'terr-on') rugbyApplyOverlayBulkFiltered(mapInst, true, rugbyIsTerritoryOverlay);
                else if (act === 'terr-off') rugbyApplyOverlayBulkFiltered(mapInst, false, rugbyIsTerritoryOverlay);
                else if (act === 'mrk-on') rugbyApplyOverlayBulkFiltered(mapInst, true, rugbyIsMarkersOverlay);
                else if (act === 'mrk-off') rugbyApplyOverlayBulkFiltered(mapInst, false, rugbyIsMarkersOverlay);
                else if (act === 'merit-on') rugbyApplyOverlayBulkFiltered(mapInst, true, rugbyIsMeritOverlay);
                else if (act === 'merit-off') rugbyApplyOverlayBulkFiltered(mapInst, false, rugbyIsMeritOverlay);
                else if (act === 'nonmerit-on') rugbyApplyOverlayBulkFiltered(mapInst, true, rugbyIsNonMeritOverlay);
                else if (act === 'nonmerit-off') rugbyApplyOverlayBulkFiltered(mapInst, false, rugbyIsNonMeritOverlay);
            },
            true,
        );
        return true;
    }
    function rugbyScheduleBulkRetries() {
        var tries = 0;
        function step() {
            if (rugbyInstallBulkToolbar()) return;
            tries += 1;
            if (tries >= 140) return;
            setTimeout(step, 50);
        }
        step();
    }
    function hookLayerControlCtor() {
        if (!window.L || !L.Control || !L.Control.Layers) {
            setTimeout(hookLayerControlCtor, 50);
            return;
        }
        if (L.Control.Layers.prototype._layerControlHooked) return;
        L.Control.Layers.prototype._layerControlHooked = true;
        var orig = L.Control.Layers.prototype.addTo;
        L.Control.Layers.prototype.addTo = function (map) {
            var ret = orig.call(this, map);
            window.layerControl = this;
            rugbyScheduleBulkRetries();
            return ret;
        };
    }
    hookLayerControlCtor();
})();
"""


class LayerControlHook(MacroElement):
    """Adds hook + bulk overlay UI in the figure script *before* ``LayerControl``'s ``addTo``."""

    _template = FoliumTemplate(
        "{% macro script(this, kwargs) %}" + _LAYER_CONTROL_HOOK_JS + "{% endmacro %}"
    )

    def __init__(self, merit_groups: set[str] | None = None) -> None:
        super().__init__()
        self._name = "LayerControlHook"
        self.merit_groups_json = json.dumps(sorted(merit_groups or ()))


def _add_layer_control(m: folium.Map, merit_groups: set[str] | None = None) -> None:
    m.add_child(LayerControlHook(merit_groups))
    folium.LayerControl().add_to(m)


# ---------------------------------------------------------------------------
# Boundary loader JavaScript
# ---------------------------------------------------------------------------


def _get_boundary_loader_script(config: MapConfig) -> str:
    if config.use_inline_boundaries:
        boundaries_path = Path(config.inline_boundaries_file)
        bd_json = "{}"
        if boundaries_path.exists():
            bd_json = boundaries_path.read_text()
        return f"""
    <script>
    (function() {{
        var _countryLayers = [], _itlLayers = [];
        var _lightCountry = {{ fillColor:'lightgray', color:'black', weight:2, fillOpacity:0.1 }};
        var _darkCountry  = {{ fillColor:'darkgray', color:'#ccc', weight:2, fillOpacity:0.1 }};
        var _lightITL     = {{ fillColor:'transparent', color:'gray', weight:0.5, fillOpacity:0, opacity:0.4 }};
        var _darkITL      = {{ fillColor:'transparent', color:'lightgray', weight:0.5, fillOpacity:0, opacity:0.4 }};
        window.updateBoundaryStyles = function(dark) {{
            var cs = dark ? _darkCountry : _lightCountry;
            var bs = dark ? _darkITL : _lightITL;
            _countryLayers.forEach(function(ly) {{ ly.setStyle(cs); }});
            _itlLayers.forEach(function(ly) {{ ly.setStyle(bs); }});
        }};
        function addBoundaries() {{
            var el = document.querySelector('.folium-map');
            if (!el || !el._leaflet_id) {{ setTimeout(addBoundaries, 100); return; }}
            var map = window[Object.keys(window).find(k => k.startsWith('map_') && window[k] instanceof L.Map)];
            if (!map) {{ setTimeout(addBoundaries, 100); return; }}
            var dark = el.classList.contains('rugby-map-dark');
            const bd = {bd_json};
            var cs = dark ? _darkCountry : _lightCountry;
            Object.entries(bd.countries || {{}}).forEach(([n, d]) => {{ var ly = L.geoJson(d, {{style:cs}}); ly.addTo(map); _countryLayers.push(ly); }});
            var bs = dark ? _darkITL : _lightITL;
            ['itl_1','itl_2','itl_3'].forEach(lv => {{ if (bd[lv]) {{ var ly = L.geoJson(bd[lv], {{style:bs}}); ly.addTo(map); _itlLayers.push(ly); }} }});
        }}
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', addBoundaries);
        else addBoundaries();
    }})();
    </script>
    """
    else:
        sp = config.shared_boundaries_path
        return f"""
    <script>
    (function() {{
        var _countryLayers = [], _itlLayers = [];
        var _lightCountry = {{ fillColor:'lightgray', color:'black', weight:2, fillOpacity:0.1 }};
        var _darkCountry  = {{ fillColor:'darkgray', color:'#ccc', weight:2, fillOpacity:0.1 }};
        var _lightITL     = {{ fillColor:'transparent', color:'gray', weight:0.5, fillOpacity:0, opacity:0.4 }};
        var _darkITL      = {{ fillColor:'transparent', color:'lightgray', weight:0.5, fillOpacity:0, opacity:0.4 }};
        window.updateBoundaryStyles = function(dark) {{
            var cs = dark ? _darkCountry : _lightCountry;
            var bs = dark ? _darkITL : _lightITL;
            _countryLayers.forEach(function(ly) {{ ly.setStyle(cs); }});
            _itlLayers.forEach(function(ly) {{ ly.setStyle(bs); }});
        }};
        function addBoundaries() {{
            var el = document.querySelector('.folium-map');
            if (!el || !el._leaflet_id) {{ setTimeout(addBoundaries, 100); return; }}
            var map = window[Object.keys(window).find(k => k.startsWith('map_') && window[k] instanceof L.Map)];
            if (!map) {{ setTimeout(addBoundaries, 100); return; }}
            var dark = el.classList.contains('rugby-map-dark');
            fetch('{sp}/boundaries.json').then(r => r.json()).then(bd => {{
                var cs = dark ? _darkCountry : _lightCountry;
                Object.entries(bd.countries).forEach(([n, d]) => {{ var ly = L.geoJson(d, {{style:cs}}); ly.addTo(map); _countryLayers.push(ly); }});
                var bs = dark ? _darkITL : _lightITL;
                ['itl_1','itl_2','itl_3'].forEach(lv => {{ if (bd[lv]) {{ var ly = L.geoJson(bd[lv], {{style:bs}}); ly.addTo(map); _itlLayers.push(ly); }} }});
            }}).catch(e => console.warn('Could not load shared boundaries:', e));
        }}
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', addBoundaries);
        else addBoundaries();
    }})();
    </script>
    """


def _get_debug_boundary_loader_script(config: MapConfig) -> str:
    if config.use_inline_boundaries:
        boundaries_path = Path(config.inline_boundaries_file)
        bd_json = "{}"
        if boundaries_path.exists():
            bd_json = boundaries_path.read_text()
        return f"""
    <script>
    (function() {{
        function addDebug() {{
            var el = document.querySelector('.folium-map');
            if (!el || !el._leaflet_id) {{ setTimeout(addDebug, 100); return; }}
            var map = window[Object.keys(window).find(k => k.startsWith('map_') && window[k] instanceof L.Map)];
            if (!map) {{ setTimeout(addDebug, 100); return; }}
            const bd = {bd_json};
            const ds = {{ fillColor:'transparent', color:'red', weight:2, fillOpacity:0 }};
            const layers = {{
                'Debug: ITL1 Boundaries': bd.itl_1, 'Debug: ITL2 Boundaries': bd.itl_2,
                'Debug: ITL3 Boundaries': bd.itl_3, 'Debug: LAD Boundaries': bd.lad,
                'Debug: Ward Boundaries': bd.wards
            }};
            Object.entries(layers).forEach(([name, data]) => {{
                if (data) {{ var ly = L.geoJson(data, {{style:ds}}); if (window.layerControl) window.layerControl.addOverlay(ly, name); }}
            }});
        }}
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', addDebug);
        else addDebug();
    }})();
    </script>
    """
    else:
        sp = config.shared_boundaries_path
        return f"""
    <script>
    (function() {{
        function addDebug() {{
            var el = document.querySelector('.folium-map');
            if (!el || !el._leaflet_id) {{ setTimeout(addDebug, 100); return; }}
            var map = window[Object.keys(window).find(k => k.startsWith('map_') && window[k] instanceof L.Map)];
            if (!map) {{ setTimeout(addDebug, 100); return; }}
            fetch('{sp}/boundaries.json').then(r => r.json()).then(bd => {{
                const ds = {{ fillColor:'transparent', color:'red', weight:2, fillOpacity:0 }};
                const layers = {{
                    'Debug: ITL1 Boundaries': bd.itl_1, 'Debug: ITL2 Boundaries': bd.itl_2,
                    'Debug: ITL3 Boundaries': bd.itl_3, 'Debug: LAD Boundaries': bd.lad,
                    'Debug: Ward Boundaries': bd.wards
                }};
                Object.entries(layers).forEach(([name, data]) => {{
                    if (data) {{ var ly = L.geoJson(data, {{style:ds}}); if (window.layerControl) window.layerControl.addOverlay(ly, name); }}
                }});
            }}).catch(e => console.warn('Could not load debug boundaries:', e));
        }}
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', addDebug);
        else addDebug();
    }})();
    </script>
    """


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _resolve_levels(tier_num: int, config: MapConfig) -> tuple[str, str]:
    """Return the (entry_level, floor_level) for a tier given the map config."""
    if config.tier_entry_level and tier_num in config.tier_entry_level:
        entry = config.tier_entry_level[tier_num]
    else:
        entry = config.default_tier_entry_level
    if config.tier_floor_level and tier_num in config.tier_floor_level:
        floor = config.tier_floor_level[tier_num]
    else:
        floor = config.default_tier_floor_level
    return entry, floor


def _territory_cache_key(
    items: list[_PlacedItem], config: MapConfig
) -> tuple[str, str, frozenset[tuple[str, str]]]:
    """Build a hashable cache key for a set of items sharing one tier."""
    tier_num = items[0].get("tier_num", 999) if items else 999
    entry, floor = _resolve_levels(tier_num, config)
    names = frozenset((it["name"], it["group"]) for it in items)
    return (entry, floor, names)


def generate_single_group_map(
    items: list[MarkerItem],
    output_path: Path,
    itl_hierarchy: ITLHierarchy,
    config: MapConfig,
    territory_cache: TerritoryCache | None = None,
) -> None:
    """Generate a map where all items share one tier, with groups as toggleable layers."""
    if not items:
        return

    items_by_tier, _ = _items_to_placed(items)
    region_to_items = _assign_items_to_itl_regions(items_by_tier, itl_hierarchy)

    all_placed: list[_PlacedItem] = []
    for placed_list in items_by_tier.values():
        all_placed.extend(placed_list)
    if not all_placed:
        return

    m = _build_base_map(config)
    root = m.get_root()
    header = root.header  # type: ignore[attr-defined]
    html_el = root.html  # type: ignore[attr-defined]

    header.add_child(folium.Element(f"<title>{escape(config.html_title or config.title)}</title>"))
    for elem in config.header_elements:
        header.add_child(folium.Element(elem))

    hatched_structures = set(config.hatched_structures)
    structure_of_group = {it["group"]: it["structure"] for it in all_placed}
    # Solid structures first, so they take the leading palette entries and head
    # the layer list, with any overlaid structures grouped after them.
    group_names = sorted(
        structure_of_group, key=lambda g: (structure_of_group[g] in hatched_structures, g)
    )
    group_colors = {grp: _pick_color(config.color_palette, j) for j, grp in enumerate(group_names)}
    hatched_groups = {g for g in group_names if structure_of_group[g] in hatched_structures}
    hatch_defs_html, territory_styles = _territory_styles(group_colors, hatched_groups)

    parent_cluster = _add_marker_cluster(m, fallback_icon_url=config.fallback_icon_url)
    shading_groups: dict[str, folium.FeatureGroup] = {}
    marker_groups: dict[str, FeatureGroupSubGroup] = {}
    for grp in group_names:
        shading_groups[grp] = folium.FeatureGroup(name=f"{grp} - Territory", show=True)
        marker_groups[grp] = FeatureGroupSubGroup(
            parent_cluster, name=f"{grp} - Markers", show=config.markers_shown_by_default
        )
        m.add_child(shading_groups[grp])
        m.add_child(marker_groups[grp])

    # One partition per structure. Leagues in parallel structures genuinely share
    # ground, so shading them together would force the splitter to award every
    # region to just one of them.
    merged_all: _TerritoryMerged = {}
    for structure in sorted(
        {it["structure"] for it in all_placed}, key=lambda s: (s in hatched_structures, s)
    ):
        structure_placed = [it for it in all_placed if it["structure"] == structure]
        cache_key = (
            _territory_cache_key(structure_placed, config) if territory_cache is not None else None
        )
        if cache_key is not None and cache_key in territory_cache:  # type: ignore[operator]
            merged_all.update(territory_cache[cache_key])  # type: ignore[index]
            continue
        geoms = _collect_group_geometries(
            structure_placed, region_to_items, itl_hierarchy, group_colors, config
        )
        merged = _merge_territories(geoms)
        if territory_cache is not None and cache_key is not None:
            territory_cache[cache_key] = merged
        merged_all.update(merged)

    territory_export: dict[str, Any] = {}
    for grp, fg in shading_groups.items():
        entry = {grp: merged_all[grp]} if grp in merged_all else {}
        if config.external_territories:
            exported = _collect_territory_export(fg, entry, territory_styles)
            if exported is not None:
                var_name, data = exported
                territory_export[var_name] = data
        else:
            _render_territories(fg, entry, territory_styles)

    if config.label_territories:
        _add_territory_labels(shading_groups, merged_all, config.zoom)
        html_el.add_child(folium.Element(_get_territory_label_zoom_script()))

    for it in all_placed:
        _add_marker(
            marker_groups[it["group"]],
            it,
            group_colors[it["group"]],
            tier_order=0,
            fallback_icon_url=config.fallback_icon_url,
            league_border=True,
        )

    _add_layer_control(m, hatched_groups)

    html_el.add_child(folium.Element(_get_boundary_loader_script(config)))
    if config.show_debug:
        html_el.add_child(folium.Element(_get_debug_boundary_loader_script(config)))
    if territory_export:
        header.add_child(
            folium.Element(_get_territory_loader_script(config.territories_sidecar_name))
        )
        header.add_child(
            folium.Element(_get_territories_preload_link(config.territories_sidecar_name))
        )

    if hatch_defs_html:
        html_el.add_child(folium.Element(hatch_defs_html))

    legend_title = (
        f"{config.title} - {len(all_placed)}" if config.show_legend_item_count else config.title
    )
    html_el.add_child(
        _legend(
            legend_title,
            items_by_tier,
            list(items_by_tier.keys()),
            group_colors,
            hatched_groups,
        )
    )

    for elem in config.body_elements:
        html_el.add_child(folium.Element(elem))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(output_path)
    if territory_export:
        _write_territories_sidecar(output_path, config.territories_sidecar_name, territory_export)
    _finalize_map_html(output_path, territory_export=bool(territory_export))
    rewrite_cdn_urls_in_html(output_path, root_relative=get_config().is_production)
    logger.info("Saved %s map with %d items to: %s", config.title, len(all_placed), output_path)


def generate_multi_group_map(
    items: list[MarkerItem],
    output_path: Path,
    itl_hierarchy: ITLHierarchy,
    config: MapConfig,
    territory_cache: TerritoryCache | None = None,
) -> None:
    """Generate a map with multiple tiers, each tier as a toggleable layer group."""
    if not items:
        return

    items_by_tier, tier_numbers = _items_to_placed(items)
    region_to_items = _assign_items_to_itl_regions(items_by_tier, itl_hierarchy)

    sorted_tier_names = sorted(items_by_tier.keys(), key=lambda t: tier_numbers[t])

    m = _build_base_map(config)
    root = m.get_root()
    header = root.header  # type: ignore[attr-defined]
    html_el = root.html  # type: ignore[attr-defined]

    header.add_child(folium.Element(f"<title>{escape(config.html_title or config.title)}</title>"))
    for elem in config.header_elements:
        header.add_child(folium.Element(elem))

    groups_by_tier: dict[str, set[str]] = {
        tier: {it["group"] for it in placed} for tier, placed in items_by_tier.items()
    }
    group_colors: dict[str, str] = {}
    for tier_idx, tier in enumerate(sorted_tier_names):
        for j, grp in enumerate(sorted(groups_by_tier.get(tier, set()))):
            group_colors[grp] = _pick_color(config.color_palette, tier_idx + j)
    _, territory_styles = _territory_styles(group_colors, set())

    territory_groups: dict[str, folium.FeatureGroup] = {}
    marker_groups: dict[str, FeatureGroupSubGroup] = {}
    sorted_tiers = [t for t in sorted_tier_names if t in items_by_tier]

    parent_cluster = _add_marker_cluster(m, fallback_icon_url=config.fallback_icon_url)
    for tier in sorted_tiers:
        territory_groups[tier] = folium.FeatureGroup(name=f"{tier} - Territory", show=False)
        marker_groups[tier] = FeatureGroupSubGroup(
            parent_cluster, name=f"{tier} - Markers", show=True
        )
        m.add_child(territory_groups[tier])
        m.add_child(marker_groups[tier])

    territory_export: dict[str, Any] = {}
    for tier, placed in sorted(items_by_tier.items()):
        cache_key = _territory_cache_key(placed, config) if territory_cache is not None else None
        if cache_key is not None and cache_key in territory_cache:  # type: ignore[operator]
            merged = territory_cache[cache_key]  # type: ignore[index]
        else:
            geoms = _collect_group_geometries(
                placed, region_to_items, itl_hierarchy, group_colors, config
            )
            merged = _merge_territories(geoms)
            if territory_cache is not None and cache_key is not None:
                territory_cache[cache_key] = merged
        if config.external_territories:
            exported = _collect_territory_export(territory_groups[tier], merged, territory_styles)
            if exported is not None:
                var_name, data = exported
                territory_export[var_name] = data
        else:
            _render_territories(territory_groups[tier], merged, territory_styles)

    tier_order_map = {tier: idx for idx, tier in enumerate(sorted_tier_names)}
    num_items = 0
    for tier in reversed(sorted_tiers):
        for it in items_by_tier[tier]:
            _add_marker(
                marker_groups[tier],
                it,
                group_colors[it["group"]],
                tier_order_map.get(tier, 999),
                fallback_icon_url=config.fallback_icon_url,
            )
            num_items += 1

    _add_layer_control(m)

    html_el.add_child(folium.Element(_get_boundary_loader_script(config)))
    if config.show_debug:
        html_el.add_child(folium.Element(_get_debug_boundary_loader_script(config)))
    if territory_export:
        header.add_child(
            folium.Element(_get_territory_loader_script(config.territories_sidecar_name))
        )
        header.add_child(
            folium.Element(_get_territories_preload_link(config.territories_sidecar_name))
        )

    html_el.add_child(
        _legend(f"{config.title} - {num_items}", items_by_tier, sorted_tiers, group_colors)
    )

    for elem in config.body_elements:
        html_el.add_child(folium.Element(elem))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(output_path)
    if territory_export:
        _write_territories_sidecar(output_path, config.territories_sidecar_name, territory_export)
    _finalize_map_html(output_path, territory_export=bool(territory_export))
    rewrite_cdn_urls_in_html(output_path, root_relative=get_config().is_production)
    logger.info("Saved %s map with %d items to: %s", config.title, num_items, output_path)
