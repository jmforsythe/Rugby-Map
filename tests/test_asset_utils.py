"""Tests for core.asset_utils."""

from __future__ import annotations

from pathlib import Path

from core.asset_utils import rewrite_cdn_urls_in_html, vendor_url_for_html


def test_rewrite_cdn_urls_replaces_only_when_vendor_file_exists(tmp_path: Path) -> None:
    """Nested map output paths (dist/<season>/<tier>/index.html) must still resolve
    to the correct vendor dir when passed explicitly, matching the real dist/ layout."""
    vendor_dir = tmp_path / "dist" / "shared" / "vendor"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "leaflet-1.9.3.js").write_text("/* leaflet */", encoding="utf-8")

    html_path = tmp_path / "dist" / "2026-2027" / "Premiership" / "index.html"
    html_path.parent.mkdir(parents=True)
    html_path.write_text(
        '<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js"></script>'
        '<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>',
        encoding="utf-8",
    )

    changed = rewrite_cdn_urls_in_html(html_path, vendor_dir=vendor_dir)

    assert changed is True
    text = html_path.read_text(encoding="utf-8")
    assert "/shared/vendor/leaflet-1.9.3.js" in text
    # jquery.min.js was never fetched into vendor_dir, so it should stay on the CDN.
    assert "code.jquery.com" in text


def test_rewrite_cdn_urls_no_vendor_dir_is_noop(tmp_path: Path) -> None:
    """When the vendor dir doesn't exist (e.g. local dev without the fetch
    script having been run), the HTML must be left untouched."""
    html_path = tmp_path / "index.html"
    original = '<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js"></script>'
    html_path.write_text(original, encoding="utf-8")

    changed = rewrite_cdn_urls_in_html(html_path, vendor_dir=tmp_path / "missing")

    assert changed is False
    assert html_path.read_text(encoding="utf-8") == original


def test_rewrite_cdn_urls_default_vendor_dir_uses_dist_dir(tmp_path: Path, monkeypatch) -> None:
    """Without an explicit vendor_dir, resolution must go through DIST_DIR, not
    html_path's own directory depth (regression test: deeply nested merit maps)."""
    import core.config as core_config

    dist_dir = tmp_path / "dist"
    vendor_dir = dist_dir / "shared" / "vendor"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "leaflet-1.9.3.js").write_text("/* leaflet */", encoding="utf-8")

    monkeypatch.setattr(core_config, "DIST_DIR", dist_dir)

    # Deeply nested merit map path: dist/<season>/merit/<comp>/<tier>/index.html
    html_path = dist_dir / "2026-2027" / "merit" / "Sussex" / "Sussex_3" / "index.html"
    html_path.parent.mkdir(parents=True)
    html_path.write_text(
        '<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js"></script>',
        encoding="utf-8",
    )

    changed = rewrite_cdn_urls_in_html(html_path)

    assert changed is True
    assert "/shared/vendor/leaflet-1.9.3.js" in html_path.read_text(encoding="utf-8")


def test_rewrite_cdn_urls_multiple_pinned_versions_stay_distinct(tmp_path: Path) -> None:
    """Folium tier maps (leaflet 1.9.3) and the custom map (leaflet 1.9.4) must
    each be rewritten to their own distinct local file, never sharing one."""
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "leaflet-1.9.3.js").write_text("/* 1.9.3 */", encoding="utf-8")
    (vendor_dir / "leaflet-1.9.4.js").write_text("/* 1.9.4 */", encoding="utf-8")

    html_path = tmp_path / "index.html"
    html_path.write_text(
        '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>',
        encoding="utf-8",
    )

    rewrite_cdn_urls_in_html(html_path, vendor_dir=vendor_dir)

    text = html_path.read_text(encoding="utf-8")
    assert "/shared/vendor/leaflet-1.9.4.js" in text
    assert "leaflet-1.9.3.js" not in text


def test_rewrite_cdn_urls_local_build_uses_relative_paths(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "dist" / "shared" / "vendor"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "leaflet-1.9.3.js").write_text("/* leaflet */", encoding="utf-8")

    html_path = tmp_path / "dist" / "2026-2027" / "Counties_1.html"
    html_path.parent.mkdir(parents=True)
    html_path.write_text(
        '<script src="/shared/vendor/leaflet-1.9.3.js"></script>',
        encoding="utf-8",
    )

    changed = rewrite_cdn_urls_in_html(html_path, vendor_dir=vendor_dir, root_relative=False)

    assert changed is True
    assert html_path.read_text(encoding="utf-8") == (
        '<script src="../shared/vendor/leaflet-1.9.3.js"></script>'
    )


def test_vendor_url_for_html_deeply_nested_merit_map(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "dist" / "shared" / "vendor"
    vendor_dir.mkdir(parents=True)
    html_path = tmp_path / "dist" / "2026-2027" / "merit" / "Sussex" / "All_Tiers" / "index.html"
    html_path.parent.mkdir(parents=True)

    url = vendor_url_for_html(
        html_path,
        "leaflet-1.9.3.js",
        vendor_dir=vendor_dir,
        root_relative=False,
    )
    assert url == "../../../../shared/vendor/leaflet-1.9.3.js"
