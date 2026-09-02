"""Tests for rugby.addresses extraction helpers."""

from bs4 import BeautifulSoup

from rugby.addresses import extract_address_from_rfu_soup, is_space_separated_address


def _soup(page_text: str | None, maps_query: str | None) -> BeautifulSoup:
    parts: list[str] = ["<html><body>"]
    if page_text is not None:
        parts.append(f'<div class="c036-club-details-address">{page_text}</div>')
    if maps_query is not None:
        href = (
            "https://www.google.com/maps/search/?api=1"
            f"&query={maps_query.replace(' ', '%20').replace(',', '%2C')}"
        )
        parts.append(f'<a class="c036-club-details-btn" href="{href}">Map</a>')
    parts.append("</body></html>")
    return BeautifulSoup("".join(parts), "html.parser")


def test_is_space_separated_address() -> None:
    assert is_space_separated_address("Park Road Town OX1 1AA United Kingdom")
    assert not is_space_separated_address("Park Road, Town, OX1 1AA, United Kingdom")
    assert not is_space_separated_address(None)


def test_extract_address_from_rfu_soup_prefers_maps_by_default() -> None:
    soup = _soup(
        "Park Road Town OX1 1AA United Kingdom",
        "Park Road, Town, OX1 1AA, United Kingdom",
    )
    address, source = extract_address_from_rfu_soup(soup)
    assert source == "maps"
    assert address == "Park Road, Town, OX1 1AA, United Kingdom"


def test_extract_address_from_rfu_soup_page_fallback() -> None:
    soup = _soup("Park Road Town OX1 1AA United Kingdom", None)
    address, source = extract_address_from_rfu_soup(soup)
    assert source == "page"
    assert address == "Park Road Town OX1 1AA United Kingdom"


def test_extract_address_from_rfu_soup_legacy_page_first() -> None:
    soup = _soup(
        "Park Road Town OX1 1AA United Kingdom",
        "Park Road, Town, OX1 1AA, United Kingdom",
    )
    address, source = extract_address_from_rfu_soup(soup, prefer_maps=False)
    assert source == "page"
    assert address == "Park Road Town OX1 1AA United Kingdom"
