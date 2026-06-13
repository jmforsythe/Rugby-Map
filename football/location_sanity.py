"""Sanity checks for geocoded football team locations within a league."""

from __future__ import annotations

import argparse
import json
import logging
import statistics
from pathlib import Path
from typing import Any

from core.config import REPO_ROOT
from football import DATA_DIR
from rugby.distances import distance as haversine_km

logger = logging.getLogger(__name__)

DEFAULT_SIGMA = 2.0
DEFAULT_MIN_TEAMS = 4

# Outlier review (sigma=2) catches ~250 teams for manual inspection.
# Definite wrong geocodes — force alternate geocoding methods:
#   nominatim_name at z>=5: club-name fallback almost always wrong place
#   any other source at z>=6 and km>=80: extreme distance, likely wrong venue
RECALC_NOMINATIM_MIN_Z = 5.0
RECALC_OTHER_MIN_Z = 6.0
RECALC_OTHER_MIN_KM = 80.0

OFFSHORE_TERRITORIES = frozenset({"Guernsey", "Jersey", "Isle of Man"})
OFFSHORE_TEAM_NAMES = frozenset({"Guernsey", "Jersey Bulls", "F.C. Isle of Man"})


def _geocoded_teams(teams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Teams with valid coordinates and no geocoding error."""
    out: list[dict[str, Any]] = []
    for team in teams:
        if team.get("error"):
            continue
        lat, lon = team.get("latitude"), team.get("longitude")
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            out.append(team)
    return out


def league_centroid(teams: list[dict[str, Any]]) -> tuple[float, float] | None:
    """Return mean lat/lon for geocoded teams, or None if none exist."""
    geocoded = _geocoded_teams(teams)
    if not geocoded:
        return None
    lat = statistics.mean(float(t["latitude"]) for t in geocoded)
    lon = statistics.mean(float(t["longitude"]) for t in geocoded)
    return lat, lon


def _peer_teams(league: dict[str, Any], team_index: int) -> list[dict[str, Any]]:
    peers: list[dict[str, Any]] = []
    for index, team in enumerate(league.get("teams", [])):
        if index == team_index:
            continue
        if team.get("error"):
            continue
        lat, lon = team.get("latitude"), team.get("longitude")
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            peers.append(team)
    return peers


def league_distance_stats(
    teams: list[dict[str, Any]],
    *,
    min_teams: int = DEFAULT_MIN_TEAMS,
) -> tuple[tuple[float, float], float, float] | None:
    """Return ``(centroid, mean_distance_km, stdev_distance_km)`` for geocoded teams."""
    geocoded = _geocoded_teams(teams)
    if len(geocoded) < min_teams:
        return None

    centroid = league_centroid(geocoded)
    assert centroid is not None
    clat, clon = centroid

    dist_values = [
        haversine_km(clat, clon, float(team["latitude"]), float(team["longitude"]))
        for team in geocoded
    ]
    median_dist = statistics.median(dist_values)
    baseline_cutoff = max(median_dist * 1.5, 15.0)
    baseline = [dist for dist in dist_values if dist <= baseline_cutoff]
    if len(baseline) < 2:
        baseline = dist_values
    if len(baseline) < 2:
        return None

    mean_dist = statistics.mean(baseline)
    stdev_dist = statistics.stdev(baseline)
    if stdev_dist == 0:
        return None

    return centroid, mean_dist, stdev_dist


def pick_best_league_geocode(
    candidates: list[dict[str, Any]],
    league: dict[str, Any],
    team_index: int,
    *,
    min_peers: int = DEFAULT_MIN_TEAMS,
) -> dict[str, Any] | None:
    """Pick the candidate with the lowest distance z-score relative to league peers."""
    if not candidates:
        return None

    stats = league_distance_stats(_peer_teams(league, team_index), min_teams=min_peers)
    if stats is None:
        return candidates[0]

    (clat, clon), mean_dist, stdev_dist = stats

    def z_score(candidate: dict[str, Any]) -> float:
        dist = haversine_km(
            clat,
            clon,
            float(candidate["latitude"]),
            float(candidate["longitude"]),
        )
        return (dist - mean_dist) / stdev_dist

    return min(candidates, key=z_score)


def team_geocode_source(team: dict[str, Any]) -> str | None:
    """Return the geocoding method used for a team, if known."""
    source = team.get("geocode_source") or team.get("geocode_precision")
    return source if isinstance(source, str) and source else None


def is_offshore_team(team: dict[str, Any]) -> bool:
    """True for Crown dependencies whose coords are legitimately far from England."""
    territory = team.get("territory")
    if isinstance(territory, str) and territory in OFFSHORE_TERRITORIES:
        return True
    name = team.get("name")
    return isinstance(name, str) and name in OFFSHORE_TEAM_NAMES


def is_definitely_wrong_location(team: dict[str, Any]) -> bool:
    """True when coordinates are almost certainly a bad geocode, not a real outlier."""
    if team.get("error") or is_offshore_team(team):
        return False
    z = team.get("centroid_distance_z")
    km = team.get("centroid_distance_km")
    if not isinstance(z, int | float) or not isinstance(km, int | float):
        return False
    source = team_geocode_source(team)
    if source in ("nominatim_name", "nominatim_club") and z >= RECALC_NOMINATIM_MIN_Z:
        return True
    if z >= RECALC_OTHER_MIN_Z and km >= RECALC_OTHER_MIN_KM:
        return True
    return False


def flag_league_location_outliers(
    league: dict[str, Any],
    *,
    sigma: float = DEFAULT_SIGMA,
    min_teams: int = DEFAULT_MIN_TEAMS,
) -> list[dict[str, Any]]:
    """Flag teams whose distance from the league centroid exceeds mean + sigma*stdev.

    Spread is measured from a baseline of in-league distances (<= 1.5x median,
    minimum 15km) so a single bad geocode does not inflate the threshold.

    Mutates matching team dicts with ``location_outlier``, ``centroid_distance_km``,
    and ``centroid_distance_z``. Returns the list of flagged teams.
    """
    geocoded = _geocoded_teams(league.get("teams", []))
    for team in league.get("teams", []):
        team.pop("location_outlier", None)
        team.pop("centroid_distance_km", None)
        team.pop("centroid_distance_z", None)

    if len(geocoded) < min_teams:
        return []

    centroid = league_centroid(geocoded)
    assert centroid is not None
    clat, clon = centroid

    distances: list[tuple[dict[str, Any], float]] = []
    for team in geocoded:
        dist = haversine_km(clat, clon, float(team["latitude"]), float(team["longitude"]))
        distances.append((team, dist))

    stats = league_distance_stats(geocoded)
    if stats is None:
        return []

    _centroid, mean_dist, stdev_dist = stats
    threshold = mean_dist + sigma * stdev_dist
    outliers: list[dict[str, Any]] = []
    for team, dist in distances:
        z = (dist - mean_dist) / stdev_dist
        if dist > threshold:
            team["location_outlier"] = True
            team["centroid_distance_km"] = round(dist, 2)
            team["centroid_distance_z"] = round(z, 2)
            outliers.append(team)

    league.pop("location_sanity", None)
    if outliers:
        league["location_sanity"] = {
            "centroid_latitude": round(clat, 6),
            "centroid_longitude": round(clon, 6),
            "mean_distance_km": round(mean_dist, 2),
            "stdev_distance_km": round(stdev_dist, 2),
            "threshold_km": round(threshold, 2),
            "sigma": sigma,
            "outlier_count": len(outliers),
        }

    return outliers


def scan_geocoded_dir(
    geo_dir: Path,
    *,
    sigma: float = DEFAULT_SIGMA,
    min_teams: int = DEFAULT_MIN_TEAMS,
) -> list[tuple[Path, dict[str, Any], list[dict[str, Any]]]]:
    """Scan JSON league files and return (path, league, outliers) tuples."""
    findings: list[tuple[Path, dict[str, Any], list[dict[str, Any]]]] = []
    for path in sorted(geo_dir.rglob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            league = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        outliers = flag_league_location_outliers(league, sigma=sigma, min_teams=min_teams)
        if outliers:
            findings.append((path, league, outliers))
    return findings


def _format_outlier_line(league_name: str, team: dict[str, Any]) -> str:
    address = team.get("address") or team.get("formatted_address") or ""
    source = team.get("geocode_source") or team.get("geocode_precision") or "?"
    return (
        f"  {team.get('name')}: {team.get('centroid_distance_km')}km "
        f"(z={team.get('centroid_distance_z')}) "
        f"[{source}] {address}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flag football teams whose coordinates are outliers for their league"
    )
    parser.add_argument("--season", default="2025-2026")
    parser.add_argument(
        "--subdir",
        default="",
        help="Optional subdir under geocoded_teams/<season>/ (pyramid, feeder, BSLFL)",
    )
    parser.add_argument(
        "--sigma",
        type=float,
        default=DEFAULT_SIGMA,
        help="Flag teams beyond mean + sigma*stdev from league centroid (default: 2.0)",
    )
    parser.add_argument(
        "--min-teams",
        type=int,
        default=DEFAULT_MIN_TEAMS,
        help="Minimum geocoded teams before outlier detection runs (default: 4)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    geo_root = DATA_DIR / "geocoded_teams" / args.season
    if args.subdir:
        geo_root = geo_root / args.subdir
    if not geo_root.is_dir():
        raise SystemExit(f"Directory not found: {geo_root}")

    findings = scan_geocoded_dir(geo_root, sigma=args.sigma, min_teams=args.min_teams)
    total_outliers = sum(len(outliers) for _, _, outliers in findings)

    for path, league, outliers in findings:
        league_name = league.get("league_name", path.stem)
        rel = path.relative_to(REPO_ROOT)
        sanity = league.get("location_sanity", {})
        logger.info(
            "%s (%d outlier%s, threshold %.1fkm, stdev %.1fkm)",
            rel,
            len(outliers),
            "" if len(outliers) == 1 else "s",
            sanity.get("threshold_km", 0),
            sanity.get("stdev_distance_km", 0),
        )
        for team in outliers:
            logger.info(_format_outlier_line(league_name, team))
            if is_definitely_wrong_location(team):
                logger.info("    -> definitely wrong (would force recalc)")

    logger.info(
        "Found %d outlier(s) in %d league(s) under %s",
        total_outliers,
        len(findings),
        geo_root.relative_to(REPO_ROOT),
    )


if __name__ == "__main__":
    main()
