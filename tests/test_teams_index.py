"""Tests for rugby.team_pages.generate_teams_index's teams.json sidecar."""

from __future__ import annotations

import json
from pathlib import Path

import rugby.team_pages as team_pages


def test_generate_teams_index_writes_teams_json_sidecar(tmp_path: Path, monkeypatch) -> None:
    dist_dir = tmp_path / "dist"
    teams_dir = dist_dir / "teams"
    teams_dir.mkdir(parents=True)
    (teams_dir / "Bath.html").write_text("<html></html>", encoding="utf-8")
    (teams_dir / "Exeter_Chiefs.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(team_pages, "DIST_DIR", dist_dir)

    team_pages.generate_teams_index()

    teams_json_path = teams_dir / "teams.json"
    assert teams_json_path.is_file()
    payload = json.loads(teams_json_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert len(payload) == 2
    for entry in payload:
        assert set(entry.keys()) == {"file", "name", "img"}

    index_html = (teams_dir / "index.html").read_text(encoding="utf-8")
    assert "fetch('teams.json')" in index_html
    # The full team catalogue must no longer be inlined into the HTML.
    assert "const teams = [" not in index_html
    assert "Bath" not in index_html
    assert "Exeter" not in index_html
    # Data fetched from teams.json is untrusted; it must be HTML-escaped
    # client-side before being interpolated into innerHTML.
    assert "escapeHtml" in index_html


def test_generate_teams_index_teams_json_is_compact(tmp_path: Path, monkeypatch) -> None:
    dist_dir = tmp_path / "dist"
    teams_dir = dist_dir / "teams"
    teams_dir.mkdir(parents=True)
    (teams_dir / "Bath.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(team_pages, "DIST_DIR", dist_dir)

    team_pages.generate_teams_index()

    text = (teams_dir / "teams.json").read_text(encoding="utf-8")
    assert "\n" not in text
    assert ", " not in text
