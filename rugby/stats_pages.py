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
    get_config,
    get_favicon_html,
    get_google_analytics_script,
    get_twitter_card_meta,
    set_config,
    setup_logging,
)
from core.config import DIST_DIR
from rugby import BRAND, DATA_DIR
from rugby.clubs import iter_geocoded_leagues, load_team_club_map, resolve_club_name
from rugby.seo import BASE_URL, OG_DEFAULT_IMAGE, breadcrumb_ld_script, og_image_meta_html
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
# Every women's league file (pyramid or merit) is named "Women's_*"; everything
# else in league_data/ is men's. See rugby.tiers.extract_tier_women, which
# dispatches on the same prefix.
_WOMENS_FILENAME_PREFIX = "Women's_"


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
        d for d in base.iterdir() if d.is_dir() and re.match(r"\d{4}-\d{4}", d.name)
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
            is_merit = rel_path.startswith("merit/")
            comp_key = rel_path.split("/")[1] if is_merit else PYRAMID_KEY
            if is_merit and comp_key not in merit_labels:
                merit_labels[comp_key] = comp_key.replace("_", " ")

            filename = rel_path.rsplit("/", 1)[-1]
            gender_key = (
                GENDER_WOMEN if filename.startswith(_WOMENS_FILENAME_PREFIX) else GENDER_MEN
            )

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
    </style>
"""


def get_stats_index_html(breakdown: StatsBreakdown) -> str:
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

    <script type="application/json" id="stats-dataset">{dataset_json}</script>
{_CHART_SCRIPT}
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

    stats_dir = DIST_DIR / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)

    html_content = get_stats_index_html(breakdown)
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
