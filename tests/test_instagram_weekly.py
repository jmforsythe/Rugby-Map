"""Tests for the weekly report ranking and its Instagram/TikTok carousel text."""

import json
import re
from datetime import date
from pathlib import Path

import pytest

from rugby.analysis.instagram_leaderboard import (
    API_IMAGE_HEIGHT,
    API_IMAGE_WIDTH,
    GRID_SAFE_WIDTH,
    GRID_SIDE_TRIM,
    MARGIN_X,
    render_leaderboard_svg,
)
from rugby.analysis.instagram_weekly import (
    CAPTION_HASHTAGS,
    CAROUSEL_CATEGORIES,
    FORMATS,
    Slide,
    TeamInfo,
    WeeklyCarouselData,
    _entry,
    _level_row_label,
    build_caption,
    most_recent_saturday,
    publish_weekly_instagram,
    rank_carousel,
    rank_score_per_level,
)
from rugby.seo import BASE_URL
from rugby.weekly_report import ScoredFixture, WeeklyLevel, rank_week

_LEVEL = WeeklyLevel(key="7", label="Counties 1", group="mens")
_LEVEL_MENS = WeeklyLevel(key="6", label="National League 2", group="mens")
_LEVEL_WOMENS = WeeklyLevel(key="104", label="Women's NC 1", group="womens")
_LEVEL_MERIT = WeeklyLevel(key="merit:Hampshire", label="Hampshire Merit", group="merit")


def _fx(
    day: int,
    home: int,
    away: int,
    hs: int,
    aws: int,
    *,
    level: WeeklyLevel = _LEVEL,
    league_name: str = "Counties 1 Test",
) -> ScoredFixture:
    return ScoredFixture(
        date=date(2026, 9, day),
        home_id=home,
        away_id=away,
        home_score=hs,
        away_score=aws,
        league_name=league_name,
        level=level,
    )


TEAMS = {1: TeamInfo("Alpha", None), 2: TeamInfo("Beta", None), 3: TeamInfo("Gamma", None)}


class TestRankWeek:
    def test_categories_and_perspective(self):
        results = [_fx(26, 1, 2, 10, 60), _fx(26, 3, 1, 45, 40), _fx(27, 2, 3, 20, 20)]
        ranked = rank_week(results)

        top_score = ranked["team_score"][0]
        assert (top_score.side, top_score.value) == ("away", 60)
        assert ranked["winning_margin"][0].value == 50
        assert [r.value for r in ranked["aggregate"]] == [85, 70, 40]
        # Draws have no loser; the 40 in a 45-40 defeat tops the list.
        assert [(r.side, r.value) for r in ranked["points_in_defeat"]] == [
            ("away", 40),
            ("home", 10),
        ]

    def test_ties_prefer_bigger_margin_then_earliest(self):
        results = [_fx(27, 1, 2, 50, 10), _fx(26, 3, 1, 50, 45), _fx(26, 2, 3, 50, 10)]
        top = rank_week(results)["team_score"]
        assert [r.fixture.date.day for r in top[:3]] == [26, 27, 26]

    def test_top_limit(self):
        results = [_fx(26, 1, 2, n, 0) for n in range(1, 20)]
        assert len(rank_week(results, top=5)["aggregate"]) == 5


class TestRankCarousel:
    def test_categories_in_slide_order(self):
        assert [key for key, _ in CAROUSEL_CATEGORIES] == [
            "mens_score",
            "womens_score",
            "merit_score",
            "points_in_defeat",
            "aggregate",
        ]

    def test_team_scores_split_by_level_group(self):
        results = [
            _fx(26, 1, 2, 90, 0, level=_LEVEL_MENS),
            _fx(26, 2, 3, 70, 5, level=_LEVEL_WOMENS),
            _fx(26, 3, 1, 50, 45, level=_LEVEL_MERIT),
        ]
        ranked = rank_carousel(results)
        assert [r.value for r in ranked["mens_score"]] == [90, 0]
        assert [r.value for r in ranked["womens_score"]] == [70, 5]
        assert [r.value for r in ranked["merit_score"]] == [50, 45]
        # Losing and combined scores stay league-wide.
        assert ranked["points_in_defeat"][0].value == 45
        assert ranked["aggregate"][0].value == 95

    def test_group_without_results_is_empty(self):
        ranked = rank_carousel([_fx(26, 1, 2, 30, 10, level=_LEVEL_MENS)])
        assert ranked["womens_score"] == []
        assert ranked["merit_score"] == []


class TestScorePerLevel:
    def test_pyramid_best_score_per_tier_in_tier_order(self):
        tier_1 = WeeklyLevel(key="1", label="Premiership", group="mens")
        results = [
            _fx(26, 1, 2, 30, 10, level=_LEVEL),
            _fx(26, 2, 3, 70, 0, level=_LEVEL),
            _fx(26, 3, 1, 40, 45, level=tier_1),
            _fx(26, 1, 2, 90, 0, level=_LEVEL_WOMENS),
        ]
        ranked = rank_score_per_level(results, "mens")
        assert [r.fixture.level["key"] for r in ranked] == ["1", "7"]
        assert [r.value for r in ranked] == [45, 70]

    def test_merit_ordered_by_competition_name(self):
        surrey = WeeklyLevel(key="merit:Surrey", label="Surrey Merit", group="merit")
        results = [_fx(26, 1, 2, 50, 0, level=surrey), _fx(26, 2, 3, 20, 5, level=_LEVEL_MERIT)]
        ranked = rank_score_per_level(results, "merit")
        assert [r.fixture.level["label"] for r in ranked] == ["Hampshire Merit", "Surrey Merit"]

    def test_row_labels(self):
        assert _level_row_label(_LEVEL_MENS) == "National League 2"
        assert _level_row_label(_LEVEL_MERIT) == "Hampshire"
        nc = WeeklyLevel(key="104", label="National Challenge 1", group="womens")
        assert _level_row_label(nc) == "National Challenge 1"
        prem = WeeklyLevel(key="101", label="Premiership Women's", group="womens")
        assert _level_row_label(prem) == "Premiership"


class TestPublishWeeklyInstagram:
    def test_publish_dir_must_be_under_dist(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="must be under"):
            publish_weekly_instagram(date(2026, 9, 26), tmp_path / "social" / "weekly")

    def test_publish_writes_post_json_with_absolute_urls(self, tmp_path: Path, monkeypatch) -> None:
        dist_social = tmp_path / "dist" / "social" / "weekly"
        monkeypatch.setattr("rugby.analysis.instagram_weekly.DIST_DIR", tmp_path / "dist")

        def _fake_load(_saturday, *, levels="default", top=10):
            return WeeklyCarouselData(
                saturday=date(2026, 9, 26),
                results=[],
                ranked={key: [] for key, _ in CAROUSEL_CATEGORIES},
                teams={},
                level_label="Test",
                data_as_of=None,
                slides=[Slide("00_cover", "Title", "Sub", [], headline="26 SEP 2026")],
            )

        def _fake_write(*_args, **_kwargs):
            png = tmp_path / "slide.png"
            png.write_bytes(b"png")
            return [png]

        monkeypatch.setattr(
            "rugby.analysis.instagram_weekly._load_weekly_carousel_data", _fake_load
        )
        monkeypatch.setattr("rugby.analysis.instagram_weekly.write_leaderboard", _fake_write)
        monkeypatch.setattr(
            "rugby.analysis.instagram_weekly.build_caption", lambda *a, **k: "caption"
        )
        monkeypatch.setattr("rugby.analysis.instagram_weekly._png_to_jpeg", lambda *_a, **_k: None)

        post_path = publish_weekly_instagram(date(2026, 9, 26), dist_social)
        post = json.loads(post_path.read_text(encoding="utf-8"))
        assert post["week"] == "2026-09-26"
        assert post["caption"] == "caption"
        assert post["assets"] == [f"{BASE_URL}/social/weekly/2026-09-26/instagram/00_cover.jpg"]
        assert not (
            tmp_path / "dist" / "social" / "weekly" / "2026-09-26" / "00_cover.svg"
        ).exists()


class TestInstagramFormat:
    def test_api_aspect_ratio(self) -> None:
        fmt = FORMATS["instagram"]
        assert fmt.svg_width / fmt.jpeg_size[0] == fmt.svg_height / fmt.jpeg_size[1]
        assert fmt.jpeg_size == (1080, 1350)
        assert fmt.jpeg_size[0] / fmt.jpeg_size[1] == 0.8  # 4:5

    def test_layout_fits_grid_preview_safe_zone(self) -> None:
        """Card width must sit inside the profile grid's center 3:4 crop."""
        assert GRID_SAFE_WIDTH == 1012
        assert GRID_SIDE_TRIM == 34
        content_width = API_IMAGE_WIDTH - 2 * MARGIN_X
        assert content_width <= GRID_SAFE_WIDTH
        assert MARGIN_X >= GRID_SIDE_TRIM
        assert API_IMAGE_HEIGHT * 3 // 4 == GRID_SAFE_WIDTH


class TestCoverLayout:
    def test_cover_headline_sits_below_title(self) -> None:
        svg = render_leaderboard_svg(
            "Weekly rugby round-up",
            [],
            subtitle="Week containing Sat 26 Sep 2026 · 817 results · Pyramid + merit leagues",
            headline="26 SEP 2026",
            cover_layout=True,
        )
        header_texts = re.findall(
            r'<text x="64" y="([\d.]+)" font-family="[^"]+" font-size="([\d.]+)" '
            r'font-weight="700"[^>]*>([^<]+)</text>',
            svg,
        )
        title = next(t for t in header_texts if t[2] == "WEEKLY RUGBY ROUND-UP")
        headline = next(t for t in header_texts if "SEP" in t[2])
        title_bottom = float(title[0]) + float(title[1]) * 0.25
        headline_top = float(headline[0]) - float(headline[1]) * 0.75
        assert headline_top >= title_bottom + 15


class TestCarouselText:
    def test_most_recent_saturday(self):
        assert most_recent_saturday(date(2026, 9, 30)) == date(2026, 9, 26)  # Wed
        assert most_recent_saturday(date(2026, 9, 26)) == date(2026, 9, 26)  # Sat
        assert most_recent_saturday(date(2026, 9, 27)) == date(2026, 9, 26)  # Sun

    def test_entries_read_from_the_ranked_teams_side(self):
        ranked = rank_week([_fx(26, 1, 2, 7, 90)])
        margin = _entry("winning_margin", ranked["winning_margin"][0], TEAMS)
        assert (margin.team_name, margin.value) == ("Beta", "+83")
        assert margin.detail == "90–7 v Alpha · Counties 1 Test"

        aggregate = _entry("aggregate", ranked["aggregate"][0], TEAMS, headline=True)
        assert aggregate.team_name == "Alpha v Beta"
        assert aggregate.category_label == "Highest combined score"
        assert aggregate.detail == "7–90"
        # An aggregate belongs to both teams, so the row carries the away crest too.
        assert aggregate.second_name == "Beta"
        assert margin.second_name is None

        level_margin = _entry(
            "winning_margin",
            rank_week([_fx(26, 1, 2, 50, 10, level=_LEVEL_MENS)])["winning_margin"][0],
            TEAMS,
            detail_suffix="National League 2",
        )
        assert level_margin.detail == "50–10 v Beta · National League 2"

    def test_league_sponsors_are_dropped(self):
        fx = ScoredFixture(
            date=date(2026, 9, 26),
            home_id=1,
            away_id=2,
            home_score=30,
            away_score=0,
            league_name="Counties 4 Tribute Ale Somerset North",
            level=_LEVEL,
        )
        entry = _entry("team_score", rank_week([fx])["team_score"][0], TEAMS)
        assert entry.detail == "30–0 v Beta · Counties 4 Somerset North"

    def test_caption_summarises_week(self):
        results = [_fx(26, 1, 2, 10, 60), _fx(26, 3, 1, 45, 40)]
        caption = build_caption(
            date(2026, 9, 26), results, rank_carousel(results), TEAMS, "Men's + women's pyramid"
        )
        assert caption.startswith("Weekly rugby round-up — weekend of Sat 26 Sep 2026")
        assert "2 results and 155 points" in caption
        assert "· Highest men's score: Alpha 10–60 Beta (Counties 1 Test)" in caption
        # The 45-40 tops both losing and combined score, so it gets one merged line.
        assert (
            "· Highest losing score & highest combined score: Gamma 45–40 Alpha (Counties 1 Test)"
            in caption
        )
        assert caption.count("Gamma 45–40 Alpha") == 1
        assert "/stats/?week=2026-09-26" in caption
        assert caption.endswith(CAPTION_HASHTAGS)
