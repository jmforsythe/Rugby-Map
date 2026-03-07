from __future__ import annotations

import argparse
import json
import re
import urllib.parse
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from utils import AntiBotDetectedError, League, LeagueInfo, Team, make_request

_PYRAMID_COMPETITIONS = [
    1699,  # South West
    1597,  # Midlands
    261,  # London and SE
    1623,  # Northern
    1605,  # National Leagues
]

_MERIT_COMPETITIONS = [
    183,  # 'IMPACT' Rugby North West Leagues
    202,  # Hampshire Merit Tables
    1600,  # Midlands Reserve Team Leagues
    252,  # Leicestershire Competitions
    1694,  # Group 1 Automotive Essex Merit League
    180,  # Yorkshire League & Merit Tables
    100,  # East Midlands Leagues
    # 1729,  # Surrey County Leagues — excluded: teams are mostly 2nd XVs that
    #        # overlap with pyramid clubs, producing co-location noise on maps
    1770,  # GRFU District Leagues
    77,  # Devon Merit Tables
    206,  # Harvey's Brewery Sussex Leagues
    104,  # Eastern Counties Greene King
    1596,  # Middlesex Merit Tables
    1681,  # Rural Kent Leagues
    1636,  # Nottinghamshire RFU Security Plus Pennant
    49,  # CANDY League
    209,  # Hertfordshire & Middlesex Merit Tables
]


def _competition_urls(competition_ids: list[int], season: str) -> list[str]:
    return [
        f"https://www.englandrugby.com/fixtures-and-results/search-results?competition={c}&season={season}"
        for c in competition_ids
    ]


def get_meta_league_urls(season: str) -> list[str]:
    """Get pyramid meta league URLs for the given season."""
    return _competition_urls(_PYRAMID_COMPETITIONS, season)


def get_merit_meta_league_urls(season: str) -> list[str]:
    """Get merit/district meta league URLs for the given season."""
    return _competition_urls(_MERIT_COMPETITIONS, season)


PREM_MAP = {
    "2025-2026": "68225",
    "2024-2025": "67686",
    "2023-2024": "51168",
    "2022-2023": "42405",
    "2021-2022": "35441",
    "2020-2021": "69325",
    "2019-2020": "69319",
    "2018-2019": "69315",
    "2017-2018": "69311",
    "2016-2017": "24",
    "2015-2016": "69308",
    "2014-2015": "",
    "2013-2014": "",
    "2012-2013": "",
    "2011-2012": "",
    "2010-2011": "",
    "2009-2010": "",
}

CHAMP_MAP = {
    "2025-2026": "67198",
    "2024-2025": "57597",
    "2023-2024": "47253",
    "2022-2023": "39369",
    "2021-2022": "33636",
    "2020-2021": "31117",
    "2019-2020": "21751",
    "2018-2019": "14205",
    "2017-2018": "11215",
    "2016-2017": "10222",
    "2015-2016": "9209",
    "2014-2015": "8279",
    "2013-2014": "7391",
    "2012-2013": "6585",
    "2011-2012": "5782",
    "2010-2011": "4971",
    "2009-2010": "4252",
}


def get_leagues(season: str) -> list[LeagueInfo]:
    """Get initial league list for the given season."""
    return [
        {
            "name": "Premiership",
            "url": f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=5&division={PREM_MAP[season]}&season={season}",
            "parent_url": "https://www.englandrugby.com/fixtures-and-results",
        },
        {
            "name": "Championship",
            "url": f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=173&division={CHAMP_MAP[season]}&season={season}",
            "parent_url": "https://www.englandrugby.com/fixtures-and-results",
        },
    ]


def get_womens_meta_league_urls(season: str) -> list[str]:
    """Get women's meta league URLs for the given season."""
    return [
        f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=1782&season={season}"
    ]


WOMENS_PREM_MAP = {
    "2025-2026": "68284",
    "2024-2025": "58646",
    "2023-2024": "49157",
    "2022-2023": "41643",
    "2021-2022": "33312",
    "2020-2021": "31109",
    "2019-2020": "24448",
    "2018-2019": "14816",
    "2017-2018": "11607",
}


def get_womens_leagues(season: str) -> list[LeagueInfo]:
    """Get initial women's league list for the given season."""
    if season < "2017-2018":
        return []
    return [
        {
            "name": "Women's Premiership",
            "url": f"https://www.englandrugby.com/fixtures-and-results/search-results?competition=1764&division={WOMENS_PREM_MAP[season]}&season={season}",
            "parent_url": "https://www.englandrugby.com/fixtures-and-results",
        }
    ]


def _meta_cache_path(season: str) -> Path:
    return Path("league_data") / season / "_meta_leagues_cache.json"


def load_meta_cache(season: str) -> dict[str, list[LeagueInfo]] | None:
    """Load cached meta league results for a season, if available."""
    path = _meta_cache_path(season)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_meta_cache(season: str, cache: dict[str, list[LeagueInfo]]) -> None:
    """Save meta league results to disk for a season."""
    path = _meta_cache_path(season)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    print(f"  Meta league cache saved to {path}")


def scrape_meta_leagues(
    meta_urls: list[str], season: str
) -> tuple[list[LeagueInfo], dict[str, list[LeagueInfo]]]:
    """Scrape leagues from meta URLs, using and updating a disk cache.

    Returns (leagues, updated_cache).
    """
    cache = load_meta_cache(season) or {}
    leagues: list[LeagueInfo] = []

    for meta_url in meta_urls:
        if meta_url in cache:
            cached = cache[meta_url]
            print(f"\n  Using cached results for {meta_url} ({len(cached)} leagues)")
            leagues.extend(cached)
        else:
            scraped = scrape_leagues_from_page(meta_url)
            cache[meta_url] = scraped
            leagues.extend(scraped)

    save_meta_cache(season, cache)
    return leagues, cache


def clean_filename(text: str) -> str:
    """Convert text to a safe filename"""
    # Remove or replace invalid filename characters
    text = re.sub(r'[<>:"/\\|?*]', "_", text)
    text = re.sub(r"\s+", "_", text)
    return text.strip("_")


def _is_all_zero_row(row: Tag, team_cell: Tag) -> bool:
    """Check whether every stat column in a table row is zero.

    Teams with all zeros were never actually in this league (e.g. listed
    due to an admin error on the source website).
    """
    stat_cells = [td for td in row.find_all("td") if td is not team_cell]
    numeric_values: list[int] = []
    for td in stat_cells:
        text = td.get_text(strip=True).lstrip("-")
        if text.isdigit():
            numeric_values.append(int(text))
    return len(numeric_values) > 0 and all(v == 0 for v in numeric_values)


def scrape_teams_from_league(
    league_url: str, league_name: str, season: str, referer: str | None = None
) -> list[Team]:
    print(f"Scraping teams from: {league_url}")

    response = make_request(league_url, referer=referer, delay_seconds=1)

    # Check for anti-bot 202 response
    if response.status_code == 202:
        print(f"    \u2717 202 code - bot detection triggered for {league_name}")
        raise AntiBotDetectedError(f"202 response for {league_name}")

    soup = BeautifulSoup(response.content, "html.parser")

    teams = []

    # Find all table cells with class containing "coh-style-team-name"
    team_cells = soup.find_all(
        "td", class_=lambda x: isinstance(x, str) and "coh-style-team-name" in x
    )

    skipped_zero_teams: list[str] = []

    for cell in team_cells:
        # Find the href within the cell
        link = cell.find("a", href=True)
        if link:
            team_name = link.get_text(strip=True)
            team_url = link["href"]

            # Make absolute URL if needed
            if team_url.startswith("/"):
                team_url = f"https://www.englandrugby.com{team_url}"

            row = cell.find_parent("tr")
            if row and _is_all_zero_row(row, cell):
                skipped_zero_teams.append(team_name)
                continue

            # Find image sibling
            img = cell.find("img")
            team_image_url: str | None = None
            if img and img.get("src"):
                team_image_url = img["src"]
                # Make absolute URL if needed
                if team_image_url and team_image_url.startswith("/"):
                    team_image_url = f"https://www.englandrugby.com{team_image_url}"

            teams.append({"name": team_name, "url": team_url, "image_url": team_image_url})

    if skipped_zero_teams:
        print(
            f"  Skipped {len(skipped_zero_teams)} all-zero teams: {', '.join(skipped_zero_teams)}"
        )
    print(f"  Found {len(teams)} teams in {league_name}")
    return teams


def scrape_leagues_from_page(page_url: str) -> list[LeagueInfo]:
    """Scrape league links from the related-leagues-overview div"""
    print(f"\nScraping leagues from: {page_url}")

    response = make_request(page_url, delay_seconds=0.5)

    # Check for anti-bot 202 response
    if response.status_code == 202:
        print("  ✗ 202 code - bot detection triggered")
        raise AntiBotDetectedError(f"202 response for {page_url}")

    soup = BeautifulSoup(response.content, "html.parser")

    # Find the div with id "related-leagues-overview"
    leagues_div = soup.find("div", id="related-leagues-overview")

    if not isinstance(leagues_div, Tag):
        print('  Warning: Could not find div with id "related-leagues-overview"')
        return []

    leagues: list[LeagueInfo] = []

    # Find all hrefs within this div
    links = leagues_div.find_all("a", href=True)

    for link in links:
        league_name = link.get_text(strip=True)
        league_url = link["href"]

        # Make absolute URL if needed
        if league_url.startswith("/"):
            league_url = f"https://www.englandrugby.com{league_url}"

        if league_name and league_url:
            leagues.append({"name": league_name, "url": league_url, "parent_url": page_url})

    print(f"  Found {len(leagues)} leagues")
    return leagues


_BANNED_WORDS = [
    "playoff",
    "play",
    "play-off",
    "phase",
    "shield",
    "trophy",
    "plate",
    "salver",
    "bowl",
    "1a",
    "1b",
    "2a",
    "2b",
    "cup",
    "pool",
    "vets",
    "veterans",
    "vase",
    "colts",
    "play-offs",
    "scrapped",
    "u18",
]

_BANNED_FILENAMES = [
    "Yorkshire_Division_Four_Premier.json",
    "Pilot_League.json",
    "Tribute_Duchy_League.json",
    "MRL3_Play_Off_1-8.json",
    "MRL3_Play_Off_9-13.json",
    "National_League_Play_Offs.json",
    "Social_Rugby_Group.json",
    "Solent_1_Play_Off.json",
    "London_and_SE_Division_Play-Offs.json",
    "Area_2_Merit_League.json",
    "Bombardier___Eagle_2017.json",
    "Bristol_&_District_3-4.json",
    "Gloucester_&_District_3-4.json",
    "Leicestershire_U18_League.json",
]


_COMPETITION_NAMES: dict[str, str] = {
    "183": "NOWIRUL",
    "202": "Hampshire",
    "1600": "Midlands_Reserve",
    "252": "Leicestershire",
    "1694": "Essex",
    "180": "Yorkshire",
    "100": "East_Midlands",
    "1770": "GRFU_District",
    "77": "Devon",
    "206": "Sussex",
    "104": "Eastern_Counties",
    "1596": "Middlesex",
    "1681": "Rural_Kent",
    "1636": "Nottinghamshire",
    "49": "CANDY",
    "209": "Herts_Middlesex",
    # "1729": "Surrey",
}


def _competition_prefix(parent_url: str) -> str | None:
    """Extract a human-readable competition prefix from a parent URL's competition ID."""
    parsed = urllib.parse.urlparse(parent_url)
    params = urllib.parse.parse_qs(parsed.query)
    comp_ids = params.get("competition", [])
    if comp_ids and comp_ids[0] in _COMPETITION_NAMES:
        return _COMPETITION_NAMES[comp_ids[0]]
    return None


def _scrape_league_list(
    leagues: list[LeagueInfo],
    output_dir: Path,
    season: str,
    *,
    use_competition_subdirs: bool = False,
) -> list[LeagueInfo]:
    """Scrape teams for each league and save to output_dir. Returns skipped leagues.

    When use_competition_subdirs is True, files are saved into subdirectories
    named after the competition (e.g. merit/Hampshire/Counties_6_South.json).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    skipped: list[LeagueInfo] = []

    for league in leagues:
        league_name = league["name"]
        league_url = league["url"]
        parent_url = league["parent_url"]

        if any(word in league_name.lower().split() for word in _BANNED_WORDS):
            print(f"Skipping {league_name} (playoff/phase league)")
            skipped.append(league)
            continue

        filename = clean_filename(league_name) + ".json"
        if use_competition_subdirs:
            comp_name = _competition_prefix(parent_url)
            if comp_name:
                league_output_dir = output_dir / comp_name
                league_output_dir.mkdir(parents=True, exist_ok=True)
                output_path = league_output_dir / filename
            else:
                output_path = output_dir / filename
        else:
            output_path = output_dir / filename

        if output_path.name in _BANNED_FILENAMES:
            print(f"Skipping {league_name} (known bad filename)")
            skipped.append(league)
            continue

        if output_path.exists():
            print(f"Skipping {league_name} (already exists)")
            continue

        parsed_url = urllib.parse.urlparse(league_url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        if "season" not in query_params:
            query_params["season"] = [season]
        new_query = urllib.parse.urlencode(query_params, doseq=True)
        league_url = urllib.parse.urlunparse(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                parsed_url.params,
                new_query,
                "tables",
            )
        )

        try:
            teams = scrape_teams_from_league(league_url, league_name, season, referer=parent_url)

            if len(teams) == 0:
                print(f"  Skipping saving {league_name} (no teams found)")
                skipped.append(league)
                continue

            league_data: League = {
                "league_name": league_name,
                "league_url": league_url,
                "teams": teams,
                "team_count": len(teams),
            }

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(league_data, f, indent=2, ensure_ascii=False)

            print(f"    Saved to: {output_path}")

        except AntiBotDetectedError:
            print(f"\n✗ Anti-bot detection triggered while scraping {league_name}")
            print("Please wait before running the script again.")
            raise

    return skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape RFU website for league and team data.")
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to scrape (e.g., 2024-2025, 2025-2026). Default: 2025-2026",
    )
    args = parser.parse_args()
    season = args.season

    print(f"Scraping data for season: {season}")

    base_dir = Path("league_data") / season
    merit_dir = base_dir / "merit"

    # --- Pyramid leagues ---
    leagues = get_leagues(season)
    try:
        meta_leagues, _ = scrape_meta_leagues(get_meta_league_urls(season), season)
        leagues.extend(meta_leagues)
    except AntiBotDetectedError as e:
        print(f"\n✗ Anti-bot detection triggered while scraping meta leagues: {e}")
        return

    # --- Merit / district leagues ---
    merit_leagues: list[LeagueInfo] = []
    try:
        merit_meta, _ = scrape_meta_leagues(get_merit_meta_league_urls(season), season)
        merit_leagues.extend(merit_meta)
    except AntiBotDetectedError as e:
        print(f"\n✗ Anti-bot detection triggered while scraping merit meta leagues: {e}")
        return

    # --- Women's leagues ---
    womens_leagues = get_womens_leagues(season)
    try:
        womens_meta, _ = scrape_meta_leagues(get_womens_meta_league_urls(season), season)
        womens_leagues.extend(womens_meta)
    except AntiBotDetectedError as e:
        print(f"\n✗ Anti-bot detection triggered while scraping women's meta leagues: {e}")
        return

    skipped_leagues: list[LeagueInfo] = []
    try:
        skipped_leagues += _scrape_league_list(leagues + womens_leagues, base_dir, season)
        skipped_leagues += _scrape_league_list(
            merit_leagues, merit_dir, season, use_competition_subdirs=True
        )
    except AntiBotDetectedError:
        return

    print(f'\nComplete! League data saved to "{base_dir}" directory')
    for skipped in skipped_leagues:
        print(f"Skipped league {skipped["name"]}: {skipped["url"]}")


if __name__ == "__main__":
    main()
