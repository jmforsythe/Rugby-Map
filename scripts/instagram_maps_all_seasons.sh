#!/usr/bin/env bash
# Generate Instagram league maps (PNG + SVG) for every season with geocoded data.
#
# Usage (from repo root):
#   bash scripts/instagram_maps_all_seasons.sh
#   bash scripts/instagram_maps_all_seasons.sh --season 2026-2027
#   make instagram-maps-all-seasons

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GEO_ROOT="data/rugby/geocoded_teams"
OUT_ROOT="output/instagram/maps"
BOUNDARY_DETAIL="BUC"

SEASON_FILTER=""
while (($#)); do
  case "$1" in
    --season)
      SEASON_FILTER="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: bash scripts/instagram_maps_all_seasons.sh [--season YYYY-YYYY]" >&2
      exit 1
      ;;
  esac
done

if [[ -n "$SEASON_FILTER" ]]; then
  SEASONS=("$SEASON_FILTER")
elif [[ ! -d "$GEO_ROOT" ]]; then
  echo "No geocoded teams directory at $GEO_ROOT" >&2
  exit 1
else
  mapfile -t SEASONS < <(ls -1 "$GEO_ROOT" | sort -r)
fi

if ((${#SEASONS[@]} == 0)); then
  echo "No seasons found under $GEO_ROOT" >&2
  exit 1
fi

for season in "${SEASONS[@]}"; do
  if [[ ! -d "$GEO_ROOT/$season" ]]; then
    echo "Skipping $season — no geocoded data at $GEO_ROOT/$season" >&2
    continue
  fi

  echo ""
  echo "================================================================================"
  echo "  Instagram maps — season ${season}"
  echo "  Outputs: ${OUT_ROOT}/${season}/level_*.svg and level_*.png"
  echo "  Boundary detail: ${BOUNDARY_DETAIL}"
  echo "================================================================================"
  python -m rugby.instagram_maps \
    --season="${season}" \
    --png \
    --boundary-detail "${BOUNDARY_DETAIL}"
done

echo ""
echo "Done: ${#SEASONS[@]} season(s)."
