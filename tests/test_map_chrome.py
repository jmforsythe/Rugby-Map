"""Season switcher helpers for map header chrome."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import DIST_DIR
from rugby.map_chrome import (
    header_bar_html,
    list_nav_seasons,
    page_rel_from_output,
    season_page_path,
    season_switch_href,
)


@pytest.fixture
def dist_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Minimal multi-season dist layout for dev (flat tier maps) and prod (directories)."""
    monkeypatch.setattr("rugby.map_chrome.DIST_DIR", tmp_path)

    for season in ("2026-2027", "2025-2026", "2024-2025"):
        (tmp_path / season).mkdir()
        (tmp_path / season / "index.html").write_text("<html></html>", encoding="utf-8")

    # Dev-style tier maps
    (tmp_path / "2026-2027" / "Counties_1.html").write_text("", encoding="utf-8")
    (tmp_path / "2025-2026" / "Counties_2.html").write_text("", encoding="utf-8")
    (tmp_path / "2024-2025" / "Counties_1.html").write_text("", encoding="utf-8")

    # Fixtures always live in a subdirectory
    for season in ("2026-2027", "2025-2026"):
        fix_dir = tmp_path / season / "fixtures"
        fix_dir.mkdir()
        (fix_dir / "index.html").write_text("", encoding="utf-8")

    return tmp_path


def test_list_nav_seasons_newest_first(dist_tree: Path) -> None:
    assert list_nav_seasons() == ["2026-2027", "2025-2026", "2024-2025"]


def test_page_rel_from_output_dev_tier_map(dist_tree: Path) -> None:
    out = dist_tree / "2026-2027" / "Counties_1.html"
    assert page_rel_from_output(out, "2026-2027") == "Counties_1"


def test_page_rel_from_output_fixtures(dist_tree: Path) -> None:
    out = dist_tree / "2026-2027" / "fixtures" / "index.html"
    assert page_rel_from_output(out, "2026-2027") == "fixtures"


def test_season_switch_href_dev(dist_tree: Path) -> None:
    out = dist_tree / "2026-2027" / "Counties_1.html"
    href = season_switch_href(out, "2024-2025", "Counties_1", is_prod=False)
    assert href == "../2024-2025/Counties_1.html"


def test_season_page_path_prod() -> None:
    path = season_page_path("2026-2027", "Premiership", is_prod=True)
    assert path == DIST_DIR / "2026-2027" / "Premiership" / "index.html"


def test_header_renders_split_control_and_disabled_season(dist_tree: Path) -> None:
    out = dist_tree / "2026-2027" / "Counties_1.html"
    html = header_bar_html(
        "2026-2027",
        "Counties 1",
        output_file=out,
    )

    assert 'class="map-header__split-season"' in html
    assert 'aria-current="true">2026-2027</span>' in html
    assert 'href="../2024-2025/Counties_1.html">2024-2025</a>' in html
    assert "map-header__menu-item--disabled" in html
    assert "No Counties 1 map for 2025-2026" in html
