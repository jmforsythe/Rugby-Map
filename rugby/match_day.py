"""
Generate a single interactive map with a date dropdown for all upcoming fixtures.

Reads committed fixture_data/<season>/ and geocoded_teams/<season>/ to show
match venues. A dropdown lets the user switch between match days.
No network access required — runs in the deployed environment.

Usage: python make_match_day_map.py --season 2025-2026
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
    get_favicon_html,
    get_google_analytics_script,
    setup_logging,
)
from core.config import DIST_DIR
from rugby import DATA_DIR
from rugby.tiers import extract_tier

logger = logging.getLogger(__name__)

RFU_FALLBACK_ICON = "https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg"

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

    return (
        '<div style="min-width:240px;font-family:sans-serif">'
        f'<div style="display:flex;align-items:center;justify-content:center;gap:12px;margin-bottom:8px">'
        f'  <div style="text-align:center">'
        f'    <img src="{escape(home_img)}" style="height:40px" '
        f"         onerror=\"this.src='{RFU_FALLBACK_ICON}'\">"
        f'    <div style="font-weight:bold;font-size:13px">{escape(home_name)}</div>'
        f"  </div>"
        f'  <div style="font-size:18px;font-weight:bold">{escape(fixture["time"] or "")}</div>'
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

    m = folium.Map(
        location=[52.5, -1.5],
        zoom_start=7,
        tiles="cartodbpositron",
    )

    icon_size = 30
    crest = icon_size
    total_w = crest * 2 + 2

    date_layers: list[tuple[str, str, int]] = []  # (date_iso, js_var_name, count)

    for date_iso in sorted_dates:
        matches = fixtures_by_date[date_iso]
        resolved = _resolve_matches(matches, team_index, season)
        if not resolved:
            continue

        is_first = len(date_layers) == 0
        fg = folium.FeatureGroup(name=f"date_{date_iso}", show=is_first, overlay=True)
        date_layers.append((date_iso, fg.get_name(), len(resolved)))

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

            marker = folium.Marker(
                location=[lat, lng],
                popup=folium.Popup(popup_html, max_width=320),
                tooltip=f"{home_name} vs {away_name} ({fixture['time'] or 'TBC'})",
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
        f'<option value="{js_var}" {"selected" if i == 0 else ""}>'
        f"{_date_short(date_iso)} ({count})</option>"
        for i, (date_iso, js_var, count) in enumerate(date_layers)
    )

    date_info_json = json.dumps(
        {
            js_var: {"display": _date_display(date_iso), "count": count}
            for date_iso, js_var, count in date_layers
        }
    )

    all_layer_vars_json = json.dumps([js_var for _, js_var, _ in date_layers])

    first_date = date_layers[0]
    first_display = _date_display(first_date[0])
    first_count = first_date[2]

    control_html = f"""
    <style>
    .matchday-control {{
        position:fixed; top:10px; left:50%; transform:translateX(-50%); z-index:9999;
        background:white; padding:8px 16px; border-radius:8px;
        border:2px solid grey; font-family:sans-serif; text-align:center;
        box-shadow:0 2px 8px rgba(0,0,0,0.2);
    }}
    .matchday-control select {{
        font-size:15px; padding:4px 8px; border-radius:4px; border:1px solid #ccc;
        cursor:pointer;
    }}
    .matchday-subtitle {{ margin:4px 0 0 0; color:#666; font-size:13px; }}
    .legend-toggle {{ cursor:pointer; user-select:none; display:inline-block; float:right; font-weight:bold; font-size:18px; }}
    .legend-content.collapsed {{ display:none; }}
    @media only screen and (max-width: 768px) {{
        .matchday-control {{ width:90%; padding:6px 10px; }}
        .matchday-control select {{ font-size:13px; }}
        .map-legend {{ bottom:10px !important; right:10px !important; width:200px !important; max-height:300px !important; font-size:11px !important; padding:8px !important; }}
        .map-legend h4 {{ font-size:13px !important; }}
        .map-legend i {{ width:12px !important; height:12px !important; }}
        .legend-content {{ max-height:250px !important; }}
    }}
    @media (prefers-color-scheme: dark) {{
        .matchday-control {{ background-color:#16213e !important; color:#e0e0e0 !important; border-color:#444 !important; }}
        .matchday-control select {{ background:#1a1a2e; color:#e0e0e0; border-color:#444; }}
        .matchday-subtitle {{ color:#aaa !important; }}
        .map-legend {{ background-color:#16213e !important; color:#e0e0e0 !important; border-color:#444 !important; }}
        .map-legend h4 {{ color:#e0e8f0; }}
    }}
    </style>

    <div class="matchday-control">
        <select id="matchday-select" onchange="switchMatchDay(this.value)">
            {dropdown_options}
        </select>
        <p class="matchday-subtitle" id="matchday-info">{escape(first_display)} &mdash; {first_count} matches</p>
    </div>

    <div class="map-legend" id="matchday-legend" style="position:fixed; bottom:50px; right:50px; width:300px;
                background-color:white; z-index:999; font-size:14px;
                border:2px solid grey; border-radius:5px; padding:10px">
        <h4 style="margin-top:0;" id="legend-title">{escape(first_display)} - {first_count}
            <span class="legend-toggle" onclick="toggleLegend()" title="Toggle legend">\u2212</span>
        </h4>
        <div class="legend-content" id="legend-content" style="overflow-y:auto; max-height:500px;">
        </div>
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

        var info = dateInfo[selectedVar];
        if (info) {{
            document.getElementById('matchday-info').innerHTML = info.display + ' &mdash; ' + info.count + ' matches';
            document.getElementById('legend-title').innerHTML = info.display + ' - ' + info.count +
                ' <span class="legend-toggle" onclick="toggleLegend()" title="Toggle legend">\\u2212</span>';
        }}
    }}

    function toggleLegend() {{
        var c = document.getElementById("legend-content");
        var toggles = document.querySelectorAll(".legend-toggle");
        if (c.classList.contains("collapsed")) {{
            c.classList.remove("collapsed");
            toggles.forEach(function(t) {{ t.textContent = "\\u2212"; }});
        }} else {{
            c.classList.add("collapsed");
            toggles.forEach(function(t) {{ t.textContent = "+"; }});
        }}
    }}

    (function() {{
        if (window.innerWidth <= 768) {{
            var c = document.getElementById("legend-content");
            var toggles = document.querySelectorAll(".legend-toggle");
            if (c) c.classList.add("collapsed");
            toggles.forEach(function(t) {{ t.textContent = "+"; }});
        }}
    }})();
    </script>
    """
    m.get_root().html.add_child(folium.Element(control_html))

    ga = get_google_analytics_script()
    favicon = get_favicon_html(depth=2)
    extra_head = f"{ga}\n{favicon}" if ga else favicon
    m.get_root().header.add_child(folium.Element(extra_head))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(output_path))
    total_placed = sum(c for _, _, c in date_layers)
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
    _default_match_day_out = DIST_DIR / "match_day" / "index.html"
    parser.add_argument(
        "--output",
        type=str,
        default=str(_default_match_day_out),
        help=f"Output file (default: {_default_match_day_out.as_posix()})",
    )
    args = parser.parse_args()

    setup_logging()
    season: str = args.season

    team_index = build_team_index(season)
    fixtures_by_date = load_all_fixtures(season)

    if not fixtures_by_date:
        logger.warning("No fixtures found for season %s", season)
        return

    build_match_day_map(fixtures_by_date, team_index, season, Path(args.output))


if __name__ == "__main__":
    main()
