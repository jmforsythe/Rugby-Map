"""
Generate a single interactive map with a date dropdown for all upcoming fixtures.

Reads committed fixture_data/<season>/ and geocoded_teams/<season>/ to show
match venues. A dropdown (centre) selects the match day; tier filters use the
native Leaflet ``L.control.layers`` widget at top-right (same chrome as the
All Leagues map). Overlays are **one row per absolute pyramid tier**, grouped under **Men's**
(tier &lt; 101), **Women's** (101+), and **Other** (unknown). Only tiers
with fixtures on the selected date appear. No network
access required — runs in the deployed environment.

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
from folium.plugins import FeatureGroupSubGroup, MarkerCluster

from core import (
    Fixture,
    FixtureLeague,
    GeocodedLeague,
    GeocodedTeam,
    get_config,
    get_favicon_html,
    get_google_analytics_script,
    get_service_worker_registration_script,
    get_twitter_card_meta,
    set_config,
    setup_logging,
)
from core.basemap_tiles import CARTO_TILE_URL_LIGHT, folium_carto_attribution
from core.config import DIST_DIR
from core.map_builder import DARK_MODE_JS, POPUP_CSS
from rugby import BRAND, DATA_DIR, short_season
from rugby.seo import BASE_URL, OG_DEFAULT_IMAGE, breadcrumb_ld_script, og_image_meta_html
from rugby.tiers import (
    _womens_current_tier_name,
    extract_tier,
    get_competition_offset,
    mens_current_tier_name,
)

logger = logging.getLogger(__name__)

RFU_FALLBACK_ICON = "https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg"

MATCHDAY_LAYER_CONTROL_HOOK_HTML = """
    <script>
    (function hookLayerControl() {
        if (!window.L || !L.Control || !L.Control.Layers) { setTimeout(hookLayerControl, 50); return; }
        if (L.Control.Layers.prototype._layerControlHooked) { return; }
        var orig = L.Control.Layers.prototype.addTo;
        L.Control.Layers.prototype._layerControlHooked = true;
        L.Control.Layers.prototype.addTo = function(map) { var r = orig.call(this, map); window.layerControl = this; return r; };
    })();
    </script>
    <style>
    .leaflet-control-layers-list { overflow-y: auto !important; max-height: min(70vh, 480px); }
    @media only screen and (max-width: 768px) { .leaflet-control-layers-list { font-size: large !important; max-height: min(55vh, 360px); } }
    </style>
"""


def matchday_cluster_icon_create_js(icon_size: int, rfu_fallback: str) -> str:
    """Leaflet MarkerCluster `icon_create_function` body (cluster crest + count)."""
    half = icon_size // 2
    return f"""
    function(cluster) {{
        var markers = cluster.getAllChildMarkers();
        var bestMarker = null;
        var bestTier = Infinity;
        var names = [];
        for (var i = 0; i < markers.length; i++) {{
            var mk = markers[i];
            if (mk.options.tierOrder !== undefined && mk.options.tierOrder !== null && mk.options.tierOrder < bestTier) {{
                bestTier = mk.options.tierOrder;
                bestMarker = mk;
            }}
            if (mk.options.itemName) names.push(mk.options.itemName);
        }}
        if (!bestMarker) bestMarker = markers[0];
        names.sort();
        var count = cluster.getChildCount();
        var tooltipText = names.length > 0 ? names.slice(0, 5).join('\\n') : count + ' matches';
        var imageUrl = bestMarker && bestMarker.options.imageUrl ? bestMarker.options.imageUrl : '';
        if (imageUrl) {{
            return L.divIcon({{
                html: '<div style="text-align:center;position:relative;" title="' + tooltipText.replace(/"/g,'&quot;') + '">' +
                      '<img src="' + imageUrl + '" style="width:{icon_size}px;height:{icon_size}px;border-radius:50%;" onerror="this.onerror=null;this.src=\\'{rfu_fallback}\\'">' +
                      '<span style="position:absolute;bottom:-5px;right:-5px;background:#333;color:white;border-radius:50%;width:16px;height:16px;font-size:10px;line-height:16px;text-align:center;">' + count + '</span></div>',
                className: 'marker-cluster-custom',
                iconSize: L.point({icon_size}, {icon_size}),
                iconAnchor: L.point({half}, {half})
            }});
        }} else {{
            return L.divIcon({{
                html: '<div style="text-align:center;" title="' + tooltipText.replace(/"/g,'&quot;') + '">' +
                      '<div style="width:{icon_size}px;height:{icon_size}px;border-radius:50%;background:#666;color:white;font-size:12px;line-height:{icon_size}px;text-align:center;border:2px solid white;box-shadow:0 0 3px rgba(0,0,0,0.3);">' + count + '</div></div>',
                className: 'marker-cluster-custom',
                iconSize: L.point({icon_size}, {icon_size}),
                iconAnchor: L.point({half}, {half})
            }});
        }}
    }}
    """


_MATCHDAY_WIDGET_HTML = """
    <style>
    .matchday-control {
        position:fixed; top:42px; left:50%; transform:translateX(-50%); z-index:999;
        background:white; padding:8px 16px; border-radius:8px;
        border:2px solid grey; font-family:sans-serif; text-align:center;
        box-shadow:0 2px 8px rgba(0,0,0,0.2);
    }
    .matchday-control select {
        font-size:15px; padding:4px 8px; border-radius:4px; border:1px solid #ccc;
        cursor:pointer;
    }
    .matchday-subtitle { margin:4px 0 0 0; color:#666; font-size:13px; }
    .matchday-updated { margin:2px 0 0 0; color:#999; font-size:11px; }
    .folium-map .leaflet-control-layers-overlays .matchday-lc-heading {
        font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.06em;
        color:#555; padding:8px 8px 4px 6px; margin:0; border-top:1px solid #e0e0e0;
    }
    .folium-map .leaflet-control-layers-overlays .matchday-lc-heading:first-child {
        border-top:0; padding-top:4px;
    }
    html[data-rugby-effective="dark"] .folium-map .leaflet-control-layers-overlays .matchday-lc-heading,
    .folium-map.rugby-map-dark .leaflet-control-layers-overlays .matchday-lc-heading {
        color:#aab8d8; border-top-color:#2a2a4a;
    }
    @media only screen and (max-width: 768px) {
        .matchday-control { width:90%; max-width:360px; padding:6px 10px; }
        .matchday-control select { font-size:13px; }
    }
    html[data-rugby-effective="dark"] .matchday-control {
        background-color:#16213e !important;
        color:#e0e0e0 !important;
        border-color:#444 !important;
    }
    html[data-rugby-effective="dark"] .matchday-control select {
        background:#1a1a2e;
        color:#e0e0e0;
        border-color:#444;
    }
    html[data-rugby-effective="dark"] .matchday-subtitle { color:#aaa !important; }
    html[data-rugby-effective="dark"] .matchday-updated { color:#777 !important; }
    </style>

    <div class="matchday-control">
        <select id="matchday-select" onchange="switchMatchDay(this.value)">
            @@DROPDOWN_OPTIONS@@
        </select>
        <p class="matchday-subtitle" id="matchday-info"></p>
        <p class="matchday-updated">Data updated: @@UPDATED_DISPLAY@@</p>
    </div>

    <script>
    var dateInfo = @@DATE_INFO_JSON@@;
    var allDates = @@ALL_DATES_JSON@@;
    var tierProxyVars = @@TIER_PROXY_VARS_JSON@@;
    var tierLabels = @@TIER_LABEL_JSON@@;
    var subgroupsByDate = @@SUBGROUPS_BY_DATE_JSON@@;
    var mapObj = null;
    var tierProxies = {};
    var tierInControl = {};
    var tierUserVisible = {};
    var matchdayInitDone = false;
    var matchdaySuppressEvents = false;

    function getMap() {
        if (mapObj) return mapObj;
        var containers = document.querySelectorAll('.folium-map');
        if (containers.length > 0) mapObj = window[containers[0].id];
        return mapObj;
    }

    function tierKeyForLayer(layer) {
        for (var k in tierProxies) {
            if (tierProxies[k] === layer) return k;
        }
        return null;
    }

    function matchdayOverlayLabelName(el) {
        var sp = el.querySelector('span');
        if (sp) return (sp.textContent || '').replace(/\\s+/g, ' ').trim();
        return (el.textContent || '').replace(/\\s+/g, ' ').trim();
    }

    function matchdayTierKeyForLabelText(name) {
        for (var k in tierLabels) {
            if (tierLabels[k] === name) return k;
        }
        return null;
    }

    function matchdayTierCategory(k) {
        if (k === null) return 'other';
        var n = parseInt(k, 10);
        if (isNaN(n) || n === 999) return 'other';
        if (n >= 101) return 'womens';
        return 'mens';
    }

    function sortMatchdayOverlayLabels() {
        var section = document.querySelector('.folium-map .leaflet-control-layers-overlays');
        if (!section) return;
        section.querySelectorAll('.matchday-lc-heading').forEach(function(h) { h.remove(); });
        var labels = Array.prototype.slice.call(section.querySelectorAll('label'));
        function orderForName(name) {
            var kk = matchdayTierKeyForLabelText(name);
            if (kk === null) return 99999;
            var n = parseInt(kk, 10);
            return isNaN(n) ? 99999 : n;
        }
        labels.sort(function(a, b) {
            return orderForName(matchdayOverlayLabelName(a)) - orderForName(matchdayOverlayLabelName(b));
        });
        labels.forEach(function(el) { section.appendChild(el); });
    }

    function applyMatchdayLayerSectionHeadings() {
        var section = document.querySelector('.folium-map .leaflet-control-layers-overlays');
        if (!section) return;
        section.querySelectorAll('.matchday-lc-heading').forEach(function(h) { h.remove(); });
        var labels = Array.prototype.slice.call(section.querySelectorAll('label'));
        if (labels.length === 0) return;
        var catOrder = ['mens', 'womens', 'other'];
        var catTitle = { mens: "Men's", womens: "Women's", other: 'Other' };
        while (section.firstChild) {
            section.removeChild(section.firstChild);
        }
        for (var ci = 0; ci < catOrder.length; ci++) {
            var cat = catOrder[ci];
            var block = labels.filter(function(lab) {
                var name = matchdayOverlayLabelName(lab);
                var k = matchdayTierKeyForLabelText(name);
                return matchdayTierCategory(k) === cat;
            });
            if (block.length === 0) continue;
            var hd = document.createElement('div');
            hd.className = 'matchday-lc-heading';
            hd.setAttribute('role', 'presentation');
            hd.textContent = catTitle[cat];
            section.appendChild(hd);
            for (var bi = 0; bi < block.length; bi++) {
                section.appendChild(block[bi]);
            }
        }
    }

    function rebindLayerControlForDate(date) {
        var map = getMap();
        if (!map || !window.layerControl) return;
        var avail = subgroupsByDate[date] || {};
        matchdaySuppressEvents = true;
        var tierKeys = Object.keys(tierProxies).sort(function(a, b) {
            return parseInt(a, 10) - parseInt(b, 10);
        });
        for (var xi = 0; xi < tierKeys.length; xi++) {
            var k = tierKeys[xi];
            var proxy = tierProxies[k];
            if (typeof proxy.clearLayers === 'function') proxy.clearLayers();
            var subVar = avail[k];
            if (subVar) {
                var sub = window[subVar];
                if (sub) proxy.addLayer(sub);
                if (tierUserVisible[k] !== false) {
                    if (!map.hasLayer(proxy)) map.addLayer(proxy);
                } else {
                    if (map.hasLayer(proxy)) map.removeLayer(proxy);
                }
                if (!tierInControl[k]) {
                    window.layerControl.addOverlay(proxy, tierLabels[k]);
                    tierInControl[k] = true;
                }
            } else {
                if (map.hasLayer(proxy)) map.removeLayer(proxy);
                if (tierInControl[k]) {
                    window.layerControl.removeLayer(proxy);
                    tierInControl[k] = false;
                }
            }
        }
        matchdaySuppressEvents = false;
        sortMatchdayOverlayLabels();
        applyMatchdayLayerSectionHeadings();
    }

    function initMatchdayLayerWiring() {
        if (matchdayInitDone) return true;
        var map = getMap();
        if (!map || !window.layerControl) return false;
        for (var k in tierProxyVars) {
            var p = window[tierProxyVars[k]];
            if (p) {
                tierProxies[k] = p;
                tierInControl[k] = true;
                tierUserVisible[k] = true;
            }
        }
        map.on('overlayadd', function(e) {
            if (matchdaySuppressEvents) return;
            var k = tierKeyForLayer(e.layer);
            if (k !== null) tierUserVisible[k] = true;
        });
        map.on('overlayremove', function(e) {
            if (matchdaySuppressEvents) return;
            var k = tierKeyForLayer(e.layer);
            if (k !== null) tierUserVisible[k] = false;
        });
        matchdayInitDone = true;
        return true;
    }

    function switchMatchDay(selectedDate) {
        var info = dateInfo[selectedDate];
        if (info) {
            document.getElementById('matchday-info').innerHTML = info.display + ' &mdash; ' + info.label;
        }
        if (!initMatchdayLayerWiring()) {
            setTimeout(function() { switchMatchDay(selectedDate); }, 50);
            return;
        }
        rebindLayerControlForDate(selectedDate);
    }

    window.addEventListener('load', function() {
        var today = new Date().toISOString().slice(0, 10);
        var defaultDate = allDates[allDates.length - 1];
        for (var i = 0; i < allDates.length; i++) {
            var iso = allDates[i];
            if (iso >= today) {
                var d = new Date(iso + 'T00:00:00');
                if (d.getDay() === 6) {
                    defaultDate = iso;
                    break;
                }
            }
        }
        var sel = document.getElementById('matchday-select');
        if (sel) { sel.value = defaultDate; }
        switchMatchDay(defaultDate);
    });
    </script>"""


def build_matchday_control_html(
    *,
    dropdown_options: str,
    updated_display: str,
    date_info_json: str,
    all_dates_json: str,
    tier_proxy_vars_json: str,
    tier_label_json: str,
    subgroups_by_date_json: str,
) -> str:
    """Dropdown + scripts for date switching and tier overlay rebinding."""
    return (
        _MATCHDAY_WIDGET_HTML.replace("@@DROPDOWN_OPTIONS@@", dropdown_options)
        .replace("@@UPDATED_DISPLAY@@", updated_display)
        .replace("@@DATE_INFO_JSON@@", date_info_json)
        .replace("@@ALL_DATES_JSON@@", all_dates_json)
        .replace("@@TIER_PROXY_VARS_JSON@@", tier_proxy_vars_json)
        .replace("@@TIER_LABEL_JSON@@", tier_label_json)
        .replace("@@SUBGROUPS_BY_DATE_JSON@@", subgroups_by_date_json)
    )


def _matchday_layer_label(tier_num: int, season: str) -> str:
    """LayerControl label per absolute tier — same naming as All Leagues merit maps."""
    if tier_num == 999:
        return "Unknown Tier"
    if tier_num >= 101:
        return _womens_current_tier_name(tier_num)
    return mens_current_tier_name(tier_num, season)


def _match_day_seo_head(season: str) -> str:
    """Return <head> elements for document title, viewport, and Open Graph (match day)."""
    season_short = short_season(season)
    page_title = f"Match Day | {season_short} | {BRAND}"
    desc = (
        f"See upcoming {season_short} English rugby fixtures near you: every ground on an "
        f"interactive map—pan and zoom to your area, then pick a week for kick-offs, scores, "
        f"and results."
    )
    lines = [
        f"    <title>{escape(page_title)}</title>",
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0" />',
        f'    <meta name="description" content="{escape(desc)}" />',
        f'    <meta property="og:title" content="{escape(page_title)}" />',
        f'    <meta property="og:description" content="{escape(desc)}" />',
        '    <meta property="og:type" content="website" />',
    ]
    if get_config().is_production:
        url = f"{BASE_URL}/{season}/match_day/"
        lines.append(f'    <link rel="canonical" href="{escape(url)}">')
        lines.append(f'    <meta property="og:url" content="{escape(url)}" />')
        lines.extend(og_image_meta_html(escape(OG_DEFAULT_IMAGE), indent="    ").split("\n"))
        lines.append(f"    {get_twitter_card_meta()}")
        lines.append(
            breadcrumb_ld_script(
                [
                    ("Home", f"{BASE_URL}/"),
                    (season, f"{BASE_URL}/{season}/"),
                    ("Match Day", url),
                ],
                indent="    ",
            )
        )
    return "\n".join(lines) + "\n"


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
) -> list[tuple[Fixture, str, int, str, GeocodedTeam, GeocodedTeam | None]]:
    """Resolve team IDs to geocoded teams, dropping unresolvable ones.

    The returned ``tier_num`` is the **absolute** pyramid tier:
    merit-league local tiers are shifted by their competition offset so
    e.g. CANDY local tier 1 (offset 7) becomes absolute tier 8 — the same
    convention the All Leagues map uses. The ``tier_name`` is left as the
    competition-qualified label from ``extract_tier`` (e.g. "CANDY 1").
    """
    resolved = []
    for fixture, league_name, rel_path in matches:
        home_team = team_index.get(fixture["home_team_id"])
        if not home_team:
            continue
        away_team = team_index.get(fixture["away_team_id"])
        tier_num, tier_name = extract_tier(rel_path, season)
        if tier_num != 999 and rel_path.replace("\\", "/").startswith("merit/"):
            comp_key = rel_path.replace("\\", "/").split("/")[1]
            tier_num += get_competition_offset(comp_key, season)
        resolved.append((fixture, league_name, tier_num, tier_name, home_team, away_team))
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
        tiles=CARTO_TILE_URL_LIGHT,
        attr=folium_carto_attribution(),
        control=False,
    ).add_to(m)
    header = m.get_root().header  # type: ignore[attr-defined]
    header.add_child(folium.Element(POPUP_CSS))
    header.add_child(folium.Element(DARK_MODE_JS))
    header.add_child(folium.Element(_match_day_seo_head(season)))

    is_prod = get_config().is_production
    home_href = "../../" if is_prod else "../../index.html"
    season_href = "../" if is_prod else "../index.html"
    season_esc = escape(season)
    home_h_e = escape(home_href)
    season_h_e = escape(season_href)

    nav_html = f"""
    <div class="map-header-wrap" id="mapHeaderWrap">
    <div class="map-header" id="mapHeader">
        <a class="map-header__crumb" href="{home_h_e}">Home</a>
        <span class="map-header__sep">&rsaquo;</span>
        <a class="map-header__crumb" href="{season_h_e}">{season_esc}</a>
        <span class="map-header__sep">&rsaquo;</span>
        <span class="map-header__title">Match Day</span>
        <span class="map-header__theme">
        <label class="map-header__theme-label" for="rugbyMapThemeSelect">Appearance</label>
        <select id="rugbyMapThemeSelect" class="map-header__theme-select" aria-label="Map color theme">
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
    html_el = m.get_root().html  # type: ignore[attr-defined]
    html_el.add_child(folium.Element(nav_html))

    icon_size = 30
    crest = icon_size
    total_w = crest * 2 + 2

    resolved_per_date: dict[
        str, list[tuple[Fixture, str, int, str, GeocodedTeam, GeocodedTeam | None]]
    ] = {}
    tier_nums_seen: set[int] = set()
    for date_iso in sorted_dates:
        resolved = _resolve_matches(fixtures_by_date[date_iso], team_index, season)
        if not resolved:
            continue
        resolved_per_date[date_iso] = resolved
        for row in resolved:
            tier_nums_seen.add(row[2])

    if not resolved_per_date:
        logger.warning("No fixtures could be placed on the map")
        return

    sorted_tier_nums = sorted(tier_nums_seen)

    parent_cluster = MarkerCluster(
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
        icon_create_function=matchday_cluster_icon_create_js(icon_size, RFU_FALLBACK_ICON),
    )
    m.add_child(parent_cluster)

    tier_label_by_num: dict[int, str] = {
        tn: _matchday_layer_label(tn, season) for tn in sorted_tier_nums
    }

    tier_proxies: dict[int, folium.FeatureGroup] = {}
    for tier_num in sorted_tier_nums:
        label = tier_label_by_num[tier_num]
        proxy = folium.FeatureGroup(name=label, overlay=True, control=True, show=True)
        proxy.add_to(m)
        tier_proxies[tier_num] = proxy

    date_meta: list[tuple[str, int, int]] = []  # (date_iso, total_count, result_count)
    subgroups_by_date: dict[str, dict[int, str]] = {}

    for date_iso in sorted_dates:
        resolved = resolved_per_date.get(date_iso)
        if not resolved:
            continue

        result_count = sum(
            1
            for f, *_ in resolved
            if (f.get("home_score") is not None and f.get("away_score") is not None)
            or f.get("status")
        )
        date_meta.append((date_iso, len(resolved), result_count))

        by_tier_num: dict[
            int, list[tuple[Fixture, str, int, str, GeocodedTeam, GeocodedTeam | None]]
        ] = defaultdict(list)
        for row in resolved:
            by_tier_num[row[2]].append(row)

        date_subs: dict[int, str] = {}
        for tier_num in sorted(by_tier_num.keys()):
            sub = FeatureGroupSubGroup(
                parent_cluster, name=None, overlay=True, control=False, show=False
            )
            m.add_child(sub)
            date_subs[tier_num] = sub.get_name()

            for fixture, league_name, _tn, tname, home_team, away_team in by_tier_num[tier_num]:
                lat = home_team.get("latitude")
                lng = home_team.get("longitude")
                if lat is None or lng is None:
                    continue
                home_icon_url = home_team.get("image_url") or RFU_FALLBACK_ICON
                away_icon_url = (
                    away_team.get("image_url") if away_team else None
                ) or RFU_FALLBACK_ICON
                popup_html = _render_popup(fixture, league_name, tname, home_team, away_team)

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
                marker.options["tierOrder"] = tier_num  # type: ignore[index]
                marker.add_to(sub)

        subgroups_by_date[date_iso] = date_subs

    if not date_meta:
        logger.warning("No fixtures could be placed on the map")
        return

    folium.LayerControl(position="topright", collapsed=True).add_to(m)
    m.get_root().header.add_child(  # type: ignore[attr-defined]
        folium.Element(MATCHDAY_LAYER_CONTROL_HOOK_HTML)
    )

    dropdown_options = "\n".join(
        f'<option value="{escape(date_iso)}">'
        f"{_date_short(date_iso)} ({_count_label(total, results)})</option>"
        for date_iso, total, results in date_meta
    )

    date_info_json = json.dumps(
        {
            date_iso: {
                "display": _date_display(date_iso),
                "label": _count_label(total, results),
            }
            for date_iso, total, results in date_meta
        }
    )
    all_dates_json = json.dumps([d for d, _, _ in date_meta])

    tier_proxy_vars_json = json.dumps(
        {str(tn): proxy.get_name() for tn, proxy in tier_proxies.items()}
    )
    tier_label_json = json.dumps({str(tn): tier_label_by_num[tn] for tn in sorted_tier_nums})
    subgroups_by_date_json = json.dumps(
        {
            date: {str(tn): name for tn, name in subs.items()}
            for date, subs in subgroups_by_date.items()
        }
    )

    updated_display = f"{generated_at.day} {generated_at.strftime('%b %Y')}"

    control_html = build_matchday_control_html(
        dropdown_options=dropdown_options,
        updated_display=updated_display,
        date_info_json=date_info_json,
        all_dates_json=all_dates_json,
        tier_proxy_vars_json=tier_proxy_vars_json,
        tier_label_json=tier_label_json,
        subgroups_by_date_json=subgroups_by_date_json,
    )
    html_el = m.get_root().html  # type: ignore[attr-defined]
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
    total_placed = sum(total for _, total, _ in date_meta)
    logger.info(
        "Map saved to %s (%d matches across %d dates, %d tiers)",
        output_path,
        total_placed,
        len(date_meta),
        len(tier_proxies),
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
