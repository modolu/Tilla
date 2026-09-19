"""Tests for the offline tooling contracts that Milestone 4 evidence relies on."""

from tools.harness import hiring_disabled


def test_hiring_disabled_removes_only_hire_orders():
    action = {
        "farmer": ["WATER"],
        "hands": [["NORTH"], ["PASS"]],
        "market": [["SELL", "WHEAT", 3], ["HIRE"], ["BUY_SEED", "MELON", 2], ["HIRE"]],
    }
    control = hiring_disabled(lambda obs: action)
    result = control({"player": 0})
    assert result["market"] == [["SELL", "WHEAT", 3], ["BUY_SEED", "MELON", 2]]
    assert result["farmer"] == ["WATER"] and result["hands"] == [["NORTH"], ["PASS"]]
    assert action["market"][1] == ["HIRE"]  # the wrapped agent's action is not mutated


def test_hiring_disabled_control_makes_the_same_unit_decisions(obs_no_hands):
    """Given the same observation, the control differs from the candidate only
    in the HIRE orders (same-state ablation)."""
    import copy

    import main
    from tests.conftest import make_state, raw_plant

    field = {
        (x, y): raw_plant(planted_day=2, consecutive_unwatered=0)
        for x in range(5)
        for y in range(5)
    }
    state = make_state(obs_no_hands, day=6, hour=0, tiles=field, money=2000)
    # Rebuild the raw observation the same way to feed both agents.
    obs = copy.deepcopy(obs_no_hands)
    obs["day"], obs["hour"], obs["step"] = 6, 0, 144
    for (x, y), tile in field.items():
        obs["farms"][0]["tiles"][y][x] = tile
    obs["farms"][0]["money"] = 2000
    assert state.me.money == 2000
    candidate = main.agent(obs)
    control = hiring_disabled(main.agent)(obs)
    assert any(o[0] == "HIRE" for o in candidate["market"])
    assert not any(o[0] == "HIRE" for o in control["market"])
    assert control["farmer"] == candidate["farmer"] and control["hands"] == candidate["hands"]
    assert [o for o in candidate["market"] if o[0] != "HIRE"] == control["market"]


def test_hiring_disabled_passes_through_non_dict_actions():
    assert hiring_disabled(lambda obs: None)({}) is None
