"""Static asset helpers for self-hosting pinned CDN libraries.

Every rugby map page (Folium tier maps, match-day, custom-map) pulls Leaflet,
MarkerCluster and a handful of small plugins from third-party CDNs
(jsdelivr/cdnjs/unpkg/jquery). This module centralises the mapping from those
literal CDN URLs to root-relative vendor paths under ``dist/shared/vendor/``,
plus a guarded rewrite helper that only swaps a URL for its local copy once
that copy has actually been fetched (see ``scripts/fetch_vendor_assets.py``).
"""

from __future__ import annotations

from pathlib import Path

# CDN URL -> root-relative vendor path (populated by scripts/fetch_vendor_assets.py).
#
# IMPORTANT: keys must be the *literal* URLs Folium/our templates actually emit
# (verified against real generated dist/ HTML — Folium's own default_js/
# default_css pin specific CDNs per asset, which don't always match the
# vendor's "canonical" CDN). Different pinned versions get distinct local
# filenames so a single asset name never silently serves the wrong version to
# a page expecting a different one (e.g. custom-map's unpkg Leaflet 1.9.4 vs
# Folium tier maps' jsdelivr 1.9.3, or custom-map's MarkerCluster 1.5.3 vs
# Folium's cdnjs 1.1.0).
CDN_TO_VENDOR: dict[str, str] = {
    # Folium map pages (core/map_builder.py -> folium.Map defaults) and
    # rugby/match_day.py, which share the same pinned versions.
    "https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js": "/shared/vendor/leaflet-1.9.3.js",
    "https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.css": (
        "/shared/vendor/leaflet-1.9.3.css"
    ),
    "https://cdn.jsdelivr.net/npm/bootstrap@5.2.2/dist/js/bootstrap.bundle.min.js": (
        "/shared/vendor/bootstrap.bundle.min.js"
    ),
    "https://cdn.jsdelivr.net/npm/bootstrap@5.2.2/dist/css/bootstrap.min.css": (
        "/shared/vendor/bootstrap.min.css"
    ),
    "https://netdna.bootstrapcdn.com/bootstrap/3.0.0/css/bootstrap-glyphicons.css": (
        "/shared/vendor/bootstrap-glyphicons.css"
    ),
    "https://code.jquery.com/jquery-3.7.1.min.js": "/shared/vendor/jquery.min.js",
    "https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.2.0/css/all.min.css": (
        "/shared/vendor/fontawesome.min.css"
    ),
    "https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.js": (
        "/shared/vendor/leaflet.awesome-markers.js"
    ),
    "https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.css": (
        "/shared/vendor/leaflet.awesome-markers.css"
    ),
    "https://cdn.jsdelivr.net/gh/python-visualization/folium/folium/templates/leaflet.awesome.rotate.min.css": (
        "/shared/vendor/leaflet.awesome.rotate.min.css"
    ),
    "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/leaflet.markercluster.js": (
        "/shared/vendor/leaflet.markercluster-1.1.0.js"
    ),
    "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/MarkerCluster.css": (
        "/shared/vendor/MarkerCluster-1.1.0.css"
    ),
    "https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/MarkerCluster.Default.css": (
        "/shared/vendor/MarkerCluster.Default-1.1.0.css"
    ),
    "https://unpkg.com/leaflet.featuregroup.subgroup@1.0.2/dist/leaflet.featuregroup.subgroup.js": (
        "/shared/vendor/leaflet.featuregroup.subgroup.js"
    ),
    # Custom map SPA (rugby/custom_map_assets/index.html) — different pinned versions.
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js": "/shared/vendor/leaflet-1.9.4.js",
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css": "/shared/vendor/leaflet-1.9.4.css",
    "https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js": (
        "/shared/vendor/leaflet.markercluster-1.5.3.js"
    ),
    "https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css": (
        "/shared/vendor/MarkerCluster-1.5.3.css"
    ),
    "https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css": (
        "/shared/vendor/MarkerCluster.Default-1.5.3.css"
    ),
    "https://unpkg.com/@turf/turf@7/turf.min.js": "/shared/vendor/turf.min.js",
}


def rewrite_cdn_urls_in_html(html_path: Path, vendor_dir: Path | None = None) -> bool:
    """Replace known CDN URLs with ``/shared/vendor/`` paths when vendor files exist.

    *vendor_dir* should be an absolute path to ``dist/shared/vendor``. When omitted,
    it is resolved from :data:`core.config.DIST_DIR` rather than guessed from
    *html_path*'s depth, since map pages are nested at varying depths under ``dist/``
    (e.g. ``dist/<season>/<tier>/index.html`` vs ``dist/<season>/merit/<comp>/<tier>/index.html``).

    Returns ``True`` if any URL was rewritten (i.e. the file was modified).
    """
    if vendor_dir is None:
        from core.config import DIST_DIR

        vendor_dir = DIST_DIR / "shared" / "vendor"
    if not vendor_dir.is_dir():
        return False
    text = html_path.read_text(encoding="utf-8")
    changed = False
    for cdn_url, vendor_path in CDN_TO_VENDOR.items():
        local = vendor_dir / Path(vendor_path).name
        if local.is_file() and cdn_url in text:
            text = text.replace(cdn_url, vendor_path)
            changed = True
    if changed:
        html_path.write_text(text, encoding="utf-8")
    return changed
