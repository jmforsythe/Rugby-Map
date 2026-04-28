"""Backward-compatible CLI entry point.

Use ``python -m rugby.analysis.tier_travel_partition`` for tiers 4–6 and
``--num-leagues``. This module delegates there so existing docs/scripts keep working.
"""

from rugby.analysis.tier_travel_partition import main

if __name__ == "__main__":
    main()
