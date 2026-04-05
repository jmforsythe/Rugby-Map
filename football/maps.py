"""
Generate an interactive multi-group map for BSLFL football leagues.

Reads geocoded team data from football/geocoded_teams/ and produces a
Folium/Leaflet HTML map using the shared map_builder module, with each
division as a toggleable layer.

Output: football/maps/{season}/BSLFL_All_Divisions.html
"""

from __future__ import annotations

import argparse
import json
import logging
from html import escape
from pathlib import Path

from core import setup_logging
from core.config import BOUNDARIES_DIR, DIST_DIR
from core.map_builder import MapConfig, MarkerItem, generate_multi_group_map, load_itl_hierarchy
from football import DATA_DIR

logger = logging.getLogger(__name__)

_TIER_MAP: dict[str, tuple[int, str]] = {
    "John Cooper Premier Division": (1, "Premier Division"),
    "Jim Hampson First Division": (2, "Division One"),
    "Richard Ayling Second Division": (3, "Division Two"),
    "Division Three": (4, "Division Three"),
}

COLOR_PALETTE = [
    "#e6194b",
    "#3cb44b",
    "#0082c8",
    "#f58231",
    "#911eb4",
    "#46f0f0",
    "#f032e6",
    "#6a8f00",
    "#fabebe",
    "#008080",
    "#e6beff",
    "#aa6e28",
    "#fffac8",
    "#800000",
    "#008f5a",
    "#808000",
    "#ffd8b1",
    "#000080",
    "#808080",
    "#ffe119",
]

BOUNDARY_PATHS = {
    "itl3": str(BOUNDARIES_DIR / "ITL_3.geojson"),
    "itl2": str(BOUNDARIES_DIR / "ITL_2.geojson"),
    "itl1": str(BOUNDARIES_DIR / "ITL_1.geojson"),
    "countries": str(BOUNDARIES_DIR / "countries.geojson"),
    "lad": str(BOUNDARIES_DIR / "local_authority_districts.geojson"),
    "wards": str(BOUNDARIES_DIR / "wards.geojson"),
}

COUNTRY_OUTLINES = ["England"]


def _classify_league(league_name: str) -> tuple[int, str]:
    if league_name in _TIER_MAP:
        return _TIER_MAP[league_name]
    logger.warning("Unclassified league: %s", league_name)
    return (99, league_name)


def _render_popup_html(
    team_name: str,
    league_name: str,
    league_url: str,
    team_url: str,
    address: str,
) -> str:
    name_esc = escape(team_name)
    league_esc = escape(league_name)
    address_esc = escape(address)

    team_link = (
        f'<p style="margin: 2px 0;"><a href="{escape(team_url)}" target="_blank">Team Page</a></p>'
        if team_url
        else ""
    )
    league_link = (
        f'<p style="margin: 2px 0;"><a href="{escape(league_url)}" target="_blank">League Table</a></p>'
        if league_url
        else ""
    )

    return (
        f'<div style="font-family: Arial; width: 220px;">'
        f'<h4 style="margin: 0;">{name_esc}</h4>'
        f'<hr style="margin: 5px 0;">'
        f'<p style="margin: 2px 0;"><b>Division:</b> {league_esc}</p>'
        f'<p style="margin: 2px 0;"><b>Ground:</b> {address_esc}</p>'
        f"{team_link}{league_link}"
        f"</div>"
    )


def _load_marker_items(geocoded_dir: Path) -> list[MarkerItem]:
    if not geocoded_dir.is_dir():
        return []

    items: list[MarkerItem] = []

    for filepath in sorted(geocoded_dir.rglob("*.json")):
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        league_name = data.get("league_name", "Unknown")
        league_url = data.get("league_url", "")
        tier_num, tier_name = _classify_league(league_name)

        for team in data.get("teams", []):
            if "latitude" not in team or "longitude" not in team:
                continue

            popup = _render_popup_html(
                team["name"],
                league_name,
                league_url,
                team.get("url", ""),
                team.get("formatted_address", team.get("address", "")),
            )

            items.append(
                MarkerItem(
                    name=team["name"],
                    latitude=team["latitude"],
                    longitude=team["longitude"],
                    group=league_name,
                    tier=tier_name,
                    tier_num=tier_num,
                    icon_url=team.get("image_url"),
                    popup_html=popup,
                )
            )

    return items


_SCRIPT_DIR = Path(__file__).parent
_SHARED_BOUNDARIES = str(_SCRIPT_DIR / "maps" / "shared" / "boundaries.json")


def _build_config(title: str, season: str, *, is_production: bool = False) -> MapConfig:
    return MapConfig(
        title=f"BSLFL {title} {season}",
        center=(51.42, 0.05),
        zoom=11,
        show_debug=not is_production,
        default_tier_entry_level="itl3",
        default_tier_floor_level="lad",
        use_inline_boundaries=True,
        inline_boundaries_file=_SHARED_BOUNDARIES,
        fallback_icon_url=None,
        color_palette=COLOR_PALETTE,
    )


def _export_england_boundaries(boundaries_path: Path) -> None:
    """Export a boundaries file with the England outline and SE England regions."""
    if boundaries_path.exists():
        return

    from shapely.geometry import mapping, shape
    from shapely.prepared import prep

    countries_file = Path(BOUNDARY_PATHS["countries"])
    if not countries_file.exists():
        logger.warning("Countries GeoJSON not found at %s", countries_file)
        return

    with open(countries_file, encoding="utf-8") as f:
        countries_data = json.load(f)

    england_geom = None
    for feat in countries_data["features"]:
        if feat["properties"].get("CTRY24NM") == "England":
            england_geom = shape(feat["geometry"])
            break

    if not england_geom:
        logger.warning("England feature not found in countries GeoJSON")
        return

    boundary_data: dict = {
        "countries": {
            "England": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": mapping(england_geom.simplify(0.001, preserve_topology=True)),
                        "properties": {"CTRY24NM": "England"},
                    }
                ],
            }
        },
        "itl_1": None,
        "itl_2": None,
        "itl_3": None,
        "lad": None,
        "wards": None,
    }

    england_prep = prep(england_geom)

    for level, key, _name_prop in [
        ("itl_1", "itl1", "ITL125NM"),
        ("itl_2", "itl2", "ITL225NM"),
        ("itl_3", "itl3", "ITL325NM"),
    ]:
        gp = Path(BOUNDARY_PATHS.get(key, ""))
        if not gp.exists():
            continue
        with open(gp, encoding="utf-8") as f:
            data = json.load(f)

        feats = []
        for feat in data["features"]:
            geom = shape(feat["geometry"])
            if england_prep.contains(geom.centroid):
                feats.append(
                    {
                        "type": "Feature",
                        "geometry": mapping(geom.simplify(0.001, preserve_topology=True)),
                        "properties": feat.get("properties", {}),
                    }
                )

        if feats:
            boundary_data[level] = {
                "type": "FeatureCollection",
                "features": feats,
            }
            logger.info("  %s: %d regions", level, len(feats))

    boundaries_path.parent.mkdir(parents=True, exist_ok=True)
    with open(boundaries_path, "w") as fout:
        json.dump(boundary_data, fout, separators=(",", ":"))
    logger.info("Exported England boundaries to %s", boundaries_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate BSLFL league map")
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to process. Default: 2025-2026",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="Production mode: output to dist/football/maps/, hide debug layers",
    )
    args = parser.parse_args()

    setup_logging()
    season = args.season
    is_prod = args.production

    logger.info("Generating BSLFL map for season: %s (production=%s)", season, is_prod)

    geocoded_dir = DATA_DIR / "geocoded_teams" / season / "BSLFL"
    items = _load_marker_items(geocoded_dir)

    if not items:
        logger.error("No geocoded items found in %s", geocoded_dir)
        return

    logger.info("Loaded %d teams across %d divisions", len(items), len(_TIER_MAP))

    boundaries_path = Path(_SHARED_BOUNDARIES)
    _export_england_boundaries(boundaries_path)

    itl_hierarchy = load_itl_hierarchy(BOUNDARY_PATHS)

    if is_prod:
        output_dir = DIST_DIR / "football" / "maps" / season
    else:
        output_dir = _SCRIPT_DIR / "maps" / season
    output_dir.mkdir(parents=True, exist_ok=True)

    out = output_dir / "BSLFL_All_Divisions.html"
    config = _build_config("All Divisions", season, is_production=is_prod)
    generate_multi_group_map(items, out, itl_hierarchy, config)

    logger.info("Map saved to %s", out)


if __name__ == "__main__":
    main()
