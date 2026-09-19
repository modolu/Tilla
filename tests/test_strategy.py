"""Tests for kaggriculture_bot.strategy (Milestone 2 farm-care baseline priorities)."""

import pytest

from kaggriculture_bot.constants import (
    BASELINE_MAX_PLANTS,
    EARLY_MIN_CASH_RESERVE,
    FEED_WHEAT_RESERVE_PER_ANIMAL,
)
from kaggriculture_bot.models import MarketOp, MarketOrder, ObjectiveKind, Position
from kaggriculture_bot.runtime import reset_episode_memory
from kaggriculture_bot.strategy import PLANT_DEADLINE_HOUR, choose_plan
from tests.conftest import make_state, raw_animal, raw_plant

P = Position


def plan_for(state):
    return choose_plan(state, reset_episode_memory(state.player_id))


# --- Watering ------------------------------------------------------------------------------


def test_at_risk_watering_outranks_planting(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=3,
        hour=5,
        tiles={(2, 2): raw_plant(planted_day=1, consecutive_unwatered=1)},
        seeds={"WHEAT": 3},
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.WATER_CROP
    assert plan.objective.targets == (P(2, 2),)


def test_fresh_planting_is_a_watering_priority(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=2,
        hour=10,
        tiles={(4, 4): raw_plant(planted_day=2, consecutive_unwatered=1)},
        seeds={"WHEAT": 5},
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.WATER_CROP
    assert plan.objective.targets == (P(4, 4),)


def test_already_watered_crop_is_not_watered_again(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=2,
        hour=10,
        tiles={(4, 4): raw_plant(planted_day=1, watered_today=True, consecutive_unwatered=0)},
        seeds={"WHEAT": 5},
    )
    plan = plan_for(state)
    assert plan.objective.kind is not ObjectiveKind.WATER_CROP
    assert P(4, 4) not in plan.objective.targets


def test_routine_watering_precedes_harvest_and_planting(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=4,
        hour=1,
        tiles={
            (4, 4): raw_plant(
                planted_day=0, watered_today=True, consecutive_unwatered=0, yield_units=4
            ),
            (3, 4): raw_plant(planted_day=0, consecutive_unwatered=0, yield_units=3),
        },
        seeds={"WHEAT": 5},
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.WATER_CROP
    assert plan.objective.targets == (P(3, 4),)


# --- Feeding ---------------------------------------------------------------------------------


def test_at_risk_animal_feed_outranks_planting_when_wheat_is_carried(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=3,
        hour=2,
        tiles={(1, 1): raw_animal(consecutive_unfed=1)},
        inventory={"WHEAT": 2},
        seeds={"WHEAT": 5},
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.FEED_ANIMAL
    assert plan.objective.targets == (P(1, 1),)


def test_feed_fetches_from_shed_when_not_carried(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=3,
        hour=2,
        tiles={(1, 1): raw_animal(consecutive_unfed=1)},
        shed={"WHEAT": 6},
        seeds={"WHEAT": 5},
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.FETCH_FEED
    assert plan.objective.targets == (P(4, 4),)  # only unlocked shed access tile
    assert plan.objective.item == "WHEAT" and plan.objective.quantity == 1
    # The same-turn PICKUP (1) and the feed reserve (2 per animal) are kept back from the sale.
    sells = [o for o in plan.market if o.op is MarketOp.SELL]
    assert sells == [MarketOrder(MarketOp.SELL, "WHEAT", 6 - 1 - FEED_WHEAT_RESERVE_PER_ANIMAL)]


def test_feed_buys_wheat_only_when_none_is_available(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=3,
        hour=2,
        tiles={(1, 1): raw_animal(consecutive_unfed=1)},
        seeds={"WHEAT": 5},
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.REPOSITION
    assert plan.objective.targets == (P(4, 4),)
    assert MarketOrder(MarketOp.BUY_PRODUCT, "WHEAT", 1) in plan.market
    assert not [o for o in plan.market if o.op is MarketOp.BUY_SEED]


def test_at_risk_crop_is_watered_before_a_feed_shed_trip(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=3,
        hour=2,
        tiles={
            (1, 1): raw_animal(consecutive_unfed=1),
            (4, 3): raw_plant(planted_day=2, consecutive_unwatered=1),
        },
        shed={"WHEAT": 3},
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.WATER_CROP


def test_routine_feeding_outranks_planting(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=3,
        hour=2,
        tiles={(1, 1): raw_animal(consecutive_unfed=0)},
        inventory={"WHEAT": 1},
        seeds={"WHEAT": 5},
    )
    assert plan_for(state).objective.kind is ObjectiveKind.FEED_ANIMAL


# --- Harvest ---------------------------------------------------------------------------------


def test_ready_harvest_outranks_expansion(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=4,
        hour=6,
        tiles={
            (3, 3): raw_plant(
                planted_day=0, watered_today=True, consecutive_unwatered=0, yield_units=4
            )
        },
        seeds={"WHEAT": 5},
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.HARVEST
    assert plan.objective.targets == (P(3, 3),)


def test_decaying_crop_harvest_outranks_routine_watering(obs_no_hands):
    """A one-time crop past max_yield_day loses a unit every other turn: survival tier."""
    state = make_state(
        obs_no_hands,
        day=5,
        hour=0,
        tiles={
            (3, 3): raw_plant(planted_day=0, consecutive_unwatered=0, yield_units=4),  # age 5
            (4, 4): raw_plant(planted_day=3, consecutive_unwatered=0, yield_units=2),  # needs water
        },
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.HARVEST
    assert plan.objective.targets == (P(3, 3),)


def test_immature_crop_is_not_harvested(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=1,
        hour=6,
        tiles={
            (3, 3): raw_plant(
                planted_day=0, watered_today=True, consecutive_unwatered=0, yield_units=1
            )
        },
        seeds={"WHEAT": 5},
    )
    assert plan_for(state).objective.kind is not ObjectiveKind.HARVEST


def test_animal_product_is_harvested(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=6,
        hour=6,
        tiles={(1, 1): raw_animal(fed_today=True, yield_units=3)},
        seeds={"WHEAT": 5},
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.HARVEST and plan.objective.targets == (P(1, 1),)


# --- Planting and seed purchase ------------------------------------------------------------------


def test_plants_nearest_the_shed_with_a_watering_deadline(obs_no_hands):
    state = make_state(obs_no_hands, day=1, hour=3, seeds={"WHEAT": 2})
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.PLANT
    assert plan.objective.item == "WHEAT"
    assert plan.objective.deadline_hour == PLANT_DEADLINE_HOUR == 22
    assert len(plan.objective.targets) == BASELINE_MAX_PLANTS
    assert plan.objective.targets[0] == P(4, 4)  # shed access tile itself, distance 0


@pytest.mark.parametrize("hour", [23])
def test_no_planting_without_same_day_watering_capacity(obs_no_hands, hour):
    state = make_state(obs_no_hands, day=1, hour=hour, seeds={"WHEAT": 2})
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.PASS
    assert plan.market == ()


def test_planting_allowed_at_the_deadline_hour(obs_no_hands):
    state = make_state(obs_no_hands, day=1, hour=PLANT_DEADLINE_HOUR, seeds={"WHEAT": 2})
    assert plan_for(state).objective.kind is ObjectiveKind.PLANT


def test_seed_purchase_fills_open_slots_within_the_cash_floor(obs_no_hands):
    state = make_state(obs_no_hands, day=0, hour=0, money=3000)
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.REPOSITION  # seeds arrive after this turn
    assert plan.market == (MarketOrder(MarketOp.BUY_SEED, "WHEAT", BASELINE_MAX_PLANTS),)
    tight = make_state(obs_no_hands, day=0, hour=0, money=EARLY_MIN_CASH_RESERVE + 25)
    assert plan_for(tight).market == (MarketOrder(MarketOp.BUY_SEED, "WHEAT", 2),)
    broke = make_state(obs_no_hands, day=0, hour=0, money=EARLY_MIN_CASH_RESERVE + 5)
    assert plan_for(broke).market == () and plan_for(broke).objective.kind is ObjectiveKind.PASS


def test_seed_purchase_never_stockpiles_beyond_open_slots(obs_no_hands):
    tiles = {
        (x, y): raw_plant(planted_day=0, watered_today=True, consecutive_unwatered=0)
        for x, y in [(4, 4), (3, 4), (4, 3), (3, 3)]
    }
    state = make_state(obs_no_hands, day=1, hour=5, tiles=tiles, seeds={"WHEAT": 1})
    plan = plan_for(state)
    open_slots = BASELINE_MAX_PLANTS - 4
    assert plan.market == (MarketOrder(MarketOp.BUY_SEED, "WHEAT", open_slots - 1),)
    assert len(plan.objective.targets) == open_slots


def test_no_planting_beyond_max_concurrent_plants(obs_no_hands):
    tiles = {
        (x, y): raw_plant(planted_day=0, watered_today=True, consecutive_unwatered=0)
        for x in range(2, 5)
        for y in range(3, 5)
    }
    assert len(tiles) == BASELINE_MAX_PLANTS
    state = make_state(obs_no_hands, day=1, hour=5, tiles=tiles, seeds={"WHEAT": 3})
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.PASS and plan.market == ()


def test_no_planting_on_weeds_structures_or_locked_land(obs_no_hands):
    tiles = {(x, y): {"kind": "WEED"} for x in range(5) for y in range(5)}
    tiles[(1, 1)] = {"kind": "COOP"}
    state = make_state(obs_no_hands, day=1, hour=5, tiles=tiles, seeds={"WHEAT": 3})
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.PASS
    assert plan.market == ()


@pytest.mark.parametrize(
    "day,expected", [(24, ObjectiveKind.PLANT), (25, ObjectiveKind.PASS), (28, ObjectiveKind.PASS)]
)
def test_terminal_horizon_guard_stops_planting_and_seed_buying(obs_no_hands, day, expected):
    state = make_state(obs_no_hands, day=day, hour=2, seeds={"WHEAT": 2})
    plan = plan_for(state)
    assert plan.objective.kind is expected
    no_seed = make_state(obs_no_hands, day=day, hour=2)
    buys = [o for o in plan_for(no_seed).market if o.op is MarketOp.BUY_SEED]
    assert bool(buys) == (expected is ObjectiveKind.PLANT)


# --- Selling and delivery -------------------------------------------------------------------------


def test_excess_shed_wheat_is_sold_keeping_the_feed_reserve(obs_no_hands):
    state = make_state(
        obs_no_hands, day=6, hour=0, shed={"WHEAT": 10}, tiles={(1, 1): raw_animal(fed_today=True)}
    )
    sells = [o for o in plan_for(state).market if o.op is MarketOp.SELL]
    assert sells == [MarketOrder(MarketOp.SELL, "WHEAT", 10 - FEED_WHEAT_RESERVE_PER_ANIMAL)]
    no_animals = make_state(obs_no_hands, day=6, hour=0, shed={"WHEAT": 10})
    assert [o for o in plan_for(no_animals).market if o.op is MarketOp.SELL] == [
        MarketOrder(MarketOp.SELL, "WHEAT", 10)
    ]


def test_final_day_delivers_carried_produce_and_sells_it_in_the_same_turn(obs_no_hands):
    state = make_state(obs_no_hands, day=29, hour=3, inventory={"WHEAT": 7}, shed={"WHEAT": 2})
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.DELIVER and plan.objective.targets == (P(4, 4),)
    assert MarketOrder(MarketOp.SELL, "WHEAT", 9) in plan.market  # drop precedes market processing
    away = make_state(
        obs_no_hands, day=29, hour=3, farmer=(2, 2), inventory={"WHEAT": 7}, shed={"WHEAT": 2}
    )
    assert [o for o in plan_for(away).market if o.op is MarketOp.SELL] == [
        MarketOrder(MarketOp.SELL, "WHEAT", 2)
    ]


def test_idle_farmer_delivers_carried_produce(obs_no_hands):
    tiles = {
        (x, y): raw_plant(planted_day=0, watered_today=True, consecutive_unwatered=0)
        for x in range(2, 5)
        for y in range(3, 5)
    }
    state = make_state(
        obs_no_hands, day=2, hour=5, tiles=tiles, farmer=(2, 3), inventory={"WHEAT": 4}
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.DELIVER


# --- Scope guards: nothing beyond the Milestone 2 baseline ---------------------------------------


def test_only_baseline_market_ops_and_no_expansion_purchases(
    obs_no_hands, obs_midgame_p1, obs_final
):
    allowed = {
        (MarketOp.BUY_SEED, "WHEAT"),
        (MarketOp.SELL, "WHEAT"),
        (MarketOp.BUY_PRODUCT, "WHEAT"),
    }
    for base in (obs_no_hands, obs_midgame_p1, obs_final):
        for day in (0, 5, 12, 26, 29):
            for hour in (0, 12, 23):
                state = make_state(base, day=day, hour=hour, money=9000)
                plan = plan_for(state)
                for order in plan.market:
                    assert (order.op, order.item) in allowed, order
                    assert order.op not in (MarketOp.BUY_LAND, MarketOp.BUY_ANIMAL, MarketOp.HIRE)
                    assert order.item != "FERTILIZER"
                assert plan.objective.kind in set(ObjectiveKind)
                assert plan.objective.item in (None, "WHEAT")


# --- Official-environment scenarios (Milestone 2 baseline loop end to end) ---------------------

PASS_ACTION = {"farmer": ["PASS"], "hands": [], "market": []}


def _env(seed=5):
    from kaggle_environments import make

    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": seed}, debug=True)
    env.reset()
    return env


def _drive(env, action_for_p0):
    """Step player 0 with ``action_for_p0(obs)`` against PASS; return player 0's new obs."""
    obs = env.state[0].observation
    env.step([action_for_p0(obs), PASS_ACTION])
    return env.state[0].observation


def _our_tiles(obs):
    return obs["farms"][0]["tiles"]


def test_scenario_production_cycle_realizes_banked_proceeds_and_stays_in_scope():
    import main

    env = _env()
    actions = []
    while not env.done:
        obs = env.state[0].observation
        action = main.agent(obs)
        actions.append((obs["day"], obs["hour"], action))
        env.step([action, PASS_ACTION])
    farmer_ops = {a["farmer"][0] for _, _, a in actions}
    market_ops = {(o[0], o[1] if len(o) > 1 else None) for _, _, a in actions for o in a["market"]}
    assert {"PLANT", "WATER", "HARVEST"} <= farmer_ops
    assert ("BUY_SEED", "WHEAT") in market_ops and ("SELL", "WHEAT") in market_ops
    # Nothing outside the baseline scope was ever attempted.
    assert farmer_ops <= {
        "PASS",
        "NORTH",
        "SOUTH",
        "EAST",
        "WEST",
        "PLANT",
        "WATER",
        "HARVEST",
        "DROP",
        "PICKUP",
    }
    assert market_ops <= {("BUY_SEED", "WHEAT"), ("SELL", "WHEAT"), ("BUY_PRODUCT", "WHEAT")}
    assert all(a["farmer"][1] == "WHEAT" for _, _, a in actions if a["farmer"][0] == "PLANT")
    # Late viability: no planting or seed purchase once wheat cannot mature before the end.
    late = [(d, a) for d, _, a in actions if d >= 25]
    assert late and not [
        a
        for _, a in late
        if a["farmer"][0] == "PLANT" or a["market"] and a["market"][0][0] == "BUY_SEED"
    ]
    final = env.steps[-1][0]
    assert final["status"] == "DONE" and final["reward"] > 3000
    assert env.steps[-1][1]["reward"] == 3000


def test_scenario_planting_safety_at_the_last_waterable_hour():
    import main

    env = _env()
    _drive(env, lambda o: {"farmer": ["PASS"], "hands": [], "market": [["BUY_SEED", "WHEAT", 1]]})
    while env.state[0].observation["hour"] < 22:
        _drive(env, lambda o: PASS_ACTION)
    obs = _drive(env, main.agent)  # hour 22: plant is still waterable at hour 23
    tile = _our_tiles(obs)[4][4]
    assert tile["kind"] == "PLANT" and tile["watered_today"] is False and obs["hour"] == 23
    obs = _drive(env, main.agent)  # hour 23: it must water the fresh planting
    assert obs["day"] == 1 and _our_tiles(obs)[4][4]["kind"] == "PLANT"
    assert _our_tiles(obs)[4][4]["consecutive_unwatered"] == 0


def test_scenario_no_planting_when_it_cannot_be_watered_today():
    import main

    env = _env()
    _drive(env, lambda o: {"farmer": ["PASS"], "hands": [], "market": [["BUY_SEED", "WHEAT", 1]]})
    while env.state[0].observation["hour"] < 23:
        _drive(env, lambda o: PASS_ACTION)
    obs = env.state[0].observation
    assert obs["hour"] == 23 and obs["private"]["seeds"]["WHEAT"] == 1
    action = main.agent(obs)
    assert action["farmer"][0] != "PLANT"
    obs = _drive(env, lambda o: action)
    assert obs["day"] == 1 and _our_tiles(obs)[4][4] is None


def test_scenario_water_emergency_is_handled_before_new_planting():
    import main

    env = _env()
    _drive(env, lambda o: {"farmer": ["PASS"], "hands": [], "market": [["BUY_SEED", "WHEAT", 1]]})
    _drive(env, lambda o: {"farmer": ["WEST"], "hands": [], "market": []})
    _drive(env, lambda o: {"farmer": ["PLANT", "WHEAT"], "hands": [], "market": []})
    _drive(env, lambda o: {"farmer": ["WATER"], "hands": [], "market": []})
    while env.state[0].observation["day"] < 2:  # day 1 entirely unwatered -> at risk on day 2
        _drive(env, lambda o: PASS_ACTION)
    obs = env.state[0].observation
    plant = _our_tiles(obs)[4][3]
    assert plant["consecutive_unwatered"] == 1 and obs["farms"][0]["farmer"] == [4, 4]
    first = main.agent(obs)
    assert first["farmer"] == ["WEST"]  # heads to the at-risk plant, not planting on (4,4)
    obs = _drive(env, lambda o: first)
    second = main.agent(obs)
    assert second["farmer"] == ["WATER"]
    obs = _drive(env, lambda o: second)
    assert _our_tiles(obs)[4][3]["watered_today"] is True
    while env.state[0].observation["day"] < 3:
        _drive(env, main.agent)
    assert _our_tiles(env.state[0].observation)[4][3]["kind"] == "PLANT"  # saved


def test_scenario_feed_emergency_is_handled_before_crop_expansion():
    import main

    env = _env()
    _drive(env, lambda o: {"farmer": ["PASS"], "hands": [], "market": [["BUY_ANIMAL", "GOOSE", 1]]})
    _drive(env, lambda o: {"farmer": ["PICKUP", "GOOSE", 1], "hands": [], "market": []})
    _drive(env, lambda o: {"farmer": ["NORTH"], "hands": [], "market": []})
    _drive(env, lambda o: {"farmer": ["NORTH"], "hands": [], "market": []})
    _drive(env, lambda o: {"farmer": ["BUILD_COOP"], "hands": [], "market": []})
    _drive(env, lambda o: {"farmer": ["PLACE", "GOOSE"], "hands": [], "market": []})
    while env.state[0].observation["day"] < 1:  # unfed all of day 0 -> at risk on day 1
        _drive(env, lambda o: PASS_ACTION)
    obs = env.state[0].observation
    goose = _our_tiles(obs)[2][4]
    assert goose["animal"] == "GOOSE" and goose["consecutive_unfed"] == 1
    assert obs["private"]["shed"]["WHEAT"] == 0 and obs["private"]["inventories"][0] == {}
    log = []
    for _ in range(12):
        obs = env.state[0].observation
        action = main.agent(obs)
        log.append(action)
        env.step([action, PASS_ACTION])
        if _our_tiles(env.state[0].observation)[2][4]["fed_today"]:
            break
    assert _our_tiles(env.state[0].observation)[2][4]["fed_today"] is True
    assert any(o[:2] == ["BUY_PRODUCT", "WHEAT"] for a in log for o in a["market"])
    assert ["PICKUP", "WHEAT", 1] in [a["farmer"] for a in log]
    assert not [a for a in log if a["farmer"][0] == "PLANT"]  # feeding came before expansion
    while env.state[0].observation["day"] < 2:
        _drive(env, main.agent)
    assert _our_tiles(env.state[0].observation)[2][4].get("animal") == "GOOSE"  # no escape


# --- Frozen baseline snapshot (agents/baseline.py) ------------------------------------------------


def test_baseline_snapshot_is_insulated_from_mutable_strategy_layers():
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "agents" / "baseline.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules, imported_names = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
            imported_names[node.module] = {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            imported_modules.update(a.name for a in node.names)
    mutable = {
        "kaggriculture_bot.strategy",
        "kaggriculture_bot.tasks",
        "kaggriculture_bot.features",
    }
    assert not imported_modules & mutable, imported_modules
    assert "main" not in imported_modules
    # Only official game constants may be shared; policy parameters are embedded copies.
    assert imported_names.get("kaggriculture_bot.constants", set()) <= {
        "CROPS",
        "LAST_DAY",
        "TURNS_PER_DAY",
        "WHEAT",
    }
    assert not [m for m in imported_modules if m.split(".")[0] in ("tools", "tests", "benchmarks")]


def test_baseline_snapshot_matches_milestone_2_tilla_action_for_action():
    import main
    from agents import baseline

    env = _env(seed=1000)
    recorded = []
    while not env.done:
        obs = env.state[0].observation
        action = main.agent(obs)
        recorded.append((obs, action))
        env.step([action, PASS_ACTION])
    assert len(recorded) == 719
    mismatches = [i for i, (obs, action) in enumerate(recorded) if baseline.agent(obs) != action]
    assert mismatches == []
    assert env.steps[-1][0]["reward"] > 3000


def test_baseline_snapshot_plays_a_full_game_and_beats_pass():
    from agents import baseline

    env = _env(seed=1001)
    env.run([baseline.agent, "pass"])
    assert len(env.steps) == 720 and env.done
    assert env.steps[-1][0]["reward"] > env.steps[-1][1]["reward"] == 3000
