"""Tests for rugby.analysis.tier_streaks."""

from __future__ import annotations

from rugby.analysis.tier_streaks import (
    COVID_GAP_SEASONS,
    LeagueHistoryEntry,
    _bridgeable_season_gap,
    _collect_streaks_for_team,
)


def _entry(
    season: str,
    tier_num: int,
    *,
    tier_name: str = "Test",
    team_name: str = "Example FC",
) -> LeagueHistoryEntry:
    return LeagueHistoryEntry(
        season=season,
        league="Example League",
        league_url="",
        position=1,
        league_team_count=10,
        tier=(tier_num, tier_name),
        tier_display=str(tier_num),
        is_merit=False,
        competition_key="",
        team_name=team_name,
    )


def test_covid_gap_missing_does_not_break_same_tier_streak() -> None:
    seasons = ["2018-2019", "2019-2020", "2020-2021", "2021-2022", "2022-2023"]
    history = [
        _entry("2018-2019", 9),
        _entry("2019-2020", 9),
        _entry("2021-2022", 9),
        _entry("2022-2023", 9),
    ]
    streaks = _collect_streaks_for_team("1", "Example FC", history, seasons)
    mens = [s for s in streaks if s.gender == "men's"]
    assert len(mens) == 1
    assert mens[0].length == 5
    assert mens[0].start_season == "2018-2019"
    assert mens[0].end_season == "2022-2023"


def test_covid_gap_with_tier_change_still_breaks_streak() -> None:
    seasons = ["2018-2019", "2019-2020", "2020-2021", "2021-2022", "2022-2023"]
    history = [
        _entry("2018-2019", 1),
        _entry("2019-2020", 1),
        _entry("2020-2021", 2, tier_name="Championship"),
        _entry("2021-2022", 1),
        _entry("2022-2023", 1),
    ]
    streaks = _collect_streaks_for_team("1", "Example FC", history, seasons)
    mens = [s for s in streaks if s.gender == "men's"]
    lengths = sorted((s.length, s.start_season, s.end_season) for s in mens)
    assert (2, "2021-2022", "2022-2023") in lengths
    assert (2, "2018-2019", "2019-2020") in lengths
    assert not any(s.length >= 4 for s in mens)


def test_bridgeable_season_gap_spans_covid_missing_season() -> None:
    seasons = ["2019-2020", "2020-2021", "2021-2022", "2022-2023"]
    by_season = {
        "2019-2020": [_entry("2019-2020", 3)],
        "2021-2022": [_entry("2021-2022", 3)],
    }
    assert (
        _bridgeable_season_gap(
            "2019-2020",
            "2021-2022",
            3,
            all_seasons=seasons,
            by_season=by_season,
            mens=True,
        )
        == 2
    )


def test_non_covid_missing_season_is_not_bridgeable() -> None:
    seasons = ["2017-2018", "2018-2019", "2019-2020", "2020-2021"]
    by_season = {
        "2017-2018": [_entry("2017-2018", 3)],
        "2020-2021": [_entry("2020-2021", 3)],
    }
    assert (
        _bridgeable_season_gap(
            "2017-2018",
            "2020-2021",
            3,
            all_seasons=seasons,
            by_season=by_season,
            mens=True,
        )
        is None
    )


def test_covid_gap_seasons_constant() -> None:
    assert frozenset({"2020-2021", "2021-2022"}) == COVID_GAP_SEASONS
