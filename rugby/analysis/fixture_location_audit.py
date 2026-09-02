"""Compare RFU match-page Location fields against our home-team addresses.

Samples fixtures at random, fetches each match-centre page, and classifies whether
the published venue matches the home club address we use on match-day maps.

Usage::

    python -m rugby.analysis.fixture_location_audit
    python -m rugby.analysis.fixture_location_audit --sample 100 --season 2026-2027
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from dataclasses import dataclass
from html import unescape
from urllib.parse import unquote

from bs4 import BeautifulSoup

from core import AntiBotDetectedError, Fixture, FixtureLeague, make_request, setup_logging
from core.config import CURRENT_SEASON
from rugby import DATA_DIR
from rugby.geocode import extract_uk_postcode
from rugby.match_day import build_team_index

_STOPWORDS = {
    "the",
    "and",
    "of",
    "at",
    "in",
    "on",
    "road",
    "rd",
    "street",
    "st",
    "lane",
    "ln",
    "avenue",
    "ave",
    "drive",
    "dr",
    "close",
    "way",
    "court",
    "ct",
    "place",
    "pl",
    "common",
    "fields",
    "field",
    "park",
    "ground",
    "rugby",
    "club",
    "fc",
    "rfc",
    "united",
    "kingdom",
    "uk",
    "england",
    "west",
    "midlands",
    "north",
    "south",
    "east",
    "hill",
    "green",
}


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if len(t) > 2 and t not in _STOPWORDS}


def _token_overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


def _parse_match_location(html: bytes) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for item in soup.select(".match-info-item"):
        label = item.select_one(".match-information-label")
        if not label or label.get_text(strip=True).lower() != "location":
            continue
        parts = [
            p.get_text(" ", strip=True)
            for p in item.select("p.c036-club-details-information")
            if p.get_text(strip=True).lower() != "location"
        ]
        if parts:
            return " ".join(parts)
        div_text = item.get_text(" ", strip=True)
        div_text = re.sub(r"^location\s*", "", div_text, flags=re.I).strip()
        return div_text or None

    match = re.search(r"staticmap\?center=([^&\"']+)", html.decode("utf-8", errors="replace"))
    if match:
        return unescape(unquote(match.group(1).replace("+", " "))).strip()
    return None


@dataclass
class AuditResult:
    match_id: str
    date: str
    league: str
    home_name: str
    away_name: str
    page_location: str | None
    home_address: str
    away_address: str
    verdict: str
    detail: str
    match_url: str


def _load_fixtures(season: str) -> list[tuple[Fixture, str]]:
    fixture_dir = DATA_DIR / "fixture_data" / season
    out: list[tuple[Fixture, str]] = []
    for json_file in sorted(fixture_dir.rglob("*.json")):
        if json_file.name.startswith("_"):
            continue
        with open(json_file, encoding="utf-8") as f:
            data: FixtureLeague = json.load(f)
        league_name = data["league_name"]
        for fixture in data.get("fixtures", []):
            if fixture.get("match_url"):
                out.append((fixture, league_name))
    return out


def _classify(page_loc: str, home_addr: str, away_addr: str) -> tuple[str, str]:
    page_pc = extract_uk_postcode(page_loc) or ""
    home_pc = extract_uk_postcode(home_addr) or ""
    away_pc = extract_uk_postcode(away_addr) or ""

    if page_pc and home_pc and page_pc.replace(" ", "").upper() == home_pc.replace(" ", "").upper():
        return "matches_home", f"postcode {page_pc}"
    if page_pc and away_pc and page_pc.replace(" ", "").upper() == away_pc.replace(" ", "").upper():
        return "matches_away", f"postcode {page_pc} (away ground)"

    page_n, home_n, away_n = _norm(page_loc), _norm(home_addr), _norm(away_addr)
    if page_n and (page_n in home_n or home_n in page_n):
        return "matches_home", "address substring"
    if page_n and away_n and (page_n in away_n or away_n in page_n):
        return "matches_away", "address substring (away ground)"

    home_overlap = _token_overlap(page_loc, home_addr)
    away_overlap = _token_overlap(page_loc, away_addr)
    if home_overlap >= 0.5 and home_overlap >= away_overlap + 0.15:
        return "matches_home", f"token overlap {home_overlap:.0%}"
    if away_overlap >= 0.5 and away_overlap >= home_overlap + 0.15:
        return "matches_away", f"token overlap {away_overlap:.0%} (away ground)"
    if home_overlap >= 0.35 and away_overlap >= 0.35:
        return "ambiguous", f"similar to both (home {home_overlap:.0%}, away {away_overlap:.0%})"
    if max(home_overlap, away_overlap) >= 0.25:
        best = "home" if home_overlap >= away_overlap else "away"
        return f"weak_{best}", f"weak overlap home {home_overlap:.0%}, away {away_overlap:.0%}"
    return "no_match", f"overlap home {home_overlap:.0%}, away {away_overlap:.0%}"


def run_audit(season: str, sample_size: int, seed: int) -> tuple[list[AuditResult], list[str]]:
    team_index = build_team_index(season)
    all_fixtures = _load_fixtures(season)
    random.seed(seed)
    sample = random.sample(all_fixtures, min(sample_size, len(all_fixtures)))

    results: list[AuditResult] = []
    errors: list[str] = []

    for i, (fixture, league_name) in enumerate(sample, 1):
        home = team_index.get(fixture["home_team_id"])
        away = team_index.get(fixture["away_team_id"])
        match_url = fixture["match_url"]
        match_id_match = re.search(r"matchId=(\d+)", match_url)
        match_id_s = match_id_match.group(1) if match_id_match else "?"

        if not home:
            errors.append(f"{match_id_s}: home team {fixture['home_team_id']} not in index")
            continue

        home_addr = home.get("address") or home.get("formatted_address") or ""
        away_addr = ""
        away_name = "?"
        if away:
            away_addr = away.get("address") or away.get("formatted_address") or ""
            away_name = away.get("name", "?")

        try:
            resp = make_request(
                match_url,
                referer="https://www.englandrugby.com/fixtures-and-results",
                delay_seconds=1,
            )
            page_loc = _parse_match_location(resp.content)
        except AntiBotDetectedError:
            errors.append(f"{match_id_s}: anti-bot detected — stopping")
            break
        except Exception as exc:
            errors.append(f"{match_id_s}: fetch error {exc}")
            continue

        if not page_loc:
            verdict, detail = "no_page_location", "Location field empty/missing on match page"
        else:
            verdict, detail = _classify(page_loc, home_addr, away_addr)

        results.append(
            AuditResult(
                match_id=match_id_s,
                date=fixture["date"],
                league=league_name,
                home_name=home.get("name", "?"),
                away_name=away_name,
                page_location=page_loc,
                home_address=home_addr,
                away_address=away_addr,
                verdict=verdict,
                detail=detail,
                match_url=match_url,
            )
        )
        if i % 10 == 0:
            print(f"  ... fetched {i}/{len(sample)}")

    return results, errors


def _print_report(results: list[AuditResult], errors: list[str], season: str) -> None:
    counts = Counter(r.verdict for r in results)
    print(f"\n=== FIXTURE LOCATION AUDIT (sample of {len(results)} from {season}) ===")
    for verdict, n in counts.most_common():
        print(f"  {verdict}: {n}")

    wrong = [
        r for r in results if r.verdict in {"matches_away", "no_match", "ambiguous", "weak_away"}
    ]
    print(f"\nLikely wrong or unclear ({len(wrong)}/{len(results)}):")
    for r in wrong:
        print(f"\n  [{r.verdict}] {r.date} | {r.home_name} vs {r.away_name} ({r.league})")
        print(f"    Page:  {r.page_location}")
        print(f"    Home:  {r.home_address[:120]}")
        if r.away_address:
            print(f"    Away:  {r.away_address[:120]}")
        print(f"    Detail: {r.detail}")
        print(f"    {r.match_url}")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for err in errors[:10]:
            print(f"  {err}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit fixture page locations vs home-team addresses."
    )
    parser.add_argument("--season", default=CURRENT_SEASON)
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    setup_logging()
    results, errors = run_audit(args.season, args.sample, args.seed)
    _print_report(results, errors, args.season)


if __name__ == "__main__":
    main()
