"""
Generate a single interactive map with a date dropdown for all upcoming fixtures.

Reads committed fixture_data/<season>/ and geocoded_teams/<season>/ to show
match venues. A dropdown lets the user switch between match days.
No network access required — runs in the deployed environment.

Usage: ``python -m rugby.match_day --season 2025-2026 --production`` (use ``--production`` for
the deployed static site; omit for local file paths).
"""

from __future__ import annotations

import argparse
import json
import logging
import urllib.parse
from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path

import folium
from folium.plugins import MarkerCluster

from core import (
    Fixture,
    FixtureLeague,
    GeocodedLeague,
    GeocodedTeam,
    get_config,
    get_favicon_html,
    get_google_analytics_script,
    get_service_worker_registration_script,
    set_config,
    setup_logging,
)
from core.config import DIST_DIR
from core.map_builder import DARK_MODE_JS, POPUP_CSS
from rugby import DATA_DIR
from rugby.tiers import extract_tier

logger = logging.getLogger(__name__)

RFU_FALLBACK_ICON = "https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg"


def _match_day_seo_head(season: str) -> str:
    """Return <head> elements for document title, viewport, and Open Graph (match day)."""
    page_title = f"Match Day Fixtures & Results - {season} | English Rugby Union Team Maps"
    desc = (
        f"Browse {season} English rugby union fixtures by match day: "
        "interactive map of match venues, dates, and results."
    )
    url = f"https://rugbyunionmap.uk/{season}/match_day/"
    return f"""    <title>{escape(page_title)}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="description" content="{escape(desc)}" />
    <meta property="og:title" content="{escape(page_title)}" />
    <meta property="og:description" content="{escape(desc)}" />
    <meta property="og:type" content="website" />
    <meta property="og:url" content="{escape(url)}" />
"""


COLOR_PALETTE = [
    "#e6194b",
    "#3cb44b",
    "#0082c8",
    "#f58231",
    "#911eb4",
    "#46f0f0",
    "#f032e6",
    "#6a8f00",
    "#008080",
    "#aa6e28",
    "#800000",
    "#008f5a",
    "#808000",
    "#000080",
    "#808080",
]


# ---------------------------------------------------------------------------
# Team location index
# ---------------------------------------------------------------------------


def _parse_team_id(url: str) -> int | None:
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    team_ids = params.get("team", [])
    if team_ids and team_ids[0].isdigit():
        return int(team_ids[0])
    return None


def build_team_index(season: str) -> dict[int, GeocodedTeam]:
    """Build team-ID -> GeocodedTeam lookup from geocoded_teams/<season>/."""
    geocoded_dir = DATA_DIR / "geocoded_teams" / season
    index: dict[int, GeocodedTeam] = {}
    if not geocoded_dir.exists():
        logger.error("geocoded_teams/%s/ not found", season)
        return index

    for json_file in geocoded_dir.rglob("*.json"):
        with open(json_file, encoding="utf-8") as f:
            data: GeocodedLeague = json.load(f)
        for team in data.get("teams", []):
            team_id = _parse_team_id(team.get("url", ""))
            if team_id is not None and "latitude" in team and "longitude" in team:
                index[team_id] = team

    logger.info("Team index: %d geocoded teams", len(index))
    return index


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def load_all_fixtures(season: str) -> dict[str, list[tuple[Fixture, str, str]]]:
    """Load all fixtures grouped by date. Returns {date: [(fixture, league_name, rel_path)]}."""
    fixture_dir = DATA_DIR / "fixture_data" / season
    if not fixture_dir.exists():
        logger.error("fixture_data/%s/ not found — run scrape_fixtures.py first", season)
        return {}

    by_date: dict[str, list[tuple[Fixture, str, str]]] = defaultdict(list)
    for json_file in sorted(fixture_dir.rglob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            data: FixtureLeague = json.load(f)

        league_name = data["league_name"]
        rel_path = json_file.relative_to(fixture_dir).as_posix()

        for fixture in data.get("fixtures", []):
            by_date[fixture["date"]].append((fixture, league_name, rel_path))

    total = sum(len(v) for v in by_date.values())
    logger.info("Loaded %d fixtures across %d dates", total, len(by_date))
    return by_date


# ---------------------------------------------------------------------------
# Popup HTML
# ---------------------------------------------------------------------------


_STATUS_LABELS: dict[str, str] = {
    "HWO": "Home walkover",
    "AWO": "Away walkover",
}


def _format_centre_display(fixture: Fixture) -> str:
    """Return the HTML for the centre element between crests in the popup.

    Shows the scoreline for completed results, status for walkovers, or
    kick-off time for upcoming fixtures.
    """
    status = fixture.get("status")
    if status:
        label = _STATUS_LABELS.get(status, status)
        return (
            f'<div style="font-size:16px;font-weight:bold;letter-spacing:1px">'
            f"{escape(status)}</div>"
            f'<div style="font-size:10px;color:#888;text-transform:uppercase">{escape(label)}</div>'
        )
    home_score = fixture.get("home_score")
    away_score = fixture.get("away_score")
    if home_score is not None and away_score is not None:
        return (
            f'<div style="font-size:20px;font-weight:bold;letter-spacing:2px">'
            f"{home_score} - {away_score}</div>"
            f'<div style="font-size:10px;color:#888;text-transform:uppercase">Full time</div>'
        )
    time_text = fixture.get("time") or "    "
    return f'<div style="font-size:18px;font-weight:bold">{escape(time_text)}</div>'


def _render_popup(
    fixture: Fixture,
    league_name: str,
    tier_name: str,
    home_team: GeocodedTeam,
    away_team: GeocodedTeam | None,
) -> str:
    home_name = home_team.get("name", "Home")
    home_img = home_team.get("image_url") or RFU_FALLBACK_ICON
    away_name = away_team.get("name", "Away") if away_team else "Away"
    away_img = (away_team.get("image_url") if away_team else None) or RFU_FALLBACK_ICON
    address = home_team.get("formatted_address", home_team.get("address", ""))
    centre_html = _format_centre_display(fixture)

    return (
        '<div style="min-width:240px;font-family:sans-serif">'
        f'<div style="display:flex;align-items:center;justify-content:center;gap:12px;margin-bottom:8px">'
        f'  <div style="text-align:center">'
        f'    <img src="{escape(home_img)}" style="height:40px" '
        f"         onerror=\"this.src='{RFU_FALLBACK_ICON}'\">"
        f'    <div style="font-weight:bold;font-size:13px">{escape(home_name)}</div>'
        f"  </div>"
        f'  <div style="text-align:center">{centre_html}</div>'
        f'  <div style="text-align:center">'
        f'    <img src="{escape(away_img)}" style="height:40px" '
        f"         onerror=\"this.src='{RFU_FALLBACK_ICON}'\">"
        f'    <div style="font-weight:bold;font-size:13px">{escape(away_name)}</div>'
        f"  </div>"
        f"</div>"
        f"<hr style='margin:4px 0'>"
        f'<p style="margin:2px 0"><b>League:</b> {escape(league_name)}</p>'
        f'<p style="margin:2px 0"><b>Tier:</b> {escape(tier_name)}</p>'
        f'<p style="margin:2px 0"><b>Address:</b> {escape(address or "Unknown")}</p>'
        f'<p style="margin:4px 0">'
        f'  <a href="{escape(fixture["match_url"])}" target="_blank">Match info</a>'
        f"</p>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Map generation
# ---------------------------------------------------------------------------


def _count_label(total: int, results: int) -> str:
    """Human-readable summary like '12 results', '5 fixtures', or '3 results, 2 fixtures'."""
    fixtures = total - results
    if results == 0:
        return f"{fixtures} fixture{'s' if fixtures != 1 else ''}"
    if fixtures == 0:
        return f"{results} result{'s' if results != 1 else ''}"
    return f"{results} result{'s' if results != 1 else ''}, {fixtures} fixture{'s' if fixtures != 1 else ''}"


def _date_display(iso_date: str) -> str:
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return f"{dt.strftime('%A')}, {dt.day} {dt.strftime('%B %Y')}"
    except ValueError:
        return iso_date


def _date_short(iso_date: str) -> str:
    """Short display for dropdown: 'Sat 11 Apr'"""
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')}"
    except ValueError:
        return iso_date


def _resolve_matches(
    matches: list[tuple[Fixture, str, str]],
    team_index: dict[int, GeocodedTeam],
    season: str,
) -> list[tuple[Fixture, str, str, GeocodedTeam, GeocodedTeam | None]]:
    """Resolve team IDs to geocoded teams, dropping unresolvable ones."""
    resolved = []
    for fixture, league_name, rel_path in matches:
        home_team = team_index.get(fixture["home_team_id"])
        if not home_team:
            continue
        away_team = team_index.get(fixture["away_team_id"])
        tier_num, tier_name = extract_tier(rel_path, season)
        resolved.append((fixture, league_name, tier_name, home_team, away_team))
    return resolved


def build_match_day_map(
    fixtures_by_date: dict[str, list[tuple[Fixture, str, str]]],
    team_index: dict[int, GeocodedTeam],
    season: str,
    output_path: Path,
) -> None:
    """Generate a single Folium map with a date dropdown for all match days."""
    sorted_dates = sorted(fixtures_by_date.keys())
    if not sorted_dates:
        logger.warning("No fixtures to map")
        return

    ts_path = DATA_DIR / "fixture_data" / season / "last_updated.txt"
    if ts_path.exists():
        try:
            generated_at = datetime.fromisoformat(ts_path.read_text(encoding="utf-8").strip())
        except ValueError:
            logger.warning("Could not parse timestamp from %s, using now()", ts_path)
            generated_at = datetime.now()
    else:
        generated_at = datetime.now()

    m = folium.Map(
        location=[52.5, -1.5],
        zoom_start=7,
        tiles=None,
    )
    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr=(
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
            'contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
        ),
        control=False,
    ).add_to(m)
    header = m.get_root().header
    header.add_child(folium.Element(POPUP_CSS))
    header.add_child(folium.Element(DARK_MODE_JS))
    header.add_child(folium.Element(_match_day_seo_head(season)))

    is_prod = get_config().is_production
    home_href = "../../" if is_prod else "../../index.html"
    season_href = "../" if is_prod else "../index.html"
    season_esc = escape(season)
    nav_html = f"""
    <div class="map-header" id="mapHeader">
        <a class="map-header__crumb" href="{home_href}">Home</a>
        <span class="map-header__sep">&rsaquo;</span>
        <a class="map-header__crumb" href="{season_href}">{season_esc}</a>
        <span class="map-header__sep">&rsaquo;</span>
        <span class="map-header__title">Match Day</span>
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
    .leaflet-top {{ top: 34px !important; }}
    @media (prefers-color-scheme: dark) {{
        .map-header {{ background: rgba(22,33,62,0.92); border-bottom-color: #2a2a4a; }}
        .map-header__crumb {{ color: #4da6ff; }}
        .map-header__sep {{ color: #666; }}
        .map-header__title {{ color: #e0e8f0; }}
    }}
    @media (max-width: 480px) {{
        .map-header {{ font-size: 12px; }}
    }}
    </style>
    """
    html_el = m.get_root().html
    html_el.add_child(folium.Element(nav_html))

    icon_size = 30
    crest = icon_size
    total_w = crest * 2 + 2

    date_layers: list[tuple[str, str, int, int]] = []  # (date_iso, js_var, count, results)

    for date_iso in sorted_dates:
        matches = fixtures_by_date[date_iso]
        resolved = _resolve_matches(matches, team_index, season)
        if not resolved:
            continue

        result_count = sum(
            1
            for f, *_ in resolved
            if (f.get("home_score") is not None and f.get("away_score") is not None)
            or f.get("status")
        )

        fg = folium.FeatureGroup(name=f"date_{date_iso}", show=False, overlay=True)
        date_layers.append((date_iso, fg.get_name(), len(resolved), result_count))

        cluster = MarkerCluster(
            control=False,
            options={
                "maxClusterRadius": 1,
                "disableClusteringAtZoom": None,
                "spiderfyOnMaxZoom": True,
                "spiderfyDistanceMultiplier": 2,
                "showCoverageOnHover": False,
                "zoomToBoundsOnClick": False,
                "animate": False,
                "animateAddingMarkers": False,
            },
            icon_create_function=f"""
            function(cluster) {{
                var markers = cluster.getAllChildMarkers();
                var bestMarker = markers[0];
                var count = cluster.getChildCount();
                var names = [];
                for (var i = 0; i < markers.length; i++) {{
                    if (markers[i].options.itemName) names.push(markers[i].options.itemName);
                }}
                names.sort();
                var tooltipText = names.length > 0 ? names.slice(0, 5).join('\\n') : count + ' matches';
                var imageUrl = bestMarker && bestMarker.options.imageUrl ? bestMarker.options.imageUrl : '';
                if (imageUrl) {{
                    return L.divIcon({{
                        html: '<div style="text-align:center;position:relative;" title="' + tooltipText.replace(/"/g,'&quot;') + '">' +
                              '<img src="' + imageUrl + '" style="width:{icon_size}px;height:{icon_size}px;border-radius:50%;" onerror="this.onerror=null;this.src=\\'{RFU_FALLBACK_ICON}\\'">' +
                              '<span style="position:absolute;bottom:-5px;right:-5px;background:#333;color:white;border-radius:50%;width:16px;height:16px;font-size:10px;line-height:16px;text-align:center;">' + count + '</span></div>',
                        className: 'marker-cluster-custom',
                        iconSize: L.point({icon_size}, {icon_size}),
                        iconAnchor: L.point({icon_size // 2}, {icon_size // 2})
                    }});
                }} else {{
                    return L.divIcon({{
                        html: '<div style="text-align:center;" title="' + tooltipText.replace(/"/g,'&quot;') + '">' +
                              '<div style="width:{icon_size}px;height:{icon_size}px;border-radius:50%;background:#666;color:white;font-size:12px;line-height:{icon_size}px;text-align:center;border:2px solid white;box-shadow:0 0 3px rgba(0,0,0,0.3);">' + count + '</div></div>',
                        className: 'marker-cluster-custom',
                        iconSize: L.point({icon_size}, {icon_size}),
                        iconAnchor: L.point({icon_size // 2}, {icon_size // 2})
                    }});
                }}
            }}
            """,
        ).add_to(fg)

        for fixture, league_name, tier_name, home_team, away_team in resolved:
            lat = home_team["latitude"]
            lng = home_team["longitude"]
            home_icon_url = home_team.get("image_url") or RFU_FALLBACK_ICON
            away_icon_url = (away_team.get("image_url") if away_team else None) or RFU_FALLBACK_ICON
            popup_html = _render_popup(fixture, league_name, tier_name, home_team, away_team)

            home_name = home_team.get("name", "Home")
            away_name = away_team.get("name", "Away") if away_team else "Away"

            onerror = f"this.onerror=null; this.src='{RFU_FALLBACK_ICON}'"
            icon_html = (
                f'<div style="display:flex;align-items:center;gap:2px">'
                f'<img src="{escape(home_icon_url)}" '
                f'style="width:{crest}px;height:{crest}px;border-radius:50%;" '
                f'onerror="{onerror}">'
                f'<img src="{escape(away_icon_url)}" '
                f'style="width:{crest}px;height:{crest}px;border-radius:50%;" '
                f'onerror="{onerror}">'
                f"</div>"
            )
            icon = folium.DivIcon(
                html=icon_html,
                icon_size=(total_w, crest),
                icon_anchor=(total_w // 2, crest // 2),
            )

            home_score = fixture.get("home_score")
            away_score = fixture.get("away_score")
            status = fixture.get("status")
            if status:
                tooltip_detail = status
            elif home_score is not None and away_score is not None:
                tooltip_detail = f"{home_score}-{away_score}"
            else:
                tooltip_detail = fixture["time"] or ""

            marker = folium.Marker(
                location=[lat, lng],
                popup=folium.Popup(popup_html, max_width=320),
                tooltip=(
                    f"{home_name} vs {away_name} ({tooltip_detail})"
                    if tooltip_detail
                    else f"{home_name} vs {away_name}"
                ),
                icon=icon,
            )
            marker.options["imageUrl"] = home_icon_url  # type: ignore[index]
            marker.options["itemName"] = f"{home_name} vs {away_name}"  # type: ignore[index]
            marker.add_to(cluster)

        fg.add_to(m)

    if not date_layers:
        logger.warning("No fixtures could be placed on the map")
        return

    dropdown_options = "\n".join(
        f'<option value="{js_var}">'
        f"{_date_short(date_iso)} ({_count_label(count, results)})</option>"
        for date_iso, js_var, count, results in date_layers
    )

    date_info_json = json.dumps(
        {
            js_var: {
                "date": date_iso,
                "display": _date_display(date_iso),
                "label": _count_label(count, results),
            }
            for date_iso, js_var, count, results in date_layers
        }
    )

    all_layer_vars_json = json.dumps([js_var for _, js_var, *_ in date_layers])

    updated_display = f"{generated_at.day} {generated_at.strftime('%b %Y')}"

    control_html = f"""
    <style>
    .matchday-control {{
        position:fixed; top:42px; left:50%; transform:translateX(-50%); z-index:999;
        background:white; padding:8px 16px; border-radius:8px;
        border:2px solid grey; font-family:sans-serif; text-align:center;
        box-shadow:0 2px 8px rgba(0,0,0,0.2);
    }}
    .matchday-control select {{
        font-size:15px; padding:4px 8px; border-radius:4px; border:1px solid #ccc;
        cursor:pointer;
    }}
    .matchday-subtitle {{ margin:4px 0 0 0; color:#666; font-size:13px; }}
    .matchday-updated {{ margin:2px 0 0 0; color:#999; font-size:11px; }}
    @media only screen and (max-width: 768px) {{
        .matchday-control {{ width:90%; padding:6px 10px; }}
        .matchday-control select {{ font-size:13px; }}
    }}
    @media (prefers-color-scheme: dark) {{
        .matchday-control {{ background-color:#16213e !important; color:#e0e0e0 !important; border-color:#444 !important; }}
        .matchday-control select {{ background:#1a1a2e; color:#e0e0e0; border-color:#444; }}
        .matchday-subtitle {{ color:#aaa !important; }}
        .matchday-updated {{ color:#777 !important; }}
    }}
    </style>

    <div class="matchday-control">
        <select id="matchday-select" onchange="switchMatchDay(this.value)">
            {dropdown_options}
        </select>
        <p class="matchday-subtitle" id="matchday-info"></p>
        <p class="matchday-updated">Data updated: {updated_display}</p>
    </div>

    <script>
    var dateInfo = {date_info_json};
    var allLayers = {all_layer_vars_json};
    var mapObj = null;

    function getMap() {{
        if (mapObj) return mapObj;
        var containers = document.querySelectorAll('.folium-map');
        if (containers.length > 0) mapObj = window[containers[0].id];
        return mapObj;
    }}

    function switchMatchDay(selectedVar) {{
        var info = dateInfo[selectedVar];
        if (info) {{
            document.getElementById('matchday-info').innerHTML = info.display + ' &mdash; ' + info.label;
        }}

        var map = getMap();
        if (!map) return;

        for (var i = 0; i < allLayers.length; i++) {{
            var layerObj = window[allLayers[i]];
            if (!layerObj) continue;
            if (allLayers[i] === selectedVar) {{
                if (!map.hasLayer(layerObj)) map.addLayer(layerObj);
            }} else {{
                if (map.hasLayer(layerObj)) map.removeLayer(layerObj);
            }}
        }}
    }}

    window.addEventListener('load', function() {{
        var today = new Date().toISOString().slice(0, 10);
        var defaultVar = allLayers[allLayers.length - 1];
        for (var i = 0; i < allLayers.length; i++) {{
            var info = dateInfo[allLayers[i]];
            if (info && info.date >= today) {{
                var d = new Date(info.date + 'T00:00:00');
                if (d.getDay() === 6) {{
                    defaultVar = allLayers[i];
                    break;
                }}
            }}
        }}
        var sel = document.getElementById('matchday-select');
        if (sel) {{ sel.value = defaultVar; }}
        switchMatchDay(defaultVar);
    }});
    </script>
    """
    html_el = m.get_root().html
    html_el.add_child(folium.Element(control_html))

    is_prod = get_config().is_production
    shared_path = "/shared" if is_prod else "../../shared"
    boundaries_file = str(DIST_DIR / "shared" / "boundaries.json")

    boundary_preamble = """
        var _countryLayers = [], _itlLayers = [];
        var _lightCountry = { fillColor:'lightgray', color:'black', weight:2, fillOpacity:0.1 };
        var _darkCountry  = { fillColor:'darkgray', color:'#ccc', weight:2, fillOpacity:0.1 };
        var _lightITL     = { fillColor:'transparent', color:'gray', weight:0.5, fillOpacity:0, opacity:0.4 };
        var _darkITL      = { fillColor:'transparent', color:'lightgray', weight:0.5, fillOpacity:0, opacity:0.4 };
        window.updateBoundaryStyles = function(dark) {
            var cs = dark ? _darkCountry : _lightCountry;
            var bs = dark ? _darkITL : _lightITL;
            _countryLayers.forEach(function(ly) { ly.setStyle(cs); });
            _itlLayers.forEach(function(ly) { ly.setStyle(bs); });
        };
    """

    boundary_load_body = """
            var dark = el.classList.contains('rugby-map-dark');
            var cs = dark ? _darkCountry : _lightCountry;
            Object.entries(bd.countries || {}).forEach(([n, d]) => { var ly = L.geoJson(d, {style:cs}); ly.addTo(map); _countryLayers.push(ly); });
            var bs = dark ? _darkITL : _lightITL;
            ['itl_1','itl_2','itl_3'].forEach(lv => { if (bd[lv]) { var ly = L.geoJson(bd[lv], {style:bs}); ly.addTo(map); _itlLayers.push(ly); } });
    """

    if is_prod:
        boundary_script = f"""
    <script>
    (function() {{
        {boundary_preamble}
        function addBoundaries() {{
            var el = document.querySelector('.folium-map');
            if (!el || !el._leaflet_id) {{ setTimeout(addBoundaries, 100); return; }}
            var map = window[Object.keys(window).find(k => k.startsWith('map_') && window[k] instanceof L.Map)];
            if (!map) {{ setTimeout(addBoundaries, 100); return; }}
            fetch('{shared_path}/boundaries.json').then(r => r.json()).then(bd => {{
                {boundary_load_body}
            }}).catch(e => console.warn('Could not load boundaries:', e));
        }}
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', addBoundaries);
        else addBoundaries();
    }})();
    </script>
    """
    else:
        bd_json = "{}"
        bp = Path(boundaries_file)
        if bp.exists():
            bd_json = bp.read_text()
        boundary_script = f"""
    <script>
    (function() {{
        {boundary_preamble}
        function addBoundaries() {{
            var el = document.querySelector('.folium-map');
            if (!el || !el._leaflet_id) {{ setTimeout(addBoundaries, 100); return; }}
            var map = window[Object.keys(window).find(k => k.startsWith('map_') && window[k] instanceof L.Map)];
            if (!map) {{ setTimeout(addBoundaries, 100); return; }}
            var bd = {bd_json};
                {boundary_load_body}
        }}
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', addBoundaries);
        else addBoundaries();
    }})();
    </script>
    """
    html_el.add_child(folium.Element(boundary_script))

    head_tail = [get_favicon_html(depth=2), get_google_analytics_script()]
    if is_prod:
        head_tail.append(get_service_worker_registration_script())
    extra_head = "\n".join(part for part in head_tail if part)
    header.add_child(folium.Element(extra_head))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(output_path))
    total_placed = sum(c for _, _, c, _ in date_layers)
    logger.info(
        "Map saved to %s (%d matches across %d dates)",
        output_path,
        total_placed,
        len(date_layers),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a match-day map from scraped fixture data."
    )
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season (default: 2025-2026)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file (default: dist/<season>/match_day/index.html)",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="Use production URLs and paths (static site root, GA/favicon prefixes)",
    )
    args = parser.parse_args()

    set_config(is_production=args.production, season=args.season)
    setup_logging()
    season: str = args.season

    output_path = (
        Path(args.output) if args.output else DIST_DIR / season / "match_day" / "index.html"
    )

    team_index = build_team_index(season)
    fixtures_by_date = load_all_fixtures(season)

    if not fixtures_by_date:
        logger.warning("No fixtures found for season %s", season)
        return

    build_match_day_map(fixtures_by_date, team_index, season, output_path)


if __name__ == "__main__":
    main()
