from collections import defaultdict
import os
import argparse
import folium
from folium.plugins import MarkerCluster, FeatureGroupSubGroup
from pathlib import Path
from shapely.geometry import shape, Point, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry, prep
from shapely.ops import unary_union
from typing import Dict, List, Any, Optional, Set, TypedDict
import numpy as np
from scipy.spatial import Voronoi
from collections import defaultdict

from utils import MapTeam, TravelDistances, json_load_cache, get_google_analytics_script

# Extended type definitions for mapping (adds geospatial fields to base types)

# Type definitions for ITL region data with geometry
class ITLRegionGeom(TypedDict):
    """ITL region with geospatial data"""
    name: str
    code: Optional[str]
    geom: BaseGeometry
    prepared: PreparedGeometry
    centroid: Point

class ITLHierarchy(TypedDict):
    itl3_regions: Dict[str, ITLRegionGeom]
    itl2_regions: Dict[str, ITLRegionGeom]
    itl1_regions: Dict[str, ITLRegionGeom]
    itl0_regions: Dict[str, ITLRegionGeom]
    itl3_to_itl2: Dict[str, str]
    itl2_to_itl1: Dict[str, str]
    itl1_to_itl2s: Dict[str, List[str]]
    itl2_to_itl3s: Dict[str, List[str]]

class RegionToTeams(TypedDict):
    itl1: Dict[str, List[MapTeam]]
    itl2: Dict[str, List[MapTeam]]
    itl3: Dict[str, List[MapTeam]]

class RegionColors(TypedDict):
    itl1: Dict[str, str]
    itl2: Dict[str, str]
    itl3: Dict[str, str]
    itl3_multi_league: List[str]  # List of ITL3 regions with 2+ leagues

def extract_tier(filename: str) -> str:
    """Extract tier information from filename."""
    if filename.startswith("Premiership"):
        return "Premiership"
    elif filename.startswith("Championship"):
        return "Championship"
    elif filename.startswith("National_League_1"):
        return "National League 1"
    elif filename.startswith("National_League_2"):
        return "National League 2"
    elif filename.startswith("Regional_1"):
        return "Regional 1"
    elif filename.startswith("Regional_2"):
        return "Regional 2"
    elif filename.startswith("Counties_1"):
        return "Counties 1"
    elif filename.startswith("Counties_2"):
        return "Counties 2"
    elif filename.startswith("Counties_3"):
        return "Counties 3"
    elif filename.startswith("Counties_4"):
        return "Counties 4"
    elif filename.startswith("Counties_5"):
        return "Counties 5"
    elif filename.startswith("Women's_Premiership"):
        return "Premiership Women's"
    elif filename.startswith("Women's_Championship"):
        if filename.endswith("1.json"):
            return "Championship 1"
        elif filename.endswith("2.json"):
            return "Championship 2"
    elif filename.startswith("Women's_NC_1"):
        return "National Challenge 1"
    elif filename.startswith("Women's_NC_2"):
        return "National Challenge 2"
    elif filename.startswith("Women's_NC_3"):
        return "National Challenge 3"
    elif filename.startswith("Cumbria_Conference"):
        if filename.endswith("1.json"):
            return "Counties 2"
        elif filename.endswith("2.json"):
            return "Counties 3"
    return "Unknown"

TIER_ORDER = ["Premiership", "Championship", "National League 1", "National League 2", "Regional 1", "Regional 2", "Counties 1", "Counties 2", "Counties 3", "Counties 4", "Counties 5"]
WOMENS_TIER_ORDER = ["Premiership Women's", "Championship 1", "Championship 2", "National Challenge 1", "National Challenge 2", "National Challenge 3"]

def load_teams_data(geocoded_teams_dir: str) -> Dict[str, List[MapTeam]]:
    """Load all teams from geocoded JSON files."""
    teams_by_tier: Dict[str, List[MapTeam]] = {}
    
    # Support both season subdirectories and root directory
    if os.path.isdir(geocoded_teams_dir):
        files_to_process = []
        for filename in os.listdir(geocoded_teams_dir):
            filepath = os.path.join(geocoded_teams_dir, filename)
            if filename.endswith(".json"):
                files_to_process.append((filepath, filename))
        
        for filepath, filename in files_to_process:
            
            data = json_load_cache(filepath)
            
            tier = extract_tier(filename)
            
            if tier not in teams_by_tier:
                teams_by_tier[tier] = []
            
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
                        "tier": tier,
                        "itl1": None,
                        "itl2": None,
                        "itl3": None
                    }
                    teams_by_tier[tier].append(team_data)
    
    return teams_by_tier

# Distinct colors for leagues
def league_color(index: int) -> str:
    palette = [
        "#e6194b","#3cb44b","#ffe119","#0082c8","#f58231","#911eb4","#46f0f0","#f032e6",
        "#d2f53c","#fabebe","#008080","#e6beff","#aa6e28","#fffac8","#800000","#aaffc3",
        "#808000","#ffd8b1","#000080","#808080","#ff6b6b","#4ecdc4","#95e1d3","#f38181",
        "#aa96da","#fcbad3","#a8d8ea","#ffcfd2",
    ]
    return palette[index % len(palette)]

def create_bounded_voronoi(teams: List[MapTeam], boundary_geom: BaseGeometry, league_colors: Dict[str, str]) -> List[Dict[str, Any]]:
    """Create Voronoi diagram bounded by rectangular box, then merge by league and clip to boundary.
    
    Args:
        teams: List of teams in the region
        boundary_geom: The ITL3 region geometry
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
    corner_points = np.array([
        [miny - padding, minx - padding],
        [miny - padding, maxx + padding],
        [maxy + padding, maxx + padding],
        [maxy + padding, minx - padding]
    ])
    
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
            result.append({
                "geom": merged,
                "color": league_colors[league],
                "league": league
            })
    
    return result

def load_itl_hierarchy() -> ITLHierarchy:
    """Load ITL regions and compute hierarchy (ITL3 -> ITL2 -> ITL1)."""
    
    # Load all ITL regions
    itl3_data = json_load_cache("boundaries/ITL_3.geojson")
    itl2_data = json_load_cache("boundaries/ITL_2.geojson")
    itl1_data = json_load_cache("boundaries/ITL_1.geojson")
    itl0_data = json_load_cache("boundaries/countries.geojson")
    
    # Parse ITL3 regions
    itl3_regions: Dict[str, ITLRegionGeom] = {}
    for feat in itl3_data["features"]:
        geom = shape(feat["geometry"])
        itl3_regions[feat["properties"]["ITL325NM"]] = {
            "name": feat["properties"]["ITL325NM"],
            "code": feat["properties"].get("ITL325CD"),
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid
        }
    
    # Parse ITL2 regions
    itl2_regions: Dict[str, ITLRegionGeom] = {}
    for feat in itl2_data["features"]:
        geom = shape(feat["geometry"])
        itl2_regions[feat["properties"]["ITL225NM"]] = {
            "name": feat["properties"]["ITL225NM"],
            "code": feat["properties"].get("ITL225CD"),
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid
        }
    
    # Parse ITL1 regions
    itl1_regions: Dict[str, ITLRegionGeom] = {}
    for feat in itl1_data["features"]:
        geom = shape(feat["geometry"])
        itl1_regions[feat["properties"]["ITL125NM"]] = {
            "name": feat["properties"]["ITL125NM"],
            "code": feat["properties"].get("ITL125CD"),
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid
        }
    
    # Parse ITL0 regions (countries)
    itl0_regions: Dict[str, ITLRegionGeom] = {}
    for feat in itl0_data["features"]:
        geom = shape(feat["geometry"])
        itl0_regions[feat["properties"]["CTRY24NM"]] = {
            "name": feat["properties"]["CTRY24NM"],
            "code": feat["properties"].get("CTRY24CD"),
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid
        }
    
    # Build code-based lookups for hierarchy
    # ITL codes follow pattern: TL + ITL1_digit + ITL2_digit + ITL3_digit
    # ITL1: TLX (e.g., "TLC")
    # ITL2: TLXX (e.g., "TLC1") 
    # ITL3: TLXXX (e.g., "TLC11")
    
    itl1_by_code = {r["code"]: r["name"] for r in itl1_regions.values() if r["code"]}
    itl2_by_code = {r["code"]: r["name"] for r in itl2_regions.values() if r["code"]}
    itl3_by_code = {r["code"]: r["name"] for r in itl3_regions.values() if r["code"]}
    
    # Build hierarchy: ITL3 -> ITL2 (extract first 4 chars from ITL3 code)
    itl3_to_itl2: Dict[str, str] = {}
    for itl3 in itl3_regions.values():
        if itl3["code"] and len(itl3["code"]) >= 4:
            parent_code = itl3["code"][:4]  # TLX + digit = ITL2 code
            if parent_code in itl2_by_code:
                itl3_to_itl2[itl3["name"]] = itl2_by_code[parent_code]
    
    # Build hierarchy: ITL2 -> ITL1 (extract first 3 chars from ITL2 code)
    itl2_to_itl1: Dict[str, str] = {}
    for itl2 in itl2_regions.values():
        if itl2["code"] and len(itl2["code"]) >= 3:
            parent_code = itl2["code"][:3]  # TLX = ITL1 code
            if parent_code in itl1_by_code:
                itl2_to_itl1[itl2["name"]] = itl1_by_code[parent_code]
    
    # Build reverse hierarchy: ITL1 -> ITL2s
    itl1_to_itl2s: Dict[str, List[str]] = {}
    for itl2_name, itl1_name in itl2_to_itl1.items():
        if itl1_name not in itl1_to_itl2s:
            itl1_to_itl2s[itl1_name] = []
        itl1_to_itl2s[itl1_name].append(itl2_name)
    
    # Build reverse hierarchy: ITL2 -> ITL3s
    itl2_to_itl3s: Dict[str, List[str]] = {}
    for itl3_name, itl2_name in itl3_to_itl2.items():
        if itl2_name not in itl2_to_itl3s:
            itl2_to_itl3s[itl2_name] = []
        itl2_to_itl3s[itl2_name].append(itl3_name)
    
    return {
        "itl3_regions": itl3_regions,
        "itl2_regions": itl2_regions,
        "itl1_regions": itl1_regions,
        "itl0_regions": itl0_regions,
        "itl3_to_itl2": itl3_to_itl2,
        "itl2_to_itl1": itl2_to_itl1,
        "itl1_to_itl2s": itl1_to_itl2s,
        "itl2_to_itl3s": itl2_to_itl3s
    }

def assign_teams_to_itl_regions(teams_by_tier: Dict[str, List[MapTeam]], itl_hierarchy: ITLHierarchy) -> RegionToTeams:
    """Assign each team to ITL regions using the hierarchy (ITL1 -> ITL2 -> ITL3 for efficiency).
    
    Returns a dictionary with reverse mappings: {
        "itl1": {"region_name": [team1, team2, ...], ...},
        "itl2": {"region_name": [team1, team2, ...], ...},
        "itl3": {"region_name": [team1, team2, ...], ...}
    }
    """
    
    itl1_regions: Dict[str, ITLRegionGeom] = itl_hierarchy["itl1_regions"]
    itl2_regions: Dict[str, ITLRegionGeom] = itl_hierarchy["itl2_regions"]
    itl3_regions: Dict[str, ITLRegionGeom] = itl_hierarchy["itl3_regions"]
    itl1_to_itl2s: Dict[str, List[str]] = itl_hierarchy["itl1_to_itl2s"]
    itl2_to_itl3s: Dict[str, List[str]] = itl_hierarchy["itl2_to_itl3s"]

    
    # Create lookup dictionaries for faster access
    itl2_by_name: Dict[str, ITLRegionGeom] = itl2_regions
    itl3_by_name: Dict[str, ITLRegionGeom] = itl3_regions
    
    # Create reverse mappings: region -> teams
    itl1_to_teams: Dict[str, List[MapTeam]] = {}
    itl2_to_teams: Dict[str, List[MapTeam]] = {}
    itl3_to_teams: Dict[str, List[MapTeam]] = {}
    
    total_assigned: int = 0
    total_teams: int = 0

    
    for tier, teams in teams_by_tier.items():
        for team in teams:
            total_teams += 1
            point = Point(team["longitude"], team["latitude"])
            
            team["itl3"] = None
            team["itl2"] = None
            team["itl1"] = None
            
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
            for itl3_name in itl3_candidates:
                itl3 = itl3_by_name[itl3_name]
                if itl3["prepared"].contains(point):
                    team["itl3"] = itl3_name
                    # Add to reverse mapping
                    if itl3_name not in itl3_to_teams:
                        itl3_to_teams[itl3_name] = []
                    itl3_to_teams[itl3_name].append(team)
                    total_assigned += 1
                    break
    
    print(f"\nITL Region Assignment:")
    print(f"  Assigned {total_assigned} of {total_teams} teams to ITL regions")
    print(f"  ITL1: {len(itl1_to_teams)} regions have teams")
    print(f"  ITL2: {len(itl2_to_teams)} regions have teams")
    print(f"  ITL3: {len(itl3_to_teams)} regions have teams")
    
    # Print example region -> teams mapping
    print(f"\nExample regions with team counts:")
    for region_name in sorted(itl1_to_teams.keys())[:3]:
        print(f"  ITL1 {region_name}: {len(itl1_to_teams[region_name])} teams")
    
    return {
        "itl1": itl1_to_teams,
        "itl2": itl2_to_teams,
        "itl3": itl3_to_teams
    }

class LeagueRegionColors(TypedDict):
    itl1: Dict[str, str]
    itl2: Dict[str, str]
    itl3: Dict[str, str]
    itl3_multi_league: List[str]

def color_regions_by_league(teams: List[MapTeam], region_to_teams: RegionToTeams, itl_hierarchy: ITLHierarchy) -> LeagueRegionColors:
    """Determine which regions should be colored based on league ownership.
    
    Returns a dict with level-specific league mappings: {
        "itl1": {"region_name": "league_name", ...},
        "itl2": {"region_name": "league_name", ...},
        "itl3": {"region_name": "league_name", ...},
        "itl3_multi_league": ["region1", "region2", ...]
    }
    
    Bottom-up ownership strategy:
    1. ITL3 owned by league: contains ≥1 team from that league, no teams from other leagues in tier
    2. ITL2 owned by league: owns multiple ITL3s, no teams from other leagues in tier in the ITL2
    3. ITL1 owned by league: owns multiple ITL2s, no teams from other leagues in tier in the ITL1
    
    Special cases:
    - Premiership / Championship / National League 1: All of England is shaded
    - National League 2: Loosen ITL1/ITL2 requirements - any teams from one league, none from others
    """
    itl1_to_teams = region_to_teams["itl1"]
    itl2_to_teams = region_to_teams["itl2"]
    itl3_to_teams = region_to_teams["itl3"]
    itl1_to_itl2s = itl_hierarchy["itl1_to_itl2s"]
    itl2_to_itl3s = itl_hierarchy["itl2_to_itl3s"]
    itl2_to_itl1 = itl_hierarchy["itl2_to_itl1"]
    
    # Get all leagues in this tier
    all_leagues = sorted(set(t["league"] for t in teams))
    
    # Detect tier for special handling
    tier_name = teams[0]["tier"] if teams else None
    do_national_shading = tier_name in ["Premiership", "Championship", "National League 1", "Premiership Women's"]
    do_bigger_shading = tier_name in ["National League 2", "Championship 1", "Championship 2"]
    
    # Special early return for top tiers: shade all of England
    if do_national_shading and len(all_leagues) == 1:
        return {
            "itl0": {"England": all_leagues[0]},
            "itl1": {},
            "itl2": {},
            "itl3": {},
            "itl3_multi_league": []
        }
    
    # Step 1: Determine ITL3 regions owned by each league
    itl3_ownership: Dict[str, str] = {}  # itl3_name -> league
    for itl3_name, teams_in_region in itl3_to_teams.items():
        tier_teams = [t for t in teams_in_region if t in teams]
        if len(tier_teams) > 0:
            leagues = set(t["league"] for t in tier_teams)
            if len(leagues) == 1:  # Only one league present
                league = leagues.pop()
                itl3_ownership[itl3_name] = league
    
    # Step 2: Determine ITL2 regions owned by each league
    itl2_ownership: Dict[str, str] = {}  # itl2_name -> league
    for itl2_name, teams_in_region in itl2_to_teams.items():
        # Check if this ITL2 has teams from other leagues in this tier
        tier_teams = [t for t in teams_in_region if t in teams]
        if len(tier_teams) == 0:
            continue
        
        leagues_in_itl2 = set(t["league"] for t in tier_teams)
        if len(leagues_in_itl2) > 1:
            # Multiple leagues in this ITL2, cannot be owned
            continue
        
        # For higher leagues, loosen requirements: any teams from one league is enough
        if do_bigger_shading:
            if len(leagues_in_itl2) == 1:
                league = leagues_in_itl2.pop()
                itl2_ownership[itl2_name] = league
                continue
        
        # Standard logic: Count owned ITL3s by league
        itl3s_in_itl2 = itl2_to_itl3s.get(itl2_name, [])
        league_itl3_counts: Dict[str, int] = {}
        for itl3_name in itl3s_in_itl2:
            if itl3_name in itl3_ownership:
                league = itl3_ownership[itl3_name]
                league_itl3_counts[league] = league_itl3_counts.get(league, 0) + 1
        
        # ITL2 is owned if one league owns multiple ITL3s or one league owns the only ITL3
        for league, count in league_itl3_counts.items():
            if count >= 2 or (count == 1 and len(itl3s_in_itl2) == 1):
                itl2_ownership[itl2_name] = league
                break
    
    # Step 3: Determine ITL1 regions owned by each league
    itl1_ownership: Dict[str, str] = {}  # itl1_name -> league
    
    for itl1_name, teams_in_region in itl1_to_teams.items():
        # Check if this ITL1 has teams from other leagues in this tier
        tier_teams = [t for t in teams_in_region if t in teams]
        if len(tier_teams) == 0:
            continue
        
        leagues_in_itl1 = set(t["league"] for t in tier_teams)
        if len(leagues_in_itl1) > 1:
            # Multiple leagues in this ITL1, cannot be owned
            continue
        
        # For National League 2, loosen requirements: any teams from one league is enough
        if do_bigger_shading:
            if len(leagues_in_itl1) == 1:
                league = leagues_in_itl1.pop()
                itl1_ownership[itl1_name] = league
                continue
        
        # Standard logic: Count owned ITL2s by league
        itl2s_in_itl1 = itl1_to_itl2s.get(itl1_name, [])
        league_itl2_counts: Dict[str, int] = {}
        for itl2_name in itl2s_in_itl1:
            if itl2_name in itl2_ownership:
                league = itl2_ownership[itl2_name]
                league_itl2_counts[league] = league_itl2_counts.get(league, 0) + 1
        
        # ITL1 is owned if one league owns multiple ITL2s or one league owns the only ITL2
        for league, count in league_itl2_counts.items():
            if count >= 2 or (count == 1 and len(itl2s_in_itl1) == 1):
                itl1_ownership[itl1_name] = league
                break
    
    # Return league ownership (not colors)
    itl1_leagues: Dict[str, str] = {}
    itl2_leagues: Dict[str, str] = {}
    itl3_leagues: Dict[str, str] = {}
    
    # ITL1 region leagues
    for itl1_name, league in itl1_ownership.items():
        itl1_leagues[itl1_name] = league
    
    # ITL2 region leagues (only if parent ITL1 is not owned)
    itl3_to_itl2 = itl_hierarchy["itl3_to_itl2"]
    for itl2_name, league in itl2_ownership.items():
        parent_itl1 = itl2_to_itl1.get(itl2_name)
        # Skip if parent ITL1 is owned by any league
        if parent_itl1 and parent_itl1 in itl1_ownership:
            continue
        itl2_leagues[itl2_name] = league
    
    # ITL3 region leagues (only if parent ITL2 and grandparent ITL1 are not owned)
    for itl3_name, league in itl3_ownership.items():
        parent_itl2 = itl3_to_itl2.get(itl3_name)
        # Skip if parent ITL2 is owned by any league
        if parent_itl2 and parent_itl2 in itl2_ownership:
            continue
        # Also skip if grandparent ITL1 is owned
        grandparent_itl1 = itl2_to_itl1.get(parent_itl2) if parent_itl2 else None
        if grandparent_itl1 and grandparent_itl1 in itl1_ownership:
            continue
        itl3_leagues[itl3_name] = league
    
    # Identify ITL3 regions with 2+ leagues for Voronoi treatment
    itl3_multi_league: List[str] = []
    for itl3_name, teams_in_region in itl3_to_teams.items():
        # Skip if already owned by one league
        if itl3_name in itl3_ownership:
            continue
        
        # Skip if parent is owned
        parent_itl2 = itl3_to_itl2.get(itl3_name)
        if parent_itl2 and parent_itl2 in itl2_ownership:
            continue
        grandparent_itl1 = itl2_to_itl1.get(parent_itl2) if parent_itl2 else None
        if grandparent_itl1 and grandparent_itl1 in itl1_ownership:
            continue
        
        # Check if this region has teams from multiple leagues
        tier_teams = [t for t in teams_in_region if t in teams]
        if len(tier_teams) >= 2:
            leagues = set(t["league"] for t in tier_teams)
            if len(leagues) >= 2:
                itl3_multi_league.append(itl3_name)
    
    return {
        "itl0": {},
        "itl1": itl1_leagues,
        "itl2": itl2_leagues,
        "itl3": itl3_leagues,
        "itl3_multi_league": itl3_multi_league
    }

def build_base_map() -> folium.Map:
    """Create a base England-centered map with light tiles and outline."""
    m = folium.Map(
        location=[52.5, -1.5],
        zoom_start=7,
        tiles=None
    )

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr="&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> contributors &copy; <a href=\"https://carto.com/attributions\">CARTO</a>",
        control=False
    ).add_to(m)

    countries_geojson_path = "boundaries/countries.geojson"
    if os.path.exists(countries_geojson_path):
        countries_data = json_load_cache(countries_geojson_path)
        england_features = [
            feat for feat in countries_data["features"]
            if feat["properties"].get("CTRY24NM") == "England"
        ]
        if england_features:
            england_data = {
                "type": "FeatureCollection",
                "features": england_features
            }
            folium.GeoJson(
                england_data,
                name="England",
                style_function=lambda x: {
                    "fillColor": "lightgray",
                    "color": "black",
                    "weight": 2,
                    "fillOpacity": 0.1,
                },
                control=False
            ).add_to(m)
    return m

def add_debug_boundaries(m: folium.Map, show_debug: bool) -> None:
    """Optionally add ITL boundary debug layers to the map."""
    if not show_debug:
        return
    for level, geojson_path, layer_name in [
        ("itl1", "boundaries/ITL_1.geojson", "Debug: ITL1 Boundaries"),
        ("itl2", "boundaries/ITL_2.geojson", "Debug: ITL2 Boundaries"),
        ("itl3", "boundaries/ITL_3.geojson", "Debug: ITL3 Boundaries")
    ]:
        if os.path.exists(geojson_path):
            itl_data = json_load_cache(geojson_path)
            debug_group = folium.FeatureGroup(name=layer_name, show=False)
            folium.GeoJson(
                itl_data,
                style_function=lambda x: {
                    "fillColor": "transparent",
                    "color": "red",
                    "weight": 2,
                    "fillOpacity": 0
                }
            ).add_to(debug_group)
            m.add_child(debug_group)

def collect_league_geometries_for_tier(
    teams: List[MapTeam],
    region_to_teams: RegionToTeams,
    itl_hierarchy: ITLHierarchy,
    league_colors: Dict[str, str]
) -> Dict[str, List[BaseGeometry]]:
    """Compute unionable geometries per league for a given tier.

    Includes ITL0 (country), ITL1/2/3 regions and bounded Voronoi for multi-league ITL3s.
    """
    region_colors = color_regions_by_league(teams, region_to_teams, itl_hierarchy)
    multi_league_regions = region_colors.get("itl3_multi_league", [])

    league_geometries: Dict[str, List[BaseGeometry]] = {}

    # Regular ITL regions
    for level in ["itl0", "itl1", "itl2", "itl3"]:
        level_colors = region_colors.get(level, {})
        if not level_colors:
            continue
        level_regions = itl_hierarchy[f"{level}_regions"]
        for region_name, region_geom in level_regions.items():
            if level == "itl3" and region_name in multi_league_regions:
                continue
            if region_name in level_colors:
                league = level_colors[region_name]
                league_geometries.setdefault(league, []).append(region_geom["geom"])

    # Voronoi for multi-league ITL3s
    if multi_league_regions:
        itl3_to_teams = region_to_teams["itl3"]
        itl2_to_teams = region_to_teams["itl2"]
        itl3_to_itl2 = itl_hierarchy["itl3_to_itl2"]
        for region_name in multi_league_regions:
            boundary_geom = itl_hierarchy["itl3_regions"][region_name]["geom"]
            teams_in_region = [t for t in itl3_to_teams.get(region_name, []) if t in teams]
            if len(teams_in_region) >= 2:
                parent_itl2 = itl3_to_itl2.get(region_name)
                leagues_in_itl2 = set()
                if parent_itl2:
                    itl2_teams = [t for t in itl2_to_teams.get(parent_itl2, []) if t in teams]
                    leagues_in_itl2 = {t["league"] for t in itl2_teams}
                teams_for_voronoi = [t for t in teams if t["league"] in leagues_in_itl2]
                if len(teams_for_voronoi) >= 2:
                    voronoi_cells = create_bounded_voronoi(teams_for_voronoi, boundary_geom, league_colors)
                    for cell in voronoi_cells:
                        league = cell["league"]
                        league_geometries.setdefault(league, []).append(cell["geom"])

    return league_geometries

def add_territories_from_geometries(group: folium.FeatureGroup, league_geometries: Dict[str, List[BaseGeometry]], league_colors: Dict[str, str]) -> None:
    """Merge and add unioned geometries to the given feature group per league."""
    for league, geometries in league_geometries.items():
        if geometries:
            simplified_geometries = [geom.simplify(0.001, preserve_topology=True) for geom in geometries]
            merged_geom = unary_union(simplified_geometries)
            color = league_colors[league]
            def style_function(feature, c=color):
                return {
                    "fillColor": c,
                    "color": c,
                    "weight": 1,
                    "fillOpacity": 0.6,
                    "opacity": 0.6
                }
            folium.GeoJson(mapping(merged_geom), style_function=style_function).add_to(group)

def add_marker(marker_group: FeatureGroupSubGroup, team: MapTeam, color: str, team_tier_order: Optional[int] = None, travel_distances: Optional[TravelDistances] = None) -> None:
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
        {f"<p style=\"margin: 2px 0;\"><a href=\"{team_url}\" target=\"_blank\">View Team Page</a></p>" if team_url else ""}
        {f"<p style=\"margin: 2px 0;\"><a href=\"{league_url}\" target=\"_blank\">View League Page</a></p>" if league_url else ""}
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
    team_image_url = team.get("image_url") or "https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg"
    
    # Add marker to tier subgroup with custom options for clustering
    marker = folium.Marker(
        location=[team["latitude"], team["longitude"]],
        popup=folium.Popup(popup_html, max_width=250),
        icon=icon,
        tooltip=team["name"]
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
            'maxClusterRadius': 1,              # Only cluster markers within 1 pixel (co-located)
            'disableClusteringAtZoom': None,    # Cluster at all zoom levels
            'spiderfyOnMaxZoom': True,          # Spread out when clicked
            'spiderfyDistanceMultiplier': 2,    # More spread when spiderfied
            'showCoverageOnHover': False,       # No polygon on hover
            'zoomToBoundsOnClick': False,       # Don't zoom, just spiderfy
            'animate': False,                   # Disable animations for better zoom performance
            'animateAddingMarkers': False,      # No animation when adding markers
        },
        icon_create_function=icon_create_function
    )
    m.add_child(parent_cluster)
    return parent_cluster

def legend(legend_title: str, teams_by_tier: Dict[str, List[MapTeam]], tier_order: List[str], league_colors: Dict[str, str]) -> folium.Element:
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
    }}
    </style>
    <div class="map-legend" style="position: fixed; 
                bottom: 50px; right: 50px; width: 300px; max-height: 500px; overflow-y: auto;
                background-color: white; z-index:9999; font-size:14px;
                border:2px solid grey; border-radius: 5px; padding: 10px">
    <h4 style="margin-top: 0;">{legend_title}
        <span class="legend-toggle" onclick="toggleLegend()" title="Toggle legend">−</span>
    </h4>
    <div class="legend-content">
    """
    
    for tier in tier_order:
        if tier not in teams_by_tier:
            continue
        teams = teams_by_tier[tier]
        legend_html += f"<p style=\"margin: 10px 0 5px 0;\"><b>{tier}</b> ({len(teams)} teams)</p>"
        
        # Group teams by league for this tier
        leagues_in_tier = sorted(set(t["league"] for t in teams))
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

def add_layer_control(m: folium.Map) -> None:
    folium.LayerControl().add_to(m)
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

def create_tier_maps(teams_by_tier: Dict[str, List[MapTeam]], tier_order: List[str], region_to_teams: RegionToTeams, itl_hierarchy: ITLHierarchy, output_dir: str = "tier_maps", show_debug: bool = True, season: str = "", team_travel_distances: Optional[TravelDistances] = None, ) -> None:
    """Create individual maps for each tier, with teams separated by league."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    for tier_num, tier in enumerate(tier_order):
        if tier not in teams_by_tier:
            continue
        teams = teams_by_tier[tier]
        m = build_base_map()
        m.get_root().header.add_child(folium.Element(get_google_analytics_script()))
        m.get_root().header.add_child(folium.Element(f"<title>{season} {tier}</title>"))

        # Group teams by league and assign colors
        leagues = set(t["league"] for t in teams)
        league_colors = {league: league_color(tier_num+j) for j, league in enumerate(sorted(leagues))}

        # Feature groups
        shading_groups: Dict[str, folium.FeatureGroup] = {}
        marker_groups: Dict[str, folium.FeatureGroup] = {}
        for league in sorted(leagues):
            shading_groups[league] = folium.FeatureGroup(name=f"{league} - Territory", show=True)
            marker_groups[league] = folium.FeatureGroup(name=f"{league} - Teams", show=True)
            m.add_child(shading_groups[league])
            m.add_child(marker_groups[league])

        # Territories
        league_geometries = collect_league_geometries_for_tier(teams, region_to_teams, itl_hierarchy, league_colors)
        for league, group in shading_groups.items():
            add_territories_from_geometries(group, {league: league_geometries.get(league, [])}, league_colors)

        # Debug boundaries
        add_debug_boundaries(m, show_debug)

        # Markers
        for team in teams:
            add_marker(marker_groups[team["league"]], team, league_colors[team["league"]], travel_distances=team_travel_distances)

        add_layer_control(m)

        m.get_root().html.add_child(legend(f"{tier} - Leagues", {tier: teams}, [tier], league_colors))
        
        # Save map
        tier_name = tier.replace(" ", "_")
        output_file = os.path.join(output_dir, f"{tier_name}.html")
        m.save(output_file)
        print(f"Saved {tier} map with {len(teams)} teams to: {output_file}")

def create_all_tiers_map(teams_by_tier: Dict[str, List[MapTeam]], tier_order: List[str], region_to_teams: RegionToTeams, itl_hierarchy: ITLHierarchy, output_dir: str = "tier_maps", output_name: str = "All_Tiers.html", show_debug: bool = True, season: str = "", team_travel_distances: Optional[TravelDistances] = None) -> None:
    """Create a single map with all tiers, where checkboxes control tiers."""
    
    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Create base map centered on England
    m = build_base_map()
    m.get_root().header.add_child(folium.Element(get_google_analytics_script()))
    m.get_root().header.add_child(folium.Element(f"<title>{season} All Tiers {"Men" if tier_order[0].find('Women')==-1 else "Women"}</title>"))

    # Get all unique leagues across all tiers
    leagues_by_tier: Dict[str, Set[str]] = {tier: set(t["league"] for t in teams) for tier, teams in teams_by_tier.items()}

    # Assign colors to leagues
    league_colors: Dict[str, str] = {}
    for tier_num, tier in enumerate(tier_order):
        tier_leagues = leagues_by_tier.get(tier, set())
        for j, league in enumerate(sorted(tier_leagues)):
            league_colors[league] = league_color(tier_num+j)

    # Create separate feature groups for territories and markers (only first tier shown by default)
    territory_groups: Dict[str, folium.FeatureGroup] = {}
    marker_groups: Dict[str, FeatureGroupSubGroup] = {}
    sorted_tiers = [tier for tier in tier_order if tier in teams_by_tier]

    parent_cluster = add_marker_cluster(m)

    # Add feature groups for each tier
    for idx, tier in enumerate(sorted_tiers):
        territory_groups[tier] = folium.FeatureGroup(name=f"{tier} - Territory", show=False)
        # Use FeatureGroupSubGroup so markers obey tier visibility toggle while using parent cluster
        marker_groups[tier] = FeatureGroupSubGroup(parent_cluster, name=f"{tier} - Teams", show=True)
        m.add_child(territory_groups[tier])
        m.add_child(marker_groups[tier])
    
    # Add colored regions for each tier
    for tier, teams in sorted(teams_by_tier.items()):
        league_geometries_for_tier = collect_league_geometries_for_tier(teams, region_to_teams, itl_hierarchy, league_colors)
        add_territories_from_geometries(territory_groups[tier], league_geometries_for_tier, league_colors)

    # Build tier order lookup for markers (lower = higher tier)
    tier_order_map = {tier: idx for idx, tier in enumerate(tier_order)}

    # Add markers for each team - clustering handles co-located teams via spiderfy
    num_teams = 0
    for tier in reversed(sorted_tiers):
        teams = teams_by_tier[tier]
        for team in teams:
            add_marker(marker_groups[tier], team, league_colors[team["league"]], tier_order_map.get(tier, 999), travel_distances=team_travel_distances)
            num_teams += 1
    
    # Add debug boundary layers for ITL regions
    add_debug_boundaries(m, show_debug)
    
    add_layer_control(m)
    
    # Add legend for tiers and leagues
    m.get_root().html.add_child(legend("All Tiers - Leagues", teams_by_tier, sorted_tiers, league_colors))
    
    # Save map
    output_file = os.path.join(output_dir, output_name)
    m.save(output_file)
    print(f"Saved All Tiers map with {num_teams} teams to: {output_file}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate rugby tier maps")
    parser.add_argument("--season", type=str, default="2025-2026", help="Season to process (e.g., 2024-2025, 2025-2026). Default: 2025-2026")
    parser.add_argument("--no-debug", action="store_true", help="Disable debug boundary layers")
    parser.add_argument("--tiers", nargs="+", help="Specific tiers to generate (e.g., 'Premiership' 'Championship'). If omitted, generates all tiers.")
    parser.add_argument("--mens", action="store_true", help="Generate men's tier maps (individual)")
    parser.add_argument("--womens", action="store_true", help="Generate women's tier maps (individual)")
    parser.add_argument("--all-tiers", action="store_true", help="Generate all-tiers combined maps")
    parser.add_argument("--all-tiers-mens", action="store_true", help="Generate men's all-tiers map only")
    parser.add_argument("--all-tiers-womens", action="store_true", help="Generate women's all-tiers map only")
    args = parser.parse_args()
    
    season = args.season
    print(f"Generating maps for season: {season}")
    
    show_debug = not args.no_debug
    
    # If no specific flags, generate everything
    if not (args.mens or args.womens or args.all_tiers or args.all_tiers_mens or args.all_tiers_womens):
        generate_mens_individual = True
        generate_womens_individual = True
        generate_all_tiers = True
    else:
        generate_mens_individual = args.mens
        generate_womens_individual = args.womens
        generate_all_tiers = args.all_tiers or args.all_tiers_mens or args.all_tiers_womens
    
    print("Loading teams data...")
    geocoded_dir = os.path.join("geocoded_teams", season)
    teams_by_tier = load_teams_data(geocoded_dir)
    
    total_teams = sum(len(teams) for teams in teams_by_tier.values())
    print(f"\nFound {total_teams} teams across {len(teams_by_tier)} tiers")
    
    print("\nTeams by tier:")
    for tier in TIER_ORDER + WOMENS_TIER_ORDER:
        if tier not in teams_by_tier:
            continue
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
    team_travel_distances = None
    if travel_distance_path.exists():
        team_travel_distances = json_load_cache(travel_distance_path)
        print(f"  Loaded travel distances for {len(team_travel_distances)} teams")
    else:
        print("  No travel distance data found")

    mens = {tier_name: teams for tier_name, teams in teams_by_tier.items() if tier_name in TIER_ORDER}
    womens = {tier_name: teams for tier_name, teams in teams_by_tier.items() if tier_name in WOMENS_TIER_ORDER}
    
    # Filter by specific tiers if requested
    if args.tiers:
        mens = {tier_name: teams for tier_name, teams in mens.items() if tier_name in args.tiers}
        womens = {tier_name: teams for tier_name, teams in womens.items() if tier_name in args.tiers}

    # Output directory for this season
    output_dir = os.path.join("tier_maps", season)

    # Generate individual tier maps
    if generate_mens_individual and mens:
        print("\nCreating men's tier maps...")
        create_tier_maps(mens, TIER_ORDER, region_to_teams, itl_hierarchy, output_dir=output_dir, show_debug=show_debug, season=season, team_travel_distances=team_travel_distances)
    
    if generate_womens_individual and womens:
        print("\nCreating women's tier maps...")
        create_tier_maps(womens, WOMENS_TIER_ORDER, region_to_teams, itl_hierarchy, output_dir=output_dir, show_debug=show_debug, season=season, team_travel_distances=team_travel_distances)
    
    # Generate all-tiers maps
    if generate_all_tiers:
        if args.all_tiers or args.all_tiers_mens or (not args.all_tiers_womens and mens):
            print("\nCreating men's all tiers map...")
            full_mens = {tier_name: teams for tier_name, teams in teams_by_tier.items() if tier_name in TIER_ORDER}
            create_all_tiers_map(full_mens, TIER_ORDER, region_to_teams, itl_hierarchy, output_dir=output_dir, output_name="All_Tiers.html", show_debug=show_debug, season=season, team_travel_distances=team_travel_distances)
        
        if args.all_tiers or args.all_tiers_womens or (not args.all_tiers_mens and womens):
            print("\nCreating women's all tiers map...")
            full_womens = {tier_name: teams for tier_name, teams in teams_by_tier.items() if tier_name in WOMENS_TIER_ORDER}
            create_all_tiers_map(full_womens, WOMENS_TIER_ORDER, region_to_teams, itl_hierarchy, output_dir=output_dir, output_name="All_Tiers_Women.html", show_debug=show_debug, season=season, team_travel_distances=team_travel_distances)
    
    print("\n✓ All maps created successfully!")
    print(f"Check \"{output_dir}\" folder for maps")

if __name__ == "__main__":
    main()
