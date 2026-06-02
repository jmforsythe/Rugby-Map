"""
Generate interactive pyramid maps for English football (levels 1-10).

Reads ``data/football/geocoded_teams/<season>/pyramid/`` and writes:
  - One map per pyramid level (divisions as toggleable layers)
  - Combined all-levels map

Output (production): ``dist/football/<season>/``
"""

from __future__ import annotations

import argparse
import logging

from core import setup_logging
from core.map_builder import TerritoryCache, generate_multi_group_map, generate_single_group_map
from football import DATA_DIR
from football.map_common import (
    build_map_config,
    dist_season_dir,
    group_by_tier,
    load_pyramid_items,
    output_path,
    prepare_map_context,
    rotated_palette,
    tier_file_slug,
    tier_sibling_links,
)

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate English football pyramid maps")
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--no-debug", action="store_true")
    args = parser.parse_args()

    setup_logging()
    season = args.season
    production = args.production
    show_debug = not args.no_debug

    geocoded_dir = DATA_DIR / "geocoded_teams" / season / "pyramid"
    items = load_pyramid_items(geocoded_dir)
    if not items:
        logger.error("No geocoded pyramid items in %s", geocoded_dir)
        return

    by_tier, tier_order = group_by_tier(items)
    logger.info("Loaded %d teams across %d levels", len(items), len(tier_order))

    itl_hierarchy = prepare_map_context(production=production, show_debug=show_debug)
    output_dir = dist_season_dir(season, production=production)
    output_dir.mkdir(parents=True, exist_ok=True)
    territory_cache: TerritoryCache = {}

    from core.map_builder import preassign_itl_regions

    preassign_itl_regions(items, itl_hierarchy)

    level_links = tier_sibling_links(tier_order, by_tier, production=production)

    for tier_name in tier_order:
        tier_items = by_tier[tier_name]
        tier_num = tier_items[0].tier_num
        slug = tier_file_slug(tier_num)
        out = output_path(output_dir, slug, production=production)
        config = build_map_config(
            tier_name,
            season,
            show_debug=show_debug,
            palette=rotated_palette(tier_num),
            production=production,
            sibling_tiers=level_links,
            current_tier=tier_name,
        )
        logger.info(
            "Creating %s (%d teams, %d divisions)",
            tier_name,
            len(tier_items),
            len({i.group for i in tier_items}),
        )
        generate_single_group_map(tier_items, out, itl_hierarchy, config, territory_cache)

    all_out = output_path(output_dir, "All_Tiers", production=production)
    all_config = build_map_config(
        "All Tiers",
        season,
        show_debug=show_debug,
        production=production,
        sibling_tiers=level_links,
        current_tier="All Tiers",
    )
    logger.info("Creating combined all-tiers map (%d teams)", len(items))
    generate_multi_group_map(items, all_out, itl_hierarchy, all_config, territory_cache)

    logger.info("Maps saved under %s", output_dir)


if __name__ == "__main__":
    main()
