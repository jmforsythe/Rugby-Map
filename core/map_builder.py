"""
Generic geographic map generation module.

Plots groups of geocoded points on Folium/Leaflet maps with territory shading,
marker clustering, legends, and layer controls. Has no knowledge of any specific
sport, league structure, or data source -- the caller provides pre-built
MarkerItem objects and a MapConfig with all project-specific settings.
"""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, TypedDict, cast

import folium
import numpy as np
from folium.plugins import FeatureGroupSubGroup, MarkerCluster
from scipy.spatial import Voronoi
from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import PreparedGeometry, prep

from core.basemap_tiles import (
    CARTO_THEME_MARK_DARK,
    CARTO_THEME_MARK_LIGHT,
    CARTO_TILE_URL_LIGHT,
    folium_carto_attribution,
)

logger = logging.getLogger(__name__)


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


@dataclass
class MapConfig:
    """Project-specific settings passed by the caller."""

    title: str
    color_palette: list[str]
    center: tuple[float, float] = (52.5, -1.5)
    zoom: int = 7
    show_debug: bool = True
    tier_entry_level: dict[int, str] = field(default_factory=dict)
    default_tier_entry_level: str = "itl2"
    tier_floor_level: dict[int, str] = field(default_factory=dict)
    default_tier_floor_level: str = "itl3"
    use_inline_boundaries: bool = True
    inline_boundaries_file: str = "dist/shared/boundaries.json"
    shared_boundaries_path: str = "../shared"
    fallback_icon_url: str | None = None
    header_elements: list[str] = field(default_factory=list)
    body_elements: list[str] = field(default_factory=list)


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

    with open(output_path, "w") as fout:
        json.dump(boundary_data, fout, separators=(",", ":"))

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
        best = min(
            parent_items,
            key=lambda it: centroid.distance(Point(it["longitude"], it["latitude"])),
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
                        for ck in children_with:
                            narrow.extend(split_region(child_level_check, ck, items_here))
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


def _render_territories(
    feature_group: folium.FeatureGroup,
    merged_geojson: _TerritoryMerged,
    group_colors: dict[str, str],
) -> None:
    """Add pre-merged GeoJSON territory layers to *feature_group*."""
    for grp, geojson_dict in merged_geojson.items():
        color = group_colors[grp]

        def style_fn(feature: Any, c: str = color) -> dict[str, Any]:
            return {"fillColor": c, "color": c, "weight": 1, "fillOpacity": 0.6, "opacity": 0.6}

        folium.GeoJson(geojson_dict, style_function=style_fn).add_to(feature_group)


# ---------------------------------------------------------------------------
# Folium map components
# ---------------------------------------------------------------------------


POPUP_CSS = """
<style>
/* Scope under .folium-map so rules win over Leaflet defaults (load order). */
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
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
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
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
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
    }

    applyBasemapTheme();
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

DARK_MODE_JS = DARK_MODE_JS.replace("__JM_LIGHT__", CARTO_THEME_MARK_LIGHT).replace(
    "__JM_DARK__", CARTO_THEME_MARK_DARK
)


def _build_base_map(config: MapConfig) -> folium.Map:
    m = folium.Map(location=list(config.center), zoom_start=config.zoom, tiles=None)
    folium.TileLayer(
        tiles=CARTO_TILE_URL_LIGHT,
        attr=folium_carto_attribution(),
        control=False,
    ).add_to(m)
    header = m.get_root().header  # type: ignore[attr-defined]
    header.add_child(folium.Element(POPUP_CSS))
    header.add_child(folium.Element(DARK_MODE_JS))
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
            f'<img src="{escape(icon_url)}" '
            f'style="width: {icon_size}px; height: {icon_size}px; border-radius: 50%; '
            f'{border_css}box-shadow: 0 0 3px rgba(0,0,0,0.3);" '
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
            return L.divIcon({{
                html: '<div style="text-align:center;position:relative;" title="' + tooltipText.replace(/"/g,'&quot;') + '">' +
                      '<img src="' + imageUrl + '" style="width:30px;height:30px;border-radius:50%;" onerror="{onerror_js}">' +
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
                html += (
                    f'<p style="margin:6px 0 2px 8px;">' f"<i>{label}</i> ({len(cat_items)})</p>"
                )
            indent = "23px" if show_sub else "15px"
            for grp in sorted({it["group"] for it in cat_items}):
                color = group_colors[grp]
                count = sum(1 for it in cat_items if it["group"] == grp)
                html += (
                    f'<p style="margin:2px 0 2px {indent};">'
                    f'<i style="background:{color}; width:16px; height:16px; '
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


def _add_layer_control(m: folium.Map) -> None:
    folium.LayerControl().add_to(m)
    header = m.get_root().header  # type: ignore[attr-defined]
    header.add_child(folium.Element("""
    <script>
    (function hookLayerControl() {
        if (!window.L || !L.Control || !L.Control.Layers) { setTimeout(hookLayerControl, 50); return; }
        if (L.Control.Layers.prototype._layerControlHooked) { return; }
        var orig = L.Control.Layers.prototype.addTo;
        L.Control.Layers.prototype._layerControlHooked = true;
        L.Control.Layers.prototype.addTo = function(map) { var r = orig.call(this, map); window.layerControl = this; return r; };
    })();
    </script>
    """))
    header.add_child(folium.Element("""
    <style>
    .leaflet-control-layers-list { overflow-y: auto !important; }
    @media only screen and (max-width: 768px) { .leaflet-control-layers-list { font-size: large !important; } }
    </style>
    """))


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

    header.add_child(folium.Element(f"<title>{escape(config.title)}</title>"))
    for elem in config.header_elements:
        header.add_child(folium.Element(elem))

    group_names = sorted({it["group"] for it in all_placed})
    group_colors = {grp: _pick_color(config.color_palette, j) for j, grp in enumerate(group_names)}

    parent_cluster = _add_marker_cluster(m, fallback_icon_url=config.fallback_icon_url)
    shading_groups: dict[str, folium.FeatureGroup] = {}
    marker_groups: dict[str, FeatureGroupSubGroup] = {}
    for grp in group_names:
        shading_groups[grp] = folium.FeatureGroup(name=f"{grp} - Territory", show=True)
        marker_groups[grp] = FeatureGroupSubGroup(
            parent_cluster, name=f"{grp} - Markers", show=True
        )
        m.add_child(shading_groups[grp])
        m.add_child(marker_groups[grp])

    cache_key = _territory_cache_key(all_placed, config) if territory_cache is not None else None
    if cache_key is not None and cache_key in territory_cache:  # type: ignore[operator]
        merged_all = territory_cache[cache_key]  # type: ignore[index]
    else:
        geoms = _collect_group_geometries(
            all_placed, region_to_items, itl_hierarchy, group_colors, config
        )
        merged_all = _merge_territories(geoms)
        if territory_cache is not None and cache_key is not None:
            territory_cache[cache_key] = merged_all
    for grp, fg in shading_groups.items():
        _render_territories(fg, {grp: merged_all[grp]} if grp in merged_all else {}, group_colors)

    for it in all_placed:
        _add_marker(
            marker_groups[it["group"]],
            it,
            group_colors[it["group"]],
            tier_order=0,
            fallback_icon_url=config.fallback_icon_url,
            league_border=True,
        )

    _add_layer_control(m)

    html_el.add_child(folium.Element(_get_boundary_loader_script(config)))
    if config.show_debug:
        html_el.add_child(folium.Element(_get_debug_boundary_loader_script(config)))

    html_el.add_child(
        _legend(
            f"{config.title} - {len(all_placed)}",
            items_by_tier,
            list(items_by_tier.keys()),
            group_colors,
        )
    )

    for elem in config.body_elements:
        html_el.add_child(folium.Element(elem))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(output_path)
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

    header.add_child(folium.Element(f"<title>{escape(config.title)}</title>"))
    for elem in config.header_elements:
        header.add_child(folium.Element(elem))

    groups_by_tier: dict[str, set[str]] = {
        tier: {it["group"] for it in placed} for tier, placed in items_by_tier.items()
    }
    group_colors: dict[str, str] = {}
    for tier_idx, tier in enumerate(sorted_tier_names):
        for j, grp in enumerate(sorted(groups_by_tier.get(tier, set()))):
            group_colors[grp] = _pick_color(config.color_palette, tier_idx + j)

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
        _render_territories(territory_groups[tier], merged, group_colors)

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

    html_el.add_child(
        _legend(f"{config.title} - {num_items}", items_by_tier, sorted_tiers, group_colors)
    )

    for elem in config.body_elements:
        html_el.add_child(folium.Element(elem))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(output_path)
    logger.info("Saved %s map with %d items to: %s", config.title, num_items, output_path)
