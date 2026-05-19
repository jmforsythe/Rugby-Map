"""Generate a static HTML carousel to skim pyramid SVG/PNG renders under ``dist/<season>/``.

After running ``python -m rugby.pyramid_image`` (with optional ``--png``) for several
seasons, rebuild the viewer and open the gallery HTML in a browser:

- Arrow keys (← / →) or on-screen buttons change the slide.
- ``Home`` / ``End`` jump to newest / oldest.
- ``-`` / ``+`` (or ``=``) zoom the image; ``0`` resets to 100%.
- Hold **Ctrl** and scroll the stage to zoom.
- Prefers ``.png`` over ``.svg`` when both exist.
  SVG slides use ``<object>`` (not ``<img>``) because browsers omit ``foreignObject``/crest markup when SVG is rasterised via ``img``.

Run::

    python -m rugby.analysis.pyramid_gallery
    python -m rugby.analysis.pyramid_gallery --womens
    python -m rugby.analysis.pyramid_gallery --all-leagues
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from core.config import DIST_DIR, REPO_ROOT

_SEASON_DIR_RE = re.compile(r"^[12]\d{3}-[12]\d{3}$")

STEM_NATIONAL = "pyramid"
STEM_ALL_LEAGUES = "pyramid_All_Leagues"
STEM_WOMENS = "pyramid_womens"

DEFAULT_OUTPUT_BY_STEM: dict[str, str] = {
    STEM_NATIONAL: "pyramid-gallery.html",
    STEM_ALL_LEAGUES: "pyramid-all-leagues-gallery.html",
}


def _season_sort_key(folder_name: str) -> tuple[int, int]:
    a, _, b = folder_name.partition("-")
    try:
        return int(a), int(b)
    except ValueError:
        return 0, 0


def _pick_image(season_dir: Path, stem: str) -> str | None:
    png = season_dir / f"{stem}.png"
    if png.is_file():
        return f"{season_dir.name}/{stem}.png"
    svg = season_dir / f"{stem}.svg"
    if svg.is_file():
        return f"{season_dir.name}/{stem}.svg"
    return None


def collect_slides(
    dist_root: Path,
    *,
    stem: str = STEM_NATIONAL,
    slide_label: str = "Men",
    include_womens: bool = False,
) -> list[dict[str, str]]:
    """Slide dicts: ``label``, ``href`` (relative to ``dist_root``)."""
    if not dist_root.is_dir():
        return []

    seasons = sorted(
        (d.name for d in dist_root.iterdir() if d.is_dir() and _SEASON_DIR_RE.match(d.name)),
        key=_season_sort_key,
        reverse=True,
    )
    slides: list[dict[str, str]] = []
    for season in seasons:
        sdir = dist_root / season
        href = _pick_image(sdir, stem)
        if href is not None:
            slides.append({"label": f"{slide_label} · {season}", "href": href})
        if include_womens:
            href_w = _pick_image(sdir, STEM_WOMENS)
            if href_w is not None:
                slides.append({"label": f"Women · {season}", "href": href_w})
    return slides


def build_html(slides: list[dict[str, str]], *, page_title: str = "Pyramid gallery") -> str:
    data_json = json.dumps(slides)
    # json.dumps yields ASCII-safe escapes; safe to embed verbatim in JS.
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{page_title}</title>
  <style>
    :root {{
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: #1a1b1e;
      color: #e8e8e8;
    }}
    body {{
      margin: 0;
      height: 100vh;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      align-items: stretch;
    }}
    header {{
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      z-index: 100;
      padding: 0.75rem 1rem;
      box-sizing: border-box;
      border-bottom: 1px solid #333;
      background: #1a1b1e;
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem 1rem;
      align-items: center;
      justify-content: center;
    }}
    header strong {{ font-size: 1rem; }}
    header span {{ opacity: 0.85; font-size: 0.9rem; }}
    .controls {{
      display: flex;
      gap: 0.5rem;
      align-items: center;
    }}
    button {{
      background: #2d2f36;
      color: #eee;
      border: 1px solid #444;
      border-radius: 6px;
      padding: 0.35rem 0.85rem;
      cursor: pointer;
      font-size: 0.95rem;
    }}
    button:hover {{ background: #383b44; }}
    button:focus-visible {{ outline: 2px solid #6ea8fe; outline-offset: 2px; }}
    #stage {{
      flex: 1;
      min-height: 0;
      width: 100%;
      margin-top: var(--gallery-header-h, 4.5rem);
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 0.75rem;
      box-sizing: border-box;
      overflow: auto;
      background: #121316;
    }}
    #frame {{
      display: block;
      margin: 0 auto;
      width: 100%;
      text-align: center;
    }}
    #stage img,
    object.slide-svg {{
      max-width: 100%;
      height: auto;
      object-fit: contain;
      vertical-align: top;
      background: #fff;
      border-radius: 4px;
      box-shadow: 0 4px 24px rgba(0,0,0,0.45);
    }}
    object.slide-svg {{
      display: block;
      margin: 0 auto;
      min-height: min(120px, 30vh);
      /* Let wheel events reach the page for Ctrl+scroll zoom (nested SVG doc otherwise captures them). */
      pointer-events: none;
    }}
    footer {{
      flex-shrink: 0;
      padding: 0.5rem;
      font-size: 0.8rem;
      opacity: 0.65;
      text-align: center;
    }}
  </style>
</head>
<body>
  <header>
    <strong id="cap">—</strong>
    <span id="hint">← → Home End · - + 0 zoom · Ctrl wheel</span>
    <div class="controls">
      <button type="button" id="prev" aria-label="Previous">← Prev</button>
      <button type="button" id="next" aria-label="Next">Next →</button>
    </div>
    <div class="controls">
      <button type="button" id="zoomOut" aria-label="Zoom out">Zoom −</button>
      <button type="button" id="zoomReset" aria-label="Zoom 100%">100%</button>
      <button type="button" id="zoomIn" aria-label="Zoom in">Zoom +</button>
      <span id="zoomPct" style="opacity:.9;min-width:3.5rem">100%</span>
    </div>
  </header>
  <div id="stage"><div id="frame"><img id="view" alt="Pyramid" decoding="async"/><object id="viewSvg" class="slide-svg" type="image/svg+xml" data="" aria-label="Pyramid (SVG)" style="display:none"></object></div></div>
  <footer>Open from <code>dist/</code> (paths are relative). SVG slides use <code>&lt;object&gt;</code> so crest tiles render (<code>&lt;img&gt;</code> skips <code>foreignObject</code>).</footer>
  <script>
    const slides = {data_json};
    let idx = 0;
    const ZOOM_MIN = 0.2;
    const ZOOM_MAX = 3;
    const ZOOM_STEP = 0.1;
    let zoom = 1;

    function clamp(n) {{
      if (!slides.length) return 0;
      if (n < 0) return 0;
      if (n >= slides.length) return slides.length - 1;
      return n;
    }}

    function clampZoom(z) {{
      if (Number.isFinite(z)) {{
        return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z * 1000) / 1000));
      }}
      return 1;
    }}

    function slideUsesSvg(path) {{
      return path.toLowerCase().endsWith(".svg");
    }}

    function applyZoom() {{
      const img = document.getElementById("view");
      const obj = document.getElementById("viewSvg");
      const pct = Math.round(100 * zoom);
      img.style.maxWidth = pct + "%";
      obj.style.maxWidth = pct + "%";
      document.getElementById("zoomPct").textContent = pct + "%";
    }}

    function bumpZoom(delta) {{
      zoom = clampZoom(zoom + delta);
      applyZoom();
    }}

    function show(i) {{
      idx = clamp(i);
      const cap = document.getElementById("cap");
      const img = document.getElementById("view");
      const obj = document.getElementById("viewSvg");
      if (!slides.length) {{
        cap.textContent = "No pyramid images — run pyramid_image first.";
        img.style.display = "none";
        img.removeAttribute("src");
        obj.style.display = "none";
        obj.removeAttribute("data");
        return;
      }}
      const s = slides[idx];
      cap.textContent = s.label + " (" + (idx + 1) + " / " + slides.length + ")";
      if (slideUsesSvg(s.href)) {{
        img.style.display = "none";
        img.removeAttribute("src");
        obj.style.display = "block";
        obj.data = s.href + "#g=" + String(idx);
        applyZoom();
      }} else {{
        obj.style.display = "none";
        obj.removeAttribute("data");
        img.style.display = "block";
        img.src = s.href;
      }}
    }}

    document.getElementById("prev").addEventListener("click", () => show(idx - 1));
    document.getElementById("next").addEventListener("click", () => show(idx + 1));

    document.getElementById("zoomOut").addEventListener("click", () => bumpZoom(-ZOOM_STEP));
    document.getElementById("zoomIn").addEventListener("click", () => bumpZoom(ZOOM_STEP));
    document.getElementById("zoomReset").addEventListener("click", () => {{
      zoom = 1;
      applyZoom();
    }});

    const imgEl = document.getElementById("view");
    const objEl = document.getElementById("viewSvg");
    imgEl.addEventListener("load", () => applyZoom());
    objEl.addEventListener("load", () => applyZoom());

    function syncHeaderOffset() {{
      const hdr = document.querySelector("header");
      if (hdr) {{
        document.documentElement.style.setProperty(
          "--gallery-header-h",
          hdr.offsetHeight + "px"
        );
      }}
    }}
    syncHeaderOffset();
    window.addEventListener("resize", syncHeaderOffset);

    function onWheelZoom(e) {{
      if (!e.ctrlKey) return;
      e.preventDefault();
      const dir = e.deltaY > 0 ? -1 : 1;
      const scale = e.deltaMode === 1 ? 3 : (e.deltaMode === 2 ? 24 : 1);
      bumpZoom(dir * ZOOM_STEP * scale);
    }}
    window.addEventListener("wheel", onWheelZoom, {{ passive: false, capture: true }});

    window.addEventListener("keydown", (e) => {{
      if (e.key === "ArrowLeft") {{ e.preventDefault(); show(idx - 1); }}
      else if (e.key === "ArrowRight") {{ e.preventDefault(); show(idx + 1); }}
      else if (e.key === "Home") {{ e.preventDefault(); show(0); }}
      else if (e.key === "End") {{ e.preventDefault(); show(slides.length - 1); }}
      else if (
        e.key === "+" || e.key === "=" || e.code === "NumpadAdd"
      ) {{ e.preventDefault(); bumpZoom(ZOOM_STEP); }}
      else if (
        e.key === "-" || e.key === "_" || e.code === "NumpadSubtract"
      ) {{ e.preventDefault(); bumpZoom(-ZOOM_STEP); }}
      else if (e.key === "0") {{ e.preventDefault(); zoom = 1; applyZoom(); }}
    }});

    show(0);
    applyZoom();
  </script>
</body>
</html>
"""


def _validate_dist_path(path: Path) -> Path:
    resolved = path.resolve()
    root = REPO_ROOT.resolve()
    if root not in resolved.parents and resolved != root:
        raise argparse.ArgumentTypeError(
            f"--dist must be inside the repository ({root}); got {resolved}"
        )
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write an HTML carousel to flip through pyramid renders under dist/<season>/."
    )
    parser.add_argument(
        "--dist",
        type=_validate_dist_path,
        default=DIST_DIR,
        help=f"Site output root (default: {DIST_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="HTML output path (default depends on --all-leagues)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--all-leagues",
        action="store_true",
        help=(
            "Use pyramid_All_Leagues.{png,svg} (national + merit) per season; "
            f"default output {DEFAULT_OUTPUT_BY_STEM[STEM_ALL_LEAGUES]!r}."
        ),
    )
    parser.add_argument(
        "--womens",
        action="store_true",
        help=(
            "With national pyramid (default): also include pyramid_womens slides. "
            "Ignored with --all-leagues."
        ),
    )
    args = parser.parse_args()
    dist_root: Path = args.dist

    if args.all_leagues:
        stem = STEM_ALL_LEAGUES
        slide_label = "All leagues"
        page_title = "Pyramid — all leagues gallery"
        include_womens = False
    else:
        stem = STEM_NATIONAL
        slide_label = "Men"
        page_title = "Pyramid gallery"
        include_womens = args.womens

    default_out_name = DEFAULT_OUTPUT_BY_STEM[stem]
    out: Path = args.output if args.output is not None else dist_root / default_out_name

    slides = collect_slides(
        dist_root,
        stem=stem,
        slide_label=slide_label,
        include_womens=include_womens,
    )
    if not slides:
        print(
            f"No pyramid images found under {dist_root} (expected "
            f"*/{stem}.png or {stem}.svg per season).",
            file=sys.stderr,
        )
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(slides, page_title=page_title), encoding="utf-8")
    print(f"Wrote {len(slides)} slides -> {out}")
    print("Open that file in a browser; arrow keys flip slides; use - / + / 0 or Zoom buttons.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
