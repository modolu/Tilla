"""Tests for kaggriculture_bot.tasks (Milestone 2 single-unit execution)."""

from kaggriculture_bot.actions import build_action
from kaggriculture_bot.models import (
    PASS_OBJECTIVE,
    Objective,
    ObjectiveKind,
    Position,
    StrategicPlan,
    UnitAction,
    UnitOp,
)
from kaggriculture_bot.tasks import assign_jobs, farmer_job
from kaggriculture_bot.validator import validate_or_fallback
from tests.conftest import make_state, raw_plant

P = Position


def test_objective_on_current_tile_becomes_direct_action(obs_no_hands):
    state = make_state(obs_no_hands, tiles={(4, 4): raw_plant()})
    assert farmer_job(state, Objective(ObjectiveKind.WATER_CROP, (P(4, 4),))) == UnitAction(
        UnitOp.WATER
    )
    assert farmer_job(state, Objective(ObjectiveKind.HARVEST, (P(4, 4),))) == UnitAction(
        UnitOp.HARVEST
    )
    assert farmer_job(state, Objective(ObjectiveKind.FEED_ANIMAL, (P(4, 4),))) == UnitAction(
        UnitOp.FEED
    )
    assert farmer_job(state, Objective(ObjectiveKind.DELIVER, (P(4, 4),))) == UnitAction(
        UnitOp.DROP
    )
    fetch = Objective(ObjectiveKind.FETCH_FEED, (P(4, 4),), "WHEAT", 2)
    assert farmer_job(state, fetch) == UnitAction(UnitOp.PICKUP, "WHEAT", 2)
    plant = Objective(ObjectiveKind.PLANT, (P(4, 4),), "WHEAT", None, 22)
    assert farmer_job(state, plant) == UnitAction(UnitOp.PLANT, "WHEAT")


def test_distant_objective_becomes_one_deterministic_step(obs_no_hands):
    state = make_state(obs_no_hands)
    assert farmer_job(state, Objective(ObjectiveKind.WATER_CROP, (P(4, 2),))) == UnitAction(
        UnitOp.NORTH
    )
    assert farmer_job(state, Objective(ObjectiveKind.WATER_CROP, (P(2, 2),))) == UnitAction(
        UnitOp.NORTH
    )
    assert farmer_job(state, Objective(ObjectiveKind.WATER_CROP, (P(2, 4),))) == UnitAction(
        UnitOp.WEST
    )


def test_nearest_of_several_targets_is_chosen(obs_no_hands):
    state = make_state(obs_no_hands, farmer=(2, 2))
    obj = Objective(ObjectiveKind.WATER_CROP, (P(4, 4), P(2, 3), P(0, 0)))
    assert farmer_job(state, obj) == UnitAction(UnitOp.SOUTH)  # (2,3) is adjacent


def test_locked_tiles_are_not_entered_but_a_locked_start_is_left(obs_no_hands):
    state = make_state(obs_no_hands, farmer=(5, 4))  # locked NE access tile (hand-spawn case)
    assert farmer_job(state, Objective(ObjectiveKind.DELIVER, (P(4, 4),))) == UnitAction(
        UnitOp.WEST
    )
    state = make_state(obs_no_hands, farmer=(4, 4))
    assert farmer_job(state, Objective(ObjectiveKind.WATER_CROP, (P(5, 4),))) == UnitAction(
        UnitOp.PASS
    )


def test_unit_never_walks_off_board(obs_no_hands):
    state = make_state(obs_no_hands, farmer=(0, 0))
    action = farmer_job(state, Objective(ObjectiveKind.WATER_CROP, (P(0, 3),)))
    assert action == UnitAction(UnitOp.SOUTH)
    action = farmer_job(state, Objective(ObjectiveKind.WATER_CROP, (P(3, 0),)))
    assert action == UnitAction(UnitOp.EAST)


def test_unreachable_or_empty_targets_degrade_to_pass(obs_no_hands):
    state = make_state(obs_no_hands)
    assert farmer_job(state, Objective(ObjectiveKind.HARVEST, (P(9, 9),))) == UnitAction(
        UnitOp.PASS
    )
    assert farmer_job(state, Objective(ObjectiveKind.HARVEST, ())) == UnitAction(UnitOp.PASS)
    assert farmer_job(state, PASS_OBJECTIVE) == UnitAction(UnitOp.PASS)
    assert farmer_job(state, Objective(ObjectiveKind.REPOSITION, (P(4, 4),))) == UnitAction(
        UnitOp.PASS
    )


def test_plant_after_deadline_is_not_executed(obs_no_hands):
    state = make_state(obs_no_hands, hour=23)
    obj = Objective(ObjectiveKind.PLANT, (P(4, 4),), "WHEAT", None, 22)
    assert farmer_job(state, obj) == UnitAction(UnitOp.PASS)


def test_hired_hands_pass_and_output_stays_legal(obs_two_hands):
    state = make_state(obs_two_hands, tiles={(4, 4): raw_plant()})
    plan = StrategicPlan(Objective(ObjectiveKind.WATER_CROP, (P(4, 4),)))
    turn = assign_jobs(state, plan)
    assert turn.farmer == UnitAction(UnitOp.WATER)
    assert turn.hands == (UnitAction(UnitOp.PASS), UnitAction(UnitOp.PASS))
    action = build_action(turn)
    assert action["hands"] == [["PASS"], ["PASS"]]
    assert validate_or_fallback(action, 2) == action
