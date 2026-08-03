#!/usr/bin/env python3
"""Download pinned JS/CSS vendor assets into dist/shared/vendor/ for self-hosting.

Run before building map pages (see .github/workflows/deploy.yml's prepare job)
so core.asset_utils.rewrite_cdn_urls_in_html has local files to rewrite CDN
URLs to. Safe to re-run: existing non-empty files are left untouched.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.asset_utils import CDN_TO_VENDOR  # noqa: E402
from core.config import DIST_DIR  # noqa: E402

VENDOR_DIR = DIST_DIR / "shared" / "vendor"


def fetch_url(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return
    print(f"Fetching {url} -> {dest}")
    # url/dest come only from the hardcoded CDN_TO_VENDOR mapping in this
    # repo, never from external/user input, so this is not an SSRF vector.
    req = urllib.request.Request(url, headers={"User-Agent": "rugby-mapping-vendor-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        dest.write_bytes(resp.read())


def main() -> None:
    # Every CDN_TO_VENDOR entry maps to a distinct local filename (one per
    # pinned version), so there is no dedup-by-name to worry about here.
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    for cdn_url, vendor_path in CDN_TO_VENDOR.items():
        fetch_url(cdn_url, VENDOR_DIR / Path(vendor_path).name)


if __name__ == "__main__":
    main()
