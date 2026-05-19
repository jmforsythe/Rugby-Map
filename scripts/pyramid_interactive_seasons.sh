#!/usr/bin/env bash
# Interactive parent linker for historical men's pyramid seasons (tiers 5–6, tier 7 order, stem).
# Women's equivalent: scripts/pyramid_interactive_seasons_womens.sh (--womens bands 2–4 feeders).
#
# Requires a real interactive terminal (TTY). Cursor/agent terminals will fail with:
#   --interactive-stem-orphans requires an interactive terminal (stdin is not a TTY).
#
# Usage (from repo root):
#   bash scripts/pyramid_interactive_seasons.sh                    # men's pyramid SVG only
#   bash scripts/pyramid_interactive_seasons.sh --png              # also rasterise (needs Playwright)
#   bash scripts/pyramid_interactive_seasons.sh --merit                 # merit pyramids + pyramid_All_Leagues (national+merit)
#   bash scripts/pyramid_interactive_seasons.sh --merit Hampshire --png # flags can be combined; order-free
#
# Seasons run newest-first between the 2022 and ~2008 restructures:
#   2021-2022 … 2008-2009
#
# Prompts (in order): tier 5→4, tier 6→5, tier 7 column-order (tiers 1–6 feeders), tier 8+ stem
#   blank or 0 — explicit unlinked for that league
#   1–N       — pick numbered parent at the tier above
#   s / stop  — stop prompting; SVG still writes for that season

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PNG_ARGS=()
MERIT_OPTS=()

while (($#)); do
  case "$1" in
    --png)
      PNG_ARGS=(--png --png-scale 2)
      shift
      ;;
    --merit)
      shift
      MERIT_OPTS=(--merit)
      if (($# > 0)) && [[ "${1:-}" != --* ]]; then
        MERIT_OPTS+=("$1")
        shift
      fi
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: bash scripts/pyramid_interactive_seasons.sh [--png] [--merit [COMPETITION]]" >&2
      exit 1
      ;;
  esac
done

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
  if ((${#MERIT_OPTS[@]} > 0)); then
    if ((${#MERIT_OPTS[@]} > 1)); then
      echo "  Merit: single competition (${MERIT_OPTS[1]})"
    else
      echo "  Merit: every competition under data/rugby/geocoded_teams/${season}/merit/"
    fi
    echo "  Outputs: dist/${season}/pyramid_merit_*.svg, pyramid_All_Leagues.{svg,png} (and .png if --png)"
  else
    echo "  Outputs: dist/${season}/pyramid.svg (and .png if --png)"
  fi
  echo "  Mappings: data/rugby/tier_mappings/${season}.json"
  echo "================================================================================"
  python -m rugby.pyramid_image \
    --season="${season}" "${MERIT_OPTS[@]}" --interactive-stem-orphans "${PNG_ARGS[@]}"
done
