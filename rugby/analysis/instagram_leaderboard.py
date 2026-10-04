"""Generate Instagram-ready leaderboard graphics.

Default canvas is 3:4 (1080×1440) for manual in-app posts and legacy map
graphics. The weekly report passes 4:5 (1080×1350) for Graph API publishing;
content margins keep the card inside the profile grid's center 3:4 preview crop.

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

    python -m rugby.analysis.instagram_leaderboard
    python -m rugby.analysis.instagram_leaderboard --dataset active --top 10
    python -m rugby.analysis.instagram_leaderboard --dataset ever --top 15 --png
    python -m rugby.analysis.instagram_leaderboard --mode dark
    python -m rugby.analysis.instagram_leaderboard --data-as-of "24 Aug 2026"
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
from rugby.text_fit import fit_line, fit_pair, text_width

OUTPUT_ROOT = REPO_ROOT / "output" / "instagram" / "leaderboards"

# Default 3:4 — matches rugby.instagram_maps and manual in-app uploads.
IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1440

# Graph API minimum portrait is 4:5 (Meta IG User Media image specs). The profile
# grid previews a center 3:4 slice (~1012×1350), trimming ~34 px from each side
# of a 1080×1350 upload. MARGIN_X (64) keeps the card inside that safe width.
API_IMAGE_WIDTH = 1080
API_IMAGE_HEIGHT = 1350
GRID_SAFE_WIDTH = (API_IMAGE_HEIGHT * 3) // 4  # 1012 — 3:4 at full canvas height
GRID_SIDE_TRIM = (API_IMAGE_WIDTH - GRID_SAFE_WIDTH + 1) // 2  # 34 px per side

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
PRETITLE_TOP_Y = 58
PRETITLE_FONT_SIZE = 26
PRETITLE_TITLE_GAP = 38
TITLE_TOP_Y = 90
TITLE_FONT_SIZE = 60
TITLE_LINE_HEIGHT = 64
TITLE_SUBTITLE_GAP = 46
# Optional oversized accent line under the title (e.g. the cover's date), sized
# to stay legible in the profile grid thumbnail (~1/8 scale on a phone).
HEADLINE_FONT_SIZE = 200
HEADLINE_GAP = 215
HEADLINE_SUBTITLE_GAP = 62
SUBTITLE_LIST_GAP = 64
# Tighter cover header: title → date → week line, without the 215 px dead zone.
COVER_TITLE_TOP_Y = 88
COVER_HEADLINE_GAP = 20
COVER_HEADLINE_FONT_SIZE = 168
COVER_HEADLINE_SUBTITLE_GAP = 34
COVER_SUBTITLE_LIST_GAP = 52
FOOTER_HEIGHT = 90
# Approximate Oswald vertical metrics (fraction of font-size) for stacking text blocks.
_TEXT_CAP = 0.75
_TEXT_DESCENT = 0.25

RANK_COL_WIDTH = 76
# Margin slides label the row by level or merit competition instead of a rank number.
RANK_LABEL_COL_WIDTH = 128
RANK_LABEL_FONT_SIZE = 20
LOGO_DIAMETER = 68
LOGO_TEXT_GAP = 24
VALUE_COL_WIDTH = 140
ROW_TEXT_GAP = 6
NAME_FONT_SIZE = 32
DETAIL_FONT_SIZE = 22
# Cover rows show the category above the team name (accent, heading weight).
COVER_CATEGORY_FONT_SIZE = 28
VALUE_FONT_SIZE = 40
# Minimum gap between the end of a name/detail line and the value column.
VALUE_TEXT_GAP = 28
# Horizontal offset of the second crest in a two-team row (overlaps the first).
PAIR_LOGO_OFFSET = 44

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
    # Cover slide: category title rendered above the team name in accent colour.
    category_label: str | None = None
    # When set, shown in the rank column instead of the row index (e.g. pyramid tier).
    rank_label: str | None = None
    # Set for rows that belong to two teams (e.g. a match aggregate): a second,
    # overlapping crest (or its initial when there's no usable crest) is drawn.
    second_name: str | None = None
    second_logo_url: str | None = None


def _usable_crest_url(url: str | None) -> bool:
    return bool(url) and url != RFU_FALLBACK_ICON and _valid_image_url(url)


def _font_import_style_svg() -> str:
    return (
        "<style>@import url('https://fonts.googleapis.com/css2?"
        "family=Oswald:wght@500;600;700&amp;family=Barlow:wght@400;500;600&amp;display=swap');"
        "</style>"
    )


def _text_block_bottom(baseline_y: float, font_size: float) -> float:
    """Lower edge of a single text line given its baseline and font size."""
    return baseline_y + font_size * _TEXT_DESCENT


def _baseline_for_block_top(top_y: float, font_size: float) -> float:
    """Baseline for a line whose cap height should start at ``top_y``."""
    return top_y + font_size * _TEXT_CAP


def _wrap_title(
    text: str, *, max_width: float, font_size: float, max_lines: int = 2
) -> tuple[list[str], float]:
    """Fit *text* (upper-cased) in at most two lines, shrinking ``font_size`` if needed.

    Uses one line when it fits; otherwise picks the two-line split whose longer
    line is shortest, so titles break into a balanced pair rather than leaving a
    lone word ("MOST TOTAL POINTS IN A / MATCH"). Widths are real Oswald 700
    metrics (see rugby.text_fit).
    """
    words = text.upper().split()
    size = font_size
    lines = [" ".join(words)]

    for _ in range(12):

        def line_width(s: str, size: float = size) -> float:
            return text_width(s, kind="heading", weight=700, size=size)

        lines = [" ".join(words)]
        if line_width(lines[0]) <= max_width:
            return lines, size
        if max_lines >= 2 and len(words) > 1:
            splits = [[" ".join(words[:i]), " ".join(words[i:])] for i in range(1, len(words))]
            lines = min(splits, key=lambda pair: max(line_width(ln) for ln in pair))
            if all(line_width(ln) <= max_width for ln in lines):
                return lines, size
        size *= 0.9

    return lines, size


def _badge_svg(
    logo_url: str | None,
    name: str,
    *,
    cx: float,
    cy: float,
    crest_hrefs: dict[str, str],
    palette: Palette,
) -> str:
    """Circular crest badge, falling back to the team's initial."""
    r = LOGO_DIAMETER / 2
    icon_url = logo_url or ""
    inline_href = crest_hrefs.get(icon_url) if _usable_crest_url(icon_url) else None
    if inline_href:
        badge_inner = (
            f'<image x="{cx - r:.2f}" y="{cy - r:.2f}" '
            f'width="{LOGO_DIAMETER:.2f}" height="{LOGO_DIAMETER:.2f}" '
            f'href="{escape(inline_href, quote=True)}" preserveAspectRatio="xMidYMid meet" '
            f'clip-path="url(#leaderboardCrestClip)"/>'
        )
    else:
        initial = (name.strip() or "?")[0].upper()
        badge_inner = (
            f'<text x="{cx:.2f}" y="{cy:.2f}" font-family="{FONT_HEADING}" font-size="28" '
            f'font-weight="600" fill="{palette.text_muted}" text-anchor="middle" '
            f'dominant-baseline="central">{escape(initial)}</text>'
        )
    return (
        f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{palette.card_bg}" '
        f'stroke="{palette.border}" stroke-width="1.5"/>'
        f"{badge_inner}"
    )


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
    show_rank: bool = True,
    logo_col_extra: float = 0.0,
    rank_col_width: int = RANK_COL_WIDTH,
) -> str:
    """One row; ``logo_col_extra`` widens the crest column (for two-team rows) so
    every row on a slide keeps its text aligned."""
    cy = y + row_height / 2
    rank_x = x + rank_col_width / 2
    logo_cx = x + rank_col_width + LOGO_DIAMETER / 2
    logo_r = LOGO_DIAMETER / 2
    text_x = logo_cx + logo_r + logo_col_extra + LOGO_TEXT_GAP
    value_x = x + width

    rank_svg = ""
    if show_rank:
        if entry.rank_label is not None:
            label, label_size = fit_line(
                entry.rank_label,
                kind="heading",
                weight=600,
                size=RANK_LABEL_FONT_SIZE,
                max_width=rank_col_width - 12,
            )
            rank_svg = (
                f'<text x="{x + 6:.2f}" y="{cy:.2f}" font-family="{FONT_HEADING}" '
                f'font-size="{label_size:.2f}" font-weight="600" fill="{palette.text_heading}" '
                f'text-anchor="start" dominant-baseline="central">{escape(label)}</text>'
            )
        else:
            rank_text = str(rank)
            rank_size = 28 if len(rank_text) >= 3 else 34
            rank_svg = (
                f'<text x="{rank_x:.2f}" y="{cy:.2f}" font-family="{FONT_HEADING}" '
                f'font-size="{rank_size}" font-weight="600" fill="{palette.accent}" '
                f'text-anchor="middle" dominant-baseline="central">{escape(rank_text)}</text>'
            )

    logo_svg = _badge_svg(
        entry.logo_url, entry.team_name, cx=logo_cx, cy=cy, crest_hrefs=crest_hrefs, palette=palette
    )
    if entry.second_name is not None:
        logo_svg += _badge_svg(
            entry.second_logo_url,
            entry.second_name,
            cx=logo_cx + PAIR_LOGO_OFFSET,
            cy=cy,
            crest_hrefs=crest_hrefs,
            palette=palette,
        )

    # Both text lines sit level with the value, so each must stop short of it:
    # shrink a little if needed, then truncate (see rugby.text_fit).
    value_width = text_width(entry.value, kind="heading", weight=700, size=VALUE_FONT_SIZE)
    text_max = value_x - value_width - VALUE_TEXT_GAP - text_x
    pair_suffix = f" v {entry.second_name}" if entry.second_name else None
    if pair_suffix and entry.team_name.endswith(pair_suffix):
        name, name_size = fit_pair(
            entry.team_name.removesuffix(pair_suffix),
            entry.second_name or "",
            kind="heading",
            weight=600,
            size=NAME_FONT_SIZE,
            max_width=text_max,
        )
    else:
        name, name_size = fit_line(
            entry.team_name, kind="heading", weight=600, size=NAME_FONT_SIZE, max_width=text_max
        )
    detail, detail_size = fit_line(
        entry.detail, kind="body", weight=500, size=DETAIL_FONT_SIZE, max_width=text_max
    )

    category_svg = ""
    if entry.category_label:
        category, category_size = fit_line(
            entry.category_label.upper(),
            kind="heading",
            weight=600,
            size=COVER_CATEGORY_FONT_SIZE,
            max_width=text_max,
        )
        category_y = cy - 24
        name_y = cy + 6
        detail_y = cy + 34
        category_svg = (
            f'<text x="{text_x:.2f}" y="{category_y:.2f}" font-family="{FONT_HEADING}" '
            f'font-size="{category_size:.2f}" font-weight="600" fill="{palette.accent}" '
            f'dominant-baseline="alphabetic">{escape(category)}</text>'
        )
    else:
        name_y = cy - ROW_TEXT_GAP
        detail_y = cy + 24

    text_svg = (
        f"{category_svg}"
        f'<text x="{text_x:.2f}" y="{name_y:.2f}" font-family="{FONT_HEADING}" '
        f'font-size="{name_size:.2f}" font-weight="600" fill="{palette.text_heading}" '
        f'dominant-baseline="alphabetic">{escape(name)}</text>'
        f'<text x="{text_x:.2f}" y="{detail_y:.2f}" font-family="{FONT_BODY}" '
        f'font-size="{detail_size:.2f}" font-weight="500" fill="{palette.text_muted}" '
        f'dominant-baseline="alphabetic">{escape(detail)}</text>'
    )

    value_svg = (
        f'<text x="{value_x:.2f}" y="{cy:.2f}" font-family="{FONT_HEADING}" font-size="{VALUE_FONT_SIZE}" '
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
    show_rank: bool = True,
    headline: str | None = None,
    pretitle: str | None = None,
    cover_layout: bool = False,
    rank_col_width: int = RANK_COL_WIDTH,
) -> str:
    """Build the full SVG document for a top-N leaderboard graphic.

    *data_as_of* is a short display string (e.g. ``"24 Aug 2026"``) stamped in
    the footer to record how current the underlying data is; pass ``None`` to
    omit it. *headline* adds an oversized accent line between the title and
    subtitle. *pretitle* adds a muted line above the title (e.g. the week date).
    *cover_layout* tightens the weekly cover header (title, date, week line).
    """
    palette = PALETTES[mode]
    crest_hrefs = crest_hrefs or {}
    available_title_width = width - 2 * MARGIN_X

    pretitle_svg = ""
    title_top_y = COVER_TITLE_TOP_Y if cover_layout else TITLE_TOP_Y
    if pretitle and not cover_layout:
        pretitle_text, pretitle_size = fit_line(
            pretitle,
            kind="body",
            weight=500,
            size=PRETITLE_FONT_SIZE,
            max_width=available_title_width,
        )
        pretitle_svg = (
            f'<text x="{MARGIN_X}" y="{PRETITLE_TOP_Y:.2f}" font-family="{FONT_BODY}" '
            f'font-size="{pretitle_size:.2f}" font-weight="500" fill="{palette.text_muted}" '
            f'text-anchor="start">{escape(pretitle_text)}</text>'
        )
        title_top_y = PRETITLE_TOP_Y + PRETITLE_TITLE_GAP

    title_lines, title_font_size = _wrap_title(
        title, max_width=available_title_width, font_size=TITLE_FONT_SIZE
    )
    title_line_height = TITLE_LINE_HEIGHT * (title_font_size / TITLE_FONT_SIZE)

    title_svg_lines: list[str] = []
    for i, line in enumerate(title_lines):
        line_y = title_top_y + i * title_line_height
        title_svg_lines.append(
            f'<text x="{MARGIN_X}" y="{line_y:.2f}" font-family="{FONT_HEADING}" '
            f'font-size="{title_font_size:.2f}" font-weight="700" fill="{palette.text_heading}" '
            f'text-anchor="start">{escape(line)}</text>'
        )
    title_bottom_y = title_top_y + (len(title_lines) - 1) * title_line_height

    headline_svg = ""
    headline_size = 0.0
    if headline:
        headline_font = COVER_HEADLINE_FONT_SIZE if cover_layout else HEADLINE_FONT_SIZE
        headline_text, headline_size = fit_line(
            headline,
            kind="heading",
            weight=700,
            size=headline_font,
            max_width=available_title_width,
        )
        if cover_layout:
            title_block_bottom = _text_block_bottom(title_bottom_y, title_font_size)
            headline_y = _baseline_for_block_top(
                title_block_bottom + COVER_HEADLINE_GAP, headline_size
            )
        else:
            title_bottom_y += HEADLINE_GAP
            headline_y = title_bottom_y
        headline_svg = (
            f'<text x="{MARGIN_X}" y="{headline_y:.2f}" font-family="{FONT_HEADING}" '
            f'font-size="{headline_size:.2f}" font-weight="700" fill="{palette.accent}" '
            f'text-anchor="start">{escape(headline_text)}</text>'
        )
        title_bottom_y = headline_y

    if headline:
        if cover_layout:
            subtitle_y = _text_block_bottom(headline_y, headline_size) + COVER_HEADLINE_SUBTITLE_GAP
        else:
            subtitle_y = title_bottom_y + headline_size * 0.35 + HEADLINE_SUBTITLE_GAP
    else:
        subtitle_y = title_bottom_y + TITLE_SUBTITLE_GAP
    subtitle_list_gap = COVER_SUBTITLE_LIST_GAP if cover_layout else SUBTITLE_LIST_GAP
    list_top = subtitle_y + subtitle_list_gap if subtitle else title_bottom_y + subtitle_list_gap

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

    logo_col_extra = PAIR_LOGO_OFFSET if any(e.second_name is not None for e in entries) else 0
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
                show_rank=show_rank,
                logo_col_extra=logo_col_extra,
                rank_col_width=rank_col_width,
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
        f"{pretitle_svg}\n"
        f"{''.join(title_svg_lines)}\n"
        f"{headline_svg}\n"
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
    width: int = IMAGE_WIDTH,
    height: int = IMAGE_HEIGHT,
    show_rank: bool = True,
    headline: str | None = None,
    pretitle: str | None = None,
    cover_layout: bool = False,
    rank_col_width: int = RANK_COL_WIDTH,
) -> list[Path]:
    """Render *entries* to *output_path* (``.svg``), embedding crests inline."""
    crest_hrefs = build_crest_href_map(
        [url or "" for e in entries for url in (e.logo_url, e.second_logo_url)],
        px=max(64, LOGO_DIAMETER * int(png_scale) if write_png else LOGO_DIAMETER),
    )

    svg_text = render_leaderboard_svg(
        title,
        entries,
        subtitle=subtitle,
        crest_hrefs=crest_hrefs,
        width=width,
        height=height,
        mode=mode,
        data_as_of=data_as_of,
        show_rank=show_rank,
        headline=headline,
        pretitle=pretitle,
        cover_layout=cover_layout,
        rank_col_width=rank_col_width,
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
