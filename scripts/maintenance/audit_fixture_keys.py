"""Compare fixture keys (date, home_team_id, away_team_id) between two trees."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FixtureKey:
    date: str
    home_team_id: int
    away_team_id: int

    @classmethod
    def from_fixture(cls, fixture: dict) -> FixtureKey | None:
        date = fixture.get("date")
        home_id = fixture.get("home_team_id")
        away_id = fixture.get("away_team_id")
        if not isinstance(date, str) or not date:
            return None
        if not isinstance(home_id, int) or not isinstance(away_id, int):
            return None
        return cls(date, home_id, away_id)


def load_fixture_keys(path: Path) -> dict[FixtureKey, dict]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    fixtures = data.get("fixtures", [])
    keys: dict[FixtureKey, dict] = {}
    for fixture in fixtures:
        key = FixtureKey.from_fixture(fixture)
        if key is not None:
            keys[key] = fixture
    return keys


def iter_fixture_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.rglob("*.json") if path.is_file() and not path.name.startswith("_")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_root", type=Path, help="Baseline fixture_data root")
    parser.add_argument("current_root", type=Path, help="Current fixture_data root")
    parser.add_argument(
        "--show-samples",
        type=int,
        default=3,
        help="Sample lost fixtures to print per file (0 to hide)",
    )
    args = parser.parse_args()

    baseline_files = {
        path.relative_to(args.baseline_root): path
        for path in iter_fixture_files(args.baseline_root)
    }
    current_files = {
        path.relative_to(args.current_root): path for path in iter_fixture_files(args.current_root)
    }
    all_relpaths = sorted(set(baseline_files) | set(current_files))

    total_lost = 0
    total_gained = 0
    files_with_losses: list[tuple[Path, int, int, int, int, list[FixtureKey]]] = []
    files_only_in_baseline: list[Path] = []
    files_only_in_current: list[Path] = []

    for relpath in all_relpaths:
        baseline_path = baseline_files.get(relpath)
        current_path = current_files.get(relpath)
        if baseline_path is None:
            files_only_in_current.append(relpath)
            continue
        if current_path is None:
            files_only_in_baseline.append(relpath)
            baseline_keys = load_fixture_keys(baseline_path)
            total_lost += len(baseline_keys)
            files_with_losses.append(
                (
                    relpath,
                    len(baseline_keys),
                    0,
                    len(baseline_keys),
                    0,
                    list(baseline_keys)[: args.show_samples],
                )
            )
            continue

        baseline_keys = load_fixture_keys(baseline_path)
        current_keys = load_fixture_keys(current_path)
        lost = baseline_keys.keys() - current_keys.keys()
        gained = current_keys.keys() - baseline_keys.keys()
        if lost:
            total_lost += len(lost)
            files_with_losses.append(
                (
                    relpath,
                    len(lost),
                    len(gained),
                    len(baseline_keys),
                    len(current_keys),
                    list(lost)[: args.show_samples],
                )
            )
        total_gained += len(gained)

    print("Fixture key audit")
    print(f"  Baseline: {args.baseline_root}")
    print(f"  Current:  {args.current_root}")
    print(f"  Files compared: {len(all_relpaths)}")
    print(f"  Files only in baseline: {len(files_only_in_baseline)}")
    print(f"  Files only in current:  {len(files_only_in_current)}")
    print(f"  Unique keys lost:   {total_lost}")
    print(f"  Unique keys gained: {total_gained}")
    print(f"  Net change:         {total_gained - total_lost:+d}")
    print(f"  Files with lost keys: {len(files_with_losses)}")
    print()

    if files_only_in_baseline:
        print("=== FILES REMOVED (all keys lost) ===")
        for relpath in files_only_in_baseline[:20]:
            print(f"  {relpath}")
        if len(files_only_in_baseline) > 20:
            print(f"  ... and {len(files_only_in_baseline) - 20} more")
        print()

    if files_only_in_current:
        print("=== NEW FILES (not in baseline) ===")
        for relpath in files_only_in_current[:20]:
            print(f"  {relpath}")
        if len(files_only_in_current) > 20:
            print(f"  ... and {len(files_only_in_current) - 20} more")
        print()

    if files_with_losses:
        print("=== FILES WITH LOST KEYS ===")
        for relpath, lost_count, gained_count, old_count, new_count, samples in sorted(
            files_with_losses, key=lambda row: (-row[1], str(row[0]))
        ):
            print(
                f"  lost={lost_count:4d} gained={gained_count:4d}  "
                f"{old_count:4d}->{new_count:4d}  {relpath}"
            )
            if args.show_samples and samples:
                baseline_path = baseline_files[relpath]
                baseline_map = load_fixture_keys(baseline_path)
                for key in samples:
                    fixture = baseline_map[key]
                    print(
                        f"      - {key.date}  "
                        f"{fixture.get('home_team', '?')} ({key.home_team_id}) vs "
                        f"{fixture.get('away_team', '?')} ({key.away_team_id})  "
                        f"status={fixture.get('status') or ''}"
                    )
    else:
        print("No fixture keys were lost.")

    return 1 if total_lost else 0


if __name__ == "__main__":
    sys.exit(main())
