"""English RFU rugby union data pipeline."""

import urllib.parse
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "rugby"

BRAND = "RugbyUnionMap"


def rfu_team_only_url(url: str | None) -> str:
    """An RFU team profile URL stripped down to just its ``team=`` query param.

    RFU search-results URLs bundle ``competition``/``division``/``season``
    params tied to the page they were scraped from. Those don't carry meaning
    outside that context (e.g. there's no single competition on the
    Constituent Body map, or on a team's own info page), so we drop everything
    but the id that actually identifies the team.
    """
    if not url:
        return url or ""
    parsed = urllib.parse.urlparse(url)
    team_vals = urllib.parse.parse_qs(parsed.query).get("team")
    query = urllib.parse.urlencode({"team": team_vals[0]}) if team_vals else ""
    return urllib.parse.urlunparse(parsed._replace(query=query))


def short_season(season: str) -> str:
    """Convert a full season string ('2025-2026') to its display form ('2025-26').

    Returns the input unchanged if it doesn't match the expected YYYY-YYYY shape,
    so callers can pass arbitrary values without tripping on edge cases.
    """
    parts = season.split("-")
    if len(parts) == 2 and len(parts[0]) == 4 and len(parts[1]) == 4 and parts[1].isdigit():
        return f"{parts[0]}-{parts[1][2:]}"
    return season
