"""Application configuration, logging setup, and HTML helpers."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"
DATA_DIR = REPO_ROOT / "data"
BOUNDARIES_DIR = DATA_DIR / "boundaries"
CACHE_DIR = DATA_DIR / "caches"

CURRENT_SEASON = "2026-2027"

#: Earliest season with usable tier/fixture/geocoding data. 1999-2000 has raw
#: league_data but predates tier_mappings, fixture_data, and geocoded_teams
#: coverage, so anything that walks league_data across all seasons should
#: start here instead.
EARLIEST_SEASON = "2000-2001"


@dataclass
class AppConfig:
    """Shared configuration for the mapping pipeline."""

    is_production: bool = False
    season: str = CURRENT_SEASON
    show_debug: bool = True


_config = AppConfig()


def get_config() -> AppConfig:
    """Return the global application config."""
    return _config


def set_config(
    *, is_production: bool = False, season: str = CURRENT_SEASON, show_debug: bool = True
) -> None:
    """Set global application config values."""
    _config.is_production = is_production
    _config.season = season
    _config.show_debug = show_debug


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for the pipeline."""
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )


def get_google_analytics_script() -> str:
    """Return Google Analytics script for embedding in HTML pages.

    Uses the GA_TRACKING_ID environment variable. Returns an empty string if not set.
    """
    ga_id = os.environ.get("GA_TRACKING_ID", "")
    if not ga_id:
        return ""
    return f"""
    <!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id={ga_id}"></script>
    <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());

    gtag('config', '{ga_id}');
    </script>
"""


def get_service_worker_registration_script() -> str:
    """Script to register the site service worker (path is root-relative, production only)."""
    return """
    <script>
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/service-worker.js')
            .then(function(reg) {
                if (reg.waiting) { reg.waiting.postMessage({type: 'SKIP_WAITING'}); }
            })
            .catch(function(err) { console.log('ServiceWorker registration failed:', err); });
        navigator.serviceWorker.addEventListener('controllerchange', function() {});
    }
    </script>
    """


def get_resource_hints_html() -> str:
    """Preconnect/dns-prefetch hints for the CARTO tile server used by every
    map page (tier maps, match-day, custom-map). We now use the single shared
    origin for the Voyager raster tiles, so there is only one warm-up target.
    """
    origin = "https://basemaps.cartocdn.com"
    lines = [
        f'    <link rel="preconnect" href="{origin}">',
        f'    <link rel="dns-prefetch" href="{origin}">',
    ]
    return "\n".join(lines)


def get_twitter_card_meta() -> str:
    """Twitter / X card hint; title, description, and image typically match Open Graph."""
    return '<meta name="twitter:card" content="summary_large_image" />'


#: Brand typography: Oswald for headings (condensed, athletic), Barlow for body
#: text. Loaded from Google Fonts on every generated page; see dist/styles.css
#: for the corresponding --font-heading / --font-body variables.
FONT_STYLESHEET_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Oswald:wght@500;600;700&family=Barlow:wght@400;500;600&display=swap"
)


def get_font_html() -> str:
    """Return <link> tags that preconnect to and load the brand Google Fonts."""
    return (
        '    <link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        f'    <link href="{FONT_STYLESHEET_URL}" rel="stylesheet">'
    )


def get_favicon_html(depth: int = 0) -> str:
    """Return <link> tags for favicon, manifest, and brand fonts.

    Args:
        depth: directory depth relative to dist/ root (0 = top-level, 1 = season, etc.)
    """
    if get_config().is_production:
        prefix = "/"
    else:
        prefix = "../" * depth if depth > 0 else ""
    return (
        f'    <link rel="icon" href="{prefix}favicon.ico" sizes="any">\n'
        f'    <link rel="icon" href="{prefix}favicon.svg" type="image/svg+xml">\n'
        f'    <link rel="manifest" href="{prefix}manifest.json">\n'
        f"{get_font_html()}"
    )
