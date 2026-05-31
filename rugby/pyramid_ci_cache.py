"""Per-season CI cache for pyramid SVG/PNG raster outputs.

Rebuild when geocoded data, tier_mappings, or :mod:`rugby.pyramid_image` change.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import shutil
import sys
from pathlib import Path

from core.config import DIST_DIR, REPO_ROOT, setup_logging

logger = logging.getLogger(__name__)

PYRAMID_RASTER_CACHE_ROOT = REPO_ROOT / "_pyramid_raster_cache"

_PYRAMID_CODE_PATHS: tuple[Path, ...] = (
    REPO_ROOT / "rugby" / "pyramid_image.py",
    REPO_ROOT / "rugby" / "tiers.py",
)

_PYRAMID_GLOB_PATTERNS: tuple[str, ...] = (
    "pyramid*.svg",
    "pyramid*.png",
    "pyramid*.preview.png",
)


def _hash_file(hasher: hashlib._Hash, path: Path) -> None:
    hasher.update(path.as_posix().encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(path.read_bytes())


def pyramid_raster_inputs_digest(season: str) -> str:
    """Stable digest of inputs that affect pyramid diagrams for ``season``."""
    hasher = hashlib.sha256()
    for code_path in _PYRAMID_CODE_PATHS:
        if code_path.is_file():
            _hash_file(hasher, code_path)
    geocoded = REPO_ROOT / "data" / "rugby" / "geocoded_teams" / season
    if geocoded.is_dir():
        for fp in sorted(geocoded.rglob("*")):
            if fp.is_file():
                _hash_file(hasher, fp)
    tier_mappings = REPO_ROOT / "data" / "rugby" / "tier_mappings" / f"{season}.json"
    if tier_mappings.is_file():
        _hash_file(hasher, tier_mappings)
    return hasher.hexdigest()[:32]


def pyramid_raster_cache_slot(season: str) -> Path:
    """Directory used with ``actions/cache`` path ``_pyramid_raster_cache/<season>``."""
    return PYRAMID_RASTER_CACHE_ROOT / season


def list_pyramid_artifacts(season_dir: Path) -> list[Path]:
    if not season_dir.is_dir():
        return []
    found: list[Path] = []
    for pattern in _PYRAMID_GLOB_PATTERNS:
        found.extend(season_dir.glob(pattern))
    return sorted({p.resolve() for p in found})


def _season_dist_dir(season: str) -> Path:
    return DIST_DIR / season


def cache_manifest_path(slot: Path) -> Path:
    return slot / ".manifest_digest"


def cache_is_valid(season: str, *, digest: str | None = None) -> bool:
    """True when the cache slot holds artifacts for the current inputs digest."""
    slot = pyramid_raster_cache_slot(season)
    digest = digest if digest is not None else pyramid_raster_inputs_digest(season)
    manifest = cache_manifest_path(slot)
    if not manifest.is_file() or manifest.read_text(encoding="utf-8").strip() != digest:
        return False
    cached = list_pyramid_artifacts(slot)
    if len(cached) < 2:
        return False
    names = {p.name for p in cached}
    if "pyramid.svg" not in names:
        return False
    if not any(n.endswith(".preview.png") for n in names):
        return False
    return True


def save_pyramid_raster_cache(season: str) -> int:
    """Copy ``dist/<season>/pyramid*`` into the cache slot for Actions to persist."""
    digest = pyramid_raster_inputs_digest(season)
    src = _season_dist_dir(season)
    artifacts = list_pyramid_artifacts(src)
    if not artifacts:
        logger.error("No pyramid artifacts under %s", src)
        return 1
    slot = pyramid_raster_cache_slot(season)
    if slot.exists():
        shutil.rmtree(slot)
    slot.mkdir(parents=True, exist_ok=True)
    for path in artifacts:
        dest = slot / path.name
        shutil.copy2(path, dest)
    cache_manifest_path(slot).write_text(digest + "\n", encoding="utf-8")
    logger.info(
        "Saved %d pyramid artifact(s) for %s (digest %s) → %s",
        len(artifacts),
        season,
        digest,
        slot,
    )
    return 0


def restore_pyramid_raster_cache(season: str) -> int:
    """Restore cached pyramid artifacts into ``dist/<season>/`` when valid."""
    digest = pyramid_raster_inputs_digest(season)
    if not cache_is_valid(season, digest=digest):
        logger.info("Pyramid raster cache miss for %s (digest %s)", season, digest)
        return 1
    slot = pyramid_raster_cache_slot(season)
    dest = _season_dist_dir(season)
    dest.mkdir(parents=True, exist_ok=True)
    restored = 0
    for path in list_pyramid_artifacts(slot):
        shutil.copy2(path, dest / path.name)
        restored += 1
    logger.info("Restored %d pyramid artifact(s) for %s from cache", restored, season)
    return 0


def _cli() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Pyramid raster CI cache helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    key_p = sub.add_parser("digest", help="Print inputs digest for a season")
    key_p.add_argument("season")

    save_p = sub.add_parser("save", help="Save dist pyramid outputs to cache slot")
    save_p.add_argument("season")

    restore_p = sub.add_parser("restore", help="Restore cache into dist/<season>/")
    restore_p.add_argument("season")

    check_p = sub.add_parser("check", help="Exit 0 if cache slot is valid")
    check_p.add_argument("season")

    args = parser.parse_args()
    season: str = args.season
    if args.command == "digest":
        print(pyramid_raster_inputs_digest(season))
        return 0
    if args.command == "save":
        return save_pyramid_raster_cache(season)
    if args.command == "restore":
        return 0 if restore_pyramid_raster_cache(season) == 0 else 1
    if args.command == "check":
        return 0 if cache_is_valid(season) else 1
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
