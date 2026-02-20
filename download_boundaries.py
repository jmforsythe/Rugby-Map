"""
Script to download boundary GeoJSON files from ONS Open Geography portal.
Downloads ITL1, ITL2, ITL3, and Countries boundaries using pagination.

Uses native ESRI JSON format for faster downloads (no server-side conversion),
then converts to GeoJSON locally.

Detail levels available:
- BFE: Full Extent (most detailed, largest files)
- BFC: Full Clipped (detailed, clipped to coastline)
- BGC: Generalised Clipped (simplified, smaller files)
- BSC: Super Generalised Clipped (very simplified)
- BUC: Ultra Generalised Clipped (least detailed, smallest files)
"""

import argparse
import enum
import json
import time
from pathlib import Path

import requests


class DetailLevel(enum.Enum):
    BFE = "BFE"  # Full Extent (most detailed, largest files)
    BFC = "BFC"  # Full Clipped (detailed, clipped to coastline)
    BGC = "BGC"  # Generalised Clipped (simplified, smaller files) - default
    BSC = "BSC"  # Super Generalised Clipped (very simplified)
    BUC = "BUC"  # Ultra Generalised Clipped (least detailed, smallest files)


def ring_is_clockwise(ring):
    """
    Check if a ring is clockwise using the shoelace formula.
    In ESRI JSON format, exterior rings are clockwise, holes are counter-clockwise.
    In geographic coordinates, positive signed area = counter-clockwise, negative = clockwise.
    But ESRI defines them based on screen coordinates where Y increases downward.
    Returns True if exterior ring (ESRI clockwise), False if hole (ESRI counter-clockwise).
    """
    area = 0
    for i in range(len(ring) - 1):
        area += (ring[i + 1][0] - ring[i][0]) * (ring[i + 1][1] + ring[i][1])
    # In ESRI, positive area = exterior, negative = hole (opposite of mathematical convention)
    return area > 0


def esri_to_geojson_geometry(esri_geom, geometry_type):
    """
    Convert ESRI JSON geometry to GeoJSON geometry.

    Args:
        esri_geom: ESRI geometry object
        geometry_type: ESRI geometry type (e.g., 'esriGeometryPolygon')

    Returns:
        GeoJSON geometry object
    """
    if geometry_type == "esriGeometryPolygon":
        rings = esri_geom.get("rings", [])
        if not rings:
            return {"type": "Polygon", "coordinates": []}

        # Group rings into polygons based on ESRI winding order
        # ESRI: Clockwise (area > 0) = exterior ring, Counter-clockwise (area < 0) = hole
        # GeoJSON: Counter-clockwise = exterior, Clockwise = hole
        # So we need to reverse all rings!
        polygons = []
        current_polygon = None

        for ring in rings:
            if ring_is_clockwise(ring):  # ESRI exterior ring (clockwise)
                # Start new polygon
                if current_polygon is not None:
                    polygons.append(current_polygon)
                # Reverse ring for GeoJSON (counter-clockwise)
                current_polygon = [ring[::-1]]
            else:  # ESRI hole (counter-clockwise)
                if current_polygon is not None:
                    # Reverse ring for GeoJSON (clockwise)
                    current_polygon.append(ring[::-1])

        if current_polygon is not None:
            polygons.append(current_polygon)

        if len(polygons) == 1:
            return {"type": "Polygon", "coordinates": polygons[0]}
        else:
            return {"type": "MultiPolygon", "coordinates": polygons}

    elif geometry_type == "esriGeometryPolyline":
        paths = esri_geom.get("paths", [])
        if len(paths) == 1:
            return {"type": "LineString", "coordinates": paths[0]}
        else:
            return {"type": "MultiLineString", "coordinates": paths}

    elif geometry_type == "esriGeometryPoint":
        return {"type": "Point", "coordinates": [esri_geom.get("x"), esri_geom.get("y")]}
    else:
        # Fallback - return as-is
        return esri_geom


def esri_to_geojson_feature(esri_feature, geometry_type):
    """
    Convert ESRI JSON feature to GeoJSON feature.

    Args:
        esri_feature: ESRI feature object
        geometry_type: ESRI geometry type

    Returns:
        GeoJSON feature object
    """
    return {
        "type": "Feature",
        "properties": esri_feature.get("attributes", {}),
        "geometry": esri_to_geojson_geometry(esri_feature.get("geometry", {}), geometry_type),
    }


def countries_url(detail_level: DetailLevel) -> str:
    return f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Countries_December_2024_Boundaries_UK_{detail_level.value}/FeatureServer/0"


def itl1_url(detail_level: DetailLevel) -> str:
    if detail_level in [DetailLevel.BFE, DetailLevel.BFC, DetailLevel.BGC, DetailLevel.BUC]:
        return f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/ITL1_JAN_2025_UK_{detail_level.value}/FeatureServer/0"
    if detail_level == DetailLevel.BSC:
        return None  # ITL1 not available in BSC level


def itl2_url(detail_level: DetailLevel) -> str:
    if detail_level in [DetailLevel.BFE, DetailLevel.BFC, DetailLevel.BGC, DetailLevel.BUC]:
        return f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/ITL2_JAN_2025_UK_{detail_level.value}/FeatureServer/0"
    if detail_level == DetailLevel.BSC:
        return None  # ITL2 not available in BSC level


def itl3_url(detail_level: DetailLevel) -> str:
    if detail_level in [DetailLevel.BFE]:
        return f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/ITL3_JAN_2025_UK_{detail_level.value}_V4/FeatureServer/0"
    if detail_level in [DetailLevel.BFC]:
        return f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/ITL3_JAN_2025_UK_{detail_level.value}_V2/FeatureServer/0"
    if detail_level in [DetailLevel.BGC]:
        return f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/ITL3_JAN_2025_UK_{detail_level.value}_V2/FeatureServer/1"
    if detail_level in [DetailLevel.BUC]:
        return f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/ITL3_JAN_2025_UK_{detail_level.value}_V2/FeatureServer/2"
    if detail_level == DetailLevel.BSC:
        return None  # ITL3 not available in BSC level


def lads_url(detail_level: DetailLevel) -> str:
    if detail_level in [DetailLevel.BFE, DetailLevel.BFC, DetailLevel.BGC, DetailLevel.BSC]:
        return f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/LAD_MAY_2025_UK_{detail_level.value}_V2/FeatureServer/0"
    if detail_level == DetailLevel.BUC:
        return f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/LAD_MAY_2025_UK_{detail_level.value}/FeatureServer/0"


def wards_url(detail_level: DetailLevel) -> str:
    if detail_level in [DetailLevel.BFE, DetailLevel.BFC, DetailLevel.BGC, DetailLevel.BSC]:
        return f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/WD_DEC_2025_UK_{detail_level.value}/FeatureServer/0"
    if detail_level == DetailLevel.BUC:
        return None  # Wards not available in BUC level


# ONS Open Geography portal ArcGIS REST API FeatureServer services
def get_boundary_services(detail_level: DetailLevel) -> dict[str, str]:
    """
    Get boundary service URLs for the specified detail level.

    Args:
        detail_level: One of BFE, BFC, BGC, BSC, BUC
    """

    return {
        "ITL_1.geojson": itl1_url(detail_level),
        "ITL_2.geojson": itl2_url(detail_level),
        "ITL_3.geojson": itl3_url(detail_level),
        "local_authority_districts.geojson": lads_url(detail_level),
        "wards.geojson": wards_url(detail_level),
        "countries.geojson": countries_url(detail_level),
    }


def download_arcgis_layer(service_url, filename, output_dir="boundaries", max_records=2000):
    """
    Download a complete ArcGIS FeatureServer layer using native ESRI JSON format (faster)
    and convert to GeoJSON locally.

    Args:
        service_url: Base URL to the FeatureServer layer (without /query)
        filename: Output filename
        output_dir: Directory to save the file
        max_records: Number of records to fetch per request
    """
    output_path = Path(output_dir) / filename
    output_path.parent.mkdir(exist_ok=True)

    print(f"Downloading {filename}...")

    try:
        query_url = f"{service_url}/query"

        # First, get the total count
        count_params = {"where": "1=1", "returnCountOnly": "true", "f": "json"}

        count_response = requests.get(query_url, params=count_params, timeout=30)
        count_response.raise_for_status()
        total_count = count_response.json().get("count", 0)

        print(f"  Total features: {total_count}")

        # Collect all features using pagination
        all_features = []
        offset = 0
        geometry_type = None

        while offset < total_count:
            query_params = {
                "where": "1=1",
                "outFields": "*",
                "f": "json",  # Native ESRI JSON format - much faster, no server conversion
                "outSR": "4326",
                "resultOffset": offset,
                "resultRecordCount": max_records,
            }

            response = requests.get(query_url, params=query_params, timeout=60)
            response.raise_for_status()

            data = response.json()
            features = data.get("features", [])

            if not features:
                break

            # Get geometry type from first response
            if geometry_type is None:
                geometry_type = data.get("geometryType", "esriGeometryPolygon")

            # Convert ESRI JSON features to GeoJSON features
            geojson_features = [esri_to_geojson_feature(f, geometry_type) for f in features]
            all_features.extend(geojson_features)
            offset += len(features)

            print(f"  Downloaded {offset}/{total_count} features", end="\r", flush=True)
            time.sleep(0.5)  # Be nice to the server

        print(f"  Downloaded {len(all_features)}/{total_count} features")

        # Construct the final GeoJSON
        geojson = {"type": "FeatureCollection", "features": all_features}

        # Save to file
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)

        print(f"  [OK] Saved to {output_path}")

    except requests.RequestException as e:
        print(f"  [ERROR] {e}")
        raise e
    except json.JSONDecodeError as e:
        print(f"  [ERROR] Invalid JSON: {e}")
        raise e
    except Exception as e:
        print(f"  [ERROR] Unexpected error: {e}")
        raise e


def download_extras(url, name, file_paths_to_add_to: list[str], output_dir="boundaries"):
    print(f"Downloading {name} data")
    response = requests.get(url)
    features = response.json().get("features", [])
    for feature in features:
        feature["properties"]["ITL325NM"] = name
        feature["properties"]["ITL225NM"] = name
        feature["properties"]["ITL125NM"] = name
        feature["properties"]["CTRY24NM"] = name
        feature["properties"]["LAD25NM"] = name
        feature["properties"]["WD25NM"] = name
        feature["properties"]["ITL325CD"] = feature["properties"]["GID_0"] + "00"
        feature["properties"]["ITL225CD"] = feature["properties"]["GID_0"] + "0"
        feature["properties"]["ITL125CD"] = feature["properties"]["GID_0"]
        feature["properties"]["CTRY24CD"] = feature["properties"]["GID_0"]
        feature["properties"]["LAD25CD"] = feature["properties"]["GID_0"]
        feature["properties"]["WD25CD"] = feature["properties"]["GID_0"]
    for path in file_paths_to_add_to:
        output_path = Path(output_dir) / path
        print(f"  Injecting {name} data into {output_path}...")
        with open(output_path, "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["features"].extend(features)
            f.seek(0)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.truncate()
        print(f"  [OK] Saved to {output_path}")


def main():
    """Download all boundary files."""
    parser = argparse.ArgumentParser(
        description="Download boundary GeoJSON files from ONS Open Geography portal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Detail levels:
  BFE  Full Extent (most detailed, largest files)
  BFC  Full Clipped (detailed, clipped to coastline)
  BGC  Generalised Clipped (simplified, smaller files) - default
  BSC  Super Generalised Clipped (very simplified)
  BUC  Ultra Generalised Clipped (least detailed, smallest files)
        """,
    )
    parser.add_argument(
        "--detail",
        choices=["BFE", "BFC", "BGC", "BSC", "BUC"],
        default="BGC",
        help="Boundary detail level (default: BGC for smaller file sizes)",
    )
    args = parser.parse_args()

    if args.detail == "BSC":
        print("BSC is not available, exiting...")
        return

    detail = DetailLevel(args.detail)

    boundary_services = get_boundary_services(detail)

    print("Downloading boundary files from ONS Open Geography portal")
    print(f"Detail level: {detail.value}")
    print("=" * 60)

    for filename, service_url in boundary_services.items():
        if service_url is None:
            print(f"Skipping {filename} (not available in {detail.value} level)")
            continue
        download_arcgis_layer(service_url, filename)
        print()

    download_extras(
        "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_IMN_0.json",
        "Isle of Man",
        list(boundary_services.keys()),
    )
    download_extras(
        "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_JEY_0.json",
        "Jersey",
        list(boundary_services.keys()),
    )
    download_extras(
        "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_GGY_0.json",
        "Guernsey",
        list(boundary_services.keys()),
    )

    print("=" * 60)
    print("Complete!")


if __name__ == "__main__":
    main()
