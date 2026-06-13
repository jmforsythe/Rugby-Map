"""Canonical league/division names from Wikipedia's club list.

Wikipedia often lists a few clubs under outdated division labels (e.g.
``Isthmian League Division One North`` vs the current ``Isthmian League North
Division``). Map those aliases before grouping clubs into league files.

Also provides stem / geographic parsing for automatic parent-league matching in
the pyramid diagram (see :func:`find_football_parent_name`).
"""

from __future__ import annotations

import re

# Geographic tails longest-first so "South East" beats "South".
_FOOTBALL_GEO_PHRASES: tuple[str, ...] = (
    "North East",
    "North West",
    "South East",
    "South West",
    "South Central",
    "North",
    "South",
    "East",
    "West",
    "Central",
)
_FOOTBALL_GEO_RE = "|".join(re.escape(g) for g in _FOOTBALL_GEO_PHRASES)
_FOOTBALL_DIVISION_ORDINAL = (
    r"(?:One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|Eleven|Twelve|\d+)"
)

_RE_FOOTBALL_PREMIER_DIVISION = re.compile(
    r"^(?P<stem>.+?)\s+Premier\s+Division(?:\s+(?P<geo>.+))?$",
    re.IGNORECASE,
)
_RE_FOOTBALL_GEO_DIVISION = re.compile(
    rf"^(?P<stem>.+?)\s+(?P<geo>{_FOOTBALL_GEO_RE})\s+Division$",
    re.IGNORECASE,
)
_RE_FOOTBALL_NUMBERED_DIVISION = re.compile(
    rf"^(?P<stem>.+?)\s+Division\s+{_FOOTBALL_DIVISION_ORDINAL}(?:\s+(?P<geo>.+))?$",
    re.IGNORECASE,
)
_RE_FOOTBALL_COMPASS_SUFFIX = re.compile(
    rf"^(?P<stem>.+?)\s+(?P<geo>{_FOOTBALL_GEO_RE})$",
    re.IGNORECASE,
)

# Exact stale labels seen on en.wikipedia.org/wiki/List_of_football_clubs_in_England
_LEAGUE_ALIASES: dict[str, str] = {
    "Isthmian League Division One North": "Isthmian League North Division",
    "Isthmian League South Central": "Isthmian League South Central Division",
    "Southern League Premier Division One Central": "Southern League Division One Central",
    "United Counties League Premier Division": "United Counties League Premier Division North",
    "Combined Counties League Premier Division One": "Combined Counties League Division One",
    "Spartan South Midlands League Premier One": "Spartan South Midlands League Division One",
    "Wessex  League Premier Division": "Wessex League Premier Division",
    "Western League Premier Division}": "Western League Premier Division",
}

# Regex rewrites for recurring Wikipedia naming drift (applied after alias lookup).
_LEAGUE_REWRITES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"^Isthmian League Division One (North|South East|South Central)$"),
        r"Isthmian League \1 Division",
    ),
    (
        re.compile(r"^Southern League Premier Division One (Central|South)$"),
        r"Southern League Division One \1",
    ),
]


def _clean_league_name(name: str) -> str:
    s = re.sub(r"\s+", " ", name.strip())
    return s.rstrip("}")


def canonical_league_name(name: str) -> str:
    """Return the preferred division label for ``name``."""
    cleaned = _clean_league_name(name)
    mapped = _LEAGUE_ALIASES.get(cleaned, cleaned)
    for pattern, repl in _LEAGUE_REWRITES:
        if pattern.fullmatch(mapped):
            mapped = pattern.sub(repl, mapped)
            break
    return mapped


def _normalize_geo_tail(geo: str) -> str:
    """Collapse whitespace and case-fold a geographic subdivision tail."""
    return " ".join(geo.strip().split()).casefold()


def football_league_family_parts(name: str) -> tuple[str, str]:
    """Split a division title into ``(family_stem, geographic_tail)``.

    Strips tier markers such as ``Premier Division``, ``North Division``, and
    ``Division One`` / ``Division 1`` (with optional trailing compass suffix).
    When no pattern matches, the whole cleaned name is the stem and the tail is
    empty — e.g. ``Premier League``, ``Essex Senior League``.
    """
    n = _clean_league_name(name)
    for pattern in (
        _RE_FOOTBALL_PREMIER_DIVISION,
        _RE_FOOTBALL_GEO_DIVISION,
        _RE_FOOTBALL_NUMBERED_DIVISION,
    ):
        match = pattern.match(n)
        if match:
            return match.group("stem").strip(), (match.group("geo") or "").strip()

    match = _RE_FOOTBALL_COMPASS_SUFFIX.match(n)
    if match:
        stem = match.group("stem").strip()
        # Avoid treating "North West Counties League" as stem "North West Counties" + geo.
        if stem.endswith((" League", " Counties")) or " League " in stem:
            return stem, match.group("geo").strip()

    return n, ""


def league_geographic_name(league_name: str) -> str | None:
    """Return a regional search hint extracted from a league/division title."""
    stem, geo = football_league_family_parts(league_name)
    region = stem
    if region.casefold().endswith(" league"):
        region = region[: -len(" League")].strip()
    parts: list[str] = []
    if region:
        parts.append(region)
    if geo and geo.casefold() not in region.casefold():
        parts.append(geo)
    if not parts:
        return None
    return " ".join(parts)


# League stems that are competition names, not places Nominatim understands.
_LEAGUE_GEO_SEARCH_ALIASES: dict[str, str] = {
    "Anglian Combination": "Norfolk",
    "Spartan South Midlands": "Bedfordshire",
}


def league_search_geography(league_name: str) -> str | None:
    """Return a Nominatim-friendly regional hint for club-name geocoding."""
    geo = league_geographic_name(league_name)
    if not geo:
        return None
    return _LEAGUE_GEO_SEARCH_ALIASES.get(geo, geo)


def _football_prefix_parent_name(child_name: str, parent_names: list[str]) -> str | None:
    """Longest parent whose full title is a prefix of ``child_name``."""
    child_cf = _clean_league_name(child_name).casefold()
    best: str | None = None
    best_len = 0
    for parent in parent_names:
        parent_clean = _clean_league_name(parent)
        parent_cf = parent_clean.casefold()
        if child_cf == parent_cf:
            continue
        if not (child_cf.startswith(parent_cf + " ") or child_cf.startswith(parent_cf + ",")):
            continue
        if len(parent_cf) > best_len:
            best = parent
            best_len = len(parent_cf)
    return best


def find_football_parent_name(child_name: str, parent_names: list[str]) -> str | None:
    """Pick a unique parent league for ``child_name`` from ``parent_names``.

    Compares family stems after stripping ``Premier Division``, ``Division One``,
    ``North Division``, etc., then matches geographic tails when present. Falls
    back to longest literal prefix match (``National League North`` →
    ``National League``).
    """
    if not parent_names:
        return None

    child_stem, child_geo = football_league_family_parts(child_name)
    child_stem_cf = child_stem.casefold()
    child_geo_cf = _normalize_geo_tail(child_geo)

    candidates: list[tuple[str, str]] = []
    for parent in parent_names:
        parent_stem, parent_geo = football_league_family_parts(parent)
        if parent_stem.casefold() != child_stem_cf:
            continue
        parent_geo_cf = _normalize_geo_tail(parent_geo)
        if child_geo_cf and parent_geo_cf and child_geo_cf != parent_geo_cf:
            continue
        candidates.append((parent, parent_geo))

    if len(candidates) == 1:
        return candidates[0][0]

    if len(candidates) > 1:
        if child_geo_cf:
            geo_exact = [p for p, g in candidates if _normalize_geo_tail(g) == child_geo_cf]
            if len(geo_exact) == 1:
                return geo_exact[0]
            apex = [p for p, g in candidates if not _normalize_geo_tail(g)]
            if len(apex) == 1:
                return apex[0]
        return None

    return _football_prefix_parent_name(child_name, parent_names)


def consolidate_pyramid_season(season: str, data_dir) -> tuple[int, int]:
    """Merge duplicate division JSON files under ``team_addresses`` and ``geocoded_teams``.

    Returns ``(files_removed, teams_merged)``.
    """
    import json
    from pathlib import Path

    from football.fetch_pyramid import _sanitize_filename

    removed = 0
    merged_teams = 0

    for subdir in ("team_addresses", "geocoded_teams"):
        pyramid_dir = Path(data_dir) / subdir / season / "pyramid"
        if not pyramid_dir.is_dir():
            continue

        grouped: dict[tuple[int, str], dict] = {}
        source_paths: dict[tuple[int, str], list[Path]] = {}

        for path in sorted(pyramid_dir.glob("*.json")):
            with open(path, encoding="utf-8") as f:
                league = json.load(f)
            teams = league.get("teams") or []
            level = next((int(t["level"]) for t in teams if t.get("level")), 0)
            if level < 1:
                continue
            canon = canonical_league_name(league.get("league_name", path.stem.replace("_", " ")))
            key = (level, canon)

            if key not in grouped:
                grouped[key] = {
                    "league_name": canon,
                    "league_url": league.get("league_url", ""),
                    "teams": [],
                }
                source_paths[key] = []
            source_paths[key].append(path)

            seen = {t.get("wiki_title") or t.get("name") for t in grouped[key]["teams"]}
            for team in teams:
                ident = team.get("wiki_title") or team.get("name")
                if ident in seen:
                    continue
                grouped[key]["teams"].append(team)
                seen.add(ident)
                if len(source_paths[key]) > 1:
                    merged_teams += 1

        for key, league in grouped.items():
            league["team_count"] = len(league["teams"])
            out_path = pyramid_dir / f"{_sanitize_filename(league['league_name'])}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(league, f, indent=2, ensure_ascii=False)
                f.write("\n")
            for old_path in source_paths[key]:
                if old_path != out_path and old_path.is_file():
                    old_path.unlink()
                    removed += 1

    return removed, merged_teams
