"""
Orchestrator script for generating rugby tier maps.

Contains all rugby-specific knowledge: JSON file reading, popup HTML rendering,
tier-to-territory-level mapping, RFU icons, and page chrome (GA, service worker,
back button). Delegates generic map rendering to map_builder.
"""

import argparse
import logging
from dataclasses import dataclass, field, replace
from html import escape
from pathlib import Path
from typing import cast

from core import (
    TravelDistances,
    get_config,
    get_favicon_html,
    get_google_analytics_script,
    json_load_cache,
    set_config,
    setup_logging,
    team_name_to_filepath,
)
from core.config import BOUNDARIES_DIR, DIST_DIR
from core.map_builder import (
    MapConfig,
    MarkerItem,
    export_shared_boundaries,
    generate_multi_group_map,
    generate_single_group_map,
    load_itl_hierarchy,
)
from rugby import DATA_DIR
from rugby.tiers import extract_tier, get_competition_offset, mens_current_tier_name

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
    "#5b2c6f",
    "#1a5276",
    "#b9441e",
    "#117a65",
    "#7d3c98",
    "#2e4053",
    "#c0392b",
    "#1f618d",
    "#884ea0",
    "#239b56",
    "#b7950b",
    "#6c3483",
    "#2874a6",
    "#ca6f1e",
    "#148f77",
    "#a04000",
    "#1b4f72",
    "#7b241c",
]

UNASSIGNED_COLOR = "#cccccc"

# If a region at this level contains items in multiple child regions, everything in this region will
# be shaded.
TIER_ENTRY_LEVELS: dict[int, str] = {
    1: "itl0",
    2: "itl0",
    3: "itl0",
    4: "itl1",
    5: "itl1",
    6: "itl1",
    101: "itl0",
    102: "itl1",
    103: "itl1",
}

# When we only have one item in a region, we narrow in. This dict defines which level this stops at.
TIER_FLOOR_LEVELS: dict[int, str] = {
    1: "itl1",
    2: "itl1",
    3: "itl1",
    4: "itl1",
    5: "itl1",
    6: "itl2",
    7: "itl2",
    101: "itl1",
    102: "itl1",
    103: "itl1",
}

BOUNDARY_PATHS = {
    "itl3": str(BOUNDARIES_DIR / "ITL_3.geojson"),
    "itl2": str(BOUNDARIES_DIR / "ITL_2.geojson"),
    "itl1": str(BOUNDARIES_DIR / "ITL_1.geojson"),
    "countries": str(BOUNDARIES_DIR / "countries.geojson"),
    "lad": str(BOUNDARIES_DIR / "local_authority_districts.geojson"),
    "wards": str(BOUNDARIES_DIR / "wards.geojson"),
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
                f"<hr>"
                f'<p><span class="popup-label">Travel Distances:</span></p>'
                f"<p>Team Average: {team_dist['avg_distance_km']:.2f} km</p>"
                f"<p>Team Total: {team_dist['total_distance_km']:.2f} km</p>"
                f"<p>League Average: {league_dist['avg_distance_km']:.2f} km</p>"
            )

    team_link = (
        f'<p><a href="{escape(team_url)}" target="_blank">View Team Page</a></p>'
        if team_url
        else ""
    )
    league_link = (
        f'<p><a href="{escape(league_url)}" target="_blank">View League Page</a></p>'
        if league_url
        else ""
    )
    info_link = (
        f'<p><a href="__INFO_PREFIX__teams/{team_name_to_filepath(team_name)}" target="_blank">View Info page</a></p>'
        if league_url
        else ""
    )

    return (
        f'<div class="rugby-popup">'
        f'<h4 class="popup-title">{name_esc}</h4>'
        f"<hr>"
        f'<p><span class="popup-label">League:</span> {league_esc}</p>'
        f'<p><span class="popup-label">Address:</span> {address_esc}</p>'
        f"__ITL_REGIONS__"
        f"{team_link}{league_link}{info_link}"
        f"{distance_html}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Page chrome generators
# ---------------------------------------------------------------------------


def _header_bar_html(
    season: str,
    title: str,
    subdirectory_depth: int = 0,
    sibling_tiers: list[tuple[str, str]] | None = None,
    current_tier: str | None = None,
) -> str:
    """Build a fixed breadcrumb header: Home › season › map title (or dropdown)."""
    is_prod = get_config().is_production

    if is_prod:
        home_href = "../" * (2 + subdirectory_depth)
        season_href = "../" * (1 + subdirectory_depth)
    else:
        home_href = "../" * (1 + subdirectory_depth) + "index.html"
        season_href = "../" * subdirectory_depth + "index.html"

    if sibling_tiers and len(sibling_tiers) > 1:
        options = []
        for tier_display, tier_href in sibling_tiers:
            selected = " selected" if tier_display == current_tier else ""
            options.append(
                f'<option value="{escape(tier_href)}"{selected}>' f"{escape(tier_display)}</option>"
            )
        title_html = (
            f'<select class="map-header__select" '
            f'onchange="if(this.value)window.location.href=this.value">'
            f"{''.join(options)}</select>"
        )
    else:
        title_html = f'<span class="map-header__title">{escape(title)}</span>'

    season_esc = escape(season)
    return f"""
    <div class="map-header" id="mapHeader">
        <a class="map-header__crumb" href="{home_href}">Home</a>
        <span class="map-header__sep">&rsaquo;</span>
        <a class="map-header__crumb" href="{season_href}">{season_esc}</a>
        <span class="map-header__sep">&rsaquo;</span>
        {title_html}
    </div>
    <style>
    .map-header {{
        position: fixed; top: 0; left: 0; right: 0; z-index: 1000;
        display: flex; align-items: center; gap: 0.4em;
        padding: 6px 12px;
        background: rgba(255,255,255,0.92); backdrop-filter: blur(8px);
        border-bottom: 1px solid #e0e0e0;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 14px;
    }}
    .map-header__crumb {{
        text-decoration: none; color: #0066cc; white-space: nowrap;
    }}
    .map-header__crumb:hover {{ text-decoration: underline; }}
    .map-header__sep {{ color: #999; font-size: 0.9em; }}
    .map-header__title {{
        font-weight: 600; color: #2c3e50; white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis;
    }}
    .map-header__select {{
        padding: 3px 8px; border: 1px solid #ccc; border-radius: 4px;
        font-size: 13px; background: white; color: #333;
        max-width: 260px; cursor: pointer;
    }}
    .leaflet-top {{ top: 34px !important; }}
    @media (prefers-color-scheme: dark) {{
        .map-header {{ background: rgba(22,33,62,0.92); border-bottom-color: #2a2a4a; }}
        .map-header__crumb {{ color: #4da6ff; }}
        .map-header__sep {{ color: #666; }}
        .map-header__title {{ color: #e0e8f0; }}
        .map-header__select {{ background: #1e2a45; color: #e0e0e0; border-color: #2a2a4a; }}
    }}
    @media (max-width: 480px) {{
        .map-header {{ font-size: 12px; }}
        .map-header__select {{ max-width: 140px; font-size: 11px; }}
    }}
    </style>
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


@dataclass
class LoadedItems:
    """Separated pyramid and merit items loaded from geocoded files."""

    pyramid: list[MarkerItem] = field(default_factory=list)
    merit: dict[str, list[MarkerItem]] = field(default_factory=dict)


def _load_marker_items(
    geocoded_teams_dir: str,
    season: str,
    travel_distances: TravelDistances | None,
) -> LoadedItems:
    """Scan geocoded JSON files and build MarkerItem objects.

    Returns pyramid items in one list and merit items grouped by competition.
    """
    geocoded_path = Path(geocoded_teams_dir)
    if not geocoded_path.is_dir():
        return LoadedItems()

    result = LoadedItems()
    for filepath in geocoded_path.rglob("*.json"):
        rel_path = filepath.relative_to(geocoded_path).as_posix()
        tier_num, tier_name = extract_tier(rel_path, season)

        data = json_load_cache(str(filepath))

        league_name = data.get("league_name", "Unknown League")
        rel_parts = list(filepath.relative_to(geocoded_path).parts)
        is_merit = len(rel_parts) >= 3 and rel_parts[0] == "merit"
        comp_key = rel_parts[1] if is_merit else ""
        if is_merit:
            comp_name = comp_key.replace("_", " ")
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

            category = comp_key.replace("_", " ") if is_merit else "Pyramid"
            item = MarkerItem(
                name=team_name,
                latitude=team["latitude"],
                longitude=team["longitude"],
                group=league_name,
                tier=tier_name,
                tier_num=tier_num,
                icon_url=icon_url,
                popup_html=popup,
                category=category,
            )

            if is_merit:
                result.merit.setdefault(comp_key, []).append(item)
            else:
                result.pyramid.append(item)

    return result


# ---------------------------------------------------------------------------
# MapConfig builder
# ---------------------------------------------------------------------------


def _resolve_info_links(items: list[MarkerItem], subdirectory_depth: int = 0) -> list[MarkerItem]:
    """Replace the ``__INFO_PREFIX__`` placeholder in popup HTML with a path
    that is correct for the output file's depth relative to the season folder."""
    if get_config().is_production:
        prefix = "/"
    else:
        prefix = "../" * (1 + subdirectory_depth)
    return [
        replace(it, popup_html=(it.popup_html or "").replace("__INFO_PREFIX__", prefix))
        for it in items
    ]


def _rotated_palette(tier_num: int) -> list[str]:
    """Rotate the palette so tier N starts from the Nth color."""
    n = (len(COLOR_PALETTE) + tier_num - 1) % len(COLOR_PALETTE)
    return COLOR_PALETTE[n:] + COLOR_PALETTE[:n]


def _build_config(
    title: str,
    season: str,
    show_debug: bool,
    palette: list[str] | None = None,
    subdirectory_depth: int = 0,
    tier_entry_level: dict[int, str] | None = None,
    tier_floor_level: dict[int, str] | None = None,
    sibling_tiers: list[tuple[str, str]] | None = None,
    current_tier: str | None = None,
) -> MapConfig:
    """Build a MapConfig with rugby-specific settings.

    *subdirectory_depth* is how many extra directory levels deep the output is
    relative to the season folder (0 for top-level maps, 2 for
    ``merit/<Competition>/``).

    *tier_entry_level* overrides the default pyramid tier-to-ITL mapping.
    *tier_floor_level* overrides the default pyramid tier-to-floor mapping.
    Pass ``{}`` for merit maps to avoid local tier numbers colliding with
    pyramid-specific entries.

    *sibling_tiers* is a list of (display_name, href) tuples for the tier
    dropdown in the header bar.
    """
    is_prod = get_config().is_production

    favicon_depth = 1 + subdirectory_depth
    meta_desc = f"{season} {title} - interactive map showing team locations and league boundaries."
    header_elements = [
        get_favicon_html(depth=favicon_depth),
        f'<meta name="description" content="{escape(meta_desc)}">',
        get_google_analytics_script(),
    ]
    if is_prod:
        header_elements.append(_service_worker_html())

    body_elements = [
        _header_bar_html(
            season,
            title,
            subdirectory_depth,
            sibling_tiers=sibling_tiers,
            current_tier=current_tier,
        )
    ]

    if is_prod:
        shared_path = "/shared"
    else:
        shared_path = "../" * (1 + subdirectory_depth) + "shared"

    return MapConfig(
        title=f"{season} {title}",
        center=(52.5, -1.5),
        zoom=7,
        show_debug=show_debug,
        tier_entry_level=tier_entry_level if tier_entry_level is not None else TIER_ENTRY_LEVELS,
        default_tier_entry_level="itl2",
        tier_floor_level=tier_floor_level if tier_floor_level is not None else TIER_FLOOR_LEVELS,
        default_tier_floor_level="itl3",
        use_inline_boundaries=not is_prod,
        shared_boundaries_path=shared_path,
        fallback_icon_url=RFU_FALLBACK_ICON,
        color_palette=palette or COLOR_PALETTE,
        header_elements=header_elements,
        body_elements=body_elements,
    )


# ---------------------------------------------------------------------------
# Helpers for grouping items by tier
# ---------------------------------------------------------------------------


def _group_by_tier(
    items: list[MarkerItem],
) -> tuple[dict[str, list[MarkerItem]], list[str]]:
    """Group items by tier name and return (by_tier dict, tier names sorted by tier_num)."""
    by_tier: dict[str, list[MarkerItem]] = {}
    for it in items:
        by_tier.setdefault(it.tier, []).append(it)
    order = sorted(by_tier.keys(), key=lambda t: by_tier[t][0].tier_num)
    return by_tier, order


def _tier_sibling_links(
    tier_order: list[str],
    is_prod: bool,
    prefix: str = "",
) -> list[tuple[str, str]]:
    """Build (display_name, href) pairs for all tiers in a group."""
    links: list[tuple[str, str]] = []
    for tier_name in tier_order:
        file_name = tier_name.replace(" ", "_")
        href = f"{prefix}../{file_name}/" if is_prod else f"{prefix}{file_name}.html"
        links.append((tier_name, href))
    return links


def _output_path(output_dir: Path, name: str, is_prod: bool) -> Path:
    if is_prod:
        return output_dir / name / "index.html"
    return output_dir / f"{name}.html"


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
    parser.add_argument(
        "--all-tiers", action="store_true", help="Generate pyramid-only all-tiers combined maps"
    )
    parser.add_argument(
        "--all-tiers-mens", action="store_true", help="Generate men's pyramid all-tiers map"
    )
    parser.add_argument(
        "--all-tiers-womens", action="store_true", help="Generate women's pyramid all-tiers map"
    )
    parser.add_argument(
        "--all-leagues", action="store_true", help="Generate all-leagues maps (pyramid + merit)"
    )
    parser.add_argument("--merit", action="store_true", help="Generate per-competition merit maps")
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
        or args.all_leagues
        or args.merit
        or args.tiers
    )
    gen_mens_individual = not any_flag or args.mens or args.tiers
    gen_womens_individual = not any_flag or args.womens or args.tiers
    gen_all_tiers_men = not any_flag or args.all_tiers_mens or args.all_tiers
    gen_all_tiers_women = not any_flag or args.all_tiers_womens or args.all_tiers
    gen_all_leagues = not any_flag or args.all_leagues
    gen_merit = not any_flag or args.merit

    # Load travel distances
    logger.debug("Loading team travel distances...")
    travel_distance_path = DATA_DIR / "distance_cache" / f"{season}.json"
    travel_distances: TravelDistances | None = None
    if travel_distance_path.exists():
        travel_distances = cast(TravelDistances, json_load_cache(travel_distance_path))
        logger.debug("  Loaded travel distances for %d teams", len(travel_distances["teams"]))
    else:
        logger.debug("  No travel distance data found")

    # Build MarkerItem objects from geocoded files
    logger.debug("Loading marker items from geocoded files...")
    geocoded_dir = str(DATA_DIR / "geocoded_teams" / season)
    loaded = _load_marker_items(geocoded_dir, season, travel_distances)

    # Split pyramid items by gender
    mens_pyramid = [it for it in loaded.pyramid if it.tier_num < 100]
    womens_pyramid = [it for it in loaded.pyramid if it.tier_num >= 100]

    if args.tiers:
        mens_pyramid = [it for it in mens_pyramid if it.tier in args.tiers]
        womens_pyramid = [it for it in womens_pyramid if it.tier in args.tiers]

    # Group pyramid items by tier
    mens_by_tier, mens_tier_order = _group_by_tier(mens_pyramid)
    womens_by_tier, womens_tier_order = _group_by_tier(womens_pyramid)

    # Build pyramid-adjusted copies of merit items for the combined All_Leagues map.
    # Merit items use local tier numbers; add the competition offset to get absolute.
    adjusted_merit: list[MarkerItem] = []
    for comp_key, comp_items in loaded.merit.items():
        offset = get_competition_offset(comp_key, season)
        for it in comp_items:
            abs_tier = it.tier_num + offset
            adjusted_merit.append(
                replace(it, tier_num=abs_tier, tier=mens_current_tier_name(abs_tier, season))
            )

    total_items = len(loaded.pyramid) + len(adjusted_merit)
    logger.info(
        "Loaded %d items (%d pyramid, %d merit across %d competitions)",
        total_items,
        len(loaded.pyramid),
        len(adjusted_merit),
        len(loaded.merit),
    )

    # Load shared data
    logger.debug("Loading ITL hierarchy...")
    itl_hierarchy = load_itl_hierarchy(BOUNDARY_PATHS)

    output_dir = DIST_DIR / season
    is_prod = get_config().is_production

    logger.debug("Exporting shared boundary data...")
    export_shared_boundaries(
        BOUNDARY_PATHS,
        output_dir=str(DIST_DIR / "shared"),
        country_names=COUNTRY_OUTLINES,
        skip_if_exists=is_prod,
    )

    # Resolve info-page links for top-level maps (depth 0).
    # Merit maps at depth 2 are resolved separately below.
    mens_pyramid_r = _resolve_info_links(mens_pyramid)
    womens_pyramid_r = _resolve_info_links(womens_pyramid)
    adjusted_merit_r = _resolve_info_links(adjusted_merit)

    # Group adjusted merit items by their absolute tier number for per-tier
    # combined maps (pyramid tier + merit at same level).
    merit_by_tier_num: dict[int, list[MarkerItem]] = {}
    for it in adjusted_merit_r:
        merit_by_tier_num.setdefault(it.tier_num, []).append(it)

    # ------------------------------------------------------------------
    # Individual pyramid tier maps (+ optional pyramid+merit variants)
    # ------------------------------------------------------------------
    if gen_mens_individual and mens_by_tier:
        logger.info("Creating men's pyramid tier maps...")
        mens_by_tier_r, mens_tier_order_r = _group_by_tier(mens_pyramid_r)
        mens_siblings = _tier_sibling_links(mens_tier_order_r, is_prod)
        for tier_name in mens_tier_order_r:
            tier_items = mens_by_tier_r[tier_name]
            tier_num = tier_items[0].tier_num
            out = _output_path(output_dir, tier_name.replace(" ", "_"), is_prod)
            config = _build_config(
                tier_name,
                season,
                show_debug,
                _rotated_palette(tier_num),
                sibling_tiers=mens_siblings,
                current_tier=tier_name,
            )
            generate_single_group_map(tier_items, out, itl_hierarchy, config)

            # Pyramid + merit at same level
            merit_at_level = merit_by_tier_num.get(tier_num, [])
            if merit_at_level:
                combined = tier_items + merit_at_level
                file_name = tier_name.replace(" ", "_") + "_All_Leagues"
                out = _output_path(output_dir, file_name, is_prod)
                config = _build_config(
                    f"{tier_name} + Merit", season, show_debug, _rotated_palette(tier_num)
                )
                generate_single_group_map(combined, out, itl_hierarchy, config)

        # Merit-only tiers below the pyramid
        pyramid_tier_nums = {it[0].tier_num for it in mens_by_tier_r.values()}
        for tier_num in sorted(merit_by_tier_num):
            if tier_num in pyramid_tier_nums:
                continue
            merit_items = merit_by_tier_num[tier_num]
            tier_name = mens_current_tier_name(tier_num, season)
            file_name = tier_name.replace(" ", "_") + "_All_Leagues"
            out = _output_path(output_dir, file_name, is_prod)
            config = _build_config(
                f"{tier_name} (Merit)", season, show_debug, _rotated_palette(tier_num)
            )
            generate_single_group_map(merit_items, out, itl_hierarchy, config)

    if gen_womens_individual and womens_by_tier:
        logger.info("Creating women's pyramid tier maps...")
        womens_by_tier_r, womens_tier_order_r = _group_by_tier(womens_pyramid_r)
        womens_siblings = _tier_sibling_links(womens_tier_order_r, is_prod)
        for tier_name in womens_tier_order_r:
            tier_items = womens_by_tier_r[tier_name]
            tier_num = tier_items[0].tier_num
            out = _output_path(output_dir, tier_name.replace(" ", "_"), is_prod)
            config = _build_config(
                tier_name,
                season,
                show_debug,
                _rotated_palette(tier_num),
                sibling_tiers=womens_siblings,
                current_tier=tier_name,
            )
            generate_single_group_map(tier_items, out, itl_hierarchy, config)

    # ------------------------------------------------------------------
    # Pyramid-only all-tiers maps
    # ------------------------------------------------------------------
    if gen_all_tiers_men and mens_pyramid_r:
        logger.info("Creating men's pyramid all-tiers map...")
        out = _output_path(output_dir, "All_Tiers", is_prod)
        config = _build_config("All Tiers Men", season, show_debug)
        generate_multi_group_map(mens_pyramid_r, out, itl_hierarchy, config)

    if gen_all_tiers_women and womens_pyramid_r:
        logger.info("Creating women's pyramid all-tiers map...")
        out = _output_path(output_dir, "All_Tiers_Women", is_prod)
        config = _build_config("All Tiers Women", season, show_debug)
        generate_multi_group_map(womens_pyramid_r, out, itl_hierarchy, config)

    # ------------------------------------------------------------------
    # All-leagues maps (pyramid + merit combined)
    # ------------------------------------------------------------------
    if gen_all_leagues:
        mens_all = mens_pyramid_r + adjusted_merit_r
        if mens_all:
            logger.info("Creating men's all-leagues map (pyramid + merit)...")
            out = _output_path(output_dir, "All_Leagues", is_prod)
            config = _build_config("All Leagues Men", season, show_debug)
            generate_multi_group_map(mens_all, out, itl_hierarchy, config)

    # ------------------------------------------------------------------
    # Per-competition merit maps
    # ------------------------------------------------------------------
    if gen_merit and loaded.merit:
        logger.info("Creating merit competition maps...")
        merit_dir = output_dir / "merit"

        for comp_key in sorted(loaded.merit):
            comp_items = loaded.merit[comp_key]
            if not comp_items:
                continue

            offset = get_competition_offset(comp_key, season)
            comp_display = comp_key.replace("_", " ")
            comp_dir = merit_dir / comp_key
            logger.debug(
                "  Competition: %s (%d items, offset %d)",
                comp_display,
                len(comp_items),
                offset,
            )

            # Resolve info links for merit depth (2 extra directories)
            comp_items_r = _resolve_info_links(comp_items, subdirectory_depth=2)

            # Combined map for the competition (local tier numbering)
            out = _output_path(comp_dir, "All_Tiers", is_prod)
            config = _build_config(
                f"{comp_display} All Tiers",
                season,
                show_debug,
                subdirectory_depth=2,
                tier_entry_level={},
                tier_floor_level={},
            )
            generate_multi_group_map(comp_items_r, out, itl_hierarchy, config)

            # Per-tier maps within the competition
            comp_by_tier, comp_tier_order = _group_by_tier(comp_items_r)
            comp_siblings = _tier_sibling_links(comp_tier_order, is_prod)
            for tier_name in comp_tier_order:
                tier_items = comp_by_tier[tier_name]
                local_tier = tier_items[0].tier_num
                file_name = tier_name.replace(" ", "_")
                out = _output_path(comp_dir, file_name, is_prod)
                config = _build_config(
                    f"{comp_display} {tier_name}",
                    season,
                    show_debug,
                    _rotated_palette(local_tier + offset),
                    subdirectory_depth=2,
                    tier_entry_level={},
                    tier_floor_level={},
                    sibling_tiers=comp_siblings,
                    current_tier=tier_name,
                )
                generate_single_group_map(tier_items, out, itl_hierarchy, config)

    logger.info("All maps created successfully in %s", output_dir)


if __name__ == "__main__":
    main()
