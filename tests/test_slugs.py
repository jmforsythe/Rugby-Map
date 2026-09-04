"""Tests for URL slug helpers."""

from __future__ import annotations

from core.slugs import (
    FEATURE_FIXTURES,
    PYRAMID_STEM_WOMEN,
    legacy_apostrophe_tier_slug,
    pyramid_labels_stem,
    pyramid_merit_stem,
    sanitize_team_name,
    slugify_content,
    slugify_path,
    stem_wants_full_png,
)


def test_slugify_content_womens_tier() -> None:
    assert slugify_content("Premiership Women's") == "Premiership_Women"


def test_slugify_content_strips_apostrophes_in_other_words() -> None:
    assert slugify_content("Bishop's Stortford") == "Bishops_Stortford"


def test_normalize_filename_stem_matches_slugify_rules() -> None:
    from core.slugs import normalize_filename_stem

    assert normalize_filename_stem("Durham_N'thm'land_1") == "Durham_Nthmland_1"
    assert normalize_filename_stem("Berks_Bucks_&_Oxon_Premier") == "Berks_Bucks_and_Oxon_Premier"
    assert normalize_filename_stem("Women's_NC_2") == "Women_NC_2"
    assert normalize_filename_stem("Harvey's_Brewery_Counties_3") == "Harveys_Brewery_Counties_3"


def test_slugify_path_kebab() -> None:
    assert slugify_path("East_Midlands") == "east-midlands"
    assert slugify_path("custom-map") == "custom-map"


def test_pyramid_merit_stem() -> None:
    assert pyramid_merit_stem("East_Midlands") == "pyramid-merit-east-midlands"


def test_pyramid_labels_stem() -> None:
    assert pyramid_labels_stem("pyramid") == "pyramid-labels"
    assert pyramid_labels_stem("pyramid-all-leagues") == "pyramid-all-leagues-labels"


def test_legacy_apostrophe_tier_slug() -> None:
    assert legacy_apostrophe_tier_slug("Premiership_Women's") == "Premiership_Women"
    assert legacy_apostrophe_tier_slug("Premiership") is None


def test_sanitize_team_name_normalizes_womens_and_plus() -> None:
    assert sanitize_team_name("Bournville Womens") == "Bournville_Women"
    assert sanitize_team_name("Hampstead Women+") == "Hampstead_Women_Plus"


def test_sanitize_team_name_unicode_apostrophe() -> None:
    left = sanitize_team_name("CCS Women\u2019s Rugby")
    right = sanitize_team_name("CCS Women's Rugby")
    assert left == right


def test_stem_wants_full_png_kebab_and_legacy() -> None:
    assert stem_wants_full_png("pyramid-all-leagues")
    assert stem_wants_full_png("pyramid_All_Leagues")
    assert not stem_wants_full_png(PYRAMID_STEM_WOMEN)


def test_feature_fixtures_constant() -> None:
    assert FEATURE_FIXTURES == "fixtures"
    assert slugify_path(FEATURE_FIXTURES) == "fixtures"
