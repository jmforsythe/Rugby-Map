"""
Scrape club data from the Bromley & South London Football League website.

Fetches https://www.bslfl.co.uk/clubs to collect:
  - Club names and page URLs
  - Club badge/logo image URLs
Then visits each club's page to extract the home ground address.

Output: football/club_directory_cache.json  (club index with images)
        football/club_addresses.json        (club data with addresses)
"""

from __future__ import annotations

import argparse
import json

from bs4 import BeautifulSoup, Tag

from core import make_request
from football import DATA_DIR

_BASE_URL = "https://www.bslfl.co.uk"
_CLUBS_URL = f"{_BASE_URL}/clubs"

_CLUB_CACHE_FILE = DATA_DIR / "club_directory_cache.json"
_ADDRESS_CACHE_FILE = DATA_DIR / "club_address_cache.json"

_NAV_LINK_TEXTS = {
    "home",
    "latest news",
    "ft test",
    "useful links",
    "management committee",
    "clubs",
    "referees",
    "referee reports",
    "referee panel",
    "league rules",
    "league tables",
    "latest results",
    "fixtures",
    "history",
    "about",
    "contact us",
    "gallery",
    "downloads",
    "social media policy",
    "league forms",
    "bank details",
}


def _is_nav_link(text: str) -> bool:
    """Return True if the link text looks like site navigation rather than a club."""
    lower = text.lower()
    if lower in _NAV_LINK_TEXTS:
        return True
    nav_keywords = [
        "cup",
        "league table",
        "fixture",
        "result",
        "referee",
        "form",
        "login",
        "bslfl",
        "south london",
        "fa ",
        "application",
    ]
    return any(kw in lower for kw in nav_keywords)


def scrape_clubs_index() -> list[dict]:
    """Scrape the clubs page for club names, page URLs, and badge image URLs.

    Returns a list of dicts with keys: name, url, image_url.
    """
    if _CLUB_CACHE_FILE.exists():
        print(f"Loading cached club directory from {_CLUB_CACHE_FILE}")
        with open(_CLUB_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)

    print(f"Fetching club directory from {_CLUBS_URL}")
    response = make_request(_CLUBS_URL, delay_seconds=1)
    soup = BeautifulSoup(response.content, "html.parser")

    image_links: dict[str, str] = {}
    for a in soup.find_all("a"):
        img = a.find("img")
        if img and isinstance(img, Tag) and "dms3rep" in (img.get("src") or ""):
            href = a.get("href", "")
            if href.startswith("/"):
                image_links[href] = str(img["src"])

    clubs: list[dict] = []
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        if not text or a.find("img"):
            continue
        href = a.get("href", "")
        if not href.startswith("/") or href == "/":
            continue
        if href not in image_links:
            continue

        club_url = f"{_BASE_URL}{href}"
        img_url = image_links[href]

        clubs.append(
            {
                "name": text,
                "url": club_url,
                "image_url": img_url,
            }
        )

    print(f"  Found {len(clubs)} clubs")

    _CLUB_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CLUB_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(clubs, f, indent=2, ensure_ascii=False)
    print(f"  Cached to {_CLUB_CACHE_FILE}")

    return clubs


def _extract_address(soup: BeautifulSoup) -> str | None:
    """Extract the home ground address from a club detail page.

    Looks for a 'Home Ground' heading in a dmNewParagraph, then reads the
    address lines from the next sibling paragraph.
    """
    for para in soup.find_all("div", class_="dmNewParagraph"):
        text = para.get_text(strip=True)
        if "Home Ground" in text and len(text) < 30:
            next_para = para.find_next_sibling("div", class_="dmNewParagraph")
            if not next_para:
                continue
            lines = [
                line.strip()
                for line in next_para.get_text(separator="\n").split("\n")
                if line.strip()
            ]
            address_lines = [line for line in lines if not line.lower().startswith("tel:")]
            if address_lines:
                return ", ".join(address_lines)
    return None


def fetch_club_addresses(clubs: list[dict]) -> list[dict]:
    """Visit each club page and extract the home ground address.

    Results are cached to disk; only clubs without an address are re-fetched.
    """
    existing: list[dict] = []
    if _ADDRESS_CACHE_FILE.exists():
        with open(_ADDRESS_CACHE_FILE, encoding="utf-8") as f:
            existing = json.load(f)

    existing_by_url = {c["url"]: c for c in existing}

    results: list[dict] = []
    fetched = 0

    for club in clubs:
        if club["url"] in existing_by_url:
            results.append(existing_by_url[club["url"]])
            print(f"  {club['name']}: cached")
            continue

        print(f"  Fetching {club['name']} ({club['url']})")
        try:
            resp = make_request(club["url"], referer=_CLUBS_URL, delay_seconds=1.5)
            page_soup = BeautifulSoup(resp.content, "html.parser")
            address = _extract_address(page_soup)
        except Exception as exc:
            print(f"    Error fetching {club['url']}: {exc}")
            address = None

        entry = {
            **club,
            "address": address,
        }
        results.append(entry)
        fetched += 1

        if address:
            print(f"    Address: {address}")
        else:
            print("    No address found")

        if fetched % 5 == 0:
            _save_address_cache(results)

    _save_address_cache(results)
    return results


def _save_address_cache(data: list[dict]) -> None:
    _ADDRESS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_ADDRESS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape BSLFL club data: badges and addresses.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download the club directory (ignore cache)",
    )
    parser.add_argument(
        "--refresh-addresses",
        action="store_true",
        help="Re-fetch all club addresses (ignore cache)",
    )
    args = parser.parse_args()

    if args.refresh and _CLUB_CACHE_FILE.exists():
        _CLUB_CACHE_FILE.unlink()
        print("Cleared club directory cache")

    if args.refresh_addresses and _ADDRESS_CACHE_FILE.exists():
        _ADDRESS_CACHE_FILE.unlink()
        print("Cleared address cache")

    clubs = scrape_clubs_index()
    results = fetch_club_addresses(clubs)

    matched = sum(1 for c in results if c.get("address"))
    print(f"\n{'='*60}")
    print(f"Complete! {matched}/{len(results)} clubs have addresses.")
    print(f"Club directory:  {_CLUB_CACHE_FILE}")
    print(f"Club addresses:  {_ADDRESS_CACHE_FILE}")
    print(f"{'='*60}")

    if unmatched := [c["name"] for c in results if not c.get("address")]:
        print(f"\nClubs without addresses ({len(unmatched)}):")
        for name in unmatched:
            print(f"  {name}")


if __name__ == "__main__":
    main()
