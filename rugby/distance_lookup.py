"""Pair-distance lookup that prefers routed values, falls back to Haversine.

A thin convenience wrapper around the global routed matrix produced by
``rugby.distances_routed``. Consumers ask for the road distance (km) or
duration (minutes) between two coordinates.

**Crown dependencies** (Jersey, Guernsey, Isle of Man): when one or both clubs
sit offshore relative to mainland England routing, OSRM ferry routes can be odd
and direct great-circle kilometres overstate perceived «road-trip» burdens. We
use an **air-bridge heuristic** (see ``rugby.offshore_travel``):

- **km**: drive to the local runway waypoint, then drive from the **best**
  mainland gateway airport (among those with direct service from that island)
  to the opponent — **airborne km is omitted**. Gateway choice minimises total
  surface kilometres.
- **minutes**: routed (or inferred) driving time for those legs plus corridor
  overhead (airport/air block), using the same gateway that minimised km
  (tie-break: lower total minutes).

Otherwise behaviour is routed matrix first, unreachable/missing coords fall
back to Haversine for km.

Usage::

    lookup = DistanceLookup.load()
    km = lookup.pair_km(lat_a, lon_a, lat_b, lon_b)
    minutes = lookup.pair_min(lat_a, lon_a, lat_b, lon_b)  # may be None …

"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from rugby.distances_routed import GEOCODE_DECIMALS, GLOBAL_NPZ, load_matrix
from rugby.offshore_travel import (
    AVG_UK_DRIVE_KMH,
    WAYPOINT_BY_KEY,
    air_minutes_between_regions,
    classify_region,
    local_airport_key,
    mainland_gateway_keys_for_region,
)

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

    # -------------------------------------------------------------------------
    # Core matrix accessors (pure OSRM-derived; no offshore policy)
    # -------------------------------------------------------------------------

    def _matrix_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
        if self._distance_km is None:
            return None
        i = self._coord_to_id.get(_round_coord(lat1, lon1))
        j = self._coord_to_id.get(_round_coord(lat2, lon2))
        if i is None or j is None:
            return None
        value = float(self._distance_km[i, j])
        if math.isnan(value):
            return None
        return value

    def _matrix_min(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
        if self._duration_min is None:
            return None
        i = self._coord_to_id.get(_round_coord(lat1, lon1))
        j = self._coord_to_id.get(_round_coord(lat2, lon2))
        if i is None or j is None:
            return None
        value = float(self._duration_min[i, j])
        if math.isnan(value):
            return None
        return value

    def _estimated_drive_min(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        return haversine_km(lat1, lon1, lat2, lon2) / AVG_UK_DRIVE_KMH * 60.0

    def _baseline_pair_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        value = self._matrix_km(lat1, lon1, lat2, lon2)
        if value is not None:
            return value
        return haversine_km(lat1, lon1, lat2, lon2)

    def _baseline_pair_min(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float | None:
        value = self._matrix_min(lat1, lon1, lat2, lon2)
        if value is not None:
            return value
        return None

    def _segment_drive_km_and_min(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> tuple[float, float]:
        km = self._baseline_pair_km(lat1, lon1, lat2, lon2)
        mins_ob = self._baseline_pair_min(lat1, lon1, lat2, lon2)
        mins = mins_ob if mins_ob is not None else self._estimated_drive_min(lat1, lon1, lat2, lon2)
        return km, mins

    # -------------------------------------------------------------------------
    # Crown-dependency composites
    # -------------------------------------------------------------------------

    def _offshore_one_mainland_pair_km_min(
        self,
        mainland_lat: float,
        mainland_lon: float,
        offshore_lat: float,
        offshore_lon: float,
        offshore_region: str,
    ) -> tuple[float, float]:
        ap_key = local_airport_key(offshore_region)  # type: ignore[arg-type]
        ap_lat, ap_lon = WAYPOINT_BY_KEY[ap_key]
        sea_km, seam = self._segment_drive_km_and_min(offshore_lat, offshore_lon, ap_lat, ap_lon)
        corridor = air_minutes_between_regions(
            "mainland",
            offshore_region,  # type: ignore[arg-type]
        )
        best: tuple[float, float] | None = None
        for hub_key in mainland_gateway_keys_for_region(offshore_region):  # type: ignore[arg-type]
            hub_lat, hub_lon = WAYPOINT_BY_KEY[hub_key]
            land_km, landm = self._segment_drive_km_and_min(
                mainland_lat, mainland_lon, hub_lat, hub_lon
            )
            total_km = sea_km + land_km
            total_min = seam + corridor + landm
            if best is None:
                best = (total_km, total_min)
            else:
                bk, bm = best
                if total_km < bk - 1e-9 or (math.isclose(total_km, bk) and total_min < bm):
                    best = (total_km, total_min)
        assert best is not None
        return best

    def _offshore_cross_pair_km_min(
        self,
        lat1: float,
        lon1: float,
        reg1: str,
        lat2: float,
        lon2: float,
        reg2: str,
    ) -> tuple[float, float]:
        """Two distinct offshore regions."""
        key1 = local_airport_key(reg1)  # type: ignore[arg-type]
        key2 = local_airport_key(reg2)  # type: ignore[arg-type]
        ap1 = WAYPOINT_BY_KEY[key1]
        ap2 = WAYPOINT_BY_KEY[key2]
        km1, m1 = self._segment_drive_km_and_min(lat1, lon1, *ap1)
        km2, m2 = self._segment_drive_km_and_min(lat2, lon2, *ap2)
        cor = air_minutes_between_regions(reg1, reg2)  # type: ignore[arg-type]
        return km1 + km2, m1 + m2 + cor

    def pair_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r1 = classify_region(lat1, lon1)
        r2 = classify_region(lat2, lon2)
        if r1 == "mainland" and r2 == "mainland":
            return self._baseline_pair_km(lat1, lon1, lat2, lon2)
        if r1 != "mainland" and r2 != "mainland" and r1 == r2:
            return self._baseline_pair_km(lat1, lon1, lat2, lon2)
        if r1 != "mainland" and r2 != "mainland":
            km, _m = self._offshore_cross_pair_km_min(lat1, lon1, r1, lat2, lon2, r2)
            return km
        if r1 == "mainland":
            km, _m = self._offshore_one_mainland_pair_km_min(lat1, lon1, lat2, lon2, r2)
            return km
        km, _m = self._offshore_one_mainland_pair_km_min(lat2, lon2, lat1, lon1, r1)
        return km

    def pair_min(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float | None:
        r1 = classify_region(lat1, lon1)
        r2 = classify_region(lat2, lon2)
        if r1 == "mainland" and r2 == "mainland":
            return self._baseline_pair_min(lat1, lon1, lat2, lon2)
        if r1 != "mainland" and r2 != "mainland" and r1 == r2:
            return self._baseline_pair_min(lat1, lon1, lat2, lon2)
        if r1 != "mainland" and r2 != "mainland":
            _km, m = self._offshore_cross_pair_km_min(lat1, lon1, r1, lat2, lon2, r2)
            return m
        if r1 == "mainland":
            _km, m = self._offshore_one_mainland_pair_km_min(lat1, lon1, lat2, lon2, r2)
            return m
        _km, m = self._offshore_one_mainland_pair_km_min(lat2, lon2, lat1, lon1, r1)
        return m

    @staticmethod
    def npz_path() -> Path:
        """Convenience for callers that need the on-disk artifact path."""
        return GLOBAL_NPZ
