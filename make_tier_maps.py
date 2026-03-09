"""
Orchestrator script for generating rugby tier maps.

Contains all rugby-specific knowledge: JSON file reading, popup HTML rendering,
tier-to-territory-level mapping, RFU icons, and page chrome (GA, service worker,
back button). Delegates generic map rendering to map_builder.
"""

import argparse
import logging
from html import escape
from pathlib import Path
from typing import cast

from map_builder import (
    MapConfig,
    MarkerItem,
    export_shared_boundaries,
    generate_multi_group_map,
    generate_single_group_map,
    load_itl_hierarchy,
)
from tier_extraction import extract_tier
from utils import (
    TravelDistances,
    get_config,
    get_google_analytics_script,
    json_load_cache,
    set_config,
    setup_logging,
    team_name_to_filepath,
)

logger = logging.getLogger(__name__)

RFU_FALLBACK_ICON = "https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg"

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
    "#ff6b6b",
    "#4ecdc4",
    "#95e1d3",
    "#f38181",
    "#aa96da",
    "#fcbad3",
    "#a8d8ea",
    "#ffcfd2",
]

TIER_ENTRY_LEVELS: dict[int, str] = {
    1: "itl0",
    2: "itl0",
    3: "itl0",
    4: "itl1",
    101: "itl0",
    102: "itl1",
    103: "itl1",
}

BOUNDARY_PATHS = {
    "itl3": "boundaries/ITL_3.geojson",
    "itl2": "boundaries/ITL_2.geojson",
    "itl1": "boundaries/ITL_1.geojson",
    "countries": "boundaries/countries.geojson",
    "lad": "boundaries/local_authority_districts.geojson",
    "wards": "boundaries/wards.geojson",
}

COUNTRY_OUTLINES = ["England", "Isle of Man", "Jersey", "Guernsey"]


# ---------------------------------------------------------------------------
# Rugby-specific popup rendering
# ---------------------------------------------------------------------------


def _render_popup_html(
    team_name: str,
    league_name: str,
    league_url: str,
    team_url: str,
    address: str,
    travel_distances: TravelDistances | None,
) -> str:
    """Build the popup HTML for a rugby team marker."""
    name_esc = escape(team_name)
    league_esc = escape(league_name)
    address_esc = escape(address)

    distance_html = ""
    if travel_distances:
        team_dist = travel_distances["teams"].get(team_name)
        league_dist = travel_distances["leagues"].get(league_name)
        if team_dist and league_dist:
            distance_html = (
                f'<hr style="margin: 5px 0;"><p style="margin: 2px 0;"><b>Travel Distances:</b></p>'
                f'<p style="margin: 2px 0;">Team Average: {team_dist["avg_distance_km"]:.2f} km</p>'
                f'<p style="margin: 2px 0;">Team Total: {team_dist["total_distance_km"]:.2f} km</p>'
                f'<p style="margin: 2px 0;">League Average: {league_dist["avg_distance_km"]:.2f} km</p>'
            )

    team_link = (
        f'<p style="margin: 2px 0;"><a href="{escape(team_url)}" target="_blank">View Team Page</a></p>'
        if team_url
        else ""
    )
    league_link = (
        f'<p style="margin: 2px 0;"><a href="{escape(league_url)}" target="_blank">View League Page</a></p>'
        if league_url
        else ""
    )
    info_prefix = "" if get_config().is_production else "../"
    info_link = (
        f'<p style="margin: 2px 0;"><a href="{info_prefix}/teams/{team_name_to_filepath(team_name)}" target="_blank">View Info page</a></p>'
        if league_url
        else ""
    )

    return (
        f'<div style="font-family: Arial; width: 220px;">'
        f'<h4 style="margin: 0;">{name_esc}</h4>'
        f'<hr style="margin: 5px 0;">'
        f'<p style="margin: 2px 0;"><b>League:</b> {league_esc}</p>'
        f'<p style="margin: 2px 0;"><b>Address:</b> {address_esc}</p>'
        f"{team_link}{league_link}{info_link}"
        f"{distance_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Page chrome generators
# ---------------------------------------------------------------------------


def _back_button_html() -> str:
    href = "../" if get_config().is_production else "index.html"
    return f"""
    <script>
    function addBackButtonToLeafletZoom() {{
        var zoom = document.querySelector('.leaflet-control-zoom');
        if (!zoom) return;
        var zoomClone = zoom.cloneNode(true);
        zoomClone.innerHTML = '';
        var backBtn = document.createElement('a');
        backBtn.className = 'leaflet-control-zoom-back leaflet-bar-part';
        backBtn.href = '{href}';
        backBtn.title = 'Back';
        backBtn.setAttribute('role', 'button');
        backBtn.setAttribute('aria-label', 'Back');
        backBtn.innerHTML = '&larr;';
        zoomClone.appendChild(backBtn);
        zoom.parentNode.insertBefore(zoomClone, zoom);
    }}
    if (window.addEventListener) {{ window.addEventListener('DOMContentLoaded', addBackButtonToLeafletZoom); }}
    else {{ window.attachEvent('onload', addBackButtonToLeafletZoom); }}
    </script>
    """


def _service_worker_html() -> str:
    return """
    <script>
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/service-worker.js')
            .then(function(reg) {
                if (reg.waiting) { reg.waiting.postMessage({type: 'SKIP_WAITING'}); }
            })
            .catch(function(err) { console.log('ServiceWorker registration failed:', err); });
        navigator.serviceWorker.addEventListener('controllerchange', function() {});
    }
    </script>
    """


# ---------------------------------------------------------------------------
# Data loading – build MarkerItem objects from geocoded JSON
# ---------------------------------------------------------------------------


def _load_marker_items(
    geocoded_teams_dir: str,
    season: str,
    travel_distances: TravelDistances | None,
) -> list[MarkerItem]:
    """Scan geocoded JSON files and build MarkerItem objects."""
    geocoded_path = Path(geocoded_teams_dir)
    if not geocoded_path.is_dir():
        return []

    items: list[MarkerItem] = []
    for filepath in geocoded_path.rglob("*.json"):
        rel_path = filepath.relative_to(geocoded_path).as_posix()
        tier_num, tier_name = extract_tier(rel_path, season)

        data = json_load_cache(str(filepath))

        league_name = data.get("league_name", "Unknown League")
        rel_parts = filepath.relative_to(geocoded_path).parts
        if len(rel_parts) >= 3 and rel_parts[0] == "merit":
            comp_name = rel_parts[1].replace("_", " ")
            if comp_name.lower() not in league_name.lower():
                league_name = f"{comp_name} {league_name}"

        league_url = data.get("league_url", "")

        for team in data.get("teams", []):
            if "latitude" not in team or "longitude" not in team:
                continue

            team_name = team["name"]
            team_url = team.get("url", "")
            address = team.get("formatted_address", team.get("address", ""))
            icon_url = team.get("image_url") or RFU_FALLBACK_ICON

            popup = _render_popup_html(
                team_name, league_name, league_url, team_url, address, travel_distances
            )

            items.append(
                MarkerItem(
                    name=team_name,
                    latitude=team["latitude"],
                    longitude=team["longitude"],
                    group=league_name,
                    tier=tier_name,
                    tier_num=tier_num,
                    icon_url=icon_url,
                    popup_html=popup,
                )
            )

    return items


# ---------------------------------------------------------------------------
# MapConfig builder
# ---------------------------------------------------------------------------


def _rotated_palette(tier_num: int) -> list[str]:
    """Rotate the palette so tier N starts from the Nth color."""
    n = (len(COLOR_PALETTE) - tier_num - 1) % len(COLOR_PALETTE)
    return COLOR_PALETTE[n:] + COLOR_PALETTE[:n]


def _build_config(
    title: str,
    season: str,
    show_debug: bool,
    palette: list[str] | None = None,
) -> MapConfig:
    """Build a MapConfig with rugby-specific settings.

    If *palette* is not given, the base COLOR_PALETTE is used.
    """
    """Build a MapConfig with rugby-specific settings."""
    is_prod = get_config().is_production

    header_elements = [get_google_analytics_script()]
    if is_prod:
        header_elements.append(_service_worker_html())

    body_elements = [_back_button_html()]

    return MapConfig(
        title=f"{season} {title}",
        center=(52.5, -1.5),
        zoom=7,
        show_debug=show_debug,
        tier_entry_level=TIER_ENTRY_LEVELS,
        default_tier_entry_level="itl2",
        use_inline_boundaries=not is_prod,
        shared_boundaries_path="/shared" if is_prod else "../shared",
        color_palette=palette or COLOR_PALETTE,
        header_elements=header_elements,
        body_elements=body_elements,
    )


# ---------------------------------------------------------------------------
# CLI and main
# ---------------------------------------------------------------------------


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
        help="Specific tiers to generate (e.g., 'Premiership' 'Championship').",
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

    set_config(is_production=args.production, season=args.season, show_debug=not args.no_debug)
    setup_logging()

    season = args.season
    show_debug = not args.no_debug
    logger.info("Generating maps for season: %s", season)

    # Determine what to generate
    any_flag = (
        args.mens
        or args.womens
        or args.all_tiers
        or args.all_tiers_mens
        or args.all_tiers_womens
        or args.tiers
    )
    gen_mens_individual = not any_flag or args.mens or args.tiers
    gen_womens_individual = not any_flag or args.womens or args.tiers
    gen_all_tiers_men = not any_flag or args.all_tiers_mens or args.all_tiers
    gen_all_tiers_women = not any_flag or args.all_tiers_womens or args.all_tiers

    # Load travel distances
    logger.info("Loading team travel distances...")
    travel_distance_path = Path("distance_cache_folder") / f"{season}.json"
    travel_distances: TravelDistances | None = None
    if travel_distance_path.exists():
        travel_distances = cast(TravelDistances, json_load_cache(travel_distance_path))
        logger.info("  Loaded travel distances for %d teams", len(travel_distances["teams"]))
    else:
        logger.info("  No travel distance data found")

    # Build MarkerItem objects from geocoded files
    logger.info("Loading marker items from geocoded files...")
    geocoded_dir = str(Path("geocoded_teams") / season)
    all_items = _load_marker_items(geocoded_dir, season, travel_distances)

    mens_items = [it for it in all_items if it.tier_num < 100]
    womens_items = [it for it in all_items if it.tier_num >= 100]

    if args.tiers:
        mens_items = [it for it in mens_items if it.tier in args.tiers]
        womens_items = [it for it in womens_items if it.tier in args.tiers]

    # Group by tier for individual maps
    mens_by_tier: dict[str, list[MarkerItem]] = {}
    for it in mens_items:
        mens_by_tier.setdefault(it.tier, []).append(it)
    mens_tier_order = sorted(mens_by_tier.keys(), key=lambda t: mens_by_tier[t][0].tier_num)

    womens_by_tier: dict[str, list[MarkerItem]] = {}
    for it in womens_items:
        womens_by_tier.setdefault(it.tier, []).append(it)
    womens_tier_order = sorted(womens_by_tier.keys(), key=lambda t: womens_by_tier[t][0].tier_num)

    logger.info(
        "Found %d items (%d men's tiers, %d women's tiers)",
        len(all_items),
        len(mens_by_tier),
        len(womens_by_tier),
    )

    # Load shared data
    logger.info("Loading ITL hierarchy...")
    itl_hierarchy = load_itl_hierarchy(BOUNDARY_PATHS)
    logger.info(
        "  Loaded %d ITL3, %d ITL2, %d ITL1 regions",
        len(itl_hierarchy["itl3_regions"]),
        len(itl_hierarchy["itl2_regions"]),
        len(itl_hierarchy["itl1_regions"]),
    )

    output_dir = Path("tier_maps") / season
    is_prod = get_config().is_production

    logger.info("Exporting shared boundary data...")
    export_shared_boundaries(
        BOUNDARY_PATHS,
        output_dir="tier_maps/shared",
        country_names=COUNTRY_OUTLINES,
        skip_if_exists=get_config().is_production,
    )

    # Generate individual tier maps
    if gen_mens_individual and mens_by_tier:
        logger.info("Creating men's tier maps...")
        for tier_name in mens_tier_order:
            tier_items = mens_by_tier[tier_name]
            tier_num = tier_items[0].tier_num
            file_name = tier_name.replace(" ", "_")
            out = (
                output_dir / file_name / "index.html"
                if is_prod
                else output_dir / f"{file_name}.html"
            )
            config = _build_config(tier_name, season, show_debug, _rotated_palette(tier_num))
            generate_single_group_map(tier_items, out, itl_hierarchy, config)

    if gen_womens_individual and womens_by_tier:
        logger.info("Creating women's tier maps...")
        for tier_name in womens_tier_order:
            tier_items = womens_by_tier[tier_name]
            tier_num = tier_items[0].tier_num
            file_name = tier_name.replace(" ", "_")
            out = (
                output_dir / file_name / "index.html"
                if is_prod
                else output_dir / f"{file_name}.html"
            )
            config = _build_config(tier_name, season, show_debug, _rotated_palette(tier_num))
            generate_single_group_map(tier_items, out, itl_hierarchy, config)

    # Generate all-tiers maps (default palette -- module handles per-tier offset internally)
    if gen_all_tiers_men and mens_items:
        logger.info("Creating men's all tiers map...")
        out_name = "All_Tiers"
        out = output_dir / out_name / "index.html" if is_prod else output_dir / f"{out_name}.html"
        config = _build_config("All Tiers Men", season, show_debug)
        generate_multi_group_map(mens_items, out, itl_hierarchy, config)

    if gen_all_tiers_women and womens_items:
        logger.info("Creating women's all tiers map...")
        out_name = "All_Tiers_Women"
        out = output_dir / out_name / "index.html" if is_prod else output_dir / f"{out_name}.html"
        config = _build_config("All Tiers Women", season, show_debug)
        generate_multi_group_map(womens_items, out, itl_hierarchy, config)

    logger.info("All maps created successfully!")
    logger.info('Check "%s" folder for maps', output_dir)


if __name__ == "__main__":
    main()
