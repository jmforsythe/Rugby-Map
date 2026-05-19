#!/usr/bin/env bash
# Per-season CI: distances, then maps ∥ match_day while pyramid PNGs run one-at-a-time.
# rugby.distances writes data/rugby/distance_cache/<season>.json (official league travel
# stats: OSRM routed km/min when the committed global cache exists, else Haversine).
# deploy.yml packs that file into the season tarball for custom_map at assemble time.
# Parallel pyramid_image --png launches multiple Chromium instances and causes runner
# OOM / Playwright screenshot timeouts (especially pyramid_All_Leagues).
set -euo pipefail

SEASON="${1:?usage: ci_rugby_season.sh YYYY-YYYY}"

python -m rugby.distances --season="$SEASON"

pids=()
fail=0

run_bg() {
  "$@" &
  pids+=("$!")
}

run_pyramid() {
  if ! "$@"; then
    fail=1
  fi
}

run_bg python -m rugby.maps --no-debug --season="$SEASON" --production

if [[ -d "data/rugby/fixture_data/$SEASON" ]]; then
  run_bg python -m rugby.match_day --season="$SEASON" --production
fi

run_pyramid python -m rugby.pyramid_image --season="$SEASON" --png --png-scale 2
run_pyramid python -m rugby.pyramid_image --womens --season="$SEASON" --png --png-scale 2

merit_comps=()
if find "data/rugby/geocoded_teams/$SEASON/merit" -mindepth 2 -name "*.json" -print -quit 2>/dev/null | grep -q .; then
  mapfile -t merit_comps < <(
    python -c "from rugby.pyramid_image import discover_merit_competitions as d; print('\n'.join(d('${SEASON}')))"
  )
  for comp in "${merit_comps[@]}"; do
    [[ -n "$comp" ]] || continue
    run_pyramid python -m rugby.pyramid_image --merit "$comp" --season="$SEASON" --png --png-scale 2
  done
  run_pyramid python -m rugby.pyramid_image --pyramid-all-leagues-only --season="$SEASON" --png --png-scale 2
fi

for pid in "${pids[@]}"; do
  if ! wait "$pid"; then
    fail=1
  fi
done

exit "$fail"
