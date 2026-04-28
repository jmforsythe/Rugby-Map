"""
Shared basemap tile URLs for Leaflet/Folium (no API key).

CartoDB Positron (light_all) / Dark Matter (dark_all): OSM-based streets, roads,
and labels on a light grey (or dark) backdrop — good readability for markers.
Light ↔ dark swaps replace the path segment light_all/dark_all (see THEME_MARK_*).
"""

# Substrings swapped by map_builder DARK_MODE_JS and custom map swapBasemapTilesForAppearance().
CARTO_THEME_MARK_LIGHT = "light_all"
CARTO_THEME_MARK_DARK = "dark_all"

CARTO_TILE_URL_LIGHT = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
CARTO_TILE_URL_DARK = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"


def folium_carto_attribution() -> str:
    """HTML attribution snippet for Folium TileLayer(attr=...)."""
    return (
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
        'contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
    )


def custom_map_basemap_html_attribution() -> str:
    """HTML for Leaflet tileLayer attribution (JSON-inserted in generated index)."""
    return folium_carto_attribution()
