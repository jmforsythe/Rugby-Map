"""Tests for match_day fixture popup HTML and client binding."""

from __future__ import annotations

from rugby.match_day import _render_popup


def _sample_fixture(**overrides):
    base = {
        "match_url": "https://example.com/m/1",
        "home_score": None,
        "away_score": None,
        "status": None,
        "time": "14:30",
    }
    base.update(overrides)
    return base  # type: ignore[return-value]


def _sample_team(name: str = "Home FC") -> dict:
    return {
        "name": name,
        "image_url": "https://example.com/logo.png",
        "formatted_address": "1 Test Street",
    }


def test_render_popup_matches_live_site_markup() -> None:
    html = _render_popup(
        _sample_fixture(),
        "National League 1",
        "National League 1",
        _sample_team("Bath"),
        _sample_team("Sale"),
    )
    assert 'style="min-width:240px;font-family:sans-serif"' in html
    assert "<b>League:</b>" in html
    assert 'class="rugby-popup"' not in html
    assert "14:30" in html


def test_render_popup_leaves_blank_centre_when_kickoff_missing() -> None:
    html = _render_popup(
        _sample_fixture(time=""),
        "League",
        "Tier",
        _sample_team(),
        _sample_team("Away FC"),
    )
    assert '<div style="font-size:18px;font-weight:bold">    </div>' in html
