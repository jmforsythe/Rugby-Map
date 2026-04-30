"""Pair-distance lookup that prefers routed values, falls back to Haversine.

A thin convenience wrapper around the global routed matrix produced by
``rugby.distances_routed``. Consumers ask for the road distance (km) or
duration (minutes) between two coordinates and get either the cached routed
value (if both points are in the matrix) or a Haversine approximation.

The routed cache is **global, not per-season**, because club locations are
static across seasons.

Usage::

    lookup = DistanceLookup.load()
    km = lookup.pair_km(lat_a, lon_a, lat_b, lon_b)
    minutes = lookup.pair_min(lat_a, lon_a, lat_b, lon_b)  # may be None

If no routed cache is available, ``pair_km`` returns Haversine and
``pair_min`` returns ``None``.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from rugby.distances_routed import GEOCODE_DECIMALS, GLOBAL_NPZ, load_matrix

logger = logging.getLogger(__name__)


def _round_coord(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, GEOCODE_DECIMALS), round(lon, GEOCODE_DECIMALS))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (mirrors ``rugby.distances.distance``)."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371.0 * c


class DistanceLookup:
    """Pair-distance lookup with optional routed cache and Haversine fallback."""

    def __init__(
        self,
        coords: list[tuple[float, float]] | None = None,
        distance_km: np.ndarray | None = None,
        duration_min: np.ndarray | None = None,
    ) -> None:
        self._coord_to_id: dict[tuple[float, float], int] = {}
        self._distance_km = distance_km
        self._duration_min = duration_min
        if coords is not None:
            for i, (lat, lon) in enumerate(coords):
                self._coord_to_id[_round_coord(lat, lon)] = i

    @classmethod
    def load(cls) -> DistanceLookup:
        """Load the global routed matrix if present, else fallback-only."""
        loaded = load_matrix()
        if loaded is None:
            logger.info(
                "No routed distance cache at %s; falling back to Haversine for all pairs. "
                "Run `python -m rugby.distances_routed` to build it.",
                GLOBAL_NPZ,
            )
            return cls()
        distance_km, duration_min, coords, _sidecar = loaded
        n = distance_km.shape[0]
        n_unreachable = int(np.isnan(distance_km).sum())
        logger.info(
            "Loaded global routed cache: %d geocodes, %d unreachable cells",
            n,
            n_unreachable,
        )
        return cls(coords=coords, distance_km=distance_km, duration_min=duration_min)

    @property
    def has_routed(self) -> bool:
        return self._distance_km is not None

    @property
    def n_routed(self) -> int:
        return len(self._coord_to_id)

    def coord_id(self, lat: float, lon: float) -> int | None:
        """Routed-matrix row index for a coordinate (None if not present)."""
        return self._coord_to_id.get(_round_coord(lat, lon))

    def coord_to_id_map(self) -> dict[tuple[float, float], int]:
        """Read-only view of the coord -> matrix-id mapping."""
        return dict(self._coord_to_id)

    def routed_km_array(self) -> np.ndarray | None:
        return self._distance_km

    def routed_min_array(self) -> np.ndarray | None:
        return self._duration_min

    def pair_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Routed road distance in km if available, else Haversine."""
        if self._distance_km is not None:
            i = self._coord_to_id.get(_round_coord(lat1, lon1))
            j = self._coord_to_id.get(_round_coord(lat2, lon2))
            if i is not None and j is not None:
                value = float(self._distance_km[i, j])
                if not math.isnan(value):
                    return value
        return haversine_km(lat1, lon1, lat2, lon2)

    def pair_min(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
        """Routed driving time in minutes if available, else None."""
        if self._duration_min is not None:
            i = self._coord_to_id.get(_round_coord(lat1, lon1))
            j = self._coord_to_id.get(_round_coord(lat2, lon2))
            if i is not None and j is not None:
                value = float(self._duration_min[i, j])
                if not math.isnan(value):
                    return value
        return None

    @staticmethod
    def npz_path() -> Path:
        """Convenience for callers that need the on-disk artifact path."""
        return GLOBAL_NPZ
