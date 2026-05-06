"""
Script to generate index.html pages for rugby maps.
Creates:
- An index.html in each season folder in dist/
- A top-level index.html in dist/ that links to season pages
"""

import argparse
import json
import re
from html import escape
from pathlib import Path
from typing import Literal

from core import (
    get_config,
    get_favicon_html,
    get_google_analytics_script,
    get_twitter_card_meta,
    set_config,
)
from core.config import DIST_DIR
from rugby import BRAND, short_season
from rugby.seo import BASE_URL, OG_DEFAULT_IMAGE, breadcrumb_ld_script, og_image_meta_html

SiteHubHighlight = Literal["home", "latest_season", "match_day", "teams", "custom_map"] | None


def discover_latest_season_dirname(dist_dir: Path) -> str:
    """Highest season slug ``YYYY-YYYY`` under dist (same heuristic as ``main()``)."""
    seasons: list[str] = []
    for item in dist_dir.iterdir():
        if (
            item.is_dir()
            and not item.name.startswith(".")
            and "-" in item.name
            and len(item.name) == 9
            and re.match(r"\d{4}-\d{4}", item.name)
        ):
            seasons.append(item.name)
    return sorted(seasons, reverse=True)[0] if seasons else ""


def site_hub_navigation_urls(*, latest_season: str, dev_prefix_to_dist_root: str) -> dict[str, str]:
    """Hrefs for the shared hub nav.

    In production uses path-absolute URLs (stable from any subtree). In development
    *dev_prefix_to_dist_root* is the ``../`` chain from the HTML file's directory up
    to ``dist`` (empty string only for ``dist/index.html``).
    """
    is_prod = get_config().is_production
    if is_prod:
        return {
            "home": "/",
            "latest_season": f"/{latest_season}/",
            "match_day": f"/{latest_season}/match_day/",
            "teams": "/teams/",
            "custom_map": "/custom-map/",
        }
    pref = dev_prefix_to_dist_root

    def jp(path_under_dist: str) -> str:
        if pref:
            return f"{pref}{path_under_dist}"
        if path_under_dist.startswith("./"):
            return path_under_dist
        return f"./{path_under_dist}"

    return {
        "home": jp("index.html"),
        "latest_season": jp(f"{latest_season}/index.html"),
        "match_day": jp(f"{latest_season}/match_day/index.html"),
        "teams": jp("teams/index.html"),
        "custom_map": jp("custom-map/index.html"),
    }


def format_site_hub_nav_html(
    urls: dict[str, str],
    *,
    latest_season_slug: str,
    highlight: SiteHubHighlight,
    css_variant: Literal["default", "map"] = "default",
) -> str:
    """Render the horizontal hub ``<nav>`` (breadcrumb-style links between major sections)."""
    specs: tuple[tuple[str, SiteHubHighlight], ...] = (
        ("Home", "home"),
        (latest_season_slug, "latest_season"),
        ("Match Day", "match_day"),
        ("Teams", "teams"),
        ("Custom Map", "custom_map"),
    )
    chunks: list[str] = []
    for label, slot in specs:
        if highlight == slot:
            chunks.append(
                f'<span class="site-hub-nav__here" aria-current="page">{escape(label)}</span>'
            )
        else:
            chunks.append(
                f'<a class="site-hub-nav__a" href="{escape(urls[slot])}">{escape(label)}</a>'
            )
    inner = '<span class="site-hub-nav__sep" aria-hidden="true">·</span>'.join(chunks)
    variant = " site-hub-nav--map" if css_variant == "map" else ""
    return f'    <nav class="site-hub-nav{variant}" aria-label="Site sections">{inner}</nav>\n'


def site_hub_nav_block(
    *,
    latest_season: str,
    dev_prefix_to_dist_root: str,
    highlight: SiteHubHighlight,
    css_variant: Literal["default", "map"] = "default",
) -> str:
    """Full hub strip: resolves URLs then renders ``format_site_hub_nav_html``."""
    urls = site_hub_navigation_urls(
        latest_season=latest_season,
        dev_prefix_to_dist_root=dev_prefix_to_dist_root,
    )
    return format_site_hub_nav_html(
        urls,
        latest_season_slug=latest_season,
        highlight=highlight,
        css_variant=css_variant,
    )


def site_hub_nav_for_map_headers(*, subdirectory_depth: int, latest_season: str) -> str:
    """Hub row for Folium map pages (tier / merit maps)."""
    if get_config().is_production:
        dev_p = ""
    else:
        dev_p = "../" * (1 + subdirectory_depth)
    return site_hub_nav_block(
        latest_season=latest_season,
        dev_prefix_to_dist_root=dev_p,
        highlight=None,
        css_variant="map",
    )


def site_hub_nav_for_match_day_header(*, latest_season: str, season_on_page: str) -> str:
    """Hub row embedded under the match-day map chrome."""
    if get_config().is_production:
        dev_p = ""
    else:
        dev_p = "../../"
    hl: SiteHubHighlight = "match_day" if season_on_page == latest_season else None
    return site_hub_nav_block(
        latest_season=latest_season,
        dev_prefix_to_dist_root=dev_p,
        highlight=hl,
        css_variant="map",
    )


_HOME_PAGE_FAQ: tuple[dict[str, str | None], ...] = (
    {
        "question": "What about leagues outside the RFU?",
        "answer_plain": (
            '"Merit" leagues organised by the local county bodies are included, but the '
            '"levels" assigned to them are somewhat arbitrary.'
        ),
        "answer_html": None,
    },
    {
        "question": "Where is Scotland / Wales?",
        "answer_plain": (
            "These are planned for the future, however English leagues were much easier to get "
            "the data from due to the RFU website layout, especially as it includes address "
            "data for each team."
        ),
        "answer_html": None,
    },
    {
        "question": "Where is Ireland?",
        "answer_plain": (
            "All-Ireland League (top 5 levels of domestic rugby) all cover the whole island, "
            "making league maps redundant. Lower leagues are organised on a provincial basis, "
            "so the maps for those would need to be collected / organised separately."
        ),
        "answer_html": None,
    },
    {
        "question": "How are league boundaries determined?",
        "answer_plain": (
            "Areas are shaded on a county / region basis, with counties that are shared "
            "between leagues further split by wards / smaller statistical areas."
        ),
        "answer_html": None,
    },
    {
        "question": "I found an error. How can I report it?",
        "answer_plain": (
            "Please open an issue on our GitHub repository with details about the error you found."
        ),
        "answer_html": (
            'Please open an issue on our <a href="https://github.com/jmforsythe/Rugby-Map/issues" '
            'target="_blank">GitHub repository</a> with details about the error you found.'
        ),
    },
)


def _home_page_faq_html() -> str:
    """Visible FAQ section (aligned with JSON-LD entries)."""
    blocks: list[str] = []
    for item in _HOME_PAGE_FAQ:
        q = escape(str(item["question"]))
        raw_html = item.get("answer_html")
        if raw_html:
            a = str(raw_html)
        else:
            a = escape(str(item["answer_plain"]))
        blocks.append(f"""        <div class="faq-item">
            <div class="faq-question" onclick="toggleFaq(this)">{q}</div>
            <div class="faq-answer">{a}</div>
        </div>""")
    return "\n\n".join(blocks)


def _home_page_json_ld_script() -> str:
    """Organization, WebSite, and FAQPage structured data for the home page."""
    org_id = f"{BASE_URL}/#organization"
    website_id = f"{BASE_URL}/#website"
    logo_url = f"{BASE_URL}/favicon.svg"
    faq_main = [
        {
            "@type": "Question",
            "name": str(item["question"]),
            "acceptedAnswer": {"@type": "Answer", "text": str(item["answer_plain"])},
        }
        for item in _HOME_PAGE_FAQ
    ]
    graph: list[dict] = [
        {
            "@type": "Organization",
            "@id": org_id,
            "name": BRAND,
            "url": BASE_URL,
            "logo": {"@type": "ImageObject", "url": logo_url},
        },
        {
            "@type": "WebSite",
            "@id": website_id,
            "url": BASE_URL,
            "name": BRAND,
            "publisher": {"@id": org_id},
        },
        {"@type": "FAQPage", "mainEntity": faq_main},
    ]
    payload = {"@context": "https://schema.org", "@graph": graph}
    return f'    <script type="application/ld+json">{json.dumps(payload, ensure_ascii=True)}</script>\n'


def get_footer_html() -> str:
    """Return common footer HTML."""
    return """    <div class="footer">
        <p><a href="https://github.com/jmforsythe/Rugby-Map">View on GitHub</a></p>
        <p>Data sources:
            <a href="https://www.englandrugby.com/">England Rugby (RFU)</a>
            <a href="https://geoportal.statistics.gov.uk/">ONS</a>
            <a href="https://nominatim.openstreetmap.org/">OpenStreetMap</a>
            <a href="https://gadm.org/">GADM</a>
        </p>
    </div>"""


def _link(name: str) -> str:
    """Return the href for a map file (directory/ in production, file.html otherwise)."""
    return f"{name}/" if get_config().is_production else f"{name}.html"


def _build_pyramid_section(
    pyramid_tiers: list[tuple[str, str]],
    all_tiers_href: str,
    all_leagues_href: str | None = None,
    tier_plus_merit: dict[str, str] | None = None,
    merit_only_tiers: list[tuple[str, str]] | None = None,
) -> str:
    """Build the pyramid tier list, optionally with a +merit column."""
    has_merit_col = all_leagues_href is not None or bool(merit_only_tiers)
    plus_merit = tier_plus_merit or {}
    extra_merit = merit_only_tiers or []

    html = '    <div class="pyramid-section">\n'
    html += '    <table class="tier-table">\n'

    if has_merit_col:
        html += "    <thead><tr>"
        html += '<th class="tier-table__head">Pyramid</th>'
        html += '<th class="tier-table__head">+ Merit</th>'
        html += "</tr></thead>\n"

    html += "    <tbody>\n"

    # "All" row
    merit_cell = ""
    if has_merit_col and all_leagues_href:
        merit_cell = f'<a href="{all_leagues_href}">All Leagues</a>'
    html += "    <tr>\n"
    html += f'        <td><a class="tier-link tier-link--primary" href="{all_tiers_href}">All Tiers</a></td>\n'
    if has_merit_col:
        if merit_cell:
            html += f'        <td><a class="tier-link tier-link--primary" href="{all_leagues_href}">All Leagues</a></td>\n'
        else:
            html += "        <td></td>\n"
    html += "    </tr>\n"

    # Per-tier rows
    for tier_name, tier_href in pyramid_tiers:
        html += "    <tr>\n"
        html += f'        <td><a class="tier-link" href="{tier_href}">{tier_name}</a></td>\n'
        if has_merit_col:
            merit_href = plus_merit.get(tier_name)
            if merit_href:
                html += f'        <td><a class="tier-link" href="{merit_href}">{tier_name} + Merit</a></td>\n'
            else:
                html += "        <td></td>\n"
        html += "    </tr>\n"

    # Merit-only rows (tiers below the pyramid)
    for tier_name, tier_href in extra_merit:
        html += "    <tr>\n"
        html += "        <td></td>\n"
        html += (
            f'        <td><a class="tier-link" href="{tier_href}">{tier_name} (Merit)</a></td>\n'
        )
        html += "    </tr>\n"

    html += "    </tbody>\n"
    html += "    </table>\n    </div>\n"
    return html


def _build_merit_section(
    merit_competitions: list[tuple[str, str, list[tuple[str, str]]]],
    all_leagues_href: str | None,
) -> str:
    """Build the merit competitions section as collapsible cards."""
    if not merit_competitions:
        return ""

    html = '    <div class="merit-section">\n'
    html += "    <h3>Merit Competitions</h3>\n"

    if all_leagues_href:
        html += (
            f'    <a class="all-leagues-btn" href="{all_leagues_href}">'
            "All Leagues (Pyramid + Merit Combined)</a>\n"
        )

    html += '    <div class="merit-grid">\n'

    for comp_display, comp_all_href, comp_tiers in merit_competitions:
        html += '    <details class="merit-card">\n'
        html += f"    <summary>{comp_display}</summary>\n"
        html += '    <div class="merit-card-body">\n'
        html += f'    <a class="merit-all-tiers" href="{comp_all_href}">All Tiers</a>\n'
        html += "    <ul>\n"
        for tier_name, tier_href in comp_tiers:
            html += f'        <li><a href="{tier_href}">{tier_name}</a></li>\n'
        html += "    </ul>\n"
        html += "    </div>\n"
        html += "    </details>\n"

    html += "    </div>\n    </div>\n"
    return html


def get_season_index_html(season: str, tier_files: dict, latest_season: str) -> str:
    """Generate HTML content for a season's index page."""
    mens_tiers: list[tuple[str, str]] = tier_files.get("mens", [])
    womens_tiers: list[tuple[str, str]] = tier_files.get("womens", [])
    has_all_leagues = tier_files.get("has_all_leagues", False)
    has_match_day = tier_files.get("has_match_day", False)
    merit_competitions: list[tuple[str, str, list[tuple[str, str]]]] = tier_files.get("merit", [])

    match_day_link = ""
    if has_match_day:
        match_day_href = "match_day/" if get_config().is_production else "match_day/index.html"
        match_day_link = (
            f'    <div class="back-link">'
            f'<a href="{match_day_href}">Fixtures &amp; Results →</a></div>\n'
        )

    season_short = short_season(season)
    page_title = f"{season_short} Season | {BRAND}"
    page_desc = (
        f"Browse {season_short} maps for every men's and women's league tier—Premiership to "
        f"Counties, merit competitions, and all-tier views. Club locations, territory shading "
        f"by county and ward, and travel distances between teams."
    )

    is_prod = get_config().is_production
    season_head_extra = ""
    if is_prod:
        page_url = f"{BASE_URL}/{season}/"
        season_head_extra = (
            f'    <link rel="canonical" href="{escape(page_url)}">\n'
            f'    <meta property="og:url" content="{escape(page_url)}" />\n'
            + og_image_meta_html(escape(OG_DEFAULT_IMAGE), indent="    ")
            + "\n"
            f"    {get_twitter_card_meta()}\n"
            + breadcrumb_ld_script(
                [("Home", f"{BASE_URL}/"), (season, page_url)],
                indent="    ",
            )
            + "\n"
        )

    hl: SiteHubHighlight = "latest_season" if season == latest_season else None
    hub_nav = ""
    if latest_season:
        hub_nav = site_hub_nav_block(
            latest_season=latest_season,
            dev_prefix_to_dist_root="../",
            highlight=hl,
            css_variant="default",
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{escape(page_desc)}">
    <meta property="og:title" content="{escape(page_title)}" />
    <meta property="og:description" content="{escape(page_desc)}" />
    <meta property="og:type" content="website" />
{season_head_extra}    <title>{escape(page_title)}</title>
    <link rel="stylesheet" href="../styles.css">
    {get_favicon_html(depth=1)}
    {get_google_analytics_script()}
</head>
<body>
    <div class="back-link">
        <a href="../{ "" if get_config().is_production else "index.html" }">← All Seasons</a>
    </div>
{hub_nav}

    <h1>English Rugby Union Team Maps</h1>
    <p>Season: {season}</p>

{match_day_link}"""

    # Men's sections
    if mens_tiers or merit_competitions:
        html += "\n    <h2>Men's</h2>\n"
        if mens_tiers:
            tier_plus_merit: dict[str, str] = tier_files.get("tier_plus_merit", {})
            merit_only: list[tuple[str, str]] = tier_files.get("merit_only_tiers", [])
            html += _build_pyramid_section(
                mens_tiers,
                all_tiers_href=_link("All_Tiers"),
                all_leagues_href=_link("All_Leagues") if has_all_leagues else None,
                tier_plus_merit=tier_plus_merit,
                merit_only_tiers=merit_only,
            )
        if merit_competitions:
            html += _build_merit_section(
                merit_competitions,
                all_leagues_href=None,
            )

    # Women's section (pyramid only, no merit)
    if womens_tiers:
        html += f"""
    <h2>Women's</h2>
    <div>
    <ul>
        <li class="all-tiers"><a href="{_link("All_Tiers_Women")}">All Women's Tiers</a></li>
    </ul>
    <ul>
"""
        for tier_name, tier_file in womens_tiers:
            html += f'        <li><a href="{tier_file}">{tier_name}</a></li>\n'
        html += """    </ul>
    </div>
"""

    html += f"""
{get_footer_html()}
</body>
</html>
"""
    return html


def get_top_level_index_html(seasons: list[str]) -> str:
    """Generate HTML content for the top-level index page."""
    sorted_seasons = sorted(seasons, reverse=True)
    latest = sorted_seasons[0] if sorted_seasons else ""
    is_prod = get_config().is_production

    def _season_href(s: str) -> str:
        return f"{s}/" if is_prod else f"{s}/index.html"

    teams_href = "./teams/" if is_prod else "./teams/index.html"
    match_day_href = f"./{latest}/match_day/" if is_prod else f"./{latest}/match_day/index.html"
    custom_map_href = "./custom-map/" if is_prod else "./custom-map/index.html"

    home_title = f"{BRAND} \u2013 English Rugby Club & League Maps"
    home_desc = (
        "Interactive maps of English rugby union: every club on the map, league territory "
        "shading, and travel distances across the men's pyramid, county merit leagues, and "
        "women's tiers. Browse any season from Premiership down to local rugby."
    )

    home_head_extra = ""
    if is_prod:
        home_head_extra = (
            f'    <link rel="canonical" href="{escape(BASE_URL)}">\n'
            f'    <meta property="og:url" content="{escape(BASE_URL)}" />\n'
            + og_image_meta_html(escape(OG_DEFAULT_IMAGE), indent="    ")
            + "\n"
            f"    {get_twitter_card_meta()}\n" + _home_page_json_ld_script()
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{escape(home_desc)}">
    <meta property="og:title" content="{escape(home_title)}" />
    <meta property="og:description" content="{escape(home_desc)}" />
    <meta property="og:type" content="website" />
{home_head_extra}    <title>{escape(home_title)}</title>
    <link rel="stylesheet" href="styles.css">
    {get_favicon_html(depth=0)}
    {get_google_analytics_script()}
</head>
<body>
    <h1>English Rugby Union Team Maps</h1>
    <p>Interactive maps showing the geographic distribution of teams across England.</p>

    <div class="hero-links">
        <a class="hero-card hero-card--primary" href="{_season_href(latest)}">
            <span class="hero-card__label">Current Season</span>
            <span class="hero-card__title">{latest}</span>
        </a>
        <a class="hero-card" href="{match_day_href}">
            <span class="hero-card__label">This Week</span>
            <span class="hero-card__title">Match Day</span>
        </a>
        <a class="hero-card" href="{teams_href}">
            <span class="hero-card__label">Browse</span>
            <span class="hero-card__title">All Teams</span>
        </a>
        <a class="hero-card" href="{custom_map_href}">
            <span class="hero-card__label">Build</span>
            <span class="hero-card__title">Custom Map</span>
        </a>
    </div>
"""

    # Past seasons grid
    past = sorted_seasons[1:]
    if past:
        html += '    <h2 class="past-heading">Past Seasons</h2>\n'
        html += '    <div class="season-grid">\n'
        for s in past:
            html += f'        <a class="season-card" href="{_season_href(s)}">' f"{s}</a>\n"
        html += "    </div>\n"

    # FAQ section
    html += f"""
    <div class="faq">
        <h2>FAQ</h2>

{_home_page_faq_html()}
    </div>

    <script>
        function toggleFaq(element) {{
            element.classList.toggle('active');
            const answer = element.nextElementSibling;
            answer.classList.toggle('active');
        }}
    </script>
"""

    html += f"""
{get_footer_html()}
</body>
</html>
"""

    return html


def _detect_existing(
    season_dir: Path,
    candidates: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Return only the (display_name, href) pairs whose files actually exist."""
    found = []
    for display, name in candidates:
        href = _link(name)
        if (season_dir / href).exists():
            found.append((display, href))
    return found


def detect_tier_files(season_dir: Path) -> dict:
    """Detect available tier map files in a season directory."""

    mens_candidates = [
        ("Premiership", "Premiership"),
        ("Championship", "Championship"),
        *((f"National League {i}", f"National_League_{i}") for i in range(1, 4)),
        *((f"Regional {i}", f"Regional_{i}") for i in range(1, 3)),
        *((f"Counties {i}", f"Counties_{i}") for i in range(1, 6)),
        *((f"Level {i}", f"Level_{i}") for i in range(5, 20)),
    ]

    womens_candidates = [
        ("Premiership", "Premiership_Women's"),
        ("Championship 1", "Championship_1"),
        ("Championship 2", "Championship_2"),
        ("National Challenge 1", "National_Challenge_1"),
        ("National Challenge 2", "National_Challenge_2"),
        ("National Challenge 3", "National_Challenge_3"),
    ]

    mens_tiers = _detect_existing(season_dir, mens_candidates)
    womens_tiers = _detect_existing(season_dir, womens_candidates)

    has_all_leagues = (season_dir / _link("All_Leagues")).exists()

    # Detect per-tier pyramid+merit variants (e.g. Counties_1_All_Leagues)
    tier_plus_merit: dict[str, str] = {}
    for display, name in mens_candidates:
        combined_name = f"{name}_All_Leagues"
        href = _link(combined_name)
        if (season_dir / href).exists():
            tier_plus_merit[display] = href

    # Detect merit-only tiers below the pyramid (e.g. Level_12_All_Leagues)
    merit_only_candidates = [(f"Level {i}", f"Level_{i}_All_Leagues") for i in range(5, 25)]
    merit_only_tiers = _detect_existing(season_dir, merit_only_candidates)

    # Detect merit competitions
    merit_dir = season_dir / "merit"
    merit_competitions: list[tuple[str, str, list[tuple[str, str]]]] = []
    if merit_dir.is_dir():
        for comp_dir in sorted(merit_dir.iterdir()):
            if not comp_dir.is_dir():
                continue
            all_tiers_href = f"merit/{comp_dir.name}/{_link('All_Tiers')}"
            if not (season_dir / all_tiers_href).exists():
                continue

            comp_display = comp_dir.name.replace("_", " ")
            tier_candidates = [
                (f"{comp_display} Premier", f"{comp_dir.name}_Premier"),
                *((f"{comp_display} {i}", f"{comp_dir.name}_{i}") for i in range(1, 20)),
            ]
            comp_tiers_raw = _detect_existing(comp_dir, tier_candidates)
            prefix = f"merit/{comp_dir.name}/"
            comp_tiers = [(name, prefix + href) for name, href in comp_tiers_raw]
            merit_competitions.append((comp_display, all_tiers_href, comp_tiers))

    has_match_day = (season_dir / "match_day" / "index.html").exists() or (
        season_dir / "match_day.html"
    ).exists()

    return {
        "mens": mens_tiers,
        "womens": womens_tiers,
        "has_all_leagues": has_all_leagues,
        "merit": merit_competitions,
        "tier_plus_merit": tier_plus_merit,
        "merit_only_tiers": merit_only_tiers,
        "has_match_day": has_match_day,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate index.html pages for rugby maps.")
    parser.add_argument(
        "--production", action="store_true", help="Change folder structure for production"
    )
    args = parser.parse_args()
    if args.production:
        set_config(is_production=True)

    dist_dir = DIST_DIR

    if not dist_dir.exists():
        print(f"Error: {dist_dir} directory not found")
        return

    # Find all season directories
    seasons = []
    for item in dist_dir.iterdir():
        # Check if it looks like a season (YYYY-YYYY format)
        if (
            item.is_dir()
            and not item.name.startswith(".")
            and "-" in item.name
            and len(item.name) == 9
        ):
            seasons.append(item.name)

    if not seasons:
        print(f"No season directories found in {dist_dir}")
        return

    print(f"Found {len(seasons)} season(s): {', '.join(sorted(seasons))}")

    latest_season_hub = sorted(seasons, reverse=True)[0] if seasons else ""

    # Generate index.html for each season
    for season in seasons:
        season_dir = dist_dir / season
        tier_files = detect_tier_files(season_dir)

        mens_count = len(tier_files.get("mens", []))
        womens_count = len(tier_files.get("womens", []))
        merit_count = len(tier_files.get("merit", []))

        if mens_count == 0 and womens_count == 0 and merit_count == 0:
            print(f"  Skipping {season} - no tier maps found")
            continue

        html_content = get_season_index_html(
            season,
            tier_files,
            latest_season=latest_season_hub or season,
        )
        index_path = season_dir / "index.html"

        with open(index_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(
            f"  Created {index_path} "
            f"({mens_count} men's tiers, {womens_count} women's tiers, "
            f"{merit_count} merit competitions)"
        )

    # Generate top-level index.html
    top_level_html = get_top_level_index_html(seasons)
    top_level_path = dist_dir / "index.html"

    with open(top_level_path, "w", encoding="utf-8") as f:
        f.write(top_level_html)

    print(f"\nCreated {top_level_path}")
    print("\nAll index pages generated successfully!")


if __name__ == "__main__":
    main()
