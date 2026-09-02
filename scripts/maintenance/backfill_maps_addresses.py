#!/usr/bin/env python3
"""Replace space-separated RFU page-text addresses with comma-separated Maps URLs.

Reads ``data/rugby/club_addresses.json``, finds entries whose address string
contains no commas (the ``c036-club-details-address`` format), re-scrapes each
distinct address once via ``rugby.addresses.fetch_club_address(prefer_maps=True)``,
and writes the Google Maps ``query=`` string back to every club key sharing that
old address. Also updates ``data/caches/club_address_cache.json`` when present.

Usage:
    python scripts/backfill_maps_addresses.py --dry-run --limit 5
    python scripts/backfill_maps_addresses.py --limit 50
    python scripts/backfill_maps_addresses.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import EARLIEST_SEASON, AntiBotDetectedError, setup_logging  # noqa: E402
from core.config import CACHE_DIR  # noqa: E402
from rugby import DATA_DIR  # noqa: E402
from rugby.addresses import (  # noqa: E402
    fetch_club_address,
    is_space_separated_address,
    load_cache,
    save_cache,
    save_name_cache,
    team_name_to_club_name,
)
from rugby.clubs import resolve_club_name  # noqa: E402

CLUB_ADDRESSES_PATH = DATA_DIR / "club_addresses.json"
CLUB_ADDRESS_CACHE_PATH = CACHE_DIR / "club_address_cache.json"
LEAGUE_DATA_DIR = DATA_DIR / "league_data"


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def build_club_url_index(club_names: dict[str, str]) -> dict[str, tuple[str, str]]:
    """Canonical club name -> (team_name, team_url), newest season first."""
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
                canonical = club_names.get(derived, derived)
                if canonical not in index:
                    index[canonical] = (name, url)
    return index


def lookup_team_url(
    club_key: str,
    club_names: dict[str, str],
    url_index: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    """Resolve a ``club_addresses.json`` key to a team page via canonical name."""
    canonical = resolve_club_name(club_key, club_names)
    if canonical in url_index:
        return url_index[canonical]
    if club_key in url_index:
        return url_index[club_key]
    return None


def group_space_separated_addresses(
    club_addresses: dict[str, str | None],
) -> dict[str, list[str]]:
    """Old address (lower-cased) -> club keys still using that space-separated text."""
    groups: dict[str, list[str]] = defaultdict(list)
    for club, address in club_addresses.items():
        if is_space_separated_address(address):
            groups[address.strip().lower()].append(club)
    return dict(groups)


def _safe_print(text: str) -> None:
    """Print without failing on Windows consoles that lack Unicode glyphs."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def sync_address_cache(
    cache: dict[str, str | None],
    old_address: str,
    new_address: str,
) -> int:
    """Update cache entries whose value equals ``old_address``."""
    old_key = old_address.strip().lower()
    updated = 0
    for key, value in cache.items():
        if value and value.strip().lower() == old_key:
            cache[key] = new_address
            updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill comma-separated Google Maps addresses in club_addresses.json"
    )
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    parser.add_argument("--limit", type=int, default=0, help="Max distinct addresses to refetch")
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds between RFU requests, plus jitter (default: 2.0, same as rugby.addresses)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Max anti-bot retries per RFU page (default: 3, same as rugby.addresses)",
    )
    parser.add_argument(
        "--antibot-cooldown",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "After session-level anti-bot (all per-page retries exhausted), sleep "
            "SECONDS and resume instead of exiting (default: 0 = stop and re-run manually)"
        ),
    )
    args = parser.parse_args()

    setup_logging()
    load_cache()

    club_names_path = DATA_DIR / "club_names.json"
    club_names = _load_json(club_names_path)
    club_addresses = _load_json(CLUB_ADDRESSES_PATH)
    address_cache = _load_json(CLUB_ADDRESS_CACHE_PATH) if CLUB_ADDRESS_CACHE_PATH.exists() else {}

    url_index = build_club_url_index(club_names)
    initial_groups = group_space_separated_addresses(club_addresses)
    total_groups = len(initial_groups)

    print(f"Space-separated club keys: {sum(len(v) for v in initial_groups.values())}")
    print(f"Distinct space-separated address strings: {total_groups}")

    upgraded = 0
    unchanged = 0
    missing_url = 0
    fetch_failed = 0
    cache_updates = 0
    processed = 0
    stopped_early = False

    try:
        for _old_norm, club_keys in sorted(initial_groups.items()):
            if args.limit and processed >= args.limit:
                break

            representative = club_keys[0]
            old_address = club_addresses[representative]
            if not old_address or not is_space_separated_address(old_address):
                continue

            team_info = lookup_team_url(representative, club_names, url_index)
            if team_info is None:
                missing_url += 1
                processed += 1
                print(f"[{processed}/{total_groups}] [skip:no-url] {representative}")
                continue

            team_name, team_url = team_info
            canonical = resolve_club_name(representative, club_names)
            fetch_key = canonical if canonical in club_names.values() else representative
            processed += 1
            if fetch_key != representative:
                print(f"[{processed}/{total_groups}] {representative} (via {fetch_key})")
            else:
                print(f"[{processed}/{total_groups}] {representative}")

            while True:
                try:
                    new_address, _log_text = fetch_club_address(
                        fetch_key,
                        team_url,
                        delay_seconds=args.delay,
                        max_retries=args.retries,
                        prefer_maps=True,
                    )
                except AntiBotDetectedError as exc:
                    if exc.log_text:
                        _safe_print(exc.log_text)
                    else:
                        _safe_print(str(exc))
                    if args.antibot_cooldown > 0:
                        _safe_print(
                            f"Anti-bot detection — cooling down {args.antibot_cooldown:.0f}s "
                            "then resuming..."
                        )
                        time.sleep(args.antibot_cooldown)
                        continue
                    stopped_early = True
                    _safe_print("Anti-bot detection — stopping early.")
                    _safe_print(
                        "Wait several minutes and re-run the same command to resume "
                        f"({processed - 1}/{total_groups} distinct addresses processed)."
                    )
                    break

                if not new_address:
                    fetch_failed += 1
                    print(f"  [fail] {representative} ({team_name})")
                elif new_address.strip().lower() == old_address.strip().lower():
                    unchanged += 1
                    print("  [same]")
                elif not is_space_separated_address(new_address):
                    upgraded += 1
                    print(f"  [upgrade] ({len(club_keys)} club keys)")
                    print(f"    old: {old_address[:100]}{'...' if len(old_address) > 100 else ''}")
                    print(f"    new: {new_address[:100]}{'...' if len(new_address) > 100 else ''}")
                    if not args.dry_run:
                        for club in club_keys:
                            club_addresses[club] = new_address
                        cache_updates += sync_address_cache(address_cache, old_address, new_address)
                        _save_json(CLUB_ADDRESSES_PATH, club_addresses)
                        if address_cache:
                            _save_json(CLUB_ADDRESS_CACHE_PATH, address_cache)
                        save_cache()
                        save_name_cache()
                else:
                    unchanged += 1
                    print("  [maps-still-space]")
                break

            if stopped_early:
                break
    finally:
        if not args.dry_run:
            save_cache()
            save_name_cache()

    print()
    print(
        f"Upgraded: {upgraded}, unchanged: {unchanged}, "
        f"missing URL: {missing_url}, fetch failed: {fetch_failed}"
    )

    if args.dry_run:
        print("Dry run — no files written.")
        return 0

    if upgraded:
        print(f"Updated {CLUB_ADDRESSES_PATH} ({upgraded} distinct addresses)")
        if address_cache:
            print(f"Updated {CLUB_ADDRESS_CACHE_PATH} ({cache_updates} cache entries)")
    else:
        print("No upgrades — club_addresses.json unchanged.")

    return 1 if stopped_early else 0


if __name__ == "__main__":
    raise SystemExit(main())
