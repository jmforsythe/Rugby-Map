"""Convert projected-leagues markdown into a custom map import JSON file.

Usage:
    python -m rugby.analysis.convert_projected_import
    python -m rugby.analysis.convert_projected_import --file data/rugby/projected_2026-2027.md
    python -m rugby.analysis.convert_projected_import --id projected-2026-2027 --label "Projected 2026-2027"
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rugby.analysis.projected_urls import PROJECTED_PATH, _parse_projected_md
from rugby.custom_map_imports import (
    IMPORTS_DIR,
    season_from_projected_markdown,
    tiers_dict_to_spec,
    write_import_spec,
)


def _default_output_path(import_id: str) -> Path:
    return IMPORTS_DIR / f"{import_id}.json"


def convert_markdown(
    md_path: Path,
    *,
    import_id: str | None = None,
    label: str | None = None,
    output: Path | None = None,
) -> Path:
    """Parse *md_path* and write an import JSON file. Returns the output path."""
    season = season_from_projected_markdown(md_path)
    resolved_id = import_id or f"projected-{season}"
    resolved_label = label or f"Projected {season}"
    out_path = output or _default_output_path(resolved_id)

    tiers_dict = _parse_projected_md(str(md_path))
    spec = tiers_dict_to_spec(
        tiers_dict,
        import_id=resolved_id,
        label=resolved_label,
        season=season,
    )
    write_import_spec(out_path, spec)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert projected-leagues markdown to custom_map_imports JSON."
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=PROJECTED_PATH,
        help="Projected markdown file (default: %(default)s)",
    )
    parser.add_argument(
        "--id",
        help="Import pack id (default: projected-<season> from filename)",
    )
    parser.add_argument(
        "--label",
        help='Section label in the import modal (default: "Projected <season>")',
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON path (default: data/rugby/custom_map_imports/<id>.json)",
    )
    args = parser.parse_args()

    if not args.file.is_file():
        parser.error(f"Markdown file not found: {args.file}")

    out_path = convert_markdown(
        args.file,
        import_id=args.id,
        label=args.label,
        output=args.output,
    )
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
