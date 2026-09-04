"""Generate the stats dashboard page (``dist/stats/index.html``).

Charts the number of distinct teams and distinct clubs fielded each season,
computed straight from ``league_data/`` (joined with club name resolution via
``rugby.clubs``) rather than the cross-season-merged ``TeamData`` produced by
``rugby.team_pages`` -- renamed/renumbered teams should count once per season
they actually appeared in, not once for their whole (merged) history.

Series are broken down per competition (pyramid vs. each merit competition)
and per gender (men's / women's), so the page can filter client-side on
either dimension independently. Each chart owns an independent filter
popover (a gear icon opening a small picker) rather than one shared control,
so "Teams" and "Clubs" can show different slices at once. The actual chart
rendering (axes, gridlines, hover crosshair/tooltip, gap detection) happens
in JS against an embedded JSON dataset -- see ``_CHART_SCRIPT``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import defaultdict
from html import escape
from pathlib import Path
from typing import TypedDict

from core import (
    EARLIEST_SEASON,
    get_config,
    get_favicon_html,
    get_google_analytics_script,
    get_twitter_card_meta,
    set_config,
    setup_logging,
)
from core.config import CURRENT_SEASON, DIST_DIR
from rugby import BRAND, DATA_DIR
from rugby.clubs import iter_geocoded_leagues, load_team_club_map, resolve_club_name
from rugby.seo import BASE_URL, OG_DEFAULT_IMAGE, breadcrumb_ld_script, og_image_meta_html
from rugby.tiers import (
    extract_tier,
    get_competition_offset,
    mens_current_tier_name,
    womens_current_tier_name,
)
from rugby.webpages import get_footer_html

logger = logging.getLogger(__name__)

PYRAMID_KEY = ""
ALL_COMPETITIONS_KEY = "__all__"

GENDER_ALL = ""
GENDER_MEN = "men"
GENDER_WOMEN = "women"
GENDER_LABELS: dict[str, str] = {
    GENDER_ALL: "All genders",
    GENDER_MEN: "Men's",
    GENDER_WOMEN: "Women's",
}
# Every women's league file (pyramid or merit) is named ``Women's_*`` or ``Women_*``;
# everything else in league_data/ is men's. See rugby.tiers.extract_tier_women.
_WOMENS_FILENAME_PREFIXES = ("Women's_", "Women_")


def _is_womens_league_filename(filename: str) -> bool:
    return filename.startswith(_WOMENS_FILENAME_PREFIXES)


class SeasonStats(TypedDict):
    """Distinct team / club counts observed in one season, across all competitions."""

    season: str
    teams: int
    clubs: int


class CompetitionSeries(TypedDict):
    """Per-season team/club counts for one competition slice, split by gender.

    ``teams``/``clubs`` map a gender filter value (``GENDER_ALL``/``GENDER_MEN``/
    ``GENDER_WOMEN``) to its per-season count list.
    """

    key: str
    label: str
    teams: dict[str, list[int]]
    clubs: dict[str, list[int]]


class StatsBreakdown(TypedDict):
    """Season list plus one ``CompetitionSeries`` per selectable competition."""

    seasons: list[str]
    competitions: list[CompetitionSeries]


def compute_competition_breakdown(league_data_dir: Path | None = None) -> StatsBreakdown:
    """Distinct team/club counts per season, sliced by competition and by gender.

    Always includes ``PYRAMID_KEY`` (non-merit pyramid tiers, men's + women's)
    and ``ALL_COMPETITIONS_KEY`` (pyramid + every merit competition combined),
    plus one entry per merit competition found under any season's ``merit/``
    directory (key = the directory name, e.g. ``"East_Midlands"``). Each
    entry's ``teams``/``clubs`` is itself keyed by gender filter value
    (``GENDER_ALL``/``GENDER_MEN``/``GENDER_WOMEN``) so competition and gender
    filters can be applied independently and combined.

    A team is counted once per season by its display name for that season (a
    club fielding "Coventry", "Coventry II", "Coventry 3rd XV" contributes one
    club but three teams); a club is counted once per season by its canonical
    name via ``rugby.clubs.resolve_club_name``.
    """
    base = league_data_dir if league_data_dir is not None else DATA_DIR / "league_data"
    if not base.exists():
        return StatsBreakdown(seasons=[], competitions=[])

    season_dirs = sorted(
        d
        for d in base.iterdir()
        if d.is_dir() and re.match(r"\d{4}-\d{4}", d.name) and d.name >= EARLIEST_SEASON
    )
    seasons = [d.name for d in season_dirs]

    team_club_map = load_team_club_map()
    merit_labels: dict[str, str] = {}
    # season -> (competition key, gender key) -> set of names/clubs observed
    teams_by_season: dict[str, defaultdict[tuple[str, str], set[str]]] = {}
    clubs_by_season: dict[str, defaultdict[tuple[str, str], set[str]]] = {}

    for season_dir in season_dirs:
        season = season_dir.name
        comp_teams: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
        comp_clubs: defaultdict[tuple[str, str], set[str]] = defaultdict(set)

        for league_file, league_data in iter_geocoded_leagues(
            season_dir, team_club_map=team_club_map
        ):
            rel_path = league_file.relative_to(season_dir).as_posix()
            if rel_path.startswith("county_championship/"):
                # Representative county sides, not club pyramid/merit teams.
                continue
            is_merit = rel_path.startswith("merit/")
            comp_key = rel_path.split("/")[1] if is_merit else PYRAMID_KEY
            if is_merit and comp_key not in merit_labels:
                merit_labels[comp_key] = comp_key.replace("_", " ")

            filename = rel_path.rsplit("/", 1)[-1]
            gender_key = GENDER_WOMEN if _is_womens_league_filename(filename) else GENDER_MEN

            for team in league_data["teams"]:
                name = team["name"]
                club = resolve_club_name(name, team_club_map)
                for comp_bucket in (comp_key, ALL_COMPETITIONS_KEY):
                    for gender_bucket in (GENDER_ALL, gender_key):
                        comp_teams[(comp_bucket, gender_bucket)].add(name)
                        comp_clubs[(comp_bucket, gender_bucket)].add(club)

        teams_by_season[season] = comp_teams
        clubs_by_season[season] = comp_clubs

    ordered_keys: list[tuple[str, str]] = [
        (PYRAMID_KEY, "Pyramid (no merit)"),
        (ALL_COMPETITIONS_KEY, "All (pyramid + merit)"),
        *sorted(merit_labels.items(), key=lambda kv: kv[1]),
    ]

    competitions: list[CompetitionSeries] = []
    for key, label in ordered_keys:
        teams_by_gender = {
            gender: [len(teams_by_season[s].get((key, gender), ())) for s in seasons]
            for gender in (GENDER_ALL, GENDER_MEN, GENDER_WOMEN)
        }
        clubs_by_gender = {
            gender: [len(clubs_by_season[s].get((key, gender), ())) for s in seasons]
            for gender in (GENDER_ALL, GENDER_MEN, GENDER_WOMEN)
        }
        competitions.append(
            CompetitionSeries(key=key, label=label, teams=teams_by_gender, clubs=clubs_by_gender)
        )

    return StatsBreakdown(seasons=seasons, competitions=competitions)


def compute_season_stats(league_data_dir: Path | None = None) -> list[SeasonStats]:
    """Distinct team and club counts per season (all competitions/genders combined)."""
    breakdown = compute_competition_breakdown(league_data_dir)
    if not breakdown["seasons"]:
        return []
    all_series = next(c for c in breakdown["competitions"] if c["key"] == ALL_COMPETITIONS_KEY)
    return [
        SeasonStats(season=season, teams=teams, clubs=clubs)
        for season, teams, clubs in zip(
            breakdown["seasons"],
            all_series["teams"][GENDER_ALL],
            all_series["clubs"][GENDER_ALL],
            strict=True,
        )
    ]


class ClubTeamPoint(TypedDict):
    """One team's finish in one season, for the club timeline chart's hover popup.

    ``level`` is the raw absolute tier (women's tiers are 101+, per ``rugby.tiers``);
    the client subtracts 100 off women's levels before plotting/labelling so both
    genders read on the same 1-based scale once the gender toggle filters to one.
    """

    level: float
    league: str
    position: int | None
    team_count: int
    is_merit: bool
    gender: str


class ClubTeamLevels(TypedDict):
    """One team's playing-level series for a club, aligned to ``ClubTimelines.seasons``.

    ``None`` marks a season the team was not fielded (a gap in its line).
    """

    team: str
    points: list[ClubTeamPoint | None]


class ClubTimeline(TypedDict):
    """A club's teams and their level series, most recently-departed team last."""

    club: str
    teams: list[ClubTeamLevels]


class ClubTimelines(TypedDict):
    """Season list plus one ``ClubTimeline`` per club observed in ``league_data``."""

    seasons: list[str]
    clubs: list[ClubTimeline]


# extract_tier's sentinel for a filename/path it doesn't recognize (e.g. representative
# county_championship fixtures or pre-2008 oddball county filenames) -- not a real pyramid
# position, so leagues resolving to this must be excluded rather than plotted as tier 999.
_UNKNOWN_TIER = 999


def _league_absolute_tier(rel_path: str, season: str) -> int | None:
    """Absolute pyramid tier for a league file, or ``None`` if it isn't a recognized tier."""
    parts = rel_path.split("/")
    is_merit = len(parts) >= 2 and parts[0] == "merit"
    local_tier, _local_name = extract_tier(rel_path, season)
    if local_tier == _UNKNOWN_TIER:
        return None
    return local_tier + get_competition_offset(parts[1], season) if is_merit else local_tier


def _team_level(
    *, abs_tier: int, position: int, team_count: int, season: str, current_season: str
) -> float:
    """Playing level for one team's finish in a league already resolved to ``abs_tier``.

    Offsets the tier by final league position: 1st place sits at the top of the
    tier (fraction 0), last place sits near the bottom (fraction approaching 1,
    i.e. just short of the tier below). Lower is always a higher standard of
    rugby. The current (in-progress) season has no final position yet, so it
    reports the bare tier with no fraction.
    """
    if season == current_season or team_count <= 0:
        return float(abs_tier)
    return abs_tier + (position - 1) / team_count


def compute_club_timelines(
    league_data_dir: Path | None = None, current_season: str = CURRENT_SEASON
) -> ClubTimelines:
    """Per-club, per-team playing-level series across every season in ``league_data``.

    Each distinct team display name observed for a club (e.g. "Alpha RFC",
    "Alpha RFC II") gets its own series -- a club fielding 3 teams in a season
    shows 3 lines. See :func:`_team_level` for how a season's finish becomes a
    single ``level`` number.
    """
    base = league_data_dir if league_data_dir is not None else DATA_DIR / "league_data"
    if not base.exists():
        return ClubTimelines(seasons=[], clubs=[])

    season_dirs = sorted(
        d
        for d in base.iterdir()
        if d.is_dir() and re.match(r"\d{4}-\d{4}", d.name) and d.name >= EARLIEST_SEASON
    )
    seasons = [d.name for d in season_dirs]

    team_club_map = load_team_club_map()
    # club -> team display name -> season -> point
    points: dict[str, dict[str, dict[str, ClubTeamPoint]]] = defaultdict(lambda: defaultdict(dict))

    for season_dir in season_dirs:
        season = season_dir.name
        for league_file, league_data in iter_geocoded_leagues(
            season_dir, team_club_map=team_club_map
        ):
            rel_path = league_file.relative_to(season_dir).as_posix()
            abs_tier = _league_absolute_tier(rel_path, season)
            if abs_tier is None:
                continue
            is_merit = rel_path.startswith("merit/")
            filename = rel_path.rsplit("/", 1)[-1]
            gender = GENDER_WOMEN if _is_womens_league_filename(filename) else GENDER_MEN

            teams = league_data["teams"]
            team_count = len(teams)
            is_current = season == current_season
            for position, team in enumerate(teams, start=1):
                name = team["name"]
                club = resolve_club_name(name, team_club_map)
                level = _team_level(
                    abs_tier=abs_tier,
                    position=position,
                    team_count=team_count,
                    season=season,
                    current_season=current_season,
                )
                point = ClubTeamPoint(
                    level=level,
                    league=league_data["league_name"],
                    position=None if is_current else position,
                    team_count=team_count,
                    is_merit=is_merit,
                    gender=gender,
                )
                # A team appearing in more than one league file the same season
                # (shouldn't normally happen) keeps its best (lowest) level.
                existing = points[club][name].get(season)
                if existing is None or level < existing["level"]:
                    points[club][name][season] = point

    clubs: list[ClubTimeline] = []
    for club in sorted(points):
        team_series = [
            ClubTeamLevels(
                team=team,
                points=[season_points.get(season) for season in seasons],
            )
            for team, season_points in sorted(points[club].items())
        ]
        clubs.append(ClubTimeline(club=club, teams=team_series))

    return ClubTimelines(seasons=seasons, clubs=clubs)


def _stat_tile(value_id: str, label_id: str) -> str:
    return f"""        <div class="stat-tile">
            <div class="stat-tile__value" id="{value_id}">&ndash;</div>
            <div class="stat-tile__label" id="{label_id}"></div>
        </div>
"""


# All chart geometry/rendering (axes, gridlines, gap detection, hover
# crosshair/tooltip) lives here so switching the competition filter can
# redraw both charts client-side against the embedded dataset, without a
# page reload or server round-trip.
_CHART_SCRIPT = """    <script>
        (function () {
            var datasetNode = document.getElementById('stats-dataset');
            if (!datasetNode) {
                return;
            }
            var dataset = JSON.parse(datasetNode.textContent);
            var seasons = dataset.seasons;
            var byKey = {};
            dataset.competitions.forEach(function (c) { byKey[c.key] = c; });

            var VIEW_W = 760, VIEW_H = 260;
            var MARGIN_LEFT = 34, MARGIN_RIGHT = 12, MARGIN_TOP = 16, MARGIN_BOTTOM = 28;
            var MAX_X_LABELS = 8;
            var GAP_RATIO = 0.3;
            var SVGNS = 'http://www.w3.org/2000/svg';

            function shortSeason(season) {
                var parts = season.split('-');
                if (parts.length === 2 && parts[0].length === 4 && parts[1].length === 4) {
                    return parts[0] + '-' + parts[1].slice(2);
                }
                return season;
            }

            // "Nice numbers" axis bounds (Heckbert): pick a round step size targeting
            // ~4 gridline intervals, then round the (padded) data range outward to
            // multiples of that step. The axis tightens to the data instead of always
            // starting at 0, while gridline labels stay on clean round numbers.
            function niceAxisBounds(min, max) {
                if (max <= min) {
                    max = min + 1;
                }
                var pad = (max - min) * 0.08;
                var paddedMin = Math.max(0, min - pad);
                var paddedMax = max + pad;
                var range = paddedMax - paddedMin;
                var roughStep = range / 4;
                var magnitude = Math.pow(10, Math.floor(Math.log(roughStep) / Math.LN10));
                var residual = roughStep / magnitude;
                var niceResidual = residual > 5 ? 10 : residual > 2 ? 5 : residual > 1 ? 2 : 1;
                var step = niceResidual * magnitude;
                var niceMin = Math.max(0, Math.floor(paddedMin / step) * step);
                var niceMax = Math.ceil(paddedMax / step) * step;
                return { min: niceMin, max: niceMax, step: step };
            }

            function detectGapIndices(values) {
                // A zero always means "this competition didn't exist yet / wasn't scraped
                // that season" -- never a real state for an active league -- so it's a gap
                // outright. A low-but-nonzero value close to its neighbours' average is a
                // real (if small) season; far below it (e.g. an abandoned COVID season) is
                // treated as incomplete data too.
                var gaps = {};
                for (var i = 0; i < values.length; i++) {
                    if (values[i] === 0) {
                        gaps[i] = true;
                        continue;
                    }
                    var neighbors = [];
                    if (i > 0) { neighbors.push(values[i - 1]); }
                    if (i < values.length - 1) { neighbors.push(values[i + 1]); }
                    if (!neighbors.length) { continue; }
                    var sum = neighbors.reduce(function (a, b) { return a + b; }, 0);
                    var avg = sum / neighbors.length;
                    if (avg > 0 && values[i] < avg * GAP_RATIO) {
                        gaps[i] = true;
                    }
                }
                return gaps;
            }

            function svgEl(tag, attrs) {
                var node = document.createElementNS(SVGNS, tag);
                Object.keys(attrs || {}).forEach(function (key) {
                    node.setAttribute(key, attrs[key]);
                });
                return node;
            }

            function renderChart(chart) {
                var svg = chart.svg;
                while (svg.firstChild) {
                    svg.removeChild(svg.firstChild);
                }

                var values = chart.values;
                var n = values.length;
                var gapSet = detectGapIndices(values);
                var plottedValues = values.filter(function (_v, i) { return !gapSet[i]; });
                var bounds = plottedValues.length
                    ? niceAxisBounds(Math.min.apply(null, plottedValues), Math.max.apply(null, plottedValues))
                    : { min: 0, max: 1, step: 1 };

                var plotX0 = MARGIN_LEFT, plotX1 = VIEW_W - MARGIN_RIGHT;
                var plotY0 = MARGIN_TOP, plotY1 = VIEW_H - MARGIN_BOTTOM;
                var plotW = plotX1 - plotX0, plotH = plotY1 - plotY0;
                var yRange = bounds.max - bounds.min;

                function xAt(i) {
                    return n <= 1 ? plotX0 + plotW / 2 : plotX0 + (plotW * i) / (n - 1);
                }
                function yAt(v) {
                    return yRange ? plotY1 - ((v - bounds.min) / yRange) * plotH : plotY1;
                }

                var gridGroup = svgEl('g', { class: 'chart-grid' });
                var tickCount = yRange ? Math.round(yRange / bounds.step) : 0;
                for (var ti = 0; ti <= tickCount; ti++) {
                    var tickValue = bounds.min + ti * bounds.step;
                    var gy = yAt(tickValue);
                    gridGroup.appendChild(svgEl('line', {
                        class: 'chart-gridline',
                        x1: plotX0, x2: plotX1, y1: gy.toFixed(1), y2: gy.toFixed(1),
                    }));
                    var label = svgEl('text', {
                        class: 'chart-axis-label chart-axis-label--y',
                        x: plotX0 - 8, y: gy.toFixed(1),
                        'text-anchor': 'end', 'dominant-baseline': 'middle',
                    });
                    label.textContent = String(Math.round(tickValue));
                    gridGroup.appendChild(label);
                }
                svg.appendChild(gridGroup);

                var axisGroup = svgEl('g', { class: 'chart-axis' });
                if (n) {
                    var step = Math.max(1, Math.ceil(n / MAX_X_LABELS));
                    var labelIdx = {};
                    labelIdx[0] = true;
                    labelIdx[n - 1] = true;
                    for (var li = 0; li < n; li += step) { labelIdx[li] = true; }
                    Object.keys(labelIdx)
                        .map(Number)
                        .sort(function (a, b) { return a - b; })
                        .forEach(function (i) {
                            var t = svgEl('text', {
                                class: 'chart-axis-label',
                                x: xAt(i).toFixed(1), y: VIEW_H - 8, 'text-anchor': 'middle',
                            });
                            t.textContent = shortSeason(seasons[i]);
                            axisGroup.appendChild(t);
                        });
                }
                svg.appendChild(axisGroup);

                var points = [];
                for (var i = 0; i < n; i++) {
                    if (gapSet[i]) { continue; }
                    points.push({ i: i, x: xAt(i), y: yAt(values[i]) });
                }

                var pathD = '';
                var prevI = null;
                points.forEach(function (p) {
                    pathD += (prevI !== null && p.i === prevI + 1 ? ' L ' : ' M ')
                        + p.x.toFixed(1) + ',' + p.y.toFixed(1);
                    prevI = p.i;
                });
                svg.appendChild(svgEl('path', { class: 'chart-line', d: pathD.trim(), fill: 'none' }));

                var dotsGroup = svgEl('g', { class: 'chart-dots' });
                points.forEach(function (p) {
                    dotsGroup.appendChild(svgEl('circle', {
                        class: 'chart-dot', cx: p.x.toFixed(1), cy: p.y.toFixed(1), r: 3,
                    }));
                });
                svg.appendChild(dotsGroup);

                var crosshair = svgEl('line', {
                    class: 'chart-crosshair', x1: 0, x2: 0, y1: plotY0, y2: plotY1,
                });
                svg.appendChild(crosshair);
                var hoverDot = svgEl('circle', { class: 'chart-hoverdot', r: 5 });
                svg.appendChild(hoverDot);
                var hit = svgEl('rect', {
                    class: 'chart-hit', x: plotX0, y: 0, width: plotW, height: VIEW_H,
                });
                svg.appendChild(hit);

                if (chart.noteEl) {
                    // Only call out gaps *within* the competition's active run (e.g. an
                    // abandoned COVID season) -- leading/trailing zeros just mean the
                    // competition didn't exist yet, which the missing line already shows.
                    var activeIdx = [];
                    for (var vi = 0; vi < values.length; vi++) {
                        if (values[vi] !== 0) { activeIdx.push(vi); }
                    }
                    var firstActive = activeIdx.length ? activeIdx[0] : -1;
                    var lastActive = activeIdx.length ? activeIdx[activeIdx.length - 1] : -1;
                    var gapList = Object.keys(gapSet)
                        .map(Number)
                        .filter(function (i) { return i > firstActive && i < lastActive; })
                        .sort(function (a, b) { return a - b; });
                    if (gapList.length) {
                        var names = gapList.map(function (i) { return shortSeason(seasons[i]); }).join(', ');
                        chart.noteEl.textContent = 'Gap: ' + names + ' had abandoned/incomplete '
                            + 'league data for this selection and is excluded rather than shown as a drop.';
                        chart.noteEl.hidden = false;
                    } else {
                        chart.noteEl.hidden = true;
                    }
                }

                var seasonEl = chart.tooltip.querySelector('.chart-tooltip__season');
                var valueEl = chart.tooltip.querySelector('.chart-tooltip__value');
                var tooltip = chart.tooltip;

                function nearestPoint(svgX) {
                    var best = points[0], bestDist = Infinity;
                    points.forEach(function (p) {
                        var d = Math.abs(p.x - svgX);
                        if (d < bestDist) { bestDist = d; best = p; }
                    });
                    return best;
                }

                function show(evt) {
                    if (!points.length) { return; }
                    var rect = svg.getBoundingClientRect();
                    var clientX = evt.touches ? evt.touches[0].clientX : evt.clientX;
                    var svgX = ((clientX - rect.left) / rect.width) * VIEW_W;
                    var p = nearestPoint(svgX);
                    crosshair.setAttribute('x1', p.x);
                    crosshair.setAttribute('x2', p.x);
                    crosshair.style.opacity = '1';
                    hoverDot.setAttribute('cx', p.x);
                    hoverDot.setAttribute('cy', p.y);
                    hoverDot.style.opacity = '1';
                    seasonEl.textContent = shortSeason(seasons[p.i]);
                    valueEl.textContent = values[p.i] + ' ' + chart.unit;
                    tooltip.style.opacity = '1';
                    tooltip.style.left = ((p.x / VIEW_W) * 100) + '%';
                }
                function hide() {
                    crosshair.style.opacity = '0';
                    hoverDot.style.opacity = '0';
                    tooltip.style.opacity = '0';
                }

                hit.addEventListener('pointermove', show);
                hit.addEventListener('pointerdown', show);
                hit.addEventListener('pointerleave', hide);
                hit.addEventListener('touchstart', show, { passive: true });

                return values.length ? values[values.length - 1] : null;
            }

            // Each chart owns an independent filter popover (gear icon -> picker)
            // rather than a single shared control, so e.g. "Teams" can show Men's
            // Pyramid while "Clubs" shows a specific merit competition, all genders.
            // Competition and gender are separate radio groups in the same popover,
            // combined (AND) when reading the series to plot.
            var chartDefs = [
                { prefix: 'teams', unit: 'teams' },
                { prefix: 'clubs', unit: 'clubs' },
            ];
            var latestSeasonLabel = seasons.length ? shortSeason(seasons[seasons.length - 1]) : '';

            function addRadioGroup(panel, groupLabelText, radioName, options, defaultKey, onChange) {
                var group = document.createElement('div');
                group.className = 'filter-popover__group';
                var groupLabel = document.createElement('span');
                groupLabel.className = 'filter-popover__group-label';
                groupLabel.textContent = groupLabelText;
                group.appendChild(groupLabel);

                options.forEach(function (opt) {
                    var row = document.createElement('label');
                    row.className = 'filter-popover__option';
                    var input = document.createElement('input');
                    input.type = 'radio';
                    input.name = radioName;
                    input.value = opt.key;
                    if (opt.key === defaultKey) {
                        input.checked = true;
                    }
                    row.appendChild(input);
                    row.appendChild(document.createTextNode(opt.label));
                    group.appendChild(row);
                    input.addEventListener('change', function () { onChange(opt.key, opt.label); });
                });
                panel.appendChild(group);
            }

            chartDefs.forEach(function (def) {
                var details = document.querySelector('.filter-popover[data-chart="' + def.prefix + '"]');
                if (!details) {
                    return;
                }
                var summaryBtn = details.querySelector('.icon-btn');
                var panel = details.querySelector('.filter-popover__panel');
                var subtitleEl = document.getElementById(def.prefix + '-chart-subtitle');
                var tileValueEl = document.getElementById('tile-' + def.prefix + '-value');
                var tileLabelEl = document.getElementById('tile-' + def.prefix + '-label');
                var chart = {
                    svg: document.getElementById(def.prefix + '-chart-svg'),
                    tooltip: document.getElementById(def.prefix + '-chart-tooltip'),
                    noteEl: document.getElementById(def.prefix + '-chart-note'),
                    unit: def.unit,
                };
                var state = { comp: '', compLabel: '', gender: '', genderLabel: '' };

                function apply() {
                    var series = byKey[state.comp] || byKey[''];
                    chart.values = (series[def.unit] && series[def.unit][state.gender]) || [];
                    var latest = renderChart(chart);
                    if (latest !== null) {
                        tileValueEl.textContent = latest;
                        tileLabelEl.textContent = def.unit.charAt(0).toUpperCase()
                            + def.unit.slice(1) + ' (' + latestSeasonLabel + ')';
                    }

                    var activeLabels = [state.genderLabel, state.compLabel].filter(Boolean);
                    summaryBtn.classList.toggle('icon-btn--active', activeLabels.length > 0);
                    subtitleEl.textContent = '';
                    if (activeLabels.length) {
                        subtitleEl.appendChild(document.createTextNode('Filtered: '));
                        var strong = document.createElement('span');
                        strong.className = 'chart-card-subtitle__value';
                        strong.textContent = activeLabels.join(', ');
                        subtitleEl.appendChild(strong);
                    }
                }

                // Gender first: it's a short, fixed-length list, whereas Competition can run
                // to a couple dozen merit leagues -- putting the long list first would push
                // Gender below the fold of the scrollable panel.
                addRadioGroup(panel, 'Gender', def.prefix + '-gender', dataset.genders, '',
                    function (key, label) {
                        state.gender = key;
                        state.genderLabel = key === '' ? '' : label;
                        apply();
                        details.open = false;
                    });
                addRadioGroup(panel, 'Competition', def.prefix + '-competition', dataset.competitions, '',
                    function (key, label) {
                        state.comp = key;
                        state.compLabel = key === '' ? '' : label;
                        apply();
                        details.open = false;
                    });

                apply();
            });

            document.addEventListener('click', function (evt) {
                document.querySelectorAll('.filter-popover[open]').forEach(function (d) {
                    if (!d.contains(evt.target)) {
                        d.open = false;
                    }
                });
            });
        })();
    </script>
"""

# Multi-line "club timeline" chart: one line per team a club has fielded, plotting
# absolute pyramid level (lower = better) across every season. Kept as a separate
# script/dataset from _CHART_SCRIPT because it plots several series at once with a
# club search box driving which club's teams are shown, rather than a single
# series behind a competition/gender filter.
_CLUB_TIMELINE_SCRIPT = """    <script>
        (function () {
            var datasetNode = document.getElementById('club-timeline-dataset');
            var searchInput = document.getElementById('club-timeline-search');
            var svg = document.getElementById('club-timeline-chart-svg');
            var legendEl = document.getElementById('club-timeline-legend');
            var noteEl = document.getElementById('club-timeline-note');
            var tooltip = document.getElementById('club-timeline-tooltip');
            var genderButtons = document.querySelectorAll('.gender-toggle__btn');
            if (!datasetNode || !searchInput || !svg) {
                return;
            }
            var dataset = JSON.parse(datasetNode.textContent);
            var seasons = dataset.seasons;
            var tierLabels = dataset.tierLabels || {};
            var clubsByName = {};
            dataset.clubs.forEach(function (c) { clubsByName[c.club.toLowerCase()] = c; });

            var VIEW_W = 760, VIEW_H = 280;
            var MARGIN_LEFT = 34, MARGIN_RIGHT = 12, MARGIN_TOP = 16, MARGIN_BOTTOM = 28;
            var MAX_X_LABELS = 8;
            var SVGNS = 'http://www.w3.org/2000/svg';
            var COLORS = [
                '#4C6FFF', '#FF6B6B', '#2EC4B6', '#FFA630', '#8338EC', '#06A77D', '#E85D75', '#5C7AEA'
            ];

            var state = { club: null, gender: 'men' };

            function shortSeason(season) {
                var parts = season.split('-');
                if (parts.length === 2 && parts[0].length === 4 && parts[1].length === 4) {
                    return parts[0] + '-' + parts[1].slice(2);
                }
                return season;
            }

            function ordinal(n) {
                var rem100 = n % 100;
                if (rem100 >= 11 && rem100 <= 13) { return n + 'th'; }
                switch (n % 10) {
                    case 1: return n + 'st';
                    case 2: return n + 'nd';
                    case 3: return n + 'rd';
                    default: return n + 'th';
                }
            }

            // Women's tiers are stored as absolute pyramid numbers (101+); rebase to the
            // same 1-based scale as men's once the toggle has filtered to one gender.
            function displayLevel(point) {
                return state.gender === 'women' ? point.level - 100 : point.level;
            }

            function tierLabel(value) {
                var rounded = Math.round(value);
                var labels = tierLabels[state.gender] || {};
                return labels[String(rounded)] || ('Tier ' + rounded);
            }

            function svgEl(tag, attrs) {
                var node = document.createElementNS(SVGNS, tag);
                Object.keys(attrs || {}).forEach(function (key) {
                    node.setAttribute(key, attrs[key]);
                });
                return node;
            }

            function hideTooltip() {
                if (tooltip) { tooltip.style.opacity = '0'; }
            }

            function showTooltip(team, seasonIdx, point, cx, cy) {
                if (!tooltip) { return; }
                var teamEl = tooltip.querySelector('.chart-tooltip__team');
                var seasonEl = tooltip.querySelector('.chart-tooltip__season');
                var valueEl = tooltip.querySelector('.chart-tooltip__value');
                var leagueEl = tooltip.querySelector('.chart-tooltip__league');
                var noteEl2 = tooltip.querySelector('.chart-tooltip__note');
                teamEl.textContent = team;
                seasonEl.textContent = shortSeason(seasons[seasonIdx]);
                valueEl.textContent = tierLabel(displayLevel(point));
                var positionText = point.position === null
                    ? 'Position: ongoing'
                    : 'Position: ' + ordinal(point.position) + ' of ' + point.team_count;
                leagueEl.textContent = point.league + ' — ' + positionText;
                if (point.is_merit) {
                    noteEl2.textContent = 'Merit competition: pyramid level shown is approximate.';
                    noteEl2.hidden = false;
                } else {
                    noteEl2.hidden = true;
                }
                tooltip.style.left = ((cx / VIEW_W) * 100) + '%';
                tooltip.style.top = ((cy / VIEW_H) * 100) + '%';
                tooltip.style.opacity = '1';
            }

            function teamHasGender(t, gender) {
                return t.points.some(function (p) { return p && p.gender === gender; });
            }

            function clubHasGender(club, gender) {
                return club.teams.some(function (t) { return teamHasGender(t, gender); });
            }

            function renderClub(club) {
                while (svg.firstChild) {
                    svg.removeChild(svg.firstChild);
                }
                legendEl.innerHTML = '';
                hideTooltip();

                genderButtons.forEach(function (btn) {
                    var has = clubHasGender(club, btn.dataset.gender);
                    btn.disabled = !has;
                    btn.classList.toggle('is-active', btn.dataset.gender === state.gender);
                });

                var teamsInView = club.teams.filter(function (t) { return teamHasGender(t, state.gender); });
                var allValues = [];
                teamsInView.forEach(function (t) {
                    t.points.forEach(function (p) {
                        if (p && p.gender === state.gender) { allValues.push(displayLevel(p)); }
                    });
                });
                if (!allValues.length) {
                    noteEl.textContent = 'No ' + state.gender + "'s level data for this club.";
                    noteEl.hidden = false;
                    return;
                }
                noteEl.hidden = true;

                // Lower tier number is a higher standard, so the axis is inverted:
                // the best level a team reached sits at the top of the chart.
                var min = Math.min.apply(null, allValues);
                var max = Math.max.apply(null, allValues);
                var loBound = Math.max(1, Math.floor(min) - 0.5);
                var hiBound = Math.ceil(max) + 0.5;
                var range = hiBound - loBound || 1;

                var plotX0 = MARGIN_LEFT, plotX1 = VIEW_W - MARGIN_RIGHT;
                var plotY0 = MARGIN_TOP, plotY1 = VIEW_H - MARGIN_BOTTOM;
                var plotW = plotX1 - plotX0, plotH = plotY1 - plotY0;
                var n = seasons.length;

                function xAt(i) {
                    return n <= 1 ? plotX0 + plotW / 2 : plotX0 + (plotW * i) / (n - 1);
                }
                function yAt(v) {
                    return plotY0 + ((v - loBound) / range) * plotH;
                }

                var gridGroup = svgEl('g', { class: 'chart-grid' });
                var MAX_Y_TICKS = 8;
                var tickLo = Math.ceil(loBound), tickHi = Math.floor(hiBound);
                var tickStep = Math.max(1, Math.ceil((tickHi - tickLo) / MAX_Y_TICKS));
                for (var tier = tickLo; tier <= tickHi; tier += tickStep) {
                    var gy = yAt(tier);
                    gridGroup.appendChild(svgEl('line', {
                        class: 'chart-gridline',
                        x1: plotX0, x2: plotX1, y1: gy.toFixed(1), y2: gy.toFixed(1),
                    }));
                    var label = svgEl('text', {
                        class: 'chart-axis-label chart-axis-label--y',
                        x: plotX0 - 8, y: gy.toFixed(1),
                        'text-anchor': 'end', 'dominant-baseline': 'middle',
                    });
                    label.textContent = tierLabel(tier);
                    gridGroup.appendChild(label);
                }
                svg.appendChild(gridGroup);

                var axisGroup = svgEl('g', { class: 'chart-axis' });
                if (n) {
                    var step = Math.max(1, Math.ceil(n / MAX_X_LABELS));
                    var labelIdx = {};
                    labelIdx[0] = true;
                    labelIdx[n - 1] = true;
                    for (var li = 0; li < n; li += step) { labelIdx[li] = true; }
                    Object.keys(labelIdx)
                        .map(Number)
                        .sort(function (a, b) { return a - b; })
                        .forEach(function (i) {
                            var t = svgEl('text', {
                                class: 'chart-axis-label',
                                x: xAt(i).toFixed(1), y: VIEW_H - 8, 'text-anchor': 'middle',
                            });
                            t.textContent = shortSeason(seasons[i]);
                            axisGroup.appendChild(t);
                        });
                }
                svg.appendChild(axisGroup);

                teamsInView.forEach(function (t, idx) {
                    var color = COLORS[idx % COLORS.length];
                    var pts = t.points.map(function (p, i) {
                        if (!p || p.gender !== state.gender) { return null; }
                        return { i: i, x: xAt(i), y: yAt(displayLevel(p)), point: p };
                    });

                    // Draw each edge between adjacent seasons individually (rather than one
                    // path) so a merit-competition leg can be dashed on its own -- a team's
                    // history can cross in and out of merit competitions season to season.
                    for (var i = 0; i < pts.length - 1; i++) {
                        if (!pts[i] || !pts[i + 1]) { continue; }
                        var isMeritEdge = pts[i].point.is_merit || pts[i + 1].point.is_merit;
                        svg.appendChild(svgEl('line', {
                            class: 'chart-line' + (isMeritEdge ? ' chart-line--merit' : ''),
                            style: 'stroke:' + color,
                            x1: pts[i].x.toFixed(1), y1: pts[i].y.toFixed(1),
                            x2: pts[i + 1].x.toFixed(1), y2: pts[i + 1].y.toFixed(1),
                        }));
                    }

                    pts.forEach(function (p) {
                        if (!p) { return; }
                        var dot = svgEl('circle', {
                            cx: p.x.toFixed(1), cy: p.y.toFixed(1), r: 4, style: 'fill:' + color,
                        });
                        dot.addEventListener('pointerenter', function () {
                            showTooltip(t.team, p.i, p.point, p.x, p.y);
                        });
                        dot.addEventListener('pointerleave', hideTooltip);
                        svg.appendChild(dot);
                    });

                    var legendItem = document.createElement('div');
                    legendItem.className = 'chart-legend__item';
                    var swatch = document.createElement('span');
                    swatch.className = 'chart-legend__swatch';
                    swatch.style.background = color;
                    legendItem.appendChild(swatch);
                    legendItem.appendChild(document.createTextNode(t.team));
                    legendEl.appendChild(legendItem);
                });
            }

            function selectClub(club) {
                state.club = club;
                if (!clubHasGender(club, state.gender)) {
                    state.gender = clubHasGender(club, 'men') ? 'men' : 'women';
                }
                renderClub(club);
            }

            var optionsList = document.getElementById('club-timeline-options');
            if (optionsList) {
                dataset.clubs.forEach(function (c) {
                    var opt = document.createElement('option');
                    opt.value = c.club;
                    optionsList.appendChild(opt);
                });
            }

            function applySearch() {
                var club = clubsByName[searchInput.value.trim().toLowerCase()];
                if (club) {
                    selectClub(club);
                }
            }
            searchInput.addEventListener('change', applySearch);
            searchInput.addEventListener('input', applySearch);

            genderButtons.forEach(function (btn) {
                btn.addEventListener('click', function () {
                    if (btn.disabled || !state.club) { return; }
                    state.gender = btn.dataset.gender;
                    renderClub(state.club);
                });
            });

            if (dataset.clubs.length) {
                searchInput.value = dataset.clubs[0].club;
                selectClub(dataset.clubs[0]);
            }
        })();
    </script>
"""

_CHART_STYLE = """    <style>
        .chart-card-header {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 0.75em;
            border-bottom: 2px solid var(--accent);
            margin-bottom: 0.3em;
            padding-bottom: 0.5em;
        }
        .chart-card-header h2 {
            margin: 0;
            border: none;
            padding: 0;
        }
        .chart-card-subtitle {
            font-size: 0.85em;
            color: var(--text-muted);
            margin: 0 0 1em;
            min-height: 1.2em;
        }
        .chart-card-subtitle__value {
            color: var(--accent);
            font-weight: 600;
        }
        .icon-btn {
            width: 32px;
            height: 32px;
            flex-shrink: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--bg-card-alt);
            color: var(--text-muted);
            cursor: pointer;
            font-size: 1.05em;
            list-style: none;
            transition: all 0.2s;
        }
        .icon-btn::-webkit-details-marker {
            display: none;
        }
        .icon-btn:hover {
            border-color: var(--accent);
            color: var(--accent);
        }
        .filter-popover[open] > .icon-btn {
            border-color: var(--accent);
            color: var(--accent);
        }
        .icon-btn.icon-btn--active {
            border-color: var(--accent);
            color: var(--accent);
            background: var(--accent-light);
        }
        .filter-popover {
            position: relative;
        }
        .filter-popover__panel {
            position: absolute;
            right: 0;
            top: calc(100% + 8px);
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            box-shadow: 0 6px 20px var(--shadow);
            padding: 0.85em;
            min-width: 230px;
            max-height: 300px;
            overflow-y: auto;
            z-index: 5;
        }
        .filter-popover__group + .filter-popover__group {
            margin-top: 0.75em;
            padding-top: 0.75em;
            border-top: 1px solid var(--border-light);
        }
        .filter-popover__group-label {
            font-size: 0.72em;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            font-weight: 700;
            margin-bottom: 0.4em;
            display: block;
        }
        .filter-popover__option {
            display: flex;
            align-items: center;
            gap: 0.5em;
            padding: 0.35em 0.3em;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.88em;
            color: var(--text);
        }
        .filter-popover__option:hover {
            background: var(--bg-card-alt);
        }
        .filter-popover__option input {
            accent-color: var(--accent);
            margin: 0;
        }
        .stats-tiles {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 1em;
            margin-bottom: 1.5em;
        }
        .stat-tile {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1em 1.25em;
            box-shadow: 0 2px 8px var(--shadow);
            text-align: center;
        }
        .stat-tile__value {
            font-family: var(--font-heading);
            font-size: 2.1em;
            font-weight: 600;
            color: var(--text-heading);
        }
        .stat-tile__label {
            font-size: 0.85em;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 0.2em;
        }
        .chart-note {
            font-size: 0.85em;
            color: var(--text-muted);
            font-style: italic;
            margin: 0 0 1em;
        }
        .chart-wrapper {
            position: relative;
        }
        .chart-svg {
            display: block;
            width: 100%;
            height: auto;
            overflow: visible;
        }
        .chart-gridline {
            stroke: var(--border);
            stroke-width: 1;
        }
        .chart-axis-label {
            fill: var(--text-muted);
            font-family: var(--font-body);
            font-size: 10px;
        }
        .chart-axis-label--y {
            font-variant-numeric: tabular-nums;
        }
        .chart-line {
            stroke: var(--accent);
            stroke-width: 2;
            stroke-linecap: round;
            stroke-linejoin: round;
        }
        .chart-dot {
            fill: var(--accent);
            stroke: var(--bg-card);
            stroke-width: 2;
        }
        .chart-crosshair {
            stroke: var(--border);
            stroke-width: 1;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.1s;
        }
        .chart-hoverdot {
            fill: var(--accent);
            stroke: var(--bg-card);
            stroke-width: 2;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.1s;
        }
        .chart-hit {
            fill: transparent;
            cursor: crosshair;
        }
        .chart-tooltip {
            position: absolute;
            top: 4px;
            transform: translateX(-50%);
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 0.4em 0.7em;
            box-shadow: 0 4px 12px var(--shadow);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.1s;
            white-space: nowrap;
        }
        .chart-tooltip__season {
            font-size: 0.75em;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .chart-tooltip__value {
            font-weight: 700;
            color: var(--text-heading);
            font-size: 1.05em;
        }
        .chart-tooltip--wide {
            white-space: normal;
            max-width: 220px;
            text-align: left;
        }
        .chart-tooltip__team {
            font-weight: 700;
            color: var(--text-heading);
            font-size: 0.95em;
        }
        .chart-tooltip__league {
            font-size: 0.85em;
            color: var(--text-muted);
            margin-top: 0.2em;
        }
        .chart-tooltip__note {
            font-size: 0.78em;
            font-style: italic;
            color: var(--text-muted);
            margin-top: 0.35em;
            border-top: 1px solid var(--border-light);
            padding-top: 0.35em;
        }
        .club-timeline-controls {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 0.75em;
            margin-bottom: 0.75em;
        }
        .club-search {
            width: 100%;
            max-width: 360px;
            padding: 0.5em 0.75em;
            font-size: 0.95em;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg-card);
            color: var(--text);
        }
        .club-search:focus {
            outline: none;
            border-color: var(--accent);
        }
        .gender-toggle {
            display: inline-flex;
            border: 1px solid var(--border);
            border-radius: 8px;
            overflow: hidden;
        }
        .gender-toggle__btn {
            padding: 0.5em 0.9em;
            font-size: 0.88em;
            border: none;
            background: var(--bg-card);
            color: var(--text-muted);
            cursor: pointer;
        }
        .gender-toggle__btn + .gender-toggle__btn {
            border-left: 1px solid var(--border);
        }
        .gender-toggle__btn.is-active {
            background: var(--accent);
            color: var(--bg-card);
            font-weight: 600;
        }
        .gender-toggle__btn:disabled {
            opacity: 0.4;
            cursor: not-allowed;
        }
        .chart-line--merit {
            stroke-dasharray: 5 4;
        }
        .chart-legend {
            display: flex;
            flex-wrap: wrap;
            gap: 0.6em 1.2em;
            margin: 0.6em 0 0;
            font-size: 0.85em;
            color: var(--text-muted);
        }
        .chart-legend__item {
            display: flex;
            align-items: center;
            gap: 0.4em;
        }
        .chart-legend__swatch {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            flex-shrink: 0;
        }
    </style>
"""


def _axis_tier_labels_men(current_season: str = CURRENT_SEASON) -> dict[str, str]:
    """Men's tier number (as a string key) -> display name, for chart y-axis gridlines."""
    return {str(tier): mens_current_tier_name(tier, current_season) for tier in range(1, 15)}


def _axis_tier_labels_women() -> dict[str, str]:
    """Women's tier number *rebased to start at 1* (100 subtracted) -> display name.

    Rebased so the women's axis reads on the same 1-based scale as the men's axis
    once the club timeline's gender toggle filters the chart to one gender.
    """
    return {str(tier): womens_current_tier_name(tier + 100) for tier in range(1, 10)}


def get_stats_index_html(breakdown: StatsBreakdown, club_timelines: ClubTimelines) -> str:
    """Generate HTML content for the stats dashboard page."""
    is_prod = get_config().is_production
    home_href = "../" if is_prod else "../index.html"

    page_title = f"Stats | {BRAND}"
    page_desc = (
        "Historical stats for English rugby union: number of teams and clubs "
        "fielded each season, filterable by pyramid or merit competition."
    )

    head_extra = ""
    if is_prod:
        page_url = f"{BASE_URL}/stats/"
        head_extra = (
            f'    <link rel="canonical" href="{escape(page_url)}">\n'
            f'    <meta property="og:url" content="{escape(page_url)}" />\n'
            + og_image_meta_html(escape(OG_DEFAULT_IMAGE), indent="    ")
            + "\n"
            f"    {get_twitter_card_meta()}\n"
            + breadcrumb_ld_script(
                [("Home", f"{BASE_URL}/"), ("Stats", page_url)],
                indent="    ",
            )
            + "\n"
        )

    dataset_json = json.dumps(
        {
            "seasons": breakdown["seasons"],
            "competitions": breakdown["competitions"],
            "genders": [
                {"key": key, "label": GENDER_LABELS[key]}
                for key in (GENDER_ALL, GENDER_MEN, GENDER_WOMEN)
            ],
        }
    )
    club_timeline_json = json.dumps(
        {
            "seasons": club_timelines["seasons"],
            "clubs": club_timelines["clubs"],
            "tierLabels": {
                GENDER_MEN: _axis_tier_labels_men(),
                GENDER_WOMEN: _axis_tier_labels_women(),
            },
        }
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{escape(page_desc)}">
    <meta property="og:title" content="{escape(page_title)}" />
    <meta property="og:description" content="{escape(page_desc)}" />
    <meta property="og:type" content="website" />
{head_extra}    <title>{escape(page_title)}</title>
    <link rel="stylesheet" href="../styles.css">
    {get_favicon_html(depth=1)}
{_CHART_STYLE}    {get_google_analytics_script()}
</head>
<body>
    <div class="back-link">
        <a href="{escape(home_href)}">← Home</a>
    </div>

    <h1>Stats</h1>
    <p>Teams and clubs fielded each season. Each chart has its own filter, defaulting to the men's + women's pyramid (excluding merit leagues).</p>

    <div class="stats-tiles">
{_stat_tile("tile-teams-value", "tile-teams-label")}{_stat_tile("tile-clubs-value", "tile-clubs-label")}    </div>

    <div class="info-section">
        <div class="chart-card-header">
            <h2>Teams per season</h2>
            <details class="filter-popover" data-chart="teams">
                <summary class="icon-btn" aria-label="Filter teams chart by competition">&#9881;</summary>
                <div class="filter-popover__panel"></div>
            </details>
        </div>
        <p class="chart-card-subtitle" id="teams-chart-subtitle"></p>
        <p class="chart-note" id="teams-chart-note" hidden></p>
        <div class="chart-wrapper">
            <svg id="teams-chart-svg" class="chart-svg" viewBox="0 0 760 260" role="img" aria-label="Teams per season line chart"></svg>
            <div id="teams-chart-tooltip" class="chart-tooltip" role="status" aria-live="polite">
                <div class="chart-tooltip__season"></div>
                <div class="chart-tooltip__value"></div>
            </div>
        </div>
    </div>

    <div class="info-section">
        <div class="chart-card-header">
            <h2>Clubs per season</h2>
            <details class="filter-popover" data-chart="clubs">
                <summary class="icon-btn" aria-label="Filter clubs chart by competition">&#9881;</summary>
                <div class="filter-popover__panel"></div>
            </details>
        </div>
        <p class="chart-card-subtitle" id="clubs-chart-subtitle"></p>
        <p class="chart-note" id="clubs-chart-note" hidden></p>
        <div class="chart-wrapper">
            <svg id="clubs-chart-svg" class="chart-svg" viewBox="0 0 760 260" role="img" aria-label="Clubs per season line chart"></svg>
            <div id="clubs-chart-tooltip" class="chart-tooltip" role="status" aria-live="polite">
                <div class="chart-tooltip__season"></div>
                <div class="chart-tooltip__value"></div>
            </div>
        </div>
    </div>

    <div class="info-section">
        <div class="chart-card-header">
            <h2>Club timeline</h2>
        </div>
        <p class="chart-card-subtitle">Playing level of each of a club's teams across every season (1st XV, 2nds, 3rds, etc. each get their own line). Lower is a higher standard of rugby. Dashed segments are merit competitions, where the pyramid level shown is an approximation.</p>
        <div class="club-timeline-controls">
            <input type="text" id="club-timeline-search" class="club-search" list="club-timeline-options" placeholder="Search for a club&hellip;" autocomplete="off">
            <datalist id="club-timeline-options"></datalist>
            <div class="gender-toggle" role="radiogroup" aria-label="Gender">
                <button type="button" class="gender-toggle__btn" data-gender="men">Men's</button>
                <button type="button" class="gender-toggle__btn" data-gender="women">Women's</button>
            </div>
        </div>
        <p class="chart-note" id="club-timeline-note" hidden></p>
        <div class="chart-wrapper">
            <svg id="club-timeline-chart-svg" class="chart-svg" viewBox="0 0 760 280" role="img" aria-label="Club timeline line chart"></svg>
            <div id="club-timeline-tooltip" class="chart-tooltip chart-tooltip--wide" role="status" aria-live="polite">
                <div class="chart-tooltip__team"></div>
                <div class="chart-tooltip__season"></div>
                <div class="chart-tooltip__value"></div>
                <div class="chart-tooltip__league"></div>
                <div class="chart-tooltip__note" hidden></div>
            </div>
        </div>
        <div class="chart-legend" id="club-timeline-legend"></div>
    </div>

    <script type="application/json" id="stats-dataset">{dataset_json}</script>
{_CHART_SCRIPT}
    <script type="application/json" id="club-timeline-dataset">{club_timeline_json}</script>
{_CLUB_TIMELINE_SCRIPT}
{get_footer_html()}
</body>
</html>
"""
    return html


def generate_stats_page() -> None:
    """Compute the competition breakdown and write ``dist/stats/index.html``."""
    breakdown = compute_competition_breakdown()
    if not breakdown["seasons"]:
        logger.warning("No season data found; skipping stats page")
        return

    club_timelines = compute_club_timelines()

    stats_dir = DIST_DIR / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)

    html_content = get_stats_index_html(breakdown, club_timelines)
    index_path = stats_dir / "index.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    logger.info(
        "Generated stats page for %d seasons (%d competitions) at %s",
        len(breakdown["seasons"]),
        len(breakdown["competitions"]),
        index_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the stats dashboard page.")
    parser.add_argument(
        "--production", action="store_true", help="Change folder structure for production"
    )
    args = parser.parse_args()
    setup_logging()
    if args.production:
        set_config(is_production=True)

    generate_stats_page()


if __name__ == "__main__":
    main()
