"""Generate Instagram-ready leaderboard graphics (3:4 portrait).

Generic top-N list template following the site's light/dark card style
(``dist/styles.css``, both ``:root`` and its ``prefers-color-scheme: dark``
block): Oswald headings, Barlow body, blue accent, cards on a soft
background. Each row reads::

    N. [team crest] Team name                                    VALUE
                     detail line (e.g. level range · date range)

A small footer watermark records how current the underlying data is (latest
``fixture_data/*/last_updated.txt`` scrape timestamp by default).

Ships with a ready-made example built on :mod:`rugby.analysis.winning_streaks`
(``--dataset ever`` / ``--dataset active``), but :func:`render_leaderboard_svg`
takes plain :class:`LeaderboardEntry` rows so it can be reused for any top-N list
(travel distances, tier streaks, etc).

Usage::

    python -m rugby.instagram_leaderboard
    python -m rugby.instagram_leaderboard --dataset active --top 10
    python -m rugby.instagram_leaderboard --dataset ever --top 15 --png
    python -m rugby.instagram_leaderboard --mode dark
    python -m rugby.instagram_leaderboard --data-as-of "24 Aug 2026"
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from html import escape
from pathlib import Path

from core.config import REPO_ROOT, setup_logging
from rugby import DATA_DIR
from rugby.instagram_maps import _site_logo_href, build_crest_href_map, rasterise_svg_to_png
from rugby.maps import RFU_FALLBACK_ICON
from rugby.pyramid_image import _valid_image_url
from rugby.seo import BASE_URL

OUTPUT_ROOT = REPO_ROOT / "output" / "instagram" / "leaderboards"

# 3:4 portrait — Instagram feed friendly (matches rugby.instagram_maps).
IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1440

FONT_HEADING = "Oswald, system-ui, -apple-system, Segoe UI, sans-serif"
FONT_BODY = "Barlow, system-ui, -apple-system, Segoe UI, sans-serif"


@dataclass(frozen=True, slots=True)
class Palette:
    bg: str
    card_bg: str
    border: str
    text_heading: str
    text_muted: str
    accent: str
    shadow: str


# Site style guide (dist/styles.css :root), light and dark (prefers-color-scheme) modes.
LIGHT_PALETTE = Palette(
    bg="#f9f9f9",
    card_bg="#ffffff",
    border="#e0e0e0",
    text_heading="#2c3e50",
    text_muted="#666666",
    accent="#0066cc",
    shadow="rgba(0, 0, 0, 0.08)",
)
DARK_PALETTE = Palette(
    bg="#1a1a2e",
    card_bg="#16213e",
    border="#2a2a4a",
    text_heading="#e0e8f0",
    text_muted="#a0a0a0",
    accent="#4da6ff",
    shadow="rgba(0, 0, 0, 0.3)",
)
PALETTES: dict[str, Palette] = {"light": LIGHT_PALETTE, "dark": DARK_PALETTE}

MARGIN_X = 64
TITLE_TOP_Y = 90
TITLE_FONT_SIZE = 60
TITLE_LINE_HEIGHT = 64
TITLE_SUBTITLE_GAP = 46
SUBTITLE_LIST_GAP = 64
FOOTER_HEIGHT = 90

RANK_COL_WIDTH = 76
LOGO_DIAMETER = 68
LOGO_TEXT_GAP = 24
VALUE_COL_WIDTH = 140
ROW_TEXT_GAP = 6

SITE_LOGO_SIZE = 30
SITE_URL_FONT_SIZE = 30
SITE_URL_GAP = 10
SITE_HOST = BASE_URL.removeprefix("https://").removeprefix("http://")


@dataclass(frozen=True, slots=True)
class LeaderboardEntry:
    team_name: str
    detail: str
    value: str
    logo_url: str | None = None


def _usable_crest_url(url: str | None) -> bool:
    return bool(url) and url != RFU_FALLBACK_ICON and _valid_image_url(url)


def _font_import_style_svg() -> str:
    return (
        "<style>@import url('https://fonts.googleapis.com/css2?"
        "family=Oswald:wght@500;600;700&amp;family=Barlow:wght@400;500;600&amp;display=swap');"
        "</style>"
    )


# Rough average glyph width for Oswald 700 uppercase, as a fraction of font-size —
# good enough to wrap/shrink a title without measuring real text metrics.
_TITLE_CHAR_WIDTH_FACTOR = 0.56


def _wrap_title(
    text: str, *, max_width: float, font_size: float, max_lines: int = 2
) -> tuple[list[str], float]:
    """Word-wrap *text* to fit *max_width*, shrinking ``font_size`` if needed.

    Returns the wrapped lines (already upper-cased) and the font-size used.
    Wraps at 88% of the available width first so a two-word title breaks into
    a balanced pair of lines rather than an almost-full first line and a
    near-empty second one.
    """
    words = text.upper().split()
    size = font_size

    for _ in range(12):
        target_width = max_width * 0.88

        def line_width(s: str, size: float = size) -> float:
            return len(s) * size * _TITLE_CHAR_WIDTH_FACTOR

        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if not current or line_width(candidate) <= target_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)

        fits = len(lines) <= max_lines and all(line_width(ln) <= max_width for ln in lines)
        if fits:
            return lines, size
        size *= 0.9

    return lines, size


def _row_svg(
    entry: LeaderboardEntry,
    rank: int,
    *,
    x: float,
    y: float,
    width: float,
    row_height: float,
    crest_hrefs: dict[str, str],
    palette: Palette,
) -> str:
    cy = y + row_height / 2
    rank_x = x + RANK_COL_WIDTH / 2
    logo_cx = x + RANK_COL_WIDTH + LOGO_DIAMETER / 2
    logo_r = LOGO_DIAMETER / 2
    text_x = logo_cx + logo_r + LOGO_TEXT_GAP
    value_x = x + width

    rank_svg = (
        f'<text x="{rank_x:.2f}" y="{cy:.2f}" font-family="{FONT_HEADING}" font-size="34" '
        f'font-weight="600" fill="{palette.accent}" text-anchor="middle" '
        f'dominant-baseline="central">{rank}</text>'
    )

    icon_url = entry.logo_url or ""
    inline_href = crest_hrefs.get(icon_url) if _usable_crest_url(icon_url) else None
    if inline_href:
        badge_inner = (
            f'<image x="{logo_cx - logo_r:.2f}" y="{cy - logo_r:.2f}" '
            f'width="{LOGO_DIAMETER:.2f}" height="{LOGO_DIAMETER:.2f}" '
            f'href="{escape(inline_href, quote=True)}" preserveAspectRatio="xMidYMid meet" '
            f'clip-path="url(#leaderboardCrestClip)"/>'
        )
    else:
        initial = (entry.team_name.strip() or "?")[0].upper()
        badge_inner = (
            f'<text x="{logo_cx:.2f}" y="{cy:.2f}" font-family="{FONT_HEADING}" font-size="28" '
            f'font-weight="600" fill="{palette.text_muted}" text-anchor="middle" '
            f'dominant-baseline="central">{escape(initial)}</text>'
        )
    logo_svg = (
        f'<circle cx="{logo_cx:.2f}" cy="{cy:.2f}" r="{logo_r:.2f}" fill="{palette.card_bg}" '
        f'stroke="{palette.border}" stroke-width="1.5"/>'
        f"{badge_inner}"
    )

    name_y = cy - ROW_TEXT_GAP
    detail_y = cy + 24
    text_svg = (
        f'<text x="{text_x:.2f}" y="{name_y:.2f}" font-family="{FONT_HEADING}" font-size="32" '
        f'font-weight="600" fill="{palette.text_heading}" dominant-baseline="alphabetic">'
        f"{escape(entry.team_name)}</text>"
        f'<text x="{text_x:.2f}" y="{detail_y:.2f}" font-family="{FONT_BODY}" font-size="22" '
        f'font-weight="500" fill="{palette.text_muted}" dominant-baseline="alphabetic">'
        f"{escape(entry.detail)}</text>"
    )

    value_svg = (
        f'<text x="{value_x:.2f}" y="{cy:.2f}" font-family="{FONT_HEADING}" font-size="40" '
        f'font-weight="700" fill="{palette.accent}" text-anchor="end" '
        f'dominant-baseline="central">{escape(entry.value)}</text>'
    )

    divider_svg = (
        ""
        if rank == 0
        else f'<line x1="{x:.2f}" y1="{y:.2f}" x2="{x + width:.2f}" y2="{y:.2f}" '
        f'stroke="{palette.border}" stroke-width="1"/>'
    )

    return f"<g>{divider_svg}{rank_svg}{logo_svg}{text_svg}{value_svg}</g>"


def render_leaderboard_svg(
    title: str,
    entries: list[LeaderboardEntry],
    *,
    subtitle: str | None = None,
    crest_hrefs: dict[str, str] | None = None,
    width: int = IMAGE_WIDTH,
    height: int = IMAGE_HEIGHT,
    mode: str = "light",
    data_as_of: str | None = None,
) -> str:
    """Build the full SVG document for a top-N leaderboard graphic.

    *data_as_of* is a short display string (e.g. ``"24 Aug 2026"``) stamped in
    the footer to record how current the underlying data is; pass ``None`` to
    omit it.
    """
    palette = PALETTES[mode]
    crest_hrefs = crest_hrefs or {}
    available_title_width = width - 2 * MARGIN_X
    title_lines, title_font_size = _wrap_title(
        title, max_width=available_title_width, font_size=TITLE_FONT_SIZE
    )
    title_line_height = TITLE_LINE_HEIGHT * (title_font_size / TITLE_FONT_SIZE)

    title_svg_lines: list[str] = []
    for i, line in enumerate(title_lines):
        line_y = TITLE_TOP_Y + i * title_line_height
        title_svg_lines.append(
            f'<text x="{MARGIN_X}" y="{line_y:.2f}" font-family="{FONT_HEADING}" '
            f'font-size="{title_font_size:.2f}" font-weight="700" fill="{palette.text_heading}" '
            f'text-anchor="start">{escape(line)}</text>'
        )
    title_bottom_y = TITLE_TOP_Y + (len(title_lines) - 1) * title_line_height

    subtitle_y = title_bottom_y + TITLE_SUBTITLE_GAP
    list_top = subtitle_y + SUBTITLE_LIST_GAP if subtitle else title_bottom_y + SUBTITLE_LIST_GAP

    list_bottom = height - FOOTER_HEIGHT - 40
    list_area_height = list_bottom - list_top
    row_height = list_area_height / max(1, len(entries))

    list_x = MARGIN_X
    list_width = width - 2 * MARGIN_X

    card_svg = (
        f'<rect x="{list_x:.2f}" y="{list_top:.2f}" width="{list_width:.2f}" '
        f'height="{list_area_height:.2f}" rx="16" fill="{palette.card_bg}" '
        f'stroke="{palette.border}" stroke-width="1" filter="url(#cardShadow)"/>'
    )

    rows_svg: list[str] = []
    for i, entry in enumerate(entries):
        rows_svg.append(
            _row_svg(
                entry,
                i + 1,
                x=list_x + 24,
                y=list_top + i * row_height,
                width=list_width - 48,
                row_height=row_height,
                crest_hrefs=crest_hrefs,
                palette=palette,
            )
        )

    subtitle_svg = ""
    if subtitle:
        subtitle_svg = (
            f'<text x="{MARGIN_X}" y="{subtitle_y:.2f}" font-family="{FONT_BODY}" font-size="30" '
            f'font-weight="500" fill="{palette.text_muted}" text-anchor="start">{escape(subtitle)}</text>'
        )

    site_logo_y = height - FOOTER_HEIGHT / 2 - SITE_LOGO_SIZE / 2
    site_text_x = MARGIN_X + SITE_LOGO_SIZE + SITE_URL_GAP
    site_line_y = height - FOOTER_HEIGHT / 2
    logo_href = escape(_site_logo_href(), quote=True)

    watermark_svg = ""
    if data_as_of:
        watermark_svg = (
            f'<text x="{width - MARGIN_X}" y="{site_line_y:.2f}" font-family="{FONT_BODY}" '
            f'font-size="22" font-weight="500" fill="{palette.text_muted}" text-anchor="end" '
            f'dominant-baseline="central">Data from englandrugby.com as of {escape(data_as_of)}</text>'
        )

    footer_svg = (
        f'<image x="{MARGIN_X}" y="{site_logo_y:.2f}" width="{SITE_LOGO_SIZE}" '
        f'height="{SITE_LOGO_SIZE}" href="{logo_href}"/>'
        f'<text x="{site_text_x}" y="{site_line_y:.2f}" font-family="{FONT_BODY}" '
        f'font-size="{SITE_URL_FONT_SIZE}" font-weight="500" fill="{palette.text_muted}" '
        f'dominant-baseline="central">{escape(SITE_HOST)}</text>'
        f"{watermark_svg}"
    )

    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        f"{_font_import_style_svg()}\n"
        "<defs>"
        '<clipPath id="leaderboardCrestClip" clipPathUnits="objectBoundingBox">'
        '<circle cx="0.5" cy="0.5" r="0.5"/></clipPath>'
        '<filter id="cardShadow" x="-20%" y="-20%" width="140%" height="140%">'
        f'<feDropShadow dx="0" dy="2" stdDeviation="6" flood-color="{palette.shadow}"/>'
        "</filter>"
        "</defs>\n"
        f'<rect width="{width}" height="{height}" fill="{palette.bg}"/>\n'
        f"{''.join(title_svg_lines)}\n"
        f"{subtitle_svg}\n"
        f"{card_svg}\n"
        f"{''.join(rows_svg)}\n"
        f"{footer_svg}\n"
        "</svg>\n"
    )


def _latest_data_timestamp() -> str | None:
    """Most recent ``last_updated.txt`` across ``fixture_data/``, as ``"24 Aug 2026"``."""
    from datetime import datetime

    fixture_root = DATA_DIR / "fixture_data"
    if not fixture_root.exists():
        return None

    latest: datetime | None = None
    for ts_path in fixture_root.glob("*/last_updated.txt"):
        try:
            ts = datetime.fromisoformat(ts_path.read_text(encoding="utf-8").strip())
        except ValueError:
            continue
        if latest is None or ts > latest:
            latest = ts

    return latest.strftime("%d %b %Y") if latest else None


def write_leaderboard(
    title: str,
    entries: list[LeaderboardEntry],
    output_path: Path,
    *,
    subtitle: str | None = None,
    mode: str = "light",
    data_as_of: str | None = None,
    write_png: bool = False,
    png_scale: float = 3.0,
) -> list[Path]:
    """Render *entries* to *output_path* (``.svg``), embedding crests inline."""
    crest_hrefs = build_crest_href_map(
        [e.logo_url or "" for e in entries],
        px=max(64, LOGO_DIAMETER * int(png_scale) if write_png else LOGO_DIAMETER),
    )

    svg_text = render_leaderboard_svg(
        title,
        entries,
        subtitle=subtitle,
        crest_hrefs=crest_hrefs,
        mode=mode,
        data_as_of=data_as_of,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg_text, encoding="utf-8")
    written = [output_path]

    if write_png:
        png_path = output_path.with_suffix(".png")
        rasterise_svg_to_png(output_path, png_path, scale=png_scale)
        written.append(png_path)

    return written


def _format_date_range(start_date: str, end_date: str, *, still_going: bool) -> str:
    """``"Sep 2021 - Apr 2024"``, or ``"Since Sep 2021"`` for a streak with no end yet."""
    from datetime import datetime

    start_label = datetime.strptime(start_date, "%Y-%m-%d").strftime("%b %Y")
    if still_going:
        return f"Since {start_label}"

    end_label = datetime.strptime(end_date, "%Y-%m-%d").strftime("%b %Y")
    if start_label == end_label:
        return start_label
    return f"{start_label} - {end_label}"


def _win_streak_entries(*, dataset: str, top: int) -> tuple[str, str, list[LeaderboardEntry]]:
    from rugby.analysis.winning_streaks import collect_all_win_streaks, format_level_range

    title = "Longest Rugby Union Winning Streaks"
    streaks = collect_all_win_streaks(min_length=1)
    if dataset == "active":
        subtitle = "Ordinary league matches, ongoing"
        # Drop streaks for teams no longer in league_data — their fixture data
        # just stopped rather than the streak actually continuing.
        ranked = sorted(
            (s for s in streaks if s.still_going and s.has_recent_league),
            key=lambda s: -s.length,
        )
    else:
        subtitle = "Ordinary league matches, all time"
        ranked = sorted(streaks, key=lambda s: -s.length)

    entries = []
    for s in ranked[:top]:
        level = format_level_range(s.level_spans)
        date_range = _format_date_range(s.start_date, s.end_date, still_going=s.still_going)
        detail = f"{level} · {date_range}" if level else date_range
        entries.append(
            LeaderboardEntry(
                team_name=s.display_name,
                detail=detail,
                value=f"{s.length}",
                logo_url=s.logo_url,
            )
        )
    return title, subtitle, entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an Instagram leaderboard graphic (3:4 portrait)"
    )
    parser.add_argument(
        "--dataset",
        choices=["active", "ever"],
        default="active",
        help="Built-in example dataset from rugby.analysis.winning_streaks (default: active)",
    )
    parser.add_argument("--top", type=int, default=10, help="Number of rows to show (default: 10)")
    parser.add_argument(
        "--output", type=Path, default=None, help="Output SVG path (single-mode runs only)"
    )
    parser.add_argument(
        "--mode",
        choices=["light", "dark", "both"],
        default="both",
        help="Colour scheme to render (default: both)",
    )
    parser.add_argument(
        "--data-as-of",
        default=None,
        help='Watermark date override (e.g. "24 Aug 2026"); default: latest fixture_data scrape timestamp',
    )
    parser.add_argument(
        "--no-data-as-of",
        dest="show_data_as_of",
        action="store_false",
        help="Omit the data-as-of watermark entirely",
    )
    parser.add_argument("--png", action="store_true", help="Also rasterise to PNG via Playwright")
    parser.add_argument(
        "--png-scale", type=float, default=3.0, help="PNG device scale factor (default: 3.0)"
    )
    args = parser.parse_args()

    setup_logging()
    title, subtitle, entries = _win_streak_entries(dataset=args.dataset, top=args.top)

    data_as_of = None
    if args.show_data_as_of:
        data_as_of = args.data_as_of or _latest_data_timestamp()

    modes = ["light", "dark"] if args.mode == "both" else [args.mode]
    for mode in modes:
        if args.output is not None:
            output_path = args.output
        else:
            suffix = f"_{mode}" if args.mode == "both" else ""
            output_path = OUTPUT_ROOT / f"win_streaks_{args.dataset}{suffix}.svg"

        written = write_leaderboard(
            title,
            entries,
            output_path,
            subtitle=subtitle,
            mode=mode,
            data_as_of=data_as_of,
            write_png=args.png,
            png_scale=args.png_scale,
        )
        for path in written:
            print(f"Wrote {path}")


if __name__ == "__main__":
    main()
