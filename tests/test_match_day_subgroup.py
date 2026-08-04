"""Tests for match_day subgroup plugin script injection."""

from __future__ import annotations

from pathlib import Path

from rugby.match_day import inject_subgroup_plugin_script


def test_inject_subgroup_plugin_script_places_after_markercluster(tmp_path: Path) -> None:
    html_path = tmp_path / "index.html"
    html_path.write_text(
        """<!DOCTYPE html>
<html><head>
<script src="/shared/vendor/leaflet-1.9.3.js"></script>
<script src="/shared/vendor/leaflet.featuregroup.subgroup.js"></script>
<script src="/shared/vendor/leaflet.markercluster-1.1.0.js"></script>
</head><body></body></html>""",
        encoding="utf-8",
    )

    assert inject_subgroup_plugin_script(html_path) is True
    text = html_path.read_text(encoding="utf-8")
    mc_idx = text.index("leaflet.markercluster")
    sg_idx = text.index("leaflet.featuregroup.subgroup")
    assert mc_idx < sg_idx
    assert text.count("leaflet.featuregroup.subgroup") == 1
