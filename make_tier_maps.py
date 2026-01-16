import json
import os
import folium
from pathlib import Path
from shapely.geometry import shape, Point
from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry, prep
from typing import Dict, List, Any, Optional, TypedDict, NotRequired

from utils import ITLRegion, MapTeam

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
    itl3_regions: List[ITLRegionGeom]
    itl2_regions: List[ITLRegionGeom]
    itl1_regions: List[ITLRegionGeom]
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
    if filename.startswith('Premiership'):
        return 'Premiership'
    elif filename.startswith('Championship'):
        return 'Championship'
    elif filename.startswith('National_League_1'):
        return 'National League 1'
    elif filename.startswith('National_League_2'):
        return 'National League 2'
    elif filename.startswith('Regional_1'):
        return 'Regional 1'
    elif filename.startswith('Regional_2'):
        return 'Regional 2'
    elif filename.startswith('Counties_1'):
        return 'Counties 1'
    elif filename.startswith('Counties_2'):
        return 'Counties 2'
    elif filename.startswith('Counties_3'):
        return 'Counties 3'
    elif filename.startswith('Counties_4'):
        return 'Counties 4'
    elif filename.startswith('Counties_5'):
        return 'Counties 5'
    return 'Unknown'

TIER_ORDER = ["Premiership", "Championship", "National League 1", "National League 2", "Regional 1", "Regional 2", "Counties 1", "Counties 2", "Counties 3", "Counties 4", "Counties 5"]

def load_teams_data(geocoded_teams_dir: str) -> Dict[str, List[MapTeam]]:
    """Load all teams from geocoded JSON files."""
    teams_by_tier: Dict[str, List[MapTeam]] = {}
    
    for filename in os.listdir(geocoded_teams_dir):
        if filename.endswith('.json'):
            filepath = os.path.join(geocoded_teams_dir, filename)
            
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            tier = extract_tier(filename)
            
            if tier not in teams_by_tier:
                teams_by_tier[tier] = []
            
            league_name = data.get('league_name', 'Unknown League')
            league_url = data.get('league_url', '')
            
            for team in data.get('teams', []):
                if 'latitude' in team and 'longitude' in team:
                    team_data: MapTeam = {
                        'name': team['name'],
                        'latitude': team['latitude'],
                        'longitude': team['longitude'],
                        'address': team.get('formatted_address', team.get('address', '')),
                        'url': team.get('url', ''),
                        'image_url': team.get('image_url'),
                        'formatted_address': team.get('formatted_address'),
                        'place_id': team.get('place_id'),
                        'league': league_name,
                        'league_url': league_url,  # type: ignore
                        'tier': tier,
                        'itl1': None,
                        'itl2': None,
                        'itl3': None
                    }
                    teams_by_tier[tier].append(team_data)
    
    return teams_by_tier

# Distinct colors for leagues
def league_color(index: int) -> str:
    palette = [
        '#e6194b','#3cb44b','#ffe119','#0082c8','#f58231','#911eb4','#46f0f0','#f032e6',
        '#d2f53c','#fabebe','#008080','#e6beff','#aa6e28','#fffac8','#800000','#aaffc3',
        '#808000','#ffd8b1','#000080','#808080'
    ]
    return palette[index % len(palette)]

def create_bounded_voronoi(teams: List[MapTeam], boundary_geom: BaseGeometry, league_colors: Dict[str, str]) -> List[Dict[str, Any]]:
    """Create Voronoi diagram bounded by rectangular box, then merge by league and clip to boundary.
    
    Args:
        teams: List of teams in the region
        boundary_geom: The ITL3 region geometry
        league_colors: Mapping of league names to colors
    
    Returns:
        List of dicts with 'geometry', 'color', and 'league' for each league's merged cells
    """
    if len(teams) < 2:
        return []
    
    import numpy as np
    from scipy.spatial import Voronoi
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union
    from collections import defaultdict
    
    # Get team positions
    points = np.array([[t['latitude'], t['longitude']] for t in teams])
    
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
        
        # Skip infinite regions (shouldn't happen with large padding, but just in case)
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
        if not clipped.is_empty and hasattr(clipped, 'area') and clipped.area > 0:
            league = teams[point_idx]['league']
            team_cells[league].append(clipped)
    
    # Merge all cells belonging to the same league
    result = []
    for league, cells in team_cells.items():
        if cells:
            merged = unary_union(cells)
            result.append({
                'geometry': merged,
                'color': league_colors[league],
                'league': league
            })
    
    return result

def load_itl_hierarchy() -> ITLHierarchy:
    """Load ITL regions and compute hierarchy (ITL3 -> ITL2 -> ITL1)."""
    
    # Load all ITL regions
    with open('boundaries/ITL_3.geojson', 'r', encoding='utf-8') as f:
        itl3_data = json.load(f)
    
    with open('boundaries/ITL_2.geojson', 'r', encoding='utf-8') as f:
        itl2_data = json.load(f)
    
    with open('boundaries/ITL_1.geojson', 'r', encoding='utf-8') as f:
        itl1_data = json.load(f)
    
    # Parse ITL3 regions
    itl3_regions: List[ITLRegion] = []
    for feat in itl3_data['features']:
        geom = shape(feat['geometry'])
        itl3_regions.append({
            'name': feat['properties']['ITL325NM'],
            'code': feat['properties'].get('ITL325CD'),
            'geom': geom,
            'prepared': prep(geom),
            'centroid': geom.centroid
        })
    
    # Parse ITL2 regions
    itl2_regions: List[ITLRegion] = []
    for feat in itl2_data['features']:
        geom = shape(feat['geometry'])
        itl2_regions.append({
            'name': feat['properties']['ITL225NM'],
            'code': feat['properties'].get('ITL225CD'),
            'geom': geom,
            'prepared': prep(geom),
            'centroid': geom.centroid
        })
    
    # Parse ITL1 regions
    itl1_regions: List[ITLRegion] = []
    for feat in itl1_data['features']:
        geom = shape(feat['geometry'])
        itl1_regions.append({
            'name': feat['properties']['ITL125NM'],
            'code': feat['properties'].get('ITL125CD'),
            'geom': geom,
            'prepared': prep(geom),
            'centroid': geom.centroid
        })
    
    # Build code-based lookups for hierarchy
    # ITL codes follow pattern: TL + ITL1_digit + ITL2_digit + ITL3_digit
    # ITL1: TLX (e.g., "TLC")
    # ITL2: TLXX (e.g., "TLC1") 
    # ITL3: TLXXX (e.g., "TLC11")
    
    itl1_by_code = {r['code']: r['name'] for r in itl1_regions if r['code']}
    itl2_by_code = {r['code']: r['name'] for r in itl2_regions if r['code']}
    itl3_by_code = {r['code']: r['name'] for r in itl3_regions if r['code']}
    
    # Build hierarchy: ITL3 -> ITL2 (extract first 4 chars from ITL3 code)
    itl3_to_itl2: Dict[str, str] = {}
    for itl3 in itl3_regions:
        if itl3['code'] and len(itl3['code']) >= 4:
            parent_code = itl3['code'][:4]  # TLX + digit = ITL2 code
            if parent_code in itl2_by_code:
                itl3_to_itl2[itl3['name']] = itl2_by_code[parent_code]
    
    # Build hierarchy: ITL2 -> ITL1 (extract first 3 chars from ITL2 code)
    itl2_to_itl1: Dict[str, str] = {}
    for itl2 in itl2_regions:
        if itl2['code'] and len(itl2['code']) >= 3:
            parent_code = itl2['code'][:3]  # TLX = ITL1 code
            if parent_code in itl1_by_code:
                itl2_to_itl1[itl2['name']] = itl1_by_code[parent_code]
    
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
        'itl3_regions': itl3_regions,
        'itl2_regions': itl2_regions,
        'itl1_regions': itl1_regions,
        'itl3_to_itl2': itl3_to_itl2,
        'itl2_to_itl1': itl2_to_itl1,
        'itl1_to_itl2s': itl1_to_itl2s,
        'itl2_to_itl3s': itl2_to_itl3s
    }

def assign_teams_to_itl_regions(teams_by_tier: Dict[str, List[MapTeam]], itl_hierarchy: ITLHierarchy) -> RegionToTeams:
    """Assign each team to ITL regions using the hierarchy (ITL1 -> ITL2 -> ITL3 for efficiency).
    
    Returns a dictionary with reverse mappings: {
        'itl1': {'region_name': [team1, team2, ...], ...},
        'itl2': {'region_name': [team1, team2, ...], ...},
        'itl3': {'region_name': [team1, team2, ...], ...}
    }
    """
    
    itl1_regions: List[ITLRegion] = itl_hierarchy['itl1_regions']
    itl2_regions: List[ITLRegion] = itl_hierarchy['itl2_regions']
    itl3_regions: List[ITLRegion] = itl_hierarchy['itl3_regions']
    itl1_to_itl2s: Dict[str, List[str]] = itl_hierarchy['itl1_to_itl2s']
    itl2_to_itl3s: Dict[str, List[str]] = itl_hierarchy['itl2_to_itl3s']

    
    # Create lookup dictionaries for faster access
    itl2_by_name: Dict[str, ITLRegion] = {r['name']: r for r in itl2_regions}
    itl3_by_name: Dict[str, ITLRegion] = {r['name']: r for r in itl3_regions}
    
    # Create reverse mappings: region -> teams
    itl1_to_teams: Dict[str, List[MapTeam]] = {}
    itl2_to_teams: Dict[str, List[MapTeam]] = {}
    itl3_to_teams: Dict[str, List[MapTeam]] = {}
    
    total_assigned: int = 0
    total_teams: int = 0

    
    for tier, teams in teams_by_tier.items():
        for team in teams:
            total_teams += 1
            point = Point(team['longitude'], team['latitude'])
            
            team['itl3'] = None
            team['itl2'] = None
            team['itl1'] = None
            
            # Step 1: Find which ITL1 region (only 12 to check!)
            found_itl1 = None
            for itl1 in itl1_regions:
                if itl1['prepared'].contains(point):
                    found_itl1 = itl1['name']
                    team['itl1'] = found_itl1
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
                if itl2['prepared'].contains(point):
                    found_itl2 = itl2_name
                    team['itl2'] = found_itl2
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
                if itl3['prepared'].contains(point):
                    team['itl3'] = itl3_name
                    # Add to reverse mapping
                    if itl3_name not in itl3_to_teams:
                        itl3_to_teams[itl3_name] = []
                    itl3_to_teams[itl3_name].append(team)
                    total_assigned += 1
                    break
    
    print(f'\nITL Region Assignment:')
    print(f'  Assigned {total_assigned} of {total_teams} teams to ITL regions')
    print(f'  ITL1: {len(itl1_to_teams)} regions have teams')
    print(f'  ITL2: {len(itl2_to_teams)} regions have teams')
    print(f'  ITL3: {len(itl3_to_teams)} regions have teams')
    
    # Print example region -> teams mapping
    print(f'\nExample regions with team counts:')
    for region_name in sorted(itl1_to_teams.keys())[:3]:
        print(f'  ITL1 {region_name}: {len(itl1_to_teams[region_name])} teams')
    
    return {
        'itl1': itl1_to_teams,
        'itl2': itl2_to_teams,
        'itl3': itl3_to_teams
    }

def color_regions_by_league(teams: List[MapTeam], region_to_teams: RegionToTeams, itl_hierarchy: ITLHierarchy) -> Dict:
    """Determine which regions should be colored based on league ownership.
    
    Returns a dict with level-specific league mappings: {
        'itl1': {'region_name': 'league_name', ...},
        'itl2': {'region_name': 'league_name', ...},
        'itl3': {'region_name': 'league_name', ...},
        'itl3_multi_league': ['region1', 'region2', ...]
    }
    
    Bottom-up ownership strategy:
    1. ITL3 owned by league: contains ≥1 team from that league, no teams from other leagues in tier
    2. ITL2 owned by league: owns multiple ITL3s, no teams from other leagues in tier in the ITL2
    3. ITL1 owned by league: owns multiple ITL2s, no teams from other leagues in tier in the ITL1
    
    Special cases:
    - National League 1: All of England is shaded
    - National League 2: Loosen ITL1/ITL2 requirements - any teams from one league, none from others
    """
    itl1_to_teams = region_to_teams['itl1']
    itl2_to_teams = region_to_teams['itl2']
    itl3_to_teams = region_to_teams['itl3']
    itl1_to_itl2s = itl_hierarchy['itl1_to_itl2s']
    itl2_to_itl3s = itl_hierarchy['itl2_to_itl3s']
    itl2_to_itl1 = itl_hierarchy['itl2_to_itl1']
    
    # Get all leagues in this tier
    all_leagues = sorted(set(t['league'] for t in teams))
    
    # Detect tier for special handling
    tier_name = teams[0]['tier'] if teams else None
    is_national_league_1 = tier_name == 'National League 1'
    is_national_league_2 = tier_name == 'National League 2'
    
    # Special early return for National League 1: shade all of England
    if is_national_league_1 and len(all_leagues) == 1:
        return {
            'itl0': {'England': all_leagues[0]},
            'itl1': {},
            'itl2': {},
            'itl3': {},
            'itl3_multi_league': []
        }
    
    # Step 1: Determine ITL3 regions owned by each league
    itl3_ownership: Dict[str, str] = {}  # itl3_name -> league
    for itl3_name, teams_in_region in itl3_to_teams.items():
        tier_teams = [t for t in teams_in_region if t in teams]
        if len(tier_teams) > 0:
            leagues = set(t['league'] for t in tier_teams)
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
        
        leagues_in_itl2 = set(t['league'] for t in tier_teams)
        if len(leagues_in_itl2) > 1:
            # Multiple leagues in this ITL2, cannot be owned
            continue
        
        # For National League 2, loosen requirements: any teams from one league is enough
        if is_national_league_2:
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
    
    # Special case: National League 1 owns all of England
    if is_national_league_1 and len(all_leagues) == 1:
        league = all_leagues[0]
        for itl1_name in itl1_to_teams.keys():
            itl1_ownership[itl1_name] = league
    else:
        for itl1_name, teams_in_region in itl1_to_teams.items():
            # Check if this ITL1 has teams from other leagues in this tier
            tier_teams = [t for t in teams_in_region if t in teams]
            if len(tier_teams) == 0:
                continue
            
            leagues_in_itl1 = set(t['league'] for t in tier_teams)
            if len(leagues_in_itl1) > 1:
                # Multiple leagues in this ITL1, cannot be owned
                continue
            
            # For National League 2, loosen requirements: any teams from one league is enough
            if is_national_league_2:
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
    itl3_to_itl2 = itl_hierarchy['itl3_to_itl2']
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
            leagues = set(t['league'] for t in tier_teams)
            if len(leagues) >= 2:
                itl3_multi_league.append(itl3_name)
    
    return {
        'itl0': {},
        'itl1': itl1_leagues,
        'itl2': itl2_leagues,
        'itl3': itl3_leagues,
        'itl3_multi_league': itl3_multi_league
    }

def create_tier_maps(teams_by_tier: Dict[str, List[MapTeam]], region_to_teams: RegionToTeams, itl_hierarchy: ITLHierarchy, output_dir: str = 'tier_maps') -> None:
    """Create individual maps for each tier, with teams separated by league."""
    
    # Create output directory
    Path(output_dir).mkdir(exist_ok=True)
    
    for tier, teams in sorted(teams_by_tier.items(), key=lambda x: TIER_ORDER.index(x[0]) if x[0] in TIER_ORDER else len(TIER_ORDER)):
        # Create base map centered on England
        m = folium.Map(
            location=[52.5, -1.5],
            zoom_start=7,
            tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
            attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
        )
        
        # Add England outline
        countries_geojson_path = 'boundaries/countries.geojson'
        if os.path.exists(countries_geojson_path):
            with open(countries_geojson_path, 'r', encoding='utf-8') as f:
                countries_data = json.load(f)
            england_features = [
                feat for feat in countries_data['features']
                if feat['properties'].get('CTRY23NM') == 'England'
            ]
            if england_features:
                england_data = {
                    'type': 'FeatureCollection',
                    'features': england_features
                }
                folium.GeoJson(
                    england_data,
                    name='England',
                    style_function=lambda x: {
                        'fillColor': 'lightgray',
                        'color': 'black',
                        'weight': 2,
                        'fillOpacity': 0.1,
                    },
                    control=False
                ).add_to(m)
        
        # Determine which regions to color based on league homogeneity
        region_colors = color_regions_by_league(teams, region_to_teams, itl_hierarchy)
        
        # Group teams by league
        teams_by_league = {}
        for team in teams:
            league = team['league']
            if league not in teams_by_league:
                teams_by_league[league] = []
            teams_by_league[league].append(team)
        
        # Assign colors to leagues
        league_colors = {}
        for i, league in enumerate(sorted(teams_by_league.keys())):
            league_colors[league] = league_color(i)
        
        # Create feature groups for each league (will contain both regions and markers)
        league_groups = {}
        for league in sorted(teams_by_league.keys()):
            league_groups[league] = folium.FeatureGroup(name=league, show=True)
            m.add_child(league_groups[league])
        
        # Add colored ITL regions to their respective league groups
        multi_league_regions = region_colors.get('itl3_multi_league', [])
        
        # Handle ITL0 (country level) - special case for National League 1
        itl0_colors = region_colors.get('itl0', {})
        if itl0_colors and os.path.exists('boundaries/countries.geojson'):
            with open('boundaries/countries.geojson', 'r', encoding='utf-8') as f:
                countries_data = json.load(f)
            
            for feat in countries_data['features']:
                country_name = feat['properties'].get('CTRY23NM')
                if country_name in itl0_colors:
                    league = itl0_colors[country_name]
                    color = league_colors[league]
                    
                    def style_function(feature, c=color):
                        return {
                            'fillColor': c,
                            'color': c,
                            'weight': 1,
                            'fillOpacity': 0.6
                        }
                    
                    folium.GeoJson(
                        feat,
                        style_function=style_function
                    ).add_to(league_groups[league])
        
        # Collect all geometries for each league (regular regions + Voronoi cells)
        from shapely.ops import unary_union
        from shapely.geometry import mapping
        league_geometries = {}
        
        for level, geojson_path in [('itl1', 'boundaries/ITL_1.geojson'),
                                      ('itl2', 'boundaries/ITL_2.geojson'),
                                      ('itl3', 'boundaries/ITL_3.geojson')]:
            level_colors = region_colors.get(level, {})
            if not level_colors:
                continue
                
            if os.path.exists(geojson_path):
                with open(geojson_path, 'r', encoding='utf-8') as f:
                    itl_data = json.load(f)
                
                property_name = f'ITL{level[3]}25NM'
                
                # Collect geometries by league
                for feat in itl_data['features']:
                    region_name = feat['properties'].get(property_name)
                    
                    # Skip ITL3 regions that will use Voronoi
                    if level == 'itl3' and region_name in multi_league_regions:
                        continue
                    
                    if region_name in level_colors:
                        league = level_colors[region_name]
                        if league not in league_geometries:
                            league_geometries[league] = []
                        league_geometries[league].append(shape(feat['geometry']))
        
        # Handle multi-league ITL3 regions with bounded Voronoi
        if multi_league_regions and os.path.exists('boundaries/ITL_3.geojson'):
            with open('boundaries/ITL_3.geojson', 'r', encoding='utf-8') as f:
                itl3_data = json.load(f)
            
            itl3_to_teams = region_to_teams['itl3']
            itl2_to_teams = region_to_teams['itl2']
            itl3_to_itl2 = itl_hierarchy['itl3_to_itl2']
            
            for feat in itl3_data['features']:
                region_name = feat['properties'].get('ITL325NM')
                if region_name in multi_league_regions:
                    boundary_geom = shape(feat['geometry'])
                    teams_in_region = [t for t in itl3_to_teams.get(region_name, []) if t in teams]
                    
                    if len(teams_in_region) >= 2:
                        # Find parent ITL2 region
                        parent_itl2 = itl3_to_itl2.get(region_name)
                        
                        # Get leagues that have presence in the parent ITL2
                        leagues_in_itl2 = set()
                        if parent_itl2:
                            itl2_teams = [t for t in itl2_to_teams.get(parent_itl2, []) if t in teams]
                            leagues_in_itl2 = {t['league'] for t in itl2_teams}
                        
                        # Use all teams from leagues that have presence in the ITL2
                        teams_for_voronoi = [t for t in teams if t['league'] in leagues_in_itl2]
                        
                        if len(teams_for_voronoi) >= 2:
                            voronoi_cells = create_bounded_voronoi(
                                teams_for_voronoi,
                                boundary_geom,
                                league_colors
                            )
                            
                            # Add Voronoi cells to league geometries
                            for cell in voronoi_cells:
                                league = cell['league']
                                if league not in league_geometries:
                                    league_geometries[league] = []
                                league_geometries[league].append(cell['geometry'])
        
        # Merge and add all geometries for each league
        for league, geometries in league_geometries.items():
            if geometries:
                merged_geom = unary_union(geometries)
                color = league_colors[league]
                
                def style_function(feature, color=color):
                    return {
                        'fillColor': color,
                        'color': color,
                        'weight': 1,
                        'fillOpacity': 0.6,
                        'opacity': 0.6
                    }
                
                folium.GeoJson(
                    mapping(merged_geom),
                    style_function=style_function
                ).add_to(league_groups[league])
        
        # Add debug boundary layers for ITL regions
        for level, geojson_path, layer_name in [
            ('itl1', 'boundaries/ITL_1.geojson', 'Debug: ITL1 Boundaries'),
            ('itl2', 'boundaries/ITL_2.geojson', 'Debug: ITL2 Boundaries'),
            ('itl3', 'boundaries/ITL_3.geojson', 'Debug: ITL3 Boundaries')
        ]:
            if os.path.exists(geojson_path):
                with open(geojson_path, 'r', encoding='utf-8') as f:
                    itl_data = json.load(f)
                
                debug_group = folium.FeatureGroup(name=layer_name, show=False)
                
                folium.GeoJson(
                    itl_data,
                    style_function=lambda x: {
                        'fillColor': 'transparent',
                        'color': 'red',
                        'weight': 2,
                        'fillOpacity': 0
                    }
                ).add_to(debug_group)
                
                m.add_child(debug_group)
        
        # Add markers for each team to their league's feature group
        for league, league_teams in teams_by_league.items():
            color = league_colors[league]
            
            for team in league_teams:
                team_url = team.get('url', '')
                league_url = team.get('league_url', '')
                popup_html = f"""
                <div style="font-family: Arial; width: 200px;">
                    <h4 style="margin: 0; color: {color};">{team['name']}</h4>
                    <hr style="margin: 5px 0;">
                    <p style="margin: 2px 0;"><b>League:</b> {team['league']}</p>
                    <p style="margin: 2px 0;"><b>Tier:</b> {tier}</p>
                    <p style="margin: 2px 0;"><b>Address:</b> {team['address']}</p>
                    {f'<p style="margin: 2px 0;"><a href="{team_url}" target="_blank">View Team Page</a></p>' if team_url else ''}
                    {f'<p style="margin: 2px 0;"><a href="{league_url}" target="_blank">View League Page</a></p>' if league_url else ''}
                </div>
                """
                
                # Create marker with image and fallback
                if team.get('image_url'):
                    # Use DivIcon with img tag that has onerror fallback to default RFU logo
                    icon_html = f'''
                    <div style="text-align: center;">
                        <img src="{team['image_url']}" 
                             style="width: 30px; height: 30px; border-radius: 50%; border: 2px solid {color};"
                             onerror="this.onerror=null; this.src='https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg';">
                    </div>
                    '''
                    icon = folium.DivIcon(html=icon_html, icon_size=(30, 30), icon_anchor=(15, 15))
                    folium.Marker(
                        location=[team['latitude'], team['longitude']],
                        popup=folium.Popup(popup_html, max_width=250),
                        icon=icon,
                        tooltip=team['name']
                    ).add_to(league_groups[league])
                else:
                    # No image URL - use fallback logo
                    icon_html = f'''
                    <div style="text-align: center;">
                        <img src="https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg" 
                             style="width: 30px; height: 30px; border-radius: 50%; border: 2px solid {color};">
                    </div>
                    '''
                    icon = folium.DivIcon(html=icon_html, icon_size=(30, 30), icon_anchor=(15, 15))
                    folium.Marker(
                        location=[team['latitude'], team['longitude']],
                        popup=folium.Popup(popup_html, max_width=250),
                        icon=icon,
                        tooltip=team['name']
                    ).add_to(league_groups[league])
        
        # Add layer control
        folium.LayerControl(collapsed=False).add_to(m)
        
        # Add legend for leagues
        legend_html = f'''
        <div style="position: fixed; 
                    bottom: 50px; right: 50px; width: 250px; max-height: 400px; overflow-y: auto;
                    background-color: white; z-index:9999; font-size:14px;
                    border:2px solid grey; border-radius: 5px; padding: 10px">
        <h4 style="margin-top: 0;">{tier} - Leagues</h4>
        '''
        
        for league in sorted(teams_by_league.keys()):
            color = league_colors[league]
            count = len(teams_by_league[league])
            legend_html += f'''
            <p style="margin: 5px 0;">
                <i style="background:{color}; width: 20px; height: 20px; 
                   display: inline-block; border-radius: 50%; border: 1px solid black;"></i>
                {league} ({count})
            </p>
            '''
        
        legend_html += '</div>'
        m.get_root().html.add_child(folium.Element(legend_html))
        
        # Save map
        tier_name = tier.replace(' ', '_')
        output_file = os.path.join(output_dir, f'{tier_name}.html')
        m.save(output_file)
        print(f'Saved {tier} map with {len(teams)} teams to: {output_file}')

def create_all_tiers_map(teams_by_tier: Dict[str, List[MapTeam]], region_to_teams: RegionToTeams, itl_hierarchy: ITLHierarchy, output_dir: str = 'tier_maps') -> None:
    """Create a single map with all tiers, where checkboxes control tiers."""
    
    # Create output directory
    Path(output_dir).mkdir(exist_ok=True)
    
    # Create base map centered on England
    m = folium.Map(
        location=[52.5, -1.5],
        zoom_start=7,
        tiles='https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
        attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
    )
    
    # Add England outline
    countries_geojson_path = 'boundaries/countries.geojson'
    if os.path.exists(countries_geojson_path):
        with open(countries_geojson_path, 'r', encoding='utf-8') as f:
            countries_data = json.load(f)
        england_features = [
            feat for feat in countries_data['features']
            if feat['properties'].get('CTRY23NM') == 'England'
        ]
        if england_features:
            england_data = {
                'type': 'FeatureCollection',
                'features': england_features
            }
            folium.GeoJson(
                england_data,
                name='England',
                style_function=lambda x: {
                    'fillColor': 'lightgray',
                    'color': 'black',
                    'weight': 2,
                    'fillOpacity': 0.1,
                },
                control=False
            ).add_to(m)
    
    # Get all unique leagues across all tiers
    all_leagues = set()
    for teams in teams_by_tier.values():
        for team in teams:
            all_leagues.add(team['league'])
    
    # Assign colors to leagues
    league_colors = {}
    for i, league in enumerate(sorted(all_leagues)):
        league_colors[league] = league_color(i)
    
    # Create feature groups for each tier (only first tier shown by default)
    tier_groups = {}
    sorted_tiers = [tier for tier in TIER_ORDER if tier in teams_by_tier]
    for idx, tier in enumerate(sorted_tiers):
        # Show only the Counties 1 tier by default
        tier_groups[tier] = folium.FeatureGroup(name=tier, show=(tier == "Counties 1"))
        m.add_child(tier_groups[tier])
    
    # Add colored regions for each tier
    for tier, teams in sorted(teams_by_tier.items()):
        
        # Get region colors for this tier
        region_colors = color_regions_by_league(teams, region_to_teams, itl_hierarchy)
        multi_league_regions = region_colors.get('itl3_multi_league', [])
        
        # Collect all geometries for each league (regular regions + Voronoi cells)
        from shapely.ops import unary_union
        from shapely.geometry import mapping
        league_geometries_for_tier = {}
        
        # Handle ITL0 (country level) - special case for National League 1
        itl0_colors = region_colors.get('itl0', {})
        if itl0_colors and os.path.exists('boundaries/countries.geojson'):
            with open('boundaries/countries.geojson', 'r', encoding='utf-8') as f:
                countries_data = json.load(f)
            
            for feat in countries_data['features']:
                country_name = feat['properties'].get('CTRY23NM')
                if country_name in itl0_colors:
                    league = itl0_colors[country_name]
                    if league not in league_geometries_for_tier:
                        league_geometries_for_tier[league] = []
                    league_geometries_for_tier[league].append(shape(feat['geometry']))
        
        # Collect regular ITL regions
        for level, geojson_path in [('itl1', 'boundaries/ITL_1.geojson'),
                                      ('itl2', 'boundaries/ITL_2.geojson'),
                                      ('itl3', 'boundaries/ITL_3.geojson')]:
            level_colors = region_colors.get(level, {})
            if not level_colors:
                continue
                
            if os.path.exists(geojson_path):
                with open(geojson_path, 'r', encoding='utf-8') as f:
                    itl_data = json.load(f)
                
                property_name = f'ITL{level[3]}25NM'
                
                for feat in itl_data['features']:
                    region_name = feat['properties'].get(property_name)
                    
                    # Skip ITL3 regions that will use Voronoi
                    if level == 'itl3' and region_name in multi_league_regions:
                        continue
                    
                    if region_name in level_colors:
                        league = level_colors[region_name]
                        if league not in league_geometries_for_tier:
                            league_geometries_for_tier[league] = []
                        league_geometries_for_tier[league].append(shape(feat['geometry']))
        
        # Handle multi-league ITL3 regions with bounded Voronoi
        if multi_league_regions and os.path.exists('boundaries/ITL_3.geojson'):
            with open('boundaries/ITL_3.geojson', 'r', encoding='utf-8') as f:
                itl3_data = json.load(f)
            
            itl3_to_teams = region_to_teams['itl3']
            itl2_to_teams = region_to_teams['itl2']
            itl3_to_itl2 = itl_hierarchy['itl3_to_itl2']
            
            for feat in itl3_data['features']:
                region_name = feat['properties'].get('ITL325NM')
                if region_name in multi_league_regions:
                    boundary_geom = shape(feat['geometry'])
                    teams_in_region = [t for t in itl3_to_teams.get(region_name, []) if t in teams]
                    
                    if len(teams_in_region) >= 2:
                        # Find parent ITL2 region
                        parent_itl2 = itl3_to_itl2.get(region_name)
                        
                        # Get leagues that have presence in the parent ITL2
                        leagues_in_itl2 = set()
                        if parent_itl2:
                            itl2_teams = [t for t in itl2_to_teams.get(parent_itl2, []) if t in teams]
                            leagues_in_itl2 = {t['league'] for t in itl2_teams}
                        
                        # Use all teams from leagues that have presence in the ITL2
                        teams_for_voronoi = [t for t in teams if t['league'] in leagues_in_itl2]
                        
                        if len(teams_for_voronoi) >= 2:
                            voronoi_cells = create_bounded_voronoi(
                                teams_for_voronoi,
                                boundary_geom,
                                league_colors
                            )
                            
                            # Add Voronoi cells to league geometries
                            for cell in voronoi_cells:
                                league = cell['league']
                                if league not in league_geometries_for_tier:
                                    league_geometries_for_tier[league] = []
                                league_geometries_for_tier[league].append(cell['geometry'])
        
        # Merge and add all geometries for each league
        for league, geometries in league_geometries_for_tier.items():
            if geometries:
                merged_geom = unary_union(geometries)
                color = league_colors[league]
                
                def style_function(feature, c=color):
                    return {
                        'fillColor': c,
                        'color': c,
                        'weight': 1,
                        'fillOpacity': 0.6,
                        'opacity': 0.6
                    }
                
                folium.GeoJson(
                    mapping(merged_geom),
                    style_function=style_function
                ).add_to(tier_groups[tier])
    
    # Add markers for each team to their tier's feature group
    all_teams = []
    for tier, teams in teams_by_tier.items():
        all_teams.extend(teams)
        
        for team in teams:
            color = league_colors[team['league']]
            team_url = team.get('url', '')
            league_url = team.get('league_url', '')
            popup_html = f"""
            <div style="font-family: Arial; width: 200px;">
                <h4 style="margin: 0; color: {color};">{team['name']}</h4>
                <hr style="margin: 5px 0;">
                <p style="margin: 2px 0;"><b>League:</b> {team['league']}</p>
                <p style="margin: 2px 0;"><b>Tier:</b> {tier}</p>
                <p style="margin: 2px 0;"><b>Address:</b> {team['address']}</p>
                {f'<p style="margin: 2px 0;"><a href="{team_url}" target="_blank">View Team Page</a></p>' if team_url else ''}
                {f'<p style="margin: 2px 0;"><a href="{league_url}" target="_blank">View League Page</a></p>' if league_url else ''}
            </div>
            """
            
            # Create marker with image and fallback
            if team.get('image_url'):
                # Use DivIcon with img tag that has onerror fallback to default RFU logo
                icon_html = f'''
                <div style="text-align: center;">
                    <img src="{team['image_url']}" 
                         style="width: 30px; height: 30px; border-radius: 50%; border: 2px solid {color};"
                         onerror="this.onerror=null; this.src='https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg';">
                </div>
                '''
                icon = folium.DivIcon(html=icon_html, icon_size=(30, 30), icon_anchor=(15, 15))
                folium.Marker(
                    location=[team['latitude'], team['longitude']],
                    popup=folium.Popup(popup_html, max_width=250),
                    icon=icon,
                    tooltip=team['name']
                ).add_to(tier_groups[tier])
            else:
                # No image URL - use fallback logo
                icon_html = f'''
                <div style="text-align: center;">
                    <img src="https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg" 
                         style="width: 30px; height: 30px; border-radius: 50%; border: 2px solid {color};">
                </div>
                '''
                icon = folium.DivIcon(html=icon_html, icon_size=(30, 30), icon_anchor=(15, 15))
                folium.Marker(
                    location=[team['latitude'], team['longitude']],
                    popup=folium.Popup(popup_html, max_width=250),
                    icon=icon,
                    tooltip=team['name']
                ).add_to(tier_groups[tier])
    
    # Add layer control
    folium.LayerControl(collapsed=False).add_to(m)
    
    # Add legend for tiers and leagues
    legend_html = f'''
    <div style="position: fixed; 
                bottom: 50px; right: 50px; width: 300px; max-height: 500px; overflow-y: auto;
                background-color: white; z-index:9999; font-size:14px;
                border:2px solid grey; border-radius: 5px; padding: 10px">
    <h4 style="margin-top: 0;">All Tiers - Leagues</h4>
    '''
    
    for tier in TIER_ORDER:
        if tier not in teams_by_tier:
            continue
        teams = teams_by_tier[tier]
        legend_html += f'<p style="margin: 10px 0 5px 0;"><b>{tier}</b> ({len(teams)} teams)</p>'
        
        # Group teams by league for this tier
        leagues_in_tier = sorted(set(t['league'] for t in teams))
        for league in leagues_in_tier:
            color = league_colors[league]
            league_team_count = sum(1 for t in teams if t['league'] == league)
            legend_html += f'''
            <p style="margin: 2px 0 2px 15px;">
                <i style="background:{color}; width: 16px; height: 16px; 
                   display: inline-block; border-radius: 50%; border: 1px solid black;"></i>
                {league} ({league_team_count})
            </p>
            '''
    
    legend_html += '</div>'
    m.get_root().html.add_child(folium.Element(legend_html))
    
    # Save map
    output_file = os.path.join(output_dir, 'All_Tiers.html')
    m.save(output_file)
    print(f'Saved All Tiers map with {len(all_teams)} teams to: {output_file}')

def main() -> None:
    print('Loading teams data...')
    teams_by_tier = load_teams_data('geocoded_teams')
    
    total_teams = sum(len(teams) for teams in teams_by_tier.values())
    print(f'\nFound {total_teams} teams across {len(teams_by_tier)} tiers')
    
    print('\nTeams by tier:')
    for tier in TIER_ORDER:
        if tier not in teams_by_tier:
            continue
        print(f'  {tier}: {len(teams_by_tier[tier])} teams')
    
    print('\nLoading ITL hierarchy...')
    itl_hierarchy = load_itl_hierarchy()
    print(f'  Loaded {len(itl_hierarchy["itl3_regions"])} ITL3 regions')
    print(f'  Loaded {len(itl_hierarchy["itl2_regions"])} ITL2 regions')
    print(f'  Loaded {len(itl_hierarchy["itl1_regions"])} ITL1 regions')
    
    print('\nAssigning teams to ITL regions...')
    region_to_teams = assign_teams_to_itl_regions(teams_by_tier, itl_hierarchy)
    
    print('\nCreating tier maps...')
    create_tier_maps(teams_by_tier, region_to_teams, itl_hierarchy)
    
    print('\nCreating all tiers map...')
    create_all_tiers_map(teams_by_tier, region_to_teams, itl_hierarchy)
    
    print('\n✓ All maps created successfully!')
    print('Check \'tier_maps/\' folder for maps')

if __name__ == '__main__':
    main()
