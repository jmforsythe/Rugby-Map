#!/usr/bin/env bash
# Interactive parent linker for historical men's pyramid seasons (tiers 5–6 feeders, then Counties stem).
# Women's equivalent: scripts/pyramid_interactive_seasons_womens.sh (--womens bands 2–4 feeders).
#
# Requires a real interactive terminal (TTY). Cursor/agent terminals will fail with:
#   --interactive-stem-orphans requires an interactive terminal (stdin is not a TTY).
#
# Usage (from repo root):
#   bash scripts/pyramid_interactive_seasons.sh              # SVG only, faster per season
#   bash scripts/pyramid_interactive_seasons.sh --png        # also rasterise (needs Playwright)
#
# Seasons run newest-first between the 2022 and ~2008 restructures:
#   2021-2022 … 2008-2009
#
# Prompts:
#   blank or 0 — explicit unlinked for that league
#   1–N       — pick numbered parent at the tier above
#   s / stop  — stop prompting; SVG still writes for that season

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
  echo "  Men's pyramid — interactive parent linker — season ${season}"
  echo "  Outputs: dist/${season}/pyramid.svg (and .png if --png)"
  echo "  Mappings: data/rugby/tier_mappings/${season}.json"
  echo "================================================================================"
  python -m rugby.pyramid_image --season="${season}" --interactive-stem-orphans "${PNG_ARGS[@]}"
done
