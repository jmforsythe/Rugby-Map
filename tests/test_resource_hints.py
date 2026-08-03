"""Tests for core.config.get_resource_hints_html."""

from __future__ import annotations

from core.config import get_resource_hints_html


def test_get_resource_hints_html_covers_all_leaflet_subdomains() -> None:
    html = get_resource_hints_html()
    for sub in "abc":
        assert f"https://{sub}.basemaps.cartocdn.com" in html


def test_get_resource_hints_html_has_preconnect_and_dns_prefetch() -> None:
    html = get_resource_hints_html()
    assert html.count('rel="preconnect"') == 3
    assert html.count('rel="dns-prefetch"') == 3


def test_get_resource_hints_html_is_valid_link_tags() -> None:
    html = get_resource_hints_html()
    for line in html.strip().splitlines():
        line = line.strip()
        assert line.startswith("<link ") and line.endswith(">")
