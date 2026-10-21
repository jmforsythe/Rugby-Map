"""Tests for the crawlable per-league team lists in the map legend."""

from __future__ import annotations

from typing import Any

from core.map_builder import MarkerItem, _items_to_placed, _legend
from rugby.maps import _resolve_info_links


def _item(name: str, group: str, link_url: str | None) -> MarkerItem:
    return MarkerItem(
        name=name,
        latitude=51.5,
        longitude=-1.0,
        group=group,
        tier="Counties 1",
        tier_num=7,
        link_url=link_url,
    )


def _legend_html(items: list[MarkerItem], **kwargs: Any) -> str:
    by_tier, order = _items_to_placed(items)
    colors = {it.group: "#336699" for it in items}
    return _legend("Counties 1 - 3", by_tier, list(order), colors, **kwargs).render()


def test_legend_lists_linked_teams_per_league_when_enabled() -> None:
    html = _legend_html(
        [
            _item("Zeta RFC", "League A", "/teams/Zeta_RFC/"),
            _item("Alpha & Co", "League A", "/teams/Alpha/"),
            _item("No Page", "League B", None),
        ],
        item_lists=True,
    )
    assert '<details class="legend-items"' in html
    assert "League A (2)</summary>" in html
    assert '<li><a href="/teams/Alpha/">Alpha &amp; Co</a></li>' in html
    assert html.index("Alpha &amp; Co") < html.index("Zeta RFC")
    assert "<li>No Page</li>" in html


def test_legend_has_no_team_lists_by_default() -> None:
    html = _legend_html([_item("Zeta RFC", "League A", "/teams/Zeta_RFC/")])
    assert "legend-items" not in html.split("</style>", 1)[1]
    assert "Zeta RFC" not in html
    assert "League A (1)</p>" in html


def test_resolve_info_links_rewrites_team_link_prefix(monkeypatch) -> None:
    from core.config import get_config

    item = _item("Club", "League", "__INFO_PREFIX__teams/Club.html")
    monkeypatch.setattr(get_config(), "is_production", False)
    (resolved,) = _resolve_info_links([item], subdirectory_depth=2)
    assert resolved.link_url == "../../../teams/Club.html"
    monkeypatch.setattr(get_config(), "is_production", True)
    (resolved,) = _resolve_info_links([item])
    assert resolved.link_url == "/teams/Club.html"
