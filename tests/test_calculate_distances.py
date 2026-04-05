"""Tests for distance calculation functions."""

import math

from rugby.distances import distance


class TestHaversineDistance:
    """Tests for the haversine distance formula."""

    def test_same_point_returns_zero(self):
        assert distance(51.5, -0.1, 51.5, -0.1) == 0.0

    def test_london_to_edinburgh(self):
        d = distance(51.5074, -0.1278, 55.9533, -3.1883)
        assert 530 < d < 540

    def test_symmetry(self):
        d1 = distance(51.5, -0.1, 53.5, -2.2)
        d2 = distance(53.5, -2.2, 51.5, -0.1)
        assert math.isclose(d1, d2, rel_tol=1e-9)

    def test_short_distance(self):
        d = distance(51.5074, -0.1278, 51.5080, -0.1280)
        assert 0 < d < 1
