"""Format travel distance / time for display (maps, team pages, etc.)."""

from __future__ import annotations

from html import escape

from core import LeagueTravelDistances, TeamTravelDistances

ISLAND_TRAVEL_INCL_LABEL = "Inc. islands"
ISLAND_TRAVEL_EXCL_LABEL = "Excl. islands"
ISLAND_TRAVEL_INCL_TITLE = (
    "Travel to every league opponent, including clubs on the Isle of Man "
    "and in the Channel Islands (Jersey and Guernsey)."
)
ISLAND_TRAVEL_EXCL_TITLE = "Travel to mainland opponents only — legs to island clubs are omitted."
ISLAND_TRAVEL_DUAL_NOTE = (
    "This league includes island clubs; figures below show travel with and " "without those trips."
)

ISLAND_TRAVEL_LABEL_CSS = """
.island-travel-label {
  cursor: help;
}
.island-travel-hint {
  font-size: 0.85em;
  opacity: 0.75;
  font-weight: normal;
}
.island-travel-note {
  font-size: 0.92em;
  opacity: 0.85;
  margin: 0 0 6px 0 !important;
}
.folium-map.rugby-map-dark .island-travel-note {
  opacity: 0.78;
}
.island-stat-group {
  display: block;
}
.island-stat-group .island-travel-label {
  display: block;
  margin: 0;
}
.island-stat-group .island-stat-value,
.island-stat-group .island-stat-label-line,
.island-stat-group p {
  margin: 0 0 2px 0;
}
.island-stat-group .island-stat-value:last-child,
.island-stat-group p:last-child {
  margin-bottom: 0;
}
.island-stat-group--spaced {
  margin-top: 0.55em;
}
"""


def has_dual_island_stats(team_dist: TeamTravelDistances | None) -> bool:
    """True when a mainland team should show incl. and excl. island travel."""
    return team_dist is not None and "excl_avg_distance_km" in team_dist


def island_travel_label_html(*, including: bool, as_popup_label: bool = False) -> str:
    """Label with hover tooltip explaining what is included or excluded."""
    label = ISLAND_TRAVEL_INCL_LABEL if including else ISLAND_TRAVEL_EXCL_LABEL
    title = ISLAND_TRAVEL_INCL_TITLE if including else ISLAND_TRAVEL_EXCL_TITLE
    css = "popup-label island-travel-label" if as_popup_label else "island-travel-label"
    return (
        f'<span class="{css}" title="{escape(title)}">'
        f"{escape(label)} "
        f'<span class="island-travel-hint" aria-hidden="true">ⓘ</span>'
        f"</span>"
    )


def _format_km_pair(avg_km: float | None, total_km: float | None) -> str:
    if avg_km is not None and total_km is not None:
        return f"{avg_km:.1f} km / {total_km:.0f} km"
    if avg_km is not None:
        return f"{avg_km:.1f} km avg"
    if total_km is not None:
        return f"{total_km:.0f} km total"
    return "N/A"


def _format_min_pair(avg_min: float | None, total_min: float | None) -> str:
    if avg_min is not None and total_min is not None:
        return f"{round(avg_min)} min / {round(total_min)} min"
    if avg_min is not None:
        return f"{round(avg_min)} min avg"
    if total_min is not None:
        return f"{round(total_min)} min total"
    return "—"


def _island_stat_group_html(including: bool, value_html: str) -> str:
    spaced = "" if including else " island-stat-group--spaced"
    return (
        f'<span class="island-stat-group{spaced}">'
        f"{island_travel_label_html(including=including)}"
        f'<span class="island-stat-value">{value_html}</span>'
        f"</span>"
    )


def format_team_travel_distance_km(team_distances: TeamTravelDistances | None) -> str:
    if team_distances is None:
        return "N/A"
    incl = _format_km_pair(
        team_distances.get("avg_distance_km"),
        team_distances.get("total_distance_km"),
    )
    if not has_dual_island_stats(team_distances):
        return incl
    excl = _format_km_pair(
        team_distances.get("excl_avg_distance_km"),
        team_distances.get("excl_total_distance_km"),
    )
    return _island_stat_group_html(True, incl) + _island_stat_group_html(False, excl)


def format_team_travel_time_min(team_distances: TeamTravelDistances | None) -> str:
    if team_distances is None:
        return "N/A"
    incl = _format_min_pair(
        team_distances.get("avg_duration_min"),
        team_distances.get("total_duration_min"),
    )
    if not has_dual_island_stats(team_distances):
        return incl
    excl = _format_min_pair(
        team_distances.get("excl_avg_duration_min"),
        team_distances.get("excl_total_duration_min"),
    )
    return _island_stat_group_html(True, incl) + _island_stat_group_html(False, excl)


def _popup_km_min_block(
    *,
    including: bool,
    avg_km: float,
    total_km: float,
    avg_min: float | None,
    total_min: float | None,
    league_avg_km: float | None = None,
    league_avg_min: float | None = None,
) -> str:
    spaced = "" if including else " island-stat-group--spaced"
    avg_part = f"{avg_km:.2f} km" + (f" / {avg_min:.0f} min" if avg_min is not None else "")
    total_part = f"{total_km:.2f} km" + (f" / {total_min:.0f} min" if total_min is not None else "")
    league_html = ""
    if league_avg_km is not None:
        league_html = (
            f"<p>League Average: {league_avg_km:.2f} km"
            + (f" / {league_avg_min:.0f} min" if league_avg_min is not None else "")
            + "</p>"
        )
    return (
        f'<div class="island-stat-group{spaced}">'
        f'<p class="island-stat-label-line">'
        f"{island_travel_label_html(including=including, as_popup_label=True)}"
        f"</p>"
        f"<p>Team Average: {avg_part}</p>"
        f"<p>Team Total: {total_part}</p>"
        f"{league_html}"
        f"</div>"
    )


def render_popup_travel_html(
    team_dist: TeamTravelDistances,
    league_dist: LeagueTravelDistances,
    *,
    distance_source: str,
) -> str:
    """Build popup travel block for rugby map markers."""
    heading = "Travel Distances" if distance_source != "routed" else "Travel Distances (road)"
    l_avg_min = league_dist.get("avg_duration_min")

    if not has_dual_island_stats(team_dist):
        t_avg_min = team_dist.get("avg_duration_min")
        t_tot_min = team_dist.get("total_duration_min")
        return (
            f"<hr>"
            f'<p><span class="popup-label">{heading}:</span></p>'
            f"<p>Team Average: {team_dist['avg_distance_km']:.2f} km"
            + (f" / {t_avg_min:.0f} min" if t_avg_min is not None else "")
            + "</p>"
            + f"<p>Team Total: {team_dist['total_distance_km']:.2f} km"
            + (f" / {t_tot_min:.0f} min" if t_tot_min is not None else "")
            + "</p>"
            + f"<p>League Average: {league_dist['avg_distance_km']:.2f} km"
            + (f" / {l_avg_min:.0f} min" if l_avg_min is not None else "")
            + "</p>"
        )

    incl_block = _popup_km_min_block(
        including=True,
        avg_km=team_dist["avg_distance_km"],
        total_km=team_dist["total_distance_km"],
        avg_min=team_dist.get("avg_duration_min"),
        total_min=team_dist.get("total_duration_min"),
        league_avg_km=league_dist["avg_distance_km"],
        league_avg_min=l_avg_min,
    )
    excl_block = _popup_km_min_block(
        including=False,
        avg_km=team_dist["excl_avg_distance_km"],
        total_km=team_dist["excl_total_distance_km"],
        avg_min=team_dist.get("excl_avg_duration_min"),
        total_min=team_dist.get("excl_total_duration_min"),
        league_avg_km=league_dist.get("excl_avg_distance_km"),
        league_avg_min=league_dist.get("excl_avg_duration_min"),
    )

    return (
        f"<hr>"
        f'<p><span class="popup-label">{heading}:</span></p>'
        f'<p class="island-travel-note">{escape(ISLAND_TRAVEL_DUAL_NOTE)}</p>'
        f"{incl_block}{excl_block}"
    )
