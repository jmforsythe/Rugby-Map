"""Parse current-season member clubs from English Wikipedia league articles."""

from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup, Tag

from football.fetch_pyramid import _extract_club_from_cell

_SEASON_PREFIX_RE = re.compile(r"^\d{4}\s*[\u2013\u2014\u2212\-–—]\s*\d{2,4}\s+")
_CHAMPION_ROW_RE = re.compile(r"^\d{4}\s*[\u2013\u2014\u2212\-–—]\s*\d{2,4}\s*$")
_INVALID_NAMES = frozenset(
    {
        "official site",
        "see also",
        "references",
        "external links",
        "edit",
        "n/a",
        "tbc",
        "tba",
    }
)


def _normalize_heading(title: str) -> str:
    """Strip footnotes and parenthetical notes from a section heading."""
    text = re.sub(r"\s+", " ", title.strip())
    text = re.sub(r"\(.*?\)", "", text).strip()
    return text


def _heading_match_key(title: str) -> str:
    """Compact key for fuzzy division matching (e.g. Premier East)."""
    text = _normalize_heading(title).casefold()
    text = re.sub(r"\bdivision\b", " ", text)
    text = re.sub(r"\bpremier\b", " premier ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_member_section_heading(title: str) -> bool:
    """True if this h2 introduces the current-season member-club list."""
    text = title.casefold()
    if "champion" in text:
        return False
    if "member clubs" in text or "current clubs" in text or "current member" in text:
        return True
    if re.search(r"\bmembers?\b", text) and "champion" not in text:
        return True
    if re.search(r"20\d{2}.*\bteams?\b", text):
        return True
    if re.search(r"20\d{2}.*\bmembers?\b", text):
        return True
    return False


def division_heading_matches(heading_text: str, division_hint: str) -> bool:
    """Return True if ``heading_text`` names the requested division."""
    head = _normalize_heading(heading_text)
    hint = _normalize_heading(division_hint)
    head_cf = head.casefold()
    hint_cf = hint.casefold()

    if head_cf == hint_cf:
        return True

    head_key = _heading_match_key(head)
    hint_key = _heading_match_key(hint)
    if head_key and head_key == hint_key:
        return True

    # "Premier Division (East)" vs "Premier Division East"
    if hint_cf in head_cf:
        prefix = head_cf[: head_cf.index(hint_cf)].strip()
        if prefix and not prefix.endswith(("league", "alliance", "combination", "counties")):
            return False
        suffix = head_cf[len(hint_cf) :].strip()
        if suffix.startswith(("(", "-", "–", ",")):
            return True
        if not suffix or suffix in {"north", "south", "east", "west"}:
            return True

    if (
        "premier" in hint_cf
        and "division one" not in hint_cf
        and not re.search(r"\bpremier\s+division\b", hint_cf)
        and head_cf in ("division one", "1st division", "first division")
    ):
        return True
    if "division one" in hint_cf and head_cf == "division one":
        return True
    if "division two" in hint_cf and head_cf.startswith("division two"):
        return True
    if "senior" in hint_cf and "senior" in head_cf:
        return True
    if "supreme" in hint_cf and "supreme" in head_cf:
        return True
    return False


def is_valid_club_name(name: str) -> bool:
    """Reject champion-list rows, table debris, and navigation labels."""
    text = re.sub(r"\s+", " ", name.strip())
    if not text or len(text) < 3:
        return False
    if text.casefold() in _INVALID_NAMES:
        return False
    if _SEASON_PREFIX_RE.match(text):
        return False
    if _CHAMPION_ROW_RE.match(text):
        return False
    if re.fullmatch(r"\d+(?:st|nd|rd|th)?(?:\s*\[[^\]]*\])?", text, re.IGNORECASE):
        return False
    if re.fullmatch(r"\d{4}[\u2013\u2014\-–—]\d{2,4}", text):
        return False
    if text.startswith(("http://", "https://", "www.")):
        return False
    if "full-time" in text.casefold() or "fulltime" in text.casefold():
        return False
    return True


def clubs_from_ul(ul: Tag, seen: set[str]) -> list[dict]:
    clubs: list[dict] = []
    for li in ul.find_all("li", recursive=False):
        name = re.sub(r"\s*\(.*$", "", li.get_text(" ", strip=True)).strip()
        if not is_valid_club_name(name) or name.casefold() in seen:
            continue
        link = li.find("a", href=True)
        wiki_title = None
        if link and link["href"].startswith("/wiki/"):
            wiki_title = urllib.parse.unquote(link["href"].split("/wiki/")[-1])
        clubs.append({"name": name, "wiki_title": wiki_title})
        seen.add(name.casefold())
    return clubs


def clubs_from_wikitable(table: Tag, seen: set[str]) -> list[dict]:
    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    if "club" not in headers:
        return []
    club_idx = headers.index("club")
    clubs: list[dict] = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= club_idx:
            continue
        extracted = _extract_club_from_cell(cells[club_idx])
        if extracted:
            name, wiki_title = extracted
        else:
            name = re.sub(r"\s*\(.*$", "", cells[club_idx].get_text(" ", strip=True)).strip()
            wiki_title = None
        if not is_valid_club_name(name) or name.casefold() in seen:
            continue
        clubs.append({"name": name, "wiki_title": wiki_title})
        seen.add(name.casefold())
    return clubs


def wikitable_after_heading(heading: Tag) -> Tag | None:
    """Return the first Club column wikitable after ``heading`` before the next section."""
    stop_level = {"h2": 2, "h3": 3, "h4": 4}.get(heading.name, 3)
    for el in heading.find_all_next():
        if el.name in ("h2", "h3", "h4") and el is not heading:
            level = {"h2": 2, "h3": 3, "h4": 4}[el.name]
            if level <= stop_level:
                break
        if el.name != "table" or "wikitable" not in el.get("class", []):
            continue
        headers = [th.get_text(strip=True).lower() for th in el.find_all("th")]
        if "club" in headers:
            return el
    return None


def parse_wikipedia_member_clubs(html: str, *, division_hint: str = "") -> list[dict]:
    """Parse club names from a league article's current-season member section."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    hint = division_hint.strip()

    member_h2: Tag | None = None
    in_member_section = False

    for heading in soup.find_all(["h2", "h3", "h4"]):
        title = heading.get_text(" ", strip=True)

        if heading.name == "h2":
            if is_member_section_heading(title):
                in_member_section = True
                member_h2 = heading
                continue
            if in_member_section:
                break
            continue

        if not in_member_section:
            continue

        if heading.name == "h3" and hint:
            if not division_heading_matches(title, hint):
                continue
            table = wikitable_after_heading(heading)
            if table:
                clubs = clubs_from_wikitable(table, seen)
                if clubs:
                    return clubs
            ul = heading.find_next("ul")
            if ul:
                clubs = clubs_from_ul(ul, seen)
                if clubs:
                    return clubs

    if member_h2 and hint:
        for h3 in member_h2.find_all_next("h3"):
            if h3.find_previous("h2") != member_h2:
                break
            title = h3.get_text(" ", strip=True)
            if not division_heading_matches(title, hint):
                continue
            table = wikitable_after_heading(h3)
            if table:
                clubs = clubs_from_wikitable(table, seen)
                if clubs:
                    return clubs
            ul = h3.find_next("ul")
            if ul:
                clubs = clubs_from_ul(ul, seen)
                if clubs:
                    return clubs

    if member_h2 and not hint:
        table = wikitable_after_heading(member_h2)
        if table:
            clubs = clubs_from_wikitable(table, seen)
            if clubs:
                return clubs
        ul = member_h2.find_next("ul")
        if ul:
            clubs = clubs_from_ul(ul, seen)
            if clubs:
                return clubs

    # Single-tier league: one club list directly under the member h2 (e.g. Surrey).
    if member_h2:
        has_intermediate_h3 = False
        for sibling in member_h2.find_next_siblings():
            if sibling.name == "h2":
                break
            if sibling.name == "h3":
                has_intermediate_h3 = True
                break
        if not has_intermediate_h3:
            ul = member_h2.find_next("ul")
            if ul:
                clubs = clubs_from_ul(ul, seen)
                if clubs:
                    return clubs

    return []
