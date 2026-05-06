#!/usr/bin/env python3
"""Capture one screenshot per representative page type under ``dist/`` for human / AI review.

Uses headless Chrome, Chromium, or Edge (no extra Python packages). After a site build:

    python scripts/capture_review_screenshots.py

Override the browser: ``CHROME_PATH`` or ``--browser`` / ``--chrome-path``.

Output: ``screenshots/review/*.png`` plus ``manifest.json`` describing each capture.

By default, existing ``*.png`` files in the output folder are **removed** before
capturing so renames or a shorter shot list do not leave duplicate/ orphan files
(next to the new ``01_*.png`` …). Use ``--keep-existing`` to skip that cleanup.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

# -----------------------------------------------------------------------------
# Types
# -----------------------------------------------------------------------------


SeasonSlug = str


@dataclass(frozen=True)
class ShotPlan:
    """One screenshot to take if *rel_path* exists under dist."""

    shot_id: str
    description: str
    rel_path: str
    kind: Literal["static", "map"]
    order: int


# -----------------------------------------------------------------------------
# Discovery
# -----------------------------------------------------------------------------

_SEASON_DIR = re.compile(r"^\d{4}-\d{4}$")


def _discover_latest_season(dist: Path) -> SeasonSlug | None:
    seasons = [
        p.name
        for p in dist.iterdir()
        if p.is_dir() and not p.name.startswith(".") and _SEASON_DIR.fullmatch(p.name)
    ]
    return sorted(seasons, reverse=True)[0] if seasons else None


def _discover_pyramid_map(season_dir: Path) -> str | None:
    """Single-tier map under season (not merit / match_day).

    Prefers **Counties 1** then **Level 7** (mix of regional/county leagues) for
    review screenshots; falls back to other tiers if those maps are not built.

    Supports production dirs (``Counties_1/index.html``) and dev flat files
    (``Counties_1.html``).
    """
    preferred = (
        "Counties_1",
        "Level_7",
        "Regional_1",
        "National_League_1",
        "National_League_2",
        "Championship",
        "Premiership",
    )
    for name in preferred:
        idx = season_dir / name / "index.html"
        if idx.is_file():
            return f"{season_dir.name}/{name}/index.html"
        flat = season_dir / f"{name}.html"
        if flat.is_file():
            return f"{season_dir.name}/{name}.html"

    skip = {"match_day", "recovered_images", "merit", "shared"}
    for child in sorted(season_dir.iterdir()):
        if not child.is_dir() or child.name in skip:
            continue
        idx = child / "index.html"
        if idx.is_file():
            return f"{season_dir.name}/{child.name}/index.html"
    return None


def _discover_all_tiers(season_dir: Path) -> str | None:
    p = season_dir / "All_Tiers" / "index.html"
    if p.is_file():
        return f"{season_dir.name}/All_Tiers/index.html"
    flat = season_dir / "All_Tiers.html"
    if flat.is_file():
        return f"{season_dir.name}/All_Tiers.html"
    return None


def _discover_merit_map(season_dir: Path) -> str | None:
    merit = season_dir / "merit"
    if not merit.is_dir():
        return None
    for comp in sorted(merit.iterdir()):
        if not comp.is_dir():
            continue
        idx = comp / "All_Tiers" / "index.html"
        if idx.is_file():
            return f"{season_dir.name}/merit/{comp.name}/All_Tiers/index.html"
        flat = comp / "All_Tiers.html"
        if flat.is_file():
            return f"{season_dir.name}/merit/{comp.name}/All_Tiers.html"
    return None


def _discover_team_page(teams_dir: Path) -> str | None:
    for html in sorted(teams_dir.glob("*.html")):
        if html.name == "index.html":
            continue
        if html.is_file():
            return f"teams/{html.name}"
    return None


def build_shot_plans(dist: Path) -> tuple[list[ShotPlan], list[str]]:
    """Return planned shots and warning messages for missing prerequisites."""
    warnings: list[str] = []
    plans: list[ShotPlan] = []
    order = 0

    def add(
        shot_id: str,
        description: str,
        rel: str | None,
        kind: Literal["static", "map"],
    ) -> None:
        nonlocal order
        if not rel:
            return
        order += 1
        plans.append(
            ShotPlan(
                shot_id=shot_id,
                description=description,
                rel_path=rel,
                kind=kind,
                order=order,
            )
        )

    if not dist.is_dir():
        return [], [f"dist directory not found: {dist}"]

    # --- Static hub & lists ---
    if (dist / "index.html").is_file():
        add("home", "Site home / season hub", "index.html", "static")
    else:
        warnings.append("Skipping home: dist/index.html missing")

    season = _discover_latest_season(dist)
    if not season:
        warnings.append("No YYYY-YYYY season folder under dist; skipping season-scoped pages")
    else:
        season_dir = dist / season
        if (season_dir / "index.html").is_file():
            add("season_index", f"Season index ({season})", f"{season}/index.html", "static")
        else:
            warnings.append(f"Skipping season index: {season}/index.html missing")

        rel_match = f"{season}/match_day/index.html"
        if (dist / rel_match).is_file():
            add("match_day", f"Match day map ({season})", rel_match, "map")
        else:
            warnings.append(f"Skipping match day: {rel_match} missing")

        rel_pyramid = _discover_pyramid_map(season_dir)
        if rel_pyramid:
            add(
                "tier_map",
                f"Single-tier map — Counties 1 / Level 7 preferred ({season})",
                rel_pyramid,
                "map",
            )
        else:
            warnings.append(f"Skipping tier map: no pyramid map index under {season}/")

        rel_all = _discover_all_tiers(season_dir)
        if rel_all:
            add("all_tiers_map", f"All tiers multi-layer map ({season})", rel_all, "map")
        else:
            warnings.append(f"Skipping All_Tiers map: {season}/All_Tiers/index.html missing")

        rel_merit = _discover_merit_map(season_dir)
        if rel_merit:
            add("merit_map", f"Merit competition map ({season})", rel_merit, "map")
        else:
            warnings.append(f"Skipping merit map: no merit/*/All_Tiers/index.html under {season}/")

    teams_dir = dist / "teams"
    if (teams_dir / "index.html").is_file():
        add("teams_index", "Teams search index", "teams/index.html", "static")
    else:
        warnings.append("Skipping teams index: teams/index.html missing")

    rel_team = _discover_team_page(teams_dir) if teams_dir.is_dir() else None
    if rel_team:
        add("team_page", "Individual team profile", rel_team, "static")
    else:
        warnings.append("Skipping team page: no teams/*.html (except index) found")

    if (dist / "custom-map" / "index.html").is_file():
        add("custom_map", "Custom map builder", "custom-map/index.html", "map")
    else:
        warnings.append("Skipping custom map: custom-map/index.html missing")

    return plans, warnings


# -----------------------------------------------------------------------------
# Browser
# -----------------------------------------------------------------------------


def _candidate_chrome_executables() -> list[Path]:
    paths: list[Path] = []
    env_keys = ("CHROME_PATH", "GOOGLE_CHROME_BIN", "CHROMIUM_PATH")
    for key in env_keys:
        raw = os.environ.get(key, "").strip()
        if raw:
            paths.append(Path(raw))

    system = sys.platform
    if system == "win32":
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        paths.extend(
            [
                Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ]
        )
    elif system == "darwin":
        paths.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        paths.append(Path("/Applications/Chromium.app/Contents/MacOS/Chromium"))
    # Linux & fallback: PATH
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        bin_path = shutil.which(name)
        if bin_path:
            paths.append(Path(bin_path))

    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def resolve_browser(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        w = shutil.which(explicit)
        if w:
            return Path(w)
        raise FileNotFoundError(f"Browser not found: {explicit}")

    for candidate in _candidate_chrome_executables():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        if sys.platform == "win32" and candidate.suffix.lower() == ".exe" and candidate.is_file():
            return candidate

    raise FileNotFoundError(
        "Could not find Chrome, Chromium, or Edge. Set CHROME_PATH or use --browser."
    )


def file_uri(html_path: Path) -> str:
    return html_path.resolve().as_uri()


def virtual_time_budget_ms(kind: Literal["static", "map"], budget_map: int) -> int:
    return budget_map if kind == "map" else min(2000, budget_map)


# -----------------------------------------------------------------------------
# Capture
# -----------------------------------------------------------------------------


def capture_one(
    browser: Path,
    html_path: Path,
    png_path: Path,
    width: int,
    height: int,
    budget_ms: int,
) -> tuple[bool, str]:
    """Return (ok, stderr_or_message)."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    url = file_uri(html_path)
    cmd: list[str] = [
        str(browser),
        "--headless=new",
        f"--window-size={width},{height}",
        "--hide-scrollbars",
        f"--screenshot={png_path.resolve()}",
    ]
    if budget_ms > 0:
        cmd.append(f"--virtual-time-budget={budget_ms}")
    cmd.append(url)
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout after 120s"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()
        return False, tail[-500:] if tail else f"exit {r.returncode}"
    if not png_path.is_file() or png_path.stat().st_size < 100:
        return False, "screenshot file missing or too small"
    return True, ""


def clean_review_pngs(out_dir: Path) -> int:
    """Remove ``*.png`` in *out_dir*; return count deleted. Ignores missing dir."""
    if not out_dir.is_dir():
        return 0
    n = 0
    for p in out_dir.glob("*.png"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def _safe_dist_file(dist: Path, rel_posix: str) -> Path | None:
    """Resolve *rel_posix* under *dist*; return path only if it stays inside dist."""
    if not rel_posix or ".." in rel_posix.split("/"):
        return None
    candidate = (dist / rel_posix).resolve()
    try:
        candidate.relative_to(dist.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture representative screenshots from dist/ for layout review."
    )
    parser.add_argument(
        "--dist",
        type=Path,
        default=None,
        help="Site root (default: repo dist/)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory (default: repo screenshots/review/)",
    )
    parser.add_argument("--width", type=int, default=1280, help="Viewport width (default 1280)")
    parser.add_argument("--height", type=int, default=900, help="Viewport height (default 900)")
    parser.add_argument(
        "--map-budget-ms",
        type=int,
        default=12_000,
        help="virtual-time-budget for map pages (tile load hint); static pages use min of this and 2000",
    )
    parser.add_argument(
        "--browser",
        "--chrome-path",
        dest="browser",
        default=None,
        help="Path to Chrome/Chromium/Edge (default: CHROME_PATH or auto-detect)",
    )
    parser.add_argument(
        "--fail-on-skip",
        action="store_true",
        help="Exit 1 if any planned page file is missing under dist",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not delete existing *.png in the output folder before capture",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    dist: Path = args.dist if args.dist is not None else repo_root / "dist"
    out_dir: Path = args.out if args.out is not None else repo_root / "screenshots" / "review"

    try:
        browser = resolve_browser(args.browser)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    plans, warnings = build_shot_plans(dist)
    for w in warnings:
        print(f"Warning: {w}", file=sys.stderr)

    if not plans:
        print("No screenshots planned; build dist/ first.", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    if not args.keep_existing:
        removed = clean_review_pngs(out_dir)
        if removed:
            print(f"Cleared {removed} prior PNG(s) in {out_dir}", file=sys.stderr)

    manifest_shots: list[dict[str, Any]] = []
    failed = 0
    for plan in plans:
        src = _safe_dist_file(dist, plan.rel_path)
        png_name = f"{plan.order:02d}_{plan.shot_id}.png"
        png_path = out_dir / png_name
        entry: dict[str, Any] = {
            **asdict(plan),
            "png": png_name,
            "source_exists": src is not None,
        }
        if src is None:
            print(f"Skip (missing): {plan.shot_id} <- {plan.rel_path}", file=sys.stderr)
            entry["captured"] = False
            entry["error"] = "source file missing"
            failed += 1
            manifest_shots.append(entry)
            continue

        budget = virtual_time_budget_ms(plan.kind, args.map_budget_ms)
        ok, err = capture_one(
            browser,
            src,
            png_path,
            args.width,
            args.height,
            budget,
        )
        entry["captured"] = ok
        entry["file_uri"] = file_uri(src)
        if not ok:
            entry["error"] = err
            failed += 1
            print(f"Failed: {plan.shot_id}: {err}", file=sys.stderr)
        else:
            print(f"OK {png_name} <- {plan.rel_path}")
        manifest_shots.append(entry)

    manifest = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "dist": str(dist.resolve()),
        "browser": str(browser),
        "viewport": {"width": args.width, "height": args.height},
        "map_virtual_time_budget_ms": args.map_budget_ms,
        "shots": manifest_shots,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    if args.fail_on_skip and failed:
        return 1
    if failed == len(plans):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
