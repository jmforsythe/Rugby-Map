"""
Script to download boundary GeoJSON files from ONS Open Geography portal.
Downloads ITL1, ITL2, ITL3, and Countries boundaries using pagination.

Detail levels available:
- BFE: Full Extent (most detailed, largest files)
- BFC: Full Clipped (detailed, clipped to coastline)
- BGC: Generalised Clipped (simplified, smaller files)
- BSC: Super Generalised Clipped (very simplified)
- BUC: Ultra Generalised Clipped (least detailed, smallest files)
"""

import argparse
import json
import time
from pathlib import Path

import requests


# ONS Open Geography portal ArcGIS REST API FeatureServer services
def get_boundary_services(detail_level="BFC"):
    """
    Get boundary service URLs for the specified detail level.

    Args:
        detail_level: One of BFE, BFC, BGC, BSC, BUC
    """
    # ITL3 uses _V2 suffix and layer ID 1 for generalised versions
    # ITL1/2 use layer ID 0
    # Countries service name varies by detail level
    if detail_level in ["BGC", "BSC", "BUC"]:
        itl3_service = f"ITL3_JAN_2025_UK_{detail_level}_V2"
        itl3_layer = "1"
        countries_service = f"Countries_December_2024_Boundaries_UK_{detail_level}"
    else:
        itl3_service = f"ITL3_JAN_2025_UK_{detail_level}"
        itl3_layer = "0"
        countries_service = f"CTRY_DEC_2024_UK_{detail_level}"

    return {
        "ITL_1.geojson": f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/ITL1_JAN_2025_UK_{detail_level}/FeatureServer/0",
        "ITL_2.geojson": f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/ITL2_JAN_2025_UK_{detail_level}/FeatureServer/0",
        "ITL_3.geojson": f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/{itl3_service}/FeatureServer/{itl3_layer}",
        "countries.geojson": f"https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/{countries_service}/FeatureServer/0",
    }


def download_arcgis_layer(service_url, filename, output_dir="boundaries", max_records=2000):
    """
    Download a complete ArcGIS FeatureServer layer as GeoJSON using pagination.

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

        while offset < total_count:
            query_params = {
                "where": "1=1",
                "outFields": "*",
                "f": "geojson",
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

            all_features.extend(features)
            offset += len(features)

            print(f"  Downloaded {offset}/{total_count} features", end="\r", flush=True)
            time.sleep(0.5)  # Be nice to the server

        print(f"  Downloaded {len(all_features)}/{total_count} features")

        # Construct the final GeoJSON
        geojson = {"type": "FeatureCollection", "features": all_features}

        # Save to file
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)

        print(f"  ✓ Saved to {output_path}")

    except requests.RequestException as e:
        print(f"  ✗ Error: {e}")
    except json.JSONDecodeError as e:
        print(f"  ✗ Invalid JSON: {e}")
    except Exception as e:
        print(f"  ✗ Unexpected error: {e}")


def main():
    """Download all boundary files."""
    parser = argparse.ArgumentParser(
        description="Download boundary GeoJSON files from ONS Open Geography portal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Detail levels:
  BFE  Full Extent (most detailed, largest files)
  BFC  Full Clipped (detailed, clipped to coastline) - default
  BGC  Generalised Clipped (simplified, smaller files)
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

    boundary_services = get_boundary_services(args.detail)

    print("Downloading boundary files from ONS Open Geography portal")
    print(f"Detail level: {args.detail}")
    print("=" * 60)

    for filename, service_url in boundary_services.items():
        download_arcgis_layer(service_url, filename)
        print()

    print("=" * 60)
    print("Complete!")


if __name__ == "__main__":
    main()
