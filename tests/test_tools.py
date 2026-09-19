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


def test_duplicate_checks_classify_same_turn_conflicts(obs_no_hands):
    """The benchmark classifier separates duplicate emitted single-use actions,
    tile-claim no-ops, redundant care and duplicate planner assignments."""
    from collections import Counter

    from kaggriculture_bot.models import PRIORITY_DAILY_WORK, Job, JobKind, Position
    from kaggriculture_bot.runtime import reset_episode_memory
    from tests.conftest import raw_plant
    from tools.harness import _duplicate_checks

    obs = obs_no_hands
    obs["farms"][0]["tiles"][3][4] = raw_plant(watered_today=True, consecutive_unwatered=0)
    units = [[4, 4], [4, 4], [3, 4], [3, 4], [4, 3]]
    acts = [["WATER"], ["WATER"], ["PLANT", "WHEAT"], ["BUILD_COOP"], ["WATER"]]
    events = _duplicate_checks(obs, units, acts, Counter())
    kinds = Counter(e["kind"] for e in events)
    assert kinds == {
        "duplicate_single_use_action": 1,  # two WATERs on (4,4)
        "same_turn_noop": 1,  # PLANT and BUILD_COOP on (3,4)
        "redundant_care_action": 1,  # WATER on the already-watered (4,3)
    }
    # Moves and logistics through a shared tile are never conflicts.
    assert (
        _duplicate_checks(obs, [[1, 1], [1, 1]], [["NORTH"], ["PICKUP", "WHEAT", 1]], Counter())
        == []
    )
    # Two units holding jobs of one single-use kind on one tile is an assignment duplicate ...
    memory = reset_episode_memory(0)
    memory.assignment_day = obs["day"]
    memory.unit_assignments = {
        0: Job(JobKind.WATER, Position(2, 2), PRIORITY_DAILY_WORK),
        1: Job(JobKind.WATER, Position(2, 2), PRIORITY_DAILY_WORK),
        2: Job(JobKind.FEED, Position(2, 2), PRIORITY_DAILY_WORK, item="WHEAT"),
    }
    events = _duplicate_checks(obs, [[0, 0]], [["PASS"]], Counter())
    assert [e["kind"] for e in events] == ["duplicate_single_use_assignment"]
    assert events[0]["resource"] == ("WATER", 2, 2)
    # ... while different kinds on one tile (feed + collect) are distinct resources.
    memory.unit_assignments = {
        0: Job(JobKind.COLLECT, Position(2, 2), PRIORITY_DAILY_WORK),
        1: Job(JobKind.FEED, Position(2, 2), PRIORITY_DAILY_WORK, item="WHEAT"),
    }
    assert _duplicate_checks(obs, [[0, 0]], [["PASS"]], Counter()) == []
