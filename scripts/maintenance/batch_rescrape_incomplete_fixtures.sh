#!/usr/bin/env bash
# Rescrape incomplete fixtures for every season and commit when changed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

REQUEST_DELAY="${REQUEST_DELAY:-3}"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-900}"
MAX_ANTIBOT_RETRIES="${MAX_ANTIBOT_RETRIES:-5}"
EARLIEST="${EARLIEST:-2000-2001}"
# Newest seasons already re-scraped in the descending pass (2026-2027 .. 2024-2025).
SKIP=(
  "2026-2027"
  "2025-2026"
)

scrape_season() {
  local season="$1"
  # --only-incomplete skips Gallagher Premiership (root Premiership.json): RFU often
  # returns no cards for past seasons. Rescrapes that return fewer rows than on disk
  # are skipped (empty for all seasons; shrinks for seasons before 2024-2025).
  python -m rugby.fixtures --season "$season" --only-incomplete --delay "$REQUEST_DELAY"
}

commit_season() {
  local season="$1"
  git add "data/rugby/fixture_data/$season/"
  if git diff --cached --quiet; then
    echo "No changes for $season; skipping commit"
    return 0
  fi

  if ! git commit -m "Re-scrape ${season} fixtures for walkovers and normalized rows.

Targeted --only-incomplete rescrape with walkover statuses and deduped fixtures."; then
    git add "data/rugby/fixture_data/$season/"
    git commit -m "Re-scrape ${season} fixtures for walkovers and normalized rows.

Targeted --only-incomplete rescrape with walkover statuses and deduped fixtures."
  fi
  echo "Committed $season"
}

is_skipped() {
  local s="$1"
  for skip in "${SKIP[@]}"; do
    [[ "$skip" == "$s" ]] && return 0
  done
  return 1
}

mapfile -t SEASONS < <(
  for season_dir in data/rugby/league_data/*/; do
    season="$(basename "$season_dir")"
    [[ "$season" =~ ^[0-9]{4}-[0-9]{4}$ ]] || continue
    [[ "$season" < "$EARLIEST" ]] && continue
    is_skipped "$season" && continue
    echo "$season"
  done | sort
)

if ((${#SEASONS[@]} == 0)); then
  echo "No seasons to process."
  exit 0
fi

echo "Processing ${#SEASONS[@]} seasons (${SEASONS[0]} .. ${SEASONS[-1]}, oldest first)"
echo "Waiting ${COOLDOWN_SECONDS}s for RFU anti-bot cooldown..."
sleep "$COOLDOWN_SECONDS"

for season in "${SEASONS[@]}"; do
  echo "========== $season =========="
  python -m rugby.analysis.validate_league_urls --season "$season" --check-fixtures --offline || true

  attempt=1
  while true; do
    if scrape_season "$season"; then
      break
    fi
    if (( attempt >= MAX_ANTIBOT_RETRIES )); then
      echo "ERROR: fixture scrape failed for $season after $attempt attempts" >&2
      exit 1
    fi
    wait=$((COOLDOWN_SECONDS * attempt))
    echo "Anti-bot or scrape error; waiting ${wait}s before retry $((attempt + 1))/$MAX_ANTIBOT_RETRIES..."
    sleep "$wait"
    attempt=$((attempt + 1))
  done

  commit_season "$season"
done

echo "All seasons processed."
