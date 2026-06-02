"""Merge duplicate pyramid division files after league-name canonicalization."""

from __future__ import annotations

import argparse
import logging

from core import setup_logging
from football import DATA_DIR
from football.league_names import consolidate_pyramid_season

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consolidate duplicate football pyramid division files"
    )
    parser.add_argument("--season", default="2025-2026")
    args = parser.parse_args()

    setup_logging()
    removed, merged = consolidate_pyramid_season(args.season, DATA_DIR)
    logger.info(
        "Consolidated %s: removed %d duplicate files, merged %d teams",
        args.season,
        removed,
        merged,
    )


if __name__ == "__main__":
    main()
