from football.league_names import canonical_league_name


def test_football_pyramid_band_label() -> None:
    from football.map_common import football_pyramid_band_label

    assert football_pyramid_band_label(1) == "Premier League"
    assert football_pyramid_band_label(6) == "Level 6"
    assert football_pyramid_band_label(7) == "Step 3"
    assert football_pyramid_band_label(10) == "Step 6"
    assert football_pyramid_band_label(11) == "Step 7"
    assert football_pyramid_band_label(12) == "Level 12"


def test_canonical_league_name_aliases() -> None:
    assert (
        canonical_league_name("Isthmian League Division One North")
        == "Isthmian League North Division"
    )
    assert (
        canonical_league_name("Isthmian League South Central")
        == "Isthmian League South Central Division"
    )
    assert (
        canonical_league_name("Southern League Premier Division One Central")
        == "Southern League Division One Central"
    )


def test_canonical_league_name_typos() -> None:
    assert (
        canonical_league_name("Wessex  League Premier Division") == "Wessex League Premier Division"
    )
    assert (
        canonical_league_name("Western League Premier Division}")
        == "Western League Premier Division"
    )


def test_canonical_league_name_unchanged() -> None:
    assert canonical_league_name("Premier League") == "Premier League"


def test_football_league_family_parts() -> None:
    from football.league_names import football_league_family_parts

    assert football_league_family_parts("Isthmian League Premier Division") == (
        "Isthmian League",
        "",
    )
    assert football_league_family_parts("Isthmian League North Division") == (
        "Isthmian League",
        "North",
    )
    assert football_league_family_parts("Northern Premier League Division One East") == (
        "Northern Premier League",
        "East",
    )
    assert football_league_family_parts("Northern Premier League Division One Midlands") == (
        "Northern Premier League",
        "Midlands",
    )
    assert football_league_family_parts("Southern League Division One Central") == (
        "Southern League",
        "Central",
    )
    assert football_league_family_parts("National League North") == ("National League", "North")
    assert football_league_family_parts("North West Counties League Premier Division") == (
        "North West Counties League",
        "",
    )
    assert football_league_family_parts("Premier League") == ("Premier League", "")


def test_find_football_parent_name() -> None:
    from football.league_names import find_football_parent_name

    tier7 = [
        "Isthmian League Premier Division",
        "Northern Premier League Premier Division",
        "Southern League Premier Division Central",
        "Southern League Premier Division South",
    ]
    assert find_football_parent_name("Isthmian League North Division", tier7) == (
        "Isthmian League Premier Division"
    )
    assert find_football_parent_name("Northern Premier League Division One Midlands", tier7) == (
        "Northern Premier League Premier Division"
    )
    assert find_football_parent_name("Southern League Division One South", tier7) == (
        "Southern League Premier Division South"
    )

    tier9 = [
        "Eastern Counties League Premier Division",
        "Combined Counties League Premier Division North",
        "Combined Counties League Premier Division South",
        "Northern League Division One",
    ]
    assert find_football_parent_name("Eastern Counties League Division One North", tier9) == (
        "Eastern Counties League Premier Division"
    )
    assert find_football_parent_name("Combined Counties League Division One", tier9) is None
    assert find_football_parent_name("Northern League Division Two", tier9) == (
        "Northern League Division One"
    )

    assert find_football_parent_name("National League North", ["National League"]) == (
        "National League"
    )
