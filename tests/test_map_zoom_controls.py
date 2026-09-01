"""Map pages expose quarter-step Leaflet zoom and a zoom stepper UI."""

from __future__ import annotations

import tempfile
from pathlib import Path

import folium

from core.basemap_tiles import CARTO_TILE_URL_LIGHT, folium_carto_attribution
from core.map_builder import MAP_ZOOM_DELTA, MAP_ZOOM_SNAP, MapConfig, _build_base_map


def test_build_base_map_emits_fractional_leaflet_zoom_options() -> None:
    config = MapConfig(title="Zoom test", color_palette=["#336699"])
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "map.html"
        m = _build_base_map(config)
        m.save(out)
        html = out.read_text(encoding="utf-8")

    assert '"zoomSnap": 0.25' in html
    assert '"zoomDelta": 0.25' in html
    assert MAP_ZOOM_SNAP == 0.25
    assert MAP_ZOOM_DELTA == 0.25


def test_map_header_includes_zoom_stepper_script_and_styles() -> None:
    config = MapConfig(title="Zoom test", color_palette=["#336699"])
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "map.html"
        m = _build_base_map(config)
        m.save(out)
        html = out.read_text(encoding="utf-8")

    assert "rugby-zoom-stepper" in html
    assert "rugbyZoomStepper" in html
    assert "applyMapZoomOptions" in html
    assert "rugbyStepZoom" in html
    assert "7x</span>" in html
    assert "leaflet-control-zoom" in html
    assert '"zoomControl": false' in html


def test_match_day_map_constructor_accepts_quarter_step_zoom() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "matchday.html"
        m = folium.Map(
            location=[52.5, -1.5],
            zoom_start=7,
            tiles=None,
            zoom_snap=MAP_ZOOM_SNAP,
            zoom_delta=MAP_ZOOM_DELTA,
        )
        folium.TileLayer(
            tiles=CARTO_TILE_URL_LIGHT,
            attr=folium_carto_attribution(),
            control=False,
        ).add_to(m)
        m.save(out)
        html = out.read_text(encoding="utf-8")

    assert '"zoomSnap": 0.25' in html


def test_map_markers_defer_real_team_images_until_after_render() -> None:
    config = MapConfig(
        title="Zoom test",
        color_palette=["#336699"],
        fallback_icon_url="https://example.com/fallback.svg",
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "deferred-images.html"
        m = _build_base_map(config)
        m.save(out)
        html = out.read_text(encoding="utf-8")

    assert "data-crest-url" in html
    assert 'querySelectorAll("[data-crest-url]:not([data-crest-ready])")' in html
    assert "batchSize = 16" in html
    assert "requestAnimationFrame" in html
    assert "rugby-map-presentation-ready" in html
    assert "rugbyLoadedCrests" in html
    assert "rugbyCrestClusterInner" in html
    assert "rugbyCrestsEnabled" in html
    assert "rugbyRefreshMarkerClusters" in html
    assert "rugbyClusterIconCache" in html
    assert "rugby-crest-marker" in html


def test_custom_map_template_defers_team_images_until_after_render() -> None:
    custom_map_path = (
        Path(__file__).resolve().parents[1] / "rugby" / "custom_map_assets" / "index.html"
    )
    html = custom_map_path.read_text(encoding="utf-8")

    assert "data-crest-url" in html
    assert 'querySelectorAll("[data-crest-url]:not([data-crest-ready])")' in html
    assert "const batchSize = 16" in html
    assert "requestAnimationFrame" in html
    assert "rugby-map-presentation-ready" in html
    assert "rugbyLoadedCrests" in html
    assert "rugbyCrestsEnabled" in html
    assert "rugbyRefreshMarkerClusters" in html
    assert "rugbyClusterIconCache" in html
    assert "rugby-crest-marker" in html
    assert "renderTerritories(cachedAllPlaced, finishMapPresentation)" in html
    assert "mapPresentationPending" in html
    assert "PRESENTATION_FALLBACK_MS" in html


def test_territory_loader_prefetches_before_leaflet_init() -> None:
    from core.map_builder import _get_territory_loader_script

    script = _get_territory_loader_script("territories.json")
    assert "territoryDataPromise" in script
    assert "fetchTerritories(0)" in script
    assert "rugby-map-presentation-ready" in script
    assert "rugbyTryApplyTerritories" in script
    assert "GROUPS_PER_FRAME" in script
    assert "applyTerritoriesProgressive" in script
    assert "PRESENTATION_FALLBACK_MS" in script
    assert "presentationReadySent" in script


def test_inject_territory_boot_hook_inserts_before_marker_cluster() -> None:
    from core.map_builder import _inject_territory_boot_hook

    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "map.html"
        html_path.write_text(
            "<script>\nvar fg = L.featureGroup({});\n"
            "fg.addTo(map_1);\n"
            "var marker_cluster_abc123 = L.markerClusterGroup({});\n"
            "</script>",
            encoding="utf-8",
        )
        _inject_territory_boot_hook(html_path)
        text = html_path.read_text(encoding="utf-8")
        hook_pos = text.find("rugbyTryApplyTerritories")
        cluster_pos = text.find("var marker_cluster_")
        assert hook_pos != -1
        assert cluster_pos != -1
        assert hook_pos < cluster_pos


def test_inject_presentation_ready_hook_appends_to_saved_map() -> None:
    from folium.plugins import FeatureGroupSubGroup

    from core.map_builder import _add_marker, _add_marker_cluster, _finalize_map_html

    config = MapConfig(
        title="Inline territory",
        color_palette=["#336699"],
        fallback_icon_url="https://example.com/fallback.svg",
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "inline.html"
        m = _build_base_map(config)
        fg_t = folium.FeatureGroup(name="League - Territory", show=True)
        m.add_child(fg_t)
        cluster = _add_marker_cluster(m, fallback_icon_url=config.fallback_icon_url)
        fg_m = FeatureGroupSubGroup(cluster, name="League - Markers", show=True)
        m.add_child(fg_m)
        item = {
            "name": "A",
            "latitude": 51.5,
            "longitude": -1.0,
            "group": "League",
            "tier": "T",
            "tier_num": 5,
            "icon_url": "https://example.com/crest.png",
            "popup_html": None,
            "category": None,
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

    ready_pos = html.rfind("rugby-map-presentation-ready")
    map_pos = html.rfind("L.map(")
    cluster_pos = html.rfind("var marker_cluster_")
    assert ready_pos != -1
    assert map_pos != -1
    assert cluster_pos != -1
    assert map_pos < ready_pos
    assert cluster_pos < ready_pos


def test_finalize_map_html_injects_territory_boot_on_sidecar_maps() -> None:
    from core.map_builder import _finalize_map_html

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sidecar.html"
        out.write_text(
            "<script>var fg = L.featureGroup({}); fg.addTo(map_1);\n"
            "var marker_cluster_x = L.markerClusterGroup({});\n</script>",
            encoding="utf-8",
        )
        _finalize_map_html(out, territory_export=True)
        text = out.read_text(encoding="utf-8")
        assert "rugbyTryApplyTerritories" in text
        assert text.find("rugbyTryApplyTerritories") < text.find("var marker_cluster_")
