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
