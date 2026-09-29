"""Weekly results report for the stats page.

Every scored league fixture is bucketed into a Saturday-centred week (Wednesday
to Tuesday) and written as one compact JSON sidecar per season under
``dist/stats/weekly/<season>.json``. The stats page embeds only a small
season/week index and fetches the selected season on demand, ranking results
client-side so the level filter works without a rebuild -- see
``WEEKLY_REPORT_SCRIPT``.

Level keys follow the fixtures map: pyramid rows use the absolute tier number
(``"3"``, ``"102"``), merit rows group per competition (``"merit:CANDY"``) and
unrecognized league files (e.g. county championship) fall into ``"other"``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict

from core import EARLIEST_SEASON
from core.types import FixtureLeague
from rugby import DATA_DIR
from rugby.tiers import (
    extract_tier,
    mens_current_tier_name,
    merit_competition_public_excluded,
    womens_current_tier_name,
)

logger = logging.getLogger(__name__)

# extract_tier's sentinel for league files it doesn't recognize.
_UNKNOWN_TIER = 999
OTHER_LEVEL_KEY = "other"
_GROUP_ORDER = {"mens": 0, "womens": 1, "merit": 2, "other": 3}


class WeeklyLevel(TypedDict):
    """One selectable level in the report's filter."""

    key: str
    label: str
    group: str  # "mens" | "womens" | "merit" | "other"


class WeeklySeason(TypedDict):
    """One season's sidecar payload.

    ``weeks`` maps a Saturday (ISO date) to its results, each a compact row
    ``[day_offset, home_idx, away_idx, home_score, away_score, league_idx]``:
    ``day_offset`` is days from that Saturday (-3..3) and the indices point into
    ``teams`` (``[name, team page href or ""]``) and ``leagues``
    (``[league_name, level_key]``).
    """

    season: str
    teams: list[list[str]]
    leagues: list[list[str]]
    levels: list[WeeklyLevel]
    weeks: dict[str, list[list[int]]]


class WeeklyIndexEntry(TypedDict):
    """Per-season entry embedded in the stats page: sidecar version + week list."""

    season: str
    v: str
    # [saturday_iso, result_count] in chronological order.
    weeks: list[list[str | int]]


def saturday_of_week(d: date) -> date:
    """Saturday of the Wednesday-to-Tuesday week containing ``d``."""
    weekday = d.weekday()  # Mon=0 .. Sun=6
    delta = 5 - weekday if weekday >= 2 else -(weekday + 2)
    return d + timedelta(days=delta)


def _season_date_window(season: str) -> tuple[date, date]:
    """1 Jan of a season's first year to 31 Dec of its second (as the fixtures map uses).

    Wide enough for Covid-delayed 2019-2020 rounds played in autumn 2020, while
    dropping junk dates (e.g. 2026 fixtures scraped into an old season's files)
    that would otherwise appear as stray weeks.
    """
    y0, y1 = (int(part) for part in season.split("-"))
    return date(y0, 1, 1), date(y1, 12, 31)


def _level_for_league(rel_path: str, season: str) -> WeeklyLevel:
    parts = rel_path.split("/")
    local_tier, _ = extract_tier(rel_path, season)
    if local_tier == _UNKNOWN_TIER:
        return WeeklyLevel(key=OTHER_LEVEL_KEY, label="Other", group="other")
    if parts[0] == "merit" and len(parts) >= 2:
        comp = parts[1]
        return WeeklyLevel(
            key=f"merit:{comp}", label=f"{comp.replace('_', ' ')} Merit", group="merit"
        )
    if local_tier >= 101:
        return WeeklyLevel(
            key=str(local_tier), label=womens_current_tier_name(local_tier), group="womens"
        )
    return WeeklyLevel(
        key=str(local_tier), label=mens_current_tier_name(local_tier, season), group="mens"
    )


def _level_sort_key(level: WeeklyLevel) -> tuple[int, int, str]:
    key = level["key"]
    return (_GROUP_ORDER[level["group"]], int(key) if key.isdigit() else 0, level["label"])


def compute_weekly_season(
    season_dir: Path,
    team_names: dict[int, str],
    team_hrefs: dict[int, str],
) -> WeeklySeason | None:
    """Group one season's scored fixtures into weeks; ``None`` when nothing is scored."""
    season = season_dir.name
    window_start, window_end = _season_date_window(season)
    seen: set[str] = set()
    team_idx: dict[int, int] = {}
    teams: list[list[str]] = []
    leagues: list[list[str]] = []
    levels: dict[str, WeeklyLevel] = {}
    weeks: defaultdict[str, list[list[int]]] = defaultdict(list)

    def team_index(team_id: int) -> int:
        if team_id not in team_idx:
            team_idx[team_id] = len(teams)
            teams.append([team_names.get(team_id, f"Team {team_id}"), team_hrefs.get(team_id, "")])
        return team_idx[team_id]

    for league_file in sorted(season_dir.rglob("*.json")):
        if league_file.name.startswith("_"):
            continue
        rel_path = league_file.relative_to(season_dir).as_posix()
        parts = rel_path.split("/")
        if (
            parts[0] == "merit"
            and len(parts) >= 2
            and merit_competition_public_excluded(season, parts[1])
        ):
            continue
        with open(league_file, encoding="utf-8") as f:
            data: FixtureLeague = json.load(f)

        league_i: int | None = None
        for fixture in data.get("fixtures", []):
            home_score = fixture.get("home_score")
            away_score = fixture.get("away_score")
            if fixture.get("status") or home_score is None or away_score is None:
                continue
            try:
                d = date.fromisoformat(fixture.get("date") or "")
            except ValueError:
                continue
            if not window_start <= d <= window_end:
                continue
            home_id, away_id = fixture["home_team_id"], fixture["away_team_id"]
            dedupe_key = fixture.get("match_url") or f"{d}:{home_id}:{away_id}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            if league_i is None:
                level = _level_for_league(rel_path, season)
                levels.setdefault(level["key"], level)
                league_i = len(leagues)
                leagues.append([data["league_name"], level["key"]])
            saturday = saturday_of_week(d)
            weeks[saturday.isoformat()].append(
                [
                    (d - saturday).days,
                    team_index(home_id),
                    team_index(away_id),
                    home_score,
                    away_score,
                    league_i,
                ]
            )

    if not weeks:
        return None
    return WeeklySeason(
        season=season,
        teams=teams,
        leagues=leagues,
        levels=sorted(levels.values(), key=_level_sort_key),
        weeks={week: weeks[week] for week in sorted(weeks)},
    )


def write_weekly_report(
    out_dir: Path,
    fixture_data_dir: Path | None = None,
    team_names: dict[int, str] | None = None,
    team_hrefs: dict[int, str] | None = None,
) -> list[WeeklyIndexEntry]:
    """Write ``<out_dir>/<season>.json`` per season and return the page index.

    Each index entry carries a content hash so the page's fetch URL changes
    whenever the sidecar does (busting the service worker's
    stale-while-revalidate cache for ``.json``).
    """
    base = fixture_data_dir if fixture_data_dir is not None else DATA_DIR / "fixture_data"
    if not base.exists():
        return []
    if team_names is None or team_hrefs is None:
        # Deferred: building team page hrefs walks every team profile.
        from rugby.team_pages import build_team_id_name_lookup, build_team_info_page_filenames

        team_names = build_team_id_name_lookup() if team_names is None else team_names
        team_hrefs = build_team_info_page_filenames() if team_hrefs is None else team_hrefs

    season_dirs = sorted(
        d
        for d in base.iterdir()
        if d.is_dir() and re.match(r"\d{4}-\d{4}$", d.name) and d.name >= EARLIEST_SEASON
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    index: list[WeeklyIndexEntry] = []
    for season_dir in season_dirs:
        payload = compute_weekly_season(season_dir, team_names, team_hrefs)
        if payload is None:
            continue
        content = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        (out_dir / f"{payload['season']}.json").write_text(content, encoding="utf-8")
        index.append(
            WeeklyIndexEntry(
                season=payload["season"],
                v=hashlib.sha256(content.encode()).hexdigest()[:12],
                weeks=[[week, len(rows)] for week, rows in payload["weeks"].items()],
            )
        )
    logger.info("Wrote weekly report data for %d seasons to %s", len(index), out_dir)
    return index


WEEKLY_REPORT_HTML = """    <div class="info-section weekly-report">
        <div class="chart-card-header">
            <h2>Weekly report</h2>
            <details class="filter-popover" data-chart="weekly">
                <summary class="icon-btn" aria-label="Filter weekly report by level">&#9881;</summary>
                <div class="filter-popover__panel"></div>
            </details>
        </div>
        <p class="chart-card-subtitle" id="weekly-subtitle"></p>
        <div class="weekly-controls">
            <button type="button" class="weekly-nav" id="weekly-prev" aria-label="Previous week">&lsaquo;</button>
            <select id="weekly-season" class="weekly-select" aria-label="Season"></select>
            <select id="weekly-week" class="weekly-select" aria-label="Week"></select>
            <button type="button" class="weekly-nav" id="weekly-next" aria-label="Next week">&rsaquo;</button>
        </div>
        <p class="weekly-summary" id="weekly-summary"></p>
        <div class="weekly-grid" id="weekly-grid"></div>
    </div>
"""

WEEKLY_REPORT_STYLE = """    <style>
        .weekly-controls {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 0.5em;
            margin-bottom: 0.75em;
        }
        .weekly-select,
        .weekly-nav {
            padding: 0.45em 0.7em;
            font-size: 0.95em;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg-card);
            color: var(--text);
        }
        .weekly-nav {
            cursor: pointer;
            font-weight: 700;
            min-width: 2.4em;
        }
        .weekly-nav:disabled {
            opacity: 0.4;
            cursor: default;
        }
        .weekly-select:focus,
        .weekly-nav:hover:not(:disabled) {
            outline: none;
            border-color: var(--accent);
        }
        .weekly-summary {
            font-size: 0.9em;
            color: var(--text-muted);
            margin: 0 0 1em;
        }
        .weekly-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 1em;
        }
        .weekly-card h3 {
            font-size: 1em;
            margin: 0 0 0.4em;
            color: var(--text-heading);
        }
        .weekly-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85em;
        }
        .weekly-table td {
            padding: 0.4em 0.3em;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
        }
        .weekly-rank {
            width: 1.8em;
            color: var(--text-muted);
            font-variant-numeric: tabular-nums;
        }
        .weekly-value {
            width: 3em;
            text-align: right;
            font-weight: 700;
            font-variant-numeric: tabular-nums;
            color: var(--accent);
        }
        .weekly-score {
            font-weight: 700;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
            padding: 0 0.25em;
        }
        .weekly-team a {
            color: inherit;
            text-decoration: none;
        }
        .weekly-team a:hover {
            text-decoration: underline;
        }
        .weekly-team--highlight {
            font-weight: 700;
        }
        .weekly-meta {
            display: block;
            font-size: 0.88em;
            color: var(--text-muted);
        }
        .weekly-empty {
            color: var(--text-muted);
        }
        .filter-popover__group-label .weekly-group-toggle {
            float: right;
            background: none;
            border: none;
            padding: 0;
            font: inherit;
            text-transform: none;
            letter-spacing: 0;
            font-weight: 600;
            color: var(--accent);
            cursor: pointer;
            margin-left: 0.6em;
        }
    </style>
"""

# Ranks one week's results client-side against the lazily fetched season sidecar.
# Across every team in a week "most points conceded" mirrors "most points scored"
# (and biggest defeat mirrors biggest win), so the league-wide lists are team score,
# winning margin, match aggregate and points in defeat.
WEEKLY_REPORT_SCRIPT = """    <script>
        (function () {
            var indexNode = document.getElementById('weekly-index');
            if (!indexNode) {
                return;
            }
            var seasons = JSON.parse(indexNode.textContent).seasons;
            var root = document.querySelector('.weekly-report');
            if (!seasons.length || !root) {
                if (root) {
                    root.hidden = true;
                }
                return;
            }
            var TOP_N = 10;
            var DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            var GROUPS = [
                { key: 'mens', label: "Men's" },
                { key: 'womens', label: "Women's" },
                { key: 'merit', label: 'Merit' },
                { key: 'other', label: 'Other' },
            ];
            var seasonSel = document.getElementById('weekly-season');
            var weekSel = document.getElementById('weekly-week');
            var prevBtn = document.getElementById('weekly-prev');
            var nextBtn = document.getElementById('weekly-next');
            var summaryEl = document.getElementById('weekly-summary');
            var gridEl = document.getElementById('weekly-grid');
            var subtitleEl = document.getElementById('weekly-subtitle');
            var details = root.querySelector('.filter-popover');
            var summaryBtn = details.querySelector('.icon-btn');
            var panel = details.querySelector('.filter-popover__panel');

            // Every week across all seasons, chronological, for prev/next navigation.
            var allWeeks = [];
            seasons.forEach(function (s) {
                s.weeks.forEach(function (w) {
                    allWeeks.push({ season: s, sat: w[0], count: w[1] });
                });
            });
            var cache = {};
            // Level choices persist across weeks/seasons; unset keys use the default
            // (pyramid levels on, merit/other off -- matching the charts' default).
            var levelChoice = {};
            var current = { pos: allWeeks.length - 1, data: null };

            function isOn(level) {
                if (level.key in levelChoice) {
                    return levelChoice[level.key];
                }
                return level.group === 'mens' || level.group === 'womens';
            }
            function parseIso(iso) {
                var p = iso.split('-');
                return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
            }
            function addDays(dt, n) {
                return new Date(dt.getTime() + n * 86400000);
            }
            function fmtDate(dt, withYear) {
                return DAYS[dt.getUTCDay()] + ' ' + dt.getUTCDate() + ' ' + MONTHS[dt.getUTCMonth()]
                    + (withYear ? ' ' + dt.getUTCFullYear() : '');
            }
            function loadSeason(s) {
                if (!cache[s.season]) {
                    cache[s.season] = fetch('weekly/' + s.season + '.json?v=' + s.v)
                        .then(function (r) {
                            if (!r.ok) {
                                throw new Error('HTTP ' + r.status);
                            }
                            return r.json();
                        });
                    cache[s.season].catch(function () { delete cache[s.season]; });
                }
                return cache[s.season];
            }

            seasons.slice().reverse().forEach(function (s) {
                var opt = document.createElement('option');
                opt.value = s.season;
                opt.textContent = s.season;
                seasonSel.appendChild(opt);
            });

            function fillWeekSelect(season) {
                weekSel.textContent = '';
                season.weeks.slice().reverse().forEach(function (w) {
                    var opt = document.createElement('option');
                    opt.value = w[0];
                    opt.textContent = fmtDate(parseIso(w[0]), true) + ' (' + w[1] + ' results)';
                    weekSel.appendChild(opt);
                });
            }

            function buildLevelFilter(levels) {
                panel.textContent = '';
                GROUPS.forEach(function (g) {
                    var groupLevels = levels.filter(function (l) { return l.group === g.key; });
                    if (!groupLevels.length) {
                        return;
                    }
                    var group = document.createElement('div');
                    group.className = 'filter-popover__group';
                    var label = document.createElement('span');
                    label.className = 'filter-popover__group-label';
                    label.textContent = g.label;
                    var inputs = [];
                    [['None', false], ['All', true]].forEach(function (t) {
                        var toggle = document.createElement('button');
                        toggle.type = 'button';
                        toggle.className = 'weekly-group-toggle';
                        toggle.textContent = t[0];
                        toggle.addEventListener('click', function () {
                            groupLevels.forEach(function (l) { levelChoice[l.key] = t[1]; });
                            inputs.forEach(function (input) { input.checked = t[1]; });
                            render();
                        });
                        label.appendChild(toggle);
                    });
                    group.appendChild(label);
                    groupLevels.forEach(function (l) {
                        var row = document.createElement('label');
                        row.className = 'filter-popover__option';
                        var input = document.createElement('input');
                        input.type = 'checkbox';
                        input.checked = isOn(l);
                        input.addEventListener('change', function () {
                            levelChoice[l.key] = input.checked;
                            render();
                        });
                        inputs.push(input);
                        row.appendChild(input);
                        row.appendChild(document.createTextNode(l.label));
                        group.appendChild(row);
                    });
                    panel.appendChild(group);
                });
            }

            function teamNode(data, idx, highlight) {
                var team = data.teams[idx];
                var span = document.createElement('span');
                span.className = 'weekly-team' + (highlight ? ' weekly-team--highlight' : '');
                if (team[1]) {
                    var a = document.createElement('a');
                    a.href = '../teams/' + team[1];
                    a.textContent = team[0];
                    span.appendChild(a);
                } else {
                    span.textContent = team[0];
                }
                return span;
            }

            function renderCard(data, sat, title, entries, formatValue) {
                var card = document.createElement('div');
                card.className = 'weekly-card';
                var h = document.createElement('h3');
                h.textContent = title;
                card.appendChild(h);
                if (!entries.length) {
                    var empty = document.createElement('p');
                    empty.className = 'weekly-empty';
                    empty.textContent = 'None this week.';
                    card.appendChild(empty);
                    return card;
                }
                var table = document.createElement('table');
                table.className = 'weekly-table';
                entries.slice(0, TOP_N).forEach(function (e, i) {
                    var m = e.m;
                    var tr = document.createElement('tr');
                    var rank = document.createElement('td');
                    rank.className = 'weekly-rank';
                    rank.textContent = i + 1;
                    var match = document.createElement('td');
                    match.appendChild(teamNode(data, m.home, e.side === 'home'));
                    var score = document.createElement('span');
                    score.className = 'weekly-score';
                    score.textContent = m.hs + '\\u2013' + m.as;
                    match.appendChild(score);
                    match.appendChild(teamNode(data, m.away, e.side === 'away'));
                    var meta = document.createElement('span');
                    meta.className = 'weekly-meta';
                    meta.textContent = m.league + ' \\u00b7 ' + fmtDate(addDays(sat, m.day), false);
                    match.appendChild(meta);
                    var value = document.createElement('td');
                    value.className = 'weekly-value';
                    value.textContent = formatValue(e.value);
                    tr.appendChild(rank);
                    tr.appendChild(match);
                    tr.appendChild(value);
                    table.appendChild(tr);
                });
                card.appendChild(table);
                return card;
            }

            function byValueThen(tiebreak) {
                return function (a, b) {
                    return (b.value - a.value) || tiebreak(a, b) || (a.m.day - b.m.day);
                };
            }

            function render() {
                var data = current.data;
                var week = allWeeks[current.pos];
                if (!data || !week) {
                    return;
                }
                var sat = parseIso(week.sat);
                var levelsByKey = {};
                data.levels.forEach(function (l) { levelsByKey[l.key] = l; });
                var matches = [];
                (data.weeks[week.sat] || []).forEach(function (r) {
                    var league = data.leagues[r[5]];
                    var level = levelsByKey[league[1]];
                    if (level && isOn(level)) {
                        matches.push({ day: r[0], home: r[1], away: r[2], hs: r[3], as: r[4],
                            league: league[0] });
                    }
                });

                var onCount = data.levels.filter(isOn).length;
                var isDefault = data.levels.every(function (l) { return !(l.key in levelChoice)
                    || levelChoice[l.key] === (l.group === 'mens' || l.group === 'womens'); });
                summaryBtn.classList.toggle('icon-btn--active', !isDefault);
                subtitleEl.textContent = 'Biggest results of each week (Wednesday to Tuesday). ';
                var strong = document.createElement('span');
                strong.className = 'chart-card-subtitle__value';
                if (isDefault) {
                    strong.textContent = "Men's + women's pyramid";
                } else if (onCount === data.levels.length) {
                    strong.textContent = 'All levels';
                } else {
                    strong.textContent = onCount + ' of ' + data.levels.length + ' levels';
                }
                subtitleEl.appendChild(strong);

                var points = 0;
                matches.forEach(function (m) { points += m.hs + m.as; });
                var range = fmtDate(addDays(sat, -3), false) + ' \\u2013 ' + fmtDate(addDays(sat, 3), true);
                summaryEl.textContent = matches.length
                    ? range + ' \\u00b7 ' + matches.length + ' results \\u00b7 ' + points
                        + ' points \\u00b7 ' + (points / matches.length).toFixed(1) + ' per match'
                    : range + ' \\u00b7 No results at the selected levels.';

                var teamScores = [];
                var wins = [];
                var aggregates = [];
                var defeats = [];
                matches.forEach(function (m) {
                    teamScores.push({ m: m, side: 'home', value: m.hs, margin: m.hs - m.as });
                    teamScores.push({ m: m, side: 'away', value: m.as, margin: m.as - m.hs });
                    aggregates.push({ m: m, side: null, value: m.hs + m.as });
                    if (m.hs !== m.as) {
                        var homeWon = m.hs > m.as;
                        wins.push({ m: m, side: homeWon ? 'home' : 'away',
                            value: Math.abs(m.hs - m.as), winner: Math.max(m.hs, m.as) });
                        defeats.push({ m: m, side: homeWon ? 'away' : 'home',
                            value: Math.min(m.hs, m.as), margin: Math.abs(m.hs - m.as) });
                    }
                });
                teamScores.sort(byValueThen(function (a, b) { return b.margin - a.margin; }));
                wins.sort(byValueThen(function (a, b) { return b.winner - a.winner; }));
                aggregates.sort(byValueThen(function (a, b) {
                    return Math.abs(a.m.hs - a.m.as) - Math.abs(b.m.hs - b.m.as);
                }));
                defeats.sort(byValueThen(function (a, b) { return a.margin - b.margin; }));

                gridEl.textContent = '';
                var plain = function (v) { return String(v); };
                gridEl.appendChild(renderCard(data, sat, 'Highest team score', teamScores, plain));
                gridEl.appendChild(renderCard(data, sat, 'Biggest winning margin', wins,
                    function (v) { return '+' + v; }));
                gridEl.appendChild(renderCard(data, sat, 'Highest match aggregate', aggregates, plain));
                gridEl.appendChild(renderCard(data, sat, 'Most points in defeat',
                    defeats.filter(function (e) { return e.value > 0; }), plain));
            }

            function updateUrl(week) {
                var url = new URL(window.location.href);
                url.searchParams.set('week', week.sat);
                history.replaceState(null, '', url);
            }

            function showWeek(pos, pushUrl) {
                current.pos = pos;
                var week = allWeeks[pos];
                if (seasonSel.value !== week.season.season || !weekSel.options.length) {
                    seasonSel.value = week.season.season;
                    fillWeekSelect(week.season);
                }
                weekSel.value = week.sat;
                prevBtn.disabled = pos <= 0;
                nextBtn.disabled = pos >= allWeeks.length - 1;
                if (pushUrl) {
                    updateUrl(week);
                }
                summaryEl.textContent = 'Loading\\u2026';
                loadSeason(week.season).then(function (data) {
                    if (current.pos !== pos) {
                        return;
                    }
                    if (current.data !== data) {
                        current.data = data;
                        buildLevelFilter(data.levels);
                    }
                    render();
                }).catch(function () {
                    summaryEl.textContent = 'Could not load results for ' + week.season.season + '.';
                    gridEl.textContent = '';
                });
            }

            function findPos(season, sat) {
                for (var i = 0; i < allWeeks.length; i++) {
                    if ((!season || allWeeks[i].season.season === season) && allWeeks[i].sat === sat) {
                        return i;
                    }
                }
                return -1;
            }

            seasonSel.addEventListener('change', function () {
                var s = seasons.filter(function (x) { return x.season === seasonSel.value; })[0];
                fillWeekSelect(s);
                showWeek(findPos(s.season, s.weeks[s.weeks.length - 1][0]), true);
            });
            weekSel.addEventListener('change', function () {
                showWeek(findPos(seasonSel.value, weekSel.value), true);
            });
            prevBtn.addEventListener('click', function () { showWeek(current.pos - 1, true); });
            nextBtn.addEventListener('click', function () { showWeek(current.pos + 1, true); });

            // ?week= may be any date: snap it to its Saturday-centred week.
            var requested = new URLSearchParams(window.location.search).get('week');
            var startPos = allWeeks.length - 1;
            if (requested && /^\\d{4}-\\d{2}-\\d{2}$/.test(requested)) {
                var d = parseIso(requested);
                var wd = d.getUTCDay();
                var delta = wd === 0 ? -1 : (wd <= 2 ? -(wd + 1) : 6 - wd);
                var satIso = addDays(d, delta).toISOString().slice(0, 10);
                var found = findPos(null, satIso);
                if (found >= 0) {
                    startPos = found;
                }
            }
            showWeek(startPos, false);
        })();
    </script>
"""
