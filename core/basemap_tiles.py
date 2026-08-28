"""Shared basemap tile URLs for Leaflet/Folium.

CARTO now requires an API key for the raster tile endpoint. Keep a single
Voyager-style URL and source the key from the environment or a local gitignored
.env.local file so both local runs and GitHub Pages builds can use the same setup.
"""

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_local_env_file() -> dict[str, str]:
    """Read a gitignored .env.local file in the repo root if present."""
    env_path = REPO_ROOT / ".env.local"
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_carto_api_key() -> str:
    """Return the CARTO API key from the environment or a local .env.local file."""
    local_values = _load_local_env_file()
    for name in ("CARTO_API_KEY", "CARTO_MAPS_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
        value = local_values.get(name, "").strip()
        if value:
            return value
    return ""


# Preserve the original CARTO light/dark tile variants so the existing theme
# switcher can swap between them without any JS changes.
CARTO_THEME_MARK_LIGHT = "light_all"
CARTO_THEME_MARK_DARK = "dark_all"


def get_carto_tile_url(style: str = "light_all") -> str:
    """Return the CARTO raster tile URL for a given theme, including the API key when configured."""
    base_url = f"https://basemaps.cartocdn.com/rastertiles/{style}/{{z}}/{{x}}/{{y}}.png"
    api_key = get_carto_api_key()
    if not api_key:
        return base_url

    parsed = urlsplit(base_url)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    params = [(key, value) for key, value in params if key != "key"]
    params.append(("key", api_key))
    query = urlencode(params)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


CARTO_TILE_URL_LIGHT = get_carto_tile_url(CARTO_THEME_MARK_LIGHT)
CARTO_TILE_URL_DARK = get_carto_tile_url(CARTO_THEME_MARK_DARK)


def folium_carto_attribution() -> str:
    """HTML attribution snippet for Folium TileLayer(attr=...)."""
    return (
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
        'contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
    )


def custom_map_basemap_html_attribution() -> str:
    """HTML for Leaflet tileLayer attribution (JSON-inserted in generated index)."""
    return folium_carto_attribution()
