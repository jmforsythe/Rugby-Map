"""Crown-dependency «air bridge» modelling for routed travel stats.

Jersey / Guernsey / Isle of Man rugby teams realistically fly part of trips to
England. We approximate that as:

- **Distance (km)**: only «ground-km» segments — routed (or Haversine) from
  each club to the local runway waypoint plus the **best** scheduled mainland
  gateway airport to the opponent (hub chosen to minimise total ground km across
  airports that have direct service from that island, per published route lists);
  **airborne km is omitted** from the displayed total.

- **Time (minutes)**: sum of routed drive durations for those segments
  (estimating unknown legs from Haversine / ``AVG_UK_DRIVE_KMH``) plus corridor
  block times covering gate/arrival/overhead plus typical block-flight time.

Gateway sets (updated from operator destination lists; verify periodically):

- **Guernsey** ↔ UK: Birmingham, Bristol, Exeter, Gatwick, Heathrow, London
  City, Manchester, Southampton (excludes Jersey, Alderney, Paris).
- **Jersey** ↔ UK: Birmingham, Bristol, East Midlands, Exeter, Leeds Bradford,
  Liverpool, Gatwick, Heathrow, Luton, Manchester, Newcastle, Norwich,
  Southampton, Southend.
- **Isle of Man** ↔ UK: Birmingham, Bristol, Liverpool, London City, Gatwick,
  Heathrow, Luton, Manchester, Newquay, Southampton.

Airport waypoint coordinates are merged into ``rugby.distances_routed`` geocode
collection so rows exist in ``all.npz`` for OSRM.
"""

from __future__ import annotations

import typing as t

GEOCODE_DECIMALS = 6


def round_coord(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, GEOCODE_DECIMALS), round(lon, GEOCODE_DECIMALS))


# (js_key, lat, lon) — reference points ~airfield; keys must be unique.
_WAYPOINT_SPECS: tuple[tuple[str, float, float], ...] = (
    ("jer_airport", 49.212222, -2.195556),  # Jersey EGJJ
    ("gci_airport", 49.434722, -2.601944),  # Guernsey EGJB
    ("iom_airport", 54.086389, -4.631111),  # Isle of Man EGNS
    # Shared / multi-island UK gateways
    ("bhx_airport", 52.453856, -1.748028),  # Birmingham EGBB
    ("brs_airport", 51.382944, -2.719097),  # Bristol EGGD
    ("ema_airport", 52.831111, -1.328056),  # East Midlands EGNX
    ("ext_airport", 50.734400, -3.413900),  # Exeter EGTE
    ("lba_airport", 53.865897, -1.660572),  # Leeds Bradford EGNM
    ("lpl_airport", 53.333611, -2.849722),  # Liverpool EGGP
    ("lgw_airport", 51.148056, -0.190278),  # London Gatwick EGKK
    ("lhr_airport", 51.470000, -0.454300),  # London Heathrow EGLL
    ("lcy_airport", 51.504800, 0.049500),  # London City EGLC
    ("ltn_airport", 51.874722, -0.368333),  # London Luton EGGW
    ("man_airport", 53.353744, -2.274950),  # Manchester EGCC
    ("ncl_airport", 55.037500, -1.691667),  # Newcastle EGNT
    ("nqy_airport", 50.440556, -4.994444),  # Newquay / Cornwall EGDG
    ("nwi_airport", 52.675833, 1.282778),  # Norwich EGSH
    ("sou_airport", 50.949331, -1.356442),  # Southampton EGHI
    ("sen_airport", 51.571389, 0.695556),  # Southend EGMC
)

WAYPOINT_BY_KEY: dict[str, tuple[float, float]] = {
    k: round_coord(lat, lon) for k, lat, lon in _WAYPOINT_SPECS
}


def _merge_label(key: str) -> str:
    return f"__routing_{key}__"


_MERGE_ROWS: tuple[tuple[str, float, float], ...] = tuple(
    (_merge_label(k), lat, lon) for k, lat, lon in _WAYPOINT_SPECS
)


WAYPOINT_MERGE_ROWS: tuple[tuple[str, tuple[float, float]], ...] = tuple(
    (_merge_label(k), round_coord(lat, lon)) for k, lat, lon in _WAYPOINT_SPECS
)


# Mainland gateways with direct scheduled service FROM each island (see module doc).


GUERNSEY_UK_GATEWAYS: frozenset[str] = frozenset(
    {
        "bhx_airport",
        "brs_airport",
        "ext_airport",
        "lgw_airport",
        "lhr_airport",
        "lcy_airport",
        "man_airport",
        "sou_airport",
    }
)

JERSEY_UK_GATEWAYS: frozenset[str] = frozenset(
    {
        "bhx_airport",
        "brs_airport",
        "ema_airport",
        "ext_airport",
        "lba_airport",
        "lpl_airport",
        "lgw_airport",
        "lhr_airport",
        "ltn_airport",
        "man_airport",
        "ncl_airport",
        "nwi_airport",
        "sou_airport",
        "sen_airport",
    }
)

# IoM gateways from published Ronaldsway route lists (verify periodically).
IOM_UK_GATEWAYS: frozenset[str] = frozenset(
    {
        "bhx_airport",
        "brs_airport",
        "lpl_airport",
        "lcy_airport",
        "lgw_airport",
        "lhr_airport",
        "ltn_airport",
        "man_airport",
        "nqy_airport",
        "sou_airport",
    }
)


def mainland_gateway_keys_for_region(
    region: t.Literal["jersey", "guernsey", "isle_of_man"],
) -> tuple[str, ...]:
    g = {
        "jersey": JERSEY_UK_GATEWAYS,
        "guernsey": GUERNSEY_UK_GATEWAYS,
        "isle_of_man": IOM_UK_GATEWAYS,
    }[region]
    return tuple(sorted(g))


OffshoreRegion = t.Literal["mainland", "jersey", "guernsey", "isle_of_man"]

# Bounding boxes (degrees). Order of checks separates Jersey ↔ Guernsey.
_JERSEY_BOX = (49.10, 49.38, -2.60, -1.95)
_GUERNSEY_BOX = (49.38, 49.76, -2.75, -2.12)
_ISLE_OF_MAN_BOX = (53.97, 54.48, -5.10, -4.00)

_REGION_EXPORT_JS: dict[str, tuple[float, float, float, float]] = {
    "jersey": _JERSEY_BOX,
    "guernsey": _GUERNSEY_BOX,
    "isle_of_man": _ISLE_OF_MAN_BOX,
}

AVG_UK_DRIVE_KMH = 55.0

AIR_MIN_CI_MAINLAND = 195  # Ci ↔ mainland (block + waits; hub choice is ground-only)
AIR_MIN_IOM_MAINLAND = 155
AIR_MIN_JER_GCI = 150  # inter-bailiwick
AIR_MIN_MIXED_CD = 300  # rare Jersey↔IoM or Guernsey↔IoM


def classify_region(lat: float, lon: float) -> OffshoreRegion:
    lo, hi, w, e = _JERSEY_BOX
    if lo <= lat <= hi and w <= lon <= e:
        return "jersey"
    lo, hi, w, e = _GUERNSEY_BOX
    if lo <= lat <= hi and w <= lon <= e:
        return "guernsey"
    lo, hi, w, e = _ISLE_OF_MAN_BOX
    if lo <= lat <= hi and w <= lon <= e:
        return "isle_of_man"
    return "mainland"


def air_minutes_between_regions(a: OffshoreRegion, b: OffshoreRegion) -> int:
    if a == "mainland" or b == "mainland":
        offshore = b if a == "mainland" else a
        if offshore in ("jersey", "guernsey"):
            return AIR_MIN_CI_MAINLAND
        if offshore == "isle_of_man":
            return AIR_MIN_IOM_MAINLAND
        return AIR_MIN_CI_MAINLAND
    if {a, b} == {"jersey", "guernsey"}:
        return AIR_MIN_JER_GCI
    return AIR_MIN_MIXED_CD


def local_airport_key(region: t.Literal["jersey", "guernsey", "isle_of_man"]) -> str:
    return {"jersey": "jer_airport", "guernsey": "gci_airport", "isle_of_man": "iom_airport"}[
        region
    ]


def _merge_meta(team_label: str, coord_key: tuple[float, float]) -> dict[str, str]:
    lat, lon = coord_key
    return {
        "team": team_label,
        "league": "__routing_waypoint__",
        "address": f"Waypoint {team_label}, {lat},{lon}",
    }


def augment_coord_meta_for_routing_waypoints(
    coord_meta: dict[tuple[float, float], list[dict[str, str]]],
) -> None:
    """Insert synthetic waypoint markers into ``coord_meta`` if missing."""
    for label, ck in WAYPOINT_MERGE_ROWS:
        coord_meta.setdefault(ck, []).append(_merge_meta(label, ck))


def offshore_js_payload(rid_by_key: dict[str, int | None]) -> dict[str, t.Any]:
    """Fragment embedded in distances.js ``ROUTED_DISTANCES.offshore``."""

    pts: dict[str, dict[str, float | int | None]] = {}
    for key, ck in WAYPOINT_BY_KEY.items():
        entry: dict[str, float | int | None] = {"lat": ck[0], "lon": ck[1]}
        rid = rid_by_key.get(key)
        if rid is not None:
            entry["rid"] = int(rid)
        pts[key] = entry

    gateways = {
        "jersey": tuple(sorted(JERSEY_UK_GATEWAYS)),
        "guernsey": tuple(sorted(GUERNSEY_UK_GATEWAYS)),
        "isle_of_man": tuple(sorted(IOM_UK_GATEWAYS)),
    }

    air = {
        "ci_mainland": AIR_MIN_CI_MAINLAND,
        "iom_mainland": AIR_MIN_IOM_MAINLAND,
        "jersey_guernsey": AIR_MIN_JER_GCI,
        "mixed_crown_dependency": AIR_MIN_MIXED_CD,
        "drive_kmh_assumed": AVG_UK_DRIVE_KMH,
    }

    return {
        "waypoints": pts,
        "gateways": gateways,
        "air_minutes": air,
        "regions": {k: list(v) for k, v in _REGION_EXPORT_JS.items()},
    }


def build_rid_map_from_lookup(
    coord_to_id_cb: t.Callable[[float, float], int | None],
) -> dict[str, int | None]:
    rid_by_key: dict[str, int | None] = {}
    for key, (lat, lon) in WAYPOINT_BY_KEY.items():
        rid_by_key[key] = coord_to_id_cb(lat, lon)
    return rid_by_key
