#!/usr/bin/env bash
# Per-season CI: pyramid maps (HTML) and pyramid diagram raster (Playwright).
# Set FOOTBALL_PYRAMID_RASTER_CACHE_RESTORED=1 (deploy.yml) to skip raster when cache is valid.
set -euo pipefail

SEASON="${1:?usage: ci_football_season.sh YYYY-YYYY}"

PYRAMID_GEO="data/football/geocoded_teams/${SEASON}/pyramid"
fail=0

if [[ -d "$PYRAMID_GEO" ]]; then
  python -m football.pyramid_maps --season="$SEASON" --production --no-debug || fail=1

  if [[ "${FOOTBALL_PYRAMID_RASTER_CACHE_RESTORED:-0}" == "1" ]]; then
    echo "Football pyramid raster cache hit for ${SEASON} — skipping Playwright export"
  else
    if ! python -m football.pyramid_image --season="$SEASON" --production --png --png-scale 2; then
      fail=1
    fi
  fi
else
  echo "No pyramid geocoded data at ${PYRAMID_GEO} — skipping football pyramid outputs"
fi

if [[ -d "data/football/geocoded_teams/${SEASON}/BSLFL" ]]; then
  python -m football.maps --production || fail=1
fi

exit "$fail"
