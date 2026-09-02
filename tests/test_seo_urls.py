"""Tests for public URL encoding and legacy redirect resolution."""

from __future__ import annotations

from pathlib import Path

from rugby.maps import _render_popup_html
from rugby.redirects import (
    _redirect_target_url,
    discover_apostrophe_tier_redirects,
    discover_feature_rename_redirects,
    generate_legacy_redirects,
    resolve_not_found_redirect,
    resolve_redirect_target,
)
from rugby.seo import absolute_url, encode_url_path, generate_sitemap
from rugby.team_pages import discover_team_rename_redirects, team_info_page_filename


def test_encode_url_path_keeps_apostrophe_in_team_paths() -> None:
    path = "/teams/Bishop's_Stortford.html"
    assert encode_url_path(path) == path
    assert absolute_url(path) == f"https://rugbyunionmap.uk{path}"


def test_encode_url_path_encodes_spaces() -> None:
    assert "%20" in encode_url_path("/teams/Foo Bar.html")


def test_resolve_merit_404_to_season(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "2016-2017").mkdir(parents=True)
    (dist / "2016-2017" / "index.html").write_text("<html></html>", encoding="utf-8")
    target = resolve_redirect_target(
        "/2016-2017/merit/Midlands_Reserve/Midlands_Reserve_4/",
        dist,
        set(),
    )
    assert target == "https://rugbyunionmap.uk/2016-2017/"


def test_resolve_team_to_teams_index(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "teams").mkdir(parents=True)
    (dist / "teams" / "index.html").write_text("<html></html>", encoding="utf-8")
    target = resolve_redirect_target("/teams/Missing_Club.html", dist, {"Other.html"})
    assert target == "https://rugbyunionmap.uk/teams/"


def test_resolve_not_found_redirect_to_parent_index() -> None:
    assert (
        resolve_not_found_redirect("/2024-2025/merit/Hampshire/Missing_Map/", is_prod=True)
        == "/2024-2025/merit/Hampshire/"
    )
    assert resolve_not_found_redirect("/teams/Missing_Club.html", is_prod=True) == "/teams/"
    assert resolve_not_found_redirect("/2024-2025", is_prod=True) == "/"
    assert resolve_not_found_redirect("/", is_prod=True) == "/"


def test_resolve_not_found_redirect_local_preview() -> None:
    assert (
        resolve_not_found_redirect("/2024-2025/National_League_1/foo", is_prod=False)
        == "/2024-2025/National_League_1/index.html"
    )
    assert resolve_not_found_redirect("/2024-2025", is_prod=False) == "/index.html"


def test_discover_team_rename_redirects_includes_middlesbrough_a_xv() -> None:
    pairs = dict(discover_team_rename_redirects())
    assert pairs["/teams/Middlesbrough_'A'_XV.html"] == "/teams/Middlesbrough_III.html"


def test_team_info_page_filename_uses_canonical_name() -> None:
    lookup = {13794: "Middlesbrough_III.html"}
    assert (
        team_info_page_filename(
            "https://www.englandrugby.com/fixtures-and-results/search-results?team=13794",
            "Middlesbrough 'A' XV",
            lookup,
        )
        == "Middlesbrough_III.html"
    )


def test_render_popup_links_to_canonical_team_page() -> None:
    lookup = {13794: "Middlesbrough_III.html"}
    html = _render_popup_html(
        "Middlesbrough 'A' XV",
        "CANDY 3 South",
        "https://example.com/league",
        "https://www.englandrugby.com/fixtures-and-results/search-results?team=13794",
        "Acklam Park",
        None,
        team_info_pages=lookup,
    )
    assert "teams/Middlesbrough_III.html" in html
    assert "Middlesbrough_'A'_XV.html" not in html


def test_generate_legacy_redirects_writes_team_rename_stub(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "teams").mkdir(parents=True)
    (dist / "teams" / "Middlesbrough_III.html").write_text("<html></html>", encoding="utf-8")

    written = generate_legacy_redirects(dist)
    assert written >= 1

    stub = dist / "teams" / "Middlesbrough_'A'_XV.html"
    assert stub.is_file()
    text = stub.read_text(encoding="utf-8")
    assert 'data-rugby-redirect="1"' in text
    assert "Middlesbrough_III.html" in text


def test_sitemap_omits_team_rename_redirect_stubs(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    teams = dist / "teams"
    teams.mkdir(parents=True)
    (teams / "Middlesbrough_III.html").write_text("<html></html>", encoding="utf-8")
    (teams / "Middlesbrough_'A'_XV.html").write_text(
        '<html data-rugby-redirect="1"></html>',
        encoding="utf-8",
    )

    sitemap = generate_sitemap(dist)
    assert "Middlesbrough_III.html" in sitemap
    assert "Middlesbrough_'A'_XV.html" not in sitemap


def test_redirect_target_url_prefers_explicit_rename(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    explicit = {"/teams/Middlesbrough_'A'_XV.html": "/teams/Middlesbrough_III.html"}
    target = _redirect_target_url(
        "/teams/Middlesbrough_'A'_XV.html",
        dist,
        set(),
        explicit,
    )
    assert target == absolute_url("/teams/Middlesbrough_III.html")


def test_discover_feature_rename_redirects_match_day_to_fixtures(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    season = dist / "2026-2027"
    (season / "fixtures").mkdir(parents=True)
    (season / "fixtures" / "index.html").write_text("<html></html>", encoding="utf-8")
    pairs = dict(discover_feature_rename_redirects(dist))
    assert pairs["/2026-2027/match_day/"] == "/2026-2027/fixtures/"


def test_discover_apostrophe_tier_redirects(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    season = dist / "2024-2025"
    (season / "Premiership_Women").mkdir(parents=True)
    (season / "Premiership_Women" / "index.html").write_text("<html></html>", encoding="utf-8")
    (season / "Premiership_Women's").mkdir(parents=True)
    pairs = dict(discover_apostrophe_tier_redirects(dist))
    assert pairs["/2024-2025/Premiership_Women's/"] == "/2024-2025/Premiership_Women/"


def test_sitemap_skips_flat_tier_html_when_directory_exists(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    season = dist / "2026-2027"
    tier = season / "Counties_1"
    tier.mkdir(parents=True)
    (tier / "index.html").write_text("<html></html>", encoding="utf-8")
    (season / "Counties_1.html").write_text("<html></html>", encoding="utf-8")
    sitemap = generate_sitemap(dist)
    assert "Counties_1/" in sitemap
    assert "Counties_1.html" not in sitemap


def test_sitemap_omits_404_html(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "404.html").write_text("<html></html>", encoding="utf-8")
    sitemap = generate_sitemap(dist)
    assert "404.html" not in sitemap
