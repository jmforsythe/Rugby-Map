"""Shared helpers for football Folium map generation."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from core import get_favicon_html, get_google_analytics_script, set_config
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
        f'<div style="font-family: Arial; width: 220px;">'
        f'<h4 style="margin: 0;">{name_esc}</h4>'
        f'<hr style="margin: 5px 0;">'
        f'<p style="margin: 2px 0;"><b>Division:</b> {league_esc}</p>'
        f'<p style="margin: 2px 0;"><b>Ground:</b> {address_esc}</p>'
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


def build_map_config(
    title: str,
    season: str,
    *,
    show_debug: bool,
    palette: list[str] | None = None,
    production: bool = False,
) -> MapConfig:
    season_short = short_season(season)
    html_title = f"{title} | {season_short} | {FOOTBALL_BRAND}"
    meta_desc = (
        f"{title}, {season_short}: interactive map of English football clubs by division "
        f"with league territory shading."
    )
    shared_path = "/shared" if production else "../../shared"
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
        header_elements=[
            get_favicon_html(depth=1),
            f'<meta name="description" content="{escape(meta_desc)}">',
            f'<meta property="og:title" content="{escape(html_title)}" />',
            f'<meta property="og:description" content="{escape(meta_desc)}" />',
            '<meta property="og:type" content="website" />',
            get_google_analytics_script(),
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
