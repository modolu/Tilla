"""Tests for the offline tooling contracts that Milestone 4 evidence relies on."""

import pytest

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


def test_wilson_lower_bound_matches_reference_values():
    from tools.tournament import gate_passes, wilson_lower_bound

    assert wilson_lower_bound(0, 0) == 0.0
    # 2120 wins of 4000 (53.0%): the lower bound sits just above 51.4%.
    assert abs(wilson_lower_bound(2120, 4000) - 0.5145) < 0.001
    assert wilson_lower_bound(4000, 4000) > 0.999
    assert abs(wilson_lower_bound(50, 100) - 0.4038) < 0.001
    passing = {
        "win_rate": 0.6, "wilson_lower_95": 0.58, "median_margin": 1.0, "crashes": 0, "timeouts": 0
    }  # fmt: skip
    assert gate_passes(passing)
    assert not gate_passes({**passing, "win_rate": 0.53})
    assert not gate_passes({**passing, "wilson_lower_95": 0.5})
    assert not gate_passes({**passing, "median_margin": 0.0})
    assert not gate_passes({**passing, "timeouts": 1})


# --- Care-loss checker (offline harness) ----------------------------------------------------


def _scripted_env(monkeypatch, always_spawn_weeds):
    pytest.importorskip("kaggle_environments")
    from kaggle_environments import make
    from kaggle_environments.envs.kaggriculture import kaggriculture as kg

    if always_spawn_weeds:

        def spawn_everywhere(farm, board_size, weed_chance, rng):
            for y in range(board_size):
                for x in range(board_size):
                    if farm["tiles"][y][x] is None:
                        farm["tiles"][y][x] = {"kind": "WEED"}

        monkeypatch.setattr(kg, "_spawn_weeds", spawn_everywhere)
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": 3}, debug=True)
    env.reset()
    return env


PASS = {"farmer": ["PASS"], "hands": [], "market": []}


def _p0(env, farmer=None, market=None):
    env.step([{"farmer": farmer or ["PASS"], "hands": [], "market": market or []}, PASS])
    return env.state[0].observation


def test_hour_23_harvest_then_weed_spawn_is_not_a_care_loss(monkeypatch):
    """Mature wheat harvested on the day's last turn: full yield is credited,
    the emptied tile gets an end-of-day weed, and no care loss is counted."""
    from kaggriculture_bot.constants import CROPS
    from tools.harness import _care_losses

    wheat = CROPS["WHEAT"]
    env = _scripted_env(monkeypatch, always_spawn_weeds=True)
    _p0(env, market=[["BUY_SEED", "WHEAT", 1]])
    _p0(env, farmer=["PLANT", "WHEAT"])  # on (4,4), the farmer's spawn tile
    _p0(env, farmer=["WATER"])  # planting day counts as unwatered: water it the same day
    while env.state[0].observation["day"] <= wheat.max_yield_day:
        obs = env.state[0].observation
        if obs["hour"] == 23 and obs["day"] == wheat.max_yield_day:
            tile = obs["farms"][0]["tiles"][4][4]
            assert tile["kind"] == "PLANT" and tile["watered_today"] is False
            harvested = tile["yield_units"]
            after = _p0(env, farmer=["HARVEST"])  # last turn of the day, then the refresh
            break
        if obs["hour"] == 0 and 0 < obs["day"] < wheat.max_yield_day:
            _p0(env, farmer=["WATER"])
        else:
            _p0(env)
    assert after["farms"][0]["tiles"][4][4] == {"kind": "WEED"}  # official spawn on the empty tile
    assert harvested >= 1 and after["private"]["shed"]["WHEAT"] == harvested  # full yield credited
    losses = _care_losses(env.steps, 0)
    assert losses.get("crops_lost_unwatered", 0) == 0
    assert losses.get("fresh_plantings_unwatered", 0) == 0
    assert losses["harvested_then_weed_spawn"] == 1


def test_genuine_unwatered_death_is_still_a_care_loss(monkeypatch):
    from tools.harness import _care_losses

    env = _scripted_env(monkeypatch, always_spawn_weeds=False)
    _p0(env, market=[["BUY_SEED", "WHEAT", 1]])
    _p0(env, farmer=["PLANT", "WHEAT"])  # planting day counts as the first unwatered day
    while env.state[0].observation["day"] < 2:
        _p0(env)
    assert env.state[0].observation["farms"][0]["tiles"][4][4] == {"kind": "WEED"}
    losses = _care_losses(env.steps, 0)
    assert losses["crops_lost_unwatered"] == 1
    assert losses.get("harvested_then_weed_spawn", 0) == 0
