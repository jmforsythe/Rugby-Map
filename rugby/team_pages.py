from __future__ import annotations

import argparse
import json
import logging
import re
import urllib.parse
from collections import defaultdict
from collections.abc import Callable, Iterable
from html import escape
from typing import Any, TypedDict

from core import (
    GeocodedLeague,
    TeamTravelDistances,
    TravelDistances,
    get_config,
    get_favicon_html,
    get_google_analytics_script,
    get_twitter_card_meta,
    sanitize_team_name,
    set_config,
    setup_logging,
    team_name_to_filepath,
)
from core.config import DIST_DIR
from rugby import BRAND, DATA_DIR
from rugby.addresses import team_name_to_club_name
from rugby.seo import BASE_URL as SITE_BASE_URL
from rugby.seo import OG_DEFAULT_IMAGE, breadcrumb_ld_script, og_image_meta_html
from rugby.tiers import extract_tier
from rugby.webpages import get_footer_html

logger = logging.getLogger(__name__)

# Men's Premiership, men's Championship, and Women's Premiership: show "Current" for the
# latest season row instead of ordinal position until those competitions finish publicly.
# All other leagues (including women's Championship tiers, merit, Counties, etc.) show
# league position on the latest season once the pyramid season is complete for that level.
_POSITION_PENDING_TOP_TIERS = frozenset({"Premiership", "Women's Premiership"})


def _parse_rfu_team_id(url: str | None) -> int | None:
    """Numeric id from ``team=`` in an RFU team profile URL, if present."""
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    vals = params.get("team", [])
    if vals and vals[0].isdigit():
        return int(vals[0])
    return None


def _build_canonical_page_key_lookup(
    pairs: Iterable[tuple[str, int | None]],
) -> Callable[[str, int | None], str]:
    """Resolver mapping ``(display_name, team_id)`` → canonical page key.

    Two observations are treated as the same team when they share a display
    name or an RFU ``team=`` id, transitively. So ``(name_a, id_a)``,
    ``(name_a, id_b)`` and ``(name_b, id_b)`` all collapse to one bucket.

    Each canonical key is the smallest id in the connected component when any
    observation in that component carries an id, otherwise the alphabetically
    smallest display name. The choice is deterministic so the same dataset
    always produces the same set of page files.
    """
    parent: dict[tuple[str, Any], tuple[str, Any]] = {}

    def find(node: tuple[str, Any]) -> tuple[str, Any]:
        if node not in parent:
            parent[node] = node
            return node
        root = node
        while parent[root] != root:
            root = parent[root]
        cur = node
        while parent[cur] != root:
            nxt = parent[cur]
            parent[cur] = root
            cur = nxt
        return root

    def union(a: tuple[str, Any], b: tuple[str, Any]) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for name, tid in pairs:
        find(("name", name))
        if tid is not None:
            find(("id", tid))
            union(("name", name), ("id", tid))

    root_members: defaultdict[tuple[str, Any], list[tuple[str, Any]]] = defaultdict(list)
    for node in list(parent):
        root_members[find(node)].append(node)

    root_to_canonical: dict[tuple[str, Any], str] = {}
    for root, members in root_members.items():
        ids = sorted(n[1] for n in members if n[0] == "id")
        if ids:
            root_to_canonical[root] = str(ids[0])
        else:
            names = sorted(n[1] for n in members if n[0] == "name")
            root_to_canonical[root] = names[0]

    def lookup(name: str, tid: int | None) -> str:
        if tid is not None:
            return root_to_canonical[find(("id", tid))]
        return root_to_canonical[find(("name", name))]

    return lookup


def _display_names_with_multiple_profiles(all_teams: dict[str, TeamData]) -> set[str]:
    """Display names that map to more than one aggregated team row (different RFU profiles)."""
    name_to_keys: defaultdict[str, set[str]] = defaultdict(set)
    for page_key, td in all_teams.items():
        n = td.get("name") or ""
        if n:
            name_to_keys[n].add(page_key)
    return {n for n, keys in name_to_keys.items() if len(keys) > 1}


def _team_page_output_filename(team_data: TeamData, ambiguous_display_names: set[str]) -> str:
    """``dist/teams/*.html`` path; add ``_<team_id>`` when the display name is shared by multiple profiles."""
    display = team_data.get("name") or ""
    if not display:
        return "unknown.html"
    if display in ambiguous_display_names:
        tid = _parse_rfu_team_id(team_data.get("url"))
        if tid is not None:
            return sanitize_team_name(display) + f"_{tid}.html"
    return team_name_to_filepath(display)


def _tier_display_number(tier_number: int) -> int:
    """Tier shown in league links; women's pyramid uses 101+ internally — show 1+ instead."""
    if tier_number >= 101:
        return tier_number - 100
    return tier_number


def _map_url_for_entry(entry: LeagueHistoryEntry) -> str | None:
    """Return a relative URL to the map page for a league history entry, or None."""
    season = entry["season"]
    is_prod = get_config().is_production

    if entry["is_merit"]:
        comp = entry["competition_key"]
        if is_prod:
            return f"/{season}/merit/{comp}/All_Tiers/"
        return f"../{season}/merit/{comp}/All_Tiers.html"

    # Use the tier name from extract_tier (stored in entry["tier"][1]) so the
    # URL matches the actual map filename generated by maps.py (including the
    # same Regional / Counties labels as the 2022+ pyramid for pre-2022 data).
    name = entry["tier"][1].replace(" ", "_")

    if is_prod:
        return f"/{season}/{name}/"
    return f"../{season}/{name}.html"


class LeagueHistoryEntry(TypedDict):
    """Entry for a team's participation in a league for a season."""

    season: str
    league: str
    league_url: str
    position: int
    league_team_count: int
    tier: tuple[int, str]  # (tier_number, tier_name)
    tier_display: str  # pyramid tier digit(s); women's 101+ shown without 100 offset
    is_merit: bool
    competition_key: str  # e.g. "CANDY", "" for pyramid
    # Display name observed for this team in this league/season. Differs from the
    # canonical TeamData["name"] when the team has been renamed — required to
    # look up distance-cache entries that were keyed by the historical name.
    team_name: str


class TeamData(TypedDict):
    """Aggregated data for a team across all seasons."""

    name: str | None
    url: str | None
    image_url: str | None
    address: str | None
    latitude: float | None
    longitude: float | None
    formatted_address: str | None
    league_history: list[LeagueHistoryEntry]
    # Display name → set of seasons that name was observed in. Used to render
    # the "Previously known as" line when an aggregated team has been renamed.
    name_seasons: dict[str, set[str]]


class TeamListEntry(TypedDict):
    """Entry for team in the searchable index."""

    file: str
    name: str
    image_url: str


def collect_all_teams_data() -> dict[str, TeamData]:
    """
    Collect all team data from geocoded files across all seasons.

    Two observations are merged into one aggregated team row when they share
    either a display name or an RFU ``team=`` id (transitively). This catches
    clubs that renamed (e.g. Newcastle Falcons → Newcastle Red Bulls) where
    the id is stable across name changes, as well as renumbered profiles
    where the display name links two ids together.

    Returns:
        Dictionary mapping a stable canonical page key to aggregated team
        data. Within each season-chronological walk, later observations
        overwrite scalar fields, so name/url/image/address reflect the most
        recent appearance for the merged team.
    """
    geocoded_dir = DATA_DIR / "geocoded_teams"

    if not geocoded_dir.exists():
        return {}

    season_dirs = [
        d for d in sorted(geocoded_dir.iterdir()) if d.is_dir() and re.match(r"\d{4}-\d{4}", d.name)
    ]

    # First pass: gather every (display_name, team_id) pair so we can build
    # the transitive grouping before aggregating. We deliberately read each
    # league file twice rather than holding all parsed JSON in memory.
    name_id_pairs: set[tuple[str, int | None]] = set()
    for season_dir in season_dirs:
        for league_file in season_dir.rglob("*.json"):
            with open(league_file, encoding="utf-8") as f:
                league_data: GeocodedLeague = json.load(f)
            for team in league_data["teams"]:
                name_id_pairs.add((team["name"], _parse_rfu_team_id(team.get("url"))))

    resolve_page_key = _build_canonical_page_key_lookup(name_id_pairs)

    teams_data: defaultdict[str, TeamData] = defaultdict(
        lambda: TeamData(
            name=None,
            url=None,
            image_url=None,
            address=None,
            latitude=None,
            longitude=None,
            formatted_address=None,
            league_history=[],
            name_seasons={},
        )
    )

    for season_dir in season_dirs:
        season = season_dir.name

        for league_file in season_dir.rglob("*.json"):
            with open(league_file, encoding="utf-8") as f:
                league_data = json.load(f)

            league_name = league_data["league_name"]
            league_team_count = len(league_data["teams"])

            for position, team in enumerate(league_data["teams"], start=1):
                team_name = team["name"]
                team_url = team.get("url")
                page_key = resolve_page_key(team_name, _parse_rfu_team_id(team_url))

                teams_data[page_key]["name"] = team_name
                teams_data[page_key]["url"] = team_url
                teams_data[page_key]["image_url"] = team.get("image_url")
                teams_data[page_key]["name_seasons"].setdefault(team_name, set()).add(season)

                addr = team.get("address")
                lat = team.get("latitude")
                lon = team.get("longitude")
                fmt_addr = team.get("formatted_address")
                if addr:
                    teams_data[page_key]["address"] = addr
                if lat is not None:
                    teams_data[page_key]["latitude"] = lat
                if lon is not None:
                    teams_data[page_key]["longitude"] = lon
                if fmt_addr:
                    teams_data[page_key]["formatted_address"] = fmt_addr

                rel_path = league_file.relative_to(season_dir).as_posix()
                tier = extract_tier(rel_path, season)
                is_merit = rel_path.startswith("merit/")
                comp_key = ""
                if is_merit:
                    comp_key = rel_path.split("/")[1]
                    comp_display = comp_key.replace("_", " ")
                    tier_display = f"{comp_display} {_tier_display_number(tier[0])}"
                else:
                    tier_display = f"{_tier_display_number(tier[0])}"
                teams_data[page_key]["league_history"].append(
                    LeagueHistoryEntry(
                        season=season,
                        league=league_name,
                        league_url=league_data["league_url"],
                        position=position,
                        league_team_count=league_team_count,
                        tier=tier,
                        tier_display=tier_display,
                        is_merit=is_merit,
                        competition_key=comp_key,
                        team_name=team_name,
                    )
                )

    return dict(teams_data)


def get_all_seasons() -> list[str]:
    """Get all available seasons from geocoded team data directories."""
    geocoded_dir = DATA_DIR / "geocoded_teams"
    if not geocoded_dir.exists():
        return []

    seasons = [
        season_dir.name
        for season_dir in geocoded_dir.iterdir()
        if season_dir.is_dir() and re.match(r"\d{4}-\d{4}", season_dir.name)
    ]
    return sorted(seasons, reverse=True)


def _format_season_ranges(seasons: Iterable[str]) -> str:
    """Compress sorted ``YYYY-YYYY`` season strings into comma-separated ranges.

    Two seasons are consecutive when the second starts the year after the first
    ends — e.g. ``2000-2001`` and ``2001-2002`` collapse to ``2000-2001 to
    2001-2002``. Non-contiguous chunks stay separated by commas.
    """
    sorted_seasons = sorted({s for s in seasons if s})
    if not sorted_seasons:
        return ""

    def start_year(s: str) -> int:
        return int(s.split("-", 1)[0])

    ranges: list[tuple[str, str]] = []
    range_start = sorted_seasons[0]
    prev = sorted_seasons[0]
    for s in sorted_seasons[1:]:
        if start_year(s) == start_year(prev) + 1:
            prev = s
            continue
        ranges.append((range_start, prev))
        range_start = s
        prev = s
    ranges.append((range_start, prev))

    return ", ".join(start if start == end else f"{start} to {end}" for start, end in ranges)


def _format_previous_names(team_data: TeamData) -> str:
    """Inline string for the "Previously known as" row, ``""`` when not renamed.

    Past names are listed most-recent-use first so the entry directly preceding
    the current name appears at the front. Each name is followed by the
    compressed season range(s) it was used in.
    """
    name_seasons = team_data.get("name_seasons") or {}
    current_name = team_data.get("name") or ""
    past = [(n, seasons) for n, seasons in name_seasons.items() if n != current_name and seasons]
    if not past:
        return ""
    past.sort(key=lambda item: max(item[1]), reverse=True)
    parts = [f"{escape(name)} ({escape(_format_season_ranges(seasons))})" for name, seasons in past]
    return "; ".join(parts)


def _format_team_travel_distance_km(team_distances: TeamTravelDistances | None) -> str:
    if team_distances is None:
        return "N/A"
    avg_dist = team_distances.get("avg_distance_km")
    total_dist = team_distances.get("total_distance_km")
    if avg_dist is not None and total_dist is not None:
        return f"{avg_dist:.1f} km / {total_dist:.0f} km"
    if avg_dist is not None:
        return f"{avg_dist:.1f} km avg"
    if total_dist is not None:
        return f"{total_dist:.0f} km total"
    return "N/A"


def _format_team_travel_time_min(team_distances: TeamTravelDistances | None) -> str:
    """Format avg/total duration when present (from routed + offshore corridor model)."""
    if team_distances is None:
        return "N/A"
    avg_m = team_distances.get("avg_duration_min")
    total_m = team_distances.get("total_duration_min")
    if avg_m is not None and total_m is not None:
        return f"{round(avg_m)} min / {round(total_m)} min"
    if avg_m is not None:
        return f"{round(avg_m)} min avg"
    if total_m is not None:
        return f"{round(total_m)} min total"
    return "—"


def build_club_index(all_teams: dict[str, TeamData]) -> dict[str, list[str]]:
    """Pre-build an index of co-located teams for fast club lookups.

    Groups teams by address and coordinates, then builds a mapping from
    each page key to sibling page keys at the same location.

    Returns:
        Dictionary mapping page key -> sorted list of other page keys at same location
    """
    address_groups: defaultdict[str, list[str]] = defaultdict(list)
    coord_groups: defaultdict[tuple[float, float], list[str]] = defaultdict(list)

    for page_key, data in all_teams.items():
        addr = data.get("address")
        lat = data.get("latitude")
        lon = data.get("longitude")
        if addr:
            address_groups[addr].append(page_key)
        if lat is not None and lon is not None:
            coord_groups[(lat, lon)].append(page_key)

    club_index: dict[str, list[str]] = {}
    for page_key in all_teams:
        siblings: set[str] = set()
        data = all_teams[page_key]
        addr = data.get("address")
        lat = data.get("latitude")
        lon = data.get("longitude")
        if addr and addr in address_groups:
            siblings.update(address_groups[addr])
        if lat is not None and lon is not None:
            key = (lat, lon)
            if key in coord_groups:
                siblings.update(coord_groups[key])
        siblings.discard(page_key)
        club_index[page_key] = sorted(siblings)

    return club_index


def _team_page_structured_data(
    team_name: str,
    team_data: TeamData,
    page_url: str,
) -> str:
    """JSON-LD SportsTeam block — links this URL to the club entity in Google's knowledge graph.

    Uses `sameAs` to tie your page to the RFU profile so Google understands both
    URLs describe the same organisation, even when the slug omits "RFC".
    """
    payload: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "SportsTeam",
        "name": team_name,
        "sport": "Rugby union",
        "url": page_url,
    }
    rfu_url = team_data.get("url")
    if rfu_url:
        payload["sameAs"] = rfu_url
    lat, lon = team_data.get("latitude"), team_data.get("longitude")
    if lat is not None and lon is not None:
        payload["location"] = {
            "@type": "Place",
            "geo": {
                "@type": "GeoCoordinates",
                "latitude": float(lat),
                "longitude": float(lon),
            },
        }
    addr = team_data.get("formatted_address") or team_data.get("address")
    if addr:
        payload["address"] = {"@type": "PostalAddress", "streetAddress": addr}
    return json.dumps(payload, ensure_ascii=True)


def get_team_page_html(
    page_key: str,
    team_data: TeamData,
    all_teams: dict[str, TeamData],
    club_index: dict[str, list[str]],
    travel_distances_by_season: dict[str, TravelDistances],
    all_seasons: list[str],
    ambiguous_display_names: set[str],
) -> str:
    """Generate HTML content for a team's individual page."""

    team_name = team_data.get("name") or page_key

    club_teams = club_index.get(page_key, [])

    # Sort league history by season (most recent first)
    league_history: list[LeagueHistoryEntry] = sorted(
        team_data["league_history"], key=lambda x: x["season"], reverse=True
    )

    # Group by season for display
    seasons_by_year: defaultdict[str, list[LeagueHistoryEntry]] = defaultdict(list)
    for entry in league_history:
        seasons_by_year[entry["season"]].append(entry)

    num_seasons = len({e["season"] for e in league_history})
    if league_history:
        latest = league_history[0]
        league_nm = latest["league"]
        tier_raw = latest["tier"][0]
        n = _tier_display_number(tier_raw)
        tier_scope = "English women's rugby" if tier_raw >= 101 else "English rugby"
        meta_desc = escape(
            f"{team_name}: English rugby union club—{league_nm} (level {n} of "
            f"{tier_scope}). Ground, league history across {num_seasons} seasons, tier maps, "
            f"and travel stats. {BRAND}."
        )
    else:
        meta_desc = escape(
            f"{team_name}: English rugby union club profile—ground address, league history "
            f"across {num_seasons} seasons, links to seasonal tier maps, and travel statistics. "
            f"{BRAND}."
        )
    page_title = escape(f"{team_name} | League History | {BRAND}")

    is_prod = get_config().is_production
    teams_index_href = "./" if is_prod else "./index.html"

    team_file = _team_page_output_filename(team_data, ambiguous_display_names)
    # Canonical URL is only meaningful in production; omit it in local dev builds.
    canonical_url = f"{SITE_BASE_URL}/teams/{team_file}" if is_prod else ""

    head_extra = ""
    if canonical_url:
        cu = escape(canonical_url)
        # canonical: tells Google which URL to index when the same page is
        # reachable via multiple paths (e.g. /teams/Oxford and /teams/Oxford.html).
        head_extra += f'    <link rel="canonical" href="{cu}">\n'
        # og:url: the definitive share URL for this page.
        head_extra += f'    <meta property="og:url" content="{cu}" />\n'
        head_extra += og_image_meta_html(escape(OG_DEFAULT_IMAGE), indent="    ") + "\n"
        head_extra += f"    {get_twitter_card_meta()}\n"
        head_extra += (
            breadcrumb_ld_script(
                [
                    ("Home", f"{SITE_BASE_URL}/"),
                    ("All Teams", f"{SITE_BASE_URL}/teams/"),
                    (team_name, canonical_url),
                ],
                indent="    ",
            )
            + "\n"
        )
        # JSON-LD SportsTeam — link this page to the RFU entity and coordinates.
        head_extra += (
            '    <script type="application/ld+json">'
            f"{_team_page_structured_data(team_name, team_data, canonical_url)}"
            "</script>\n"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{meta_desc}">
    <meta property="og:title" content="{page_title}" />
    <meta property="og:description" content="{meta_desc}" />
    <meta property="og:type" content="website" />
    <title>{page_title}</title>
{head_extra}    <link rel="stylesheet" href="../styles.css">
    {get_favicon_html(depth=1)}
    <style>
        .team-header {{
            text-align: center;
            margin-bottom: 2em;
        }}
        .team-logo {{
            max-width: 150px;
            max-height: 150px;
            margin: 1em auto;
            display: block;
        }}
        .info-row {{
            margin: 0.5em 0;
            line-height: 1.8;
        }}
        .info-label {{
            font-weight: 600;
            color: var(--text-muted);
        }}
        .club-teams {{
            list-style: none;
            padding: 0;
        }}
        .club-teams li {{
            margin: 0.5em 0;
        }}
        .league-history-table {{
            width: 100%;
            border-collapse: collapse;
            /* Never force the table wider than its card: min-width 760px caused a horizontal
               scrollbar on common viewport widths (section padding shrinks the inner width). */
            min-width: min(100%, 760px);
        }}
        .league-history-table th {{
            background: var(--bg-card-alt);
            padding: 0.8em;
            text-align: left;
            font-weight: 600;
            color: var(--text-heading);
            border-bottom: 2px solid var(--accent);
        }}
        .league-history-table td {{
            padding: 0.8em;
            border-bottom: 1px solid var(--border);
        }}
        .league-history-table tr:hover {{
            background: var(--bg-card-alt);
        }}
        .league-history-table .distance-cell {{
            font-variant-numeric: tabular-nums;
            color: var(--text-muted);
        }}
        .league-history-table .league-link {{
            display: inline-block;
            padding: 0.4em 0.6em;
            font-size: 0.95em;
        }}
        .league-history-table .map-cell {{
            width: 2.5em;
            min-width: 2.25em;
            text-align: center;
            padding-left: 0.5em;
            padding-right: 1.65em;
            box-sizing: content-box;
        }}
        .position {{
            font-weight: 600;
            color: var(--accent);
        }}
        .address {{
            color: var(--text-muted);
            font-style: italic;
        }}
        .distance-header-full {{
            display: inline;
        }}
        .distance-header-short {{
            display: none;
        }}

        .time-header-full {{
            display: inline;
        }}
        .time-header-short {{
            display: none;
        }}

        /* Responsive styles for smaller screens */
        @media (max-width: 768px) {{
            .league-history-table {{
                min-width: 500px;
                font-size: 0.9em;
            }}
            .league-history-table th,
            .league-history-table td {{
                padding: 0.6em 0.4em;
            }}
        }}

        @media (max-width: 480px) {{
            .league-history-table {{
                min-width: 450px;
                font-size: 0.85em;
            }}
            .league-history-table th,
            .league-history-table td {{
                padding: 0.5em 0.3em;
            }}
            .distance-header-full {{
                display: none;
            }}
            .distance-header-short {{
                display: inline;
            }}
            .time-header-full {{
                display: none;
            }}
            .time-header-short {{
                display: inline;
            }}
        }}
    </style>
    {get_google_analytics_script()}
</head>
<body>
    <div class="back-link">
        <a href="{escape(teams_index_href)}">← All Teams</a>
    </div>

    <div class="team-header">
        <h1>{escape(team_name)}</h1>
"""

    # Add logo if available
    image_url = team_data.get("image_url")
    if image_url:
        html += f'        <img src="{escape(image_url)}" alt="{escape(team_name)} logo"'
        html += ' onerror="this.onerror=null; this.src=\'https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg\'" class="team-logo">\n'

    html += """    </div>
"""

    # Basic Info Section
    html += """    <div class="info-section">
        <h2>Basic Information</h2>
"""

    if team_data.get("formatted_address") or team_data.get("address"):
        address = team_data.get("formatted_address") or team_data.get("address")
        html += f'        <div class="info-row"><span class="info-label">Address:</span> <span class="address">{escape(address or "")}</span></div>\n'

    previous_names_html = _format_previous_names(team_data)
    if previous_names_html:
        html += f'        <div class="info-row"><span class="info-label">Previously known as:</span> {previous_names_html}</div>\n'

    team_url = team_data.get("url")
    if team_url:
        html += f'        <div class="info-row"><span class="info-label">RFU Profile:</span> <a href="{escape(team_url)}" target="_blank">View on England Rugby</a></div>\n'

    html += """    </div>
"""

    # Club Teams Section
    if club_teams:
        html += """    <div class="info-section">
        <h2>Other Teams at This Club</h2>
        <ul class="club-teams">
"""
        for sibling_key in club_teams:
            sib = all_teams[sibling_key]
            sib_name = sib.get("name") or sibling_key
            sib_file = _team_page_output_filename(sib, ambiguous_display_names)
            html += f'            <li><a href="{escape(sib_file)}" class="card-link card-inline">{escape(sib_name)}</a></li>\n'

        html += """        </ul>
    </div>
"""

    # League History Section
    if league_history:
        html += """    <div class="info-section">
        <h2>League History</h2>
        <div class="table-wrapper">
        <table class="league-history-table">
            <thead>
                <tr>
                    <th>Season</th>
                    <th>Tier: League</th>
                    <th>Position</th>
                    <th><span class="distance-header-full">Travel distance (avg / total)</span><span class="distance-header-short">Dist avg/tot</span></th>
                    <th><span class="time-header-full">Travel time (avg / total)</span><span class="time-header-short">Time avg/tot</span></th>
                    <th class="map-cell"></th>
                </tr>
            </thead>
            <tbody>
"""

        for season in all_seasons:
            season_entries = seasons_by_year.get(season, [])

            # If team has no league for this season, render a blank row.
            if not season_entries:
                html += f"""                <tr>
                    <td>{season}</td>
                    <td>&nbsp;</td>
                    <td>&nbsp;</td>
                    <td class="distance-cell">&nbsp;</td>
                    <td class="distance-cell">&nbsp;</td>
                    <td class="map-cell"></td>
                </tr>
"""
                continue

            for entry in season_entries:
                league: str = entry["league"]
                position: int = entry["position"]
                n_in_league: int = entry["league_team_count"]

                suppress_position_latest = (
                    season == all_seasons[0] and league in _POSITION_PENDING_TOP_TIERS
                )
                if suppress_position_latest:
                    position_display = '<span class="address">Current</span>'
                else:
                    position_display = f'<span class="position">#{position}/{n_in_league}</span>'

                team_td: TeamTravelDistances | None = None
                if season in travel_distances_by_season:
                    season_data = travel_distances_by_season[season]
                    if "teams" in season_data:
                        # Use the name observed for this row — the cache was keyed
                        # by whichever display name the team had that season, so
                        # the current name won't match for renamed teams.
                        raw_td = season_data["teams"].get(entry["team_name"])
                        if raw_td is not None:
                            team_td = raw_td

                travel_km = escape(_format_team_travel_distance_km(team_td))
                travel_time = escape(_format_team_travel_time_min(team_td))

                tier_display: str = entry["tier_display"]
                league_link: str = (
                    f'<a href="{escape(entry["league_url"])}" class="card-link league-link">{escape(tier_display)}: {escape(league)}</a>'
                )

                map_url = _map_url_for_entry(entry)
                map_cell = (
                    f'<a href="{escape(map_url)}" title="View on map">&#x1f5fa;</a>'
                    if map_url
                    else ""
                )

                html += f"""                <tr>
                    <td>{season}</td>
                    <td>{league_link}</td>
                    <td>{position_display}</td>
                    <td class="distance-cell">{travel_km}</td>
                    <td class="distance-cell">{travel_time}</td>
                    <td class="map-cell">{map_cell}</td>
                </tr>
"""

        html += """            </tbody>
        </table>
        </div>
    </div>
"""

    # Footer
    html += f"""
{get_footer_html()}
</body>
</html>
"""

    return html


def load_travel_distances() -> dict[str, TravelDistances]:
    """Load per-season travel stats from ``data/rugby/distance_cache/<season>.json``.

    Produced by ``python -m rugby.distances`` — includes km plus ``avg_duration_min`` /
    ``total_duration_min`` when the routed cache resolves every league pair.
    """
    distances_dir = DATA_DIR / "distance_cache"
    travel_distances_by_season: dict[str, TravelDistances] = {}

    if not distances_dir.exists():
        return {}

    for distance_file in distances_dir.glob("*.json"):
        season: str = distance_file.stem  # e.g., "2018-2019"

        try:
            with open(distance_file, encoding="utf-8") as f:
                data: TravelDistances = json.load(f)
                travel_distances_by_season[season] = data
        except Exception as e:
            logger.warning("Could not load distances for %s: %s", season, e)

    return travel_distances_by_season


def generate_team_pages() -> dict[str, TeamData]:
    """Generate individual HTML pages for all teams. Returns collected team data."""
    logger.info("Generating individual team pages...")

    # Collect all team data
    logger.info("  Collecting team data from all seasons...")
    all_teams = collect_all_teams_data()

    if not all_teams:
        logger.warning("  No team data found!")
        return {}

    logger.info("  Found %d unique teams", len(all_teams))

    # Get full season list so team history tables include blank rows for missing years
    all_seasons = get_all_seasons()

    # Load travel distances
    logger.info("  Loading travel distances...")
    travel_distances_by_season = load_travel_distances()
    logger.info("  Loaded distances for %d seasons", len(travel_distances_by_season))

    # Pre-build club index for fast co-location lookups
    logger.info("  Building club index...")
    club_index = build_club_index(all_teams)

    # Create teams directory
    teams_dir = DIST_DIR / "teams"
    teams_dir.mkdir(parents=True, exist_ok=True)

    ambiguous = _display_names_with_multiple_profiles(all_teams)

    # Generate page for each team
    generated_count = 0
    for page_key, team_data in all_teams.items():
        try:
            html_content = get_team_page_html(
                page_key,
                team_data,
                all_teams,
                club_index,
                travel_distances_by_season,
                all_seasons,
                ambiguous,
            )

            filename = _team_page_output_filename(team_data, ambiguous)
            filepath = teams_dir / filename

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_content)

            generated_count += 1

        except Exception as e:
            logger.error("Error generating page for %s: %s", page_key, e)

    logger.info("Generated %d team pages in %s", generated_count, teams_dir)
    return all_teams


RFU_FALLBACK_ICON = "https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg"


def generate_teams_index(all_teams: dict[str, TeamData] | None = None) -> None:
    """Generate the teams/index.html page with searchable list of all teams."""
    teams_dir = DIST_DIR / "teams"
    if not teams_dir.exists():
        logger.warning("Teams directory doesn't exist")
        return

    team_files = sorted(teams_dir.glob("*.html"))
    if not team_files:
        logger.warning("No team HTML files found")
        return

    teams_list: list[TeamListEntry] = []
    if all_teams is not None:
        ambiguous = _display_names_with_multiple_profiles(all_teams)
        for _pk, td in all_teams.items():
            display_name = td.get("name") or ""
            if not display_name:
                continue
            fn = _team_page_output_filename(td, ambiguous)
            if not (teams_dir / fn).exists():
                continue
            teams_list.append(
                TeamListEntry(
                    file=fn,
                    name=display_name,
                    image_url=td.get("image_url") or RFU_FALLBACK_ICON,
                )
            )
    else:
        for file_path in team_files:
            if file_path.name == "index.html":
                continue
            filename: str = file_path.name[:-5]  # Remove .html
            display_name: str = filename.replace("_", " ")
            image_url = RFU_FALLBACK_ICON
            teams_list.append(
                TeamListEntry(file=file_path.name, name=display_name, image_url=image_url)
            )

    if not teams_list:
        logger.warning("No team entries to index")
        return

    # Sort by club name (remove II/III/IV suffixes for grouping), then by display name
    # so e.g. 1st XV appears before II/2nd XV; filename breaks ties between identical labels.
    teams_list.sort(
        key=lambda x: (
            team_name_to_club_name(x["name"]).lower(),
            x["name"].lower(),
            x["file"].lower(),
        ),
    )

    teams_js = ",\n            ".join(
        f'{{file: "{escape(t["file"])}", name: "{escape(t["name"])}", img: "{escape(t["image_url"])}"}}'
        for t in teams_list
    )

    teams_page_title = f"All Teams | {BRAND}"
    teams_page_desc = (
        f"Search {len(teams_list)} English rugby union clubs by name. "
        "Ground addresses, RFU league history, and links to interactive tier maps."
    )

    teams_head_extra = ""
    if get_config().is_production:
        page_url = f"{SITE_BASE_URL}/teams/"
        teams_head_extra = (
            f'    <link rel="canonical" href="{escape(page_url)}">\n'
            f'    <meta property="og:url" content="{escape(page_url)}" />\n'
            + og_image_meta_html(escape(OG_DEFAULT_IMAGE), indent="    ")
            + "\n"
            f"    {get_twitter_card_meta()}\n"
            + breadcrumb_ld_script(
                [("Home", f"{SITE_BASE_URL}/"), ("All Teams", page_url)],
                indent="    ",
            )
            + "\n"
        )

    is_ix_prod = get_config().is_production
    home_href_teams_ix = "../" if is_ix_prod else "../index.html"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{escape(teams_page_desc)}">
    <meta property="og:title" content="{escape(teams_page_title)}" />
    <meta property="og:description" content="{escape(teams_page_desc)}" />
    <meta property="og:type" content="website" />
{teams_head_extra}    <title>{escape(teams_page_title)}</title>
    <link rel="stylesheet" href="../styles.css">
    {get_favicon_html(depth=1)}
    <style>
        .search-box {{
            text-align: center;
            margin: 2em 0;
            width: 100%;
            max-width: 100%;
            box-sizing: border-box;
        }}
        #searchInput {{
            width: 100%;
            max-width: 500px;
            box-sizing: border-box;
            padding: 12px 20px;
            font-size: 16px;
            border: 2px solid var(--border);
            border-radius: 25px;
            outline: none;
            transition: border-color 0.2s;
            background: var(--bg-card);
            color: var(--text);
        }}
        #searchInput:focus {{
            border-color: var(--accent);
        }}
        .team-count {{
            text-align: center;
            color: var(--text-muted);
            margin: 1em 0 2em 0;
        }}
        .teams-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 1em;
            margin: 2em 0;
        }}
        .team-card a {{
            font-size: 1.05em;
            display: flex;
            align-items: center;
            gap: 0.6em;
        }}
        .team-card__logo {{
            width: 28px;
            height: 28px;
            border-radius: 50%;
            flex-shrink: 0;
            object-fit: cover;
        }}
        .no-results {{
            text-align: center;
            color: var(--text-muted);
            font-size: 1.2em;
            margin: 3em 0;
            display: none;
        }}
    </style>

    {get_google_analytics_script()}

</head>
<body class="wide-layout">
    <div class="back-link">
        <a href="{home_href_teams_ix}">← Home</a>
    </div>

    <h1>All English Rugby Union Teams</h1>

    <div class="search-box">
        <input type="text" id="searchInput" placeholder="Search teams...">
    </div>

    <div class="team-count">
        <span id="visibleCount"></span> teams
    </div>

    <div class="teams-grid" id="teamsGrid"></div>

    <div class="no-results" id="noResults">No teams found matching your search.</div>

    <div class="footer">
        <p><a href="https://github.com/jmforsythe/Rugby-Map">View on GitHub</a></p>
        <p>Data sources: <a href="https://www.englandrugby.com/">England Rugby (RFU)</a> <a href="https://geoportal.statistics.gov.uk/">ONS</a> <a href="https://nominatim.openstreetmap.org/">OpenStreetMap</a></p>
    </div>

    <script>
        const teams = [
            {teams_js}
        ];

        const teamsGrid = document.getElementById('teamsGrid');
        const searchInput = document.getElementById('searchInput');
        const visibleCount = document.getElementById('visibleCount');
        const noResults = document.getElementById('noResults');

        function displayTeams(filteredTeams) {{
            teamsGrid.innerHTML = '';

            if (filteredTeams.length === 0) {{
                noResults.style.display = 'block';
                teamsGrid.style.display = 'none';
            }} else {{
                noResults.style.display = 'none';
                teamsGrid.style.display = 'grid';

                filteredTeams.forEach(team => {{
                    const card = document.createElement('div');
                    card.className = 'card team-card';
                    const fallback = '{RFU_FALLBACK_ICON}';
                    card.innerHTML = `<a href="${{team.file}}"><img src="${{team.img}}" class="team-card__logo" loading="lazy" onerror="this.onerror=null;this.src='${{fallback}}'">${{team.name}}</a>`;
                    teamsGrid.appendChild(card);
                }});
            }}

            visibleCount.textContent = filteredTeams.length;
        }}

        function filterTeams() {{
            const searchTerm = searchInput.value.toLowerCase();
            const filtered = teams.filter(team =>
                team.name.toLowerCase().includes(searchTerm)
            );
            displayTeams(filtered);
        }}

        searchInput.addEventListener('input', filterTeams);

        // Initial display
        displayTeams(teams);
    </script>
</body>
</html>
"""

    index_path = teams_dir / "index.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    logger.info("Generated teams index with %d teams at %s", len(teams_list), index_path)


def main() -> None:
    """Main entry point for generating team pages."""
    parser = argparse.ArgumentParser(description="Generate index.html pages for rugby maps.")
    parser.add_argument(
        "--production", action="store_true", help="Change folder structure for production"
    )
    args = parser.parse_args()
    setup_logging()
    if args.production:
        set_config(is_production=True)

    all_teams = generate_team_pages()
    generate_teams_index(all_teams)


if __name__ == "__main__":
    main()
