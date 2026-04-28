"""Normalize RFU fixtures URL strings in scraped JSON to match historic on-disk layout.

Historical convention (through 2024-2025):
- ``league_url``: ``search-results?competition=C&division=D&season=S#tables``
- Team ``url`` outside ``merit/``: ``search-results?team=T&season=S``
- Team ``url`` under ``merit/``: ``search-results?team=T`` (no season parameter)

Later scrapes emitted full verbose team links including competition and division.
This module rewrote those back to the short forms for a season tree.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
from pathlib import Path

from rugby import DATA_DIR

_SEARCH_PATH = "/fixtures-and-results/search-results"


def json_files_under(season_root: Path) -> list[Path]:
    if not season_root.is_dir():
        return []
    return sorted(p for p in season_root.rglob("*.json") if not p.name.startswith("_"))


def path_is_under_merit(league_json: Path, season_root: Path) -> bool:
    try:
        rel = league_json.relative_to(season_root)
    except ValueError:
        return False
    return len(rel.parts) > 1 and rel.parts[0] == "merit"


def normalize_league_url(url: str) -> str:
    """``competition&division&season`` then fragment ``tables``."""
    if not url or _SEARCH_PATH not in url:
        return url
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    competition = (qs.get("competition") or [None])[0]
    division = (qs.get("division") or [None])[0]
    season_q = (qs.get("season") or [None])[0]
    if not competition or not division or not season_q:
        return url
    query = urllib.parse.urlencode(
        [("competition", competition), ("division", division), ("season", season_q)]
    )
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "www.englandrugby.com"
    path = parsed.path if parsed.path else _SEARCH_PATH
    return urllib.parse.urlunparse((scheme, netloc, path, "", query, "tables"))


def normalize_team_url(url: str, season: str, *, merit_league_file: bool) -> str:
    """Short RFU ``search-results`` team URLs matching older committed data."""
    if not url:
        return url
    if url.startswith("/"):
        url = urllib.parse.urljoin("https://www.englandrugby.com/", url)
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc and "englandrugby.com" not in parsed.netloc:
        return url
    path = parsed.path or ""
    if "/fixtures-and-results/search-results" not in path and not path.endswith("search-results"):
        return url

    qs = urllib.parse.parse_qs(parsed.query)
    team_vals = qs.get("team")
    if not team_vals or not team_vals[0]:
        return url
    team_id = team_vals[0]

    if merit_league_file:
        pairs = [("team", team_id)]
    else:
        season_val = (qs.get("season") or [season])[0]
        pairs = [("team", team_id), ("season", season_val)]

    query = urllib.parse.urlencode(pairs)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "www.englandrugby.com"
    return urllib.parse.urlunparse((scheme, netloc, _SEARCH_PATH, "", query, ""))


def transform_record(data: dict[str, object], season: str, *, merit_league_file: bool) -> int:
    changed = 0
    league_url = data.get("league_url")
    if isinstance(league_url, str):
        new_lu = normalize_league_url(league_url)
        if new_lu != league_url:
            data["league_url"] = new_lu
            changed += 1

    teams_raw = data.get("teams")
    if isinstance(teams_raw, list):
        for team_o in teams_raw:
            if not isinstance(team_o, dict):
                continue
            u = team_o.get("url")
            if isinstance(u, str):
                nu = normalize_team_url(u, season, merit_league_file=merit_league_file)
                if nu != u:
                    team_o["url"] = nu
                    changed += 1

    return changed


def process_directory(
    label: str,
    season_root: Path,
    season: str,
    *,
    write: bool,
) -> tuple[int, int]:
    files = json_files_under(season_root)
    touched_files = 0
    total_changes = 0
    for path in files:
        merit = path_is_under_merit(path, season_root)
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            continue
        diff = transform_record(payload, season, merit_league_file=merit)
        if diff:
            touched_files += 1
            total_changes += diff
            if write:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                print(f"  [{label}] wrote {path.relative_to(season_root)} ({diff} field(s))")
    return touched_files, total_changes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize RFU search-results URLs in league/address/geocode JSON.",
    )
    parser.add_argument(
        "--season",
        required=True,
        help="Season directory (e.g. 2025-2026)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing files.",
    )
    args = parser.parse_args()
    season: str = args.season

    dirs: list[tuple[str, Path]] = [
        ("league_data", DATA_DIR / "league_data" / season),
        ("team_addresses", DATA_DIR / "team_addresses" / season),
        ("geocoded_teams", DATA_DIR / "geocoded_teams" / season),
    ]

    write = not args.dry_run
    grand_files = 0
    grand_changes = 0

    for label, root in dirs:
        if not root.is_dir():
            print(f"[{label}] skip (missing {root})")
            continue
        print(f"[{label}] scanning {root} ...")
        tf, tc = process_directory(label, root, season, write=write)
        grand_files += tf
        grand_changes += tc
        print(f"  -> {tf} file(s) updated, {tc} URL field change(s)")

    if args.dry_run:
        print(
            f"Dry-run complete ({grand_files} file(s), {grand_changes} change(s)). "
            "Re-run without --dry-run to write."
        )
    else:
        print(f"Done. Updated {grand_files} file(s) ({grand_changes} URL field replacements).")


if __name__ == "__main__":
    main()
