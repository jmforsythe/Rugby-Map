"""Tests for core.config.get_resource_hints_html."""

from __future__ import annotations

from core.basemap_tiles import CARTO_TILE_URL_DARK, CARTO_TILE_URL_LIGHT, get_carto_api_key
from core.config import get_resource_hints_html


def test_get_carto_api_key_reads_local_env_file(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("CARTO_API_KEY=from_local_file\n", encoding="utf-8")
    monkeypatch.setattr("core.basemap_tiles.REPO_ROOT", tmp_path)
    assert get_carto_api_key() == "from_local_file"


def test_carto_tile_urls_use_light_dark_variants() -> None:
    assert "/light_all/" in CARTO_TILE_URL_LIGHT
    assert "/dark_all/" in CARTO_TILE_URL_DARK


def test_get_resource_hints_html_uses_carto_origin() -> None:
    html = get_resource_hints_html()
    assert "https://basemaps.cartocdn.com" in html


def test_get_resource_hints_html_has_preconnect_and_dns_prefetch() -> None:
    html = get_resource_hints_html()
    assert html.count('rel="preconnect"') == 1
    assert html.count('rel="dns-prefetch"') == 1


def test_get_resource_hints_html_is_valid_link_tags() -> None:
    html = get_resource_hints_html()
    for line in html.strip().splitlines():
        line = line.strip()
        assert line.startswith("<link ") and line.endswith(">")
