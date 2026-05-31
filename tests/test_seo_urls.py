"""Tests for public URL encoding and legacy redirect resolution."""

from __future__ import annotations

from pathlib import Path

from rugby.redirects import resolve_not_found_redirect, resolve_redirect_target
from rugby.seo import absolute_url, encode_url_path


def test_encode_url_path_keeps_apostrophe() -> None:
    path = "/2017-2018/Premiership_Women's/"
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
