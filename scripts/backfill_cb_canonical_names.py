#!/usr/bin/env python3
"""Backfill club_names.json canonical names that don't resolve to an RFU
Constituent Body, by rescraping the RFU's own team-page heading.

Many club_names.json entries were never scraped and fall back to the derived
team name (e.g. "Doncaster" instead of "Doncaster RFC" -- see
rugby/clubs.py's ``resolve_club_name``). Since data/rugby/club_cb_mapping.json
(the RFU's "CB and Club Relationships" export) is keyed by each club's full
name including its "RFC"/"RUFC"/etc. suffix, an unscraped canonical name
often can't be matched to its CB even though the CB export has the club.

This script only ever changes a club's canonical name when:
  1. one of its teams has a live RFU page reporting a *different* name, AND
  2. that different name resolves to a CB the current canonical name doesn't.

Every other case (page unreachable, name unchanged, name still doesn't
resolve) is left untouched and reported. On a rename, this also renames the
matching key in club_addresses.json/club_geocodes.json to keep the
name/address/geocode join intact -- unless that new key already exists with
a materially different address, in which case the whole change is skipped
and flagged for manual review rather than guessed at.

Usage:
    python scripts/backfill_cb_canonical_names.py --limit 12
    python scripts/backfill_cb_canonical_names.py --dry-run
    python scripts/backfill_cb_canonical_names.py            # full remaining gap
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import EARLIEST_SEASON, AntiBotDetectedError  # noqa: E402
from rugby import DATA_DIR  # noqa: E402
from rugby.addresses import fetch_club_canonical_name, team_name_to_club_name  # noqa: E402
from rugby.constituent_bodies import get_constituent_body  # noqa: E402

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


def _postcode(address: str | None) -> str | None:
    if not address:
        return None
    match = _POSTCODE_RE.search(address)
    return match.group(0).replace(" ", "").upper() if match else None


def build_club_url_index(club_names: dict[str, str]) -> dict[str, tuple[str, str]]:
    """Canonical club name -> (team_name, team_url) for one representative team.

    Walks league_data/<season>/ newest-season-first so the URL picked is the
    most likely to still be live.
    """
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


def find_missing_clubs(club_names: dict[str, str]) -> list[str]:
    """Canonical club names that currently don't resolve to a CB, sorted."""
    canon_values = sorted(set(club_names.values()))
    return [v for v in canon_values if get_constituent_body(v) is None]


def rename_canonical(
    club_names: dict[str, str],
    club_addresses: dict[str, str | None],
    club_geocodes: dict[str, dict],
    old_name: str,
    new_name: str,
) -> str | None:
    """Rename ``old_name`` to ``new_name`` across all three club-keyed maps.

    Mutates the dicts in place. Returns an error string (and leaves the dicts
    unchanged) if an address/geocode collision looks like two different real
    clubs rather than the same club under two spellings.
    """
    old_addr = club_addresses.get(old_name)
    new_addr = club_addresses.get(new_name)
    both_have_addresses = old_name in club_addresses and new_name in club_addresses
    if both_have_addresses and old_addr is not None and new_addr is not None:
        old_pc, new_pc = _postcode(old_addr), _postcode(new_addr)
        if old_pc is None or new_pc is None or old_pc != new_pc:
            return (
                f"address collision: {old_name!r} ({old_addr!r}) vs "
                f"existing {new_name!r} ({new_addr!r}) -- can't confirm same club, skipping"
            )

    for key, value in club_names.items():
        if value == old_name:
            club_names[key] = new_name

    if old_name in club_addresses:
        if new_name not in club_addresses or club_addresses[new_name] is None:
            club_addresses[new_name] = club_addresses[old_name]
        del club_addresses[old_name]

    if old_name in club_geocodes:
        if new_name not in club_geocodes:
            club_geocodes[new_name] = club_geocodes[old_name]
        del club_geocodes[old_name]

    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, default=None, help="Process at most N missing clubs")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between RFU requests")
    parser.add_argument("--retries", type=int, default=2, help="Max retries per RFU request")
    parser.add_argument(
        "--dry-run", action="store_true", help="Scrape and report, but don't write any files"
    )
    args = parser.parse_args()

    club_names = _load_json(CLUB_NAMES_PATH)
    club_addresses = _load_json(CLUB_ADDRESSES_PATH)
    club_geocodes = _load_json(CLUB_GEOCODES_PATH)

    missing = find_missing_clubs(club_names)
    print(f"{len(missing)} canonical club(s) currently don't resolve to a CB")

    if args.limit is not None:
        missing = missing[: args.limit]
    print(f"Processing {len(missing)} club(s)\n")

    print("Building club -> team URL index from league_data/ ...")
    url_index = build_club_url_index(club_names)
    print(f"Indexed {len(url_index)} canonical clubs\n")

    fixed: list[tuple[str, str, str]] = []
    skipped: dict[str, list[str]] = {
        "no_team_url": [],
        "scrape_failed": [],
        "no_change": [],
        "still_no_cb_match": [],
        "collision": [],
    }

    for i, club in enumerate(missing, 1):
        print(f"[{i}/{len(missing)}] {club}")
        entry = url_index.get(club)
        if entry is None:
            print("  ✗ no team URL found in league_data")
            skipped["no_team_url"].append(club)
            continue

        team_name, url = entry
        try:
            scraped, log_text = fetch_club_canonical_name(
                club, url, delay_seconds=args.delay, max_retries=args.retries
            )
        except AntiBotDetectedError as e:
            print("\nAnti-bot detection triggered -- stopping early.")
            if e.log_text:
                print(e.log_text)
            break

        if not scraped:
            print("  ✗ scrape failed (no canonical name found)")
            skipped["scrape_failed"].append(club)
            continue

        if scraped == club:
            print("  = unchanged -- genuine CB export gap")
            skipped["no_change"].append(club)
            continue

        new_cb = get_constituent_body(scraped)
        if new_cb is None:
            print(f"  ✗ scraped {scraped!r}, still no CB match")
            skipped["still_no_cb_match"].append(club)
            continue

        error = rename_canonical(club_names, club_addresses, club_geocodes, club, scraped)
        if error:
            print(f"  ✗ {error}")
            skipped["collision"].append(club)
            continue

        print(f"  ✓ {club!r} -> {scraped!r} ({new_cb})")
        fixed.append((club, scraped, new_cb))

        if not args.dry_run:
            _save_json(CLUB_NAMES_PATH, club_names)
            _save_json(CLUB_ADDRESSES_PATH, club_addresses)
            _save_json(CLUB_GEOCODES_PATH, club_geocodes)

    print(f"\n{'='*80}")
    print(f"Fixed: {len(fixed)}")
    for old, new, cb in fixed:
        print(f"  {old!r} -> {new!r} ({cb})")
    for reason, clubs in skipped.items():
        if clubs:
            print(f"Skipped ({reason}): {len(clubs)}")
            for c in clubs:
                print(f"  {c!r}")
    print(f"{'='*80}")
    if args.dry_run:
        print("Dry run -- no files written.")
    else:
        print(f"Wrote {CLUB_NAMES_PATH}, {CLUB_ADDRESSES_PATH}, {CLUB_GEOCODES_PATH}")


if __name__ == "__main__":
    main()
