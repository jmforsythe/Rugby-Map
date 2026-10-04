"""Turn the stats page's weekly report into a ready-to-post carousel.

Renders a cover slide (the week's #1 in each category) plus one top-10 slide
per category from :mod:`rugby.weekly_report`, using the
:mod:`rugby.analysis.instagram_leaderboard` card template, then writes:

- ``output/instagram/weekly/<saturday>/<format>/*.svg|png``: the renders
- ``output/instagram/upload/weekly-<saturday>[-tiktok]/*.jpg``: upload-ready JPEGs
- ``output/instagram/queue/weekly-<saturday>[-tiktok].json``: caption + asset
  list, in the same shape as the other queued posts

``--publish-dir`` writes only what the Instagram API pipeline needs — JPEGs and
``post.json`` under ``dist/social/weekly/<saturday>/`` — and replaces any prior
``dist/social/`` tree (ephemeral hosting, not kept on the site long term).

Formats: ``instagram`` is 4:5 (1080×1350 JPEGs) for the Graph API, with layout
margins that survive the profile grid's 3:4 preview crop; ``tiktok`` is 9:16
(1080×1920) for photo mode.

Usage::

    python -m rugby.analysis.instagram_weekly                    # most recent Saturday
    python -m rugby.analysis.instagram_weekly --week 2026-09-26
    python -m rugby.analysis.instagram_weekly --levels pyramid --format tiktok --mode dark
    python -m rugby.analysis.instagram_weekly --publish-dir dist/social/weekly --week 2026-09-26
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from core.config import DIST_DIR, REPO_ROOT, setup_logging
from rugby import DATA_DIR
from rugby.analysis.instagram_leaderboard import (
    RANK_COL_WIDTH,
    SITE_HOST,
    LeaderboardEntry,
    _latest_data_timestamp,
    write_leaderboard,
)
from rugby.display_names import short_league_name, short_team_name
from rugby.seo import absolute_url
from rugby.weekly_report import (
    ALL_LEVEL_GROUPS,
    DEFAULT_LEVEL_GROUPS,
    DEFAULT_LEVEL_LABEL,
    WEEKLY_CATEGORIES,
    RankedResult,
    ScoredFixture,
    load_week_results,
    rank_week,
    saturday_of_week,
)

# (key, title) for the carousel's cover rows and slides, in display order. Kept
# separate from WEEKLY_CATEGORIES, which the stats page also ranks by: team score
# is split by level group so men's, women's and merit each get their own slide.
CAROUSEL_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("mens_score", "Highest men's score"),
    ("womens_score", "Highest women's score"),
    ("merit_score", "Highest merit score"),
    ("points_in_defeat", "Highest losing score"),
    ("aggregate", "Highest combined score"),
)
_SCORE_GROUPS = {"mens_score": "mens", "womens_score": "womens", "merit_score": "merit"}
_SCORE_SCOPE_LABELS = {
    "mens_score": "Men's pyramid",
    "womens_score": "Women's pyramid",
    "merit_score": "Merit leagues",
}
# Slide before each group's top 10: its best score at every level (stem suffix, title).
_PER_LEVEL_SLIDES = {
    "mens_score": ("mens_by_level", "Highest men's score at each level"),
    "womens_score": ("womens_by_level", "Highest women's score at each level"),
    "merit_score": ("merit_by_competition", "Highest score in each merit competition"),
}
LEVEL_LABEL_COL_WIDTH = 184  # level / competition name instead of rank numbers
_CATEGORY_TITLES = dict(WEEKLY_CATEGORIES) | dict(CAROUSEL_CATEGORIES)

# Caption template (Instagram and TikTok share the same wording and hashtags).
CAPTION_HASHTAGS = "#rugby #rugbyunion #grassrootsrugby #englishrugby #rugbyresults"
CAPTION_CTA = "Swipe for the top 10s and the best score at every level."

INSTAGRAM_ROOT = REPO_ROOT / "output" / "instagram"
RENDER_ROOT = INSTAGRAM_ROOT / "weekly"
UPLOAD_ROOT = INSTAGRAM_ROOT / "upload"
QUEUE_ROOT = INSTAGRAM_ROOT / "queue"

_TEAM_ID_RE = re.compile(r"[?&]team=(\d+)")


@dataclass(frozen=True, slots=True)
class SlideFormat:
    key: str
    svg_width: int
    svg_height: int
    # Final upload JPEG size.
    jpeg_size: tuple[int, int]
    post_suffix: str


FORMATS: dict[str, SlideFormat] = {
    # 4:5 for Graph API; leaderboard margins keep content inside the grid's 3:4 crop.
    "instagram": SlideFormat("instagram", 1080, 1350, (1080, 1350), ""),
    "tiktok": SlideFormat("tiktok", 1080, 1920, (1080, 1920), "-tiktok"),
}

LEVEL_CHOICES: dict[str, tuple[frozenset[str], str]] = {
    "default": (DEFAULT_LEVEL_GROUPS, DEFAULT_LEVEL_LABEL),
    "pyramid": (frozenset({"mens", "womens"}), "Men's + women's pyramid"),
    "all": (ALL_LEVEL_GROUPS, "All levels"),
}


@dataclass(frozen=True, slots=True)
class TeamInfo:
    name: str
    logo_url: str | None


def most_recent_saturday(today: date) -> date:
    """The latest Saturday on or before ``today``."""
    return today - timedelta(days=(today.weekday() - 5) % 7)


def _team_lookup(saturday: date) -> dict[int, TeamInfo]:
    """RFU team id -> name/crest from the season(s) the week can fall in (later wins)."""
    lookup: dict[int, TeamInfo] = {}
    for y0 in (saturday.year - 1, saturday.year):
        season_dir = DATA_DIR / "league_data" / f"{y0}-{y0 + 1}"
        if not season_dir.is_dir():
            continue
        for league_file in sorted(season_dir.rglob("*.json")):
            if league_file.name.startswith("_"):
                continue
            with open(league_file, encoding="utf-8") as f:
                league = json.load(f)
            for team in league.get("teams", []):
                match = _TEAM_ID_RE.search(team.get("url") or "")
                if match:
                    # Compact display names ("X RFC 2nd XV Men" -> "X II"); the card
                    # renderer shrinks/truncates anything still too wide.
                    lookup[int(match.group(1))] = TeamInfo(
                        short_team_name(team["name"]), team.get("image_url")
                    )
    return lookup


def rank_carousel(results: list[ScoredFixture], top: int = 10) -> dict[str, list[RankedResult]]:
    """Top ``top`` results per ``CAROUSEL_CATEGORIES`` key."""
    league_wide = rank_week(results, top=top)
    ranked: dict[str, list[RankedResult]] = {}
    for key, _title in CAROUSEL_CATEGORIES:
        group = _SCORE_GROUPS.get(key)
        if group is None:
            ranked[key] = league_wide[key]
        else:
            in_group = [fx for fx in results if fx.level["group"] == group]
            ranked[key] = rank_week(in_group, top=top)["team_score"]
    return ranked


def rank_score_per_level(results: list[ScoredFixture], group: str) -> list[RankedResult]:
    """Highest team score at each level in ``group``: pyramid by tier, merit A–Z."""
    by_level: dict[str, list[ScoredFixture]] = {}
    for fx in results:
        if fx.level["group"] == group:
            by_level.setdefault(fx.level["key"], []).append(fx)
    best = [rank_week(fixtures, top=1)["team_score"][0] for fixtures in by_level.values()]
    if group == "merit":
        return sorted(best, key=lambda r: r.fixture.level["label"].casefold())
    return sorted(best, key=lambda r: int(r.fixture.level["key"]))


def _level_row_label(level: dict[str, str]) -> str:
    """Left-column label on per-level slides: tier name or merit competition."""
    label = level["label"]
    if level["group"] == "merit" and label.endswith(" Merit"):
        return label[: -len(" Merit")]
    if label == "Premiership Women's":
        return "Premiership"
    return label


def _score_line(fx: ScoredFixture, side: str | None) -> tuple[int, int]:
    """(first, second) score as read from ``side``'s perspective (home first for matches)."""
    if side == "away":
        return fx.away_score, fx.home_score
    return fx.home_score, fx.away_score


def _entry(
    category: str,
    ranked: RankedResult,
    teams: dict[int, TeamInfo],
    *,
    headline: bool = False,
    detail_suffix: str | None = None,
    rank_label: str | None = None,
) -> LeaderboardEntry:
    fx = ranked.fixture

    def team(team_id: int) -> TeamInfo:
        return teams.get(team_id, TeamInfo(f"Team {team_id}", None))

    home, away = team(fx.home_id), team(fx.away_id)
    first, second = _score_line(fx, ranked.side)
    value = f"+{ranked.value}" if category == "winning_margin" else str(ranked.value)
    title = _CATEGORY_TITLES[category]

    second_name: str | None = None
    second_logo: str | None = None
    if ranked.side is None:
        # A match aggregate belongs to both teams: show both crests.
        name = f"{home.name} v {away.name}"
        detail = f"{first}–{second}"
        logo = home.logo_url
        second_name, second_logo = away.name, away.logo_url
    else:
        own, opponent = (home, away) if ranked.side == "home" else (away, home)
        name = own.name
        detail = f"{first}–{second} v {opponent.name}"
        logo = own.logo_url
    if headline:
        match_detail = detail
    else:
        suffix = detail_suffix if detail_suffix is not None else short_league_name(fx.league_name)
        match_detail = f"{detail} · {suffix}"
    return LeaderboardEntry(
        team_name=name,
        detail=match_detail,
        value=value,
        logo_url=logo,
        category_label=title if headline else None,
        rank_label=rank_label,
        second_name=second_name,
        second_logo_url=second_logo,
    )


def _saturday_label(saturday: date) -> str:
    """``"Sat 26 Sep 2026"``. Weeks are labelled by their Saturday ("weekend of ..."):
    fixtures are almost all Friday to Sunday, so the Wednesday-to-Tuesday window is
    an implementation detail, not a headline."""
    return f"{saturday:%a} {saturday.day} {saturday:%b %Y}"


def _week_containing_label(saturday: date) -> str:
    """Cover pretitle: the Wednesday-to-Tuesday week anchored on ``saturday``."""
    return f"Week containing {_saturday_label(saturday)}"


def build_caption(
    saturday: date,
    results: list[ScoredFixture],
    ranked: dict[str, list[RankedResult]],
    teams: dict[int, TeamInfo],
    level_label: str,
) -> str:
    """Build the weekly carousel caption from the template in :data:`CAPTION_HASHTAGS`."""
    points = sum(fx.home_score + fx.away_score for fx in results)
    lines = [
        f"Weekly rugby round-up — weekend of {_saturday_label(saturday)}",
        f"{len(results):,} results and {points:,} points ({level_label.lower()}).",
        "",
    ]
    # One line per headline match: a 64-59 is often both the highest losing score
    # and the highest combined score, so its labels are merged rather than
    # repeating the match. Keyed by identity: ScoredFixture holds a dict (its
    # level) so isn't hashable.
    headline_labels: dict[int, tuple[ScoredFixture, list[str]]] = {}
    for key, title in CAROUSEL_CATEGORIES:
        if ranked.get(key):
            fx = ranked[key][0].fixture
            headline_labels.setdefault(id(fx), (fx, []))[1].append(title)
    for fx, fx_labels in headline_labels.values():
        home = teams.get(fx.home_id, TeamInfo(f"Team {fx.home_id}", None)).name
        away = teams.get(fx.away_id, TeamInfo(f"Team {fx.away_id}", None)).name
        match = f"{home} {fx.home_score}–{fx.away_score} {away}"
        label = " & ".join([fx_labels[0], *(lb.lower() for lb in fx_labels[1:])])
        lines.append(f"· {label}: {match} ({short_league_name(fx.league_name)})")
    lines += [
        "",
        CAPTION_CTA,
        f"Every result → {SITE_HOST}/stats/?week={saturday.isoformat()}",
        "",
        CAPTION_HASHTAGS,
    ]
    return "\n".join(lines)


def _png_to_jpeg(png_path: Path, jpeg_path: Path, size: tuple[int, int]) -> None:
    from PIL import Image  # noqa: PLC0415

    jpeg_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(png_path) as im:
        im.convert("RGB").resize(size, Image.Resampling.LANCZOS).save(
            jpeg_path, "JPEG", quality=92, optimize=True
        )


@dataclass(frozen=True, slots=True)
class Slide:
    stem: str
    title: str
    subtitle: str
    entries: list[LeaderboardEntry]
    headline: str | None = None
    pretitle: str | None = None
    rank_col_width: int | None = None


@dataclass(frozen=True, slots=True)
class WeeklyCarouselData:
    saturday: date
    results: list[ScoredFixture]
    ranked: dict[str, list[RankedResult]]
    teams: dict[int, TeamInfo]
    level_label: str
    data_as_of: str | None
    slides: list[Slide]


def _render_slide(
    slide: Slide, svg_path: Path, fmt: SlideFormat, *, mode: str, data_as_of: str | None
) -> Path:
    """Render one slide to SVG + PNG; returns the PNG path."""
    written = write_leaderboard(
        slide.title,
        slide.entries,
        svg_path,
        subtitle=slide.subtitle,
        mode=mode,
        data_as_of=data_as_of,
        write_png=True,
        png_scale=2.0,
        width=fmt.svg_width,
        height=fmt.svg_height,
        # Cover rows are one headline per category, not a ranking.
        show_rank=slide.stem != "00_cover",
        headline=slide.headline,
        pretitle=slide.pretitle,
        cover_layout=slide.stem == "00_cover",
        rank_col_width=slide.rank_col_width or RANK_COL_WIDTH,
    )
    return written[-1]


def _load_weekly_carousel_data(
    saturday: date,
    *,
    levels: str = "default",
    top: int = 10,
) -> WeeklyCarouselData:
    level_groups, level_label = LEVEL_CHOICES[levels]
    results = load_week_results(saturday, level_groups=level_groups)
    if not results:
        raise SystemExit(f"No scored results for the week around {saturday.isoformat()}")
    ranked = rank_carousel(results, top=top)
    teams = _team_lookup(saturday)
    data_as_of = _latest_data_timestamp()
    range_label = f"Weekend of {_saturday_label(saturday)}"

    slides = [
        Slide(
            "00_cover",
            "Weekly rugby round-up",
            f"{_week_containing_label(saturday)} · {len(results):,} results · {level_label}",
            [
                _entry(key, ranked[key][0], teams, headline=True)
                for key, _ in CAROUSEL_CATEGORIES
                if ranked[key]
            ],
            # The date is what tells covers apart in the profile grid.
            headline=f"{saturday.day} {saturday:%b %Y}".upper(),
        )
    ]
    for key, title in CAROUSEL_CATEGORIES:
        if not ranked[key]:
            continue
        subtitle = f"{range_label} · {_SCORE_SCOPE_LABELS.get(key, level_label)}"
        if key in _PER_LEVEL_SLIDES:
            stem_suffix, level_title = _PER_LEVEL_SLIDES[key]
            per_level = rank_score_per_level(results, _SCORE_GROUPS[key])
            slides.append(
                Slide(
                    f"{len(slides):02d}_{stem_suffix}",
                    level_title,
                    subtitle,
                    [
                        _entry(key, r, teams, rank_label=_level_row_label(r.fixture.level))
                        for r in per_level
                    ],
                    rank_col_width=LEVEL_LABEL_COL_WIDTH,
                )
            )
        slides.append(
            Slide(
                f"{len(slides):02d}_{key}",
                title,
                subtitle,
                [_entry(key, r, teams) for r in ranked[key]],
            )
        )

    return WeeklyCarouselData(
        saturday=saturday,
        results=results,
        ranked=ranked,
        teams=teams,
        level_label=level_label,
        data_as_of=data_as_of,
        slides=slides,
    )


def publish_weekly_instagram(
    saturday: date,
    publish_dir: Path,
    *,
    levels: str = "default",
    mode: str = "light",
    top: int = 10,
) -> Path:
    """Render Instagram JPEGs and ``post.json`` for ephemeral site hosting.

    Replaces the entire ``dist/social/`` tree (only the current week is kept).
    No SVG/PNG archives, queue files, or TikTok assets.
    """
    publish_dir = publish_dir.resolve()
    if not publish_dir.is_relative_to(DIST_DIR.resolve()):
        raise SystemExit(f"--publish-dir must be under {DIST_DIR}; got {publish_dir}")

    social_root = publish_dir.parent
    if social_root.is_dir():
        shutil.rmtree(social_root)

    data = _load_weekly_carousel_data(saturday, levels=levels, top=top)
    fmt = FORMATS["instagram"]
    week_dir = publish_dir / saturday.isoformat()
    jpeg_dir = week_dir / "instagram"
    jpeg_dir.mkdir(parents=True)

    asset_urls: list[str] = []
    with tempfile.TemporaryDirectory(prefix="instagram-weekly-") as tmp:
        scratch = Path(tmp)
        for slide in data.slides:
            png = _render_slide(
                slide,
                scratch / f"{slide.stem}_{mode}.svg",
                fmt,
                mode=mode,
                data_as_of=data.data_as_of,
            )
            _png_to_jpeg(png, jpeg_dir / f"{slide.stem}.jpg", fmt.jpeg_size)
            site_path = f"/social/weekly/{saturday.isoformat()}/instagram/{slide.stem}.jpg"
            asset_urls.append(absolute_url(site_path))

    caption = build_caption(
        data.saturday,
        data.results,
        data.ranked,
        data.teams,
        data.level_label,
    )
    post_path = week_dir / "post.json"
    post_path.write_text(
        json.dumps(
            {
                "week": saturday.isoformat(),
                "caption": caption,
                "assets": asset_urls,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return post_path


def _clean_output_dir(path: Path) -> None:
    """Remove stale renders from a prior carousel layout."""
    if not path.is_dir():
        return
    for child in path.iterdir():
        if child.is_file():
            child.unlink()


def generate_weekly_carousel(
    saturday: date,
    *,
    levels: str = "default",
    formats: list[str] | None = None,
    mode: str = "light",
    top: int = 10,
) -> list[Path]:
    """Render slides, JPEGs and queue entries for one week; returns the queue files."""
    data = _load_weekly_carousel_data(saturday, levels=levels, top=top)

    queue_files: list[Path] = []
    for fmt_key in formats or list(FORMATS):
        fmt = FORMATS[fmt_key]
        post_id = f"weekly-{saturday.isoformat()}{fmt.post_suffix}"
        _clean_output_dir(RENDER_ROOT / saturday.isoformat() / fmt.key)
        _clean_output_dir(UPLOAD_ROOT / post_id)
        assets: list[str] = []
        for slide in data.slides:
            png = _render_slide(
                slide,
                RENDER_ROOT / saturday.isoformat() / fmt.key / f"{slide.stem}_{mode}.svg",
                fmt,
                mode=mode,
                data_as_of=data.data_as_of,
            )
            jpeg_rel = f"{post_id}/{slide.stem}.jpg"
            _png_to_jpeg(png, UPLOAD_ROOT / jpeg_rel, fmt.jpeg_size)
            assets.append(jpeg_rel)

        queue_path = QUEUE_ROOT / f"{post_id}.json"
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text(
            json.dumps(
                {
                    "post_id": post_id,
                    "series": "weekly",
                    "caption": build_caption(
                        data.saturday,
                        data.results,
                        data.ranked,
                        data.teams,
                        data.level_label,
                    ),
                    "assets": assets,
                    "scheduled_for": None,
                    "location_id": None,
                    "notes": f"Weekly report, weekend of {_saturday_label(saturday)} ({fmt.key}, {data.level_label}) "
                    "· post by hand from the app",
                    "channel": "manual",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        queue_files.append(queue_path)
    return queue_files


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render the stats page weekly report as an Instagram/TikTok carousel"
    )
    parser.add_argument(
        "--week",
        type=date.fromisoformat,
        default=None,
        help="Any date in the week (snapped to its Saturday); default: most recent Saturday",
    )
    parser.add_argument(
        "--levels",
        choices=list(LEVEL_CHOICES),
        default="default",
        help="default: pyramid + merit leagues; pyramid: no merit; all: incl. other",
    )
    parser.add_argument(
        "--format",
        choices=[*FORMATS, "both"],
        default="both",
        help="instagram (4:5 API), tiktok (9:16) or both (default)",
    )
    parser.add_argument("--mode", choices=["light", "dark"], default="light")
    parser.add_argument("--top", type=int, default=10, help="Rows per category slide")
    parser.add_argument(
        "--publish-dir",
        type=Path,
        default=None,
        help="Ephemeral dist output for the Instagram API pipeline (JPEGs + post.json only)",
    )
    args = parser.parse_args()

    setup_logging()
    saturday = saturday_of_week(args.week) if args.week else most_recent_saturday(date.today())
    if args.publish_dir is not None:
        post_path = publish_weekly_instagram(
            saturday,
            args.publish_dir,
            levels=args.levels,
            mode=args.mode,
            top=args.top,
        )
        post = json.loads(post_path.read_text(encoding="utf-8"))
        print(f"Published {post_path} ({len(post['assets'])} slides)")
        return

    formats = list(FORMATS) if args.format == "both" else [args.format]
    for queue_path in generate_weekly_carousel(
        saturday, levels=args.levels, formats=formats, mode=args.mode, top=args.top
    ):
        post = json.loads(queue_path.read_text(encoding="utf-8"))
        print(f"Queued {queue_path} ({len(post['assets'])} slides)")


if __name__ == "__main__":
    main()
