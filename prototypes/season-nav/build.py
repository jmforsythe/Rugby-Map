#!/usr/bin/env python3
"""Build minimal static pages demonstrating a season-navigation header variant."""

from __future__ import annotations

import argparse
from contextlib import suppress
from html import escape
from pathlib import Path

from headers import HEADER_JS, HEADER_STYLES, RENDERERS
from site_data import SEASONS, VARIANT_META, pages_for

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"


def _page_shell(
    *,
    title: str,
    header_html: str,
    body_html: str,
    variant: str,
    badge: str,
) -> str:
    meta = VARIANT_META[variant]
    return f"""<!DOCTYPE html>
<html lang="en" data-rugby-effective="light">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} — proto {escape(meta["label"])}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&family=Oswald:wght@600&display=swap" rel="stylesheet">
  <style>{HEADER_STYLES}</style>
</head>
<body>
{header_html}
<div class="proto-badge">{escape(badge)} · :{escape(meta["port"])}</div>
{body_html}
<script>{HEADER_JS}</script>
</body>
</html>
"""


def _hub_body(season: str) -> str:
    tier_links = []
    other_links = []
    for page in pages_for(season):
        if page.in_tier_nav:
            tier_links.append(f'<li><a href="{page.slug}.html">{escape(page.title)}</a></li>')
        else:
            other_links.append(f'<li><a href="{page.slug}.html">{escape(page.title)}</a></li>')

    sections = ""
    if tier_links:
        sections += f"<h2>Tier maps</h2><ul>{''.join(tier_links)}</ul>"
    if other_links:
        sections += f"<h2>Combined &amp; other</h2><ul>{''.join(other_links)}</ul>"
    if not sections:
        sections = "<p><em>No maps for this season — use the season switcher to compare fallbacks.</em></p>"

    return f"""
<div class="proto-hub">
  <h1>{escape(season)}</h1>
  <p>Season hub (prototype). Tier dropdown and season switcher only list pages that exist for each season.</p>
  {sections}
</div>"""


def _map_placeholder(label: str) -> str:
    return f'<div class="proto-map-placeholder"><p>{escape(label)} — map placeholder</p></div>'


def _landing_body(variant: str) -> str:
    meta = VARIANT_META[variant]
    season_links = "".join(f'<li><a href="{s}/index.html">{escape(s)}</a></li>' for s in SEASONS)
    demo_links = """
    <li><a href="2026-2027/Counties_1_All_Leagues.html">Counties 1 + Merit (2026-2027 only)</a></li>
    <li><a href="2026-2027/National_League_3.html">National League 3 (2026-2027 only)</a></li>
    <li><a href="2025-2026/Level_12_All_Leagues.html">Level 12 Merit (2025-2026 only)</a></li>
    <li><a href="2025-2026/Counties_2.html">Counties 2 — different band vs 2026-2027</a></li>
    <li><a href="2026-2027/fixtures.html">Fixtures (2026-2027)</a></li>
    <li><a href="1999-2000/index.html">1999-2000 hub (Premiership missing)</a></li>
    """
    others = "".join(
        f'<li><a href="http://127.0.0.1:{m["port"]}/">{escape(m["label"])}</a> — :{m["port"]}</li>'
        for k, m in VARIANT_META.items()
        if k != variant
    )
    return f"""
<div class="proto-hub">
  <h1>Season navigation prototype</h1>
  <p><strong>{escape(meta["label"])}</strong> — {meta["blurb"]}</p>
  <h2>Try these pages</h2>
  <ul>{demo_links}</ul>
  <h2>All seasons</h2>
  <ul>{season_links}</ul>
  <h2>Other prototypes</h2>
  <ul>{others or "<li><em>Only this variant in this worktree</em></li>"}</ul>
</div>"""


def build(variant: str) -> None:
    if variant not in RENDERERS:
        raise SystemExit(f"Unknown variant {variant!r}; choose from {list(RENDERERS)}")

    render = RENDERERS[variant]
    meta = VARIANT_META[variant]

    DIST.mkdir(parents=True, exist_ok=True)
    for old in DIST.rglob("*"):
        if old.is_file():
            old.unlink()
    for old in sorted(DIST.rglob("*"), reverse=True):
        if old.is_dir():
            with suppress(OSError):
                old.rmdir()

    landing = _page_shell(
        title="Season nav prototypes",
        header_html="",
        body_html=_landing_body(variant),
        variant=variant,
        badge=meta["label"],
    )
    (DIST / "index.html").write_text(landing, encoding="utf-8")

    for season in SEASONS:
        season_dir = DIST / season
        season_dir.mkdir()
        depth = 1

        hub_header = render(season, depth=depth)
        (season_dir / "index.html").write_text(
            _page_shell(
                title=f"{season} maps",
                header_html=hub_header,
                body_html=_hub_body(season),
                variant=variant,
                badge=meta["label"],
            ),
            encoding="utf-8",
        )

        for page in pages_for(season):
            if page.fixed_title:
                header = render(
                    season,
                    depth=depth,
                    page_slug=page.slug,
                    fixed_title=page.title,
                )
            elif page.in_tier_nav:
                header = render(season, depth=depth, page_slug=page.slug)
            else:
                header = render(
                    season,
                    depth=depth,
                    page_slug=page.slug,
                    fixed_title=page.title,
                )

            (season_dir / f"{page.slug}.html").write_text(
                _page_shell(
                    title=f"{page.title} | {season}",
                    header_html=header,
                    body_html=_map_placeholder(f"{page.title} · {season}"),
                    variant=variant,
                    badge=meta["label"],
                ),
                encoding="utf-8",
            )

    n_pages = sum(1 for _ in DIST.rglob("*.html"))
    print(f"Built {variant} prototype -> {DIST} ({n_pages} pages)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "variant",
        nargs="?",
        default=(
            Path(__file__).with_name("variant.txt").read_text(encoding="utf-8").strip()
            if Path(__file__).with_name("variant.txt").is_file()
            else "select"
        ),
        choices=list(RENDERERS),
    )
    args = parser.parse_args()
    build(args.variant)


if __name__ == "__main__":
    main()
