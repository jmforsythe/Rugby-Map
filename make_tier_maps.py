import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, TypedDict

import folium
import numpy as np
from folium.plugins import FeatureGroupSubGroup, MarkerCluster
from scipy.spatial import Voronoi
from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import PreparedGeometry, prep

from utils import (
    MapTeam,
    TravelDistances,
    get_google_analytics_script,
    json_load_cache,
    team_name_to_filepath,
)

# Extended type definitions for mapping (adds geospatial fields to base types)


# Type definitions for ITL region data with geometry
class ITLRegionGeom(TypedDict):
    """ITL region with geospatial data"""

    name: str
    code: str | None
    geom: BaseGeometry
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
    ward_to_lad: dict[str, str]
    itl1_to_itl2s: dict[str, list[str]]
    itl0_to_itl1s: dict[str, list[str]]
    itl2_to_itl3s: dict[str, list[str]]
    itl3_to_lads: dict[str, list[str]]
    lad_to_wards: dict[str, list[str]]


class RegionToTeams(TypedDict):
    itl0: dict[str, list[MapTeam]]
    itl1: dict[str, list[MapTeam]]
    itl2: dict[str, list[MapTeam]]
    itl3: dict[str, list[MapTeam]]
    lad: dict[str, list[MapTeam]]
    ward: dict[str, list[MapTeam]]


def extract_tier(filename: str, season: str = "2025-2026") -> tuple[int, str]:
    tier = extract_tier_men(filename, season)
    if tier is None:
        tier = extract_tier_women(filename, season)
    if tier is None:
        print("Warning: Could not extract tier from filename:", filename, "for season:", season)
        return (999, "Unknown Tier")
    return tier


def extract_tier_men(filename: str, season: str) -> tuple[int, str]:
    season_start_year = int(season.split("-")[0])
    if season_start_year <= 2021:
        return extract_tier_men_pre_2021(filename, season)
    else:
        return extract_tier_men_current(filename, season)


def extract_tier_women(filename: str, season: str) -> tuple[int, str]:
    season_start_year = int(season.split("-")[0])
    if season_start_year <= 2018:
        return extract_tier_women_pre_2018(filename, season)
    else:
        return extract_tier_women_current(filename, season)


def extract_tier_men_current(filename: str, season: str) -> tuple[int, str]:
    """Extract tier from 2022-2023 onwards filename format."""
    if filename.startswith("Premiership"):
        return (1, "Premiership")
    if filename.startswith("Championship"):
        return (2, "Championship")
    if filename.startswith("National_League_1"):
        return (3, "National League 1")
    if filename.startswith("National_League_2"):
        return (4, "National League 2")
    if filename.startswith("Regional_1"):
        return (5, "Regional 1")
    if filename.startswith("Regional_2"):
        return (6, "Regional 2")
    if filename.startswith("Counties_1"):
        return (7, "Counties 1")
    if filename.startswith("Counties_2"):
        return (8, "Counties 2")
    if filename.startswith("Counties_3"):
        return (9, "Counties 3")
    if filename.startswith("Counties_4"):
        return (10, "Counties 4")
    if filename.startswith("Counties_5"):
        return (11, "Counties 5")
    if filename.startswith("Cumbria_Conference"):
        if filename.endswith("1.json"):
            return (8, "Counties 2")
        if filename.endswith("2.json"):
            return (9, "Counties 3")
    return None


def extract_tier_women_current(filename: str, season: str) -> tuple[int, str] | None:
    if filename.startswith("Women's_Premiership"):
        return (101, "Premiership Women's")
    if filename.startswith("Women's_Championship"):
        if filename.endswith("1.json"):
            return (102, "Championship 1")
        if filename.endswith("2.json"):
            return (103, "Championship 2")
    if filename.startswith("Women's_NC_1"):
        return (104, "National Challenge 1")
    if filename.startswith("Women's_NC_2"):
        return (105, "National Challenge 2")
    if filename.startswith("Women's_NC_3"):
        return (106, "National Challenge 3")
    return None


def extract_tier_men_pre_2021(filename: str, season: str) -> tuple[int, str] | None:
    """Extract tier from 2021-2022 and earlier filename format."""
    # set "zeroth tier" of prefix, numbers will be used as offsets
    filename = (
        filename.removeprefix("Tribute_")
        .removeprefix("Wadworth_")
        .removeprefix("Harvey's_of_")
        .removeprefix("Harvey\u2019s_Brewery_")
        .removeprefix("Greene_King_IPA_")
        .removeprefix("Shepherd_Neame_")
        .removeprefix("6X_")
        .removeprefix("Snows_Group_")
        .removeprefix("SSE_")
    )

    zeroth_tier_map = {
        "National_League": 2,
        "North_Lancs_Cumbria": 7,
        "North_Lancashire": 7,
        "North": 5,
        "Midlands": 5,
        "London": 5,
        "South_West": 5,
        "Cumbria": (6 if season >= "2018-2019" else 8),
        "Durham_Northumberland": 6,
        "Essex": 8,
        "Eastern_Counties": 8,
        "Hampshire": (9 if season >= "2018-2019" else 8),
        "Sussex": 8,
        "Herts_Middlesex": 8,
        "Kent": 8,
        "Surrey": 8,
        "Berks_Bucks_&_Oxon": 8,
        "Cornwall_Devon": 8,
        "Cornwall": 8,
        "Devon": 8,
        "Dorset_&_Wilts": 8,
        "Dorset": 7,
        "Gloucester": 8,
        "Somerset": 8,
        "Southern_Counties": 7,
        "Western_Counties": 7,
        "Yorkshire": 6,
        "Lancs_Cheshire": (7 if season >= "2018-2019" else 6),
        "South_Lancs_Cheshire": 6,
        "Lancashire_(North)": 8,
        "Cheshire": 8,
        "Merseyside": 8,
    }
    if filename.startswith("Premiership"):
        return (1, "Premiership")
    if filename.startswith("Championship"):
        return (2, "Championship")
    if filename.startswith("National_League_1"):
        return (3, "National League 1")
    if filename.startswith("National_League_2"):
        return (4, "National League 2")
    for prefix, offset in zeroth_tier_map.items():
        if filename.startswith(prefix):
            num = get_number_from_tier_name(filename, prefix)
            if (
                prefix == "Berks_Bucks_&_Oxon"
                and season <= "2018-2019"
                and "Premier" not in filename
            ):
                num += 1
            tier = offset + num
            return (tier, f"Level {tier}")
    return None


def extract_tier_women_pre_2018(filename: str, season: str) -> tuple[int, str] | None:
    if season < "2012-2013":
        return extract_tier_women_pre_2012(filename, season)
    if filename.startswith("Women's_Premiership"):
        return (101, "Premiership Women's")
    if filename.startswith("Women's_Championship"):
        if "2" in filename:
            return (103, "Championship 2")
        else:
            return (102, "Championship 1")
    num = get_number_from_tier_name(filename, "")
    if filename.startswith("Women") and num != 0:
        return (103 + num, f"National Challenge {num}")
    return None


def extract_tier_women_pre_2012(filename: str, season: str) -> tuple[int, str] | None:
    if filename.startswith("RFUW_"):
        filename = filename.replace("RFUW_", "Women's_")
    if filename.startswith("NC_"):
        filename = "Women's_" + filename
    if filename.endswith("A.json"):
        filename = filename.removesuffix("A.json") + "1.json"
    elif filename.endswith("B.json"):
        filename = filename.removesuffix("B.json") + "2.json"
    return extract_tier_women_pre_2018(filename, "2012-2013")


def get_number_from_tier_name(filename: str, prefix: str) -> int:
    other_words = filename.removesuffix(".json")[len(prefix) :].removeprefix("_").split("_")
    num_map = {
        "1": 1,
        "One": 1,
        "2": 2,
        "Two": 2,
        "3": 3,
        "Three": 3,
        "4": 4,
        "Four": 4,
        "5": 5,
        "Five": 5,
    }
    num = 0
    for part in other_words:
        if part in num_map:
            num = num_map[part]
            break
    return num


def load_teams_data(
    geocoded_teams_dir: str, season: str = "2025-2026"
) -> tuple[dict[str, list[MapTeam]], dict[str, int]]:
    """Load all teams from geocoded JSON files.

    Args:
        geocoded_teams_dir: Directory containing geocoded team JSON files
        season: Season in format 'YYYY-YYYY' for tier extraction

    Returns:
        Tuple of (teams_by_tier, tier_numbers) where:
        - teams_by_tier: Dictionary mapping tier names to lists of teams
        - tier_numbers: Dictionary mapping tier names to tier numbers
    """
    teams_by_tier: dict[str, list[MapTeam]] = {}
    tier_numbers: dict[str, int] = {}

    # Support both season subdirectories and root directory
    if os.path.isdir(geocoded_teams_dir):
        files_to_process = []
        for filename in os.listdir(geocoded_teams_dir):
            filepath = os.path.join(geocoded_teams_dir, filename)
            if filename.endswith(".json"):
                files_to_process.append((filepath, filename))

        for filepath, filename in files_to_process:

            data = json_load_cache(filepath)

            tier_num, tier_name = extract_tier(filename, season)

            if tier_name not in teams_by_tier:
                teams_by_tier[tier_name] = []
                tier_numbers[tier_name] = tier_num

            league_name = data.get("league_name", "Unknown League")
            league_url = data.get("league_url", "")

            for team in data.get("teams", []):
                if "latitude" in team and "longitude" in team:
                    team_data: MapTeam = {
                        "name": team["name"],
                        "latitude": team["latitude"],
                        "longitude": team["longitude"],
                        "address": team.get("formatted_address", team.get("address", "")),
                        "url": team.get("url", ""),
                        "image_url": team.get("image_url"),
                        "formatted_address": team.get("formatted_address"),
                        "place_id": team.get("place_id"),
                        "league": league_name,
                        "league_url": league_url,  # type: ignore
                        "tier": tier_name,
                        "tier_num": tier_num,
                        "itl0": None,
                        "itl1": None,
                        "itl2": None,
                        "itl3": None,
                        "lad": None,
                        "ward": None,
                    }
                    teams_by_tier[tier_name].append(team_data)

    return teams_by_tier, tier_numbers


# Distinct colors for leagues
def league_color(index: int) -> str:
    palette = [
        "#e6194b",
        "#3cb44b",
        "#ffe119",
        "#0082c8",
        "#f58231",
        "#911eb4",
        "#46f0f0",
        "#f032e6",
        "#d2f53c",
        "#fabebe",
        "#008080",
        "#e6beff",
        "#aa6e28",
        "#fffac8",
        "#800000",
        "#aaffc3",
        "#808000",
        "#ffd8b1",
        "#000080",
        "#808080",
        "#ff6b6b",
        "#4ecdc4",
        "#95e1d3",
        "#f38181",
        "#aa96da",
        "#fcbad3",
        "#a8d8ea",
        "#ffcfd2",
    ]
    return palette[index % len(palette)]


def create_bounded_voronoi(
    teams: list[MapTeam], boundary_geom: BaseGeometry, league_colors: dict[str, str]
) -> list[dict[str, Any]]:
    """Create Voronoi diagram bounded by rectangular box, then merge by league and clip to boundary.

    Args:
        teams: List of teams in the region
        boundary_geom: Geometry to split (any boundary level)
        league_colors: Mapping of league names to colors

    Returns:
        List of dicts with "geometry", "color", and "league" for each league"s merged cells
    """
    if len(teams) < 2:
        return []

    # Get team positions
    points = np.array([[t["latitude"], t["longitude"]] for t in teams])

    # Get bounding box of the region
    minx, miny, maxx, maxy = boundary_geom.bounds

    # Add large padding to ensure all Voronoi regions are bounded
    width = maxx - minx
    height = maxy - miny
    padding = max(width, height) * 2  # Large padding to ensure no infinite regions

    # Add corner points far outside the boundary to bound the Voronoi
    corner_points = np.array(
        [
            [miny - padding, minx - padding],
            [miny - padding, maxx + padding],
            [maxy + padding, maxx + padding],
            [maxy + padding, minx - padding],
        ]
    )

    all_points = np.vstack([points, corner_points])

    # Compute Voronoi
    vor = Voronoi(all_points)

    # Build Voronoi cells for each team (skip corner points)
    team_cells = defaultdict(list)

    for point_idx in range(len(teams)):
        region_idx = vor.point_region[point_idx]
        region_vertices = vor.regions[region_idx]

        # Skip empty regions
        if not region_vertices:
            continue

        # Skip infinite regions (shouldn"t happen with large padding, but just in case)
        if -1 in region_vertices:
            continue

        # Build polygon from vertices (swap lat/lon to lon/lat for shapely)
        vertices = [(vor.vertices[i][1], vor.vertices[i][0]) for i in region_vertices]

        if len(vertices) < 3:
            continue

        cell = Polygon(vertices)

        # Clip to actual boundary
        clipped = cell.intersection(boundary_geom)

        # Accept any non-empty geometry, even tiny slivers
        if not clipped.is_empty and hasattr(clipped, "area") and clipped.area > 0:
            league = teams[point_idx]["league"]
            team_cells[league].append(clipped)

    # Merge all cells belonging to the same league
    result = []
    for league, cells in team_cells.items():
        if cells:
            merged = unary_union(cells)
            result.append({"geom": merged, "color": league_colors[league], "league": league})

    return result


def load_itl_hierarchy() -> ITLHierarchy:
    """Load boundaries and compute hierarchy links (ITL0 -> ITL1 -> ITL2 -> ITL3 -> LAD -> Ward)."""

    # Load all ITL regions
    itl3_data = json_load_cache("boundaries/ITL_3.geojson")
    itl2_data = json_load_cache("boundaries/ITL_2.geojson")
    itl1_data = json_load_cache("boundaries/ITL_1.geojson")
    itl0_data = json_load_cache("boundaries/countries.geojson")
    lad_data = json_load_cache("boundaries/local_authority_districts.geojson")
    wards_path = "boundaries/wards.geojson"
    if os.path.exists(wards_path):
        ward_data = json_load_cache(wards_path)
    else:
        print(f"Warning: {wards_path} not found, skipping ward-level hierarchy")
        ward_data = {"features": []}

    # Parse ITL3 regions
    itl3_regions: dict[str, ITLRegionGeom] = {}
    for feat in itl3_data["features"]:
        geom = shape(feat["geometry"])
        itl3_regions[feat["properties"]["ITL325NM"]] = {
            "name": feat["properties"]["ITL325NM"],
            "code": feat["properties"].get("ITL325CD"),
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }

    # Parse ITL2 regions
    itl2_regions: dict[str, ITLRegionGeom] = {}
    for feat in itl2_data["features"]:
        geom = shape(feat["geometry"])
        itl2_regions[feat["properties"]["ITL225NM"]] = {
            "name": feat["properties"]["ITL225NM"],
            "code": feat["properties"].get("ITL225CD"),
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }

    # Parse ITL1 regions
    itl1_regions: dict[str, ITLRegionGeom] = {}
    for feat in itl1_data["features"]:
        geom = shape(feat["geometry"])
        itl1_regions[feat["properties"]["ITL125NM"]] = {
            "name": feat["properties"]["ITL125NM"],
            "code": feat["properties"].get("ITL125CD"),
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }

    # Parse ITL0 regions (countries)
    itl0_regions: dict[str, ITLRegionGeom] = {}
    for feat in itl0_data["features"]:
        geom = shape(feat["geometry"])
        itl0_regions[feat["properties"]["CTRY24NM"]] = {
            "name": feat["properties"]["CTRY24NM"],
            "code": feat["properties"].get("CTRY24CD"),
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }

    # Parse LAD regions (local authority districts), indexed by LAD25CD
    lad_regions: dict[str, ITLRegionGeom] = {}
    for feat in lad_data["features"]:
        properties = feat["properties"]
        lad_code = properties.get("LAD25CD")
        if not lad_code:
            continue
        geom = shape(feat["geometry"])
        lad_regions[lad_code] = {
            "name": properties["LAD25NM"],
            "code": lad_code,
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }

    # Parse ward regions, indexed by WD25CD
    ward_regions: dict[str, ITLRegionGeom] = {}
    for feat in ward_data["features"]:
        properties = feat["properties"]
        ward_code = properties.get("WD25CD")
        if not ward_code:
            continue
        geom = shape(feat["geometry"])
        ward_regions[ward_code] = {
            "name": properties["WD25NM"],
            "code": ward_code,
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid,
            "parent_lad": properties.get("LAD25CD"),
        }

    # Build code-based lookups for hierarchy
    # ITL codes follow pattern: TL + ITL1_digit + ITL2_digit + ITL3_digit
    # ITL1: TLX (e.g., "TLC")
    # ITL2: TLXX (e.g., "TLC1")
    # ITL3: TLXXX (e.g., "TLC11")

    itl1_by_code = {r["code"]: r["name"] for r in itl1_regions.values() if r["code"]}
    itl2_by_code = {r["code"]: r["name"] for r in itl2_regions.values() if r["code"]}

    # Build hierarchy: ITL3 -> ITL2 (extract first 4 chars from ITL3 code)
    itl3_to_itl2: dict[str, str] = {}
    for itl3 in itl3_regions.values():
        if itl3["code"] and len(itl3["code"]) >= 4:
            parent_code = itl3["code"][:4]  # TLX + digit = ITL2 code
            if parent_code in itl2_by_code:
                itl3_to_itl2[itl3["name"]] = itl2_by_code[parent_code]

    # Build hierarchy: ITL2 -> ITL1 (extract first 3 chars from ITL2 code)
    itl2_to_itl1: dict[str, str] = {}
    for itl2 in itl2_regions.values():
        if itl2["code"] and len(itl2["code"]) >= 3:
            parent_code = itl2["code"][:3]  # TLX = ITL1 code
            if parent_code in itl1_by_code:
                itl2_to_itl1[itl2["name"]] = itl1_by_code[parent_code]

    # Build reverse hierarchy: ITL1 -> ITL2s
    itl1_to_itl2s: dict[str, list[str]] = {}
    for itl2_name, itl1_name in itl2_to_itl1.items():
        if itl1_name not in itl1_to_itl2s:
            itl1_to_itl2s[itl1_name] = []
        itl1_to_itl2s[itl1_name].append(itl2_name)

    # Build reverse hierarchy: ITL0 -> ITL1s using centroid containment
    itl0_to_itl1s: dict[str, list[str]] = {}
    for itl1_name, itl1 in itl1_regions.items():
        itl1_centroid = itl1["centroid"]
        for itl0_name, itl0 in itl0_regions.items():
            if itl0["prepared"].contains(itl1_centroid):
                if itl0_name not in itl0_to_itl1s:
                    itl0_to_itl1s[itl0_name] = []
                itl0_to_itl1s[itl0_name].append(itl1_name)
                break

    # Build reverse hierarchy: ITL2 -> ITL3s
    itl2_to_itl3s: dict[str, list[str]] = {}
    for itl3_name, itl2_name in itl3_to_itl2.items():
        if itl2_name not in itl2_to_itl3s:
            itl2_to_itl3s[itl2_name] = []
        itl2_to_itl3s[itl2_name].append(itl3_name)

    # Assign LADs to ITL3 regions efficiently using hierarchy
    print("Assigning LADs to ITL regions...")
    lad_to_itl3: dict[str, str] = {}
    itl3_to_lads: dict[str, list[str]] = {}

    for lad_code, lad in lad_regions.items():
        lad_centroid = lad["centroid"]

        # Step 1: Find which ITL1 region
        found_itl1 = None
        for itl1 in itl1_regions.values():
            if itl1["prepared"].contains(lad_centroid):
                found_itl1 = itl1["name"]
                break

        if not found_itl1:
            continue

        # Step 2: Check ITL2 regions within this ITL1
        itl2_candidates = itl1_to_itl2s.get(found_itl1, [])
        found_itl2 = None
        for itl2_name in itl2_candidates:
            itl2 = itl2_regions[itl2_name]
            if itl2["prepared"].contains(lad_centroid):
                found_itl2 = itl2_name
                break

        if not found_itl2:
            continue

        # Step 3: Check ITL3 regions within this ITL2
        itl3_candidates = itl2_to_itl3s.get(found_itl2, [])
        for itl3_name in itl3_candidates:
            itl3 = itl3_regions[itl3_name]
            if itl3["prepared"].contains(lad_centroid):
                lad_to_itl3[lad_code] = itl3_name
                if itl3_name not in itl3_to_lads:
                    itl3_to_lads[itl3_name] = []
                itl3_to_lads[itl3_name].append(lad_code)
                break

    print(f"  Assigned {len(lad_to_itl3)} of {len(lad_regions)} LADs to ITL3 regions")
    print(f"  {len(itl3_to_lads)} ITL3 regions contain LADs")

    # Assign wards to LADs using parent lad
    print("Assigning wards to LADs...")
    ward_to_lad: dict[str, str] = {}
    lad_to_wards: dict[str, list[str]] = {}

    for ward_code, ward in ward_regions.items():
        parent_code = ward.get("parent_lad")
        if parent_code and parent_code in lad_regions:
            lad = lad_regions[parent_code]
            ward_to_lad[ward_code] = parent_code
            if parent_code not in lad_to_wards:
                lad_to_wards[parent_code] = []
            lad_to_wards[parent_code].append(ward_code)

    print(f"  Assigned {len(ward_to_lad)} of {len(ward_regions)} wards to LADs")
    print(f"  {len(lad_to_wards)} LADs contain wards")

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


def assign_teams_to_itl_regions(
    teams_by_tier: dict[str, list[MapTeam]], itl_hierarchy: ITLHierarchy
) -> RegionToTeams:
    """Assign each team to all supported boundary levels via hierarchical containment checks.

    Returns a dictionary with reverse mappings: {
        "itl0": {"country_name": [team1, team2, ...], ...},
        "itl1": {"region_name": [team1, team2, ...], ...},
        "itl2": {"region_name": [team1, team2, ...], ...},
        "itl3": {"region_name": [team1, team2, ...], ...},
        "lad": {"lad_code": [team1, team2, ...], ...},
        "ward": {"ward_code": [team1, team2, ...], ...}
    }
    """

    itl0_regions: dict[str, ITLRegionGeom] = itl_hierarchy["itl0_regions"]
    itl1_regions: dict[str, ITLRegionGeom] = itl_hierarchy["itl1_regions"]
    itl2_regions: dict[str, ITLRegionGeom] = itl_hierarchy["itl2_regions"]
    itl3_regions: dict[str, ITLRegionGeom] = itl_hierarchy["itl3_regions"]
    lad_regions: dict[str, ITLRegionGeom] = itl_hierarchy["lad_regions"]
    ward_regions: dict[str, ITLRegionGeom] = itl_hierarchy["ward_regions"]
    itl1_to_itl2s: dict[str, list[str]] = itl_hierarchy["itl1_to_itl2s"]
    itl2_to_itl3s: dict[str, list[str]] = itl_hierarchy["itl2_to_itl3s"]
    itl3_to_lads: dict[str, list[str]] = itl_hierarchy["itl3_to_lads"]
    lad_to_wards: dict[str, list[str]] = itl_hierarchy["lad_to_wards"]

    # Create lookup dictionaries for faster access
    itl2_by_name: dict[str, ITLRegionGeom] = itl2_regions
    itl3_by_name: dict[str, ITLRegionGeom] = itl3_regions

    # Create reverse mappings: region -> teams
    itl0_to_teams: dict[str, list[MapTeam]] = {}
    itl1_to_teams: dict[str, list[MapTeam]] = {}
    itl2_to_teams: dict[str, list[MapTeam]] = {}
    itl3_to_teams: dict[str, list[MapTeam]] = {}
    lad_to_teams: dict[str, list[MapTeam]] = {}
    ward_to_teams: dict[str, list[MapTeam]] = {}

    total_assigned: int = 0
    total_teams: int = 0

    for _, teams in teams_by_tier.items():
        for team in teams:
            total_teams += 1
            point = Point(team["longitude"], team["latitude"])

            team["itl0"] = None
            team["itl3"] = None
            team["itl2"] = None
            team["itl1"] = None
            team["lad"] = None
            team["ward"] = None

            # Step 0: Find country (ITL0)
            for itl0 in itl0_regions.values():
                if itl0["prepared"].contains(point):
                    team["itl0"] = itl0["name"]
                    if itl0["name"] not in itl0_to_teams:
                        itl0_to_teams[itl0["name"]] = []
                    itl0_to_teams[itl0["name"]].append(team)
                    break

            # Step 1: Find which ITL1 region (only 12 to check!)
            found_itl1 = None
            for itl1 in itl1_regions.values():
                if itl1["prepared"].contains(point):
                    found_itl1 = itl1["name"]
                    team["itl1"] = found_itl1
                    # Add to reverse mapping
                    if found_itl1 not in itl1_to_teams:
                        itl1_to_teams[found_itl1] = []
                    itl1_to_teams[found_itl1].append(team)
                    break

            if not found_itl1:
                continue

            # Step 2: Only check ITL2 regions within this ITL1
            itl2_candidates = itl1_to_itl2s.get(found_itl1, [])
            found_itl2 = None
            for itl2_name in itl2_candidates:
                itl2 = itl2_by_name[itl2_name]
                if itl2["prepared"].contains(point):
                    found_itl2 = itl2_name
                    team["itl2"] = found_itl2
                    # Add to reverse mapping
                    if found_itl2 not in itl2_to_teams:
                        itl2_to_teams[found_itl2] = []
                    itl2_to_teams[found_itl2].append(team)
                    break

            if not found_itl2:
                continue

            # Step 3: Only check ITL3 regions within this ITL2
            itl3_candidates = itl2_to_itl3s.get(found_itl2, [])
            found_itl3 = None
            for itl3_name in itl3_candidates:
                itl3 = itl3_by_name[itl3_name]
                if itl3["prepared"].contains(point):
                    found_itl3 = itl3_name
                    team["itl3"] = itl3_name
                    # Add to reverse mapping
                    if itl3_name not in itl3_to_teams:
                        itl3_to_teams[itl3_name] = []
                    itl3_to_teams[itl3_name].append(team)
                    total_assigned += 1
                    break

            if not found_itl3:
                continue

            # Step 4: Assign LAD within this ITL3
            lad_candidates = itl3_to_lads.get(found_itl3, [])
            found_lad = None
            for lad_code in lad_candidates:
                lad = lad_regions.get(lad_code)
                if lad and lad["prepared"].contains(point):
                    found_lad = lad_code
                    team["lad"] = lad_code
                    if lad_code not in lad_to_teams:
                        lad_to_teams[lad_code] = []
                    lad_to_teams[lad_code].append(team)
                    break

            # Step 5: Assign ward within this LAD (only when ward data exists)
            if found_lad and ward_regions:
                ward_candidates = lad_to_wards.get(found_lad, [])
                for ward_code in ward_candidates:
                    ward = ward_regions.get(ward_code)
                    if ward and ward["prepared"].contains(point):
                        team["ward"] = ward_code
                        if ward_code not in ward_to_teams:
                            ward_to_teams[ward_code] = []
                        ward_to_teams[ward_code].append(team)
                        break

    print("\nITL Region Assignment:")
    print(f"  Assigned {total_assigned} of {total_teams} teams to ITL regions")
    print(f"  ITL1: {len(itl1_to_teams)} regions have teams")
    print(f"  ITL2: {len(itl2_to_teams)} regions have teams")
    print(f"  ITL3: {len(itl3_to_teams)} regions have teams")
    print(f"  LAD: {len(lad_to_teams)} regions have teams")
    if ward_regions:
        print(f"  Wards: {len(ward_to_teams)} regions have teams")

    # Print example region -> teams mapping
    print("\nExample regions with team counts:")
    for region_name in sorted(itl1_to_teams.keys())[:3]:
        print(f"  ITL1 {region_name}: {len(itl1_to_teams[region_name])} teams")

    return {
        "itl0": itl0_to_teams,
        "itl1": itl1_to_teams,
        "itl2": itl2_to_teams,
        "itl3": itl3_to_teams,
        "lad": lad_to_teams,
        "ward": ward_to_teams,
    }


def export_shared_boundaries(output_dir: str = "tier_maps/shared") -> None:
    """Export simplified boundary data to a shared JSON file for client-side use.

    Creates a single boundaries.json file containing all country and ITL region
    geometries. This file is loaded once by the client and referenced by all maps,
    avoiding redundant geometry data in each HTML file.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = os.path.join(output_dir, "boundaries.json")
    if IS_PRODUCTION and os.path.exists(output_path):
        print(f"Shared boundary file already exists at {output_path}, skipping export.")
        return

    boundary_data = {
        "countries": {},
        "itl1": None,
        "itl2": None,
        "itl3": None,
        "lad": None,
        "wards": None,
    }

    # Export country boundaries
    countries_geojson_path = "boundaries/countries.geojson"
    if os.path.exists(countries_geojson_path):
        countries_data = json_load_cache(countries_geojson_path)
        countries_to_outline = ["England", "Isle of Man", "Jersey", "Guernsey"]
        for country_name in countries_to_outline:
            country_features = [
                feat
                for feat in countries_data["features"]
                if feat["properties"].get("CTRY24NM") == country_name
            ]
            if country_features:
                # Simplify country outlines
                simplified_features = []
                for feat in country_features:
                    geom = shape(feat["geometry"])
                    simplified_geom = geom.simplify(0.001, preserve_topology=True)
                    simplified_features.append(
                        {
                            "type": "Feature",
                            "geometry": mapping(simplified_geom),
                            "properties": feat.get("properties", {}),
                        }
                    )

                boundary_data["countries"][country_name] = {
                    "type": "FeatureCollection",
                    "features": simplified_features,
                }

    # Export ITL boundaries
    for level in ["ITL_1", "ITL_2", "ITL_3"]:
        geojson_path = f"boundaries/{level}.geojson"
        if os.path.exists(geojson_path):
            data = json_load_cache(geojson_path)
            # Simplify geometries (0.02 degrees ≈ 2km tolerance)
            simplified_features = []
            for feature in data["features"]:
                geom = shape(feature["geometry"])
                simplified_geom = geom.simplify(0.001, preserve_topology=True)
                simplified_features.append(
                    {
                        "type": "Feature",
                        "geometry": mapping(simplified_geom),
                        "properties": feature.get("properties", {}),
                    }
                )

            boundary_data[level.lower()] = {
                "type": "FeatureCollection",
                "features": simplified_features,
            }

    # Export LAD boundaries
    lad_geojson_path = "boundaries/local_authority_districts.geojson"
    if os.path.exists(lad_geojson_path):
        data = json_load_cache(lad_geojson_path)
        simplified_features = []
        for feature in data["features"]:
            geom = shape(feature["geometry"])
            simplified_geom = geom.simplify(0.001, preserve_topology=True)
            simplified_features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(simplified_geom),
                    "properties": feature.get("properties", {}),
                }
            )

        boundary_data["lad"] = {
            "type": "FeatureCollection",
            "features": simplified_features,
        }

    # Export ward boundaries
    wards_geojson_path = "boundaries/wards.geojson"
    if os.path.exists(wards_geojson_path):
        data = json_load_cache(wards_geojson_path)
        simplified_features = []
        for feature in data["features"]:
            geom = shape(feature["geometry"])
            simplified_geom = geom.simplify(0.001, preserve_topology=True)
            simplified_features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(simplified_geom),
                    "properties": feature.get("properties", {}),
                }
            )

        boundary_data["wards"] = {
            "type": "FeatureCollection",
            "features": simplified_features,
        }

    # Save to JSON file
    with open(output_path, "w") as f:
        json.dump(boundary_data, f, separators=(",", ":"))  # Compact format

    print(f"Exported shared boundary data to: {output_path}")


def get_boundary_loader_script(
    relative_path_to_shared: str = "../shared", use_inline: bool = False
) -> str:
    """Return JavaScript that loads and renders boundaries from shared file.

    Args:
        relative_path_to_shared: Relative path from the HTML file to the shared folder
        use_inline: If True, embed boundary data inline instead of using fetch (for local dev)

    Returns:
        JavaScript code that loads boundaries.json and renders them on the map
    """
    if use_inline:
        # Load the boundary data and embed it inline for local development
        boundaries_path = "tier_maps/shared/boundaries.json"
        boundary_data_json = "{}"
        if os.path.exists(boundaries_path):
            with open(boundaries_path) as f:
                boundary_data_json = f.read()

        return f"""
    <script>
    // Embedded boundary data for local development (avoids fetch/CORS issues)
    (function() {{
        // Wait for map to be initialized
        function addBoundaries() {{
            // Find the Leaflet map instance
            var mapElement = document.querySelector('.folium-map');
            if (!mapElement || !mapElement._leaflet_id) {{
                setTimeout(addBoundaries, 100);
                return;
            }}
            // Get the actual Leaflet map object from the element
            var map = window[Object.keys(window).find(key => key.startsWith('map_') && window[key] instanceof L.Map)];
            if (!map) {{
                setTimeout(addBoundaries, 100);
                return;
            }}

            const boundaryData = {boundary_data_json};

            // Add country outlines
            const countryStyle = {{
                fillColor: 'lightgray',
                color: 'black',
                weight: 2,
                fillOpacity: 0.1
            }};

            Object.entries(boundaryData.countries || {{}}).forEach(([name, data]) => {{
                L.geoJson(data, {{
                    style: countryStyle
                }}).addTo(map);
            }});

            // Add ITL borders (faint background)
            const borderStyle = {{
                fillColor: 'transparent',
                color: 'gray',
                weight: 0.5,
                fillOpacity: 0,
                opacity: 0.4
            }};

            ['itl_1', 'itl_2', 'itl_3'].forEach(level => {{
                if (boundaryData[level]) {{
                    L.geoJson(boundaryData[level], {{
                        style: borderStyle
                    }}).addTo(map);
                }}
            }});
        }}

        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', addBoundaries);
        }} else {{
            addBoundaries();
        }}
    }})();
    </script>
    """
    else:
        # Use fetch for production
        return f"""
    <script>
    // Load and render shared boundary data
    (function() {{
        function addBoundaries() {{
            // Find the Leaflet map instance
            var mapElement = document.querySelector('.folium-map');
            if (!mapElement || !mapElement._leaflet_id) {{
                setTimeout(addBoundaries, 100);
                return;
            }}
            // Get the actual Leaflet map object from the element
            var map = window[Object.keys(window).find(key => key.startsWith('map_') && window[key] instanceof L.Map)];
            if (!map) {{
                setTimeout(addBoundaries, 100);
                return;
            }}

            fetch('{relative_path_to_shared}/boundaries.json')
                .then(response => response.json())
                .then(boundaryData => {{
                    // Add country outlines
                    const countryStyle = {{
                        fillColor: 'lightgray',
                        color: 'black',
                        weight: 2,
                        fillOpacity: 0.1
                    }};

                    Object.entries(boundaryData.countries).forEach(([name, data]) => {{
                        L.geoJson(data, {{
                            style: countryStyle
                        }}).addTo(map);
                    }});

                    // Add ITL borders (faint background)
                    const borderStyle = {{
                        fillColor: 'transparent',
                        color: 'gray',
                        weight: 0.5,
                        fillOpacity: 0,
                        opacity: 0.4
                    }};

                    ['itl_1', 'itl_2', 'itl_3'].forEach(level => {{
                        if (boundaryData[level]) {{
                            L.geoJson(boundaryData[level], {{
                                style: borderStyle
                            }}).addTo(map);
                        }}
                    }});
                }})
                .catch(err => console.warn('Could not load shared boundaries:', err));
        }}

        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', addBoundaries);
        }} else {{
            addBoundaries();
        }}
    }})();
    </script>
    """


def get_debug_boundary_loader_script(
    relative_path_to_shared: str = "../shared", use_inline: bool = False
) -> str:
    """Return JavaScript that loads ITL debug boundaries from shared file.

    Args:
        relative_path_to_shared: Relative path from the HTML file to the shared folder
        use_inline: If True, embed boundary data inline instead of using fetch (for local dev)

    Returns:
        JavaScript code that loads debug boundaries and adds them to layer control
    """
    if use_inline:
        # Load the boundary data and embed it inline for local development
        boundaries_path = "tier_maps/shared/boundaries.json"
        boundary_data_json = "{}"
        if os.path.exists(boundaries_path):
            with open(boundaries_path) as f:
                boundary_data_json = f.read()

        return f"""
    <script>
    // Embedded debug boundary data for local development
    (function() {{
        function addDebugBoundaries() {{
            // Find the Leaflet map instance
            var mapElement = document.querySelector('.folium-map');
            if (!mapElement || !mapElement._leaflet_id) {{
                setTimeout(addDebugBoundaries, 100);
                return;
            }}
            // Get the actual Leaflet map object from the element
            var map = window[Object.keys(window).find(key => key.startsWith('map_') && window[key] instanceof L.Map)];
            if (!map) {{
                setTimeout(addDebugBoundaries, 100);
                return;
            }}

            const boundaryData = {boundary_data_json};

            const debugStyle = {{
                fillColor: 'transparent',
                color: 'red',
                weight: 2,
                fillOpacity: 0
            }};

            const debugLayers = {{
                'Debug: ITL1 Boundaries': boundaryData.itl_1,
                'Debug: ITL2 Boundaries': boundaryData.itl_2,
                'Debug: ITL3 Boundaries': boundaryData.itl_3,
                'Debug: LAD Boundaries': boundaryData.lad,
                'Debug: Ward Boundaries': boundaryData.wards
            }};

            // Add debug layers to existing layer control
            Object.entries(debugLayers).forEach(([name, data]) => {{
                if (data) {{
                    const layer = L.geoJson(data, {{
                        style: debugStyle
                    }});
                    // Add to overlays in layer control
                    if (window.layerControl) {{
                        window.layerControl.addOverlay(layer, name);
                    }}
                }}
            }});
        }}

        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', addDebugBoundaries);
        }} else {{
            addDebugBoundaries();
        }}
    }})();
    </script>
    """
    else:
        # Use fetch for production
        return f"""
    <script>
    // Load and render debug ITL boundaries
    (function() {{
        function addDebugBoundaries() {{
            // Find the Leaflet map instance
            var mapElement = document.querySelector('.folium-map');
            if (!mapElement || !mapElement._leaflet_id) {{
                setTimeout(addDebugBoundaries, 100);
                return;
            }}
            // Get the actual Leaflet map object from the element
            var map = window[Object.keys(window).find(key => key.startsWith('map_') && window[key] instanceof L.Map)];
            if (!map) {{
                setTimeout(addDebugBoundaries, 100);
                return;
            }}

            fetch('{relative_path_to_shared}/boundaries.json')
                .then(response => response.json())
                .then(boundaryData => {{
                    const debugStyle = {{
                        fillColor: 'transparent',
                        color: 'red',
                        weight: 2,
                        fillOpacity: 0
                    }};

                    const debugLayers = {{
                        'Debug: ITL1 Boundaries': boundaryData.itl_1,
                        'Debug: ITL2 Boundaries': boundaryData.itl_2,
                        'Debug: ITL3 Boundaries': boundaryData.itl_3,
                        'Debug: LAD Boundaries': boundaryData.lad,
                        'Debug: Ward Boundaries': boundaryData.wards
                    }};

                    // Add debug layers to existing layer control
                    Object.entries(debugLayers).forEach(([name, data]) => {{
                        if (data) {{
                            const layer = L.geoJson(data, {{
                                style: debugStyle
                            }});
                            // Add to overlays in layer control
                            if (window.layerControl) {{
                                window.layerControl.addOverlay(layer, name);
                            }}
                        }}
                    }});
                }})
                .catch(err => console.warn('Could not load debug boundaries:', err));
        }}

        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', addDebugBoundaries);
        }} else {{
            addDebugBoundaries();
        }}
    }})();
    </script>
    """


def build_base_map() -> folium.Map:
    """Create a base England-centered map with light tiles.

    Boundaries are loaded from shared/boundaries.json via client-side JavaScript
    to avoid embedding redundant geometry data in each HTML file.
    """
    m = folium.Map(location=[52.5, -1.5], zoom_start=7, tiles=None)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        control=False,
    ).add_to(m)

    # Boundaries will be loaded via JavaScript from shared/boundaries.json
    # This avoids embedding the same geometry data in every HTML file
    return m


def add_debug_boundaries(m: folium.Map, show_debug: bool) -> None:
    """Optionally add ITL boundary debug layers to the map.

    Debug boundaries are loaded from shared/boundaries.json via client-side JavaScript.
    """
    # Debug boundaries will be loaded via JavaScript if needed
    # This is handled in the boundary loader script
    pass


def collect_league_geometries_for_tier(
    teams: list[MapTeam],
    region_to_teams: RegionToTeams,
    itl_hierarchy: ITLHierarchy,
    league_colors: dict[str, str],
) -> dict[str, list[BaseGeometry]]:
    """Compute unionable geometries per league for a given tier.

    Uses hierarchical splitting from a tier-dependent start level.
    If an area is single-league owned, it is shaded at that level.
    If multi-league, recurse to children and fill empty children by nearest league in parent.
    """
    if not teams:
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

    team_ids = {id(t) for t in teams}
    filtered_region_to_teams: dict[str, dict[str, list[MapTeam]]] = {}
    for level in all_levels:
        level_map = region_to_teams.get(level, {})
        filtered_region_to_teams[level] = {
            region_key: [team for team in level_teams if id(team) in team_ids]
            for region_key, level_teams in level_map.items()
        }

    tier_num = teams[0].get("tier_num", 999)

    # Saying tier x starts at ITLY means that:
    # if an ITLY region contains any teams at all, every part of that region will be shaded
    if tier_num <= 3 or tier_num == 101:
        tier_entry_level = "itl0"
    elif tier_num <= 4 or tier_num in [102, 103]:
        tier_entry_level = "itl1"
    else:
        tier_entry_level = "itl2"

    league_geometries: dict[str, list[BaseGeometry]] = {}

    def closest_league_in_parent(parent_teams: list[MapTeam], centroid: Point) -> str | None:
        if not parent_teams:
            return None
        closest_team = min(
            parent_teams,
            key=lambda team: centroid.distance(Point(team["longitude"], team["latitude"])),
        )
        return closest_team["league"]

    def split_region(
        level: str, region_key: str, parent_teams: list[MapTeam]
    ) -> list[dict[str, Any]]:
        level_regions = regions_by_level[level]
        region = level_regions.get(region_key)
        if not region:
            return []

        teams_in_region = filtered_region_to_teams[level].get(region_key, [])
        if not teams_in_region:
            fallback_league = closest_league_in_parent(parent_teams, region["centroid"])
            if not fallback_league:
                return []
            return [
                {
                    "geom": region["geom"],
                    "league": fallback_league,
                    "color": league_colors[fallback_league],
                }
            ]

        leagues_here = {team["league"] for team in teams_in_region}
        if len(leagues_here) == 1:
            league = next(iter(leagues_here))
            return [{"geom": region["geom"], "league": league, "color": league_colors[league]}]

        child_level = next_level.get(level)
        if not child_level:
            voronoi_cells = create_bounded_voronoi(teams_in_region, region["geom"], league_colors)
            if voronoi_cells:
                return voronoi_cells
            fallback_league = closest_league_in_parent(teams_in_region, region["centroid"])
            if not fallback_league:
                return []
            return [
                {
                    "geom": region["geom"],
                    "league": fallback_league,
                    "color": league_colors[fallback_league],
                }
            ]

        child_regions = regions_by_level[child_level]
        child_keys = [
            child_key
            for child_key in child_map_by_level.get(level, {}).get(region_key, [])
            if child_key in child_regions
        ]

        if not child_keys:
            voronoi_cells = create_bounded_voronoi(teams_in_region, region["geom"], league_colors)
            if voronoi_cells:
                return voronoi_cells
            fallback_league = closest_league_in_parent(teams_in_region, region["centroid"])
            if not fallback_league:
                return []
            return [
                {
                    "geom": region["geom"],
                    "league": fallback_league,
                    "color": league_colors[fallback_league],
                }
            ]

        result_cells: list[dict[str, Any]] = []
        empty_children: list[str] = []

        for child_key in child_keys:
            teams_in_child = filtered_region_to_teams[child_level].get(child_key, [])
            if not teams_in_child:
                empty_children.append(child_key)
                continue

            child_leagues = {team["league"] for team in teams_in_child}
            if len(child_leagues) == 1:
                league = next(iter(child_leagues))
                result_cells.append(
                    {
                        "geom": child_regions[child_key]["geom"],
                        "league": league,
                        "color": league_colors[league],
                    }
                )
            else:
                result_cells.extend(split_region(child_level, child_key, teams_in_child))

        for empty_child_key in empty_children:
            empty_child = child_regions[empty_child_key]
            fallback_league = closest_league_in_parent(teams_in_region, empty_child["centroid"])
            if fallback_league:
                result_cells.append(
                    {
                        "geom": empty_child["geom"],
                        "league": fallback_league,
                        "color": league_colors[fallback_league],
                    }
                )

        return result_cells

    start_region_to_teams = filtered_region_to_teams[tier_entry_level]
    for region_key, teams_in_region in start_region_to_teams.items():
        cells = split_region(tier_entry_level, region_key, teams_in_region)
        for cell in cells:
            league = cell["league"]
            league_geometries.setdefault(league, []).append(cell["geom"])

    return league_geometries


def add_territories_from_geometries(
    group: folium.FeatureGroup,
    league_geometries: dict[str, list[BaseGeometry]],
    league_colors: dict[str, str],
) -> None:
    """Merge and add unioned geometries to the given feature group per league."""

    def strip_interiors(geom: BaseGeometry) -> BaseGeometry:
        if geom.is_empty:
            return geom
        if geom.geom_type == "Polygon":
            return Polygon(geom.exterior)
        if geom.geom_type == "MultiPolygon":
            polygons = [Polygon(part.exterior) for part in geom.geoms if not part.is_empty]
            return MultiPolygon(polygons) if polygons else geom
        return geom

    for league, geometries in league_geometries.items():
        if geometries:
            simplified_geometries = [
                geom.simplify(0.001, preserve_topology=True) for geom in geometries
            ]
            merged_geom = unary_union(simplified_geometries)
            merged_geom = strip_interiors(merged_geom)
            color = league_colors[league]

            def style_function(feature, c=color):
                return {"fillColor": c, "color": c, "weight": 1, "fillOpacity": 0.6, "opacity": 0.6}

            folium.GeoJson(mapping(merged_geom), style_function=style_function).add_to(group)


def add_marker(
    marker_group: FeatureGroupSubGroup,
    team: MapTeam,
    color: str,
    team_tier_order: int | None = None,
    travel_distances: TravelDistances | None = None,
) -> None:
    team_url = team.get("url", "")
    league_url = team.get("league_url", "")
    distance_html = ""
    if travel_distances:
        team_distance_info = travel_distances["teams"].get(team["name"], None)
        league_distance_info = travel_distances["leagues"].get(team["league"], None)
        if team_distance_info and league_distance_info:
            distance_html = f"""
        <hr style=\"margin: 5px 0;\"><p style=\"margin: 2px 0;\"><b>Travel Distances:</b></p>
        {f"<p style=\"margin: 2px 0;\">Team Average Travel Distance: {team_distance_info["avg_distance_km"]:.2f} km</p>"}
        {f"<p style=\"margin: 2px 0;\">Team Total Travel Distance: {team_distance_info["total_distance_km"]:.2f} km</p>"}
        {f"<p style=\"margin: 2px 0;\">League Average Travel Distance: {league_distance_info["avg_distance_km"]:.2f} km</p>"}
            """
    popup_html = f"""
    <div style="font-family: Arial; width: 220px;">
        <h4 style="margin: 0; color: {color};">{team["name"]}</h4>
        <hr style="margin: 5px 0;">
        <p style="margin: 2px 0;"><b>League:</b> {team["league"]}</p>
        <p style="margin: 2px 0;"><b>Address:</b> {team["address"]}</p>
        <p style="margin: 2px 0;"><b>{team["itl1"]}</b> | {team["itl2"]} | <i>{team["itl3"]}</i></p>
        {f"<p style=\"margin: 2px 0;\"><a href=\"{team_url}\" target=\"_blank\">View Team Page</a></p>" if team_url else ""}
        {f"<p style=\"margin: 2px 0;\"><a href=\"{league_url}\" target=\"_blank\">View League Page</a></p>" if league_url else ""}
        {f"<p style=\"margin: 2px 0;\"><a href=\"{"" if IS_PRODUCTION else "../"}/teams/{team_name_to_filepath(team['name'])}\" target=\"_blank\">View Info page</a></p>" if league_url else ""}
        {distance_html}
    </div>
    """

    # Create marker icon
    icon_size = 30
    if team.get("image_url"):
        icon_html = f"""
        <div style="text-align: center;">
            <img src="{team["image_url"]}"
                    style="width: {icon_size}px; height: {icon_size}px; border-radius: 50%;"
                    onerror="this.onerror=null; this.src='https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg';">
        </div>
        """
    else:
        icon_html = f"""
        <div style="text-align: center;">
            <img src="https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg"
                    style="width: {icon_size}px; height: {icon_size}px; border-radius: 50%;">
        </div>
        """

    icon = folium.DivIcon(html=icon_html, icon_size=(icon_size, icon_size), icon_anchor=(15, 15))

    # Get tier order and image URL for cluster icon selection
    team_image_url = (
        team.get("image_url") or "https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg"
    )

    # Add marker to tier subgroup with custom options for clustering
    marker = folium.Marker(
        location=[team["latitude"], team["longitude"]],
        popup=folium.Popup(popup_html, max_width=250),
        icon=icon,
        tooltip=team["name"],
    )
    # Add custom options for cluster icon selection and tooltip
    marker.options["tierOrder"] = team_tier_order
    marker.options["imageUrl"] = team_image_url
    marker.options["teamName"] = team["name"]  # Used by cluster iconCreateFunction for tooltip

    marker.add_to(marker_group)


def add_marker_cluster(m: folium.Map) -> MarkerCluster:
    # JavaScript function to create cluster icon showing the highest tier team's icon
    # Lower tierOrder = higher tier (Premiership=0, Championship=1, etc.)
    icon_create_function = """
    function(cluster) {
        var markers = cluster.getAllChildMarkers();
        var bestMarker = null;
        var bestTier = Infinity;
        var teamNames = [];

        for (var i = 0; i < markers.length; i++) {
            var m = markers[i];
            if (m.options.tierOrder !== undefined && m.options.tierOrder < bestTier) {
                bestTier = m.options.tierOrder;
                bestMarker = m;
            }
            // Collect team names for tooltip
            if (m.options.teamName) {
                teamNames.push(m.options.teamName);
            }
        }
        teamNames.sort();


        var imageUrl = bestMarker && bestMarker.options.imageUrl
            ? bestMarker.options.imageUrl
            : 'https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg';
        var count = cluster.getChildCount();

        // Build tooltip text showing all teams in the cluster (using newlines for title attr)
        var tooltipText = count + ' teams at this location';
        if (teamNames.length > 0) {
            tooltipText = teamNames.slice(0, 5).join('\\n');
        }

        return L.divIcon({
            html: '<div style="text-align: center; position: relative;" title="' + tooltipText.replace(/"/g, '&quot;') + '">' +
                  '<img src="' + imageUrl + '" style="width: 30px; height: 30px; border-radius: 50%;" ' +
                  'onerror="this.onerror=null; this.src=\\'https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg\\';">' +
                  '<span style="position: absolute; bottom: -5px; right: -5px; background: #333; color: white; ' +
                  'border-radius: 50%; width: 16px; height: 16px; font-size: 10px; line-height: 16px; text-align: center;">' +
                  count + '</span></div>',
            className: 'marker-cluster-custom',
            iconSize: L.point(30, 30),
            iconAnchor: L.point(15, 15)
        });
    }
    """

    # Create parent MarkerCluster (not in layer control) - handles clustering of co-located teams
    parent_cluster = MarkerCluster(
        control=False,  # Not shown in layer control
        options={
            "maxClusterRadius": 1,  # Only cluster markers within 1 pixel (co-located)
            "disableClusteringAtZoom": None,  # Cluster at all zoom levels
            "spiderfyOnMaxZoom": True,  # Spread out when clicked
            "spiderfyDistanceMultiplier": 2,  # More spread when spiderfied
            "showCoverageOnHover": False,  # No polygon on hover
            "zoomToBoundsOnClick": False,  # Don't zoom, just spiderfy
            "animate": False,  # Disable animations for better zoom performance
            "animateAddingMarkers": False,  # No animation when adding markers
        },
        icon_create_function=icon_create_function,
    )
    m.add_child(parent_cluster)
    return parent_cluster


def legend(
    legend_title: str,
    teams_by_tier: dict[str, list[MapTeam]],
    tier_order: list[str],
    league_colors: dict[str, str],
) -> folium.Element:
    legend_html = f"""
    <style>
    .legend-toggle {{
        cursor: pointer;
        user-select: none;
        display: inline-block;
        float: right;
        font-weight: bold;
        font-size: 18px;
    }}
    .legend-content.collapsed {{
        display: none;
    }}
    @media only screen and (max-width: 768px) {{
        .map-legend {{
            bottom: 10px !important;
            right: 10px !important;
            width: 200px !important;
            max-height: 300px !important;
            font-size: 11px !important;
            padding: 8px !important;
        }}
        .map-legend h4 {{
            font-size: 13px !important;
        }}
        .map-legend i {{
            width: 12px !important;
            height: 12px !important;
        }}
        .legend-content {{
            max-height: 250px !important;
        }}
    }}
    </style>
    <div class="map-legend" style="position: fixed;
                bottom: 50px; right: 50px; width: 300px;
                background-color: white; z-index:999; font-size:14px;
                border:2px solid grey; border-radius: 5px; padding: 10px">
    <h4 style="margin-top: 0;">{legend_title}
        <span class="legend-toggle" onclick="toggleLegend()" title="Toggle legend">−</span>
    </h4>
    <div class="legend-content" style="overflow-y: auto; max-height: 500px;">
    """

    for tier in tier_order:
        if tier not in teams_by_tier:
            continue
        teams = teams_by_tier[tier]
        legend_html += f'<p style="margin: 10px 0 5px 0;"><b>{tier}</b> ({len(teams)} teams)</p>'

        # Group teams by league for this tier
        leagues_in_tier = sorted({t["league"] for t in teams})
        for league in leagues_in_tier:
            color = league_colors[league]
            league_team_count = sum(1 for t in teams if t["league"] == league)
            legend_html += f"""
            <p style="margin: 2px 0 2px 15px;">
                <i style="background:{color}; width: 16px; height: 16px;
                   display: inline-block; border-radius: 50%; border: 1px solid black;"></i>
                {league} ({league_team_count})
            </p>
            """

    legend_html += """</div></div>
    <script>
    function toggleLegend() {
        var content = document.querySelector(".legend-content");
        var toggle = document.querySelector(".legend-toggle");
        if (content.classList.contains("collapsed")) {
            content.classList.remove("collapsed");
            toggle.textContent = "−";
        } else {
            content.classList.add("collapsed");
            toggle.textContent = "+";
        }
    }
    </script>
    """
    return folium.Element(legend_html)


def back_button_element() -> folium.Element:
    js = f"""
    <script>
    function addBackButtonToLeafletZoom() {{
        var zoom = document.querySelector('.leaflet-control-zoom');
        if (!zoom) return;
        var zoomClone = zoom.cloneNode(true);
        zoomClone.innerHTML = '';
        var backBtn = document.createElement('a');
        backBtn.className = 'leaflet-control-zoom-back leaflet-bar-part';
        backBtn.href = '{"../" if IS_PRODUCTION else "index.html"}';
        backBtn.title = 'Back';
        backBtn.setAttribute('role', 'button');
        backBtn.setAttribute('aria-label', 'Back');
        backBtn.innerHTML = '&larr;';
        zoomClone.appendChild(backBtn);
        zoom.parentNode.insertBefore(zoomClone, zoom);
    }}
    if (window.addEventListener) {{
        window.addEventListener('DOMContentLoaded', addBackButtonToLeafletZoom);
    }} else {{
        window.attachEvent('onload', addBackButtonToLeafletZoom);
    }}
    </script>
    """
    return folium.Element(js)


def service_worker_registration(relative_path_to_shared: str = "../shared") -> folium.Element:
    """Return script to register service worker for caching external images.

    Args:
        relative_path_to_shared: Relative path from the HTML file to the shared folder (not used for SW path)

    Returns:
        Script element that registers the service worker
    """
    return folium.Element("""
    <script>
    // Register service worker IMMEDIATELY for aggressive caching of external images (RFU logos)
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/service-worker.js')
            .then(function(registration) {
                console.log('✅ ServiceWorker registered with scope:', registration.scope);
                // Force waiting service worker to activate immediately
                if (registration.waiting) {
                    registration.waiting.postMessage({type: 'SKIP_WAITING'});
                }
            })
            .catch(function(err) {
                console.log('❌ ServiceWorker registration failed:', err);
            });

        // Listen for controller change (when service worker activates)
        navigator.serviceWorker.addEventListener('controllerchange', function() {
            console.log('🔄 ServiceWorker activated - images will be cached');
        });
    }
    </script>
    """)


def add_layer_control(m: folium.Map) -> None:
    folium.LayerControl().add_to(m)
    m.get_root().header.add_child(folium.Element("""
    <script>
    (function hookLayerControl() {
        if (!window.L || !L.Control || !L.Control.Layers) {
            setTimeout(hookLayerControl, 50);
            return;
        }
        if (L.Control.Layers.prototype._layerControlHooked) {
            return;
        }
        var originalAddTo = L.Control.Layers.prototype.addTo;
        L.Control.Layers.prototype._layerControlHooked = true;
        L.Control.Layers.prototype.addTo = function(map) {
            var result = originalAddTo.call(this, map);
            window.layerControl = this;
            return result;
        };
    })();
    </script>
    """))
    # Add custom CSS for LayerControl
    m.get_root().header.add_child(folium.Element("""
    <style>
    .leaflet-control-layers-list {
        overflow-y: auto !important;
    }
    @media only screen and (max-width: 768px) {
        .leaflet-control-layers-list {
            font-size: large !important;
        }
    }
    </style>
    """))


IS_PRODUCTION = False


def relative_path_to_shared() -> str:
    return "/shared" if IS_PRODUCTION else "../shared"


def create_tier_maps(
    teams_by_tier: dict[str, list[MapTeam]],
    tier_order: list[str],
    region_to_teams: RegionToTeams,
    itl_hierarchy: ITLHierarchy,
    output_dir: str = "tier_maps",
    show_debug: bool = True,
    season: str = "",
    team_travel_distances: TravelDistances | None = None,
) -> None:
    """Create individual maps for each tier, with teams separated by league."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    for tier_num, tier in enumerate(tier_order):
        if tier not in teams_by_tier:
            continue
        teams = teams_by_tier[tier]
        m = build_base_map()
        m.get_root().header.add_child(folium.Element(get_google_analytics_script()))
        m.get_root().header.add_child(folium.Element(f"<title>{season} {tier}</title>"))

        # Register service worker for caching external images (production only)
        if IS_PRODUCTION:
            m.get_root().header.add_child(
                service_worker_registration(relative_path_to_shared=relative_path_to_shared())
            )

        # Group teams by league and assign colors
        leagues = {t["league"] for t in teams}
        league_colors = {
            league: league_color(tier_num + j) for j, league in enumerate(sorted(leagues))
        }

        # Feature groups
        shading_groups: dict[str, folium.FeatureGroup] = {}
        marker_groups: dict[str, folium.FeatureGroup] = {}
        for league in sorted(leagues):
            shading_groups[league] = folium.FeatureGroup(name=f"{league} - Territory", show=True)
            marker_groups[league] = folium.FeatureGroup(name=f"{league} - Teams", show=True)
            m.add_child(shading_groups[league])
            m.add_child(marker_groups[league])

        # Territories
        league_geometries = collect_league_geometries_for_tier(
            teams, region_to_teams, itl_hierarchy, league_colors
        )
        for league, group in shading_groups.items():
            add_territories_from_geometries(
                group, {league: league_geometries.get(league, [])}, league_colors
            )

        # Debug boundaries
        add_debug_boundaries(m, show_debug)

        # Warn about co-located teams in individual tier maps (no clustering/spiderfy here)
        teams_by_coordinate: dict[tuple[float, float], list[MapTeam]] = defaultdict(list)
        for team in teams:
            coord_key = (round(team["latitude"], 7), round(team["longitude"], 7))
            teams_by_coordinate[coord_key].append(team)

        co_located_groups = [
            (coord_key, grouped_teams)
            for coord_key, grouped_teams in teams_by_coordinate.items()
            if len(grouped_teams) >= 2 and len({t["league"] for t in grouped_teams}) >= 2
        ]
        if co_located_groups:
            print(
                f"⚠ Warning: Found {len(co_located_groups)} co-located coordinate group(s) across different leagues in {tier}. "
                "Markers may overlap in individual tier maps."
            )
            for (lat, lon), grouped_teams in sorted(co_located_groups):
                team_labels = [f"{t['name']} ({t['league']})" for t in grouped_teams]
                print(
                    f"  - ({lat}, {lon}): {len(grouped_teams)} teams -> "
                    + ", ".join(sorted(team_labels))
                )

        # Markers
        for team in teams:
            add_marker(
                marker_groups[team["league"]],
                team,
                league_colors[team["league"]],
                travel_distances=team_travel_distances,
            )

        add_layer_control(m)

        # Add boundary loader scripts (loads from shared/boundaries.json)
        # Use inline data for local dev, fetch for production
        m.get_root().html.add_child(
            folium.Element(
                get_boundary_loader_script(
                    relative_path_to_shared=relative_path_to_shared(), use_inline=not IS_PRODUCTION
                )
            )
        )
        if show_debug:
            m.get_root().html.add_child(
                folium.Element(
                    get_debug_boundary_loader_script(
                        relative_path_to_shared=relative_path_to_shared(),
                        use_inline=not IS_PRODUCTION,
                    )
                )
            )

        m.get_root().html.add_child(
            legend(f"{tier} - {len(teams)} teams", {tier: teams}, [tier], league_colors)
        )

        m.get_root().html.add_child(back_button_element())

        # Save map
        tier_name = tier.replace(" ", "_")
        if IS_PRODUCTION:
            (Path(output_dir) / tier_name).mkdir(parents=True, exist_ok=True)
        output_file = os.path.join(
            output_dir, f"{tier_name}{"/index.html" if IS_PRODUCTION else ".html"}"
        )
        m.save(output_file)
        print(f"Saved {tier} map with {len(teams)} teams to: {output_file}")


def create_all_tiers_map(
    teams_by_tier: dict[str, list[MapTeam]],
    tier_order: list[str],
    region_to_teams: RegionToTeams,
    itl_hierarchy: ITLHierarchy,
    output_dir: str = "tier_maps",
    output_name: str = "All_Tiers",
    show_debug: bool = True,
    season: str = "",
    team_travel_distances: TravelDistances | None = None,
) -> None:
    """Create a single map with all tiers, where checkboxes control tiers."""

    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Create base map centered on England
    m = build_base_map()
    m.get_root().header.add_child(folium.Element(get_google_analytics_script()))
    m.get_root().header.add_child(
        folium.Element(
            f"<title>{season} All Tiers {"Men" if tier_order[0].find('Women')==-1 else "Women"}</title>"
        )
    )

    # Register service worker for caching external images (production only)
    if IS_PRODUCTION:
        m.get_root().header.add_child(
            service_worker_registration(relative_path_to_shared=relative_path_to_shared())
        )

    # Get all unique leagues across all tiers
    leagues_by_tier: dict[str, set[str]] = {
        tier: {t["league"] for t in teams} for tier, teams in teams_by_tier.items()
    }

    # Assign colors to leagues
    league_colors: dict[str, str] = {}
    for tier_num, tier in enumerate(tier_order):
        tier_leagues = leagues_by_tier.get(tier, set())
        for j, league in enumerate(sorted(tier_leagues)):
            league_colors[league] = league_color(tier_num + j)

    # Create separate feature groups for territories and markers (only first tier shown by default)
    territory_groups: dict[str, folium.FeatureGroup] = {}
    marker_groups: dict[str, FeatureGroupSubGroup] = {}
    sorted_tiers = [tier for tier in tier_order if tier in teams_by_tier]

    parent_cluster = add_marker_cluster(m)

    # Add feature groups for each tier
    for tier in sorted_tiers:
        territory_groups[tier] = folium.FeatureGroup(name=f"{tier} - Territory", show=False)
        # Use FeatureGroupSubGroup so markers obey tier visibility toggle while using parent cluster
        marker_groups[tier] = FeatureGroupSubGroup(
            parent_cluster, name=f"{tier} - Teams", show=True
        )
        m.add_child(territory_groups[tier])
        m.add_child(marker_groups[tier])

    # Add colored regions for each tier
    for tier, teams in sorted(teams_by_tier.items()):
        league_geometries_for_tier = collect_league_geometries_for_tier(
            teams, region_to_teams, itl_hierarchy, league_colors
        )
        add_territories_from_geometries(
            territory_groups[tier], league_geometries_for_tier, league_colors
        )

    # Build tier order lookup for markers (lower = higher tier)
    tier_order_map = {tier: idx for idx, tier in enumerate(tier_order)}

    # Add markers for each team - clustering handles co-located teams via spiderfy
    num_teams = 0
    for tier in reversed(sorted_tiers):
        teams = teams_by_tier[tier]
        for team in teams:
            add_marker(
                marker_groups[tier],
                team,
                league_colors[team["league"]],
                tier_order_map.get(tier, 999),
                travel_distances=team_travel_distances,
            )
            num_teams += 1

    # Add debug boundary layers for ITL regions
    add_debug_boundaries(m, show_debug)

    add_layer_control(m)

    # Add boundary loader scripts (loads from shared/boundaries.json)
    # Use inline data for local dev, fetch for production
    m.get_root().html.add_child(
        folium.Element(
            get_boundary_loader_script(
                relative_path_to_shared=relative_path_to_shared(), use_inline=not IS_PRODUCTION
            )
        )
    )
    if show_debug:
        m.get_root().html.add_child(
            folium.Element(get_debug_boundary_loader_script(use_inline=not IS_PRODUCTION))
        )

    # Add legend for tiers and leagues
    m.get_root().html.add_child(
        legend(f"All Tiers - {num_teams} teams", teams_by_tier, sorted_tiers, league_colors)
    )

    m.get_root().html.add_child(back_button_element())

    # Save map
    if IS_PRODUCTION:
        (Path(output_dir) / output_name).mkdir(parents=True, exist_ok=True)
    output_file = os.path.join(
        output_dir, f"{output_name}{"/index.html" if IS_PRODUCTION else ".html"}"
    )
    m.save(output_file)
    print(f"Saved All Tiers map with {num_teams} teams to: {output_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate rugby tier maps")
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to process (e.g., 2024-2025, 2025-2026). Default: 2025-2026",
    )
    parser.add_argument("--no-debug", action="store_true", help="Disable debug boundary layers")
    parser.add_argument(
        "--tiers",
        nargs="+",
        help="Specific tiers to generate (e.g., 'Premiership' 'Championship'). If omitted, generates all tiers.",
    )
    parser.add_argument("--mens", action="store_true", help="Generate men's tier maps (individual)")
    parser.add_argument(
        "--womens", action="store_true", help="Generate women's tier maps (individual)"
    )
    parser.add_argument("--all-tiers", action="store_true", help="Generate all-tiers combined maps")
    parser.add_argument(
        "--all-tiers-mens", action="store_true", help="Generate men's all-tiers map only"
    )
    parser.add_argument(
        "--all-tiers-womens", action="store_true", help="Generate women's all-tiers map only"
    )
    parser.add_argument(
        "--production", action="store_true", help="Change folder structure for production"
    )
    args = parser.parse_args()

    if args.production:
        global IS_PRODUCTION
        IS_PRODUCTION = True

    season = args.season
    print(f"Generating maps for season: {season}")

    show_debug = not args.no_debug

    # If no specific flags, generate everything
    if not (
        args.mens
        or args.womens
        or args.all_tiers
        or args.all_tiers_mens
        or args.all_tiers_womens
        or args.tiers
    ):
        generate_mens_individual = True
        generate_womens_individual = True
        generate_all_tiers_men = True
        generate_all_tiers_women = True
    else:
        generate_mens_individual = args.mens or args.tiers
        generate_womens_individual = args.womens or args.tiers
        generate_all_tiers_men = args.all_tiers_mens or args.all_tiers
        generate_all_tiers_women = args.all_tiers_womens or args.all_tiers

    print("Loading teams data...")
    geocoded_dir = os.path.join("geocoded_teams", season)
    teams_by_tier, tier_numbers = load_teams_data(geocoded_dir, season)

    # Sort tiers by tier number
    sorted_tier_names = sorted(teams_by_tier.keys(), key=lambda t: tier_numbers[t])

    # Separate mens and womens based on tier number (1-99 = mens, 100+ = womens)
    mens_tier_order = [t for t in sorted_tier_names if tier_numbers[t] < 100]
    womens_tier_order = [t for t in sorted_tier_names if tier_numbers[t] >= 100]

    total_teams = sum(len(teams) for teams in teams_by_tier.values())
    print(f"\nFound {total_teams} teams across {len(teams_by_tier)} tiers")

    print("\nTeams by tier:")
    for tier in sorted_tier_names:
        print(f"  {tier}: {len(teams_by_tier[tier])} teams")

    print("\nLoading ITL hierarchy...")
    itl_hierarchy = load_itl_hierarchy()
    print(f"  Loaded {len(itl_hierarchy["itl3_regions"])} ITL3 regions")
    print(f"  Loaded {len(itl_hierarchy["itl2_regions"])} ITL2 regions")
    print(f"  Loaded {len(itl_hierarchy["itl1_regions"])} ITL1 regions")

    print("\nAssigning teams to ITL regions...")
    region_to_teams = assign_teams_to_itl_regions(teams_by_tier, itl_hierarchy)

    print("\nLoading team travel distances...")
    travel_distance_path = Path("distance_cache_folder") / f"{args.season}.json"
    team_travel_distances: TravelDistances | None = None
    if travel_distance_path.exists():
        team_travel_distances = json_load_cache(travel_distance_path)
        print(f"  Loaded travel distances for {len(team_travel_distances["teams"])} teams")
    else:
        print("  No travel distance data found")

    # Separate mens and womens teams
    mens = {
        tier_name: teams
        for tier_name, teams in teams_by_tier.items()
        if tier_name in mens_tier_order
    }
    womens = {
        tier_name: teams
        for tier_name, teams in teams_by_tier.items()
        if tier_name in womens_tier_order
    }

    # Filter by specific tiers if requested
    if args.tiers:
        mens = {tier_name: teams for tier_name, teams in mens.items() if tier_name in args.tiers}
        womens = {
            tier_name: teams for tier_name, teams in womens.items() if tier_name in args.tiers
        }
        # Update tier orders to only include filtered tiers
        mens_tier_order = [t for t in mens_tier_order if t in args.tiers]
        womens_tier_order = [t for t in womens_tier_order if t in args.tiers]

    # Output directory for this season
    output_dir = os.path.join("tier_maps", season)

    # Export shared boundary data (used by all maps to avoid redundant geometry)
    print("\nExporting shared boundary data...")
    export_shared_boundaries(output_dir="tier_maps/shared")

    # Generate individual tier maps
    if generate_mens_individual and mens:
        print("\nCreating men's tier maps...")
        create_tier_maps(
            mens,
            mens_tier_order,
            region_to_teams,
            itl_hierarchy,
            output_dir=output_dir,
            show_debug=show_debug,
            season=season,
            team_travel_distances=team_travel_distances,
        )

    if generate_womens_individual and womens:
        print("\nCreating women's tier maps...")
        create_tier_maps(
            womens,
            womens_tier_order,
            region_to_teams,
            itl_hierarchy,
            output_dir=output_dir,
            show_debug=show_debug,
            season=season,
            team_travel_distances=team_travel_distances,
        )

    # Generate all-tiers maps
    if generate_all_tiers_men:
        print("\nCreating men's all tiers map...")
        full_mens = {
            tier_name: teams
            for tier_name, teams in teams_by_tier.items()
            if tier_name in mens_tier_order
        }
        create_all_tiers_map(
            full_mens,
            mens_tier_order,
            region_to_teams,
            itl_hierarchy,
            output_dir=output_dir,
            output_name="All_Tiers",
            show_debug=show_debug,
            season=season,
            team_travel_distances=team_travel_distances,
        )

    if generate_all_tiers_women:
        print("\nCreating women's all tiers map...")
        full_womens = {
            tier_name: teams
            for tier_name, teams in teams_by_tier.items()
            if tier_name in womens_tier_order
        }
        create_all_tiers_map(
            full_womens,
            womens_tier_order,
            region_to_teams,
            itl_hierarchy,
            output_dir=output_dir,
            output_name="All_Tiers_Women",
            show_debug=show_debug,
            season=season,
            team_travel_distances=team_travel_distances,
        )

    print("\n✓ All maps created successfully!")
    print(f'Check "{output_dir}" folder for maps')


if __name__ == "__main__":
    main()
