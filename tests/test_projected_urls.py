"""Tests for projected markdown parsing helpers."""

from rugby.analysis.projected_urls import normalize_tier_leagues


class TestNormalizeTierLeagues:
    def test_sorts_teams_alphabetically(self):
        leagues = [("Regional 1 Midlands", ["Zulu RFC", "Alpha RFC", "Middles RFC"])]
        out = normalize_tier_leagues(leagues)
        assert out[0][1] == ["Alpha RFC", "Middles RFC", "Zulu RFC"]

    def test_folds_bpr_into_unassigned(self):
        leagues = [
            ("Regional 2 North", ["A"]),
            ("Best Playing Record into Tier 6", ["Ryton", "Finchley"]),
            ("Unassigned", ["Bognor"]),
        ]
        out = normalize_tier_leagues(leagues)
        names = [name for name, _ in out]
        assert "Best Playing Record into Tier 6" not in names
        unassigned = next(teams for name, teams in out if name == "Unassigned")
        assert unassigned == ["Bognor", "Finchley", "Ryton"]

    def test_creates_unassigned_when_bpr_only(self):
        leagues = [
            ("Regional 2 North", ["A"]),
            ("Best Playing Record into Tier 6", ["Ryton"]),
        ]
        out = normalize_tier_leagues(leagues)
        unassigned = next(teams for name, teams in out if name == "Unassigned")
        assert unassigned == ["Ryton"]

    def test_sorts_leagues_alphabetically(self):
        leagues = [
            ("Regional 2 South East", ["A"]),
            ("Regional 2 Anglia", ["B"]),
            ("Regional 2 Midlands East", ["C"]),
        ]
        out = normalize_tier_leagues(leagues)
        assert [name for name, _ in out] == [
            "Regional 2 Anglia",
            "Regional 2 Midlands East",
            "Regional 2 South East",
        ]
