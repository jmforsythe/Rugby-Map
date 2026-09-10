"""Patch baked-in season navigation after CI merges per-season map tarballs.

Matrix jobs build one ``dist/<season>/`` at a time, so map headers only list that
season. Run at assemble time once every season folder is on disk.
"""

from __future__ import annotations

import argparse
import logging
import re
from html import unescape
from pathlib import Path

from core.config import DIST_DIR, set_config, setup_logging
from rugby.map_chrome import list_nav_seasons, page_rel_from_output, season_menu_html

logger = logging.getLogger(__name__)

_SEASON_DIR = re.compile(r"^\d{4}-\d{4}$")
_MENU_RE = re.compile(
    r'(<div class="map-header__menu">)(.*?)(</div>)',
    re.DOTALL,
)
_TIER_SELECT_RE = re.compile(
    r'<select class="map-header__select"[^>]*>.*?'
    r'<option value="[^"]*" selected>([^<]*)</option>',
    re.DOTALL,
)
_TITLE_RE = re.compile(r'<span class="map-header__title">([^<]*)</span>')


def page_label_from_header_html(html: str) -> str:
    """Map title from tier dropdown or fixed title span (for disabled tooltips)."""
    match = _TIER_SELECT_RE.search(html)
    if match:
        return unescape(match.group(1).strip())
    match = _TITLE_RE.search(html)
    if match:
        return unescape(match.group(1).strip())
    return "Map"


def season_from_output_path(output_file: Path, dist_dir: Path = DIST_DIR) -> str | None:
    try:
        rel = output_file.resolve().relative_to(dist_dir.resolve())
    except ValueError:
        return None
    if rel.parts and _SEASON_DIR.match(rel.parts[0]):
        return rel.parts[0]
    return None


def patch_map_page_html(
    html: str,
    output_file: Path,
    *,
    is_prod: bool,
    dist_dir: Path = DIST_DIR,
) -> str | None:
    """Rebuild the season menu when split-button chrome is present; else None."""
    if "map-header__split-season" not in html:
        return None
    season = season_from_output_path(output_file, dist_dir=dist_dir)
    if season is None:
        return None
    page_rel = page_rel_from_output(output_file, season)
    page_label = page_label_from_header_html(html)
    new_menu = season_menu_html(
        season,
        page_rel,
        page_label,
        output_file,
        is_prod=is_prod,
    )

    def replacer(match: re.Match[str]) -> str:
        return match.group(1) + new_menu + match.group(3)

    patched, count = _MENU_RE.subn(replacer, html, count=1)
    if count != 1:
        logger.warning("Expected one season menu in %s, found %d", output_file, count)
        return None
    return patched


def iter_season_map_pages(dist_dir: Path = DIST_DIR, *, is_prod: bool) -> list[Path]:
    """Map HTML paths under ``dist/<season>/`` (production directory layout)."""
    pages: list[Path] = []
    for season in list_nav_seasons():
        season_dir = dist_dir / season
        if not season_dir.is_dir():
            continue
        if is_prod:
            pages.extend(sorted(season_dir.rglob("index.html")))
        else:
            for path in sorted(season_dir.rglob("*.html")):
                if path.name == "index.html":
                    if path.parent != season_dir:
                        pages.append(path)
                elif path.parent == season_dir:
                    pages.append(path)
            hub = season_dir / "index.html"
            if hub.is_file() and hub not in pages:
                pages.append(hub)
    return pages


def patch_dist_season_nav(
    dist_dir: Path = DIST_DIR,
    *,
    is_prod: bool,
) -> int:
    """Rewrite season menus on every season-scoped map page; return patch count."""
    updated = 0
    for path in iter_season_map_pages(dist_dir, is_prod=is_prod):
        try:
            html = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Skipping unreadable map page %s: %s", path, exc)
            continue
        patched = patch_map_page_html(html, path, is_prod=is_prod, dist_dir=dist_dir)
        if patched is None or patched == html:
            continue
        path.write_text(patched, encoding="utf-8")
        updated += 1
    logger.info(
        "Patched season navigation on %d map page(s) across %d season(s)",
        updated,
        len(list_nav_seasons()),
    )
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild season switcher menus after all dist/<season>/ folders exist.",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="Use production map paths (directory URLs and index.html layout)",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DIST_DIR,
        help="Site output root (default: dist/)",
    )
    args = parser.parse_args()
    setup_logging()
    set_config(is_production=args.production)
    patch_dist_season_nav(args.dist_dir.resolve(), is_prod=args.production)


if __name__ == "__main__":
    main()
