#!/usr/bin/env python3
"""Detect and fix derived club names wrongly grouped under one canonical club.

Some ``club_names.json`` entries collapse multiple real clubs into a single
canonical name because they share a ground (address/coordinate union in
``rugby/team_pages.py``'s ``build_club_index``). The symptom is one canonical
club with two or more derived names whose *first words* clearly differ (e.g.
``Huish Tigers`` and ``Rebels Rugby`` both mapping to ``Rebels Rugby``).

For every derived name in such a group this script checks the RFU team page
heading (``fetch_club_canonical_name`` / ``club_canonical_name_cache.json``).
When the scraped ground-truth name differs from the current assignment it
updates ``club_names.json`` and, when safe, seeds ``club_addresses.json`` /
``club_geocodes.json`` for the new canonical from the old group's data (same
shared ground).

Usage:
    python scripts/fix_address_merged_clubs.py --report-only
    python scripts/fix_address_merged_clubs.py --dry-run --limit 10
    python scripts/fix_address_merged_clubs.py --limit 20
    python scripts/fix_address_merged_clubs.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import EARLIEST_SEASON, AntiBotDetectedError  # noqa: E402
from rugby import DATA_DIR  # noqa: E402
from rugby.addresses import (  # noqa: E402
    fetch_club_canonical_name,
    load_name_cache,
    save_name_cache,
    team_name_to_club_name,
)

CLUB_NAMES_PATH = DATA_DIR / "club_names.json"
CLUB_ADDRESSES_PATH = DATA_DIR / "club_addresses.json"
CLUB_GEOCODES_PATH = DATA_DIR / "club_geocodes.json"
LEAGUE_DATA_DIR = DATA_DIR / "league_data"

_POSTCODE_RE = re.compile(r"[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}", re.IGNORECASE)


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def _first_word(name: str) -> str:
    parts = name.split()
    return parts[0].lower() if parts else ""


def looks_like_different_club(a: str, b: str) -> bool:
    """True when *a* and *b* plausibly name different clubs (not just variants)."""
    if _first_word(a) == _first_word(b):
        return False
    al, bl = a.lower(), b.lower()
    if al.startswith(bl) or bl.startswith(al):
        return False
    common = 0
    for ca, cb in zip(al, bl, strict=False):
        if ca == cb:
            common += 1
        else:
            break
    if common >= 4:
        return False
    return True


def group_by_canonical(club_names: dict[str, str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for derived, canonical in club_names.items():
        groups[canonical].append(derived)
    return dict(groups)


def find_suspicious_groups(club_names: dict[str, str]) -> list[tuple[str, list[str]]]:
    """Canonical clubs whose derived members include clearly different first words."""
    suspicious: list[tuple[str, list[str]]] = []
    for canonical, members in sorted(group_by_canonical(club_names).items()):
        if len(members) < 2:
            continue
        if any(
            looks_like_different_club(a, b) for i, a in enumerate(members) for b in members[i + 1 :]
        ):
            suspicious.append((canonical, sorted(members)))
    return suspicious


def members_needing_check(
    members: list[str],
    club_names: dict[str, str],
    name_cache: dict[str, str],
) -> list[str]:
    """Derived names in a suspicious group that lack a verified canonical assignment."""
    needing: list[str] = []
    for derived in members:
        assigned = club_names[derived]
        cached = name_cache.get(derived)
        if cached is None or cached != assigned:
            needing.append(derived)
    return needing


def build_derived_url_index() -> dict[str, tuple[str, str]]:
    """Derived club name -> (team_name, team_url), newest season first."""
    index: dict[str, tuple[str, str]] = {}
    season_dirs = sorted(
        (d for d in LEAGUE_DATA_DIR.iterdir() if d.is_dir() and d.name >= EARLIEST_SEASON),
        reverse=True,
    )
    for season_dir in season_dirs:
        for league_file in season_dir.rglob("*.json"):
            if league_file.name.startswith("_"):
                continue
            try:
                league = _load_json(league_file)
            except (json.JSONDecodeError, OSError):
                continue
            for team in league.get("teams", []):
                name = team.get("name", "")
                url = team.get("url")
                if not name or not url or name.startswith(("To be arranged", "TBC")):
                    continue
                derived = team_name_to_club_name(name)
                index.setdefault(derived, (name, url))
    return index


def _postcode(address: str | None) -> str | None:
    if not address:
        return None
    match = _POSTCODE_RE.search(address)
    return match.group(0).replace(" ", "").upper() if match else None


def assign_canonical(
    derived: str,
    new_canonical: str,
    old_canonical: str,
    *,
    club_names: dict[str, str],
    club_addresses: dict[str, str | None],
    club_geocodes: dict[str, dict],
) -> str | None:
    """Point ``derived`` at ``new_canonical`` and seed address/geocode when safe.

    Returns an error string when ``new_canonical`` already exists with a
    materially different address (different postcode) — same guard as
    ``scripts/backfill_cb_canonical_names.py``.
    """
    old_addr = club_addresses.get(old_canonical)
    new_addr = club_addresses.get(new_canonical)
    if (
        old_canonical in club_addresses
        and new_canonical in club_addresses
        and old_addr is not None
        and new_addr is not None
        and old_canonical != new_canonical
    ):
        old_pc, new_pc = _postcode(old_addr), _postcode(new_addr)
        if old_pc is None or new_pc is None or old_pc != new_pc:
            return (
                f"address collision for {derived!r}: existing {new_canonical!r} "
                f"({new_addr!r}) vs group {old_canonical!r} ({old_addr!r})"
            )

    club_names[derived] = new_canonical

    if new_canonical not in club_addresses and old_canonical in club_addresses:
        club_addresses[new_canonical] = club_addresses[old_canonical]
    if new_canonical not in club_geocodes and old_canonical in club_geocodes:
        club_geocodes[new_canonical] = club_geocodes[old_canonical]

    return None


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="List suspicious groups and members needing checks; no RFU requests",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape RFU pages and report fixes, but don't write any files",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N derived names needing a canonical check",
    )
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between RFU requests")
    parser.add_argument("--retries", type=int, default=2, help="Max retries per RFU request")
    parser.add_argument(
        "--antibot-cooldown",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="After anti-bot, sleep SECONDS and resume (default: 0 = stop)",
    )
    args = parser.parse_args()

    club_names = _load_json(CLUB_NAMES_PATH)
    club_addresses = _load_json(CLUB_ADDRESSES_PATH)
    club_geocodes = _load_json(CLUB_GEOCODES_PATH)
    load_name_cache()
    from rugby import addresses

    suspicious = find_suspicious_groups(club_names)
    print(f"{len(suspicious)} canonical club(s) with suspicious derived-name mixes")

    queue: list[tuple[str, str]] = []
    for canonical, members in suspicious:
        for derived in members_needing_check(members, club_names, addresses.club_name_cache):
            queue.append((canonical, derived))

    print(f"{len(queue)} derived name(s) need a canonical check")
    if args.report_only:
        for canonical, members in suspicious:
            needing = members_needing_check(members, club_names, addresses.club_name_cache)
            if not needing:
                continue
            print(f"\n{canonical}:")
            for derived in members:
                flag = " *" if derived in needing else ""
                cached = addresses.club_name_cache.get(derived, "(none)")
                print(f"  {derived!r} -> {club_names[derived]!r}  [cache: {cached}]{flag}")
        return 0

    if not queue:
        print("Nothing to do - every suspicious derived name is already cached and matches.")
        return 0

    print("Building derived name -> team URL index ...")
    url_index = build_derived_url_index()
    print(f"Indexed {len(url_index)} derived names\n")

    if args.limit is not None:
        queue = queue[: args.limit]

    fixed: list[tuple[str, str, str]] = []
    verified: list[str] = []
    skipped: dict[str, list[str]] = {
        "no_team_url": [],
        "scrape_failed": [],
        "collision": [],
    }

    for i, (group_canonical, derived) in enumerate(queue, 1):
        assigned = club_names[derived]
        print(f"[{i}/{len(queue)}] {derived!r} (group {group_canonical!r}, assigned {assigned!r})")

        entry = url_index.get(derived)
        if entry is None:
            _safe_print("  [skip] no team URL in league_data")
            skipped["no_team_url"].append(derived)
            continue

        _team_name, url = entry
        while True:
            try:
                scraped, log_text = fetch_club_canonical_name(
                    derived, url, delay_seconds=args.delay, max_retries=args.retries
                )
            except AntiBotDetectedError as exc:
                if exc.log_text:
                    _safe_print(exc.log_text)
                if args.antibot_cooldown > 0:
                    _safe_print(
                        f"Anti-bot detection — cooling down {args.antibot_cooldown:.0f}s "
                        "then resuming..."
                    )
                    time.sleep(args.antibot_cooldown)
                    continue
                _safe_print("\nAnti-bot detection triggered — stopping early.")
                if not args.dry_run:
                    save_name_cache()
                    _save_json(CLUB_NAMES_PATH, club_names)
                    _save_json(CLUB_ADDRESSES_PATH, club_addresses)
                    _save_json(CLUB_GEOCODES_PATH, club_geocodes)
                return 1
            break

        if not scraped:
            _safe_print("  [fail] scrape failed")
            skipped["scrape_failed"].append(derived)
            continue

        addresses.club_name_cache[derived] = scraped

        if scraped == assigned:
            _safe_print(f"  [ok] verified ({scraped!r})")
            verified.append(derived)
            if not args.dry_run:
                save_name_cache()
            continue

        error = assign_canonical(
            derived,
            scraped,
            group_canonical,
            club_names=club_names,
            club_addresses=club_addresses,
            club_geocodes=club_geocodes,
        )
        if error:
            _safe_print(f"  [skip] {error}")
            skipped["collision"].append(derived)
            continue

        _safe_print(f"  [fix] reassigned {assigned!r} -> {scraped!r}")
        fixed.append((derived, assigned, scraped))

        if not args.dry_run:
            save_name_cache()
            _save_json(CLUB_NAMES_PATH, club_names)
            _save_json(CLUB_ADDRESSES_PATH, club_addresses)
            _save_json(CLUB_GEOCODES_PATH, club_geocodes)

    print(f"\n{'='*80}")
    print(f"Verified (assignment matches RFU): {len(verified)}")
    print(f"Reassigned: {len(fixed)}")
    for derived, old, new in fixed:
        print(f"  {derived!r}: {old!r} -> {new!r}")
    for reason, names in skipped.items():
        if names:
            print(f"Skipped ({reason}): {len(names)}")
            for name in names:
                print(f"  {name!r}")
    print(f"{'='*80}")
    if args.dry_run:
        print("Dry run — no files written.")
    elif fixed or verified:
        print(f"Wrote cache, {CLUB_NAMES_PATH}, {CLUB_ADDRESSES_PATH}, {CLUB_GEOCODES_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
