"""Crown-dependency distance model: discounted flying km but air-corridor minutes."""

import math

import numpy as np
import pytest

from rugby.distance_lookup import DistanceLookup, haversine_km
from rugby.offshore_travel import AIR_MIN_CI_MAINLAND, classify_region


def test_classify_regions_jersey_and_london():
    assert classify_region(49.20, -2.06) == "jersey"
    assert classify_region(51.5074, -0.1278) == "mainland"


def test_offshore_vs_mainland_uses_air_bridge_not_direct_haversine(monkeypatch):
    """Pin Jersey to a single UK gateway so the synthetic matrix stays deterministic."""
    monkeypatch.setattr(
        "rugby.offshore_travel.JERSEY_UK_GATEWAYS",
        frozenset({"sou_airport"}),
    )
    jersey_club = (49.190000, -2.075000)
    london = (51.507400, -0.127800)
    jer_air = (49.212222, -2.195556)
    sou_air = (50.949331, -1.356442)

    coords = [jersey_club, london, jer_air, sou_air]
    n = len(coords)
    dtype = np.float32
    dist = np.zeros((n, n), dtype=dtype)
    dur = np.zeros((n, n), dtype=dtype)
    ocean_hops = ((0, 1),)

    dist[:] = np.nan
    dur[:] = np.nan
    np.fill_diagonal(dist, 0.0)
    np.fill_diagonal(dur, 0.0)

    dist[0, 2] = dist[2, 0] = 14.0
    dist[1, 3] = dist[3, 1] = 138.5
    dur[0, 2] = dur[2, 0] = 41.5
    dur[1, 3] = dur[3, 1] = 157.25
    for i, j in ocean_hops:
        dist[i, j] = dist[j, i] = np.nan

    lookup = DistanceLookup(coords=coords, distance_km=dist, duration_min=dur)
    bridged_km = lookup.pair_km(*jersey_club, *london)

    straight = haversine_km(*jersey_club, *london)
    assert bridged_km == pytest.approx(152.5)  # 14 + 138.5; air km omitted.
    assert bridged_km < 0.7 * straight  # Not the naive ferry/great-circle full hop.

    bridged_min = lookup.pair_min(*jersey_club, *london)
    assert bridged_min is not None and math.isclose(
        bridged_min,
        41.5 + AIR_MIN_CI_MAINLAND + 157.25,
        rel_tol=1e-4,
        abs_tol=0.05,
    )
