"""
Script to generate index.html pages for rugby maps.
Creates:
- An index.html in each season folder in tier_maps/
- A top-level index.html in tier_maps/ that links to season pages
"""

import argparse
from pathlib import Path

from utils import get_config, get_google_analytics_script, set_config


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
) -> str:
    """Build the pyramid tier list with a prominent 'All Tiers' button."""
    html = '    <div class="pyramid-section">\n'
    html += "    <ul>\n"
    html += f'        <li class="all-tiers"><a href="{all_tiers_href}">All Pyramid Tiers</a></li>\n'
    html += "    </ul>\n"
    html += "    <ul>\n"

    for tier_name, tier_href in pyramid_tiers:
        html += f'        <li><a href="{tier_href}">{tier_name}</a></li>\n'

    html += "    </ul>\n    </div>\n"
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


def get_season_index_html(season: str, tier_files: dict) -> str:
    """Generate HTML content for a season's index page."""
    mens_tiers: list[tuple[str, str]] = tier_files.get("mens", [])
    womens_tiers: list[tuple[str, str]] = tier_files.get("womens", [])
    has_all_leagues = tier_files.get("has_all_leagues", False)
    merit_competitions: list[tuple[str, str, list[tuple[str, str]]]] = tier_files.get("merit", [])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>English Rugby Union Team Maps - {season}</title>
    <link rel="stylesheet" href="../styles.css">
    {get_google_analytics_script()}
</head>
<body>
    <div class="back-link">
        <a href="../{ "" if get_config().is_production else "index.html" }">← All Seasons</a>
    </div>

    <h1>English Rugby Union Team Maps</h1>
    <p>Season: {season}</p>
"""

    # Men's sections
    if mens_tiers or merit_competitions:
        html += "\n    <h2>Men's</h2>\n"
        if mens_tiers:
            html += _build_pyramid_section(
                mens_tiers,
                all_tiers_href=_link("All_Tiers"),
            )
        if merit_competitions:
            html += _build_merit_section(
                merit_competitions,
                all_leagues_href=_link("All_Leagues") if has_all_leagues else None,
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
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta property="og:title" content="English Rugby Union Team Maps" />
    <meta property="og:description" content="Interactive maps showing the geographic distribution of English rugby union teams and leagues." />
    <meta property="og:image" content="https://raw.githubusercontent.com/jmforsythe/Rugby-Map/main/example.png" />
    <meta property="og:type" content="website" />
    <meta property="og:url" content="https://rugbyunionmap.uk" />
    <title>English Rugby Union Team Maps</title>
    <link rel="stylesheet" href="styles.css">
    {get_google_analytics_script()}
</head>
<body>
    <h1>English Rugby Union Team Maps</h1>
    <p>Interactive maps showing the geographic distribution of teams across England.</p>

    <ul>
"""

    # Add season links (most recent first)
    for season in sorted(seasons, reverse=True):
        html += f'        <li><a href="{season}/{ "" if get_config().is_production else "index.html"}">Season {season}</a></li>\n'

    html += """    </ul>"""

    # Add link to all teams page
    html += f"""
        <div class="separator"></div>
        <ul>
        <li><a href="./teams/{ "" if get_config().is_production else "index.html" }">Teams</a></li>
        </ul>
"""

    # Add FAQ section
    html += """
    <div class="faq">
        <h2>FAQ</h2>

        <div class="faq-item">
            <div class="faq-question" onclick="toggleFaq(this)">What about leagues outside the RFU?</div>
            <div class="faq-answer">"Merit" leagues organised by the local county bodies are included, but the "levels" assigned to them are somewhat arbitrary.</div>
        </div>

        <div class="faq-item">
            <div class="faq-question" onclick="toggleFaq(this)">Where is Scotland / Wales?</div>
            <div class="faq-answer">These are planned for the future, however English leagues were much easier to get the data from due to the RFU website layout, especially as it includes address data for each team.</div>
        </div>

        <div class="faq-item">
            <div class="faq-question" onclick="toggleFaq(this)">Where is Ireland?</div>
            <div class="faq-answer">All-Ireland League (top 5 levels of domestic rugby) all cover the whole island, making league maps redundant. Lower leagues are organised on a provincial basis, so the maps for those would need to be collected / organised separately.</div>
        </div>

        <div class="faq-item">
            <div class="faq-question" onclick="toggleFaq(this)">How are league boundaries determined?</div>
            <div class="faq-answer">Areas are shaded on a county / region basis, with counties that are shared between leagues further split by wards / smaller statistical areas.</div>
        </div>

        <div class="faq-item">
            <div class="faq-question" onclick="toggleFaq(this)">I found an error. How can I report it?</div>
            <div class="faq-answer">Please open an issue on our <a href="https://github.com/jmforsythe/Rugby-Map/issues" target="_blank">GitHub repository</a> with details about the error you found.</div>
        </div>
    </div>

    <script>
        function toggleFaq(element) {
            element.classList.toggle('active');
            const answer = element.nextElementSibling;
            answer.classList.toggle('active');
        }
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
        ("National League 1", "National_League_1"),
        ("National League 2", "National_League_2"),
        ("Regional 1", "Regional_1"),
        ("Regional 2", "Regional_2"),
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
                *((f"Counties {i}", f"Counties_{i}") for i in range(1, 6)),
                *((f"Level {i}", f"Level_{i}") for i in range(5, 20)),
            ]
            comp_tiers_raw = _detect_existing(comp_dir, tier_candidates)
            prefix = f"merit/{comp_dir.name}/"
            comp_tiers = [(name, prefix + href) for name, href in comp_tiers_raw]
            merit_competitions.append((comp_display, all_tiers_href, comp_tiers))

    return {
        "mens": mens_tiers,
        "womens": womens_tiers,
        "has_all_leagues": has_all_leagues,
        "merit": merit_competitions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate index.html pages for rugby maps.")
    parser.add_argument(
        "--production", action="store_true", help="Change folder structure for production"
    )
    args = parser.parse_args()
    if args.production:
        set_config(is_production=True)

    """Generate index.html files for all seasons and top-level."""
    tier_maps_dir = Path("tier_maps")

    if not tier_maps_dir.exists():
        print(f"Error: {tier_maps_dir} directory not found")
        return

    # Find all season directories
    seasons = []
    for item in tier_maps_dir.iterdir():
        # Check if it looks like a season (YYYY-YYYY format)
        if (
            item.is_dir()
            and not item.name.startswith(".")
            and "-" in item.name
            and len(item.name) == 9
        ):
            seasons.append(item.name)

    if not seasons:
        print(f"No season directories found in {tier_maps_dir}")
        return

    print(f"Found {len(seasons)} season(s): {', '.join(sorted(seasons))}")

    # Generate index.html for each season
    for season in seasons:
        season_dir = tier_maps_dir / season
        tier_files = detect_tier_files(season_dir)

        mens_count = len(tier_files.get("mens", []))
        womens_count = len(tier_files.get("womens", []))
        merit_count = len(tier_files.get("merit", []))

        if mens_count == 0 and womens_count == 0 and merit_count == 0:
            print(f"  Skipping {season} - no tier maps found")
            continue

        html_content = get_season_index_html(season, tier_files)
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
    top_level_path = tier_maps_dir / "index.html"

    with open(top_level_path, "w", encoding="utf-8") as f:
        f.write(top_level_html)

    print(f"\nCreated {top_level_path}")
    print("\nAll index pages generated successfully!")


if __name__ == "__main__":
    main()
