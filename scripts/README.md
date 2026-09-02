# Scripts

Helper entry points that are not part of the main `python -m rugby.*` pipeline.
Diagnostics live under `rugby/analysis/` instead.

## Layout

| Directory | Purpose |
|---|---|
| `ci/` | GitHub Actions season build wrappers |
| `maintenance/` | One-off or occasional data backfills and migrations |
| `batch/` | Multi-season shell loops (pyramids, Instagram maps) |
| *(root)* | Deploy-critical utilities (`fetch_vendor_assets`, review screenshots) |

## Production / CI

```bash
python scripts/fetch_vendor_assets.py          # self-host Leaflet vendor JS/CSS (CI)
bash scripts/ci/ci_rugby_season.sh 2026-2027   # per-season rugby matrix job
bash scripts/ci/ci_football_season.sh 2025-2026
python scripts/capture_review_screenshots.py   # or: make review-screenshots
```

## Batch (local)

```bash
bash scripts/batch/pyramid_merit_all_seasons.sh     # make pyramid-merit-all-seasons
bash scripts/batch/instagram_maps_all_seasons.sh  # make instagram-maps-all-seasons
bash scripts/batch/pyramid_interactive_seasons.sh --merit --png
```

## Maintenance (run when needed)

```bash
python scripts/maintenance/build_club_cb_mapping.py "CB and Club Relationships.pdf"
python scripts/maintenance/migrate_geocoded_to_club_maps.py
python scripts/maintenance/interactive_merit_parent_review.py
```

## Diagnostics

Prefer `python -m rugby.analysis.<module>` or Makefile targets:

```bash
make audit-fixtures    # fixture venue vs home-address audit
make validate-tiers    # tier extraction sanity check
python -m rugby.analysis.merit_cb_crossref
python -m rugby.analysis.report_merit_parent_gaps
```
