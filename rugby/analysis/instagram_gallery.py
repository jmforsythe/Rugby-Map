"""Generate a static HTML carousel to skim Instagram map renders under ``output/instagram/maps/<season>/``.

After running ``python -m rugby.instagram_maps`` (with optional ``--png``), rebuild the viewer
and open the gallery HTML in a browser. Controls match :mod:`rugby.analysis.pyramid_gallery`:

- Arrow keys (← / →) or on-screen buttons change the slide.
- ``Home`` / ``End`` jump to first / last slide.
- ``-`` / ``+`` (or ``=``) zoom the image; ``0`` resets to 100%.
- Hold **Ctrl** and scroll the stage to zoom.
- Prefers ``.png`` over ``.svg`` when both exist.
- Each season folder also gets ``gallery.html`` (30% default zoom).

Run::

    python -m rugby.analysis.instagram_gallery
    python -m rugby.analysis.instagram_gallery --season 2026-2027
    python -m rugby.analysis.instagram_gallery --boundary-detail BGC
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from core.boundaries import VALID_DETAIL_LEVELS
from core.config import REPO_ROOT
from rugby.analysis.pyramid_gallery import _season_sort_key, build_html
from rugby.instagram_maps import OUTPUT_ROOT
from rugby.tiers import mens_current_tier_name

_SEASON_DIR_RE = re.compile(r"^[12]\d{3}-[12]\d{3}$")
# The name suffix is dropped for merit-only levels, whose only name is "Level N".
_LEVEL_STEM_RE = re.compile(r"^level_(\d+)(?:_(.+))?$")
_LEGACY_STEM_RE = re.compile(r"^(\d{2})_(.+)$")

DEFAULT_OUTPUT = OUTPUT_ROOT / "instagram-gallery.html"
SEASON_CAROUSEL_NAME = "gallery.html"
INSTAGRAM_DEFAULT_ZOOM = 0.3


def _validate_instagram_root(path: str) -> Path:
    resolved = Path(path).resolve()
    root = REPO_ROOT.resolve()
    if root not in resolved.parents and resolved != root:
        raise argparse.ArgumentTypeError(
            f"--instagram-root must be inside the repository ({root}); got {resolved}"
        )
    return resolved


def _pick_image_path(directory: Path, stem: str) -> Path | None:
    png = directory / f"{stem}.png"
    if png.is_file():
        return png
    svg = directory / f"{stem}.svg"
    if svg.is_file():
        return svg
    return None


def _format_level_label(tier_num: int, season: str) -> str:
    tier_name = mens_current_tier_name(tier_num, season)
    return f"Level {tier_num} · {tier_name} · {season}"


def _format_legacy_label(stem: str, season: str) -> str:
    m = _LEGACY_STEM_RE.match(stem)
    if not m:
        return f"{stem.replace('_', ' ')} · {season}"
    order, rest = m.group(1), m.group(2).replace("_", " ")
    return f"{order} · {rest} · {season}"


def _level_stems_in_dir(image_dir: Path) -> dict[int, str]:
    tier_stems: dict[int, str] = {}
    if not image_dir.is_dir():
        return tier_stems
    for path in image_dir.iterdir():
        if not path.is_file():
            continue
        m = _LEVEL_STEM_RE.match(path.stem)
        if m is None:
            continue
        tier_stems[int(m.group(1))] = path.stem
    return tier_stems


def collect_season_slides(
    season_dir: Path,
    season: str,
    *,
    boundary_detail: str | None = None,
) -> list[dict[str, str]]:
    """Slides for one season; ``href`` values are relative to *season_dir*."""
    if boundary_detail:
        image_dir = season_dir / "boundary-detail" / boundary_detail.upper()
    else:
        image_dir = season_dir

    tier_stems = _level_stems_in_dir(image_dir)

    slides: list[dict[str, str]] = []
    for tier_num in sorted(tier_stems):
        picked = _pick_image_path(image_dir, tier_stems[tier_num])
        if picked is None:
            continue
        slides.append(
            {
                "label": _format_level_label(tier_num, season),
                "href": picked.name,
            }
        )
    return slides


def write_season_carousel(
    season_dir: Path,
    season: str,
    *,
    boundary_detail: str | None = None,
    default_zoom: float = INSTAGRAM_DEFAULT_ZOOM,
) -> Path | None:
    """Write ``gallery.html`` inside a season folder (paths relative to that folder)."""
    slides = collect_season_slides(season_dir, season, boundary_detail=boundary_detail)
    if not slides:
        return None

    out = season_dir / SEASON_CAROUSEL_NAME
    out.write_text(
        build_html(
            slides,
            page_title=f"Instagram maps · {season}",
            image_alt="Instagram map",
            empty_message="No Instagram maps — run instagram_maps first.",
            footer_note=(
                "Open this file from the season folder (paths are relative). "
                "SVG slides use <code>&lt;object&gt;</code> so crest tiles render."
            ),
            default_zoom=default_zoom,
        ),
        encoding="utf-8",
    )
    return out


def collect_level_slides(
    instagram_root: Path,
    *,
    seasons: list[str] | None = None,
    boundary_detail: str | None = None,
) -> list[dict[str, str]]:
    """Collect per-tier ``level_*`` slides, newest season first, levels ascending."""
    if not instagram_root.is_dir():
        return []

    if seasons is None:
        seasons = sorted(
            (
                d.name
                for d in instagram_root.iterdir()
                if d.is_dir() and _SEASON_DIR_RE.match(d.name)
            ),
            key=_season_sort_key,
            reverse=True,
        )

    slides: list[dict[str, str]] = []
    for season in seasons:
        season_dir = instagram_root / season
        if not season_dir.is_dir():
            continue

        if boundary_detail:
            image_dir = season_dir / "boundary-detail" / boundary_detail.upper()
            rel_prefix = f"{season}/boundary-detail/{boundary_detail.upper()}/"
        else:
            image_dir = season_dir
            rel_prefix = f"{season}/"

        if not image_dir.is_dir():
            continue

        tier_stems = _level_stems_in_dir(image_dir)

        for tier_num in sorted(tier_stems):
            picked = _pick_image_path(image_dir, tier_stems[tier_num])
            if picked is None:
                continue
            slides.append(
                {
                    "label": _format_level_label(tier_num, season),
                    "href": rel_prefix + picked.name,
                }
            )
    return slides


def collect_legacy_slides(
    instagram_root: Path,
    *,
    seasons: list[str] | None = None,
) -> list[dict[str, str]]:
    """Collect legacy grouped carousel slides (``01_tiers1-4_1of3``, etc.)."""
    if not instagram_root.is_dir():
        return []

    if seasons is None:
        seasons = sorted(
            (
                d.name
                for d in instagram_root.iterdir()
                if d.is_dir() and _SEASON_DIR_RE.match(d.name)
            ),
            key=_season_sort_key,
            reverse=True,
        )

    slides: list[dict[str, str]] = []
    for season in seasons:
        season_dir = instagram_root / season
        if not season_dir.is_dir():
            continue

        stems: list[str] = []
        for path in season_dir.iterdir():
            if not path.is_file():
                continue
            if _LEVEL_STEM_RE.match(path.stem):
                continue
            if not _LEGACY_STEM_RE.match(path.stem):
                continue
            if path.stem not in stems:
                stems.append(path.stem)

        for stem in sorted(stems):
            picked = _pick_image_path(season_dir, stem)
            if picked is None:
                continue
            slides.append(
                {
                    "label": _format_legacy_label(stem, season),
                    "href": f"{season}/{picked.name}",
                }
            )
    return slides


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write an HTML carousel to flip through Instagram map renders."
    )
    parser.add_argument(
        "--instagram-root",
        type=_validate_instagram_root,
        default=OUTPUT_ROOT,
        help=f"Instagram output root (default: {OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"HTML output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--season",
        action="append",
        metavar="YYYY-YYYY",
        help="Only include these seasons (repeatable). Default: all seasons under the root.",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Use legacy grouped carousel filenames (01_tiers1-4_1of3, etc.) instead of level_*.",
    )
    parser.add_argument(
        "--boundary-detail",
        choices=list(VALID_DETAIL_LEVELS),
        default=None,
        metavar="LEVEL",
        help="View maps under boundary-detail/<LEVEL>/ instead of the season root.",
    )
    args = parser.parse_args()

    instagram_root: Path = args.instagram_root
    out: Path = args.output if args.output is not None else DEFAULT_OUTPUT

    seasons = args.season if args.season else None
    if args.legacy:
        slides = collect_legacy_slides(instagram_root, seasons=seasons)
        page_title = "Instagram legacy carousel gallery"
        empty_message = "No legacy Instagram carousel images found."
    else:
        slides = collect_level_slides(
            instagram_root,
            seasons=seasons,
            boundary_detail=args.boundary_detail,
        )
        page_title = "Instagram maps gallery"
        empty_message = "No Instagram maps — run instagram_maps first."

    if not slides:
        print(
            f"No Instagram images found under {instagram_root} "
            f"(expected level_*.png/svg per season).",
            file=sys.stderr,
        )
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        build_html(
            slides,
            page_title=page_title,
            image_alt="Instagram map",
            empty_message=empty_message,
            footer_note=(
                "Open from <code>output/instagram/maps/</code> (paths are relative). "
                "SVG slides use <code>&lt;object&gt;</code> so crest tiles render."
            ),
            default_zoom=INSTAGRAM_DEFAULT_ZOOM,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {len(slides)} slides -> {out}")

    if not args.legacy:
        season_dirs: list[str]
        if seasons is None:
            season_dirs = sorted(
                (
                    d.name
                    for d in instagram_root.iterdir()
                    if d.is_dir() and _SEASON_DIR_RE.match(d.name)
                ),
                key=_season_sort_key,
                reverse=True,
            )
        else:
            season_dirs = seasons

        for season in season_dirs:
            season_dir = instagram_root / season
            season_out = write_season_carousel(
                season_dir,
                season,
                boundary_detail=args.boundary_detail,
            )
            if season_out is not None:
                print(f"Wrote season carousel -> {season_out}")

    print("Open that file in a browser; arrow keys flip slides; use - / + / 0 or Zoom buttons.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
