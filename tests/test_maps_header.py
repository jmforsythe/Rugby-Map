"""Men's map header dropdown lists pyramid tiers, then the merit variants."""

from __future__ import annotations

from rugby.maps import SIBLING_DIVIDER, _header_bar_html, _mens_sibling_links, _tier_sibling_links

PYRAMID_ORDER = ["Premiership", "Counties 4", "Counties 5"]
PYRAMID_NUMS = {"Premiership": 1, "Counties 4": 10, "Counties 5": 11}


def test_mens_sibling_links_appends_merit_maps_after_a_divider() -> None:
    links = _mens_sibling_links(
        PYRAMID_ORDER, PYRAMID_NUMS, {10, 11, 12, 13}, "2025-2026", is_prod=False
    )

    assert links == [
        ("Premiership", "Premiership.html"),
        ("Counties 4", "Counties_4.html"),
        ("Counties 5", "Counties_5.html"),
        SIBLING_DIVIDER,
        ("Counties 4 + Merit", "Counties_4_All_Leagues.html"),
        ("Counties 5 + Merit", "Counties_5_All_Leagues.html"),
        ("Level 12 (Merit)", "Level_12_All_Leagues.html"),
        ("Level 13 (Merit)", "Level_13_All_Leagues.html"),
    ]


def test_mens_sibling_links_uses_directory_hrefs_in_production() -> None:
    links = _mens_sibling_links(
        ["Counties 5"], {"Counties 5": 11}, {11, 12}, "2025-2026", is_prod=True
    )

    assert links == [
        ("Counties 5", "../Counties_5/"),
        SIBLING_DIVIDER,
        ("Counties 5 + Merit", "../Counties_5_All_Leagues/"),
        ("Level 12 (Merit)", "../Level_12_All_Leagues/"),
    ]


def test_mens_sibling_links_without_merit_matches_plain_tier_links() -> None:
    assert _mens_sibling_links(
        PYRAMID_ORDER, PYRAMID_NUMS, set(), "2025-2026", is_prod=False
    ) == _tier_sibling_links(PYRAMID_ORDER, is_prod=False)


def test_header_bar_renders_divider_as_a_disabled_option() -> None:
    html = _header_bar_html(
        "2025-2026",
        "Counties 5 + Merit",
        sibling_tiers=_mens_sibling_links(
            PYRAMID_ORDER, PYRAMID_NUMS, {11, 12}, "2025-2026", is_prod=False
        ),
        current_tier="Counties 5 + Merit",
    )

    assert f'<option value="" disabled>{SIBLING_DIVIDER[0]}</option>' in html
    assert (
        '<option value="Counties_5_All_Leagues.html" selected>Counties 5 + Merit</option>' in html
    )
    # The dropdown replaces the plain title span on merit maps too.
    assert 'class="map-header__title"' not in html
