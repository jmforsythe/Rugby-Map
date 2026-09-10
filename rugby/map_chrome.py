"""Shared map page chrome: breadcrumb header with split-button season navigation."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from core import get_config
from core.config import DIST_DIR

_SEASON_DIR = re.compile(r"^\d{4}-\d{4}$")


def list_nav_seasons() -> list[str]:
    """Season slugs under ``dist/`` that have been built, newest first."""
    if not DIST_DIR.is_dir():
        return []
    seasons = [
        item.name for item in DIST_DIR.iterdir() if item.is_dir() and _SEASON_DIR.match(item.name)
    ]
    return sorted(seasons, reverse=True)


def page_rel_from_output(output_file: Path, season: str) -> str:
    """Return the map path within a season folder (no ``.html`` suffix)."""
    season_dir = (DIST_DIR / season).resolve()
    rel = output_file.resolve().relative_to(season_dir)
    if rel.name == "index.html":
        parent = rel.parent.as_posix()
        return "" if parent == "." else parent
    return rel.with_suffix("").as_posix()


def season_page_path(season: str, page_rel: str, *, is_prod: bool) -> Path:
    """Filesystem path for a map page within a season."""
    base = DIST_DIR / season
    if not page_rel:
        return base / "index.html"
    if is_prod:
        return base / page_rel / "index.html"
    # Dev: tier maps are flat ``.html``; fixtures and nested merit use ``index.html``.
    if page_rel == "fixtures" or "/" in page_rel:
        return base / page_rel / "index.html"
    return base / f"{page_rel}.html"


def season_switch_href(
    output_file: Path,
    target_season: str,
    page_rel: str,
    *,
    is_prod: bool,
) -> str:
    """Relative href from ``output_file`` to the same map in ``target_season``."""
    depth = len(output_file.resolve().parent.relative_to(DIST_DIR.resolve()).parts)
    up = "../" * depth
    if not page_rel:
        return f"{up}{target_season}/" if is_prod else f"{up}{target_season}/index.html"
    if is_prod:
        return f"{up}{target_season}/{page_rel}/"
    if page_rel == "fixtures" or "/" in page_rel:
        return f"{up}{target_season}/{page_rel}/index.html"
    return f"{up}{target_season}/{page_rel}.html"


def _season_hub_href(
    output_file: Path | None, season: str, subdirectory_depth: int, *, is_prod: bool
) -> str:
    if output_file is not None:
        depth = len(output_file.resolve().parent.relative_to(DIST_DIR.resolve()).parts)
        up = "../" * (depth - 1)
    else:
        up = "../" * (1 + subdirectory_depth) if is_prod else "../" * subdirectory_depth
    return up if is_prod else f"{up}index.html"


def _home_href(
    output_file: Path | None, subdirectory_depth: int, *, is_prod: bool, season: str | None
) -> str:
    if season is None:
        root_depth = "../" * (1 + subdirectory_depth)
        return root_depth if is_prod else root_depth + "index.html"
    if output_file is not None:
        depth = len(output_file.resolve().parent.relative_to(DIST_DIR.resolve()).parts)
        up = "../" * depth
        return up if is_prod else f"{up}index.html"
    up = "../" * (1 + subdirectory_depth)
    return up if is_prod else f"{up}index.html"


def season_menu_html(
    season: str,
    page_rel: str,
    page_label: str,
    output_file: Path,
    *,
    is_prod: bool,
) -> str:
    items: list[str] = []
    for target in list_nav_seasons():
        if target == season:
            items.append(
                f'<span class="map-header__menu-item map-header__menu-item--current" '
                f'aria-current="true">{escape(target)}</span>'
            )
            continue
        if season_page_path(target, page_rel, is_prod=is_prod).is_file():
            href = season_switch_href(output_file, target, page_rel, is_prod=is_prod)
            items.append(
                f'<a class="map-header__menu-item" href="{escape(href)}">{escape(target)}</a>'
            )
        else:
            title = f"No {page_label} map for {target}"
            items.append(
                f'<span class="map-header__menu-item map-header__menu-item--disabled" '
                f'title="{escape(title)}" aria-disabled="true">{escape(target)}</span>'
            )
    return "".join(items)


def _season_split_html(
    season: str,
    page_rel: str,
    page_label: str,
    output_file: Path,
    subdirectory_depth: int,
    *,
    is_prod: bool,
) -> str:
    hub = _season_hub_href(output_file, season, subdirectory_depth, is_prod=is_prod)
    menu = season_menu_html(season, page_rel, page_label, output_file, is_prod=is_prod)
    return f"""
        <span class="map-header__split-season">
          <a class="map-header__split-label" href="{escape(hub)}">{escape(season)}</a>
          <details class="map-header__split-menu">
            <summary class="map-header__split-trigger" aria-label="Switch season">
              <span class="map-header__split-trigger-icon" aria-hidden="true"></span>
            </summary>
            <div class="map-header__menu">{menu}</div>
          </details>
        </span>
        <span class="map-header__sep">&rsaquo;</span>
        """


def header_bar_html(
    season: str | None,
    title: str,
    subdirectory_depth: int = 0,
    sibling_tiers: list[tuple[str, str]] | None = None,
    current_tier: str | None = None,
    output_file: Path | None = None,
) -> str:
    """Fixed map chrome: Home › season › title (or tier dropdown), plus appearance.

    ``season=None`` renders a standalone top-level page (Home › title only,
    no season crumb) — for maps that aren't scoped to a season, e.g. the
    Constituent Body map.

    When ``output_file`` is set for a season-scoped page, the season crumb
    becomes a split control: label links to the season hub, chevron opens a
    menu of other seasons for the same map (disabled when that page does not
    exist).
    """
    is_prod = get_config().is_production
    home_href = _home_href(output_file, subdirectory_depth, is_prod=is_prod, season=season)

    if sibling_tiers and len(sibling_tiers) > 1:
        options = []
        for tier_display, tier_href in sibling_tiers:
            if not tier_href:
                options.append(f'<option value="" disabled>{escape(tier_display)}</option>')
                continue
            selected = " selected" if tier_display == current_tier else ""
            options.append(
                f'<option value="{escape(tier_href)}"{selected}>{escape(tier_display)}</option>'
            )
        title_html = (
            f'<select class="map-header__select" '
            f'onchange="if(this.value)window.location.href=this.value">'
            f"{''.join(options)}</select>"
        )
    else:
        title_html = f'<span class="map-header__title">{escape(title)}</span>'

    season_crumb = ""
    if season is not None:
        if output_file is not None:
            page_rel = page_rel_from_output(output_file, season)
            season_crumb = _season_split_html(
                season,
                page_rel,
                title,
                output_file,
                subdirectory_depth,
                is_prod=is_prod,
            )
        else:
            season_href = _season_hub_href(output_file, season, subdirectory_depth, is_prod=is_prod)
            season_crumb = (
                f'<a class="map-header__crumb" href="{escape(season_href)}">{escape(season)}</a>\n'
                f'        <span class="map-header__sep">&rsaquo;</span>\n        '
            )

    return f"""
    <div class="map-header-wrap" id="mapHeaderWrap">
    <div class="map-header" id="mapHeader">
        <a class="map-header__crumb" href="{escape(home_href)}">Home</a>
        <span class="map-header__sep">&rsaquo;</span>
        {season_crumb}{title_html}
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
    html[data-rugby-effective="dark"] .map-header__split-season {{
        background: #1e2a45; border-color: #2a2a4a;
    }}
    html[data-rugby-effective="dark"] .map-header-wrap {{
        background: rgba(22,33,62,0.92); border-bottom-color: #2a2a4a;
    }}
    .map-header {{
        position: static;
        display: flex; align-items: center; gap: 0.4em;
        padding: 6px 12px;
        border-bottom: none;
        font-family: 'Barlow', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
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
        font-family: 'Oswald', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-weight: 600; letter-spacing: 0.01em; color: #2c3e50; white-space: nowrap;
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
    .map-header__split-season {{
        display: inline-flex; align-items: stretch;
        border: 1px solid #ccc; border-radius: 4px;
        background: #fff; overflow: visible;
    }}
    .map-header__split-label {{
        display: inline-flex; align-items: center;
        padding: 3px 10px;
        text-decoration: none; color: #0066cc; white-space: nowrap;
        font-size: 13px; line-height: 1.3;
        border-right: 1px solid #ccc;
    }}
    html[data-rugby-effective="dark"] .map-header__split-label {{
        color: #4da6ff; border-right-color: #2a2a4a;
    }}
    .map-header__split-label:hover {{ background: #f0f4ff; text-decoration: none; }}
    html[data-rugby-effective="dark"] .map-header__split-label:hover {{ background: #2a3a5a; }}
    .map-header__split-menu {{ position: relative; display: inline-flex; align-items: stretch; }}
    .map-header__split-trigger {{
        list-style: none; cursor: pointer;
        display: inline-flex; align-items: center; justify-content: center;
        min-width: 2.25rem; padding: 3px 10px;
        user-select: none; color: #333; background: #f7f7f7;
        border: none; border-radius: 0 3px 3px 0;
        line-height: 1;
    }}
    html[data-rugby-effective="dark"] .map-header__split-trigger {{
        color: #e0e0e0; background: #253352;
    }}
    .map-header__split-trigger:hover {{ background: #ececec; }}
    html[data-rugby-effective="dark"] .map-header__split-trigger:hover {{ background: #2f4068; }}
    .map-header__split-menu[open] .map-header__split-trigger {{
        background: #e8eef8; color: #004499;
    }}
    html[data-rugby-effective="dark"] .map-header__split-menu[open] .map-header__split-trigger {{
        background: #354878; color: #fff;
    }}
    .map-header__split-trigger::-webkit-details-marker {{ display: none; }}
    .map-header__split-trigger-icon {{
        display: block; width: 0; height: 0;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 6px solid currentColor;
    }}
    .map-header__split-menu .map-header__menu {{
        left: auto; right: 0; min-width: 12.5rem;
    }}
    .map-header__menu {{
        position: absolute; top: calc(100% + 4px); left: 0;
        min-width: 11rem; max-height: 16rem; overflow-y: auto;
        background: #fff; border: 1px solid #ccc; border-radius: 6px;
        box-shadow: 0 4px 14px rgba(0,0,0,0.12); z-index: 1100;
        padding: 0.35rem 0;
    }}
    html[data-rugby-effective="dark"] .map-header__menu {{
        background: #1e2a45; border-color: #2a2a4a;
    }}
    .map-header__menu-item {{
        display: block; padding: 0.35rem 0.75rem;
        text-decoration: none; color: #0066cc; font-size: 13px; white-space: nowrap;
    }}
    .map-header__menu-item:hover {{ background: #f0f4ff; text-decoration: none; }}
    html[data-rugby-effective="dark"] .map-header__menu-item {{ color: #4da6ff; }}
    html[data-rugby-effective="dark"] .map-header__menu-item:hover {{ background: #2a3a5a; }}
    .map-header__menu-item--current {{
        font-weight: 600; color: #333; cursor: default; pointer-events: none;
    }}
    html[data-rugby-effective="dark"] .map-header__menu-item--current {{ color: #e0e8f0; }}
    .map-header__menu-item--disabled {{
        color: #aaa; cursor: not-allowed; pointer-events: none;
        font-style: italic; opacity: 0.75;
    }}
    html[data-rugby-effective="dark"] .map-header__menu-item--disabled {{ color: #667; }}
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
        .map-header__split-label {{ font-size: 11px; padding: 3px 8px; }}
        .map-header__split-trigger {{ min-width: 2rem; padding: 3px 8px; }}
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
        document.querySelectorAll(".map-header__split-menu").forEach(function (d) {{
            d.addEventListener("toggle", function () {{
                if (!d.open) return;
                document.querySelectorAll(".map-header__split-menu").forEach(function (other) {{
                    if (other !== d) other.open = false;
                }});
            }});
        }});
    }})();
    </script>
    """
