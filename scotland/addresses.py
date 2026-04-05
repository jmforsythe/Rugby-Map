"""
Fetch addresses for Scottish Rugby teams by matching league team names
to the club directory on scottishrugby.org/find-a-club/.

The find-a-club page embeds a JSON array (window.Clubs.markers) containing
all 177 clubs with lat/lng, address, website, and club page URL.  This script
extracts that data, then fuzzy-matches each team from league_data to a club.

Output: scotland/team_addresses/{season}/{section}/{league}.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re

from scotland import DATA_DIR

_FIND_A_CLUB_URL = "https://scottishrugby.org/find-a-club/"

_CLUB_CACHE_FILE = DATA_DIR / "club_directory_cache.json"
_FAVICON_CACHE_FILE = DATA_DIR / "favicon_cache.json"

# --------------------------------------------------------------------- #
# Suffixes / words to strip when trying to match a team to a club
# --------------------------------------------------------------------- #
_RESERVE_SUFFIXES = re.compile(
    r"\s*(?:" r"2nd\s*XV|3rd\s*XV|2XV|3XV|\(2nd\s*XV\)" r"|2nd|3rd" r"|\bA$" r")" r"\s*$",
    re.IGNORECASE,
)

_WOMENS_NICKNAMES: dict[str, str] = {
    "Aberdeenshire Quines": "Aberdeenshire RFC",
    "Caithness Krakens": "Caithness RFC",
    "Dundee Valkyries": "Dundee Rugby Club",
    "Dundee Valkyries 2nd XV": "Dundee Rugby Club",
    "Gala Reivers": "Gala RFC",
    "Greenock Wanderers Wasps": "Greenock Wanderers RFC",
    "Hawick Force": "Hawick RFC",
    "Heriot's Rugby Women": "Heriot's Rugby Club",
    "Hillfoots Vixens": "Hillfoots RFC",
    "Howe Crusaders": "Howe of Fife RFC",
    "Inverness Craig Dunain Women": "Inverness Craig Dunain RFC",
    "Kelso Sharks": "Kelso RFC",
    "Melrose Storm": "Melrose RFC",
    "Orkney Dragons": "Orkney RFC",
    "Peebles Reds": "Peebles RFC",
    "Shetland Valkyries": "Shetland RFC",
    "Fraserburgh Women": "Fraserburgh RFC",
    "Peterhead Women": "Peterhead RFC",
    "Hamilton Bulls": "Hamilton Rugby Club",
}

_NAME_FIXES: dict[str, str] = {
    "Heriot's Rugby Men": "Heriot's Rugby Club",
    "Melrose Rugby": "Melrose RFC",
    "Dundee Rugby": "Dundee Rugby Club",
    "Dundee Rugby 2XV": "Dundee Rugby Club",
    "Dundee Uni Medics": "Dundee University Medics",
    "Allan Glen's RFC": "Allan Glen's RFC",
    "Crieff & Strathearn RFC": "Crieff & Strathearn RFC",
    "Stewart's Melville RFC": "Stewart's Melville RFC",
    "Loch Lomond / Helensburgh": "Loch Lomond RFC",
    "Jed-Forest A": "Jed-Forest RFC",
    "Selkirk A": "Selkirk RFC",
    "Strathmore 2nd XV": "Strathmore RFC",
}


def _decode_html_entities(text: str) -> str:
    """Decode HTML entities like &#8217; and &#038;."""
    import html as html_module

    return html_module.unescape(text)


# --------------------------------------------------------------------- #
# Favicon fetching
# --------------------------------------------------------------------- #

_favicon_cache: dict[str, str | None] = {}


def _load_favicon_cache() -> None:
    """Load the on-disk favicon cache into memory."""
    global _favicon_cache
    if _FAVICON_CACHE_FILE.exists():
        with open(_FAVICON_CACHE_FILE, encoding="utf-8") as f:
            _favicon_cache = json.load(f)


def _save_favicon_cache() -> None:
    _FAVICON_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FAVICON_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(_favicon_cache, f, indent=2, ensure_ascii=False)


def _fetch_favicon_from_site(website: str) -> str | None:
    """Fetch a website and extract the best icon URL from <link> tags.

    Prefers apple-touch-icon (largest), then icon with sizes, then any icon.
    Falls back to /favicon.ico if no link tags found.
    """
    import html as html_module
    import subprocess
    from urllib.parse import urljoin

    url = website if website.startswith(("http://", "https://")) else f"http://{website}"

    try:
        result = subprocess.run(
            ["curl", "-sL", "--max-time", "10", url],
            capture_output=True,
            timeout=15,
        )
        html_bytes = result.stdout
        html_text = html_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"    Favicon fetch failed for {url}: {exc}")
        return None

    if not html_text:
        return None

    raw_links = re.findall(r"<link[^>]+>", html_text, re.IGNORECASE)

    best: str | None = None
    best_priority = -1

    for tag in raw_links:
        rel_match = re.search(r"rel\s*=\s*[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
        href_match = re.search(r"href\s*=\s*[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
        if not rel_match or not href_match:
            continue

        rel = rel_match.group(1).lower()
        href = html_module.unescape(href_match.group(1))

        if "icon" not in rel:
            continue

        sizes_match = re.search(r"sizes\s*=\s*[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
        size = 0
        if sizes_match:
            dim = sizes_match.group(1).split("x")[0]
            with contextlib.suppress(ValueError):
                size = int(dim)

        if "apple-touch-icon" in rel:
            priority = 200 + size
        elif size > 0:
            priority = 100 + size
        else:
            priority = 50

        if priority > best_priority:
            best_priority = priority
            best = href

    if best:
        if not best.startswith(("http://", "https://", "//")):
            best = urljoin(url, best)
        elif best.startswith("//"):
            best = "https:" + best
        return best

    return urljoin(url, "/favicon.ico")


def fetch_favicon(website: str) -> str | None:
    """Get the favicon URL for a website, using the on-disk cache."""
    if website in _favicon_cache:
        return _favicon_cache[website]

    print(f"    Fetching favicon for {website}")
    icon = _fetch_favicon_from_site(website)
    _favicon_cache[website] = icon
    return icon


class ClubInfo:
    """Parsed club record from the find-a-club page."""

    __slots__ = ("id", "title", "lat", "lng", "address", "website", "url", "location")

    def __init__(self, raw: dict) -> None:
        self.id: int = raw["id"]
        self.title: str = _decode_html_entities(raw["title"])
        self.lat: float = raw["lat"]
        self.lng: float = raw["lng"]
        self.address: str | None = (
            _decode_html_entities(raw["address"]) if raw.get("address") else None
        )
        self.website: str | None = raw.get("website") or None
        self.url: str | None = raw.get("url") or None
        self.location: str | None = (
            _decode_html_entities(raw["location"]) if raw.get("location") else None
        )


def fetch_club_directory() -> list[ClubInfo]:
    """Download and parse the club directory from the find-a-club page.

    The page embeds ``window.Clubs.markers = [...]`` containing all clubs.
    Results are cached to disk so we only hit the server once.
    """
    if _CLUB_CACHE_FILE.exists():
        print(f"Loading cached club directory from {_CLUB_CACHE_FILE}")
        with open(_CLUB_CACHE_FILE, encoding="utf-8") as f:
            raw_list = json.load(f)
        return [ClubInfo(c) for c in raw_list]

    print(f"Fetching club directory from {_FIND_A_CLUB_URL}")
    import subprocess

    result = subprocess.run(
        ["curl", "-sL", _FIND_A_CLUB_URL],
        capture_output=True,
        text=True,
        timeout=30,
    )
    html = result.stdout

    marker = "window.Clubs.markers ="
    idx = html.find(marker)
    if idx < 0:
        raise RuntimeError("Could not find window.Clubs.markers in find-a-club page")

    arr_start = html.index("[", idx)
    arr_end = html.index("}]", arr_start) + 2
    raw_list = json.loads(html[arr_start:arr_end])

    print(f"  Found {len(raw_list)} clubs")

    _CLUB_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CLUB_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(raw_list, f, indent=2, ensure_ascii=False)
    print(f"  Cached to {_CLUB_CACHE_FILE}")

    return [ClubInfo(c) for c in raw_list]


def _normalize(name: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace for comparison."""
    name = name.lower()
    name = name.replace("\u2019", "'").replace("\u2018", "'")
    name = re.sub(r"[''`]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def build_club_lookup(clubs: list[ClubInfo]) -> dict[str, ClubInfo]:
    """Build a title -> ClubInfo mapping (exact, case-insensitive)."""
    lookup: dict[str, ClubInfo] = {}
    for club in clubs:
        lookup[_normalize(club.title)] = club
    return lookup


def match_team_to_club(
    team_name: str,
    lookup: dict[str, ClubInfo],
) -> ClubInfo | None:
    """Try to match a team name to a club using several strategies."""
    norm = _normalize(team_name)

    # 1. Exact match
    if norm in lookup:
        return lookup[norm]

    # 2. Explicit name fixes / women's nicknames
    for mapping in (_NAME_FIXES, _WOMENS_NICKNAMES):
        if team_name in mapping:
            fixed = _normalize(mapping[team_name])
            if fixed in lookup:
                return lookup[fixed]

    # 3. Strip reserve suffixes (2nd XV, A, etc.)
    stripped = _RESERVE_SUFFIXES.sub("", team_name).strip()
    if stripped != team_name:
        return match_team_to_club(stripped, lookup)

    # 4. Try partial matching: team name starts with club name or vice-versa
    for club_norm, club in lookup.items():
        if norm.startswith(club_norm) or club_norm.startswith(norm):
            return club

    return None


def process_season(season: str) -> None:
    """Process all league files for a season."""
    clubs = fetch_club_directory()
    lookup = build_club_lookup(clubs)
    _load_favicon_cache()

    league_base = DATA_DIR / "league_data" / season
    if not league_base.exists():
        print(f"Error: {league_base} not found. Run scrape_leagues.py first.")
        return

    output_base = DATA_DIR / "team_addresses" / season

    league_files = sorted(f for f in league_base.rglob("*.json") if not f.name.startswith("_"))
    print(f"Found {len(league_files)} league files to process")

    unmatched_teams: list[str] = []

    for league_file in league_files:
        relative = league_file.relative_to(league_base)
        output_file = output_base / relative

        if output_file.exists():
            print(f"Skipping {league_file.name} (already exists)")
            continue

        with open(league_file, encoding="utf-8") as f:
            league_data = json.load(f)

        league_name = league_data["league_name"]
        print(f"\nProcessing: {league_name}")

        address_teams = []

        for team in league_data["teams"]:
            team_name = team["name"]
            club = match_team_to_club(team_name, lookup)

            if club:
                icon_url = fetch_favicon(club.website) if club.website else None
                address_teams.append(
                    {
                        "name": team_name,
                        "url": club.url,
                        "image_url": icon_url,
                        "address": club.address,
                        "website": club.website,
                    }
                )
                print(f"  {team_name} -> {club.title} ({club.address})")
            else:
                address_teams.append(
                    {
                        "name": team_name,
                        "url": None,
                        "image_url": None,
                        "address": None,
                        "website": None,
                    }
                )
                unmatched_teams.append(f"{team_name} ({league_name})")
                print(f"  {team_name} -> NO MATCH")

        output_data = {
            "league_name": league_name,
            "league_url": league_data["league_url"],
            "teams": address_teams,
            "team_count": len(address_teams),
        }

        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        matched = sum(1 for t in address_teams if t["address"])
        print(f"  Saved: {output_file} ({matched}/{len(address_teams)} matched)")

    _save_favicon_cache()

    print(f"\n{'='*80}")
    print(f"Complete! Address data saved to {output_base}")
    print(f"{'='*80}")

    if unmatched_teams:
        print(f"\nUNMATCHED TEAMS ({len(unmatched_teams)}):")
        for t in sorted(set(unmatched_teams)):
            print(f"  {t}")
    else:
        print("\nAll teams matched!")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch addresses for Scottish Rugby teams from club directory."
    )
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to process (e.g. 2025-2026). Default: 2025-2026",
    )
    parser.add_argument(
        "--refresh-clubs",
        action="store_true",
        help="Re-download the club directory (ignore cache)",
    )
    args = parser.parse_args()

    if args.refresh_clubs and _CLUB_CACHE_FILE.exists():
        _CLUB_CACHE_FILE.unlink()
        print("Cleared club directory cache")

    process_season(args.season)


if __name__ == "__main__":
    main()
