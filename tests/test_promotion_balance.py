"""Tests for Counties 1 scheduled relegation overrides in promotion_relegation."""

from rugby.analysis.promotion_relegation import (
    _COUNTIES_ONE_SCHEDULED_DOWN_MECH,
    _PLAYOFF_LOSS_RELEGATE_MECH,
    _PLAYOFF_WIN_PROMOTE_MECH,
    _apply_counties_one_scheduled_downs,
    _tier8_promotable_positions,
    apply_playoff_overrides,
    second_xv_promotion_blocked,
)


def _row(
    team: str,
    *,
    cur: int,
    nxt: int,
    ln: str,
    pos: int,
    mechanism: str = "Stay",
) -> dict:
    return {
        "team_name": team,
        "league_name": ln,
        "filename": "x.json",
        "current_tier": cur,
        "position": pos,
        "total_in_league": 99,
        "next_tier": nxt,
        "mechanism": mechanism,
        "dest_league": "",
    }


def test_scheduled_downs_picks_worst_stayers() -> None:
    league = "Counties 1 Test North"
    c1 = frozenset({league})
    sched = {league: 2}
    assignments = [
        _row("A", cur=7, nxt=7, ln=league, pos=1),
        _row("B", cur=7, nxt=7, ln=league, pos=2),
        _row("C", cur=7, nxt=7, ln=league, pos=3),
        _row("D", cur=7, nxt=7, ln=league, pos=4),
    ]
    nd = _apply_counties_one_scheduled_downs(assignments, c1, schedule=sched)
    assert nd == 2
    by_name = {a["team_name"]: a for a in assignments}
    assert by_name["C"]["next_tier"] == 8
    assert by_name["D"]["next_tier"] == 8
    assert by_name["C"]["mechanism"] == _COUNTIES_ONE_SCHEDULED_DOWN_MECH
    assert by_name["A"]["next_tier"] == 7
    assert by_name["B"]["next_tier"] == 7


def test_scheduled_downs_skips_already_promoted() -> None:
    league = "Counties 1 Test South"
    c1 = frozenset({league})
    sched = {league: 2}
    assignments = [
        _row("Top", cur=7, nxt=6, ln=league, pos=1, mechanism="Auto-promotion"),
        _row("M1", cur=7, nxt=7, ln=league, pos=2),
        _row("M2", cur=7, nxt=7, ln=league, pos=3),
        _row("Bot", cur=7, nxt=7, ln=league, pos=4),
    ]
    nd = _apply_counties_one_scheduled_downs(assignments, c1, schedule=sched, quiet=True)
    assert nd == 2
    by_name = {a["team_name"]: a for a in assignments}
    assert by_name["Bot"]["next_tier"] == 8
    assert by_name["M2"]["next_tier"] == 8
    assert by_name["Top"]["next_tier"] == 6
    assert by_name["M1"]["next_tier"] == 7


def test_scheduled_downs_caps_when_fewer_stayers_than_slots() -> None:
    league = "Counties 1 Test West"
    c1 = frozenset({league})
    sched = {league: 3}
    assignments = [
        _row("A", cur=7, nxt=7, ln=league, pos=1),
        _row("B", cur=7, nxt=7, ln=league, pos=2),
    ]
    nd = _apply_counties_one_scheduled_downs(assignments, c1, schedule=sched, quiet=True)
    assert nd == 2
    assert all(a["next_tier"] == 8 for a in assignments)


def test_second_xv_blocked_to_regional_one_or_above() -> None:
    assert second_xv_promotion_blocked("Old Boys II", 5, {"Old Boys": 8})
    assert second_xv_promotion_blocked("Old Boys II", 3, {})
    assert not second_xv_promotion_blocked("Old Boys", 5, {})
    assert not second_xv_promotion_blocked("Old Boys II", 8, {})


def test_second_xv_blocked_when_would_be_at_or_above_principal_tier() -> None:
    assert second_xv_promotion_blocked("Old Boys II", 7, {"Old Boys": 7})
    assert second_xv_promotion_blocked("Old Boys II", 7, {"Old Boys": 8})
    assert not second_xv_promotion_blocked("Old Boys II", 7, {"Old Boys": 6})


def test_tier8_slot_prefers_below_principal_blocks_at_counties_same_tier() -> None:
    teams = ["Beaconsfield II", "Milton Keynes", "Salisbury"]
    assert _tier8_promotable_positions(teams, 1, {"Beaconsfield": 7}) == frozenset({2})
    assert _tier8_promotable_positions(teams, 1, {"Beaconsfield": 6}) == frozenset({1})


# ---------------------------------------------------------------------------
# Knockout play-off override tests (strict winners only, per source file)
# ---------------------------------------------------------------------------


def _outcome(
    *,
    current_tier: int,
    target_tier: int,
    wins: int,
    losses: int,
    source_file: str = "test.json",
    playoff_complete: bool = True,
) -> dict:
    return {
        "wins": wins,
        "losses": losses,
        "current_tier": current_tier,
        "target_tier": target_tier,
        "source_file": source_file,
        "playoff_complete": playoff_complete,
    }


def test_strict_winners_n1n2_tied_undefeated_no_changes() -> None:
    """Real N1/N2 case: Bham (upper 1-0) and Oundle (lower 1-0). Bham absorbs Oundle in
    the tie ('highest team stays') so no overrides are applied; everyone else keeps
    default. The other two N2 teams (Tynedale, Luctonians) lost — defaults apply.
    """
    src = "N1N2.json"
    outcomes = {
        ("Birmingham Moseley", "National League 1"): _outcome(
            current_tier=3, target_tier=4, wins=1, losses=0, source_file=src
        ),
        ("Oundle", "National League 2 East"): _outcome(
            current_tier=4, target_tier=3, wins=1, losses=0, source_file=src
        ),
        ("Tynedale", "National League 2 North"): _outcome(
            current_tier=4, target_tier=3, wins=0, losses=1, source_file=src
        ),
        ("Luctonians", "National League 2 West"): _outcome(
            current_tier=4, target_tier=3, wins=0, losses=1, source_file=src
        ),
    }
    assignments = [
        _row("Birmingham Moseley", cur=3, nxt=3, ln="National League 1", pos=11),
        _row("Oundle", cur=4, nxt=4, ln="National League 2 East", pos=2),
        _row("Tynedale", cur=4, nxt=4, ln="National League 2 North", pos=2),
        _row("Luctonians", cur=4, nxt=4, ln="National League 2 West", pos=2),
    ]
    assert apply_playoff_overrides(assignments, outcomes) == 0
    for a in assignments:
        assert a["mechanism"] == "Stay"


def test_strict_winners_lower_promotes_when_no_undefeated_upper() -> None:
    """When the upper team in the playoff has lost, the undefeated lower team promotes
    and the (only) upper team is the worst-record relegation pick.
    """
    src = "N1N2.json"
    outcomes = {
        ("Bham Loser", "National League 1"): _outcome(
            current_tier=3, target_tier=4, wins=0, losses=1, source_file=src
        ),
        ("Oundle", "National League 2 East"): _outcome(
            current_tier=4, target_tier=3, wins=1, losses=0, source_file=src
        ),
    }
    assignments = [
        _row("Bham Loser", cur=3, nxt=3, ln="National League 1", pos=11),
        _row("Oundle", cur=4, nxt=4, ln="National League 2 East", pos=2),
    ]
    apply_playoff_overrides(assignments, outcomes)
    by_name = {a["team_name"]: a for a in assignments}

    assert by_name["Bham Loser"]["next_tier"] == 4
    assert by_name["Bham Loser"]["mechanism"] == _PLAYOFF_LOSS_RELEGATE_MECH
    assert by_name["Oundle"]["next_tier"] == 3
    assert by_name["Oundle"]["mechanism"] == _PLAYOFF_WIN_PROMOTE_MECH


def test_strict_winners_lower_promotes_when_strictly_better_than_undefeated_upper() -> None:
    """Real N2/R1 case: Harrogate (lower, 3-0) outranks Henley/Exeter (upper, 1-0)
    because her record is strictly better. She promotes; the worst-record upper team
    (Rossendale 0-1) relegates. Henley & Exeter stay (no override) because they were
    not absorbed — but their default is also Stay so nothing changes for them.
    """
    src = "N2R1.json"
    outcomes = {
        ("Harrogate", "Regional 1 North East"): _outcome(
            current_tier=5, target_tier=4, wins=3, losses=0, source_file=src
        ),
        ("Henley", "National League 2 East"): _outcome(
            current_tier=4, target_tier=5, wins=1, losses=0, source_file=src
        ),
        ("Exeter University", "National League 2 West"): _outcome(
            current_tier=4, target_tier=5, wins=1, losses=0, source_file=src
        ),
        ("Rossendale", "National League 2 North"): _outcome(
            current_tier=4, target_tier=5, wins=0, losses=1, source_file=src
        ),
        ("Stourbridge", "Regional 1 Midlands"): _outcome(
            current_tier=5, target_tier=4, wins=2, losses=1, source_file=src
        ),
        ("Tunbridge Wells", "Regional 1 South Central"): _outcome(
            current_tier=5, target_tier=4, wins=2, losses=1, source_file=src
        ),
        ("Loser1", "Regional 1 South East"): _outcome(
            current_tier=5, target_tier=4, wins=0, losses=1, source_file=src
        ),
    }
    assignments = [
        _row("Harrogate", cur=5, nxt=5, ln="Regional 1 North East", pos=3),
        _row("Henley", cur=4, nxt=4, ln="National League 2 East", pos=12),
        _row("Exeter University", cur=4, nxt=4, ln="National League 2 West", pos=12),
        _row("Rossendale", cur=4, nxt=4, ln="National League 2 North", pos=12),
        _row("Stourbridge", cur=5, nxt=5, ln="Regional 1 Midlands", pos=3),
        _row("Tunbridge Wells", cur=5, nxt=5, ln="Regional 1 South Central", pos=3),
        _row("Loser1", cur=5, nxt=5, ln="Regional 1 South East", pos=3),
    ]
    apply_playoff_overrides(assignments, outcomes)
    by_name = {a["team_name"]: a for a in assignments}

    # Only Harrogate (lower, 3-0 promote) and Rossendale (upper, 0-1 relegate) change.
    assert by_name["Harrogate"]["next_tier"] == 4
    assert by_name["Harrogate"]["mechanism"] == _PLAYOFF_WIN_PROMOTE_MECH
    assert by_name["Rossendale"]["next_tier"] == 5
    assert by_name["Rossendale"]["mechanism"] == _PLAYOFF_LOSS_RELEGATE_MECH

    # Everyone else keeps their default — no override applied (concise diff).
    assert by_name["Henley"]["next_tier"] == 4
    assert by_name["Henley"]["mechanism"] == "Stay"
    assert by_name["Exeter University"]["next_tier"] == 4
    assert by_name["Exeter University"]["mechanism"] == "Stay"
    assert by_name["Stourbridge"]["next_tier"] == 5
    assert by_name["Stourbridge"]["mechanism"] == "Stay"
    assert by_name["Tunbridge Wells"]["next_tier"] == 5
    assert by_name["Tunbridge Wells"]["mechanism"] == "Stay"
    assert by_name["Loser1"]["next_tier"] == 5
    assert by_name["Loser1"]["mechanism"] == "Stay"


def test_strict_winners_lower_with_losses_does_not_promote() -> None:
    """A lower-tier team that has any losses (e.g. 2W-1L) does NOT get promoted, even
    if it has more total wins than other playoff teams. Only undefeated lower teams
    promote (per the user's rule: 'teams that lose go down / dont get promoted').
    """
    src = "test.json"
    outcomes = {
        ("UpperA", "Tier3 League"): _outcome(
            current_tier=3, target_tier=4, wins=0, losses=1, source_file=src
        ),
        ("LowerWinsButLost", "Tier4 League"): _outcome(
            current_tier=4, target_tier=3, wins=2, losses=1, source_file=src
        ),
    }
    assignments = [
        _row("UpperA", cur=3, nxt=3, ln="Tier3 League", pos=11),
        _row("LowerWinsButLost", cur=4, nxt=4, ln="Tier4 League", pos=2),
    ]
    assert apply_playoff_overrides(assignments, outcomes) == 0
    by_name = {a["team_name"]: a for a in assignments}
    assert by_name["LowerWinsButLost"]["next_tier"] == 4
    assert by_name["LowerWinsButLost"]["mechanism"] == "Stay"
    assert by_name["UpperA"]["next_tier"] == 3
    assert by_name["UpperA"]["mechanism"] == "Stay"


def test_strict_winners_relegations_pick_worst_record_upper_first() -> None:
    """Two undefeated lower teams promote. Two upper teams relegate, picked by worst
    record first (most losses, fewest wins, then alphabetical).
    """
    src = "test.json"
    outcomes = {
        ("LowerA", "Tier6 A"): _outcome(
            current_tier=6, target_tier=5, wins=4, losses=0, source_file=src
        ),
        ("LowerB", "Tier6 B"): _outcome(
            current_tier=6, target_tier=5, wins=4, losses=0, source_file=src
        ),
        ("UpperBad", "Tier5 Mid"): _outcome(
            current_tier=5, target_tier=6, wins=0, losses=2, source_file=src
        ),
        ("UpperWorse", "Tier5 East"): _outcome(
            current_tier=5, target_tier=6, wins=0, losses=2, source_file=src
        ),
        ("UpperOK", "Tier5 West"): _outcome(
            current_tier=5, target_tier=6, wins=1, losses=1, source_file=src
        ),
    }
    assignments = [
        _row("LowerA", cur=6, nxt=6, ln="Tier6 A", pos=2),
        _row("LowerB", cur=6, nxt=6, ln="Tier6 B", pos=2),
        _row("UpperBad", cur=5, nxt=5, ln="Tier5 Mid", pos=10),
        _row("UpperWorse", cur=5, nxt=5, ln="Tier5 East", pos=10),
        _row("UpperOK", cur=5, nxt=5, ln="Tier5 West", pos=10),
    ]
    apply_playoff_overrides(assignments, outcomes)
    by_name = {a["team_name"]: a for a in assignments}

    assert by_name["LowerA"]["next_tier"] == 5
    assert by_name["LowerA"]["mechanism"] == _PLAYOFF_WIN_PROMOTE_MECH
    assert by_name["LowerB"]["next_tier"] == 5
    assert by_name["LowerB"]["mechanism"] == _PLAYOFF_WIN_PROMOTE_MECH
    # UpperBad and UpperWorse both 0-2; alphabetical tiebreak picks both.
    assert by_name["UpperBad"]["next_tier"] == 6
    assert by_name["UpperBad"]["mechanism"] == _PLAYOFF_LOSS_RELEGATE_MECH
    assert by_name["UpperWorse"]["next_tier"] == 6
    assert by_name["UpperWorse"]["mechanism"] == _PLAYOFF_LOSS_RELEGATE_MECH
    # UpperOK (1-1) is not in the bottom-2 by record, so stays at default.
    assert by_name["UpperOK"]["next_tier"] == 5
    assert by_name["UpperOK"]["mechanism"] == "Stay"


def test_strict_winners_pending_file_defers_all_overrides() -> None:
    """Blackheath case: file incomplete, no overrides applied at all."""
    src = "ChampN1.json"
    outcomes = {
        ("Blackheath", "National League 1"): _outcome(
            current_tier=3,
            target_tier=2,
            wins=1,
            losses=0,
            source_file=src,
            playoff_complete=False,
        ),
        ("Plymouth Albion", "National League 1"): _outcome(
            current_tier=3,
            target_tier=2,
            wins=0,
            losses=1,
            source_file=src,
            playoff_complete=False,
        ),
    }
    assignments = [
        _row("Blackheath", cur=3, nxt=3, ln="National League 1", pos=2),
        _row("Plymouth Albion", cur=3, nxt=3, ln="National League 1", pos=3),
    ]
    assert apply_playoff_overrides(assignments, outcomes) == 0
    for a in assignments:
        assert a["mechanism"] == "Stay"


def test_strict_winners_skips_teams_not_in_outcomes() -> None:
    a = _row("Untouched", cur=4, nxt=4, ln="National League 2 East", pos=5)
    assert apply_playoff_overrides([a], {}) == 0
    assert a["mechanism"] == "Stay"


def test_strict_winners_per_file_grouping_is_independent() -> None:
    """Two playoff files: each file's tiebreak / promotion logic runs independently."""
    file_a = {
        ("AlphaUpper", "Tier3 League"): _outcome(
            current_tier=3, target_tier=4, wins=1, losses=0, source_file="A.json"
        ),
        ("AlphaLower", "Tier4 League A"): _outcome(
            current_tier=4, target_tier=3, wins=0, losses=1, source_file="A.json"
        ),
    }
    file_b = {
        ("BetaUpper", "Tier3 League"): _outcome(
            current_tier=3, target_tier=4, wins=0, losses=1, source_file="B.json"
        ),
        ("BetaLower", "Tier4 League B"): _outcome(
            current_tier=4, target_tier=3, wins=1, losses=0, source_file="B.json"
        ),
    }
    outcomes = {**file_a, **file_b}
    assignments = [
        _row("AlphaUpper", cur=3, nxt=3, ln="Tier3 League", pos=11),
        _row("AlphaLower", cur=4, nxt=4, ln="Tier4 League A", pos=2),
        _row("BetaUpper", cur=3, nxt=3, ln="Tier3 League", pos=11),
        _row("BetaLower", cur=4, nxt=4, ln="Tier4 League B", pos=2),
    ]
    apply_playoff_overrides(assignments, outcomes)
    by_name = {a["team_name"]: a for a in assignments}

    # File A: only AlphaUpper undefeated, no undefeated lower → no promotion, no override.
    assert by_name["AlphaUpper"]["mechanism"] == "Stay"
    assert by_name["AlphaLower"]["mechanism"] == "Stay"

    # File B: BetaLower undefeated, BetaUpper lost. BetaLower promotes; BetaUpper
    # is the only upper-team and worst-record → relegates.
    assert by_name["BetaUpper"]["next_tier"] == 4
    assert by_name["BetaUpper"]["mechanism"] == _PLAYOFF_LOSS_RELEGATE_MECH
    assert by_name["BetaLower"]["next_tier"] == 3
    assert by_name["BetaLower"]["mechanism"] == _PLAYOFF_WIN_PROMOTE_MECH
