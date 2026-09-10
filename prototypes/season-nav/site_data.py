"""Minimal fake site graph for season-navigation prototypes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MapPage:
    slug: str
    title: str
    in_tier_nav: bool = True
    fixed_title: bool = False


FIXTURES = MapPage("fixtures", "Fixtures & Results", in_tier_nav=False, fixed_title=True)

# Newest first — mirrors production season ordering.
SEASONS: tuple[str, ...] = (
    "2026-2027",
    "2025-2026",
    "2024-2025",
    "2022-2023",
    "1999-2000",
)

# Per-season page lists — tier nav dropdown is built from ``in_tier_nav`` entries only.
SEASON_PAGES: dict[str, tuple[MapPage, ...]] = {
    "2026-2027": (
        MapPage("Premiership", "Premiership"),
        MapPage("Championship", "Championship"),
        MapPage("National_League_1", "National League 1"),
        MapPage("National_League_2", "National League 2"),
        MapPage("National_League_3", "National League 3"),
        MapPage("Regional_1", "Regional 1"),
        MapPage("Regional_2", "Regional 2"),
        MapPage("Counties_1", "Counties 1"),
        MapPage("Counties_2", "Counties 2"),
        MapPage("Counties_1_All_Leagues", "Counties 1 + Merit"),
        MapPage("All_Tiers", "All Tiers Men", in_tier_nav=False),
        MapPage("All_Leagues", "All Leagues Men", in_tier_nav=False),
        FIXTURES,
    ),
    "2025-2026": (
        MapPage("Premiership", "Premiership"),
        MapPage("Championship", "Championship"),
        MapPage("National_League_1", "National League 1"),
        MapPage("National_League_2", "National League 2"),
        # NL 3 dropped this season
        MapPage("Regional_1", "Regional 1"),
        # Regional 2 not run
        MapPage("Counties_2", "Counties 2"),  # Counties 1 absent — renamed band
        MapPage("Level_12_All_Leagues", "Level 12 (Merit)"),
        MapPage("All_Tiers", "All Tiers Men", in_tier_nav=False),
        # All_Leagues not generated this season
        FIXTURES,
    ),
    "2024-2025": (
        MapPage("Premiership", "Premiership"),
        MapPage("Championship", "Championship"),
        MapPage("National_League_1", "National League 1"),
        MapPage("Regional_1", "Regional 1"),
        MapPage("Counties_1", "Counties 1"),
        MapPage("All_Tiers", "All Tiers Men", in_tier_nav=False),
        MapPage("All_Leagues", "All Leagues Men", in_tier_nav=False),
        FIXTURES,
    ),
    "2022-2023": (
        MapPage("Premiership", "Premiership"),
        MapPage("Championship", "Championship"),
        MapPage("National_League_1", "National League 1"),
        MapPage("All_Tiers", "All Tiers Men", in_tier_nav=False),
    ),
    "1999-2000": (MapPage("Championship", "Championship"),),  # no Premiership this far back
}

VARIANT_META: dict[str, dict[str, str]] = {
    "select": {
        "label": "Season select dropdown",
        "blurb": "Replace the season link with a &lt;select&gt; (same pattern as the tier dropdown).",
        "port": "8765",
    },
    "split": {
        "label": "Split-button season crumb (large trigger)",
        "blurb": "Season label links to the season hub; bordered split control with a larger dropdown trigger (tier-select height).",
        "port": "8766",
    },
    "arrows": {
        "label": "Previous / next season",
        "blurb": "Compact prev/current/next control; skips seasons where this page does not exist.",
        "port": "8767",
    },
    "menu": {
        "label": "Link menu (&lt;details&gt;)",
        "blurb": "Real &lt;a&gt; links in a dropdown — middle-click and copy-link friendly.",
        "port": "8768",
    },
}


def pages_for(season: str) -> tuple[MapPage, ...]:
    return SEASON_PAGES.get(season, ())


def page_def(season: str, slug: str) -> MapPage | None:
    for page in pages_for(season):
        if page.slug == slug:
            return page
    return None


def page_exists(season: str, slug: str) -> bool:
    if not slug:
        return True
    return page_def(season, slug) is not None


def tier_nav_for(season: str) -> tuple[tuple[str, str], ...]:
    """(dropdown label, slug) pairs for the current season's tier maps."""
    return tuple((p.title, p.slug) for p in pages_for(season) if p.in_tier_nav)


def href_for(season: str, slug: str, *, depth: int) -> str | None:
    """Relative href from a page ``depth`` levels below the site root."""
    if not page_exists(season, slug):
        return None
    up = "../" * depth
    if not slug:
        return f"{up}{season}/index.html"
    return f"{up}{season}/{slug}.html"


def season_nav_targets(
    current_season: str, page_slug: str, *, depth: int
) -> list[tuple[str, str | None, str]]:
    """(season, href_or_none, note) for season switcher UI."""
    rows: list[tuple[str, str | None, str]] = []
    for season in SEASONS:
        target = href_for(season, page_slug, depth=depth)
        if target:
            note = "same page" if page_slug else "season hub"
        elif page_slug:
            fallback = href_for(season, "", depth=depth)
            note = "no equivalent — season hub only"
            target = fallback
        else:
            note = "season hub"
        rows.append((season, target, note))
    return rows


def adjacent_seasons(current: str, page_slug: str) -> tuple[str | None, str | None]:
    idx = SEASONS.index(current)
    prev_s = next_s = None
    for i in range(idx - 1, -1, -1):
        if page_exists(SEASONS[i], page_slug):
            prev_s = SEASONS[i]
            break
    for i in range(idx + 1, len(SEASONS)):
        if page_exists(SEASONS[i], page_slug):
            next_s = SEASONS[i]
            break
    return prev_s, next_s
