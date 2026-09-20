"""Tests for map team/fixture search sidecars and HTML injection."""

from __future__ import annotations

from pathlib import Path

from core.map_search import (
    FIXTURE_SEARCH_JS,
    inject_team_search,
    team_search_rows,
    write_team_search_sidecar,
)


def test_team_search_rows_deduplicates_and_sorts() -> None:
    items = [
        {"name": "Zebra RFC", "latitude": 51.5, "longitude": -1.0, "group": "League A"},
        {"name": "Alpha RFC", "latitude": 52.0, "longitude": -0.5, "group": "League B"},
        {"name": "Zebra RFC", "latitude": 51.5, "longitude": -1.0, "group": "League A"},
    ]
    rows = team_search_rows(items)
    assert [r["n"] for r in rows] == ["Alpha RFC", "Zebra RFC"]
    assert rows[1]["g"] == "League A"
    assert rows[1]["lat"] == 51.5


def test_write_and_inject_team_search(tmp_path: Path) -> None:
    html_path = tmp_path / "map.html"
    html_path.write_text(
        "<html><head></head><body><div class='folium-map'></div></body></html>",
        encoding="utf-8",
    )
    rows = [{"n": "Test Club", "lat": 51.0, "lng": -1.0, "g": "Tier 1"}]
    write_team_search_sidecar(html_path, rows)
    assert (tmp_path / "search.json").is_file()
    inject_team_search(html_path)
    text = html_path.read_text(encoding="utf-8")
    assert 'id="rugbyTeamSearchWrap"' in text
    assert "Search teams on this map" in text
    inject_team_search(html_path)
    assert text.count('id="rugbyTeamSearchWrap"') == 1


def test_fixture_search_click_keeps_query_text() -> None:
    """Selecting a fixture must not replace the user's search with the home team."""
    assert "input.value = entry.h" not in FIXTURE_SEARCH_JS


def test_fixture_search_refocus_reopens_results() -> None:
    assert "addEventListener('focus'" in FIXTURE_SEARCH_JS
    assert "filterFixtures(input.value)" in FIXTURE_SEARCH_JS


def test_search_fly_uses_regional_zoom_not_cluster_max() -> None:
    from core.map_search import MARKER_CLUSTER_REVEAL_JS

    assert "var flyZoom = 14" in MARKER_CLUSTER_REVEAL_JS
    assert "clusterGroup.getMaxZoom()" not in MARKER_CLUSTER_REVEAL_JS


def test_matchday_control_mobile_leaves_room_for_map_controls() -> None:
    from rugby.match_day import build_matchday_control_html

    html = build_matchday_control_html(
        dropdown_options="<option>2026-09-06</option>",
        updated_display="1 Sep 2026",
        date_info_json="{}",
        all_dates_json="[]",
        tier_proxy_vars_json="{}",
        tier_label_json="{}",
        data_base_url_json='"/data/"',
        parent_cluster_var_json='"marker_cluster"',
        historic_archive_js="false",
        fixture_data_version_json='"2026-09-01T00:00:00"',
    )
    compact = html.replace(" ", "")
    assert "min(280px,calc(100vw-96px))" in compact
    assert "calc(100vw-16px)" not in compact
