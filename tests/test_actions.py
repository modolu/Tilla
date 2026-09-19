"""Tests for kaggriculture_bot.actions (typed -> Kaggle shape) and validator (legality)."""

import copy

import pytest

from kaggriculture_bot.actions import (
    build_action,
    build_pass_action,
    fallback_pass_action,
    format_market_order,
    format_unit_action,
)
from kaggriculture_bot.constants import MAX_MARKET_ORDERS_PER_TURN
from kaggriculture_bot.models import (
    MarketOp,
    MarketOrder,
    TurnAction,
    UnitAction,
    UnitOp,
    pass_turn_action,
)
from kaggriculture_bot.validator import (
    sanitize_market_order,
    sanitize_unit_action,
    validate_or_fallback,
)


def assert_valid_action_shape(action: dict, expected_hands: int) -> None:
    assert isinstance(action, dict)
    assert set(action.keys()) == {"farmer", "hands", "market"}
    assert isinstance(action["farmer"], list) and len(action["farmer"]) >= 1
    assert isinstance(action["farmer"][0], str)
    assert isinstance(action["hands"], list) and len(action["hands"]) == expected_hands
    for hand_action in action["hands"]:
        assert isinstance(hand_action, list) and len(hand_action) >= 1
        assert isinstance(hand_action[0], str)
    assert isinstance(action["market"], list) and len(action["market"]) <= 10
    for order in action["market"]:
        assert isinstance(order, list) and len(order) >= 1 and isinstance(order[0], str)


# --- Typed action -> Kaggle shape ---------------------------------------------------


@pytest.mark.parametrize("hand_count", [0, 1, 2, 5])
def test_typed_pass_turn_formats_to_one_pass_per_unit(hand_count):
    turn = pass_turn_action(hand_count)
    assert len(turn.hands) == hand_count and turn.market == ()
    action = build_action(turn)
    assert_valid_action_shape(action, expected_hands=hand_count)
    assert action == {"farmer": ["PASS"], "hands": [["PASS"]] * hand_count, "market": []}
    assert build_pass_action(hand_count) == action


def test_pass_turn_rejects_negative_hand_count():
    with pytest.raises(ValueError):
        pass_turn_action(-1)


def test_unit_action_formatting_matches_official_forms():
    assert format_unit_action(UnitAction(UnitOp.WATER)) == ["WATER"]
    assert format_unit_action(UnitAction(UnitOp.PLANT, "WHEAT")) == ["PLANT", "WHEAT"]
    assert format_unit_action(UnitAction(UnitOp.PICKUP, "WHEAT", 3)) == ["PICKUP", "WHEAT", 3]
    assert format_unit_action(UnitAction(UnitOp.PLACE, "GOOSE")) == ["PLACE", "GOOSE"]


def test_market_order_formatting_matches_official_forms():
    assert format_market_order(MarketOrder(MarketOp.HIRE)) == ["HIRE"]
    assert format_market_order(MarketOrder(MarketOp.BUY_LAND)) == ["BUY_LAND"]
    assert format_market_order(MarketOrder(MarketOp.SELL, "WHEAT", 4)) == ["SELL", "WHEAT", 4]
    assert format_market_order(MarketOrder(MarketOp.BUY_SEED, "CARROT", 1)) == [
        "BUY_SEED",
        "CARROT",
        1,
    ]


def test_build_action_preserves_hand_and_market_order():
    turn = TurnAction(
        farmer=UnitAction(UnitOp.HARVEST),
        hands=(UnitAction(UnitOp.NORTH), UnitAction(UnitOp.PASS), UnitAction(UnitOp.WATER)),
        market=(MarketOrder(MarketOp.SELL, "EGG", 2), MarketOrder(MarketOp.HIRE)),
    )
    assert build_action(turn) == {
        "farmer": ["HARVEST"],
        "hands": [["NORTH"], ["PASS"], ["WATER"]],
        "market": [["SELL", "EGG", 2], ["HIRE"]],
    }


def test_build_action_returns_fresh_objects():
    turn = pass_turn_action(2)
    first, second = build_action(turn), build_action(turn)
    assert first == second and first is not second
    assert first["hands"] is not second["hands"]
    assert first["hands"][0] is not first["hands"][1]
    assert first["farmer"] is not second["farmer"]


# --- Fallback --------------------------------------------------------------------------


def test_fallback_pass_action_matches_official_pass_agent_shape():
    action = fallback_pass_action()
    assert action == {"farmer": ["PASS"], "hands": [], "market": []}
    assert fallback_pass_action() is not action


@pytest.mark.parametrize("hand_count", [0, 1, 3])
def test_fallback_pass_action_emits_one_pass_per_known_hand(hand_count):
    action = fallback_pass_action(hand_count)
    assert_valid_action_shape(action, expected_hands=hand_count)
    assert action == build_pass_action(hand_count)


@pytest.mark.parametrize("bad_count", [-1, None, "2", 2.0, True])
def test_fallback_pass_action_never_raises_on_bad_hand_count(bad_count):
    assert fallback_pass_action(bad_count) == {"farmer": ["PASS"], "hands": [], "market": []}


# --- Validator ------------------------------------------------------------------------------


def test_validator_passes_through_legal_action_as_fresh_copy():
    action = {"farmer": ["WATER"], "hands": [["NORTH"], ["PLANT", "WHEAT"]], "market": [["HIRE"]]}
    original = copy.deepcopy(action)
    result = validate_or_fallback(action, hand_count=2)
    assert result == original
    assert result is not action and result["hands"] is not action["hands"]
    assert action == original


@pytest.mark.parametrize("hand_count", [0, 2])
def test_validator_accepts_typed_pass_turn(hand_count):
    action = build_pass_action(hand_count)
    assert validate_or_fallback(action, hand_count) == action


def test_validator_pads_and_trims_hands_to_hired_count():
    too_few = validate_or_fallback({"farmer": ["PASS"], "hands": [], "market": []}, hand_count=2)
    assert too_few["hands"] == [["PASS"], ["PASS"]]
    too_many = validate_or_fallback(
        {"farmer": ["PASS"], "hands": [["NORTH"], ["SOUTH"], ["EAST"]], "market": []}, hand_count=1
    )
    assert too_many["hands"] == [["NORTH"]]


@pytest.mark.parametrize(
    "farmer",
    [
        None,
        [],
        "PASS",
        ["FLY"],
        ["PLANT"],
        ["PLANT", 3],
        ["WATER", "X"],
        [1],
        ["PICKUP", "WHEAT", 0],
    ],
)
def test_validator_downgrades_malformed_farmer_action_to_pass(farmer):
    result = validate_or_fallback({"farmer": farmer, "hands": [], "market": []}, hand_count=0)
    assert result["farmer"] == ["PASS"]


def test_validator_downgrades_only_the_malformed_hand():
    result = validate_or_fallback(
        {"farmer": ["PASS"], "hands": [["WATER"], ["DANCE"], ["PLACE", "GOOSE", 1]], "market": []},
        hand_count=3,
    )
    assert result["hands"] == [["WATER"], ["PASS"], ["PLACE", "GOOSE", 1]]


def test_validator_trims_market_orders_to_official_cap_keeping_earliest():
    orders = [["SELL", "WHEAT", i + 1] for i in range(15)]
    result = validate_or_fallback({"farmer": ["PASS"], "hands": [], "market": orders}, 0)
    assert len(result["market"]) == MAX_MARKET_ORDERS_PER_TURN == 10
    assert result["market"] == orders[:10]


def test_validator_drops_malformed_market_orders_before_applying_cap():
    orders = [["BOGUS"], ["HIRE", "x"], ["SELL", "WHEAT"], ["SELL", "WHEAT", -1], ["BUY_LAND"]]
    orders += [["BUY_SEED", "WHEAT", 1]] * 12
    result = validate_or_fallback({"farmer": ["PASS"], "hands": [], "market": orders}, 0)
    assert result["market"][0] == ["BUY_LAND"]
    assert len(result["market"]) == 10


@pytest.mark.parametrize("bad_action", [None, [], "PASS", 3, {}, {"farmer": None, "hands": None}])
def test_validator_rebuilds_anything_unshaped_into_all_pass(bad_action):
    result = validate_or_fallback(bad_action, hand_count=2)
    assert result == {"farmer": ["PASS"], "hands": [["PASS"], ["PASS"]], "market": []}


def test_validator_negative_hand_count_yields_no_hands():
    assert validate_or_fallback(build_pass_action(0), hand_count=-3)["hands"] == []


def test_sanitizers_accept_every_official_op_form():
    for op in UnitOp:
        if op in (UnitOp.PICKUP, UnitOp.PLANT, UnitOp.PLACE):
            assert sanitize_unit_action([op.value, "WHEAT"]) == [op.value, "WHEAT"]
        else:
            assert sanitize_unit_action([op.value]) == [op.value]
    assert sanitize_unit_action(["PICKUP", "WHEAT", 2]) == ["PICKUP", "WHEAT", 2]
    assert sanitize_unit_action(["PLANT", "WHEAT", 2]) == ["PLANT", "WHEAT", 2]
    for op in MarketOp:
        if op in (MarketOp.HIRE, MarketOp.BUY_LAND):
            assert sanitize_market_order([op.value]) == [op.value]
            assert sanitize_market_order([op.value, "X", 1]) is None
        else:
            assert sanitize_market_order([op.value, "WHEAT", 1]) == [op.value, "WHEAT", 1]
            assert sanitize_market_order([op.value, "WHEAT"]) is None
            assert sanitize_market_order([op.value, "WHEAT", True]) is None


def test_sanitized_output_never_aliases_input():
    raw = ["PICKUP", "WHEAT", 2]
    out = sanitize_unit_action(raw)
    assert out == raw and out is not raw
    raw_order = ["SELL", "WHEAT", 1]
    out_order = sanitize_market_order(raw_order)
    assert out_order == raw_order and out_order is not raw_order
