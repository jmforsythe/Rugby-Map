"""Detect where merit competitions run *alongside* the RFU pyramid rather than beneath it.

Merit competitions are modelled as sitting underneath the pyramid: a merit apex at
local tier 1 is placed at ``1 + COMPETITION_OFFSETS[comp]`` and its parent is the
pyramid league one tier above. This script gathers the evidence for and against that
model, so offsets and ``tier_mappings`` apex parents can be corrected.

Four independent signals are reported:

``crossovers``
    Clubs appearing in a merit league one season and a pyramid league the next (or
    vice versa). The *structural gap* ``pyramid_abs - merit_abs`` is direction
    normalised, so promotion and relegation produce the same sign. A gap of -1 is
    what the underneath model predicts (clubs promote out of the merit apex into its
    pyramid parent); a gap of 0 means the two leagues sit at the same level.

``identity``
    The same ladder reclassified between the pyramid folder and ``merit/``. Detected
    by normalised league-name equality across adjacent seasons. These pin the offset:
    a reclassified league should keep its absolute tier.

``overlap``
    Merit and pyramid leagues at the *same absolute tier* drawing on the same
    geography. This is the real violation of the "one league per area per tier"
    territory rule, and the strongest evidence a merit ladder runs alongside the
    pyramid instead of below it.

``offsets``
    ``tier_mappings/<season>.json`` apex parents versus ``COMPETITION_OFFSETS``. An
    apex whose parent is a pyramid league at tier ``T`` implies an offset of
    ``T + 1 - local_tier``. Disagreement means the two sources have drifted apart.

``reserves``
    How much of a merit competition is second and third XVs, and which absolute tier
    those clubs' principal XVs play at. A ladder made of reserve sides is not a
    continuation of the pyramid below its lowest county league — the same clubs are
    already in the pyramid — so an apex placed below the pyramid floor is suspect.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from core import setup_logging
from rugby import DATA_DIR
from rugby.addresses import team_name_to_club_name
from rugby.tiers import extract_tier, get_competition_offset

GEOCODED_DIR = DATA_DIR / "geocoded_teams"
TIER_MAPPINGS_DIR = DATA_DIR / "tier_mappings"
SEASONS = sorted(d.name for d in GEOCODED_DIR.iterdir() if d.is_dir() and "-" in d.name)
WOMENS_MIN = 100

# Keys in tier_mappings/<season>.json that are not merit competitions.
_RESERVED_MAPPING_KEYS = frozenset(
    {"schema_version", "season", "men", "women", "tier7_column_order", "stem_slot_strips"}
)

# Default radius for treating two clubs as drawing on the same locality.
DEFAULT_OVERLAP_KM = 25.0

# Sponsor / filler words removed before comparing league names for ladder identity.
_NAME_NOISE = re.compile(
    r"\b(greene\s*king|ipa|tribute|ale|shepherd\s*neame|harvey'?s?|brewery|wharf|olympia|"
    r"bathtime|cotton\s*traders|adm|nowirul|candy|sse|euromanx|wadworth|6x|bombardier|"
    r"spitfire|county|counties|rfu|league|division|div|table|the)\b",
    re.IGNORECASE,
)
_NAME_PUNCT = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Placement:
    """One team's league placement in one season."""

    season: str
    team: str
    club: str
    league: str
    rel_path: str
    is_merit: bool
    comp: str | None
    local_tier: int
    abs_tier: int
    tier_name: str

    @property
    def is_reserve(self) -> bool:
        """True for second/third/fourth XVs, i.e. anything with an XV suffix stripped."""
        return self.club != self.team


@dataclass(frozen=True)
class LeagueGeo:
    """A league's identity and club locations for one season."""

    season: str
    league: str
    is_merit: bool
    comp: str | None
    local_tier: int
    abs_tier: int
    points: tuple[tuple[float, float], ...]


@dataclass
class Crossover:
    """A club moving between the merit and pyramid trees across consecutive seasons."""

    club: str
    from_season: str
    to_season: str
    from_p: Placement
    to_p: Placement

    @property
    def tier_delta(self) -> int:
        """Absolute tier change in travel order (negative = moved up a level)."""
        return self.to_p.abs_tier - self.from_p.abs_tier

    @property
    def merit_p(self) -> Placement:
        return self.from_p if self.from_p.is_merit else self.to_p

    @property
    def pyramid_p(self) -> Placement:
        return self.to_p if not self.to_p.is_merit else self.from_p

    @property
    def gap(self) -> int:
        """Structural gap ``pyramid_abs - merit_abs``, independent of travel direction.

        ``-1`` is what the underneath model predicts, ``0`` means same level.
        """
        return self.pyramid_p.abs_tier - self.merit_p.abs_tier

    @property
    def merit_to_pyramid(self) -> bool:
        return self.from_p.is_merit


def normalise_league_name(name: str) -> str:
    """Collapse a league name to a sponsor-free key for ladder-identity matching."""
    cleaned = _NAME_NOISE.sub(" ", name.lower())
    return _NAME_PUNCT.sub("", cleaned)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _parse_league_file(
    season: str, filepath: Path, season_dir: Path
) -> tuple[LeagueGeo | None, list[Placement]]:
    rel = filepath.relative_to(season_dir).as_posix()
    parts = rel.split("/")
    is_merit = len(parts) >= 3 and parts[0] == "merit"
    comp = parts[1] if is_merit else None
    local_tier, tier_name = extract_tier(rel, season)
    if local_tier >= WOMENS_MIN:
        return None, []

    abs_tier = (
        local_tier + get_competition_offset(comp, season) if is_merit and comp else local_tier
    )

    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    league_name = data.get("league_name", filepath.stem)

    placements: list[Placement] = []
    points: list[tuple[float, float]] = []
    for team in data.get("teams", []):
        placements.append(
            Placement(
                season=season,
                team=team["name"],
                club=team_name_to_club_name(team["name"]),
                league=league_name,
                rel_path=rel,
                is_merit=is_merit,
                comp=comp,
                local_tier=local_tier,
                abs_tier=abs_tier,
                tier_name=tier_name,
            )
        )
        lat, lon = team.get("latitude"), team.get("longitude")
        if lat is not None and lon is not None:
            points.append((float(lat), float(lon)))

    geo = LeagueGeo(
        season=season,
        league=league_name,
        is_merit=is_merit,
        comp=comp,
        local_tier=local_tier,
        abs_tier=abs_tier,
        points=tuple(points),
    )
    return geo, placements


@dataclass
class Dataset:
    """Everything loaded from ``geocoded_teams`` needed by the analyses."""

    by_club: dict[str, dict[str, list[Placement]]]
    leagues: dict[str, list[LeagueGeo]]
    placements: dict[str, list[Placement]]
    principal_tier: dict[str, dict[str, int]]

    def pyramid_floor(self, season: str) -> int:
        """Deepest absolute tier occupied by a pyramid (non-merit) league."""
        tiers = [geo.abs_tier for geo in self.leagues.get(season, []) if not geo.is_merit]
        return max(tiers, default=0)


def load_dataset(seasons: list[str] | None = None) -> Dataset:
    """Load placements and league geometries for every season."""
    target = seasons or SEASONS
    by_club: dict[str, dict[str, list[Placement]]] = defaultdict(lambda: defaultdict(list))
    leagues: dict[str, list[LeagueGeo]] = {}
    placements: dict[str, list[Placement]] = {}
    principal_tier: dict[str, dict[str, int]] = {}

    for season in target:
        season_dir = GEOCODED_DIR / season
        if not season_dir.is_dir():
            continue
        season_leagues: list[LeagueGeo] = []
        season_placements: list[Placement] = []
        for filepath in sorted(season_dir.rglob("*.json")):
            geo, parsed = _parse_league_file(season, filepath, season_dir)
            if geo is not None:
                season_leagues.append(geo)
            for placement in parsed:
                by_club[placement.club][season].append(placement)
                season_placements.append(placement)
        leagues[season] = season_leagues
        placements[season] = season_placements
        # Best (highest) tier each club reaches with a principal XV that season.
        best: dict[str, int] = {}
        for placement in season_placements:
            if placement.is_reserve:
                continue
            prior = best.get(placement.club)
            if prior is None or placement.abs_tier < prior:
                best[placement.club] = placement.abs_tier
        principal_tier[season] = best

    return Dataset(
        by_club=by_club,
        leagues=leagues,
        placements=placements,
        principal_tier=principal_tier,
    )


def find_crossovers(by_club: dict[str, dict[str, list[Placement]]]) -> list[Crossover]:
    """Club-level merit <-> pyramid transitions between consecutive recorded seasons."""
    out: list[Crossover] = []
    for club, seasons in by_club.items():
        prev: Placement | None = None
        prev_season: str | None = None
        for season in SEASONS:
            entries = seasons.get(season)
            if not entries:
                prev = None
                prev_season = None
                continue
            current = min(entries, key=lambda e: e.abs_tier)
            if prev is not None and prev_season is not None and prev.is_merit != current.is_merit:
                out.append(
                    Crossover(
                        club=club,
                        from_season=prev_season,
                        to_season=season,
                        from_p=prev,
                        to_p=current,
                    )
                )
            prev = current
            prev_season = season
    return out


# ---------------------------------------------------------------------------
# Signal 1 & 2 — crossover summary and one-step promotion/relegation
# ---------------------------------------------------------------------------


def print_crossover_summary(crossovers: list[Crossover]) -> None:
    merit_to_pyramid = [c for c in crossovers if c.merit_to_pyramid]
    pyramid_to_merit = [c for c in crossovers if not c.merit_to_pyramid]

    print("=" * 100)
    print("CROSSOVERS: clubs moving between the merit and pyramid trees")
    print(f"Seasons {SEASONS[0]} .. {SEASONS[-1]} ({len(SEASONS)} seasons)")
    print("=" * 100)
    print(
        f"Total: {len(crossovers)}  (merit->pyramid {len(merit_to_pyramid)}, "
        f"pyramid->merit {len(pyramid_to_merit)})"
    )
    print()

    print("--- Structural gap (pyramid_abs - merit_abs), direction normalised ---")
    print("     gap -1 = pyramid one level above merit (what the underneath model predicts)")
    print("     gap  0 = merit and pyramid league at the same level")
    gap_ctr = Counter(c.gap for c in crossovers)
    total = len(crossovers) or 1
    for gap in sorted(gap_ctr):
        n = gap_ctr[gap]
        bar = "#" * max(1, round(40 * n / total))
        print(f"  gap {gap:+3d}: {n:4d} ({100 * n / total:4.1f}%)  {bar}")
    print()


def print_one_step_moves(crossovers: list[Crossover]) -> None:
    one_step = [c for c in crossovers if abs(c.tier_delta) == 1]
    promoted = [c for c in one_step if c.tier_delta < 0]
    relegated = [c for c in one_step if c.tier_delta > 0]
    total = len(crossovers) or 1

    print("=" * 100)
    print("ONE-STEP MOVES: exactly +/-1 absolute tier (promotion / relegation candidates)")
    print("=" * 100)
    print(
        f"|delta| == 1: {len(one_step)} of {len(crossovers)} ({100 * len(one_step) / total:.1f}%)"
    )
    print(f"  moved up   (delta -1): {len(promoted)}")
    print(f"  moved down (delta +1): {len(relegated)}")
    print()
    print("  Caution: a one-step move is ambiguous. Promotion out of a merit apex and an")
    print("  offset that is too large by one produce the identical signature. Use the gap")
    print("  histogram plus the overlap and identity sections below to tell them apart.")
    print()


# ---------------------------------------------------------------------------
# Signal 3 — ladder identity (reclassification between pyramid and merit)
# ---------------------------------------------------------------------------


@dataclass
class IdentityMatch:
    comp: str
    merit_season: str
    merit_league: str
    merit_abs: int
    pyramid_season: str
    pyramid_league: str
    pyramid_abs: int

    @property
    def tier_shift(self) -> int:
        return self.pyramid_abs - self.merit_abs


def find_identity_matches(dataset: Dataset) -> list[IdentityMatch]:
    """Merit leagues whose normalised name also appears as a pyramid league in a nearby season."""
    pyramid_by_key: dict[str, list[LeagueGeo]] = defaultdict(list)
    for season_leagues in dataset.leagues.values():
        for geo in season_leagues:
            if not geo.is_merit:
                key = normalise_league_name(geo.league)
                if key:
                    pyramid_by_key[key].append(geo)

    matches: list[IdentityMatch] = []
    for season_leagues in dataset.leagues.values():
        for geo in season_leagues:
            if not geo.is_merit:
                continue
            key = normalise_league_name(geo.league)
            if not key:
                continue
            for pyr in pyramid_by_key.get(key, []):
                # Only compare adjacent seasons: the same ladder changing folder.
                if abs(SEASONS.index(pyr.season) - SEASONS.index(geo.season)) != 1:
                    continue
                matches.append(
                    IdentityMatch(
                        comp=geo.comp or "?",
                        merit_season=geo.season,
                        merit_league=geo.league,
                        merit_abs=geo.abs_tier,
                        pyramid_season=pyr.season,
                        pyramid_league=pyr.league,
                        pyramid_abs=pyr.abs_tier,
                    )
                )
    return matches


def print_identity_matches(matches: list[IdentityMatch]) -> None:
    print("=" * 100)
    print("LADDER IDENTITY: the same league classified as merit one season, pyramid the next")
    print("A reclassified ladder should keep its absolute tier, so shift != 0 means the")
    print("merit offset disagrees with where the pyramid put the same competition.")
    print("=" * 100)
    if not matches:
        print("  (none)")
        print()
        return

    by_comp: dict[str, list[IdentityMatch]] = defaultdict(list)
    for m in matches:
        by_comp[m.comp].append(m)

    for comp, rows in sorted(by_comp.items()):
        shifts = Counter(m.tier_shift for m in rows)
        verdict = "consistent" if set(shifts) == {0} else f"SHIFTED {dict(sorted(shifts.items()))}"
        print(f"\n  {comp}: {len(rows)} identity match(es) — {verdict}")
        for m in sorted(rows, key=lambda r: (r.merit_season, r.merit_league))[:8]:
            print(
                f"     {m.merit_season} merit abs{m.merit_abs} '{m.merit_league[:38]}'"
                f"  <->  {m.pyramid_season} pyramid abs{m.pyramid_abs} '{m.pyramid_league[:38]}'"
                f"  shift {m.tier_shift:+d}"
            )
        if len(rows) > 8:
            print(f"     ... {len(rows) - 8} more")
    print()


# ---------------------------------------------------------------------------
# Signal 4 — same-tier geographic overlap (the parallel-structure test)
# ---------------------------------------------------------------------------


@dataclass
class OverlapConflict:
    season: str
    comp: str
    local_tier: int
    abs_tier: int
    merit_league: str
    pyramid_league: str
    merit_covered: float
    pyramid_covered: float
    centroid_km: float


def _coverage(
    a: tuple[tuple[float, float], ...], b: tuple[tuple[float, float], ...], radius_km: float
) -> float:
    """Fraction of points in *a* with a point of *b* within ``radius_km``."""
    if not a or not b:
        return 0.0
    near = 0
    for lat, lon in a:
        if any(_haversine_km(lat, lon, blat, blon) <= radius_km for blat, blon in b):
            near += 1
    return near / len(a)


def _centroid(points: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


def find_overlap_conflicts(
    dataset: Dataset,
    *,
    radius_km: float = DEFAULT_OVERLAP_KM,
    min_coverage: float = 0.6,
    max_centroid_km: float = 35.0,
) -> list[OverlapConflict]:
    """Merit and pyramid leagues placed at the same absolute tier over the same geography.

    Coverage is required in *both* directions and the two leagues must be centred near each
    other. A one-way overlap only means one league sits inside the other's catchment, which
    happens routinely between neighbouring counties in dense areas such as London.
    """
    conflicts: list[OverlapConflict] = []
    for season, season_leagues in dataset.leagues.items():
        pyramid_by_tier: dict[int, list[LeagueGeo]] = defaultdict(list)
        for geo in season_leagues:
            if not geo.is_merit and geo.points:
                pyramid_by_tier[geo.abs_tier].append(geo)

        for geo in season_leagues:
            if not geo.is_merit or not geo.points:
                continue
            for pyr in pyramid_by_tier.get(geo.abs_tier, []):
                merit_covered = _coverage(geo.points, pyr.points, radius_km)
                if merit_covered < min_coverage:
                    continue
                pyramid_covered = _coverage(pyr.points, geo.points, radius_km)
                if pyramid_covered < min_coverage:
                    continue
                mc, pc = _centroid(geo.points), _centroid(pyr.points)
                if _haversine_km(mc[0], mc[1], pc[0], pc[1]) > max_centroid_km:
                    continue
                conflicts.append(
                    OverlapConflict(
                        season=season,
                        comp=geo.comp or "?",
                        local_tier=geo.local_tier,
                        abs_tier=geo.abs_tier,
                        merit_league=geo.league,
                        pyramid_league=pyr.league,
                        merit_covered=merit_covered,
                        pyramid_covered=pyramid_covered,
                        centroid_km=_haversine_km(mc[0], mc[1], pc[0], pc[1]),
                    )
                )
    return conflicts


def print_overlap_conflicts(conflicts: list[OverlapConflict], *, recent_only: int = 0) -> None:
    print("=" * 100)
    print("SAME-TIER TERRITORY CONFLICTS: merit and pyramid leagues sharing a tier and a locality")
    print("These break the 'one league per area per tier' rule and are the strongest sign a")
    print("merit ladder runs alongside the pyramid rather than beneath it.")
    print("=" * 100)

    rows = conflicts
    if recent_only:
        window = set(SEASONS[-recent_only:])
        rows = [c for c in conflicts if c.season in window]
        print(f"(restricted to the last {recent_only} seasons)")

    if not rows:
        print("  (none)")
        print()
        return

    by_comp: Counter[str] = Counter(c.comp for c in rows)
    print(f"\nTotal conflicts: {len(rows)} across {len(by_comp)} competition(s)")
    for comp, n in by_comp.most_common():
        print(f"  {comp}: {n}")
    print()

    for c in sorted(rows, key=lambda r: (r.season, r.comp, r.local_tier)):
        print(
            f"  {c.season} {c.comp:16s} loc{c.local_tier} abs{c.abs_tier}  "
            f"'{c.merit_league[:38]}'"
        )
        print(
            f"      vs pyramid '{c.pyramid_league[:38]}'  "
            f"merit_covered={c.merit_covered:.0%} pyramid_covered={c.pyramid_covered:.0%} "
            f"centroids {c.centroid_km:.0f}km apart"
        )
    print()


# ---------------------------------------------------------------------------
# Signal 5 — offset audit against tier_mappings apex parents
# ---------------------------------------------------------------------------


@dataclass
class OffsetAudit:
    season: str
    comp: str
    local_tier: int
    child: str
    parent: str
    parent_tier: int
    implied_offset: int
    current_offset: int

    @property
    def consistent(self) -> bool:
        return self.implied_offset == self.current_offset


def audit_offsets(dataset: Dataset) -> tuple[list[OffsetAudit], list[tuple[str, str]]]:
    """Compare tier_mappings apex parents with COMPETITION_OFFSETS.

    Returns ``(audits, missing)`` where *missing* lists ``(season, comp)`` pairs that
    have geocoded merit data but no apex row linking into the pyramid.
    """
    pyramid_tier_by_name: dict[str, dict[str, int]] = {}
    merit_tier_by_name: dict[str, dict[str, tuple[str, int]]] = {}
    merit_comps_with_data: dict[str, set[str]] = {}
    for season, season_leagues in dataset.leagues.items():
        pyramid_tier_by_name[season] = {
            geo.league: geo.abs_tier for geo in season_leagues if not geo.is_merit
        }
        merit_tier_by_name[season] = {
            geo.league: (geo.comp or "?", geo.abs_tier)
            for geo in season_leagues
            if geo.is_merit and geo.comp
        }
        merit_comps_with_data[season] = {
            geo.comp for geo in season_leagues if geo.is_merit and geo.comp
        }

    audits: list[OffsetAudit] = []
    linked: dict[str, set[str]] = defaultdict(set)

    for path in sorted(TIER_MAPPINGS_DIR.glob("*.json")):
        season = path.stem
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        names = pyramid_tier_by_name.get(season, {})

        merit_names = merit_tier_by_name.get(season, {})

        for comp, section in payload.items():
            if comp in _RESERVED_MAPPING_KEYS or not isinstance(section, dict):
                continue
            best: OffsetAudit | None = None
            for local_str, children in sorted(section.items(), key=lambda kv: int(kv[0])):
                local_tier = int(local_str)
                for child, parent in children.items():
                    parents = parent if isinstance(parent, list) else [parent]
                    for parent_name in parents:
                        # An apex may stem into another merit competition (e.g. NOWIRUL into
                        # Lancashire). That still anchors the ladder, so treat it as linked.
                        if parent_name in merit_names:
                            parent_comp, _ = merit_names[parent_name]
                            if parent_comp != comp:
                                linked[season].add(comp)
                            continue
                        parent_tier = names.get(parent_name)
                        if parent_tier is None:
                            continue
                        candidate = OffsetAudit(
                            season=season,
                            comp=comp,
                            local_tier=local_tier,
                            child=child,
                            parent=parent_name,
                            parent_tier=parent_tier,
                            implied_offset=parent_tier + 1 - local_tier,
                            current_offset=get_competition_offset(comp, season),
                        )
                        if best is None or candidate.local_tier < best.local_tier:
                            best = candidate
            if best is not None:
                audits.append(best)
                linked[season].add(comp)

    missing = [
        (season, comp)
        for season, comps in sorted(merit_comps_with_data.items())
        for comp in sorted(comps)
        if comp not in linked.get(season, set())
    ]
    return audits, missing


def print_offset_audit(audits: list[OffsetAudit], missing: list[tuple[str, str]]) -> None:
    print("=" * 100)
    print("OFFSET AUDIT: tier_mappings apex parent vs COMPETITION_OFFSETS")
    print("implied_offset = parent_pyramid_tier + 1 - local_tier")
    print("=" * 100)

    bad = [a for a in audits if not a.consistent]
    print(f"Apex rows checked: {len(audits)}   inconsistent: {len(bad)}")
    print()
    for a in sorted(bad, key=lambda r: (r.comp, r.season)):
        print(
            f"  {a.season} {a.comp:18s} loc{a.local_tier} '{a.child[:34]}'"
            f" parent '{a.parent[:34]}' (tier {a.parent_tier})"
            f"  implied {a.implied_offset} vs current {a.current_offset}"
        )
    if not bad:
        print("  (all consistent)")
    print()

    print(f"--- Merit data with no apex link into the pyramid: {len(missing)} ---")
    by_comp: dict[str, list[str]] = defaultdict(list)
    for season, comp in missing:
        by_comp[comp].append(season)
    for comp, seasons in sorted(by_comp.items()):
        shown = ", ".join(seasons[:6]) + (f" ... (+{len(seasons) - 6})" if len(seasons) > 6 else "")
        print(f"  {comp:18s} {len(seasons):3d} season(s): {shown}")
    print()


# ---------------------------------------------------------------------------
# Signal 6 — reserve-XV profile and apexes stranded below the pyramid floor
# ---------------------------------------------------------------------------


@dataclass
class ReserveProfile:
    """How much of one merit competition-season is reserve XVs, and where their clubs play."""

    season: str
    comp: str
    teams: int
    reserves: int
    apex_abs: int
    deepest_abs: int
    pyramid_floor: int
    principal_tiers: list[int]

    @property
    def reserve_share(self) -> float:
        return self.reserves / self.teams if self.teams else 0.0

    @property
    def median_principal_tier(self) -> float | None:
        if not self.principal_tiers:
            return None
        ordered = sorted(self.principal_tiers)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return float(ordered[mid])
        return (ordered[mid - 1] + ordered[mid]) / 2

    @property
    def below_floor_by(self) -> int:
        """Tiers by which the apex sits beneath the deepest pyramid league (0 if not)."""
        return max(0, self.apex_abs - self.pyramid_floor)


def profile_reserves(dataset: Dataset) -> list[ReserveProfile]:
    """Reserve share and principal-XV tiers for every merit competition-season."""
    profiles: list[ReserveProfile] = []
    for season, season_placements in dataset.placements.items():
        by_comp: dict[str, list[Placement]] = defaultdict(list)
        for placement in season_placements:
            if placement.is_merit and placement.comp:
                by_comp[placement.comp].append(placement)

        floor = dataset.pyramid_floor(season)
        principal = dataset.principal_tier.get(season, {})
        for comp, members in sorted(by_comp.items()):
            reserves = [p for p in members if p.is_reserve]
            profiles.append(
                ReserveProfile(
                    season=season,
                    comp=comp,
                    teams=len(members),
                    reserves=len(reserves),
                    apex_abs=min(p.abs_tier for p in members),
                    deepest_abs=max(p.abs_tier for p in members),
                    pyramid_floor=floor,
                    principal_tiers=[principal[p.club] for p in reserves if p.club in principal],
                )
            )
    return profiles


def print_reserve_profiles(profiles: list[ReserveProfile], *, min_share: float = 0.5) -> None:
    print("=" * 100)
    print("RESERVE PROFILE: merit competitions built from second and third XVs")
    print("A ladder of reserve sides is not the pyramid continuing downwards — those clubs")
    print("already sit in the pyramid with their principal XV, shown here as 'principal'.")
    print("=" * 100)

    stranded = [p for p in profiles if p.below_floor_by > 0]
    print(
        f"\nCompetition-seasons profiled: {len(profiles)}   "
        f"apex below the pyramid floor: {len(stranded)}"
    )
    print()
    print(
        f"{'season':<12}{'competition':<18}{'teams':>6}{'reserve':>8}"
        f"{'apex':>6}{'floor':>6}{'below':>6}  principal median"
    )
    print("-" * 100)
    for p in sorted(stranded, key=lambda r: (r.comp, r.season)):
        median = p.median_principal_tier
        median_text = f"tier {median:g}" if median is not None else "-"
        print(
            f"{p.season:<12}{p.comp:<18}{p.teams:>6}{p.reserve_share:>7.0%}"
            f"{p.apex_abs:>6}{p.pyramid_floor:>6}{p.below_floor_by:>6}  {median_text}"
        )
    if not stranded:
        print("  (no merit apex sits below the pyramid floor)")
    print()

    heavy = [p for p in profiles if p.reserve_share >= min_share]
    by_comp: dict[str, list[ReserveProfile]] = defaultdict(list)
    for p in heavy:
        by_comp[p.comp].append(p)
    print(f"--- Competitions at least {min_share:.0%} reserve sides, latest season each ---")
    for comp, rows in sorted(by_comp.items()):
        latest = max(rows, key=lambda r: r.season)
        median = latest.median_principal_tier
        median_text = f"{median:g}" if median is not None else "-"
        print(
            f"  {comp:<18} {latest.season}  {latest.reserve_share:>4.0%} reserve, "
            f"merit abs {latest.apex_abs}-{latest.deepest_abs}, "
            f"principal XVs median tier {median_text}"
        )
    print()


# ---------------------------------------------------------------------------
# Signal 7 — anchoring guardrails
# ---------------------------------------------------------------------------

# A merit apex may never sit above this level: the pyramid owns tiers 1-6.
MERIT_APEX_CEILING_TIER = 7


@dataclass
class XVViolation:
    """A reserve side placed at or above its own club's principal XV."""

    season: str
    comp: str
    team: str
    merit_abs: int
    principal_abs: int
    local_tier: int


def find_xv_violations(dataset: Dataset) -> list[XVViolation]:
    """Reserve sides ranked at or above their club's principal XV.

    A lower XV can never be better placed than a higher XV of the same club, so any hit
    here means the merit ladder is anchored too high for at least one of its members.
    """
    out: list[XVViolation] = []
    for season, season_placements in dataset.placements.items():
        principal = dataset.principal_tier.get(season, {})
        for p in season_placements:
            if not (p.is_merit and p.comp and p.is_reserve):
                continue
            best = principal.get(p.club)
            if best is not None and p.abs_tier <= best:
                out.append(
                    XVViolation(
                        season=season,
                        comp=p.comp,
                        team=p.team,
                        merit_abs=p.abs_tier,
                        principal_abs=best,
                        local_tier=p.local_tier,
                    )
                )
    return out


@dataclass
class AnchorHeadroom:
    """How far a merit competition could rise before it breaks a guardrail."""

    season: str
    comp: str
    current_offset: int
    highest_legal_offset: int
    apex_now: int
    apex_highest: int
    binding: str

    @property
    def headroom(self) -> int:
        """Tiers the ladder could move up (positive) or must move down (negative)."""
        return self.current_offset - self.highest_legal_offset


def compute_anchor_headroom(dataset: Dataset) -> list[AnchorHeadroom]:
    """Highest anchor each merit competition-season could take without breaking a guardrail.

    Two guardrails bound the ladder: the apex may not rise above
    :data:`MERIT_APEX_CEILING_TIER`, and every reserve side must stay below its own
    principal XV. This reports the ceiling, not a recommendation — where a ladder
    actually belongs is a per-competition judgement.
    """
    out: list[AnchorHeadroom] = []
    for season, season_placements in dataset.placements.items():
        principal = dataset.principal_tier.get(season, {})
        by_comp: dict[str, list[Placement]] = defaultdict(list)
        for p in season_placements:
            if p.is_merit and p.comp:
                by_comp[p.comp].append(p)

        for comp, members in sorted(by_comp.items()):
            current = get_competition_offset(comp, season)
            apex_local = min(p.local_tier for p in members)
            required = MERIT_APEX_CEILING_TIER - apex_local
            binding = f"apex ceiling (level {MERIT_APEX_CEILING_TIER})"
            for p in members:
                if not p.is_reserve:
                    continue
                best = principal.get(p.club)
                if best is None:
                    continue
                need = best - p.local_tier + 1
                if need > required:
                    required = need
                    binding = f"{p.team} principal XV at tier {best} (rung {p.local_tier})"
            out.append(
                AnchorHeadroom(
                    season=season,
                    comp=comp,
                    current_offset=current,
                    highest_legal_offset=required,
                    apex_now=apex_local + current,
                    apex_highest=apex_local + required,
                    binding=binding,
                )
            )
    return out


def print_anchor_guardrails(
    violations: list[XVViolation],
    headroom: list[AnchorHeadroom],
    *,
    recent_only: int = 0,
) -> None:
    print("=" * 100)
    print("ANCHORING GUARDRAILS")
    print("A merit apex may not rise above level 7, and no reserve side may be placed at or")
    print("above its own club's principal XV. Headroom is how far a ladder could still rise.")
    print("=" * 100)

    window = set(SEASONS[-recent_only:]) if recent_only else None
    viols = [v for v in violations if window is None or v.season in window]
    rooms = [h for h in headroom if window is None or h.season in window]

    print(f"\n--- Reserve sides at or above their principal XV: {len(viols)} ---")
    by_key: dict[tuple[str, str], list[XVViolation]] = defaultdict(list)
    for v in viols:
        by_key[(v.season, v.comp)].append(v)
    for (season, comp), rows in sorted(by_key.items()):
        print(f"  {season} {comp}:")
        for v in rows[:4]:
            print(
                f"      {v.team[:40]:40s} merit tier {v.merit_abs} "
                f"vs principal XV tier {v.principal_abs}"
            )
        if len(rows) > 4:
            print(f"      ... {len(rows) - 4} more")
    if not viols:
        print("  (none)")

    print(
        f"\n--- Ladders sitting below their ceiling (could rise): {sum(1 for h in rooms if h.headroom > 0)} ---"
    )
    print(
        f"{'season':<12}{'competition':<18}{'offset':>7}{'ceiling':>8}"
        f"{'apex':>6}{'could be':>9}  binding constraint"
    )
    print("-" * 100)
    for h in sorted(rooms, key=lambda r: (-r.headroom, r.comp, r.season)):
        if h.headroom <= 0:
            continue
        print(
            f"{h.season:<12}{h.comp:<18}{h.current_offset:>7}{h.highest_legal_offset:>8}"
            f"{h.apex_now:>6}{h.apex_highest:>9}  {h.binding[:44]}"
        )
    print()


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def print_verdicts(
    crossovers: list[Crossover],
    conflicts: list[OverlapConflict],
    identity: list[IdentityMatch],
    profiles: list[ReserveProfile],
) -> None:
    """Per-competition read on whether the merit ladder runs alongside the pyramid."""
    stranded_by_comp: Counter[str] = Counter(p.comp for p in profiles if p.below_floor_by > 0)
    reserve_by_comp: dict[str, float] = {}
    for p in profiles:
        reserve_by_comp[p.comp] = max(reserve_by_comp.get(p.comp, 0.0), p.reserve_share)

    print("=" * 100)
    print("VERDICT PER COMPETITION")
    print("=" * 100)
    print(
        f"{'Competition':<18} {'Xover':>6} {'gap0':>5} {'gap-1':>6} {'Conflict':>9} "
        f"{'Ident':>6} {'Resv':>5} {'Sunk':>5}  Reading"
    )
    print("-" * 100)

    comps: set[str] = set()
    xs_by_comp: dict[str, list[Crossover]] = defaultdict(list)
    for c in crossovers:
        comp = c.merit_p.comp or "?"
        comps.add(comp)
        xs_by_comp[comp].append(c)

    conflicts_by_comp: Counter[str] = Counter(c.comp for c in conflicts)
    identity_by_comp: Counter[str] = Counter(m.comp for m in identity)
    comps |= set(conflicts_by_comp) | set(identity_by_comp)

    for comp in sorted(comps):
        rows = xs_by_comp.get(comp, [])
        n = len(rows)
        gap0 = sum(1 for c in rows if c.gap == 0)
        gap_minus = sum(1 for c in rows if c.gap == -1)
        n_conflict = conflicts_by_comp.get(comp, 0)
        n_ident = identity_by_comp.get(comp, 0)
        n_sunk = stranded_by_comp.get(comp, 0)
        reserve = reserve_by_comp.get(comp, 0.0)

        if n_sunk:
            reading = f"SUNK — apex below pyramid floor in {n_sunk} season(s), mostly reserve XVs"
        elif n_conflict >= 3:
            reading = "ALONGSIDE — shares tier+area with pyramid"
        elif n_conflict >= 1:
            reading = "PARTLY ALONGSIDE — some shared tier+area"
        elif n and gap_minus >= max(2, 0.5 * n):
            reading = "UNDERNEATH — feeds pyramid one tier above"
        elif n_ident:
            reading = "RECLASSIFIED — same ladder changed folder"
        else:
            reading = "insufficient evidence"

        print(
            f"{comp:<18} {n:>6} {gap0:>5} {gap_minus:>6} {n_conflict:>9} {n_ident:>6} "
            f"{reserve:>4.0%} {n_sunk:>5}  {reading}"
        )
    print()


def print_club_timeline(by_club: dict[str, dict[str, list[Placement]]], query: str) -> None:
    matches = sorted(name for name in by_club if query.lower() in name.lower())
    if not matches:
        print(f"No clubs matching '{query}'")
        return
    for club in matches:
        print(f"\nTimeline: {club}")
        print("-" * 90)
        for season in SEASONS:
            for p in sorted(by_club[club].get(season, []), key=lambda e: e.abs_tier):
                kind = f"merit/{p.comp}" if p.is_merit else "pyramid"
                print(
                    f"  {season}  abs{p.abs_tier:>3} loc{p.local_tier:>3}  {kind:<22}  {p.league}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--club", help="Print the full timeline for clubs matching this substring")
    parser.add_argument(
        "--season",
        action="append",
        dest="seasons",
        help="Restrict to a season (repeatable); default is every season",
    )
    parser.add_argument(
        "--overlap-km",
        type=float,
        default=DEFAULT_OVERLAP_KM,
        help="Radius treating two clubs as the same locality (default: %(default)s)",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.6,
        help="Minimum share of clubs near the other league, both ways (default: %(default)s)",
    )
    parser.add_argument(
        "--max-centroid-km",
        type=float,
        default=35.0,
        help="Maximum distance between league centroids to flag (default: %(default)s)",
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=0,
        help="Limit the territory-conflict listing to the last N seasons",
    )
    args = parser.parse_args()

    setup_logging()
    dataset = load_dataset(args.seasons)
    crossovers = find_crossovers(dataset.by_club)
    identity = find_identity_matches(dataset)
    conflicts = find_overlap_conflicts(
        dataset,
        radius_km=args.overlap_km,
        min_coverage=args.min_coverage,
        max_centroid_km=args.max_centroid_km,
    )
    audits, missing = audit_offsets(dataset)
    profiles = profile_reserves(dataset)
    violations = find_xv_violations(dataset)
    headroom = compute_anchor_headroom(dataset)

    print_crossover_summary(crossovers)
    print_one_step_moves(crossovers)
    print_identity_matches(identity)
    print_overlap_conflicts(conflicts, recent_only=args.recent)
    print_offset_audit(audits, missing)
    print_reserve_profiles(profiles)
    print_anchor_guardrails(violations, headroom, recent_only=args.recent)
    print_verdicts(crossovers, conflicts, identity, profiles)

    if args.club:
        print_club_timeline(dataset.by_club, args.club)


if __name__ == "__main__":
    main()
