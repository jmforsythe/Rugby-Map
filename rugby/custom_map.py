"""
Export a deduplicated team catalogue, boundary data, and assembled HTML page
for the custom map builder.

Walks all seasons under data/rugby/geocoded_teams/, keeps the most recently
seen version of each team (by season sort order) with valid lat/lng, runs
point-in-polygon to assign ITL2/ITL3/LAD regions to each team, and writes:

- teams.js       — team catalogue with pre-computed region assignments
- boundaries.js  — England-only simplified boundary geometries + hierarchy
- index.html     — assembled SPA from rugby/custom_map_assets/ template
"""

import argparse
import json
import logging
import re
from pathlib import Path

from shapely.geometry import Point, mapping, shape
from shapely.prepared import prep

from core import GeocodedLeague, TravelDistances, get_config, set_config, setup_logging
from core.config import BOUNDARIES_DIR, DIST_DIR, get_favicon_html, get_google_analytics_script
from rugby import DATA_DIR
from rugby.analysis.projected_urls import _parse_projected_md
from rugby.tiers import extract_tier, get_competition_offset, mens_current_tier_name

logger = logging.getLogger(__name__)

GEOCODED_DIR = DATA_DIR / "geocoded_teams"
DISTANCE_CACHE_DIR = DATA_DIR / "distance_cache"
OUTPUT_DIR = DIST_DIR / "custom-map"
TEMPLATE_DIR = Path(__file__).resolve().parent / "custom_map_assets"

SIMPLIFY_TOLERANCE = 0.001

BOUNDARY_PATHS = {
    "itl1": BOUNDARIES_DIR / "ITL_1.geojson",
    "itl2": BOUNDARIES_DIR / "ITL_2.geojson",
    "itl3": BOUNDARIES_DIR / "ITL_3.geojson",
    "lad": BOUNDARIES_DIR / "local_authority_districts.geojson",
    "wards": BOUNDARIES_DIR / "wards.geojson",
    "countries": BOUNDARIES_DIR / "countries.geojson",
}

COUNTRY_OUTLINES = ["England", "Isle of Man", "Jersey", "Guernsey"]

WARD_SIMPLIFY_TOLERANCE = 0.001

# ITL code prefixes for England + Crown Dependencies
_ENGLAND_ITL_PREFIXES = frozenset(
    {"TLC", "TLD", "TLE", "TLF", "TLG", "TLH", "TLI", "TLJ", "TLK", "GGY", "IMN", "JEY"}
)


# ---------------------------------------------------------------------------
# Boundary loading
# ---------------------------------------------------------------------------


def _load_geojson(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_regions(path: Path, code_key: str, name_key: str) -> dict[str, dict]:
    """Load GeoJSON features into region dicts keyed by name."""
    data = _load_geojson(path)
    regions: dict[str, dict] = {}
    for feat in data["features"]:
        props = feat["properties"]
        code = props.get(code_key, "")
        name = props.get(name_key, "")
        if not code or not name:
            continue
        geom = shape(feat["geometry"])
        regions[name] = {
            "name": name,
            "code": code,
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }
    return regions


def _load_lad_regions(path: Path) -> dict[str, dict]:
    """Load LAD regions keyed by LAD code."""
    data = _load_geojson(path)
    regions: dict[str, dict] = {}
    for feat in data["features"]:
        props = feat["properties"]
        code = props.get("LAD25CD", "")
        name = props.get("LAD25NM", "")
        if not code:
            continue
        geom = shape(feat["geometry"])
        regions[code] = {
            "name": name,
            "code": code,
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }
    return regions


def _load_ward_regions(path: Path) -> tuple[dict[str, dict], dict[str, str | None]]:
    """Load ward regions keyed by ward code, plus ward→LAD parent mapping."""
    data = _load_geojson(path)
    regions: dict[str, dict] = {}
    ward_to_lad: dict[str, str | None] = {}
    for feat in data["features"]:
        props = feat["properties"]
        code = props.get("WD25CD", "")
        name = props.get("WD25NM", "")
        if not code:
            continue
        geom = shape(feat["geometry"])
        regions[code] = {
            "name": name,
            "code": code,
            "geom": geom,
            "prepared": prep(geom),
            "centroid": geom.centroid,
        }
        ward_to_lad[code] = props.get("LAD25CD")
    return regions, ward_to_lad


def _build_hierarchy(
    itl1_regions: dict[str, dict],
    itl2_regions: dict[str, dict],
    itl3_regions: dict[str, dict],
    lad_regions: dict[str, dict],
    ward_to_lad: dict[str, str | None] | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[str]], dict[str, list[str]]]:
    """Build downward hierarchy maps: itl1→itl2s, itl2→itl3s, itl3→lads, lad→wards."""
    itl1_by_code = {r["code"]: r["name"] for r in itl1_regions.values() if r["code"]}
    itl2_by_code = {r["code"]: r["name"] for r in itl2_regions.values() if r["code"]}

    itl3_to_itl2: dict[str, str] = {}
    for itl3 in itl3_regions.values():
        if itl3["code"] and len(itl3["code"]) >= 4:
            parent_code = itl3["code"][:4]
            if parent_code in itl2_by_code:
                itl3_to_itl2[itl3["name"]] = itl2_by_code[parent_code]

    itl2_to_itl1: dict[str, str] = {}
    for itl2 in itl2_regions.values():
        if itl2["code"] and len(itl2["code"]) >= 3:
            parent_code = itl2["code"][:3]
            if parent_code in itl1_by_code:
                itl2_to_itl1[itl2["name"]] = itl1_by_code[parent_code]

    itl1_to_itl2s: dict[str, list[str]] = {}
    for itl2_name, itl1_name in itl2_to_itl1.items():
        itl1_to_itl2s.setdefault(itl1_name, []).append(itl2_name)

    itl2_to_itl3s: dict[str, list[str]] = {}
    for itl3_name, itl2_name in itl3_to_itl2.items():
        itl2_to_itl3s.setdefault(itl2_name, []).append(itl3_name)

    itl3_to_lads: dict[str, list[str]] = {}
    for lad_code, lad in lad_regions.items():
        centroid = lad["centroid"]
        found_itl1 = None
        for itl1 in itl1_regions.values():
            if itl1["prepared"].contains(centroid):
                found_itl1 = itl1["name"]
                break
        if not found_itl1:
            continue
        found_itl2 = None
        for itl2_name in itl1_to_itl2s.get(found_itl1, []):
            if itl2_regions[itl2_name]["prepared"].contains(centroid):
                found_itl2 = itl2_name
                break
        if not found_itl2:
            continue
        for itl3_name in itl2_to_itl3s.get(found_itl2, []):
            if itl3_regions[itl3_name]["prepared"].contains(centroid):
                itl3_to_lads.setdefault(itl3_name, []).append(lad_code)
                break

    lad_to_wards: dict[str, list[str]] = {}
    if ward_to_lad:
        for ward_code, parent_lad in ward_to_lad.items():
            if parent_lad and parent_lad in lad_regions:
                lad_to_wards.setdefault(parent_lad, []).append(ward_code)

    return itl2_to_itl3s, itl3_to_lads, itl1_to_itl2s, lad_to_wards


def _assign_team_regions(
    lat: float,
    lng: float,
    itl1_regions: dict[str, dict],
    itl2_regions: dict[str, dict],
    itl3_regions: dict[str, dict],
    lad_regions: dict[str, dict],
    ward_regions: dict[str, dict],
    itl1_to_itl2s: dict[str, list[str]],
    itl2_to_itl3s: dict[str, list[str]],
    itl3_to_lads: dict[str, list[str]],
    lad_to_wards: dict[str, list[str]],
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Point-in-polygon assignment: returns (itl1_name, itl2_name, itl3_name, lad_code, ward_code)."""
    point = Point(lng, lat)

    found_itl1 = None
    for itl1 in itl1_regions.values():
        if itl1["prepared"].contains(point):
            found_itl1 = itl1["name"]
            break
    if not found_itl1:
        return None, None, None, None, None

    found_itl2 = None
    for itl2_name in itl1_to_itl2s.get(found_itl1, []):
        if itl2_regions[itl2_name]["prepared"].contains(point):
            found_itl2 = itl2_name
            break
    if not found_itl2:
        return found_itl1, None, None, None, None

    found_itl3 = None
    for itl3_name in itl2_to_itl3s.get(found_itl2, []):
        if itl3_regions[itl3_name]["prepared"].contains(point):
            found_itl3 = itl3_name
            break
    if not found_itl3:
        return found_itl1, found_itl2, None, None, None

    found_lad = None
    for lad_code in itl3_to_lads.get(found_itl3, []):
        lad = lad_regions.get(lad_code)
        if lad and lad["prepared"].contains(point):
            found_lad = lad_code
            break
    if not found_lad:
        return found_itl1, found_itl2, found_itl3, None, None

    found_ward = None
    for ward_code in lad_to_wards.get(found_lad, []):
        ward = ward_regions.get(ward_code)
        if ward and ward["prepared"].contains(point):
            found_ward = ward_code
            break

    return found_itl1, found_itl2, found_itl3, found_lad, found_ward


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def _load_latest_distances() -> TravelDistances | None:
    """Load the distance cache for the most recent season available."""
    if not DISTANCE_CACHE_DIR.exists():
        return None
    cache_files = sorted(DISTANCE_CACHE_DIR.glob("*.json"))
    if not cache_files:
        return None
    latest = cache_files[-1]
    logger.info("Loading distance cache from %s", latest.name)
    with open(latest, encoding="utf-8") as f:
        return json.load(f)


def _collect_teams(
    distances: TravelDistances | None,
    itl1_regions: dict[str, dict],
    itl2_regions: dict[str, dict],
    itl3_regions: dict[str, dict],
    lad_regions: dict[str, dict],
    ward_regions: dict[str, dict],
    itl1_to_itl2s: dict[str, list[str]],
    itl2_to_itl3s: dict[str, list[str]],
    itl3_to_lads: dict[str, list[str]],
    lad_to_wards: dict[str, list[str]],
) -> list[dict]:
    """Walk every season and return deduplicated teams (latest season wins)."""
    seasons = sorted(
        [d.name for d in GEOCODED_DIR.iterdir() if d.is_dir()],
    )

    dist_teams = distances["teams"] if distances else {}
    dist_leagues = distances["leagues"] if distances else {}

    seen: dict[str, dict] = {}

    for season in seasons:
        season_dir = GEOCODED_DIR / season
        for json_path in sorted(season_dir.rglob("*.json")):
            rel_path = json_path.relative_to(season_dir).as_posix()
            rel_parts = list(json_path.relative_to(season_dir).parts)
            is_merit = len(rel_parts) >= 3 and rel_parts[0] == "merit"

            local_tier_num, local_tier_name = extract_tier(rel_path, season)
            if is_merit:
                comp_key = rel_parts[1]
                comp_display = comp_key.replace("_", " ")
                offset = get_competition_offset(comp_key, season)
                abs_tier = local_tier_num + offset
                abs_tier_name = mens_current_tier_name(abs_tier, season)
            else:
                comp_display = ""
                local_tier_name = ""
                abs_tier = local_tier_num
                abs_tier_name = extract_tier(rel_path, season)[1]

            try:
                with open(json_path, encoding="utf-8") as f:
                    league: GeocodedLeague = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Skipping %s: %s", json_path, exc)
                continue

            for team in league.get("teams", []):
                lat = team.get("latitude")
                lng = team.get("longitude")
                if lat is None or lng is None:
                    continue

                name = team["name"]
                entry: dict = {
                    "n": name,
                    "lat": round(lat, 5),
                    "lng": round(lng, 5),
                    "img": team.get("image_url") or "",
                    "addr": team.get("address") or "",
                    "t": abs_tier,
                    "tn": abs_tier_name,
                }
                if is_merit:
                    entry["mc"] = comp_display
                    entry["ml"] = local_tier_name

                td = dist_teams.get(name)
                if td:
                    league_name = td.get("league", "")
                    ld = dist_leagues.get(league_name)
                    entry["lg"] = league_name
                    entry["tavg"] = td.get("avg_distance_km", 0)
                    entry["ttot"] = td.get("total_distance_km", 0)
                    if ld:
                        entry["lavg"] = round(ld.get("avg_distance_km", 0), 2)

                seen[name] = entry

    teams = sorted(seen.values(), key=lambda t: t["n"])

    assigned = 0
    ward_assigned = 0
    for team in teams:
        itl1, itl2, itl3, lad, ward = _assign_team_regions(
            team["lat"],
            team["lng"],
            itl1_regions,
            itl2_regions,
            itl3_regions,
            lad_regions,
            ward_regions,
            itl1_to_itl2s,
            itl2_to_itl3s,
            itl3_to_lads,
            lad_to_wards,
        )
        if itl1:
            team["r1"] = itl1
        if itl2:
            team["r2"] = itl2
        if itl3:
            team["r3"] = itl3
        if lad:
            team["rl"] = lad
        if ward:
            team["rw"] = ward
        if itl1:
            assigned += 1
        if ward:
            ward_assigned += 1

    logger.info(
        "Assigned %d of %d teams to ITL regions (%d to wards)", assigned, len(teams), ward_assigned
    )
    return teams


# ---------------------------------------------------------------------------
# Boundary export
# ---------------------------------------------------------------------------


def _is_england(code: str, level: str) -> bool:
    """Check if a region code belongs to England or Crown Dependencies."""
    if level == "lad":
        return code.startswith("E")
    return code[:3] in _ENGLAND_ITL_PREFIXES


def _export_boundaries(
    itl1_regions: dict[str, dict],
    itl2_regions: dict[str, dict],
    itl3_regions: dict[str, dict],
    lad_regions: dict[str, dict],
    ward_regions: dict[str, dict],
    itl1_to_itl2s: dict[str, list[str]],
    itl2_to_itl3s: dict[str, list[str]],
    itl3_to_lads: dict[str, list[str]],
    lad_to_wards: dict[str, list[str]],
) -> None:
    """Write boundaries.js with simplified England-only geometries and hierarchy."""

    def _region_entry(region: dict, tolerance: float = SIMPLIFY_TOLERANCE) -> dict:
        simplified = region["geom"].simplify(tolerance, preserve_topology=True)
        c = region["centroid"]
        return {
            "geom": mapping(simplified),
            "centroid": [round(c.x, 4), round(c.y, 4)],
        }

    bd: dict = {
        "countries": {},
        "itl0": {},
        "itl1": {},
        "itl2": {},
        "itl3": {},
        "lad": {},
        "ward": {},
    }

    country_prepared: dict = {}
    countries_path = BOUNDARY_PATHS.get("countries")
    if countries_path and countries_path.exists():
        countries_data = _load_geojson(countries_path)
        for feat in countries_data.get("features", []):
            name = feat["properties"].get("CTRY24NM", "")
            if name in COUNTRY_OUTLINES:
                geom = shape(feat["geometry"]).simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)
                geom_dict = mapping(geom)
                c = geom.centroid
                bd["countries"][name] = {"geom": geom_dict}
                bd["itl0"][name] = {
                    "geom": geom_dict,
                    "centroid": [round(c.x, 4), round(c.y, 4)],
                }
                country_prepared[name] = prep(geom)

    for name, r in itl1_regions.items():
        if _is_england(r["code"], "itl"):
            bd["itl1"][name] = _region_entry(r)

    for name, r in itl2_regions.items():
        if _is_england(r["code"], "itl"):
            bd["itl2"][name] = _region_entry(r)

    for name, r in itl3_regions.items():
        if _is_england(r["code"], "itl"):
            bd["itl3"][name] = _region_entry(r)

    for code, r in lad_regions.items():
        if _is_england(code, "lad"):
            bd["lad"][code] = _region_entry(r)

    for code, r in ward_regions.items():
        if _is_england(code, "lad"):
            bd["ward"][code] = _region_entry(r, WARD_SIMPLIFY_TOLERANCE)

    itl0_to_itl1s: dict[str, list[str]] = {}
    for c_name, c_prep in country_prepared.items():
        for itl1_name in bd["itl1"]:
            if c_prep.contains(itl1_regions[itl1_name]["centroid"]):
                itl0_to_itl1s.setdefault(c_name, []).append(itl1_name)
    bd["itl0_to_itl1s"] = itl0_to_itl1s

    bd["itl1_to_itl2s"] = {
        k: [v2 for v2 in v if v2 in bd["itl2"]] for k, v in itl1_to_itl2s.items() if k in bd["itl1"]
    }
    bd["itl2_to_itl3s"] = {k: v for k, v in itl2_to_itl3s.items() if k in bd["itl2"]}
    bd["itl3_to_lads"] = {
        k: [lc for lc in v if lc in bd["lad"]] for k, v in itl3_to_lads.items() if k in bd["itl3"]
    }
    bd["lad_to_wards"] = {
        k: [wc for wc in v if wc in bd["ward"]] for k, v in lad_to_wards.items() if k in bd["lad"]
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "boundaries.js"
    payload = json.dumps(bd, separators=(",", ":"), ensure_ascii=False)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by rugby.custom_map — do not edit\n")
        f.write("var BOUNDARIES = ")
        f.write(payload)
        f.write(";\n")

    size_mb = output_path.stat().st_size / 1024 / 1024
    logger.info(
        "Wrote %s (%.1f MB, %d ITL1 + %d ITL2 + %d ITL3 + %d LAD + %d ward regions)",
        output_path,
        size_mb,
        len(bd["itl1"]),
        len(bd["itl2"]),
        len(bd["itl3"]),
        len(bd["lad"]),
        len(bd["ward"]),
    )


# ---------------------------------------------------------------------------
# Projected tiers export
# ---------------------------------------------------------------------------


def _find_latest_projected_md() -> Path | None:
    """Return the most recent ``data/rugby/projected_*.md`` file, or *None*."""
    candidates = sorted(DATA_DIR.glob("projected_*.md"))
    return candidates[-1] if candidates else None


def _write_projected_js() -> None:
    """Parse the latest projected-leagues markdown and write ``projected.js``."""
    md_path = _find_latest_projected_md()
    if md_path is None:
        logger.info("No projected markdown found — skipping projected.js")
        output_path = OUTPUT_DIR / "projected.js"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("// Auto-generated by rugby.custom_map — do not edit\n")
            f.write("var PROJECTED_DATA = null;\n")
        return

    season_match = re.search(r"projected[_-](\d{4}[_-]\d{4})", md_path.stem)
    season = season_match.group(1).replace("_", "-") if season_match else "unknown"

    logger.info("Parsing projected markdown %s (season %s)", md_path.name, season)
    tiers_dict = _parse_projected_md(str(md_path))

    tiers_list: list[dict] = []
    for tier_num in sorted(tiers_dict):
        tier_name = mens_current_tier_name(tier_num)
        leagues = [
            {"name": league_name, "teams": team_names}
            for league_name, team_names in tiers_dict[tier_num]
        ]
        tiers_list.append(
            {
                "tier": tier_num,
                "name": f"Tier {tier_num} \u2014 {tier_name}",
                "leagues": leagues,
            }
        )

    payload_obj = {"season": season, "tiers": tiers_list}
    payload = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=False)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "projected.js"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by rugby.custom_map — do not edit\n")
        f.write("var PROJECTED_DATA = ")
        f.write(payload)
        f.write(";\n")

    total_teams = sum(len(t) for lg in tiers_dict.values() for _, t in lg)
    logger.info(
        "Wrote %s (%d tiers, %d teams, season %s)",
        output_path,
        len(tiers_list),
        total_teams,
        season,
    )


# ---------------------------------------------------------------------------
# HTML page assembly
# ---------------------------------------------------------------------------


def _build_page() -> None:
    """Read the HTML template and write the assembled index.html to OUTPUT_DIR."""
    template_path = TEMPLATE_DIR / "index.html"
    if not template_path.exists():
        logger.error("Template not found: %s", template_path)
        return

    template = template_path.read_text(encoding="utf-8")

    is_prod = get_config().is_production
    home_href = "/" if is_prod else "../index.html"

    replacements = {
        "{{GA_SCRIPT}}": get_google_analytics_script(),
        "{{FAVICON_HTML}}": get_favicon_html(depth=1),
        "{{HOME_HREF}}": home_href,
    }
    html = template
    for token, value in replacements.items():
        html = html.replace(token, value)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "index.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("Wrote %s", output_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export team catalogue and boundary data for the custom map builder."
    )
    parser.add_argument(
        "--production", action="store_true", help="Use production paths (absolute /)"
    )
    args = parser.parse_args()
    if args.production:
        set_config(is_production=True)

    setup_logging()

    if not GEOCODED_DIR.exists():
        logger.error("Geocoded data directory not found: %s", GEOCODED_DIR)
        return

    core_boundary_keys = ("itl1", "itl2", "itl3", "lad")
    has_boundaries = all(BOUNDARY_PATHS[k].exists() for k in core_boundary_keys)
    if not has_boundaries:
        logger.warning(
            "Boundary files not found in %s — run 'make boundaries' first. "
            "Skipping ITL assignment and boundary export.",
            BOUNDARIES_DIR,
        )

    itl1_regions: dict[str, dict] = {}
    itl2_regions: dict[str, dict] = {}
    itl3_regions: dict[str, dict] = {}
    lad_regions: dict[str, dict] = {}
    ward_regions: dict[str, dict] = {}
    ward_to_lad: dict[str, str | None] = {}
    itl1_to_itl2s: dict[str, list[str]] = {}
    itl2_to_itl3s: dict[str, list[str]] = {}
    itl3_to_lads: dict[str, list[str]] = {}
    lad_to_wards: dict[str, list[str]] = {}

    if has_boundaries:
        logger.info("Loading boundary data...")
        itl1_regions = _load_regions(BOUNDARY_PATHS["itl1"], "ITL125CD", "ITL125NM")
        itl2_regions = _load_regions(BOUNDARY_PATHS["itl2"], "ITL225CD", "ITL225NM")
        itl3_regions = _load_regions(BOUNDARY_PATHS["itl3"], "ITL325CD", "ITL325NM")
        lad_regions = _load_lad_regions(BOUNDARY_PATHS["lad"])
        if BOUNDARY_PATHS["wards"].exists():
            logger.info("Loading ward boundaries...")
            ward_regions, ward_to_lad = _load_ward_regions(BOUNDARY_PATHS["wards"])
        else:
            logger.warning(
                "Wards file not found at %s, skipping ward-level data", BOUNDARY_PATHS["wards"]
            )
        logger.info(
            "Loaded %d ITL1, %d ITL2, %d ITL3, %d LAD, %d ward regions",
            len(itl1_regions),
            len(itl2_regions),
            len(itl3_regions),
            len(lad_regions),
            len(ward_regions),
        )

        logger.info("Building hierarchy...")
        itl2_to_itl3s, itl3_to_lads, itl1_to_itl2s, lad_to_wards = _build_hierarchy(
            itl1_regions,
            itl2_regions,
            itl3_regions,
            lad_regions,
            ward_to_lad,
        )

    logger.info("Scanning geocoded teams in %s", GEOCODED_DIR)
    distances = _load_latest_distances()
    teams = _collect_teams(
        distances,
        itl1_regions,
        itl2_regions,
        itl3_regions,
        lad_regions,
        ward_regions,
        itl1_to_itl2s,
        itl2_to_itl3s,
        itl3_to_lads,
        lad_to_wards,
    )
    logger.info("Found %d unique teams with coordinates", len(teams))

    if has_boundaries:
        countries_path = BOUNDARY_PATHS.get("countries")
        if countries_path and countries_path.exists():
            itl1_to_country: dict[str, str] = {}
            countries_data = _load_geojson(countries_path)
            for feat in countries_data.get("features", []):
                cname = feat["properties"].get("CTRY24NM", "")
                if cname in COUNTRY_OUTLINES:
                    c_prep = prep(shape(feat["geometry"]))
                    for itl1_name, itl1 in itl1_regions.items():
                        if c_prep.contains(itl1["centroid"]):
                            itl1_to_country[itl1_name] = cname
            country_assigned = 0
            for team in teams:
                r1 = team.get("r1")
                if r1 and r1 in itl1_to_country:
                    team["r0"] = itl1_to_country[r1]
                    country_assigned += 1
            logger.info("Assigned %d teams to countries via ITL1", country_assigned)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Write teams.js
    output_path = OUTPUT_DIR / "teams.js"
    payload = json.dumps(teams, separators=(",", ":"), ensure_ascii=False)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by rugby.custom_map — do not edit\n")
        f.write("var TEAMS_DATA = ")
        f.write(payload)
        f.write(";\n")

    size_kb = output_path.stat().st_size / 1024
    logger.info("Wrote %s (%.1f KB, %d teams)", output_path, size_kb, len(teams))

    # Write projected.js (predicted tier compositions)
    _write_projected_js()

    # Write boundaries.js
    if has_boundaries:
        _export_boundaries(
            itl1_regions,
            itl2_regions,
            itl3_regions,
            lad_regions,
            ward_regions,
            itl1_to_itl2s,
            itl2_to_itl3s,
            itl3_to_lads,
            lad_to_wards,
        )

    # Write index.html from template
    _build_page()


if __name__ == "__main__":
    main()
