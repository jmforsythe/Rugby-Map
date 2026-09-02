"""URL slug helpers for site feature paths and RFU-derived content names."""

from __future__ import annotations

import re
import unicodedata

# Site feature path segments (kebab-case or single lowercase words).
FEATURE_FIXTURES = "fixtures"
LEGACY_FEATURE_MATCH_DAY = "match_day"

# Pyramid diagram asset stems (kebab-case, site-owned static files).
PYRAMID_STEM = "pyramid"
PYRAMID_STEM_LABELS = "pyramid-labels"
PYRAMID_STEM_ALL_LEAGUES = "pyramid-all-leagues"
PYRAMID_STEM_ALL_LEAGUES_LABELS = "pyramid-all-leagues-labels"
PYRAMID_STEM_WOMEN = "pyramid-women"
PYRAMID_STEM_WOMEN_LABELS = "pyramid-women-labels"
PYRAMID_GALLERY_HTML = "pyramid-gallery.html"
PYRAMID_ALL_LEAGUES_GALLERY_HTML = "pyramid-all-leagues-gallery.html"

# Map old on-disk pyramid stems → current stems (for redirects and detection fallbacks).
LEGACY_PYRAMID_STEM_MAP: dict[str, str] = {
    "pyramid_Labels": PYRAMID_STEM_LABELS,
    "pyramid_All_Leagues": PYRAMID_STEM_ALL_LEAGUES,
    "pyramid_All_Leagues_Labels": PYRAMID_STEM_ALL_LEAGUES_LABELS,
    "pyramid_womens": PYRAMID_STEM_WOMEN,
    "pyramid_womens_Labels": PYRAMID_STEM_WOMEN_LABELS,
}

_FULL_PNG_PYRAMID_STEMS = frozenset(
    {
        PYRAMID_STEM,
        PYRAMID_STEM_LABELS,
        PYRAMID_STEM_ALL_LEAGUES,
        PYRAMID_STEM_ALL_LEAGUES_LABELS,
    }
)

_WOMENS_NORMALIZE_RE = re.compile(r"Women'?s?\b", re.IGNORECASE)
_APOSTROPHE_VARIANTS_RE = re.compile(r"['\u2019\u2018`´]")


def slugify_path(name: str) -> str:
    """Lowercase kebab-case for site feature segments (``custom-map``, ``east-midlands``)."""
    s = unicodedata.normalize("NFKC", name).replace("_", "-")
    s = re.sub(r"[^a-zA-Z0-9-]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-").lower()


def slugify_content(name: str) -> str:
    """RFU-derived PascalSnake slug: spaces → ``_``, no apostrophes, ``&`` → ``and``."""
    s = unicodedata.normalize("NFKC", name)
    s = _APOSTROPHE_VARIANTS_RE.sub("'", s)
    s = s.replace("&", "and").replace("/", "_").replace("|", "_")
    s = re.sub(r"Women\+", "Women_Plus", s, flags=re.IGNORECASE)
    s = _WOMENS_NORMALIZE_RE.sub("Women", s)
    s = s.replace("'", "")
    s = s.replace(",", "_").replace("+", "_Plus")
    s = re.sub(r"[\s.]+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


def sanitize_team_name(team_name: str) -> str:
    """Convert team display name to a URL slug (PascalSnake, minimal punctuation)."""
    s = unicodedata.normalize("NFKC", team_name)
    s = _APOSTROPHE_VARIANTS_RE.sub("'", s)
    s = s.replace("&", "and").replace("/", "_").replace("|", "_")
    s = re.sub(r"Women\+", "Women_Plus", s, flags=re.IGNORECASE)
    s = s.replace("+", "_Plus").replace(",", "_")
    s = _WOMENS_NORMALIZE_RE.sub("Women", s)
    s = s.replace(" ", "_")
    s = re.sub(r"[\s_-]+", "_", s)
    return s.strip("_")


def team_name_to_filepath(team_name: str) -> str:
    """Convert team name to corresponding HTML filename."""
    return sanitize_team_name(team_name) + ".html"


def pyramid_merit_stem(competition: str) -> str:
    """Kebab-case stem for a merit competition pyramid diagram."""
    return f"pyramid-merit-{slugify_path(competition)}"


def pyramid_labels_stem(stem: str) -> str:
    """Labelled variant of a pyramid asset stem (``pyramid`` → ``pyramid-labels``)."""
    if stem.endswith("-labels"):
        return stem
    legacy = LEGACY_PYRAMID_STEM_MAP.get(stem)
    if legacy:
        stem = legacy
    return f"{stem}-labels"


def resolve_pyramid_stem(stem: str) -> str:
    """Map a legacy pyramid stem to the current kebab-case stem if known."""
    return LEGACY_PYRAMID_STEM_MAP.get(stem, stem)


def legacy_apostrophe_tier_slug(stem: str) -> str | None:
    """``Premiership_Women's`` → ``Premiership_Women`` when the slug differs."""
    if "'" not in stem:
        return None
    target = stem.replace("'", "")
    target = target.replace("_Womens", "_Women")
    return target if target != stem else None


def stem_wants_full_png(stem: str) -> bool:
    """True when a pyramid PNG stem should get a full ``--png-scale`` raster."""
    return resolve_pyramid_stem(stem) in _FULL_PNG_PYRAMID_STEMS
