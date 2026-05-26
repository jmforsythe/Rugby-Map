"""Generate SEO files: sitemap.xml and robots.txt for the rugby maps site."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from core.config import DIST_DIR, REPO_ROOT

BASE_URL = "https://rugbyunionmap.uk"

# Encode each path segment for URLs; keep apostrophe literal to match on-disk names
# (e.g. Premiership_Women's/) and avoid canonical/sitemap mismatches with %27.
_PATH_SEGMENT_SAFE = "'"


def encode_url_path(site_path: str) -> str:
    """Return a pathname (leading ``/``) with per-segment quoting for ``<loc>``/canonical."""
    if not site_path or site_path == "/":
        return "/"
    trailing_slash = site_path.endswith("/")
    parts = [p for p in site_path.strip("/").split("/") if p]
    encoded = "/" + "/".join(quote(p, safe=_PATH_SEGMENT_SAFE) for p in parts)
    return encoded + "/" if trailing_slash else encoded


def absolute_url(site_path: str) -> str:
    """``BASE_URL`` + encoded pathname (``/`` for site root)."""
    if site_path in ("", "/"):
        return f"{BASE_URL}/"
    return f"{BASE_URL}{encode_url_path(site_path)}"


def absolute_url_for_dist_file(dist_dir: Path, html_file: Path) -> str:
    """Public URL for a file under *dist_dir* (``index.html`` → directory URL)."""
    rel = html_file.relative_to(dist_dir)
    if rel.name == "index.html":
        parent = rel.parent.as_posix()
        site_path = "/" if parent == "." else f"/{parent}/"
    else:
        site_path = f"/{rel.as_posix()}"
    return absolute_url(site_path)


# Share image deployed at site root (copied from repo `example.png` in main()).
OG_SHARE_IMAGE_FILE = "example.png"
OG_IMAGE_WIDTH = 709
OG_IMAGE_HEIGHT = 901
OG_DEFAULT_IMAGE = f"{BASE_URL}/{OG_SHARE_IMAGE_FILE}"


def og_image_meta_html(escaped_image_url: str, *, indent: str = "") -> str:
    """Open Graph and Twitter image tags. *escaped_image_url* must be HTML-escaped."""
    pad = indent
    return (
        f'{pad}<meta property="og:image" content="{escaped_image_url}" />\n'
        f'{pad}<meta property="og:image:width" content="{OG_IMAGE_WIDTH}" />\n'
        f'{pad}<meta property="og:image:height" content="{OG_IMAGE_HEIGHT}" />\n'
        f'{pad}<meta name="twitter:image" content="{escaped_image_url}" />'
    )


def breadcrumb_list_json_ld(items: list[tuple[str, str]]) -> str:
    """JSON-LD BreadcrumbList. *items* are (name, absolute_url) root-to-leaf."""
    data = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": name,
                "item": url,
            }
            for i, (name, url) in enumerate(items)
        ],
    }
    return json.dumps(data, ensure_ascii=True)


def breadcrumb_ld_script(items: list[tuple[str, str]], *, indent: str = "    ") -> str:
    """A single <script type=\"application/ld+json\"> block for BreadcrumbList."""
    return f'{indent}<script type="application/ld+json">{breadcrumb_list_json_ld(items)}</script>'


def copy_share_image(dist_dir: Path) -> None:
    """Copy repo example.png to dist so OG URLs stay on-site."""
    src = REPO_ROOT / OG_SHARE_IMAGE_FILE
    if not src.is_file():
        return
    dest = dist_dir / OG_SHARE_IMAGE_FILE
    shutil.copy2(src, dest)


def _lastmod_utc_date(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=UTC).date().isoformat()
    except OSError:
        return ""


# Path on site (pathname + query is empty): "/2025-2026/", "/teams/foo.html".
# Relative sitemap hints only; crawlers mostly use these weakly versus internal links.
_SEASON_ROOT_INDEX = re.compile(r"^/\d{4}-\d{4}/$")
_MATCH_DAY_INDEX = re.compile(r"^/\d{4}-\d{4}/match_day/$")
_SEASON_DIR_NAME = re.compile(r"^\d{4}-\d{4}$")


def _is_redirect_stub(html_file: Path) -> bool:
    """True when *html_file* is a generated legacy redirect (marked in HTML)."""
    try:
        head = html_file.read_text(encoding="utf-8", errors="replace")[:800]
    except OSError:
        return False
    return 'data-rugby-redirect="1"' in head


def _discover_latest_season(dist_dir: Path) -> str:
    """Highest ``YYYY-YYYY`` season slug under *dist_dir*; ``""`` if none."""
    seasons = [
        item.name
        for item in dist_dir.iterdir()
        if item.is_dir() and _SEASON_DIR_NAME.fullmatch(item.name)
    ]
    return sorted(seasons, reverse=True)[0] if seasons else ""


def _priority_for_site_path(site_path: str, *, latest_season: str = "") -> float:
    """Higher = more prominent in ``<priority>`` (relative inside this domain only).

    When *latest_season* is supplied (e.g. ``"2025-2026"``), its hub index and
    match-day page get a small bump above older seasons so crawlers can pick up
    the current year's content as the canonical entry point.
    """
    if site_path == "/":
        return 1.0
    if latest_season and site_path in (f"/{latest_season}/", f"/{latest_season}/match_day/"):
        return 0.9
    if site_path == "/teams/" or site_path == "/custom-map/":
        return 0.85
    if _SEASON_ROOT_INDEX.fullmatch(site_path) or _MATCH_DAY_INDEX.fullmatch(site_path):
        return 0.85
    return 0.5


def generate_sitemap(dist_dir: Path) -> str:
    """Walk dist/ HTML and produce a sitemap.xml string.

    Emits trailing-slash URLs for ``index.html`` (directory canonical form) and
    full paths for every other ``*.html`` (tier maps, team pages, etc.).

    ``<priority>`` reflects hub vs leaf tiers; the latest season's hub gets a
    small boost above older seasons. Entries are sorted with higher priority
    first then by URL.
    """
    url_parts: list[tuple[float, str, str]] = []
    latest_season = _discover_latest_season(dist_dir)

    for html_file in sorted(dist_dir.rglob("*.html")):
        try:
            rel_path = html_file.relative_to(dist_dir)
        except ValueError:
            continue
        # Experimental football maps are not linked from the rugby site; omit from sitemap.
        if rel_path.parts and rel_path.parts[0] == "football":
            continue

        # Redirect stubs for legacy URLs are noindex and must not be re-submitted.
        if html_file.name == "index.html" and _is_redirect_stub(html_file):
            continue

        loc = absolute_url_for_dist_file(dist_dir, html_file)
        url_path = encode_url_path(
            "/" + rel_path.parent.as_posix() + "/"
            if rel_path.name == "index.html"
            else f"/{rel_path.as_posix()}"
        )
        prio = _priority_for_site_path(url_path, latest_season=latest_season)
        lm = _lastmod_utc_date(html_file)
        url_parts.append((prio, loc, lm))

    url_parts.sort(key=lambda row: (-row[0], row[1]))

    url_lines = []
    for prio, loc, lm in url_parts:
        prio_s = f"{prio:.10g}"
        chunk = f"  <url><loc>{loc}</loc><priority>{prio_s}</priority>"
        if lm:
            chunk += f"<lastmod>{lm}</lastmod>"
        chunk += "</url>"
        url_lines.append(chunk)

    xml_entries = "\n".join(url_lines)
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

    copy_share_image(dist_dir)

    sitemap = generate_sitemap(dist_dir)
    sitemap_path = dist_dir / "sitemap.xml"
    sitemap_path.write_text(sitemap, encoding="utf-8")
    url_count = sitemap.count("<url>")
    print(f"Created {sitemap_path} ({url_count} URLs)")

    robots = generate_robots()
    robots_path = dist_dir / "robots.txt"
    robots_path.write_text(robots, encoding="utf-8")
    print(f"Created {robots_path}")

    from rugby.redirects import generate_legacy_redirects

    redirect_count = generate_legacy_redirects(dist_dir)
    print(f"Legacy redirect stubs: {redirect_count} written or updated")


if __name__ == "__main__":
    main()
