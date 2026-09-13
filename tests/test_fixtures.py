"""Tests for rugby.fixtures league discovery and card parsing."""

from __future__ import annotations

import json
from pathlib import Path

from bs4 import BeautifulSoup

from rugby.fixtures import (
    _discover_fixture_only_leagues,
    _fixture_sort_key,
    _is_placeholder_team_name,
    _parse_fixture_card,
    _should_preserve_existing_fixtures,
    _skip_incomplete_rescrape,
    expected_fixture_count,
    find_incomplete_fixture_leagues,
    normalize_fixtures,
)

_WALKOVER_CARD_HTML = """
<div class="coh-style-card-scores">
  <div class="coh-style-half-width-layout">
    <div class="coh-style-hometeam">
      <a href="/fixtures-and-results/search-results?team=1272">Banbury III</a>
    </div>
  </div>
  <div class="fnr-scores">
    <span class="coh-style-comp-versace">HWO</span>
  </div>
  <div class="coh-style-narrow-width-layout">
    <div class="coh-style-away-team">
      <a href="/fixtures-and-results/search-results?team=9607">Harwell</a>
    </div>
  </div>
</div>
"""


_PLACEHOLDER_CARD_HTML = """
<div class="coh-style-card-scores">
  <div class="coh-style-half-width-layout">
    <div class="coh-style-hometeam">
      <a href="/fixtures-and-results/search-results?team=94438">TBC</a>
    </div>
  </div>
  <div class="fnr-scores">
    <a class="coh-style-comp-time" href="/fixtures-and-results/match-centre-community?matchId=1313560">15:00</a>
  </div>
  <div class="coh-style-narrow-width-layout">
    <div class="coh-style-away-team">
      <a href="/fixtures-and-results/search-results?team=1943">Belsize Park Bulls</a>
    </div>
  </div>
</div>
"""


def test_is_placeholder_team_name() -> None:
    assert _is_placeholder_team_name("TBC")
    assert _is_placeholder_team_name("To be arranged")
    assert not _is_placeholder_team_name("Banbury III")


def test_parse_fixture_card_skips_placeholder_team() -> None:
    card = BeautifulSoup(_PLACEHOLDER_CARD_HTML, "html.parser").find(
        "div", class_="coh-style-card-scores"
    )
    assert card is not None
    assert _parse_fixture_card(card, "2026-09-26") is None


def test_normalize_fixtures_dedupes_same_date_and_orientation() -> None:
    fixtures = [
        {
            "date": "2026-09-26",
            "time": "",
            "home_team_id": 1,
            "away_team_id": 2,
            "match_url": "https://example.com/a",
        },
        {
            "date": "2026-09-26",
            "time": "",
            "home_team_id": 1,
            "away_team_id": 2,
            "match_url": "https://example.com/b",
            "home_score": 10,
            "away_score": 5,
        },
        {
            "date": "2026-09-26",
            "time": "",
            "home_team_id": 94438,
            "away_team_id": 94438,
            "match_url": "https://example.com/self",
        },
    ]
    normalized = normalize_fixtures(fixtures)
    assert len(normalized) == 1
    assert normalized[0]["home_score"] == 10
    assert normalized[0]["match_url"] == "https://example.com/b"


def test_expected_fixture_count_is_double_round_robin() -> None:
    assert expected_fixture_count(10) == 90
    assert expected_fixture_count(2) == 2
    assert expected_fixture_count(1) == 0


def test_skip_incomplete_rescrape_excludes_gallagher_premiership() -> None:
    assert _skip_incomplete_rescrape(Path("Premiership.json"))
    assert not _skip_incomplete_rescrape(Path("merit/Hampshire/Premiership_North.json"))


def test_should_preserve_existing_fixtures_on_empty_rescrape() -> None:
    assert _should_preserve_existing_fixtures(120, 0, "2022-2023")
    assert not _should_preserve_existing_fixtures(0, 0, "2022-2023")
    assert not _should_preserve_existing_fixtures(90, 72, "2025-2026")


def test_should_preserve_existing_fixtures_on_historical_shrink() -> None:
    assert _should_preserve_existing_fixtures(30, 9, "2015-2016")
    assert not _should_preserve_existing_fixtures(30, 9, "2025-2026")


def test_find_incomplete_fixture_leagues(tmp_path, monkeypatch) -> None:
    import rugby.fixtures as fx

    league_dir = tmp_path / "league_data" / "2025-2026"
    fixture_dir = tmp_path / "fixture_data" / "2025-2026"
    league_dir.mkdir(parents=True)
    fixture_dir.mkdir(parents=True)

    league_path = league_dir / "Test_League.json"
    league_path.write_text(
        json.dumps(
            {
                "league_name": "Test League",
                "league_url": "https://www.englandrugby.com/fixtures-and-results/search-results?division=1",
                "teams": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
                "team_count": 3,
            }
        ),
        encoding="utf-8",
    )
    (fixture_dir / "Test_League.json").write_text(
        json.dumps({"league_name": "Test League", "league_url": "", "fixtures": [{}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(fx, "DATA_DIR", tmp_path)
    rows = find_incomplete_fixture_leagues("2025-2026")
    assert len(rows) == 1
    assert rows[0]["expected_count"] == 6
    assert rows[0]["actual_count"] == 0


def test_fixture_sort_key_orders_by_date_then_team_ids() -> None:
    fixtures = [
        {"date": "2026-01-01", "home_team_id": 2, "away_team_id": 1},
        {"date": "2025-12-31", "home_team_id": 9, "away_team_id": 8},
        {"date": "2026-01-01", "home_team_id": 1, "away_team_id": 2},
    ]
    fixtures.sort(key=_fixture_sort_key)
    assert [f["home_team_id"] for f in fixtures] == [9, 1, 2]


def test_parse_fixture_card_walkover_from_versace_span() -> None:
    card = BeautifulSoup(_WALKOVER_CARD_HTML, "html.parser").find(
        "div", class_="coh-style-card-scores"
    )
    assert card is not None
    fixture = _parse_fixture_card(card, "2026-04-25")
    assert fixture is not None
    assert fixture["status"] == "HWO"
    assert fixture["home_team_id"] == 1272
    assert fixture["away_team_id"] == 9607
    assert "home_score" not in fixture
    assert "away_score" not in fixture


def test_discover_leagues_from_fixture_data(tmp_path: Path, monkeypatch) -> None:
    import rugby.fixtures as fx

    fixture_dir = tmp_path / "fixture_data" / "2026-2027"
    (fixture_dir / "merit" / "Essex").mkdir(parents=True)

    def write(relative: str, payload: dict[str, object]) -> None:
        (fixture_dir / relative).write_text(json.dumps(payload), encoding="utf-8")

    rfu = "https://www.englandrugby.com/fixtures-and-results/search-results"
    write(
        "Championship.json",
        {"league_name": "Championship", "league_url": f"{rfu}?division=1", "fixtures": []},
    )
    write(
        "merit/Essex/Division_2.json",
        {"league_name": "Division 2", "league_url": f"{rfu}?division=2", "fixtures": []},
    )
    write(
        "Counties_2_Essex_U20.json",
        {"league_name": "Counties 2 Essex U20", "league_url": f"{rfu}?division=3", "fixtures": []},
    )
    write("No_Url.json", {"league_name": "No Url", "league_url": "", "fixtures": []})
    write(
        "Offsite.json",
        {"league_name": "Offsite", "league_url": "https://example.com/x", "fixtures": []},
    )

    monkeypatch.setattr(fx, "DATA_DIR", tmp_path)
    discovered = fx._discover_leagues_from_fixture_data("2026-2027")

    assert discovered == [
        ("Championship", f"{rfu}?division=1", Path("Championship.json"), False),
        ("Division 2", f"{rfu}?division=2", Path("merit/Essex/Division_2.json"), False),
    ]


def test_fixture_only_merit_leagues_use_competition_subdir(tmp_path: Path) -> None:
    sidecar = [
        {
            "name": "Premiership South East",
            "url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=202&season=2026-2027&division=71150#tables",
            "parent_url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=202&season=2026-2027",
        },
        {
            "name": "Table 1",
            "url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=209&season=2026-2027&division=68220#tables",
            "parent_url": "https://www.englandrugby.com/fixtures-and-results/search-results?competition=209&season=2026-2027",
        },
    ]
    league_dir = tmp_path / "2026-2027"
    league_dir.mkdir()
    (league_dir / "_fixture_only_leagues.json").write_text(json.dumps(sidecar), encoding="utf-8")

    discovered = _discover_fixture_only_leagues(league_dir)

    assert discovered == [
        (
            "Premiership South East",
            sidecar[0]["url"],
            Path("merit/Hampshire/Premiership_South_East.json"),
        ),
        (
            "Table 1",
            sidecar[1]["url"],
            Path("merit/Herts_Middlesex/Table_1.json"),
        ),
    ]
