"""
Generate interactive maps for Scottish Rugby leagues.

Reads geocoded team data from scotland/geocoded_teams/ and produces Folium/
Leaflet HTML maps using the shared map_builder module.  Since ITL boundary
data covers England only, Scottish maps use Voronoi-based territories with
a Scotland outline instead.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from html import escape
from pathlib import Path

from core import setup_logging
from core.config import BOUNDARIES_DIR, DIST_DIR
from core.map_builder import (
    MapConfig,
    MarkerItem,
    generate_multi_group_map,
    generate_single_group_map,
    load_itl_hierarchy,
)
from scotland import DATA_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scottish league tier assignment
# ---------------------------------------------------------------------------

_TIER_MAP_MENS: dict[str, tuple[int, str]] = {
    "Premiership": (1, "Premiership"),
    "National League Division 1": (2, "National League 1"),
    "National League Division 2": (3, "National League 2"),
    "National League Division 3": (4, "National League 3"),
    "National League Division 4": (5, "National League 4"),
    "Caledonia Region League Division 1": (6, "Regional Division 1"),
    "East Region League Division 1": (6, "Regional Division 1"),
    "West Region League Division 1": (6, "Regional Division 1"),
    "Caledonia Midlands Region League Division 2": (7, "Regional Division 2"),
    "Caledonia North Region League Division 2": (7, "Regional Division 2"),
    "East Region League Division 2": (7, "Regional Division 2"),
    "West Region League Division 2": (7, "Regional Division 2"),
    "Caledonia Midlands Region League Division 3": (8, "Regional Division 3"),
    "Caledonia North Region League Division 3": (8, "Regional Division 3"),
    "East Region League Division 3": (8, "Regional Division 3"),
    "West Region League Division 3": (8, "Regional Division 3"),
}

_TIER_MAP_WOMENS: dict[str, tuple[int, str]] = {
    "Premiership": (101, "Women's Premiership"),
    "Regional Play-Off Series": (102, "Women's Play-Offs"),
    "West Region League Division 1": (102, "Women's Regional 1"),
    "West Region League  Division 1": (102, "Women's Regional 1"),
    "Caledonia Midlands/East Region League 1": (102, "Women's Regional 1"),
    "Caledonia Midlands/East Region League Division 1": (102, "Women's Regional 1"),
    "Caledonia North Region League 1": (102, "Women's Regional 1"),
    "Caledonia North Region League Division 1": (102, "Women's Regional 1"),
    "West Region League Division 2": (103, "Women's Regional 2"),
    "Caledonia Midlands/East Region League 2": (103, "Women's Regional 2"),
    "Caledonia Midlands/East Region League Division 2": (103, "Women's Regional 2"),
    "Caledonia North Region League 2": (103, "Women's Regional 2"),
    "Caledonia North Region League Division 2": (103, "Women's Regional 2"),
    "West Region League Division 3": (104, "Women's Regional 3"),
    "Caledonia North Region League 3": (104, "Women's Regional 3"),
    "Caledonia North Region League Division 3": (104, "Women's Regional 3"),
}

_BORDER_TIER: tuple[int, str] = (9, "Border Leagues")

COLOR_PALETTE = [
    "#e6194b",
    "#3cb44b",
    "#ffe119",
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
]

BOUNDARY_PATHS = {
    "itl3": str(BOUNDARIES_DIR / "ITL_3.geojson"),
    "itl2": str(BOUNDARIES_DIR / "ITL_2.geojson"),
    "itl1": str(BOUNDARIES_DIR / "ITL_1.geojson"),
    "countries": str(BOUNDARIES_DIR / "countries.geojson"),
    "lad": str(BOUNDARIES_DIR / "local_authority_districts.geojson"),
    "wards": str(BOUNDARIES_DIR / "wards.geojson"),
    "lad_to_itl_lookup": str(BOUNDARIES_DIR / "lad_to_itl.json"),
    "ward_to_lad_lookup": str(BOUNDARIES_DIR / "ward_to_lad.json"),
}

COUNTRY_OUTLINES = ["Scotland"]


def _export_scotland_boundaries(boundaries_path: Path) -> None:
    """Export a boundaries file with the Scotland outline and Scottish ITL regions.

    Filters ITL regions to only those whose centroid falls inside Scotland.
    """
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

    scotland_geom = None
    for feat in countries_data["features"]:
        if feat["properties"].get("CTRY24NM") == "Scotland":
            scotland_geom = shape(feat["geometry"])
            break

    if not scotland_geom:
        logger.warning("Scotland feature not found in countries GeoJSON")
        return

    scotland_prep = prep(scotland_geom)

    boundary_data: dict = {
        "countries": {},
        "itl_1": None,
        "itl_2": None,
        "itl_3": None,
        "lad": None,
        "wards": None,
    }

    boundary_data["countries"]["Scotland"] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": mapping(scotland_geom.simplify(0.001, preserve_topology=True)),
                "properties": {"CTRY24NM": "Scotland"},
            }
        ],
    }

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

        scottish_feats = []
        for feat in data["features"]:
            geom = shape(feat["geometry"])
            if scotland_prep.contains(geom.centroid):
                scottish_feats.append(
                    {
                        "type": "Feature",
                        "geometry": mapping(geom.simplify(0.001, preserve_topology=True)),
                        "properties": feat.get("properties", {}),
                    }
                )

        if scottish_feats:
            boundary_data[level] = {
                "type": "FeatureCollection",
                "features": scottish_feats,
            }
            logger.info("  %s: %d Scottish regions", level, len(scottish_feats))

    boundaries_path.parent.mkdir(parents=True, exist_ok=True)
    with open(boundaries_path, "w") as fout:
        json.dump(boundary_data, fout, separators=(",", ":"))
    logger.info("Exported Scotland boundaries to %s", boundaries_path)


def _strip_sponsor(name: str) -> str:
    """Remove the 'Arnold Clark' sponsor prefix from league names."""
    return re.sub(r"^Arnold Clark\s+", "", name)


def _classify_league(league_name: str) -> tuple[int, str]:
    """Determine tier number and display name for a Scottish league."""
    stripped = _strip_sponsor(league_name)

    is_womens = "(Women's)" in stripped or "Women" in stripped.split("(")[0]

    tier_map = _TIER_MAP_WOMENS if is_womens else _TIER_MAP_MENS

    clean = re.sub(r"\s*\((?:Men's|Women's)\)\s*$", "", stripped).strip()
    clean = re.sub(r"\s+Stage \d+$", "", clean).strip()

    if clean in tier_map:
        return tier_map[clean]

    for key, val in tier_map.items():
        if key in clean or clean in key:
            return val

    if "Border" in league_name or "BSPC" in league_name:
        return _BORDER_TIER

    logger.warning("Unclassified league: %s (cleaned: %s)", league_name, clean)
    return (99, "Other")


# ---------------------------------------------------------------------------
# Popup rendering
# ---------------------------------------------------------------------------


def _render_popup_html(
    team_name: str,
    league_name: str,
    league_url: str,
    team_url: str,
    address: str,
    website: str,
) -> str:
    """Build the popup HTML for a Scottish Rugby team marker."""
    name_esc = escape(team_name)
    league_esc = escape(league_name)
    address_esc = escape(address)

    website_link = (
        f'<p style="margin: 2px 0;"><a href="{escape(website)}" target="_blank">Team Website</a></p>'
        if website
        else ""
    )
    team_link = (
        f'<p style="margin: 2px 0;"><a href="{escape(team_url)}" target="_blank">Club Page</a></p>'
        if team_url
        else ""
    )
    league_link = (
        f'<p style="margin: 2px 0;"><a href="{escape(league_url)}" target="_blank">League Page</a></p>'
        if league_url
        else ""
    )

    return (
        f'<div style="font-family: Arial; width: 220px;">'
        f'<h4 style="margin: 0;">{name_esc}</h4>'
        f'<hr style="margin: 5px 0;">'
        f'<p style="margin: 2px 0;"><b>League:</b> {league_esc}</p>'
        f'<p style="margin: 2px 0;"><b>Address:</b> {address_esc}</p>'
        f"{website_link}{team_link}{league_link}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_marker_items(geocoded_dir: Path) -> list[MarkerItem]:
    """Scan geocoded JSON files and build MarkerItem objects."""
    if not geocoded_dir.is_dir():
        return []

    items: list[MarkerItem] = []

    for filepath in geocoded_dir.rglob("*.json"):
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        league_name = data.get("league_name", "Unknown League")
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
                team.get("website", ""),
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


# ---------------------------------------------------------------------------
# MapConfig builder
# ---------------------------------------------------------------------------


_SHARED_BOUNDARIES = str(DIST_DIR / "scotland" / "shared" / "boundaries.json")


def _build_config(
    title: str,
    season: str,
    palette: list[str] | None = None,
) -> MapConfig:
    return MapConfig(
        title=f"{season} Scotland {title}",
        center=(56.5, -4.0),
        zoom=7,
        show_debug=False,
        tier_entry_level={},
        default_tier_entry_level="itl1",
        tier_floor_level={},
        default_tier_floor_level="itl1",
        use_inline_boundaries=True,
        inline_boundaries_file=_SHARED_BOUNDARIES,
        fallback_icon_url=None,
        color_palette=palette or COLOR_PALETTE,
    )


def _rotated_palette(tier_num: int) -> list[str]:
    n = (len(COLOR_PALETTE) + tier_num - 1) % len(COLOR_PALETTE)
    return COLOR_PALETTE[n:] + COLOR_PALETTE[:n]


def _group_by_tier(
    items: list[MarkerItem],
) -> tuple[dict[str, list[MarkerItem]], list[str]]:
    by_tier: dict[str, list[MarkerItem]] = {}
    for it in items:
        by_tier.setdefault(it.tier, []).append(it)
    order = sorted(by_tier.keys(), key=lambda t: by_tier[t][0].tier_num)
    return by_tier, order


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Scottish Rugby tier maps")
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to process. Default: 2025-2026",
    )
    args = parser.parse_args()

    setup_logging()
    season = args.season

    logger.info("Generating Scottish maps for season: %s", season)

    geocoded_dir = DATA_DIR / "geocoded_teams" / season
    items = _load_marker_items(geocoded_dir)

    if not items:
        logger.error("No geocoded items found in %s", geocoded_dir)
        return

    mens = [it for it in items if it.tier_num < 100]
    womens = [it for it in items if it.tier_num >= 100]

    logger.info(
        "Loaded %d items (%d men's, %d women's)",
        len(items),
        len(mens),
        len(womens),
    )

    itl_hierarchy = load_itl_hierarchy(BOUNDARY_PATHS)

    output_dir = DIST_DIR / "scotland" / season

    scotland_boundaries = Path(_SHARED_BOUNDARIES)
    _export_scotland_boundaries(scotland_boundaries)

    # --- Individual tier maps ---
    for _gender_label, gender_items in [("Men's", mens), ("Women's", womens)]:
        if not gender_items:
            continue
        by_tier, tier_order = _group_by_tier(gender_items)
        for tier_name in tier_order:
            tier_items = by_tier[tier_name]
            tier_num = tier_items[0].tier_num
            file_name = tier_name.replace(" ", "_").replace("'", "")
            out = output_dir / f"{file_name}.html"
            config = _build_config(tier_name, season, _rotated_palette(tier_num))
            generate_single_group_map(tier_items, out, itl_hierarchy, config)

    # --- Combined all-tiers maps ---
    if mens:
        logger.info("Creating men's all-tiers map...")
        out = output_dir / "All_Tiers_Mens.html"
        config = _build_config("All Tiers Men's", season)
        generate_multi_group_map(mens, out, itl_hierarchy, config)

    if womens:
        logger.info("Creating women's all-tiers map...")
        out = output_dir / "All_Tiers_Womens.html"
        config = _build_config("All Tiers Women's", season)
        generate_multi_group_map(womens, out, itl_hierarchy, config)

    # --- Grand combined map ---
    logger.info("Creating combined all-leagues map...")
    out = output_dir / "All_Leagues.html"
    config = _build_config("All Leagues", season)
    generate_multi_group_map(items, out, itl_hierarchy, config)

    logger.info("All maps created in %s", output_dir)


if __name__ == "__main__":
    main()
