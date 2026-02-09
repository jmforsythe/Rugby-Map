"""
Script to generate index.html pages for rugby maps.
Creates:
- An index.html in each season folder in tier_maps/
- A top-level index.html in tier_maps/ that links to season pages
"""

import argparse
from pathlib import Path

from utils import get_google_analytics_script

IS_PRODUCTION = False


def get_common_css() -> str:
    """Return common CSS styles used across all pages."""
    return """
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px;
            margin: 60px auto;
            padding: 0 20px;
            line-height: 1.6;
            color: #333;
            background: #f9f9f9;
        }
        h1 {
            font-size: 2.2em;
            margin-bottom: 0.3em;
            color: #2c3e50;
            text-align: center;
        }
        body > p {
            text-align: center;
            color: #666;
            margin-bottom: 2em;
        }
        ul {
            list-style: none;
            padding: 0;
            text-align: center;
            background: white;
            border-radius: 8px;
            padding: 1.5em;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        li {
            margin: 0.7em 0;
        }
        a {
            display: block;
            color: #2c3e50;
            text-decoration: none;
            font-size: 1.05em;
            transition: all 0.2s;
            padding: 0.8em 1.5em;
            background: #f5f7fa;
            border-radius: 6px;
            border: 1px solid #e0e0e0;
        }
        a:hover {
            background: #0066cc;
            color: white;
            border-color: #0066cc;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,102,204,0.2);
        }
        .all-tiers a {
            background: #0066cc;
            color: white;
            border-color: #0066cc;
            font-weight: 600;
        }
        .all-tiers a:hover {
            background: #0052a3;
            border-color: #0052a3;
        }
        .separator {
            margin: 2em 0;
            border-bottom: 2px solid #e0e0e0;
        }
        .back-link {
            text-align: center;
            margin: 2em 0;
        }
        .back-link a {
            color: #0066cc;
            text-decoration: none;
            font-size: 1em;
            padding: 0.8em 1.5em;
            background: white;
            border-radius: 6px;
            border: 1px solid #e0e0e0;
            display: inline-block;
            transition: all 0.2s;
        }
        .back-link a:hover {
            background: #0066cc;
            color: white;
            border-color: #0066cc;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,102,204,0.2);
        }
        .footer {
            margin-top: 3em;
            padding-top: 2em;
            border-top: 1px solid #ddd;
            font-size: 0.9em;
            color: #666;
            text-align: center;
            background: white;
            border-radius: 8px;
            padding: 1.5em;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }
        .footer a {
            color: #0066cc;
            font-size: 1em;
        }
        .footer a:hover {
            color: white;
        }
        .footer p {
            margin: 0.5em 0;
        }"""


def get_footer_html() -> str:
    """Return common footer HTML."""
    return """    <div class="footer">
        <p><a href="https://github.com/jmforsythe/Rugby-Map">View on GitHub</a></p>
        <p>Data sources: <a href="https://www.englandrugby.com/">England Rugby (RFU)</a> <a href="https://geoportal.statistics.gov.uk/">ONS</a> <a href="https://nominatim.openstreetmap.org/">OpenStreetMap</a></p>
    </div>"""


def get_season_index_html(season: str, tier_files: dict[str, list[str]]) -> str:
    """Generate HTML content for a season's index page."""
    mens_tiers = tier_files.get("mens", [])
    womens_tiers = tier_files.get("womens", [])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>English Rugby Union Team Maps - {season}</title>
    <style>
        {get_common_css()}
    </style>
    {get_google_analytics_script()}
</head>
<body>
    <div class="back-link">
        <a href="../{ "" if IS_PRODUCTION else "index.html" }">← All Seasons</a>
    </div>

    <h1>English Rugby Union Team Maps</h1>
    <p>Season: {season}</p>
"""

    # Men's tiers section
    if mens_tiers:
        html += f"""
    <div>
    <ul>
        <li class="all-tiers"><a href="All_Tiers{"/" if IS_PRODUCTION else ".html"}">All Men's Tiers</a></li>
    </ul>

    <ul>
"""
        for tier_name, tier_file in mens_tiers:
            html += f'        <li><a href="{tier_file}">{tier_name}</a></li>\n'

        html += """    </ul>
    </div>
"""

    # Women's tiers section
    if womens_tiers:
        if mens_tiers:
            html += """
    <div class="separator"></div>
"""

        html += f"""
    <div>
    <ul>
        <li class="all-tiers"><a href="All_Tiers_Women{"/" if IS_PRODUCTION else ".html"}">All Women's Tiers</a></li>
    </ul>

    <ul>
"""
        for tier_name, tier_file in womens_tiers:
            html += f'        <li><a href="{tier_file}">{tier_name}</a></li>\n'

        html += """    </ul>
    </div>
"""

    # Footer
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
    <style>
        {get_common_css()}
    </style>
    {get_google_analytics_script()}
</head>
<body>
    <h1>English Rugby Union Team Maps</h1>
    <p>Interactive maps showing the geographic distribution of teams across England.</p>

    <ul>
"""

    # Add season links (most recent first)
    for season in sorted(seasons, reverse=True):
        html += f'        <li><a href="{season}/{ "" if IS_PRODUCTION else "index.html"}">Season {season}</a></li>\n'

    html += """    </ul>"""

    # Add link to all teams page
    html += f"""
        <div class="separator"></div>
        <ul>
        <li><a href="./teams/{ "" if IS_PRODUCTION else "index.html" }">Teams</a></li>
        </ul>
"""

    html += f"""
{get_footer_html()}
</body>
</html>
"""

    return html


def detect_tier_files(season_dir: Path) -> dict[str, list[tuple]]:
    """Detect available tier map files in a season directory."""

    # Men's tiers in order
    mens_tier_order = [
        ("Premiership", f"Premiership{"/" if IS_PRODUCTION else ".html"}"),
        ("Championship", f"Championship{"/" if IS_PRODUCTION else ".html"}"),
        ("National League 1", f"National_League_1{"/" if IS_PRODUCTION else ".html"}"),
        ("National League 2", f"National_League_2{"/" if IS_PRODUCTION else ".html"}"),
        ("Regional 1", f"Regional_1{"/" if IS_PRODUCTION else ".html"}"),
        ("Regional 2", f"Regional_2{"/" if IS_PRODUCTION else ".html"}"),
        ("Counties 1", f"Counties_1{"/" if IS_PRODUCTION else ".html"}"),
        ("Counties 2", f"Counties_2{"/" if IS_PRODUCTION else ".html"}"),
        ("Counties 3", f"Counties_3{"/" if IS_PRODUCTION else ".html"}"),
        ("Counties 4", f"Counties_4{"/" if IS_PRODUCTION else ".html"}"),
        ("Counties 5", f"Counties_5{"/" if IS_PRODUCTION else ".html"}"),
    ]

    # Women's tiers in order
    womens_tier_order = [
        ("Premiership", f"Premiership_Women's{"/" if IS_PRODUCTION else ".html"}"),
        ("Championship 1", f"Championship_1{"/" if IS_PRODUCTION else ".html"}"),
        ("Championship 2", f"Championship_2{"/" if IS_PRODUCTION else ".html"}"),
        ("National Challenge 1", f"National_Challenge_1{"/" if IS_PRODUCTION else ".html"}"),
        ("National Challenge 2", f"National_Challenge_2{"/" if IS_PRODUCTION else ".html"}"),
        ("National Challenge 3", f"National_Challenge_3{"/" if IS_PRODUCTION else ".html"}"),
    ]

    mens_tiers = []
    womens_tiers = []

    # Check which files exist
    for tier_name, tier_file in mens_tier_order:
        if (season_dir / tier_file).exists():
            mens_tiers.append((tier_name, tier_file))

    for tier_name, tier_file in womens_tier_order:
        if (season_dir / tier_file).exists():
            womens_tiers.append((tier_name, tier_file))

    return {"mens": mens_tiers, "womens": womens_tiers}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate index.html pages for rugby maps.")
    parser.add_argument(
        "--production", action="store_true", help="Change folder structure for production"
    )
    args = parser.parse_args()
    global IS_PRODUCTION
    if args.production:
        IS_PRODUCTION = True

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

        if mens_count == 0 and womens_count == 0:
            print(f"  Skipping {season} - no tier maps found")
            continue

        html_content = get_season_index_html(season, tier_files)
        index_path = season_dir / "index.html"

        with open(index_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(f"  ✓ Created {index_path} ({mens_count} men's tiers, {womens_count} women's tiers)")

    # Generate top-level index.html
    top_level_html = get_top_level_index_html(seasons)
    top_level_path = tier_maps_dir / "index.html"

    with open(top_level_path, "w", encoding="utf-8") as f:
        f.write(top_level_html)

    print(f"\n✓ Created {top_level_path}")
    print("\nAll index pages generated successfully!")


if __name__ == "__main__":
    main()
