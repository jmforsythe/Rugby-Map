"""Generate static redirect pages for legacy URLs reported in Search Console.

GitHub Pages serves ``404.html`` with HTTP 404 for missing paths. Writing a small
HTML file at each retired URL returns 200 and consolidates signals via
``rel=canonical`` plus ``noindex`` on the stub.
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path
from urllib.parse import unquote

from core.config import DIST_DIR, REPO_ROOT
from rugby.seo import absolute_url

GSC_404_PATHS_FILE = REPO_ROOT / "data" / "rugby" / "seo_gsc_404_paths.txt"
_REDIRECT_MARKER = 'data-rugby-redirect="1"'

_SEASON_RE = re.compile(r"^/(\d{4}-\d{4})(?:/|$)")
_MERIT_RE = re.compile(r"^/(\d{4}-\d{4})/merit/([^/]+)")
_TEAM_RE = re.compile(r"^/teams/([^/]+\.html)$", re.I)
_SEASON_DIR = re.compile(r"^\d{4}-\d{4}$")


def _normalize_site_path(path: str) -> str:
    p = unquote(path.strip())
    if not p.startswith("/"):
        p = "/" + p
    if p != "/" and p.endswith("/"):
        p = p.rstrip("/")
    return p


def resolve_not_found_redirect(pathname: str, *, is_prod: bool) -> str:
    """Next hop for a missing URL: parent directory index, else site home.

    GitHub Pages serves one static ``404.html`` for every missing path. The browser
    keeps the requested URL in ``location.pathname``, so we walk up one segment per
    404 until an existing ``index.html`` is reached.
    """
    default_home = "/" if is_prod else "/index.html"
    path = pathname or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if path in ("", "/"):
        return default_home
    slash = path.rfind("/")
    if slash <= 0:
        return default_home
    parent = path[:slash] or "/"
    if parent == "/":
        return default_home
    return f"{parent}/" if is_prod else f"{parent}/index.html"


def _redirect_stub_html(target_url: str, title: str) -> str:
    t = escape(target_url)
    title_esc = escape(title)
    return f"""<!DOCTYPE html>
<html lang="en" {_REDIRECT_MARKER}>
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="0;url={t}">
    <link rel="canonical" href="{t}">
    <meta name="robots" content="noindex,follow">
    <meta name="description" content="This page has moved.">
    <title>{title_esc}</title>
    <script>location.replace({json.dumps(target_url)});</script>
</head>
<body>
    <p>This page has moved. <a href="{t}">Continue</a>.</p>
</body>
</html>
"""


def _site_path_to_dist_file(dist_dir: Path, site_path: str) -> Path:
    rel = site_path.strip("/")
    if site_path.endswith(".html"):
        return dist_dir / rel
    return dist_dir / rel / "index.html"


def _load_team_filenames(dist_dir: Path) -> set[str]:
    teams = dist_dir / "teams"
    if not teams.is_dir():
        return set()
    return {p.name for p in teams.glob("*.html") if p.name != "index.html"}


def _resolve_team_filename(filename: str, existing: set[str]) -> str | None:
    if filename in existing:
        return None

    def norm(stem: str) -> str:
        return stem.lower().replace("_", "").replace("'", "")

    stem = filename[:-5] if filename.endswith(".html") else filename
    target_key = norm(stem)
    for name in existing:
        if norm(name[:-5]) == target_key:
            return name

    base = stem.split("_")[0]
    if not base:
        return None
    matches = [n for n in existing if n.startswith(base + "_") or n.startswith(base + ".")]
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_redirect_target(site_path: str, dist_dir: Path, team_files: set[str]) -> str:
    """Choose the canonical destination URL for a legacy *site_path*."""
    path = _normalize_site_path(site_path)

    if path in ("/merit", "/merit/"):
        return absolute_url("/")

    m = _MERIT_RE.match(path)
    if m:
        season, comp = m.group(1), m.group(2)
        all_tiers = dist_dir / season / "merit" / comp / "All_Tiers" / "index.html"
        if all_tiers.is_file():
            return absolute_url(f"/{season}/merit/{comp}/All_Tiers/")
        return absolute_url(f"/{season}/")

    m = _TEAM_RE.match(path)
    if m:
        alt = _resolve_team_filename(m.group(1), team_files)
        if alt:
            return absolute_url(f"/teams/{alt}")
        return absolute_url("/teams/")

    m = _SEASON_RE.match(path)
    if m and path.count("/") >= 2:
        return absolute_url(f"/{m.group(1)}/")

    if path.startswith("/teams"):
        return absolute_url("/teams/")

    if path.startswith("/custom-map"):
        return absolute_url("/custom-map/")

    return absolute_url("/")


def discover_legacy_tier_html_redirects(dist_dir: Path) -> list[tuple[str, str]]:
    """``/season/Tier.html`` → ``/season/Tier/`` when the directory map exists."""
    pairs: list[tuple[str, str]] = []
    for season_dir in sorted(dist_dir.iterdir()):
        if not season_dir.is_dir() or not _SEASON_DIR.fullmatch(season_dir.name):
            continue
        for html in sorted(season_dir.glob("*.html")):
            if html.name == "index.html":
                continue
            tier_dir = season_dir / html.stem / "index.html"
            if tier_dir.is_file():
                pairs.append(
                    (
                        f"/{season_dir.name}/{html.name}",
                        f"/{season_dir.name}/{html.stem}/",
                    )
                )
    return pairs


def _is_redirect_stub(path: Path) -> bool:
    try:
        return _REDIRECT_MARKER in path.read_text(encoding="utf-8", errors="replace")[:800]
    except OSError:
        return False


def _collect_paths(dist_dir: Path) -> set[str]:
    paths: set[str] = set()
    if GSC_404_PATHS_FILE.is_file():
        for line in GSC_404_PATHS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                paths.add(_normalize_site_path(line))
    for src, _dest in discover_legacy_tier_html_redirects(dist_dir):
        paths.add(_normalize_site_path(src))
    return paths


def generate_legacy_redirects(dist_dir: Path | None = None) -> int:
    """Write redirect stub HTML for legacy paths; return number of files written."""
    root = dist_dir or DIST_DIR
    if not root.is_dir():
        return 0

    team_files = _load_team_filenames(root)
    paths = _collect_paths(root)
    written = 0

    for site_path in sorted(paths):
        out = _site_path_to_dist_file(root, site_path)
        if out.is_file() and _is_redirect_stub(out):
            continue
        if out.is_file():
            continue

        target = resolve_redirect_target(site_path, root, team_files)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            _redirect_stub_html(target, "Redirecting… | rugbyunionmap.uk"),
            encoding="utf-8",
        )
        written += 1

    for src, dest in discover_legacy_tier_html_redirects(root):
        site_path = _normalize_site_path(src)
        out = _site_path_to_dist_file(root, site_path)
        if out.is_file() and _is_redirect_stub(out):
            continue
        target = absolute_url(dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            _redirect_stub_html(target, "Redirecting… | rugbyunionmap.uk"),
            encoding="utf-8",
        )
        written += 1

    return written


def main() -> None:
    count = generate_legacy_redirects()
    print(f"Wrote {count} legacy redirect stub(s) under {DIST_DIR}")


if __name__ == "__main__":
    main()
