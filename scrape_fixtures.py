"""
Scrape upcoming and past fixtures from RFU league pages.

Reads league URLs from geocoded_teams/<season>/ and scrapes the fixtures view
of each league page. Outputs one JSON file per league to fixture_data/<season>/,
mirroring the geocoded_teams/ directory structure.

Run locally: python scrape_fixtures.py --season 2025-2026
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from utils import (
    AntiBotDetectedError,
    Fixture,
    FixtureLeague,
    GeocodedLeague,
    make_request,
    setup_logging,
)

logger = logging.getLogger(__name__)

_RFU_BASE = "https://www.englandrugby.com"


def _parse_team_id(url: str) -> int | None:
    """Extract the numeric team ID from an RFU team URL query parameter."""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    team_ids = params.get("team", [])
    if team_ids and team_ids[0].isdigit():
        return int(team_ids[0])
    return None


def _abs_url(url: str) -> str:
    """Turn a possibly-relative URL into an absolute one."""
    if url.startswith("/"):
        return f"{_RFU_BASE}{url}"
    return url


def _parse_date(date_text: str) -> str:
    """Parse 'Saturday, 11 Apr 2026' into ISO 'YYYY-MM-DD'."""
    cleaned = re.sub(r"^[A-Za-z]+,\s*", "", date_text.strip())
    try:
        dt = datetime.strptime(cleaned, "%d %b %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        logger.warning("Could not parse date: %r", date_text)
        return ""


def _league_url_to_fixtures_url(league_url: str) -> str:
    """Rewrite a league #tables URL to its #fixtures view."""
    parsed = urllib.parse.urlparse(league_url)
    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            "fixtures",
        )
    )


def scrape_fixtures_from_league(league_url: str, league_name: str) -> list[Fixture]:
    """Scrape all fixtures from one league's fixtures page."""
    fixtures_url = _league_url_to_fixtures_url(league_url)
    logger.info("Scraping fixtures from: %s", fixtures_url)

    referer = f"{_RFU_BASE}/fixtures-and-results"
    response = make_request(fixtures_url, referer=referer, delay_seconds=1)
    soup = BeautifulSoup(response.content, "html.parser")

    fixtures: list[Fixture] = []
    current_date = ""

    card_headers = soup.find_all(
        "div",
        class_=lambda x: isinstance(x, str) and "coh-style-card-left-date" in x,
    )

    for date_div in card_headers:
        date_text = date_div.get_text(strip=True)
        parsed_date = _parse_date(date_text)
        if not parsed_date:
            continue
        current_date = parsed_date

        card_header = date_div.find_parent(
            "div", class_=lambda x: isinstance(x, str) and "cardLayout" in x
        )
        if not card_header:
            continue

        body_class = f"cardContainer_{date_text.replace(' ', '')}"
        card_bodies = card_header.find_next_siblings(
            "div",
            class_=lambda x, bc=body_class: isinstance(x, str) and bc in x.replace(" ", ""),
        )
        if not card_bodies:
            card_bodies = card_header.find_next_siblings("div", class_="coh-style-card-body")

        for card_body in card_bodies:
            score_cards = card_body.find_all("div", class_="coh-style-card-scores")
            for card in score_cards:
                fixture = _parse_fixture_card(card, current_date)
                if fixture:
                    fixtures.append(fixture)

    logger.info("  Found %d fixtures in %s", len(fixtures), league_name)
    return fixtures


def _parse_fixture_card(card: Tag, date: str) -> Fixture | None:
    """Parse a single fixture card div into a Fixture dict."""
    home_div = card.find("div", class_="coh-style-hometeam")
    away_div = card.find("div", class_="coh-style-away-team")
    score_div = card.find("div", class_="fnr-scores")

    if not home_div or not away_div or not score_div:
        return None

    time_link = score_div.find("a", class_="coh-style-comp-time")
    vs_div = score_div.find("div", class_="coh-style-comp-versace") if not time_link else None
    if not time_link and not vs_div:
        return None

    kick_off = time_link.get_text(strip=True) if time_link else ""
    match_href = time_link.get("href", "") if time_link else ""
    if not match_href:
        card_body = card.parent
        if card_body:
            info_link = card_body.find("a", class_="c065-match-link")
            if info_link:
                match_href = info_link.get("href", "")
    match_url = _abs_url(match_href) if match_href else ""

    home_link = home_div.find("a", href=True)
    away_link = away_div.find("a", href=True)
    if not home_link or not away_link:
        return None

    home_href = home_link["href"]
    if home_href.startswith("/"):
        home_href = f"{_RFU_BASE}{home_href}"
    home_id = _parse_team_id(home_href)

    away_href = away_link["href"]
    if away_href.startswith("/"):
        away_href = f"{_RFU_BASE}{away_href}"
    away_id = _parse_team_id(away_href)

    if home_id is None or away_id is None:
        home_name = home_link.get_text(strip=True)
        away_name = away_link.get_text(strip=True)
        logger.warning("Could not parse team IDs for %s vs %s", home_name, away_name)
        return None

    return {
        "date": date,
        "time": kick_off,
        "home_team_id": home_id,
        "away_team_id": away_id,
        "match_url": match_url,
    }


def _discover_leagues(season: str) -> list[tuple[str, str, Path]]:
    """Read geocoded_teams/<season>/ to get (league_name, league_url, relative_output_path) tuples."""
    geocoded_dir = Path("geocoded_teams") / season
    if not geocoded_dir.exists():
        logger.error("Geocoded teams directory not found: %s", geocoded_dir)
        return []

    leagues: list[tuple[str, str, Path]] = []
    for json_file in sorted(geocoded_dir.rglob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            data: GeocodedLeague = json.load(f)

        league_name = data["league_name"]
        league_url = data.get("league_url", "")
        if not league_url:
            continue

        relative = json_file.relative_to(geocoded_dir)
        leagues.append((league_name, league_url, relative))

    return leagues


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape RFU fixtures for a season.")
    parser.add_argument(
        "--season",
        type=str,
        default="2025-2026",
        help="Season to scrape (e.g. 2025-2026). Default: 2025-2026",
    )
    args = parser.parse_args()
    season: str = args.season

    setup_logging()
    logger.info("Scraping fixtures for season: %s", season)

    leagues = _discover_leagues(season)
    logger.info("Found %d leagues in geocoded_teams/%s/", len(leagues), season)

    output_dir = Path("fixture_data") / season
    scraped = 0
    skipped = 0

    for league_name, league_url, relative_path in leagues:
        output_path = output_dir / relative_path
        if output_path.exists():
            logger.info("Skipping %s (already exists)", league_name)
            skipped += 1
            continue

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            fixtures = scrape_fixtures_from_league(league_url, league_name)
        except AntiBotDetectedError:
            logger.error("Anti-bot detection triggered while scraping %s", league_name)
            logger.error("Please wait before running the script again.")
            raise
        except Exception:
            logger.exception("Failed to scrape fixtures for %s", league_name)
            continue

        fixture_league: FixtureLeague = {
            "league_name": league_name,
            "league_url": league_url,
            "fixtures": fixtures,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(fixture_league, f, indent=2, ensure_ascii=False)

        scraped += 1
        logger.info("  Saved %d fixtures to %s", len(fixtures), output_path)

    logger.info(
        "Complete! Scraped %d leagues, skipped %d. Output in %s",
        scraped,
        skipped,
        output_dir,
    )


if __name__ == "__main__":
    main()
