"""Season-switcher header variants for map chrome prototypes."""

from __future__ import annotations

from html import escape

from site_data import adjacent_seasons, href_for, page_def, season_nav_targets, tier_nav_for


def _theme_block() -> str:
    return """
        <span class="map-header__theme">
        <label class="map-header__theme-label" for="rugbyMapThemeSelect">Appearance</label>
        <select id="rugbyMapThemeSelect" class="map-header__theme-select" aria-label="Map color theme">
            <option value="light">Light</option>
            <option value="system" selected>System</option>
            <option value="dark">Dark</option>
        </select>
        </span>"""


def _tier_select(current_slug: str, season: str, *, depth: int) -> str:
    options = []
    page = page_def(season, current_slug)
    fallback_title = page.title if page else current_slug.replace("_", " ")
    for label, slug in tier_nav_for(season):
        href = href_for(season, slug, depth=depth)
        if not href:
            continue
        selected = " selected" if slug == current_slug else ""
        options.append(f'<option value="{escape(href)}"{selected}>{escape(label)}</option>')
    if len(options) <= 1:
        return f'<span class="map-header__title">{escape(fallback_title)}</span>'
    return (
        f'<select class="map-header__select" '
        f'onchange="if(this.value)window.location.href=this.value">'
        f"{''.join(options)}</select>"
    )


def _title_block(page_slug: str, season: str, *, depth: int, fixed_title: str | None) -> str:
    if fixed_title:
        return f'<span class="map-header__title">{escape(fixed_title)}</span>'
    if page_slug:
        return _tier_select(page_slug, season, depth=depth)
    return f'<span class="map-header__title">{escape(season)} maps</span>'


def header_select(
    season: str,
    *,
    depth: int,
    page_slug: str = "",
    fixed_title: str | None = None,
) -> str:
    targets = season_nav_targets(season, page_slug, depth=depth)
    options = []
    for s, href, note in targets:
        if not href:
            continue
        selected = " selected" if s == season else ""
        disabled = ""
        if s != season and note.startswith("no equivalent"):
            # Still navigable via season hub fallback — mark in label.
            label = f"{s} (hub)"
        else:
            label = s
        options.append(
            f'<option value="{escape(href)}"{selected}{disabled}>{escape(label)}</option>'
        )
    season_html = (
        f'<select class="map-header__select map-header__season-select" '
        f'aria-label="Switch season" '
        f'onchange="if(this.value)window.location.href=this.value">'
        f"{''.join(options)}</select>"
    )
    return _wrap_header(season, depth, season_html, page_slug, fixed_title)


def header_split(
    season: str,
    *,
    depth: int,
    page_slug: str = "",
    fixed_title: str | None = None,
) -> str:
    hub = href_for(season, "", depth=depth) or "#"
    page = page_def(season, page_slug) if page_slug else None
    page_label = page.title if page else page_slug.replace("_", " ")
    items = []
    for s, href, note in season_nav_targets(season, page_slug, depth=depth):
        if s == season:
            items.append(
                f'<span class="map-header__menu-item map-header__menu-item--current" '
                f'aria-current="true">{escape(s)}</span>'
            )
            continue
        if note.startswith("no equivalent"):
            title = f"No {page_label} map for {s}"
            items.append(
                f'<span class="map-header__menu-item map-header__menu-item--disabled" '
                f'title="{escape(title)}" aria-disabled="true">{escape(s)}</span>'
            )
            continue
        if not href:
            continue
        items.append(f'<a class="map-header__menu-item" href="{escape(href)}">{escape(s)}</a>')

    season_html = f"""
        <span class="map-header__split-season">
          <a class="map-header__split-label" href="{escape(hub)}">{escape(season)}</a>
          <details class="map-header__split-menu">
            <summary class="map-header__split-trigger" aria-label="Switch season">
              <span class="map-header__split-trigger-icon" aria-hidden="true"></span>
            </summary>
            <div class="map-header__menu">{''.join(items)}</div>
          </details>
        </span>"""
    return _wrap_header(season, depth, season_html, page_slug, fixed_title)


def header_arrows(
    season: str,
    *,
    depth: int,
    page_slug: str = "",
    fixed_title: str | None = None,
) -> str:
    prev_s, next_s = adjacent_seasons(season, page_slug)
    hub = href_for(season, "", depth=depth) or "#"

    def arrow(label: str, target_season: str | None, direction: str) -> str:
        if not target_season:
            return f'<span class="map-header__arrow map-header__arrow--disabled" aria-hidden="true">{label}</span>'
        href = href_for(target_season, page_slug, depth=depth) or href_for(
            target_season, "", depth=depth
        )
        title = f"{direction} season: {target_season}"
        return (
            f'<a class="map-header__arrow" href="{escape(href or "#")}" '
            f'title="{escape(title)}" aria-label="{escape(title)}">{label}</a>'
        )

    season_html = f"""
        <span class="map-header__season-arrows">
          {arrow("‹", prev_s, "Previous")}
          <a class="map-header__crumb map-header__season-current" href="{escape(hub)}">{escape(season)}</a>
          {arrow("›", next_s, "Next")}
        </span>"""
    return _wrap_header(season, depth, season_html, page_slug, fixed_title)


def header_menu(
    season: str,
    *,
    depth: int,
    page_slug: str = "",
    fixed_title: str | None = None,
) -> str:
    hub = href_for(season, "", depth=depth) or "#"
    items = [
        f'<a class="map-header__menu-item map-header__menu-item--hub" href="{escape(hub)}">'
        f"{escape(season)} overview</a>"
    ]
    for s, href, note in season_nav_targets(season, page_slug, depth=depth):
        if s == season:
            continue
        if not href:
            continue
        if note.startswith("no equivalent"):
            text = f"{s} — no {page_slug or 'page'}, open hub"
            cls = "map-header__menu-item map-header__menu-item--fallback"
        else:
            text = s
            cls = "map-header__menu-item"
        items.append(f'<a class="{cls}" href="{escape(href)}">{escape(text)}</a>')

    season_html = f"""
        <details class="map-header__season-details">
          <summary class="map-header__crumb map-header__season-summary">{escape(season)} ▾</summary>
          <div class="map-header__menu">{''.join(items)}</div>
        </details>"""
    return _wrap_header(season, depth, season_html, page_slug, fixed_title)


def _wrap_header(
    season: str,
    depth: int,
    season_html: str,
    page_slug: str,
    fixed_title: str | None,
) -> str:
    home_href = "../" * (depth + 1) + "index.html"
    title_html = _title_block(page_slug, season, depth=depth, fixed_title=fixed_title)
    sep_after_season = '<span class="map-header__sep">&rsaquo;</span>'

    return f"""
    <div class="map-header-wrap" id="mapHeaderWrap">
    <div class="map-header" id="mapHeader">
        <a class="map-header__crumb" href="{escape(home_href)}">Home</a>
        <span class="map-header__sep">&rsaquo;</span>
        {season_html}
        {sep_after_season}
        {title_html}
        {_theme_block()}
    </div>
    </div>"""


HEADER_STYLES = """
    .map-header-wrap {
        position: fixed; top: 0; left: 0; right: 0; z-index: 1000;
        background: rgba(255,255,255,0.92); backdrop-filter: blur(8px);
        border-bottom: 1px solid #e0e0e0;
    }
    html[data-rugby-effective="dark"] .map-header-wrap {
        background: rgba(22,33,62,0.92); border-bottom-color: #2a2a4a;
    }
    .map-header {
        display: flex; align-items: center; gap: 0.4em;
        padding: 6px 12px;
        font-family: 'Barlow', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 14px;
    }
    .map-header__crumb {
        text-decoration: none; color: #0066cc; white-space: nowrap;
    }
    html[data-rugby-effective="dark"] .map-header__crumb { color: #4da6ff; }
    .map-header__crumb:hover { text-decoration: underline; }
    .map-header__sep { color: #999; font-size: 0.9em; }
    html[data-rugby-effective="dark"] .map-header__sep { color: #666; }
    .map-header__title {
        font-family: 'Oswald', sans-serif;
        font-weight: 600; color: #2c3e50; white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis;
        flex: 1 1 auto; min-width: 0;
    }
    html[data-rugby-effective="dark"] .map-header__title { color: #e0e8f0; }
    .map-header__select {
        padding: 3px 8px; border: 1px solid #ccc; border-radius: 4px;
        font-size: 13px; background: white; color: #333;
        max-width: 260px; cursor: pointer;
    }
    .map-header__season-select { max-width: 130px; }
    html[data-rugby-effective="dark"] .map-header__select {
        background: #1e2a45; color: #e0e0e0; border-color: #2a2a4a;
    }
    .map-header__theme {
        margin-left: auto;
        display: inline-flex; align-items: center; gap: 0.35em; flex-shrink: 0;
    }
    .map-header__theme-label { font-size: 12px; color: #444; }
    html[data-rugby-effective="dark"] .map-header__theme-label { color: #aab8d8; }
    .map-header__theme-select {
        padding: 3px 6px; border: 1px solid #ccc; border-radius: 4px;
        font-size: 12px; max-width: 118px;
    }
    /* split-button — label links to hub; trigger matches tier-select height */
    .map-header__split-season {
        display: inline-flex; align-items: stretch;
        border: 1px solid #ccc; border-radius: 4px;
        background: #fff; overflow: visible;
    }
    html[data-rugby-effective="dark"] .map-header__split-season {
        background: #1e2a45; border-color: #2a2a4a;
    }
    .map-header__split-label {
        display: inline-flex; align-items: center;
        padding: 3px 10px;
        text-decoration: none; color: #0066cc; white-space: nowrap;
        font-size: 13px; line-height: 1.3;
        border-right: 1px solid #ccc;
    }
    html[data-rugby-effective="dark"] .map-header__split-label {
        color: #4da6ff; border-right-color: #2a2a4a;
    }
    .map-header__split-label:hover { background: #f0f4ff; text-decoration: none; }
    html[data-rugby-effective="dark"] .map-header__split-label:hover { background: #2a3a5a; }
    .map-header__split-menu { position: relative; display: inline-flex; align-items: stretch; }
    .map-header__split-trigger {
        list-style: none; cursor: pointer;
        display: inline-flex; align-items: center; justify-content: center;
        min-width: 2.25rem; padding: 3px 10px;
        user-select: none; color: #333; background: #f7f7f7;
        border: none; border-radius: 0 3px 3px 0;
        line-height: 1;
    }
    html[data-rugby-effective="dark"] .map-header__split-trigger {
        color: #e0e0e0; background: #253352;
    }
    .map-header__split-trigger:hover { background: #ececec; }
    html[data-rugby-effective="dark"] .map-header__split-trigger:hover { background: #2f4068; }
    .map-header__split-menu[open] .map-header__split-trigger {
        background: #e8eef8; color: #004499;
    }
    html[data-rugby-effective="dark"] .map-header__split-menu[open] .map-header__split-trigger {
        background: #354878; color: #fff;
    }
    .map-header__split-trigger::-webkit-details-marker { display: none; }
    .map-header__split-trigger-icon {
        display: block; width: 0; height: 0;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 6px solid currentColor;
    }
    .map-header__split-menu .map-header__menu {
        left: auto; right: 0; min-width: 12.5rem;
    }
    /* arrows */
    .map-header__season-arrows { display: inline-flex; align-items: center; gap: 0.15em; }
    .map-header__arrow {
        text-decoration: none; color: #0066cc; font-size: 1.1em;
        padding: 0 0.15em; line-height: 1;
    }
    .map-header__arrow--disabled { color: #bbb; cursor: default; }
    .map-header__season-current { font-weight: 600; }
    /* shared menu panel */
    .map-header__menu, .map-header__season-details .map-header__menu {
        position: absolute; top: calc(100% + 4px); left: 0;
        min-width: 11rem; max-height: 16rem; overflow-y: auto;
        background: #fff; border: 1px solid #ccc; border-radius: 6px;
        box-shadow: 0 4px 14px rgba(0,0,0,0.12); z-index: 1100;
        padding: 0.35rem 0;
    }
    html[data-rugby-effective="dark"] .map-header__menu {
        background: #1e2a45; border-color: #2a2a4a;
    }
    .map-header__menu-item {
        display: block; padding: 0.35rem 0.75rem;
        text-decoration: none; color: #0066cc; font-size: 13px; white-space: nowrap;
    }
    .map-header__menu-item:hover { background: #f0f4ff; text-decoration: none; }
    html[data-rugby-effective="dark"] .map-header__menu-item { color: #4da6ff; }
    html[data-rugby-effective="dark"] .map-header__menu-item:hover { background: #2a3a5a; }
    .map-header__menu-item--hub { font-weight: 600; border-bottom: 1px solid #eee; margin-bottom: 0.2rem; }
    .map-header__menu-item--fallback { color: #888; font-style: italic; }
    .map-header__menu-item--current {
        font-weight: 600; color: #333; cursor: default; pointer-events: none;
    }
    html[data-rugby-effective="dark"] .map-header__menu-item--current { color: #e0e8f0; }
    .map-header__menu-item--disabled {
        color: #aaa; cursor: not-allowed; pointer-events: none;
        font-style: italic; opacity: 0.75;
    }
    html[data-rugby-effective="dark"] .map-header__menu-item--disabled { color: #667; }
    .map-header__season-details { position: relative; }
    .map-header__season-summary { cursor: pointer; list-style: none; }
    .map-header__season-summary::-webkit-details-marker { display: none; }
    .proto-badge {
        position: fixed; bottom: 12px; right: 12px; z-index: 1001;
        background: #2c3e50; color: #fff; font-size: 11px;
        padding: 6px 10px; border-radius: 6px; opacity: 0.9;
    }
    .proto-map-placeholder {
        margin-top: 52px; min-height: calc(100vh - 52px);
        background: linear-gradient(160deg, #e8f0e8 0%, #d4e4f7 45%, #f5f0e6 100%);
        display: flex; align-items: center; justify-content: center;
        color: #555; font-family: 'Barlow', sans-serif; font-size: 15px;
    }
    html[data-rugby-effective="dark"] .proto-map-placeholder {
        background: linear-gradient(160deg, #1a2744 0%, #16213e 50%, #1f1a2e 100%);
        color: #aab8d8;
    }
    .proto-hub {
        max-width: 640px; margin: 4rem auto 2rem; padding: 0 1.25rem;
        font-family: 'Barlow', sans-serif;
    }
    .proto-hub h1 { font-family: 'Oswald', sans-serif; }
    .proto-hub ul { line-height: 1.8; }
    @media (max-width: 480px) {
        .map-header { font-size: 12px; }
        .map-header__select { max-width: 120px; font-size: 11px; }
        .map-header__split-label { font-size: 11px; padding: 3px 8px; }
        .map-header__split-trigger { min-width: 2rem; padding: 3px 8px; }
        .map-header__theme-label { display: none; }
    }
"""

HEADER_JS = """
    (function () {
        function syncRugbyMapChromeTop() {
            var el = document.getElementById("mapHeaderWrap");
            var px = el && el.offsetHeight ? String(el.offsetHeight) + "px" : "56px";
            document.documentElement.style.setProperty("--rugby-map-chrome-top", px);
        }
        syncRugbyMapChromeTop();
        window.addEventListener("resize", syncRugbyMapChromeTop);
        document.querySelectorAll(".map-header__season-details, .map-header__split-menu")
            .forEach(function (d) {
                d.addEventListener("toggle", function () {
                    if (!d.open) return;
                    document.querySelectorAll(".map-header__season-details, .map-header__split-menu")
                        .forEach(function (other) { if (other !== d) other.open = false; });
                });
            });
    })();
"""

RENDERERS = {
    "select": header_select,
    "split": header_split,
    "arrows": header_arrows,
    "menu": header_menu,
}
