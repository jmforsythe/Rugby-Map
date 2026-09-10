"""Regression guards for map crest flicker and MarkerCluster redraw bugs.

These tests lock in fixes for:
- Solo/cluster markers reloading ``<img src>`` on zoom (use div backgrounds).
- Zoom/spiderfy crest resync hooks causing observer ↔ refreshClusters loops.
- Cluster icons missing cache keys or showing reserve badges when count > 1.
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

from folium.plugins import FeatureGroupSubGroup

from core.map_builder import (
    MapConfig,
    MarkerItem,
    _add_marker,
    _add_marker_cluster,
    _build_base_map,
    _collect_marker_crest_urls,
    _crest_icon_html,
    _crest_url_registry_element,
    _deferred_image_loader_script,
    _finalize_map_html,
    crest_style_class,
)
from rugby.match_day import build_matchday_control_html, matchday_cluster_icon_create_js

# Reverted approach: re-sync crest classes/backgrounds on every zoom step.
_TIER_CREST_FORBIDDEN = (
    "rugbyReapplyKnownCrests",
    "hookCrestResync",
    'zoomstart", reapplyKnownCrests',
    'zoomanim", reapplyKnownCrests',
    'spiderfied", reapplyKnownCrests',
    'unspiderfied", reapplyKnownCrests',
    "classList.add(ensureCrestStyleRule",
)


def test_deferred_loader_omits_zoom_crest_resync_hooks() -> None:
    script = _deferred_image_loader_script()
    for forbidden in _TIER_CREST_FORBIDDEN:
        assert forbidden not in script, f"reintroduced {forbidden!r}"


def test_deferred_loader_primes_registry_and_clears_cluster_cache_on_enable() -> None:
    script = _deferred_image_loader_script()
    assert "primeCrestStyleRules" in script
    assert "rugby-crest-url-registry" in script
    assert "window.rugbyClusterIconCache = {}" in script
    assert "clusterRefreshPending = true" in script


def test_crest_icon_html_prebakes_style_class_without_img() -> None:
    url = "https://example.com/teams/foo.png"
    html = _crest_icon_html(
        icon_url=url,
        icon_size=30,
        color="#336699",
        border_css="border: 2px solid #336699; ",
        crest_badge="II",
    )
    assert crest_style_class(url) in html
    assert "data-crest-url" in html
    assert "rugby-crest-badge" in html
    assert "<img" not in html.lower()


def test_crest_url_registry_collects_unique_marker_urls() -> None:
    url = "https://example.com/crest-a.png"
    items = [
        MarkerItem(
            name="Avon RFC II",
            latitude=51.5,
            longitude=-1.0,
            group="League",
            tier="T",
            tier_num=5,
            icon_url=url,
            crest_badge="II",
        ),
        MarkerItem(
            name="Somewhere RFC",
            latitude=51.6,
            longitude=-1.1,
            group="League",
            tier="T",
            tier_num=5,
            icon_url=url,
        ),
    ]
    urls = _collect_marker_crest_urls(items)
    assert urls == [url]
    registry = _crest_url_registry_element(urls)
    assert 'id="rugby-crest-url-registry"' in registry
    assert url in registry


def test_tier_cluster_icon_create_function_avoids_img_and_caches_badges() -> None:
    from core import map_builder

    source = inspect.getsource(map_builder._add_marker_cluster)
    assert "rugbyCrestClusterInner" in source
    assert "rugbyClusterIconCache" in source
    assert "count === 1" in source
    assert "cacheKey = imageUrl + '|' + count + '|' + crestBadge" in source
    assert "<img" not in source


def test_tier_saved_map_marker_carries_prebaked_crest_class() -> None:
    config = MapConfig(
        title="Crest class",
        color_palette=["#336699"],
        fallback_icon_url="https://example.com/fallback.svg",
    )
    crest_url = "https://example.com/crest.png"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "crest-class.html"
        m = _build_base_map(config)
        cluster = _add_marker_cluster(m, fallback_icon_url=config.fallback_icon_url)
        fg_m = FeatureGroupSubGroup(cluster, name="League - Markers", show=True)
        m.add_child(fg_m)
        item = {
            "name": "Club",
            "latitude": 51.5,
            "longitude": -1.0,
            "group": "League",
            "tier": "T",
            "tier_num": 5,
            "icon_url": crest_url,
            "popup_html": None,
            "category": None,
            "crest_badge": None,
            "structure": "pyramid",
            "itl0": None,
            "itl1": None,
            "itl2": None,
            "itl3": None,
            "lad": None,
            "ward": None,
        }
        _add_marker(fg_m, item, "#336699", fallback_icon_url=config.fallback_icon_url)
        m.save(out)
        _finalize_map_html(out, territory_export=False)
        html = out.read_text(encoding="utf-8")

    assert crest_style_class(crest_url) in html
    assert "data-crest-url" in html
    assert "rugby-crest-marker" in html
    assert f"rugby-crest-marker {crest_style_class(crest_url)}" in html.replace("\\", "")


def test_matchday_marker_builder_uses_inline_background_crest_divs() -> None:
    from rugby import match_day

    source = inspect.getsource(match_day.build_match_day_map)
    icon_block = source.split("icon_html =", 1)[1].split("markers_payload.append", 1)[0]
    assert "_matchday_crest_div" in icon_block
    assert "team_lower_xv_roman" in source
    assert '"crestBadge": home_badge or ""' in source
    assert "<img" not in icon_block


def test_matchday_cluster_js_avoids_img_and_caches_with_badge_guard() -> None:
    js = matchday_cluster_icon_create_js(32)
    assert "rugbyClusterIconCache" in js
    assert "background:url(" in js
    assert "count === 1" in js
    assert "cacheKey = imageUrl + '|' + count + '|' + crestBadge" in js
    assert "rugby-crest-wrap" in js
    assert "<img" not in js


def test_matchday_widget_initializes_cluster_cache_and_passes_crest_badge() -> None:
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
    )
    assert "window.rugbyClusterIconCache = window.rugbyClusterIconCache || {};" in html
    assert "crestBadge: md.crestBadge || ''" in html


def test_matchday_control_html_is_valid_folium_jinja_template() -> None:
    """Folium parses injected HTML as Jinja2; doubled braces break deploy builds."""
    import folium

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
    )
    folium.Element(html)


def test_custom_map_template_omits_zoom_crest_resync_hooks() -> None:
    custom_map_path = (
        Path(__file__).resolve().parents[1] / "rugby" / "custom_map_assets" / "index.html"
    )
    html = custom_map_path.read_text(encoding="utf-8")
    for forbidden in _TIER_CREST_FORBIDDEN:
        assert forbidden not in html, f"custom map reintroduced {forbidden!r}"
