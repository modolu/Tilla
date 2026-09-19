"""Action-shape tests for kaggriculture_bot.actions (Milestone 0: PASS only)."""

import pytest

from kaggriculture_bot.actions import build_pass_action, fallback_pass_action


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


@pytest.mark.parametrize("hand_count", [0, 1, 2, 5])
def test_build_pass_action_has_one_pass_per_unit(hand_count):
    action = build_pass_action(hand_count)
    assert_valid_action_shape(action, expected_hands=hand_count)
    assert action["farmer"] == ["PASS"]
    assert action["hands"] == [["PASS"]] * hand_count
    assert action["market"] == []


def test_build_pass_action_rejects_negative_hand_count():
    with pytest.raises(ValueError):
        build_pass_action(-1)


def test_build_pass_action_returns_fresh_objects():
    first = build_pass_action(2)
    second = build_pass_action(2)
    assert first == second
    assert first is not second
    assert first["hands"] is not second["hands"]
    assert first["hands"][0] is not first["hands"][1]


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
