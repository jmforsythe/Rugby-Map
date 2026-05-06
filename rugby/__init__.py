"""English RFU rugby union data pipeline."""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "rugby"

BRAND = "RugbyUnionMap"


def short_season(season: str) -> str:
    """Convert a full season string ('2025-2026') to its display form ('2025-26').

    Returns the input unchanged if it doesn't match the expected YYYY-YYYY shape,
    so callers can pass arbitrary values without tripping on edge cases.
    """
    parts = season.split("-")
    if len(parts) == 2 and len(parts[0]) == 4 and len(parts[1]) == 4 and parts[1].isdigit():
        return f"{parts[0]}-{parts[1][2:]}"
    return season
