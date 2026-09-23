#!/usr/bin/env python3
"""Merge a Search Console "Not found (404)" export into ``seo_gsc_404_paths.txt``.

``rugby.redirects`` turns every path in that file into a 200-status stub carrying
``rel=canonical`` + ``noindex``, which is how retired URLs hand their signals to
the page that replaced them. Paths only get there by hand today, so exports drift
out of sync with what Google is actually reporting.

Accepts the CSV Search Console produces (a ``url``/``page`` column, or a single
unlabelled column) as well as a plain newline-separated list. Rows pointing at
another host are skipped, as are paths the current ``dist/`` build already serves
(real page or auto-generated stub) -- so run it after a full production build.

Usage::

    python scripts/maintenance/merge_gsc_404_paths.py ~/Downloads/table.csv
    python scripts/maintenance/merge_gsc_404_paths.py table.csv --dry-run
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from core.config import DIST_DIR  # noqa: E402
from rugby.redirects import (  # noqa: E402
    GSC_404_PATHS_FILE,
    _normalize_site_path,
    _site_path_to_dist_file,
)
from rugby.seo import BASE_URL  # noqa: E402

_SITE_HOST = urlparse(BASE_URL).netloc
_URL_COLUMNS = ("url", "page", "urls", "pages", "address", "location")


def extract_paths(text: str) -> tuple[list[str], int]:
    """Site-relative paths from *text*; also returns the off-host row count."""
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return [], 0

    column = 0
    header = [cell.strip().strip("\ufeff").lower() for cell in rows[0]]
    if any(cell in _URL_COLUMNS for cell in header):
        column = next(i for i, cell in enumerate(header) if cell in _URL_COLUMNS)
        rows = rows[1:]

    paths: list[str] = []
    off_host = 0
    for row in rows:
        if len(row) <= column:
            continue
        raw = row[column].strip().strip('"')
        if not raw or raw.startswith("#"):
            continue
        parsed = urlparse(raw)
        if parsed.netloc and parsed.netloc != _SITE_HOST:
            off_host += 1
            continue
        path = unquote(parsed.path) if parsed.scheme or parsed.netloc else unquote(raw)
        if not path:
            continue
        paths.append(_normalize_site_path(path))
    return paths, off_host


def listed_form(site_path: str) -> str:
    """How *site_path* is written to the list: directory URLs keep their trailing slash."""
    if site_path == "/" or site_path.endswith(".html"):
        return site_path
    return site_path + "/"


def already_served(site_path: str) -> bool:
    """True when *site_path* resolves in dist/, as a real page or an auto-generated stub.

    Stubs for renamed teams, ``.html`` team URLs etc. are rediscovered on every
    build, so listing them here would only add noise. Run after a full build.
    """
    return _site_path_to_dist_file(DIST_DIR, site_path).is_file()


def read_existing(path: Path) -> tuple[list[str], set[str]]:
    """Existing file lines and the normalized paths they represent."""
    if not path.is_file():
        return [], set()
    lines = path.read_text(encoding="utf-8").splitlines()
    known = {
        _normalize_site_path(line)
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    }
    return lines, known


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="Search Console CSV or newline-separated URLs")
    parser.add_argument(
        "--output",
        type=Path,
        default=GSC_404_PATHS_FILE,
        help=f"Path list to update (default: {GSC_404_PATHS_FILE})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would change, write nothing"
    )
    parser.add_argument(
        "--keep-served",
        action="store_true",
        help="Also add paths that already resolve in dist/",
    )
    args = parser.parse_args()

    export = args.export.expanduser().resolve()
    if not export.is_file():
        parser.error(f"No such file: {export}")

    paths, off_host = extract_paths(export.read_text(encoding="utf-8", errors="replace"))
    if not paths:
        print("No URLs found in the export -- is it the Search Console table CSV?")
        return

    lines, known = read_existing(args.output)

    new: list[str] = []
    skipped_served = 0
    for path in paths:
        if path in known:
            continue
        if not args.keep_served and already_served(path):
            skipped_served += 1
            continue
        known.add(path)
        new.append(listed_form(path))

    print(f"Export rows with a usable path : {len(paths)}")
    if off_host:
        print(f"Skipped (different host)       : {off_host}")
    print(f"Already listed                 : {len(paths) - len(new) - skipped_served}")
    if skipped_served:
        print(f"Skipped (already served)       : {skipped_served}")
    print(f"New paths to add               : {len(new)}")
    for path in sorted(new)[:20]:
        print(f"  {path}")
    if len(new) > 20:
        print(f"  ... and {len(new) - 20} more")

    if not new or args.dry_run:
        if args.dry_run and new:
            print("\nDry run -- nothing written.")
        return

    body = sorted(line for line in lines if line.strip() and not line.strip().startswith("#"))
    comments = [line for line in lines if line.strip().startswith("#")]
    merged = comments + sorted({*body, *new})
    args.output.write_text("\n".join(merged) + "\n", encoding="utf-8")
    print(f"\nWrote {args.output} ({len(merged)} paths)")
    print("Next: python -m rugby.seo  (regenerates redirect stubs), then commit.")


if __name__ == "__main__":
    main()
