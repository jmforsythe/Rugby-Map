"""Tests for the routed/Haversine DistanceLookup facade."""

import math

import numpy as np
import pytest

from rugby.distance_lookup import DistanceLookup, haversine_km

# Three made-up coordinates around the UK. Values for the routed matrix are
# arbitrary -- the tests only care about the lookup mechanics, not the numbers
# themselves.
COORD_A = (51.5074, -0.1278)  # London-ish
COORD_B = (53.4808, -2.2426)  # Manchester-ish
COORD_C = (55.9533, -3.1883)  # Edinburgh-ish

ROUTED_KM = np.array(
    [
        [0.0, 300.0, 600.0],
        [300.0, 0.0, 350.0],
        [600.0, 350.0, 0.0],
    ],
    dtype=np.float32,
)
ROUTED_MIN = np.array(
    [
        [0.0, 240.0, 450.0],
        [240.0, 0.0, 270.0],
        [450.0, 270.0, 0.0],
    ],
    dtype=np.float32,
)


def _build_lookup_with_routed() -> DistanceLookup:
    return DistanceLookup(
        coords=[COORD_A, COORD_B, COORD_C],
        distance_km=ROUTED_KM,
        duration_min=ROUTED_MIN,
    )


class TestHaversineHelper:
    def test_same_point_zero(self):
        assert haversine_km(*COORD_A, *COORD_A) == 0.0

    def test_london_edinburgh(self):
        # London to Edinburgh great-circle is roughly 535 km.
        d = haversine_km(*COORD_A, *COORD_C)
        assert 530 < d < 540

    def test_symmetry(self):
        d1 = haversine_km(*COORD_A, *COORD_B)
        d2 = haversine_km(*COORD_B, *COORD_A)
        assert math.isclose(d1, d2, rel_tol=1e-12)


class TestEmptyLookup:
    """No routed cache available: everything must fall back to Haversine."""

    def test_has_routed_false(self):
        lookup = DistanceLookup()
        assert not lookup.has_routed
        assert lookup.n_routed == 0

    def test_pair_km_falls_back_to_haversine(self):
        lookup = DistanceLookup()
        expected = haversine_km(*COORD_A, *COORD_B)
        assert lookup.pair_km(*COORD_A, *COORD_B) == pytest.approx(expected)

    def test_pair_min_returns_none(self):
        lookup = DistanceLookup()
        assert lookup.pair_min(*COORD_A, *COORD_B) is None

    def test_coord_id_none(self):
        lookup = DistanceLookup()
        assert lookup.coord_id(*COORD_A) is None


class TestRoutedLookup:
    """Routed cache present: hits use cache, misses fall back."""

    def test_has_routed_true(self):
        lookup = _build_lookup_with_routed()
        assert lookup.has_routed
        assert lookup.n_routed == 3

    def test_routed_pair_uses_cache(self):
        lookup = _build_lookup_with_routed()
        assert lookup.pair_km(*COORD_A, *COORD_B) == pytest.approx(300.0)
        assert lookup.pair_km(*COORD_B, *COORD_C) == pytest.approx(350.0)

    def test_routed_pair_minutes(self):
        lookup = _build_lookup_with_routed()
        assert lookup.pair_min(*COORD_A, *COORD_B) == pytest.approx(240.0)
        assert lookup.pair_min(*COORD_B, *COORD_C) == pytest.approx(270.0)

    def test_unknown_coord_falls_back_to_haversine(self):
        lookup = _build_lookup_with_routed()
        unknown = (50.0, 0.0)  # not in the cache
        # km falls back to Haversine, not to a stale routed value.
        expected = haversine_km(*unknown, *COORD_A)
        assert lookup.pair_km(*unknown, *COORD_A) == pytest.approx(expected)
        # minutes returns None when either point is missing.
        assert lookup.pair_min(*unknown, *COORD_A) is None

    def test_nan_pair_falls_back(self):
        """NaN cells (unreachable routes) should also trigger Haversine."""
        km = ROUTED_KM.copy()
        mins = ROUTED_MIN.copy()
        km[0, 1] = float("nan")
        mins[0, 1] = float("nan")
        lookup = DistanceLookup(
            coords=[COORD_A, COORD_B, COORD_C],
            distance_km=km,
            duration_min=mins,
        )
        expected_km = haversine_km(*COORD_A, *COORD_B)
        assert lookup.pair_km(*COORD_A, *COORD_B) == pytest.approx(expected_km)
        assert lookup.pair_min(*COORD_A, *COORD_B) is None

    def test_coord_id_rounding(self):
        """Coordinates that round to the same key should hit the cache."""
        lookup = _build_lookup_with_routed()
        # Same place to 6dp; the lookup rounds before hashing so this hits.
        nudged_a = (COORD_A[0] + 1e-9, COORD_A[1] - 1e-9)
        assert lookup.coord_id(*nudged_a) == lookup.coord_id(*COORD_A)
        # Differ in the 5th decimal place -- still rounds to the same 6dp key.
        assert lookup.pair_km(*nudged_a, *COORD_B) == pytest.approx(300.0)

    def test_arrays_returned(self):
        lookup = _build_lookup_with_routed()
        assert lookup.routed_km_array() is ROUTED_KM
        assert lookup.routed_min_array() is ROUTED_MIN

    def test_coord_to_id_map_is_a_copy(self):
        lookup = _build_lookup_with_routed()
        m = lookup.coord_to_id_map()
        m.clear()
        assert lookup.n_routed == 3
