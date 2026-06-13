"""Scrape team lists from FA Full-Time league table pages."""

from __future__ import annotations

import html
import logging
import re
from urllib.parse import parse_qs

from bs4 import BeautifulSoup, Tag

from core import make_request

logger = logging.getLogger(__name__)

_FULLTIME_BASE = "https://fulltime.thefa.com"
_TABLE_CLASS = "cell-dividers"


def _parse_table_query(index_html: str) -> dict[str, str] | None:
    """Extract default ``table.html`` query params from a league index page."""
    match = re.search(
        r"table\.html\?([^\"'&]+(?:&amp;[^\"'&]+)*)",
        index_html,
        re.IGNORECASE,
    )
    if not match:
        return None
    qs = html.unescape(match.group(1))
    parsed = parse_qs(qs, keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items() if v}


def list_division_options(index_html: str) -> dict[str, str]:
    """Return ``{division_id: label}`` from league index page selects and links."""
    soup = BeautifulSoup(index_html, "html.parser")
    divisions: dict[str, str] = {}

    for opt in soup.find_all("option"):
        value = (opt.get("value") or "").strip()
        label = opt.get_text(strip=True)
        if value.isdigit() and label and not label[:4].isdigit():
            divisions[value] = label

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "selectedDivision=" not in href:
            continue
        match = re.search(r"selectedDivision=(\d+)", href)
        if not match:
            continue
        label = anchor.get_text(strip=True)
        if label and not label[:4].isdigit():
            divisions[match.group(1)] = label

    return divisions


def pick_division_id(
    divisions: dict[str, str],
    *,
    division_hint: str,
) -> str | None:
    """Choose the current-season feeder division from FA division labels."""
    if not divisions:
        return None

    hint = division_hint.casefold()
    hint_tokens = [t for t in re.split(r"\s+", hint) if t]

    def score(label: str) -> int:
        label_cf = label.casefold()
        if label_cf.startswith("20") and len(label_cf) >= 4 and label_cf[:4].isdigit():
            return -1000
        if "cup" in label_cf or "shield" in label_cf or "trophy" in label_cf:
            return -500
        if any(
            skip in label_cf
            for skip in (
                "youth",
                "reserve",
                "u21",
                "under 16",
                "under 18",
                "veteran",
                "ladies",
                "women",
                "girls",
            )
        ):
            return -500
        s = 0
        has_premier_label = any("premier" in label.casefold() for label in divisions.values())
        if "premier" in hint_tokens:
            if re.search(r"\bdivision\s+(two|three|four|2|3|4)\b", label_cf):
                s -= 90
            if label_cf == "premier division" or label_cf.startswith("premier division "):
                s += 120
            elif "premier" in label_cf and "ladies" not in label_cf:
                s += 50
            elif not has_premier_label and (
                re.search(r"\bdivision\s+1\b", label_cf) or label_cf.endswith("division 1")
            ):
                s += 110
            elif re.search(r"\bdivision\s+one\b", label_cf) and "premier" not in label_cf:
                s += 100
            elif re.search(r"\bdivision\s+(two|three)\b", label_cf) and "premier" not in label_cf:
                s -= 40
        if "senior" in hint_tokens and "senior" in label_cf:
            s += 45
        if "supreme" in hint_tokens and "supreme" in label_cf:
            s += 45
        if "division one" in hint or "div 1" in hint:
            if re.search(r"\bdivision\s+one\b", label_cf) or label_cf.endswith(" division 1"):
                s += 40
            if re.search(r"\b1st\b", label_cf):
                s += 35
        if "division two" in hint and re.search(r"\bdivision\s+two\b", label_cf):
            s += 40
        if "east" in hint_tokens and "east" in label_cf:
            s += 12
        if "west" in hint_tokens and "west" in label_cf:
            s += 12
        for token in hint_tokens:
            if len(token) > 3 and token in label_cf:
                s += 8
        if "north" in hint_tokens and "north" in label_cf:
            s += 6
        if "south" in hint_tokens and "south" in label_cf:
            s += 6
        if "premier" in label_cf:
            s += 5
        return s

    ranked = sorted(divisions.items(), key=lambda item: score(item[1]), reverse=True)
    best_id, best_label = ranked[0]
    if score(best_label) < 0:
        return None
    if len(ranked) > 1 and score(best_label) == score(ranked[1][1]):
        logger.warning(
            "Ambiguous FA division for hint %r: %r vs %r",
            division_hint,
            best_label,
            ranked[1][1],
        )
    return best_id


def fetch_league_index(fa_league_id: str) -> str:
    """Download the league landing page HTML."""
    url = f"{_FULLTIME_BASE}/index.html?league={fa_league_id}"
    response = make_request(url, delay_seconds=1.0)
    return response.text


def resolve_table_url(
    fa_league_id: str,
    *,
    division_hint: str,
    fa_division_id: str | None = None,
    index_html: str | None = None,
) -> str | None:
    """Build a ``table.html`` URL for the feeder division matching ``division_hint``."""
    html_text = index_html if index_html is not None else fetch_league_index(fa_league_id)

    params = _parse_table_query(html_text)
    if not params:
        logger.warning("No default table link for FA league %s", fa_league_id)
        return None

    division_id = fa_division_id
    if not division_id:
        divisions = list_division_options(html_text)
        division_id = pick_division_id(divisions, division_hint=division_hint)
    if division_id:
        params["selectedDivision"] = division_id

    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{_FULLTIME_BASE}/table.html?{query}"


def scrape_teams_from_table(table_url: str) -> list[dict]:
    """Scrape ``{name, url}`` teams from a Full-Time division table page."""
    logger.info("  Scraping Full-Time table: %s", table_url)
    response = make_request(table_url, delay_seconds=1.5)
    soup = BeautifulSoup(response.content, "html.parser")

    table = soup.find("table", class_=_TABLE_CLASS)
    if not table or not isinstance(table, Tag):
        logger.warning("    No league table on page")
        return []

    teams: list[dict] = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        team_cell = cells[1]
        team_name = team_cell.get_text(strip=True)
        if not team_name or team_name.startswith("*"):
            continue

        link = team_cell.find("a")
        team_url = None
        if link and link.get("href"):
            href = link["href"]
            team_url = href if href.startswith("http") else f"{_FULLTIME_BASE}{href}"

        teams.append({"name": team_name, "url": team_url})

    logger.info("    Found %d teams", len(teams))
    return teams
