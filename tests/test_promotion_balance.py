"""Tests for Counties 1 scheduled relegation overrides in promotion_relegation."""

from rugby.analysis.promotion_relegation import (
    _COUNTIES_ONE_SCHEDULED_DOWN_MECH,
    _apply_counties_one_scheduled_downs,
    _tier8_promotable_positions,
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
