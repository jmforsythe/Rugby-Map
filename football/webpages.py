"""
Generate index pages for English football pyramid maps.

Creates:
  dist/football/index.html
  dist/football/<season>/index.html
"""

from __future__ import annotations

import argparse
import re
from html import escape
from pathlib import Path

from core import get_favicon_html, get_google_analytics_script, set_config
from football.map_common import (
    FOOTBALL_BRAND,
    FOOTBALL_DIST,
    short_season,
    tier_display_name,
    tier_file_slug,
)
from rugby.webpages import _build_pyramid_diagram_stack_html, _detect_pyramid_diagram_pair

_SEASON_RE = re.compile(r"^\d{4}-\d{4}$")


def _link(name: str, *, production: bool) -> str:
    if production:
        return f"{name}/"
    return f"{name}.html"


def detect_levels(season_dir: Path, *, production: bool) -> list[tuple[str, str]]:
    """Return (display name, href) pairs for available level maps."""
    found: list[tuple[int, str, str]] = []
    for level in range(1, 11):
        slug = tier_file_slug(level)
        href = _link(slug, production=production)
        if production:
            exists = (season_dir / slug / "index.html").is_file()
        else:
            exists = (season_dir / f"{slug}.html").is_file()
        if exists:
            found.append((level, tier_display_name(level), href))
    return [(name, href) for _, name, href in found]


def season_index_html(
    season: str, levels: list[tuple[str, str]], *, production: bool, season_dir: Path
) -> str:
    season_short = short_season(season)
    home_href = "../index.html" if production else "../index.html"
    styles_href = "../../styles.css" if production else "../../styles.css"

    level_items = "\n".join(
        f'        <li><a href="{escape(href)}">{escape(name)}</a></li>' for name, href in levels
    )
    all_tiers_href = _link("All_Tiers", production=production)

    pyramid_block = ""
    pyramid_diagrams = _detect_pyramid_diagram_pair(season_dir, "pyramid")
    if pyramid_diagrams.get("full_href") or pyramid_diagrams.get("thumb_src"):
        pyramid_stack = _build_pyramid_diagram_stack_html(
            pyramid_diagrams,
            alt_base=f"English football pyramid for {season_short}: club crests by level",
            aria_base=f"Open full football pyramid diagram for {season_short}",
        )
        if pyramid_stack:
            pyramid_block = f"""
    <h2>Pyramid diagram</h2>
{pyramid_stack}"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="English football pyramid maps for {escape(season_short)} — levels 1–10 with interactive territory maps.">
    <title>{escape(season_short)} | {escape(FOOTBALL_BRAND)}</title>
    <link rel="stylesheet" href="{styles_href}">
    {get_favicon_html(depth=2)}
    {get_google_analytics_script()}
</head>
<body>
    <div class="back-link"><a href="{home_href}">← Football home</a></div>
    <h1>{escape(FOOTBALL_BRAND)}</h1>
    <p>Season: {escape(season)}</p>
{pyramid_block}
    <h2>Maps by level</h2>
    <ul>
        <li class="all-tiers"><a href="{all_tiers_href}">All Tiers (combined)</a></li>
    </ul>
    <ul>
{level_items}
    </ul>
</body>
</html>
"""


def top_index_html(seasons: list[str], *, production: bool) -> str:
    season_links = "\n".join(
        f'        <li><a href="{s}/{"index.html" if not production else ""}">{escape(short_season(s))} ({escape(s)})</a></li>'
        for s in sorted(seasons, reverse=True)
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="Interactive maps of the English football pyramid — club locations and league territories by level.">
    <title>{escape(FOOTBALL_BRAND)}</title>
    <link rel="stylesheet" href="../styles.css">
    {get_favicon_html(depth=1)}
    {get_google_analytics_script()}
</head>
<body>
    <h1>{escape(FOOTBALL_BRAND)}</h1>
    <p>Interactive territory maps for the English football league pyramid (levels 1–10).</p>
    <h2>Seasons</h2>
    <ul>
{season_links}
    </ul>
</body>
</html>
"""


def _list_seasons(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(
        (item.name for item in root.iterdir() if item.is_dir() and _SEASON_RE.match(item.name)),
        reverse=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate football map index pages")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--season", default="2025-2026")
    args = parser.parse_args()

    if args.production:
        set_config(is_production=True)

    production = args.production
    seasons_root = FOOTBALL_DIST if production else Path(__file__).parent
    seasons_root.mkdir(parents=True, exist_ok=True)

    seasons = _list_seasons(seasons_root)
    if args.season and (seasons_root / args.season).is_dir() and args.season not in seasons:
        seasons.append(args.season)
        seasons.sort(reverse=True)

    for season in seasons:
        season_dir = seasons_root / season
        levels = detect_levels(season_dir, production=production)
        if not levels:
            print(f"  Skipping {season} — no level maps found")
            continue
        html = season_index_html(season, levels, production=production, season_dir=season_dir)
        index_path = season_dir / "index.html"
        index_path.write_text(html, encoding="utf-8")
        print(f"  Created {index_path} ({len(levels)} levels)")

    if seasons:
        FOOTBALL_DIST.mkdir(parents=True, exist_ok=True)
        top_path = FOOTBALL_DIST / "index.html"
        top_path.write_text(top_index_html(seasons, production=production), encoding="utf-8")
        print(f"  Created {top_path}")


if __name__ == "__main__":
    main()
