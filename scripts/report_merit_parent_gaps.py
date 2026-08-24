"""Run --merit --no-interactive-on-warnings for every merit competition in every season,
and compile a report of leagues whose pyramid parent is missing or was rejected.

Usage: python scripts/report_merit_parent_gaps.py [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rugby.pyramid_image as pyramid_image  # noqa: E402
from core import EARLIEST_SEASON  # noqa: E402
from rugby.pyramid_image import discover_merit_competitions  # noqa: E402

GEO = Path("data/rugby/league_data")

MISSING_RE = re.compile(
    r"no parent given or inferred for ['\"](?P<league>.+)['\"] \(local tier (?P<tier>\d+)\)"
)
REJECTED_RE = re.compile(
    r"Stem parent override ['\"](?P<parent>.+)['\"] for ['\"](?P<league>.+)['\"] does not match "
    r"(?P<pool>.+); ignoring"
)


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())


def all_seasons() -> list[str]:
    return sorted(
        d.name for d in GEO.iterdir() if d.is_dir() and "-" in d.name and d.name >= EARLIEST_SEASON
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=str, default=None, help="Write structured report here")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    capture = _CaptureHandler()
    logging.getLogger().addHandler(capture)

    entries: list[dict] = []
    errors: list[dict] = []
    total = 0
    for season in all_seasons():
        comps = discover_merit_competitions(season)
        for comp in comps:
            total += 1
            capture.records.clear()
            argv_backup = sys.argv
            sys.argv = [
                "pyramid_image",
                "--merit",
                comp,
                "--season",
                season,
                "--no-interactive-on-warnings",
            ]
            try:
                rc = pyramid_image.main()
            except Exception as exc:  # noqa: BLE001
                rc = -1
                errors.append({"season": season, "comp": comp, "exception": repr(exc)})
            finally:
                sys.argv = argv_backup

            if rc:
                errors.append({"season": season, "comp": comp, "rc": rc})

            unique_records = list(dict.fromkeys(capture.records))
            by_league: dict[str, dict] = {}
            for msg in unique_records:
                m = REJECTED_RE.search(msg)
                if m:
                    league = m.group("league")
                    row = by_league.setdefault(
                        league, {"season": season, "comp": comp, "league": league}
                    )
                    row["kind"] = "rejected_override"
                    row["rejected_parent"] = m.group("parent")
                    row["pool"] = m.group("pool")
            for msg in unique_records:
                m = MISSING_RE.search(msg)
                if m:
                    league = m.group("league")
                    row = by_league.setdefault(
                        league, {"season": season, "comp": comp, "league": league}
                    )
                    row["local_tier"] = int(m.group("tier"))
                    row.setdefault("kind", "missing_parent")
            entries.extend(by_league.values())
            print(f"[{total}] {season} {comp}: {len(capture.records)} warning(s)")

    logging.getLogger().removeHandler(capture)

    print("\n" + "=" * 100)
    print(f"Checked {total} (season, competition) pairs.")
    print(f"Malformed entries: {len(entries)}   Hard errors: {len(errors)}")
    print("=" * 100)
    for e in entries:
        if e["kind"] == "rejected_override":
            print(
                f"  REJECTED  {e['season']:10s} {e['comp']:18s} {e['league']!r:45s} "
                f"-> {e.get('rejected_parent')!r} ({e.get('pool', '')})"
            )
        else:
            print(
                f"  MISSING   {e['season']:10s} {e['comp']:18s} {e['league']!r:45s} "
                f"(local tier {e['local_tier']})"
            )
    if errors:
        print("\nHard errors:")
        for e in errors:
            print(" ", e)

    if args.json:
        Path(args.json).write_text(
            json.dumps({"entries": entries, "errors": errors}, indent=2), encoding="utf-8"
        )
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
