"""
Script to backfill canonical club names for every club currently tracked.

Groups teams using the same ``team_name_to_club_name`` heuristic already used
for the address/coordinate caches, then scrapes one representative team page
per club for the ground-truth name in the "c036-club-details-heading"
element. Each club is scraped at most once, ever: results are cached in
``data/caches/club_canonical_name_cache.json`` and skipped on subsequent
runs (``rugby/addresses.py`` also piggybacks this same extraction onto its
own address-fetch requests, so the cache keeps growing over time without
extra requests).
"""

import argparse
import concurrent.futures
import json
from pathlib import Path

from core import AntiBotDetectedError, League, print_block, setup_logging
from core.config import CURRENT_SEASON
from rugby import DATA_DIR, addresses


def collect_club_representative_urls(
    season: str, league_files: list[Path] | None = None
) -> dict[str, str]:
    """Map derived club_name -> first-seen team URL for teams in a season.

    Args:
        season: Season to scan (e.g. "2026-2027").
        league_files: Restrict to these league JSON files instead of scanning
            every file under ``league_data/<season>`` (used by ``--league``).
    """
    league_dir = DATA_DIR / "league_data" / season
    representative_urls: dict[str, str] = {}

    if league_files is None:
        league_files = sorted(f for f in league_dir.rglob("*.json") if not f.name.startswith("_"))
    for league_file in league_files:
        with open(league_file, encoding="utf-8") as f:
            league_data: League = json.load(f)

        for team in league_data["teams"]:
            team_name = team["name"]
            if team_name.startswith("To be arranged") or team_name.startswith("TBC"):
                continue

            club_name = addresses.team_name_to_club_name(team_name)
            representative_urls.setdefault(club_name, team["url"])

    return representative_urls


def build_canonical_name_groups() -> dict[str, list[str]]:
    """Invert the canonical-name cache into canonical_name -> [derived club_names].

    Useful for spotting places where the ``team_name_to_club_name`` heuristic
    under- or over-splits a club (multiple derived names sharing one
    ground-truth RFU name, e.g. "Tamworth" and "Tamworth 3rds" both being
    "Tamworth RUFC").
    """
    groups: dict[str, list[str]] = {}
    for derived_name, canonical_name in sorted(addresses.club_name_cache.items()):
        groups.setdefault(canonical_name, []).append(derived_name)
    return groups


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill canonical club names from RFU team pages"
    )
    parser.add_argument(
        "--season",
        type=str,
        default=CURRENT_SEASON,
        help=f"Season to process (e.g., 2024-2025, 2025-2026). Default: {CURRENT_SEASON}",
    )
    parser.add_argument(
        "--workers", type=int, default=7, help="Max concurrent requests (default: 7)"
    )
    parser.add_argument(
        "--delay", type=float, default=1, help="Seconds between requests (default: 1)"
    )
    parser.add_argument(
        "--retries", type=int, default=3, help="Max retries for failed requests (default: 3)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch canonical names even if already cached",
    )
    parser.add_argument(
        "--league", type=str, default=None, help="Process only a single league file"
    )
    args = parser.parse_args()

    setup_logging()

    print(f"Processing season: {args.season}")

    addresses.load_name_cache()

    league_dir = DATA_DIR / "league_data" / args.season
    if not league_dir.exists():
        print(f"Error: league_data/{args.season} directory not found")
        return

    league_files = None
    if args.league:
        league_arg = Path(args.league)
        if league_arg.exists():
            league_files = [league_arg]
        else:
            candidates = list(league_dir.rglob(f"{args.league}*.json"))
            if not candidates:
                candidate = league_dir / args.league
                if candidate.suffix != ".json":
                    candidate = candidate.with_suffix(".json")
                if candidate.exists():
                    candidates = [candidate]
            if not candidates:
                print(f"Error: league file not found: {args.league}")
                return
            league_files = candidates

    representative_urls = collect_club_representative_urls(args.season, league_files)
    print(f"Found {len(representative_urls)} distinct clubs (derived) in season {args.season}")

    if args.force:
        to_fetch = dict(representative_urls)
    else:
        to_fetch = {
            name: url
            for name, url in representative_urls.items()
            if name not in addresses.club_name_cache
        }

    already_cached = len(representative_urls) - len(to_fetch)
    print(f"{len(to_fetch)} clubs need scraping ({already_cached} already cached)")

    if to_fetch:
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures_to_club = {
                    executor.submit(
                        addresses.fetch_club_canonical_name,
                        club_name,
                        url,
                        args.delay,
                        args.retries,
                    ): club_name
                    for club_name, url in to_fetch.items()
                }

                try:
                    for future in concurrent.futures.as_completed(futures_to_club):
                        club_name = futures_to_club[future]

                        try:
                            canonical_name, log_text = future.result()
                            print_block(log_text)
                        except AntiBotDetectedError as e:
                            for f in futures_to_club:
                                f.cancel()
                            if e.log_text is not None:
                                print_block(e.log_text)
                            print(f"{"="*80}")
                            print("ANTI-BOT DETECTION TRIGGERED")
                            print("Aborting processing to avoid being blocked")
                            print(f"{"="*80}")
                            raise
                        except Exception as e:
                            print_block(f"  Processing: {club_name}\n    ✗ Error: {e}")
                            canonical_name = None

                        if canonical_name:
                            addresses.club_name_cache[club_name] = canonical_name
                finally:
                    addresses.save_name_cache()
        except AntiBotDetectedError:
            print("\n✗ Anti-bot detection triggered")
            print("Please wait before running the script again.")
            return

    print(f"{"="*80}")
    print(f"Canonical name cache size: {len(addresses.club_name_cache)}")
    print(f"{"="*80}")

    groups = build_canonical_name_groups()
    merge_candidates = {name: members for name, members in groups.items() if len(members) > 1}
    if merge_candidates:
        print(f"\n{len(merge_candidates)} canonical clubs span multiple derived names:")
        for canonical_name, members in sorted(merge_candidates.items()):
            print(f"  {canonical_name}: {members}")


if __name__ == "__main__":
    main()
