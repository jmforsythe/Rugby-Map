#!/usr/bin/env bash
# Regenerate every pyramid (men's national, all merit + All_Leagues, women's) for all seasons.
# Uses saved tier_mappings JSON only (no TTY prompts). Each default output also writes a
# club-name-labelled sibling (*_Labels.{svg,png}).
#
# Usage (from repo root):
#   bash scripts/pyramid_all_seasons.sh
#   bash scripts/pyramid_all_seasons.sh --png

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PNG_ARGS=()
while (($#)); do
  case "$1" in
    --png)
      PNG_ARGS=(--png --png-scale 2)
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: bash scripts/pyramid_all_seasons.sh [--png]" >&2
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
  echo "  Pyramids — season ${season}"
  echo "  Men's national → dist/${season}/pyramid.{svg,png} + pyramid_Labels.*"
  echo "  Merit + All Leagues → dist/${season}/pyramid_merit_* + pyramid_All_Leagues.*"
  echo "  Women's → dist/${season}/pyramid_womens.* + pyramid_womens_Labels.*"
  if ((${#PNG_ARGS[@]} > 0)); then
    echo "  (+ matching .png via Playwright)"
  fi
  echo "================================================================================"

  python -m rugby.pyramid_image --season="${season}" "${PNG_ARGS[@]}"
  python -m rugby.pyramid_image --season="${season}" --merit "${PNG_ARGS[@]}"
  python -m rugby.pyramid_image --season="${season}" --womens "${PNG_ARGS[@]}"
done

echo ""
echo "Done: ${#SEASONS[@]} season(s)."
