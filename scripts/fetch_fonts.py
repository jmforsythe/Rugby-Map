#!/usr/bin/env python3
"""Download the brand Google Fonts into dist/fonts/ for self-hosting.

Writes the woff2 files, ``fonts.css`` (the @font-face rules plus metric-matched
Arial fallback faces that stop text reflowing when the web font swaps in) and
``fonts.json`` (which files core.config.get_font_html preloads). The output is
committed, so this only needs re-running to change families/weights or pick up
upstream font updates.

Fallback metrics are measured against Arial from the local system fonts
(Windows: C:/Windows/Fonts, or pass --arial-dir).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from io import BytesIO
from pathlib import Path

from fontTools.ttLib import TTFont

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import DIST_DIR, FONT_STYLESHEET_URL  # noqa: E402

FONTS_DIR = DIST_DIR / "fonts"
SUBSETS = ("latin", "latin-ext")

# Google Fonts picks the format from the User-Agent: woff2 for modern
# browsers, TTF (which fontTools can read without brotli) for legacy ones.
WOFF2_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
TTF_UA = "Mozilla/4.0"

# Faces whose latin subset is preloaded: body text and headings render above
# the fold on every page.
PRELOAD = (("Barlow", 400), ("Oswald", 600))

# (family, weight range, Arial file, local() names, weight to measure)
FALLBACKS = (
    ("Barlow", "400 500", "arial.ttf", ("Arial", "ArialMT"), 400),
    ("Barlow", "600 700", "arialbd.ttf", ("Arial Bold", "Arial-BoldMT"), 600),
    ("Oswald", "500 700", "arialbd.ttf", ("Arial Bold", "Arial-BoldMT"), 600),
)

# Mixed-case sample weighted towards the text these fonts actually set.
SAMPLE = (
    "The quick brown fox jumps over the lazy dog. "
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz 0123456789 "
    "Counties 1 Surrey Regional 2 Midlands National League Rugby Football Club"
)

FACE_RE = re.compile(r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*\{([^}]*)\}")


def http_get(url: str, user_agent: str) -> bytes:
    # Only ever called with FONT_STYLESHEET_URL or URLs Google returns in it.
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return resp.read()


def descriptor(block: str, name: str) -> str:
    match = re.search(rf"{name}\s*:\s*([^;]+);", block)
    if not match:
        raise ValueError(f"@font-face block missing {name}: {block}")
    return match.group(1).strip()


def parse_faces(css: str) -> list[dict[str, str]]:
    faces = []
    for subset, block in FACE_RE.findall(css):
        faces.append(
            {
                "subset": subset,
                "family": descriptor(block, "font-family").strip("'\""),
                "weight": descriptor(block, "font-weight"),
                "url": re.search(r"url\(([^)]+)\)", descriptor(block, "src")).group(1),
                "unicode_range": descriptor(block, "unicode-range"),
            }
        )
    return faces


def ttf_url_by_face(css: str) -> dict[tuple[str, int], str]:
    """Legacy-UA CSS has no subset comments: one TTF per family/weight."""
    urls = {}
    for block in re.findall(r"@font-face\s*\{([^}]*)\}", css):
        family = descriptor(block, "font-family").strip("'\"")
        weight = int(descriptor(block, "font-weight"))
        urls[(family, weight)] = re.search(r"url\(([^)]+)\)", descriptor(block, "src")).group(1)
    return urls


def avg_width(font: TTFont) -> float:
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    upm = font["head"].unitsPerEm
    widths = [hmtx[cmap[ord(ch)]][0] for ch in SAMPLE if ord(ch) in cmap]
    return sum(widths) / len(widths) / upm


def fallback_rule(
    family: str, weights: str, locals_: tuple[str, ...], web: TTFont, arial: TTFont
) -> str:
    size_adjust = avg_width(web) / avg_width(arial)
    hhea = web["hhea"]
    upm = web["head"].unitsPerEm

    def pct(value: float) -> str:
        return f"{value / upm / size_adjust * 100:.2f}%"

    src = ",".join(f'local("{name}")' for name in locals_)
    return (
        f'@font-face{{font-family:"{family} Fallback";font-style:normal;font-weight:{weights};'
        f"src:{src};size-adjust:{size_adjust * 100:.2f}%;"
        f"ascent-override:{pct(hhea.ascent)};descent-override:{pct(abs(hhea.descent))};"
        f"line-gap-override:{pct(hhea.lineGap)}}}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arial-dir", type=Path, default=Path("C:/Windows/Fonts"))
    args = parser.parse_args()

    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    faces = [
        f
        for f in parse_faces(http_get(FONT_STYLESHEET_URL, WOFF2_UA).decode())
        if f["subset"] in SUBSETS
    ]

    # Variable fonts (Oswald) return the same file for every weight; merge
    # those into a single face with a weight range.
    merged: dict[str, dict] = {}
    for face in faces:
        entry = merged.setdefault(face["url"], {**face, "weights": []})
        entry["weights"].append(int(face["weight"]))

    rules: list[str] = []
    preload: list[str] = []
    keep: set[str] = set()
    for url, face in merged.items():
        version = re.search(r"/(v\d+)/", url)
        lo, hi = min(face["weights"]), max(face["weights"])
        weight_label = str(lo) if lo == hi else f"{lo}-{hi}"
        filename = (
            f"{face['family'].lower()}-{weight_label}-{face['subset']}"
            f"-{version.group(1) if version else 'v0'}.woff2"
        )
        keep.add(filename)
        dest = FONTS_DIR / filename
        if not dest.is_file():
            print(f"Fetching {url} -> {dest}")
            dest.write_bytes(http_get(url, WOFF2_UA))
        weight_desc = str(lo) if lo == hi else f"{lo} {hi}"
        rules.append(
            f'@font-face{{font-family:"{face["family"]}";font-style:normal;'
            f"font-weight:{weight_desc};font-display:swap;"
            f'src:url({filename}) format("woff2");unicode-range:{face["unicode_range"]}}}'
        )
        if face["subset"] == "latin" and any(
            family == face["family"] and lo <= weight <= hi for family, weight in PRELOAD
        ):
            preload.append(filename)

    ttf_urls = ttf_url_by_face(http_get(FONT_STYLESHEET_URL, TTF_UA).decode())
    for family, weights, arial_file, locals_, measure_weight in FALLBACKS:
        web = TTFont(BytesIO(http_get(ttf_urls[(family, measure_weight)], TTF_UA)))
        arial = TTFont(args.arial_dir / arial_file)
        rules.append(fallback_rule(family, weights, locals_, web, arial))

    for stale in FONTS_DIR.glob("*.woff2"):
        if stale.name not in keep:
            print(f"Removing stale {stale}")
            stale.unlink()

    (FONTS_DIR / "fonts.css").write_text("\n".join(rules) + "\n", encoding="utf-8")
    (FONTS_DIR / "fonts.json").write_text(
        json.dumps({"preload": sorted(preload)}, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(merged)} font files, preloading {preload}")


if __name__ == "__main__":
    main()
