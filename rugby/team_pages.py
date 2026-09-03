from __future__ import annotations

import argparse
import json
import logging
import re
import urllib.parse
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from core import (
    Fixture,
    FixtureLeague,
    League,
    TeamTravelDistances,
    TravelDistances,
    get_config,
    get_favicon_html,
    get_google_analytics_script,
    get_stylesheet_href,
    get_twitter_card_meta,
    sanitize_team_name,
    set_config,
    setup_logging,
)
from core.config import CURRENT_SEASON, DIST_DIR, EARLIEST_SEASON
from core.json_utils import write_compact_json
from rugby import BRAND, DATA_DIR, rfu_team_only_url
from rugby.addresses import team_name_to_club_name
from rugby.clubs import iter_geocoded_leagues, load_team_club_map, resolve_club_name
from rugby.constituent_bodies import get_constituent_body
from rugby.distance_lookup import DistanceLookup
from rugby.distances import enrich_island_excl_stats
from rugby.seo import BASE_URL as SITE_BASE_URL
from rugby.seo import OG_DEFAULT_IMAGE, absolute_url, breadcrumb_ld_script, og_image_meta_html
from rugby.tiers import extract_tier
from rugby.travel_display import format_team_travel_distance_km, format_team_travel_time_min
from rugby.webpages import get_footer_html

logger = logging.getLogger(__name__)


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


def _team_page_slug(team_data: TeamData, ambiguous_display_names: set[str]) -> str:
    """Bare team-page slug (no extension); ``_<team_id>`` suffix when the display
    name is shared by multiple profiles."""
    display = team_data.get("name") or ""
    if not display:
        return "unknown"
    if display in ambiguous_display_names:
        tid = _parse_rfu_team_id(team_data.get("url"))
        if tid is not None:
            return sanitize_team_name(display) + f"_{tid}"
    return sanitize_team_name(display)


def _team_page_output_path(teams_dir: Path, slug: str) -> Path:
    """``dist/teams/{slug}/index.html`` in production, else ``dist/teams/{slug}.html``."""
    if get_config().is_production:
        return teams_dir / slug / "index.html"
    return teams_dir / f"{slug}.html"


def _team_page_href(slug: str) -> str:
    """Public href for a team page from ``teams/index.html``: ``{slug}/`` in production, else ``{slug}.html``."""
    if get_config().is_production:
        return f"{slug}/"
    return f"{slug}.html"


def _team_page_sibling_href(slug: str) -> str:
    """Relative href from one team page to another under ``dist/teams/``."""
    if get_config().is_production:
        return f"../{slug}/"
    return f"{slug}.html"


def _team_page_asset_depth() -> int:
    """Directory depth of team pages relative to ``dist/`` (1 flat dev, 2 nested prod)."""
    return 2 if get_config().is_production else 1


def _team_page_output_filename(team_data: TeamData, ambiguous_display_names: set[str]) -> str:
    """``dist/teams/*.html`` filename (dev-mode naming); add ``_<team_id>`` when the
    display name is shared by multiple profiles."""
    slug = _team_page_slug(team_data, ambiguous_display_names)
    return f"{slug}.html"


def build_team_info_page_filenames() -> dict[int, str]:
    """Map RFU ``team=`` id to canonical team page href under dist (``teams/`` relative)."""
    all_teams = collect_all_teams_data()
    ambiguous = _display_names_with_multiple_profiles(all_teams)
    id_to_page_key = build_id_to_page_key(all_teams)
    by_page_key = {
        page_key: _team_page_href(_team_page_slug(team_data, ambiguous))
        for page_key, team_data in all_teams.items()
    }
    return {
        team_id: by_page_key[page_key]
        for team_id, page_key in id_to_page_key.items()
        if page_key in by_page_key
    }


def team_info_page_filename(
    team_url: str | None,
    team_name: str,
    lookup: dict[int, str] | None = None,
) -> str:
    """Canonical team info page href, resolving renames via RFU team id."""
    pages = lookup if lookup is not None else build_team_info_page_filenames()
    team_id = _parse_rfu_team_id(team_url)
    if team_id is not None and team_id in pages:
        return pages[team_id]
    return _team_page_href(sanitize_team_name(team_name))


def discover_team_rename_redirects() -> list[tuple[str, str]]:
    """``/teams/old.html`` → ``/teams/current/`` (or ``.html`` in dev) for every observed rename."""
    all_teams = collect_all_teams_data()
    ambiguous = _display_names_with_multiple_profiles(all_teams)
    pairs: list[tuple[str, str]] = []
    for team_data in all_teams.values():
        current_slug = _team_page_slug(team_data, ambiguous)
        current_href = _team_page_href(current_slug)
        current_name = team_data.get("name") or ""
        for old_name in team_data.get("name_seasons") or {}:
            if old_name == current_name:
                continue
            old_slug = sanitize_team_name(old_name)
            if old_slug != current_slug:
                pairs.append((f"/teams/{old_slug}.html", f"/teams/{current_href}"))
    return pairs


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
    from core.slugs import slugify_content

    name = slugify_content(entry["tier"][1])

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
    # RFU Constituent Body (county union / services body) the club is
    # affiliated to, e.g. "Surrey Rugby". None when no confident match was
    # found in the club/CB export (see rugby.constituent_bodies).
    constituent_body: str | None
    league_history: list[LeagueHistoryEntry]
    # RFU ``team=`` ids observed for this aggregated profile (renames / renumbers).
    team_ids: set[int]
    # Display name → set of seasons that name was observed in. Used to render
    # the "Previously known as" line when an aggregated team has been renamed.
    name_seasons: dict[str, set[str]]


class TeamFixtureEntry(TypedDict):
    """One fixture row for a team page (home or away)."""

    season: str
    league_name: str
    date: str
    time: str
    is_home: bool
    opponent_id: int
    match_url: str
    home_score: NotRequired[int | None]
    away_score: NotRequired[int | None]
    status: NotRequired[str]


_FIXTURE_STATUS_LABELS: dict[str, str] = {
    "HWO": "Home walkover",
    "AWO": "Away walkover",
}


class TeamListEntry(TypedDict):
    """Entry for team in the searchable index."""

    file: str
    name: str
    image_url: str


def collect_all_teams_data() -> dict[str, TeamData]:
    """
    Collect all team data from league_data, joined with club address/geocode
    data via rugby.clubs, across all seasons.

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
    league_data_dir = DATA_DIR / "league_data"

    if not league_data_dir.exists():
        return {}

    season_dirs = [
        d
        for d in sorted(league_data_dir.iterdir())
        if d.is_dir() and re.match(r"\d{4}-\d{4}", d.name) and d.name >= EARLIEST_SEASON
    ]

    # First pass: gather every (display_name, team_id) pair so we can build
    # the transitive grouping before aggregating. We deliberately read each
    # league file twice rather than holding all parsed JSON in memory.
    name_id_pairs: set[tuple[str, int | None]] = set()
    for season_dir in season_dirs:
        for _league_file, league_data in iter_geocoded_leagues(season_dir):
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
            constituent_body=None,
            league_history=[],
            team_ids=set(),
            name_seasons={},
        )
    )

    for season_dir in season_dirs:
        season = season_dir.name

        for league_file, league_data in iter_geocoded_leagues(season_dir):
            rel_path = league_file.relative_to(season_dir).as_posix()
            if rel_path.startswith("county_championship/"):
                # Representative county sides, not club pyramid/merit teams.
                continue
            league_name = league_data["league_name"]
            league_team_count = len(league_data["teams"])

            for position, team in enumerate(league_data["teams"], start=1):
                team_name = team["name"]
                team_url = team.get("url")
                page_key = resolve_page_key(team_name, _parse_rfu_team_id(team_url))

                teams_data[page_key]["name"] = team_name
                teams_data[page_key]["url"] = team_url
                teams_data[page_key]["image_url"] = team.get("image_url")
                # Keep the most recent successful match rather than blanking it
                # out if a later season's display name happens not to match.
                cb = get_constituent_body(team_name)
                if cb is not None:
                    teams_data[page_key]["constituent_body"] = cb
                tid = _parse_rfu_team_id(team_url)
                if tid is not None:
                    teams_data[page_key]["team_ids"].add(tid)
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
    """Get all available seasons from league_data directories."""
    league_data_dir = DATA_DIR / "league_data"
    if not league_data_dir.exists():
        return []

    seasons = [
        season_dir.name
        for season_dir in league_data_dir.iterdir()
        if season_dir.is_dir()
        and re.match(r"\d{4}-\d{4}", season_dir.name)
        and season_dir.name >= EARLIEST_SEASON
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


def build_club_index(all_teams: dict[str, TeamData]) -> dict[str, list[str]]:
    """Pre-build an index of co-located teams for fast club lookups.

    Unions teams into connected components across three independent
    signals: shared address string, shared (lat, lon), and shared canonical
    club name (``rugby.clubs.resolve_club_name``, backed by
    ``data/rugby/club_names.json``). Address and coordinate signals are
    scoped per canonical club so distinct clubs sharing a ground (e.g. East
    London RFC and Kings Cross Steelers RFC at Holland Road) are not merged.
    The canonical-name signal still links every derived name for the same club.

    Returns:
        Dictionary mapping page key -> sorted list of other page keys at the same club
    """
    team_club_map = load_team_club_map()

    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        root = node
        while parent[root] != root:
            root = parent[root]
        cur = node
        while parent[cur] != root:
            nxt = parent[cur]
            parent[cur] = root
            cur = nxt
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    first_member_for_signal: dict[tuple[str, object], str] = {}

    for page_key, data in all_teams.items():
        find(page_key)  # ensure every team has its own component even with no signals

        addr = data.get("address")
        lat = data.get("latitude")
        lon = data.get("longitude")
        name = data.get("name") or ""
        club = resolve_club_name(name, team_club_map) if name else ""

        canon = resolve_club_name(name, team_club_map) if name else ""

        signals: list[tuple[str, object]] = []
        if addr:
            signals.append(("addr", (addr, canon)))
        if lat is not None and lon is not None:
            signals.append(("coord", ((lat, lon), canon)))
        if club:
            signals.append(("club", club))

        for signal in signals:
            if signal in first_member_for_signal:
                union(page_key, first_member_for_signal[signal])
            else:
                first_member_for_signal[signal] = page_key

    components: defaultdict[str, list[str]] = defaultdict(list)
    for page_key in all_teams:
        components[find(page_key)].append(page_key)

    return {
        page_key: sorted(k for k in components[find(page_key)] if k != page_key)
        for page_key in all_teams
    }


def build_id_to_page_key(all_teams: dict[str, TeamData]) -> dict[int, str]:
    """Map RFU team id → canonical team page key."""
    lookup: dict[int, str] = {}
    for page_key, data in all_teams.items():
        for tid in data.get("team_ids") or set():
            lookup[tid] = page_key
    return lookup


def build_team_id_name_lookup() -> dict[int, str]:
    """Latest display name per RFU team id from league_data (chronological walk).

    Only ``name``/``url`` are needed, so this reads ``league_data/`` directly
    rather than joining club address/geocode data.
    """
    league_data_dir = DATA_DIR / "league_data"
    if not league_data_dir.exists():
        return {}

    season_dirs = [
        d
        for d in sorted(league_data_dir.iterdir())
        if d.is_dir() and re.match(r"\d{4}-\d{4}", d.name) and d.name >= EARLIEST_SEASON
    ]
    names: dict[int, str] = {}
    for season_dir in season_dirs:
        for league_file in season_dir.rglob("*.json"):
            if league_file.name.startswith("_"):
                continue
            with open(league_file, encoding="utf-8") as f:
                league_data: League = json.load(f)
            for team in league_data.get("teams", []):
                tid = _parse_rfu_team_id(team.get("url"))
                if tid is not None:
                    names[tid] = team["name"]
    return names


def collect_team_fixtures(id_to_page_key: dict[int, str]) -> dict[str, list[TeamFixtureEntry]]:
    """Load committed fixture JSON and group rows by canonical team page key."""
    fixture_root = DATA_DIR / "fixture_data"
    if not fixture_root.exists() or not id_to_page_key:
        return {}

    by_page_key: dict[str, list[TeamFixtureEntry]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()

    season_dirs = [
        d for d in sorted(fixture_root.iterdir()) if d.is_dir() and re.match(r"\d{4}-\d{4}", d.name)
    ]
    for season_dir in season_dirs:
        season = season_dir.name
        for json_file in sorted(season_dir.rglob("*.json")):
            with open(json_file, encoding="utf-8") as f:
                data: FixtureLeague = json.load(f)
            league_name = data.get("league_name") or json_file.stem.replace("_", " ")
            for fixture in data.get("fixtures", []):
                _append_fixture_rows(
                    fixture,
                    season,
                    league_name,
                    id_to_page_key,
                    by_page_key,
                    seen,
                )

    result: dict[str, list[TeamFixtureEntry]] = {}
    for page_key, rows in by_page_key.items():
        upcoming = sorted(
            (r for r in rows if _fixture_is_upcoming(r)),
            key=lambda r: (r["date"], r["match_url"]),
        )
        past = sorted(
            (r for r in rows if not _fixture_is_upcoming(r)),
            key=lambda r: (r["date"], r["match_url"]),
            reverse=True,
        )
        result[page_key] = upcoming + past
    return result


def _fixture_is_upcoming(entry: TeamFixtureEntry) -> bool:
    try:
        return date.fromisoformat(entry["date"]) >= date.today()
    except ValueError:
        return False


def _append_fixture_rows(
    fixture: Fixture,
    season: str,
    league_name: str,
    id_to_page_key: dict[int, str],
    by_page_key: dict[str, list[TeamFixtureEntry]],
    seen: set[tuple[str, str]],
) -> None:
    """Add home/away rows when either side maps to a team page."""
    ds = fixture.get("date")
    if not isinstance(ds, str) or not ds:
        return
    match_url = fixture.get("match_url") or ""

    for is_home, team_id, opponent_id in (
        (True, fixture["home_team_id"], fixture["away_team_id"]),
        (False, fixture["away_team_id"], fixture["home_team_id"]),
    ):
        page_key = id_to_page_key.get(team_id)
        if not page_key:
            continue
        dedupe_key = (page_key, match_url or f"{ds}:{team_id}:{opponent_id}")
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        row = TeamFixtureEntry(
            season=season,
            league_name=league_name,
            date=ds,
            time=fixture.get("time") or "",
            is_home=is_home,
            opponent_id=opponent_id,
            match_url=match_url,
        )
        if "home_score" in fixture:
            row["home_score"] = fixture.get("home_score")
        if "away_score" in fixture:
            row["away_score"] = fixture.get("away_score")
        if "status" in fixture:
            row["status"] = fixture.get("status")
        by_page_key[page_key].append(row)


def _format_fixture_date(iso_date: str) -> str:
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b %Y')}"
    except ValueError:
        return iso_date


def _format_fixture_result(entry: TeamFixtureEntry) -> str:
    status = entry.get("status")
    if status:
        label = _FIXTURE_STATUS_LABELS.get(status, status)
        return f'<span title="{escape(label)}">{escape(status)}</span>'
    home_score = entry.get("home_score")
    away_score = entry.get("away_score")
    if home_score is not None and away_score is not None:
        is_home = entry["is_home"]
        own_score = home_score if is_home else away_score
        opponent_score = away_score if is_home else home_score
        if own_score > opponent_score:
            badge_class, badge_label = "result-win", "W"
        elif own_score < opponent_score:
            badge_class, badge_label = "result-loss", "L"
        else:
            badge_class, badge_label = "result-draw", "D"
        home_class = "score-home own-score" if is_home else "score-home"
        away_class = "score-away own-score" if not is_home else "score-away"
        badge_html = f'<span class="result-badge {badge_class}">{badge_label}</span>'
        return (
            f'<span class="fixture-score">'
            f'<span class="{home_class}">{home_score}</span>'
            f'<span class="score-sep">–</span>'
            f'<span class="{away_class}">{away_score}</span>'
            f"{badge_html}"
            f"</span>"
        )
    time_text = entry.get("time") or ""
    return escape(time_text) if time_text else "—"


def _opponent_page_link(
    opponent_id: int,
    opponent_name: str,
    all_teams: dict[str, TeamData],
    id_to_page_key: dict[int, str],
    ambiguous_display_names: set[str],
) -> str:
    page_key = id_to_page_key.get(opponent_id)
    if page_key and page_key in all_teams:
        slug = _team_page_slug(all_teams[page_key], ambiguous_display_names)
        href = _team_page_sibling_href(slug)
        return (
            f'<a href="{escape(href)}" class="card-link card-inline fixture-opponent-link">'
            f"{escape(opponent_name)}</a>"
        )
    return f'<span class="fixture-opponent-name">{escape(opponent_name)}</span>'


def _sort_season_fixtures(rows: list[TeamFixtureEntry]) -> list[TeamFixtureEntry]:
    """Within one season: upcoming fixtures first, then past results (newest first)."""
    upcoming = sorted(
        (r for r in rows if _fixture_is_upcoming(r)),
        key=lambda r: (r["date"], r["match_url"]),
    )
    past = sorted(
        (r for r in rows if not _fixture_is_upcoming(r)),
        key=lambda r: (r["date"], r["match_url"]),
        reverse=True,
    )
    return upcoming + past


def _render_fixture_table_rows(
    rows: list[TeamFixtureEntry],
    all_teams: dict[str, TeamData],
    id_to_page_key: dict[int, str],
    team_id_names: dict[int, str],
    ambiguous_display_names: set[str],
) -> str:
    html = ""
    for entry in rows:
        opponent_name = team_id_names.get(entry["opponent_id"], f"Team {entry['opponent_id']}")
        venue = "Home" if entry["is_home"] else "Away"
        opponent_html = _opponent_page_link(
            entry["opponent_id"],
            opponent_name,
            all_teams,
            id_to_page_key,
            ambiguous_display_names,
        )
        result_html = _format_fixture_result(entry)
        match_link = ""
        if entry.get("match_url"):
            match_link = (
                f'<a href="{escape(entry["match_url"])}" target="_blank" '
                f'title="View on England Rugby">↗</a>'
            )
        html += f"""                <tr>
                    <td class="fixture-date-cell">{escape(_format_fixture_date(entry["date"]))}</td>
                    <td class="fixture-opponent-cell">
                        <div class="fixture-opponent">
                            <span class="fixture-venue">{escape(venue)}</span>
                            {opponent_html}
                        </div>
                    </td>
                    <td class="distance-cell fixture-result-cell">{result_html}</td>
                    <td>{escape(entry["league_name"])}</td>
                    <td class="map-cell">{match_link}</td>
                </tr>
"""
    return html


def _render_fixtures_section(
    fixtures: list[TeamFixtureEntry],
    all_teams: dict[str, TeamData],
    id_to_page_key: dict[int, str],
    team_id_names: dict[int, str],
    ambiguous_display_names: set[str],
) -> str:
    if not fixtures:
        return ""

    by_season: defaultdict[str, list[TeamFixtureEntry]] = defaultdict(list)
    for entry in fixtures:
        by_season[entry["season"]].append(entry)

    seasons = sorted(by_season.keys(), reverse=True)
    current_season = CURRENT_SEASON

    html = """    <div class="info-section fixtures-section">
        <h2>Fixtures & Results</h2>
"""
    for season in seasons:
        rows = _sort_season_fixtures(by_season[season])
        count = len(rows)
        match_word = "fixture" if count == 1 else "fixtures"
        open_attr = " open" if season == current_season else ""
        html += f"""        <details class="fixtures-season"{open_attr}>
            <summary>{escape(season)} ({count} {match_word})</summary>
            <div class="table-wrapper">
            <table class="league-history-table fixtures-table">
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Opponent</th>
                        <th>Result / time</th>
                        <th>League</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>
"""
        html += _render_fixture_table_rows(
            rows,
            all_teams,
            id_to_page_key,
            team_id_names,
            ambiguous_display_names,
        )
        html += """                </tbody>
            </table>
            </div>
        </details>
"""
    html += """    </div>
"""
    return html


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
    constituent_body = team_data.get("constituent_body")
    if constituent_body:
        payload["memberOf"] = {"@type": "SportsOrganization", "name": constituent_body}
    return json.dumps(payload, ensure_ascii=True)


def get_team_page_html(
    page_key: str,
    team_data: TeamData,
    all_teams: dict[str, TeamData],
    club_index: dict[str, list[str]],
    travel_distances_by_season: dict[str, TravelDistances],
    all_seasons: list[str],
    ambiguous_display_names: set[str],
    team_fixtures: list[TeamFixtureEntry],
    id_to_page_key: dict[int, str],
    team_id_names: dict[int, str],
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
    teams_index_href = "../" if is_prod else "./index.html"

    team_slug = _team_page_slug(team_data, ambiguous_display_names)
    # Canonical URL is only meaningful in production; omit it in local dev builds.
    canonical_url = absolute_url(f"/teams/{team_slug}/") if is_prod else ""

    head_extra = ""
    if not league_history:
        head_extra += '    <meta name="robots" content="noindex">\n'
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
{head_extra}    <link rel="stylesheet" href="{get_stylesheet_href(depth=_team_page_asset_depth())}">
    <link rel="stylesheet" href="{get_stylesheet_href(depth=_team_page_asset_depth(), filename="team-pages.css")}">
    {get_favicon_html(depth=_team_page_asset_depth())}
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

    constituent_body = team_data.get("constituent_body")
    if constituent_body:
        html += f'        <div class="info-row"><span class="info-label">Constituent Body:</span> <span class="address">{escape(constituent_body)}</span></div>\n'

    previous_names_html = _format_previous_names(team_data)
    if previous_names_html:
        html += f'        <div class="info-row"><span class="info-label">Previously known as:</span> {previous_names_html}</div>\n'

    team_url = team_data.get("url")
    if team_url:
        html += f'        <div class="info-row"><span class="info-label">RFU Profile:</span> <a href="{escape(rfu_team_only_url(team_url))}" target="_blank">View on England Rugby</a></div>\n'

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
            sib_href = _team_page_sibling_href(_team_page_slug(sib, ambiguous_display_names))
            html += f'            <li><a href="{escape(sib_href)}" class="card-link card-inline">{escape(sib_name)}</a></li>\n'

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
                    <td class="season-cell">{season}</td>
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

                suppress_position_latest = season == all_seasons[0]
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

                travel_km = format_team_travel_distance_km(team_td)
                travel_time = format_team_travel_time_min(team_td)

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
                    <td class="season-cell">{season}</td>
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

    fixtures_html = _render_fixtures_section(
        team_fixtures,
        all_teams,
        id_to_page_key,
        team_id_names,
        ambiguous_display_names,
    )
    if fixtures_html:
        html += fixtures_html

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

    lookup = DistanceLookup.load()
    for distance_file in sorted(distances_dir.glob("*.json")):
        season: str = distance_file.stem  # e.g., "2018-2019"

        try:
            with open(distance_file, encoding="utf-8") as f:
                data: TravelDistances = json.load(f)
                travel_distances_by_season[season] = enrich_island_excl_stats(data, season, lookup)
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

    logger.info("  Loading fixtures...")
    id_to_page_key = build_id_to_page_key(all_teams)
    team_id_names = build_team_id_name_lookup()
    fixtures_by_page_key = collect_team_fixtures(id_to_page_key)
    logger.info("  Loaded fixtures for %d teams", len(fixtures_by_page_key))

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
                fixtures_by_page_key.get(page_key, []),
                id_to_page_key,
                team_id_names,
            )

            slug = _team_page_slug(team_data, ambiguous)
            filepath = _team_page_output_path(teams_dir, slug)
            filepath.parent.mkdir(parents=True, exist_ok=True)

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

    is_prod = get_config().is_production
    flat_team_files = [p for p in teams_dir.glob("*.html") if p.name != "index.html"]
    dir_team_slugs = [
        p.name for p in teams_dir.iterdir() if p.is_dir() and (p / "index.html").is_file()
    ]
    if not flat_team_files and not dir_team_slugs:
        logger.warning("No team pages found")
        return

    teams_list: list[TeamListEntry] = []
    if all_teams is not None:
        ambiguous = _display_names_with_multiple_profiles(all_teams)
        for _pk, td in all_teams.items():
            display_name = td.get("name") or ""
            if not display_name:
                continue
            slug = _team_page_slug(td, ambiguous)
            if not _team_page_output_path(teams_dir, slug).exists():
                continue
            teams_list.append(
                TeamListEntry(
                    file=_team_page_href(slug),
                    name=display_name,
                    image_url=td.get("image_url") or RFU_FALLBACK_ICON,
                )
            )
    elif is_prod:
        for slug in dir_team_slugs:
            display_name = slug.replace("_", " ")
            teams_list.append(
                TeamListEntry(
                    file=_team_page_href(slug), name=display_name, image_url=RFU_FALLBACK_ICON
                )
            )
    else:
        for file_path in flat_team_files:
            slug = file_path.name[:-5]  # Remove .html
            display_name = slug.replace("_", " ")
            teams_list.append(
                TeamListEntry(
                    file=_team_page_href(slug), name=display_name, image_url=RFU_FALLBACK_ICON
                )
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

    teams_payload = [
        {"file": t["file"], "name": t["name"], "img": t["image_url"]} for t in teams_list
    ]
    write_compact_json(teams_dir / "teams.json", teams_payload)

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
    <link rel="stylesheet" href="{get_stylesheet_href(depth=1)}">
    <link rel="stylesheet" href="{get_stylesheet_href(depth=1, filename="team-pages.css")}">
    {get_favicon_html(depth=1)}

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

{get_footer_html()}

    <script>
        let teams = [];

        const teamsGrid = document.getElementById('teamsGrid');
        const searchInput = document.getElementById('searchInput');
        const visibleCount = document.getElementById('visibleCount');
        const noResults = document.getElementById('noResults');

        function escapeHtml(value) {{
            const div = document.createElement('div');
            div.textContent = value == null ? '' : String(value);
            return div.innerHTML;
        }}

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
                    const file = escapeHtml(team.file);
                    const img = escapeHtml(team.img);
                    const name = escapeHtml(team.name);
                    card.innerHTML = `<a href="${{file}}"><img src="${{img}}" class="team-card__logo" loading="lazy" onerror="this.onerror=null;this.src='${{fallback}}'">${{name}}</a>`;
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

        fetch('teams.json')
            .then(r => r.json())
            .then(data => {{
                teams = data;
                displayTeams(teams);
            }})
            .catch(err => {{
                console.error('Failed to load teams.json', err);
                noResults.textContent = 'Failed to load teams. Please refresh the page.';
                noResults.style.display = 'block';
            }});
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
