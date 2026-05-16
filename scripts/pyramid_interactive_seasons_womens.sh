#!/usr/bin/env bash
# Interactive feeder linker for women's pyramid seasons (visual bands 2–4 → parents above).
#
# Requires a real interactive terminal (TTY). Cursor/agent terminals will fail with:
#   --interactive-stem-orphans requires an interactive terminal (stdin is not a TTY).
#
# Usage (from repo root):
#   bash scripts/pyramid_interactive_seasons_womens.sh              # SVG only
#   bash scripts/pyramid_interactive_seasons_womens.sh --png      # also rasterise (Playwright)
#
# Seasons run newest-first (same span as the men's interactive script).
#
# Prompts:
#   blank or 0 — explicit unlinked for that league ("-" in tier_mappings JSON)
#   1–N       — pick numbered parent at the tier above
#   s / stop  — stop prompting; SVG still writes for that season
#
# Writes: dist/<season>/pyramid_womens.svg (and .png if --png)
# Merges into: data/rugby/tier_mappings/<season>.json under womens_overrides_by_tier
#              (men's overrides_by_tier and stem_slot_strips are preserved when present).

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PNG_ARGS=()
if [[ "${1:-}" == "--png" ]]; then
  PNG_ARGS=(--png --png-scale 2)
fi

SEASONS=(
  2025-2026
  2024-2025
  2023-2024
  2022-2023
  2021-2022
  2020-2021
  2019-2020
  2018-2019
  2017-2018
  2016-2017
  2015-2016
  2014-2015
  2013-2014
  2012-2013
  2011-2012
  2010-2011
  2009-2010
  2008-2009
  2007-2008
  2006-2007
  2005-2006
  2004-2005
  2003-2004
  2002-2003
  2001-2002
  2000-2001
)

for season in "${SEASONS[@]}"; do
  echo ""
  echo "================================================================================"
  echo "  Women's pyramid — interactive feeder linker — season ${season}"
  echo "  Outputs: dist/${season}/pyramid_womens.svg (and .png if --png)"
  echo "  Mappings: data/rugby/tier_mappings/${season}.json (womens_overrides_by_tier)"
  echo "================================================================================"
  python -m rugby.pyramid_image --womens --season="${season}" --interactive-stem-orphans "${PNG_ARGS[@]}"
done
