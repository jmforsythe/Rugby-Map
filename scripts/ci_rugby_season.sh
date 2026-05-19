#!/usr/bin/env bash
# Per-season CI: distances, then maps / pyramids / match_day in parallel.
# Merit competitions render concurrently; pyramid_All_Leagues runs after they finish.
set -euo pipefail

SEASON="${1:?usage: ci_rugby_season.sh YYYY-YYYY}"

python -m rugby.distances --season="$SEASON"

pids=()
fail=0

run_bg() {
  "$@" &
  pids+=("$!")
}

run_bg python -m rugby.maps --no-debug --season="$SEASON" --production
run_bg python -m rugby.pyramid_image --season="$SEASON" --png --png-scale 2
run_bg python -m rugby.pyramid_image --womens --season="$SEASON" --png --png-scale 2

merit_comps=()
if find "data/rugby/geocoded_teams/$SEASON/merit" -mindepth 2 -name "*.json" -print -quit 2>/dev/null | grep -q .; then
  mapfile -t merit_comps < <(
    python -c "from rugby.pyramid_image import discover_merit_competitions as d; print('\n'.join(d('${SEASON}')))"
  )
  for comp in "${merit_comps[@]}"; do
    [[ -n "$comp" ]] || continue
    run_bg python -m rugby.pyramid_image --merit "$comp" --season="$SEASON" --png --png-scale 2
  done
fi

if [[ -d "data/rugby/fixture_data/$SEASON" ]]; then
  run_bg python -m rugby.match_day --season="$SEASON" --production
fi

for pid in "${pids[@]}"; do
  if ! wait "$pid"; then
    fail=1
  fi
done

if ((${#merit_comps[@]} > 0)); then
  if ! python -m rugby.pyramid_image --pyramid-all-leagues-only --season="$SEASON" --png --png-scale 2; then
    fail=1
  fi
fi

exit "$fail"
