"""Tests for the stats page weekly results report data."""

import json
from datetime import date

from rugby.weekly_report import compute_weekly_season, saturday_of_week, write_weekly_report


class TestSaturdayOfWeek:
    def test_wednesday_to_tuesday_window(self):
        saturday = date(2024, 10, 5)
        for day in range(2, 9):  # Wed 2 Oct .. Tue 8 Oct
            assert saturday_of_week(date(2024, 10, day)) == saturday

    def test_boundaries_roll_to_neighbouring_weeks(self):
        assert saturday_of_week(date(2024, 10, 1)) == date(2024, 9, 28)  # Tue
        assert saturday_of_week(date(2024, 10, 9)) == date(2024, 10, 12)  # Wed


def _write_league(path, league_name, fixtures):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"league_name": league_name, "league_url": "", "fixtures": fixtures}),
        encoding="utf-8",
    )


def _fixture(ds, home, away, hs=None, as_=None, **extra):
    f = {
        "date": ds,
        "time": "",
        "home_team_id": home,
        "away_team_id": away,
        "match_url": f"https://example.com/{ds}/{home}/{away}",
    }
    if hs is not None:
        f["home_score"] = hs
        f["away_score"] = as_
    f.update(extra)
    return f


class TestComputeWeeklySeason:
    def test_groups_scored_fixtures_by_week(self, tmp_path):
        season_dir = tmp_path / "2024-2025"
        _write_league(
            season_dir / "Premiership.json",
            "Premiership",
            [
                _fixture("2024-10-04", 1, 2, 30, 10),
                _fixture("2024-10-06", 3, 1, 7, 45),
                _fixture("2024-10-12", 2, 3, 20, 20),
                _fixture("2024-10-19", 1, 3),  # unplayed
                _fixture("2024-10-19", 2, 1, status="HWO"),
            ],
        )
        payload = compute_weekly_season(season_dir, {1: "Alpha", 2: "Beta"}, {1: "Alpha.html"})

        assert payload is not None
        assert list(payload["weeks"]) == ["2024-10-05", "2024-10-12"]
        first_week = payload["weeks"]["2024-10-05"]
        assert first_week[0] == [-1, 0, 1, 30, 10, 0]
        assert first_week[1][0] == 1  # Sunday
        assert payload["teams"][0] == ["Alpha", "Alpha.html"]
        assert payload["teams"][2] == ["Team 3", ""]
        assert payload["leagues"] == [["Premiership", "1"]]
        assert payload["levels"][0]["group"] == "mens"

    def test_dedupes_by_match_url_and_skips_unscored_seasons(self, tmp_path):
        season_dir = tmp_path / "2024-2025"
        dup = _fixture("2024-10-05", 1, 2, 10, 5)
        _write_league(season_dir / "Premiership.json", "Premiership", [dup, dup])
        payload = compute_weekly_season(season_dir, {}, {})
        assert payload is not None
        assert len(payload["weeks"]["2024-10-05"]) == 1

        empty_dir = tmp_path / "2025-2026"
        _write_league(empty_dir / "Premiership.json", "Premiership", [_fixture("2025-10-04", 1, 2)])
        assert compute_weekly_season(empty_dir, {}, {}) is None

    def test_drops_dates_outside_the_season(self, tmp_path):
        season_dir = tmp_path / "2019-2020"
        _write_league(
            season_dir / "Premiership.json",
            "Premiership",
            [
                _fixture("2020-10-10", 1, 2, 20, 10),  # Covid-delayed, kept
                _fixture("2026-09-12", 1, 2, 30, 10),  # junk date, dropped
            ],
        )
        payload = compute_weekly_season(season_dir, {}, {})
        assert payload is not None
        assert list(payload["weeks"]) == ["2020-10-10"]


class TestWriteWeeklyReport:
    def test_writes_sidecars_and_versioned_index(self, tmp_path):
        fixture_dir = tmp_path / "fixture_data"
        _write_league(
            fixture_dir / "2024-2025" / "Premiership.json",
            "Premiership",
            [_fixture("2024-10-05", 1, 2, 10, 5)],
        )
        out_dir = tmp_path / "weekly"
        index = write_weekly_report(out_dir, fixture_dir, team_names={}, team_hrefs={})

        assert [entry["season"] for entry in index] == ["2024-2025"]
        assert index[0]["weeks"] == [["2024-10-05", 1]]
        assert len(index[0]["v"]) == 12
        assert (out_dir / "2024-2025.json").exists()
