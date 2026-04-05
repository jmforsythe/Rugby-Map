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


@dataclass
class AppConfig:
    """Shared configuration for the mapping pipeline."""

    is_production: bool = False
    season: str = "2025-2026"
    show_debug: bool = True


_config = AppConfig()


def get_config() -> AppConfig:
    """Return the global application config."""
    return _config


def set_config(
    *, is_production: bool = False, season: str = "2025-2026", show_debug: bool = True
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


def get_favicon_html(depth: int = 0) -> str:
    """Return <link> tags for favicon and manifest.

    Args:
        depth: directory depth relative to dist/ root (0 = top-level, 1 = season, etc.)
    """
    if get_config().is_production:
        prefix = "/"
    else:
        prefix = "../" * depth if depth > 0 else ""
    return (
        f'    <link rel="icon" href="{prefix}favicon.svg" type="image/svg+xml">\n'
        f'    <link rel="manifest" href="{prefix}manifest.json">'
    )
