"""Assemble-time season navigation patch."""

from __future__ import annotations

from html import escape
from pathlib import Path

import pytest

from rugby.patch_season_nav import (
    page_label_from_header_html,
    patch_dist_season_nav,
    patch_map_page_html,
    season_from_output_path,
)


def _sample_map_html(*, season: str, menu_html: str, title: str = "Counties 1") -> str:
    return f"""<!DOCTYPE html>
<html><body>
<div class="map-header">
  <span class="map-header__split-season">
    <a class="map-header__split-label" href="../">{escape(season)}</a>
    <details class="map-header__split-menu">
      <summary class="map-header__split-trigger"></summary>
      <div class="map-header__menu">{menu_html}</div>
    </details>
  </span>
  <span class="map-header__title">{escape(title)}</span>
</div>
</body></html>"""


@pytest.fixture
def dist_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("rugby.map_chrome.DIST_DIR", tmp_path)
    monkeypatch.setattr("rugby.patch_season_nav.DIST_DIR", tmp_path)

    for season in ("2026-2027", "2025-2026", "2024-2025"):
        tier = tmp_path / season / "Counties_1"
        tier.mkdir(parents=True)
        (tier / "index.html").write_text("", encoding="utf-8")
        (tmp_path / season / "index.html").write_text("", encoding="utf-8")

    out = tmp_path / "2026-2027" / "Counties_1" / "index.html"
    # Matrix jobs only embed the season being built — not the full dist tree.
    ci_menu = (
        '<span class="map-header__menu-item map-header__menu-item--current" '
        'aria-current="true">2026-2027</span>'
    )
    out.write_text(
        _sample_map_html(season="2026-2027", menu_html=ci_menu),
        encoding="utf-8",
    )
    return tmp_path


def test_page_label_from_header_html() -> None:
    html = _sample_map_html(season="2026-2027", menu_html="", title="Counties 1")
    assert page_label_from_header_html(html) == "Counties 1"

    tier_html = html.replace(
        '<span class="map-header__title">Counties 1</span>',
        '<select class="map-header__select">'
        '<option value="../Regional_1/" selected>Regional 1</option></select>',
    )
    assert page_label_from_header_html(tier_html) == "Regional 1"


def test_season_from_output_path(dist_tree: Path) -> None:
    out = dist_tree / "2026-2027" / "Counties_1" / "index.html"
    assert season_from_output_path(out, dist_dir=dist_tree) == "2026-2027"
    assert (
        season_from_output_path(dist_tree / "teams" / "foo" / "index.html", dist_dir=dist_tree)
        is None
    )


def test_patch_map_page_html_adds_other_seasons(dist_tree: Path) -> None:
    out = dist_tree / "2026-2027" / "Counties_1" / "index.html"
    html = out.read_text(encoding="utf-8")
    assert 'href="../../2024-2025/Counties_1/">2024-2025</a>' not in html

    patched = patch_map_page_html(html, out, is_prod=True, dist_dir=dist_tree)
    assert patched is not None
    assert 'href="../../2024-2025/Counties_1/">2024-2025</a>' in patched
    assert 'aria-current="true">2026-2027</span>' in patched


def test_patch_skips_pages_without_split_control(dist_tree: Path) -> None:
    plain = dist_tree / "2026-2027" / "index.html"
    plain.write_text("<html><body>No header</body></html>", encoding="utf-8")
    assert patch_map_page_html(plain.read_text(), plain, is_prod=True, dist_dir=dist_tree) is None


def test_patch_dist_season_nav_updates_files(dist_tree: Path) -> None:
    updated = patch_dist_season_nav(dist_tree, is_prod=True)
    assert updated == 1
    html = (dist_tree / "2026-2027" / "Counties_1" / "index.html").read_text(encoding="utf-8")
    assert 'href="../../2025-2026/Counties_1/">2025-2026</a>' in html
