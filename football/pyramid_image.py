"""
Generate the English football pyramid diagram (SVG + optional PNG).

Uses the same crest-grid renderer as :mod:`rugby.pyramid_image` (variable-depth
trapezoid taper with club crests per division). Football levels 1–10 are rendered
in merit-style taper mode (no Counties stem).

Output (with ``--production``):
  dist/football/<season>/pyramid.{svg,png,preview.png}
  dist/football/<season>/pyramid_Labels.{svg,png,preview.png}

Run:
  python -m football.pyramid_image --season 2025-2026 --production
  python -m football.pyramid_image --season 2025-2026 --production --png
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from core import setup_logging
from football import DATA_DIR
from football.map_common import dist_season_dir, football_pyramid_band_label, short_season
from football.pyramid_parents import resolve_parent_overrides
from rugby.pyramid_image import (
    PYRAMID_PREVIEW_MAX_WIDTH,
    LeagueData,
    StemParentOverrides,
    TeamLogo,
    _pyramid_labels_path,
    _write_pyramid_svg_and_png,
    render_pyramid_svg,
)

logger = logging.getLogger(__name__)

_FOOTBALL_MERIT_MODE_KEY = "Football"


def _valid_football_crest_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    return url.strip().startswith("https://")


def load_football_pyramid_leagues(season: str) -> list[LeagueData]:
    """Load pyramid division JSON into :class:`LeagueData` for the shared renderer."""
    pyramid_dir = DATA_DIR / "geocoded_teams" / season / "pyramid"
    if not pyramid_dir.is_dir():
        raise FileNotFoundError(f"No pyramid data at {pyramid_dir}")

    leagues: list[LeagueData] = []
    for filepath in sorted(pyramid_dir.glob("*.json")):
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        teams_raw = data.get("teams") or []
        level = next((int(t["level"]) for t in teams_raw if t.get("level")), 0)
        if level < 1:
            continue

        teams: list[TeamLogo] = []
        for team in teams_raw:
            name = team.get("name")
            if not name:
                continue
            raw_url = team.get("image_url")
            teams.append(
                TeamLogo(
                    name=str(name),
                    image_url=str(raw_url).strip() if _valid_football_crest_url(raw_url) else None,
                )
            )

        leagues.append(
            LeagueData(
                tier_num=level,
                tier_name=football_pyramid_band_label(level),
                league_name=data.get("league_name", filepath.stem.replace("_", " ")),
                teams=teams,
                team_count=data.get("team_count", len(teams)),
            )
        )

    return sorted(leagues, key=lambda lg: (lg.tier_num, lg.league_name))


def _build_args(*, png: bool, png_scale: float, png_image_timeout_ms: float) -> SimpleNamespace:
    return SimpleNamespace(
        png=png,
        png_scale=png_scale,
        png_image_timeout_ms=png_image_timeout_ms,
        no_png_preview=False,
        png_force_full=True,
        png_preview_max_width=PYRAMID_PREVIEW_MAX_WIDTH,
        output=None,
        png_output=None,
        labels_under_valid_crests=False,
        labels_under_layout_height_scale=None,
    )


def _write_labelled_sibling(
    *,
    season: str,
    leagues: list[LeagueData],
    base_svg_path: Path,
    base_png_path: Path,
    args: SimpleNamespace,
    parent_overrides: StemParentOverrides | None,
) -> int:
    labels_svg_path = _pyramid_labels_path(base_svg_path)
    labels_png_path = _pyramid_labels_path(base_png_path)
    logger.info("Rendering labelled pyramid (%s) …", labels_svg_path.name)
    svg_labels = render_pyramid_svg(
        season,
        leagues,
        merit_competition=_FOOTBALL_MERIT_MODE_KEY,
        merit_local_offset=0,
        gender="mens",
        parent_overrides=parent_overrides,
        labels_under_valid_crests=True,
        diagram_main_title="ENGLISH FOOTBALL PYRAMID",
        diagram_subtitle=f"Men's leagues, {short_season(season)}",
    )
    return _write_pyramid_svg_and_png(svg_labels, labels_svg_path, labels_png_path, args)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate English football pyramid diagram")
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument("--production", action="store_true")
    parser.add_argument(
        "--png", action="store_true", help="Also rasterise SVG to PNG via Playwright"
    )
    parser.add_argument("--png-scale", type=float, default=1.0)
    parser.add_argument(
        "--png-image-timeout-ms",
        type=float,
        default=120_000.0,
        help="Max time to wait for crest images before PNG screenshot",
    )
    parser.add_argument(
        "--interactive-stem-orphans",
        "--interactive",
        dest="interactive_stem_orphans",
        action="store_true",
        help="TTY: prompt for missing pyramid parent links (saves tier_mappings JSON)",
    )
    parser.add_argument(
        "--ignore-saved-stem-parent-overrides",
        action="store_true",
        help="Do not load existing football tier_mappings before linking",
    )
    args = parser.parse_args()

    setup_logging()
    leagues = load_football_pyramid_leagues(args.season)
    if not leagues:
        logger.error("No pyramid leagues found for %s", args.season)
        return 1

    parent_overrides = resolve_parent_overrides(
        args.season,
        leagues,
        interactive=args.interactive_stem_orphans,
        ignore_saved=args.ignore_saved_stem_parent_overrides,
    )
    parent_overrides_arg = parent_overrides if parent_overrides else None

    render_kwargs = {
        "merit_competition": _FOOTBALL_MERIT_MODE_KEY,
        "merit_local_offset": 0,
        "gender": "mens",
        "parent_overrides": parent_overrides_arg,
        "diagram_main_title": "ENGLISH FOOTBALL PYRAMID",
        "diagram_subtitle": f"Men's leagues, {short_season(args.season)}",
    }

    out_dir = dist_season_dir(args.season, production=args.production)
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_path = out_dir / "pyramid.svg"
    png_path = out_dir / "pyramid.png"

    logger.info(
        "Rendering football pyramid (%d divisions, %d teams) …",
        len(leagues),
        sum(lg.team_count for lg in leagues),
    )
    svg = render_pyramid_svg(args.season, leagues, **render_kwargs)

    write_args = _build_args(
        png=args.png,
        png_scale=args.png_scale,
        png_image_timeout_ms=args.png_image_timeout_ms,
    )
    rc = _write_pyramid_svg_and_png(svg, svg_path, png_path, write_args)
    if rc != 0:
        return rc

    rc = _write_labelled_sibling(
        season=args.season,
        leagues=leagues,
        base_svg_path=svg_path,
        base_png_path=png_path,
        args=write_args,
        parent_overrides=parent_overrides_arg,
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
