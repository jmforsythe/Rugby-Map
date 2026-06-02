"""Shared helpers for football Folium map generation."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from core import (
    get_favicon_html,
    get_google_analytics_script,
    get_service_worker_registration_script,
    set_config,
)
from core.config import BOUNDARIES_DIR, DIST_DIR
from core.map_builder import MapConfig, MarkerItem, export_shared_boundaries, load_itl_hierarchy

FOOTBALL_DIST = DIST_DIR / "football"
FOOTBALL_BRAND = "English Football Pyramid Maps"

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

TIER_ENTRY_LEVELS: dict[int, str] = dict.fromkeys(range(1, 11), "itl0")

TIER_FLOOR_LEVELS: dict[int, str] = {
    1: "itl1",
    2: "itl1",
    3: "itl1",
    4: "itl2",
    5: "itl2",
    6: "itl2",
    7: "itl3",
    8: "lad",
    9: "lad",
    10: "ward",
}

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

COUNTRY_OUTLINES = ["England", "Isle of Man", "Jersey", "Guernsey"]

_LEVEL_DISPLAY: dict[int, str] = {
    1: "Premier League",
    2: "Championship",
    3: "League One",
    4: "League Two",
    5: "National League",
}


def short_season(season: str) -> str:
    """``2025-2026`` -> ``2025/26``."""
    start, end = season.split("-")
    return f"{start}/{end[2:]}"


def tier_display_name(level: int) -> str:
    return _LEVEL_DISPLAY.get(level, f"Level {level}")


def football_pyramid_band_label(level: int) -> str:
    """Margin tier label on the football pyramid diagram."""
    if 7 <= level <= 11:
        return f"Step {level - 4}"
    if level >= 12:
        return f"Level {level}"
    return tier_display_name(level)


def tier_file_slug(level: int) -> str:
    name = tier_display_name(level)
    return name.replace(" ", "_").replace("&", "and")


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
        f'<p style="margin: 2px 0;"><a href="{escape(team_url)}" target="_blank">Team</a></p>'
        if team_url
        else ""
    )
    league_link = (
        f'<p style="margin: 2px 0;"><a href="{escape(league_url)}" target="_blank">League</a></p>'
        if league_url
        else ""
    )
    return (
        f'<div class="rugby-popup">'
        f'<h4 class="popup-title">{name_esc}</h4>'
        f"<hr>"
        f'<p><span class="popup-label">Division:</span> {league_esc}</p>'
        f'<p><span class="popup-label">Ground:</span> {address_esc}</p>'
        f"__ITL_REGIONS__"
        f"{team_link}{league_link}"
        f"</div>"
    )


def load_pyramid_items(geocoded_dir: Path) -> list[MarkerItem]:
    """Load MarkerItems from ``geocoded_teams/<season>/pyramid/*.json``."""
    if not geocoded_dir.is_dir():
        return []

    items: list[MarkerItem] = []
    for filepath in sorted(geocoded_dir.glob("*.json")):
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        league_name = data.get("league_name", filepath.stem.replace("_", " "))
        league_url = data.get("league_url", "")

        for team in data.get("teams", []):
            if "latitude" not in team or "longitude" not in team:
                continue
            level = int(team.get("level", 0))
            if level < 1:
                continue
            items.append(
                MarkerItem(
                    name=team["name"],
                    latitude=team["latitude"],
                    longitude=team["longitude"],
                    group=league_name,
                    tier=tier_display_name(level),
                    tier_num=level,
                    icon_url=team.get("image_url"),
                    popup_html=_render_popup_html(
                        team["name"],
                        league_name,
                        league_url,
                        team.get("url", ""),
                        team.get("formatted_address") or team.get("address") or "",
                    ),
                )
            )
    return items


def group_by_tier(items: list[MarkerItem]) -> tuple[dict[str, list[MarkerItem]], list[str]]:
    by_tier: dict[str, list[MarkerItem]] = {}
    for item in items:
        by_tier.setdefault(item.tier, []).append(item)
    order = sorted(by_tier, key=lambda name: by_tier[name][0].tier_num)
    return by_tier, order


def rotated_palette(tier_num: int) -> list[str]:
    n = (len(COLOR_PALETTE) + tier_num - 1) % len(COLOR_PALETTE)
    return COLOR_PALETTE[n:] + COLOR_PALETTE[:n]


def output_path(output_dir: Path, name: str, *, production: bool) -> Path:
    if production:
        return output_dir / name / "index.html"
    return output_dir / f"{name}.html"


def tier_sibling_links(
    tier_order: list[str], items_by_tier: dict[str, list[MarkerItem]], *, production: bool
) -> list[tuple[str, str]]:
    """Build (display name, href) pairs for the level dropdown in map chrome."""
    links: list[tuple[str, str]] = []
    for tier_name in tier_order:
        tier_num = items_by_tier[tier_name][0].tier_num
        slug = tier_file_slug(tier_num)
        href = f"../{slug}/" if production else f"{slug}.html"
        links.append((tier_name, href))
    return links


def header_bar_html(
    season: str,
    title: str,
    *,
    production: bool,
    sibling_tiers: list[tuple[str, str]] | None = None,
    current_tier: str | None = None,
) -> str:
    """Fixed map chrome: Football › season › title (or level dropdown), plus appearance."""
    if production:
        home_href = "../../"
        season_href = "../"
    else:
        home_href = "../index.html"
        season_href = "index.html"

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
    <div class="map-header-wrap" id="mapHeaderWrap">
    <div class="map-header" id="mapHeader">
        <a class="map-header__crumb" href="{escape(home_href)}">Football</a>
        <span class="map-header__sep">&rsaquo;</span>
        <a class="map-header__crumb" href="{escape(season_href)}">{season_esc}</a>
        <span class="map-header__sep">&rsaquo;</span>
        {title_html}
        <span class="map-header__theme">
        <label class="map-header__theme-label" for="rugbyMapThemeSelect">Appearance</label>
        <select id="rugbyMapThemeSelect" class="map-header__theme-select"
            aria-label="Map color theme">
            <option value="light">Light</option>
            <option value="system" selected>System</option>
            <option value="dark">Dark</option>
        </select>
        </span>
    </div>
    </div>
    <style>
    .map-header-wrap {{
        position: fixed; top: 0; left: 0; right: 0; z-index: 1000;
        background: rgba(255,255,255,0.92); backdrop-filter: blur(8px);
        border-bottom: 1px solid #e0e0e0;
    }}
    html[data-rugby-effective="dark"] .map-header-wrap {{
        background: rgba(22,33,62,0.92); border-bottom-color: #2a2a4a;
    }}
    .map-header {{
        position: static;
        display: flex; align-items: center; gap: 0.4em;
        padding: 6px 12px;
        border-bottom: none;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 14px;
    }}
    .map-header__crumb {{
        text-decoration: none; color: #0066cc; white-space: nowrap;
    }}
    html[data-rugby-effective="dark"] .map-header__crumb {{
        color: #4da6ff;
    }}
    .map-header__crumb:hover {{ text-decoration: underline; }}
    .map-header__sep {{ color: #999; font-size: 0.9em; }}
    html[data-rugby-effective="dark"] .map-header__sep {{
        color: #666;
    }}
    .map-header__title {{
        font-weight: 600; color: #2c3e50; white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis;
        flex: 1 1 auto; min-width: 0;
    }}
    html[data-rugby-effective="dark"] .map-header__title {{
        color: #e0e8f0;
    }}
    .map-header__select {{
        padding: 3px 8px; border: 1px solid #ccc; border-radius: 4px;
        font-size: 13px; background: white; color: #333;
        max-width: 260px; cursor: pointer;
    }}
    html[data-rugby-effective="dark"] .map-header__select {{
        background: #1e2a45; color: #e0e0e0; border-color: #2a2a4a;
    }}
    .map-header__theme {{
        margin-left: auto;
        display: inline-flex;
        align-items: center;
        gap: 0.35em;
        flex-shrink: 0;
    }}
    .map-header__theme-label {{
        font-size: 12px;
        font-weight: 500;
        color: #444;
        white-space: nowrap;
    }}
    html[data-rugby-effective="dark"] .map-header__theme-label {{
        color: #aab8d8;
    }}
    .map-header__theme-select {{
        padding: 3px 6px;
        border: 1px solid #ccc;
        border-radius: 4px;
        font-size: 12px;
        background: #fff;
        color: #333;
        cursor: pointer;
        max-width: 118px;
    }}
    html[data-rugby-effective="dark"] .map-header__theme-select {{
        background: #1e2a45;
        color: #e0e0e0;
        border-color: #2a2a4a;
    }}
    .leaflet-top {{
        top: var(--rugby-map-chrome-top, 56px) !important;
    }}
    @media (max-width: 480px) {{
        .map-header {{ font-size: 12px; }}
        .map-header__select {{ max-width: 140px; font-size: 11px; }}
        .map-header__theme-label {{ display: none; }}
        .map-header__theme-select {{ max-width: 100px; font-size: 11px; }}
    }}
    </style>
    <script>
    (function () {{
        function syncRugbyMapChromeTop() {{
            var el = document.getElementById("mapHeaderWrap");
            var px = el && el.offsetHeight ? String(el.offsetHeight) + "px" : "56px";
            document.documentElement.style.setProperty("--rugby-map-chrome-top", px);
        }}
        syncRugbyMapChromeTop();
        window.addEventListener("resize", syncRugbyMapChromeTop);
        var wrap = document.getElementById("mapHeaderWrap");
        if (wrap && window.ResizeObserver) {{
            new ResizeObserver(syncRugbyMapChromeTop).observe(wrap);
        }}
    }})();
    </script>
    """


def build_map_config(
    title: str,
    season: str,
    *,
    show_debug: bool,
    palette: list[str] | None = None,
    production: bool = False,
    sibling_tiers: list[tuple[str, str]] | None = None,
    current_tier: str | None = None,
) -> MapConfig:
    season_short = short_season(season)
    html_title = f"{title} | {season_short} | {FOOTBALL_BRAND}"
    meta_desc = (
        f"{title}, {season_short}: interactive map of English football clubs by division "
        f"with league territory shading."
    )
    shared_path = "/shared" if production else "../../shared"
    header_elements = [
        get_favicon_html(depth=2),
        f'<meta name="description" content="{escape(meta_desc)}">',
        f'<meta property="og:title" content="{escape(html_title)}" />',
        f'<meta property="og:description" content="{escape(meta_desc)}" />',
        '<meta property="og:type" content="website" />',
        get_google_analytics_script(),
    ]
    if production:
        header_elements.append(get_service_worker_registration_script())
    return MapConfig(
        title=f"{season_short} {title}",
        html_title=html_title,
        center=(52.5, -1.5),
        zoom=7,
        show_debug=show_debug,
        tier_entry_level=TIER_ENTRY_LEVELS,
        default_tier_entry_level="itl0",
        tier_floor_level=TIER_FLOOR_LEVELS,
        default_tier_floor_level="itl3",
        use_inline_boundaries=not production,
        shared_boundaries_path=shared_path,
        fallback_icon_url=None,
        color_palette=palette or COLOR_PALETTE,
        header_elements=header_elements,
        body_elements=[
            header_bar_html(
                season,
                title,
                production=production,
                sibling_tiers=sibling_tiers,
                current_tier=current_tier,
            )
        ],
    )


def prepare_map_context(*, production: bool, show_debug: bool = True):
    """Load boundaries, export shared bundle, return ITL hierarchy."""
    set_config(is_production=production, show_debug=show_debug)
    itl_hierarchy = load_itl_hierarchy(BOUNDARY_PATHS)
    export_shared_boundaries(
        BOUNDARY_PATHS,
        output_dir=str(DIST_DIR / "shared"),
        country_names=COUNTRY_OUTLINES,
        skip_if_exists=production,
        itl_hierarchy=itl_hierarchy,
    )
    return itl_hierarchy


def dist_season_dir(season: str, *, production: bool) -> Path:
    if production:
        return FOOTBALL_DIST / season
    return Path(__file__).parent / season
