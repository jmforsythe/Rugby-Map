"""Generate SEO files: sitemap.xml and robots.txt for the rugby maps site."""

import argparse
from pathlib import Path
from urllib.parse import quote

from core.config import DIST_DIR

BASE_URL = "https://rugbyunionmap.uk"


def generate_sitemap(dist_dir: Path) -> str:
    """Walk dist/ and produce a sitemap.xml string."""
    urls: list[str] = []

    for html_file in sorted(dist_dir.rglob("index.html")):
        rel = html_file.relative_to(dist_dir).parent.as_posix()
        path = f"/{quote(rel)}/" if rel != "." else "/"
        urls.append(path)

    for html_file in sorted((dist_dir / "teams").glob("*.html")):
        if html_file.name == "index.html":
            continue
        path = f"/teams/{quote(html_file.name)}"
        urls.append(path)

    xml_entries = "\n".join(f"  <url><loc>{BASE_URL}{url}</loc></url>" for url in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{xml_entries}\n"
        "</urlset>\n"
    )


def generate_robots() -> str:
    """Produce a robots.txt string."""
    return "User-agent: *\n" "Allow: /\n" f"\nSitemap: {BASE_URL}/sitemap.xml\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SEO files for the rugby maps site.")
    parser.parse_args()

    dist_dir = DIST_DIR
    if not dist_dir.exists():
        print(f"Error: {dist_dir} directory not found")
        return

    sitemap = generate_sitemap(dist_dir)
    sitemap_path = dist_dir / "sitemap.xml"
    sitemap_path.write_text(sitemap, encoding="utf-8")
    url_count = sitemap.count("<url>")
    print(f"Created {sitemap_path} ({url_count} URLs)")

    robots = generate_robots()
    robots_path = dist_dir / "robots.txt"
    robots_path.write_text(robots, encoding="utf-8")
    print(f"Created {robots_path}")


if __name__ == "__main__":
    main()
