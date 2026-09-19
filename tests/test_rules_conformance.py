"""Rule-conformance tests against the installed official Kaggriculture environment.

These tests protect ``TILLA_RULES.md`` against upstream drift by measuring the
installed ``kaggle-environments`` behaviour directly rather than trusting docs.
"""

import pytest

from kaggriculture_bot.actions import build_pass_action

pytest.importorskip("kaggle_environments")

from kaggle_environments import make  # noqa: E402

# Products consumed by shops (kaggriculture.py SHOPS). MELON appears in no shop,
# so in a PASS-vs-PASS episode its market inventory changes only via the town
# center; FERTILIZER is excluded from town-center consumption entirely.
TOWN_CENTER_ONLY_PRODUCT = "MELON"
NEVER_CONSUMED_PRODUCT = "FERTILIZER"

# What TILLA_RULES.md §19 states for the town center.
RULES_TOWN_CENTER_INTERVAL = 12
RULES_TOWN_CENTER_SCHEDULE = [(20, 4), (10, 2), (0, 1)]  # (from_day, units per product)


def _expected_multiplier(day: int) -> int:
    return next(m for threshold, m in RULES_TOWN_CENTER_SCHEDULE if day >= threshold)


def _pass_agent(obs):
    return build_pass_action(0)


@pytest.fixture(scope="module")
def pass_episode():
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": 11}, debug=True)
    env.run([_pass_agent, _pass_agent])
    assert len(env.steps) == 720
    return env


def _inventory_series(env, product):
    return [step[0]["observation"]["market"]["inventory"][product] for step in env.steps]


def test_default_town_center_interval_configuration(pass_episode):
    cfg = pass_episode.configuration
    assert cfg["townCenterSellInterval"] == RULES_TOWN_CENTER_INTERVAL
    assert cfg["turnsPerDay"] == 24


def test_town_center_consumes_every_12_turns_with_day_scaled_quantity(pass_episode):
    turns_per_day = pass_episode.configuration["turnsPerDay"]
    series = _inventory_series(pass_episode, TOWN_CENTER_ONLY_PRODUCT)
    # env.steps[s + 1] holds the state after the interpreter processed turn s.
    observed = {s: series[s] - series[s + 1] for s in range(len(series) - 1)}
    expected = {
        s: (_expected_multiplier(s // turns_per_day) if s % RULES_TOWN_CENTER_INTERVAL == 0 else 0)
        for s in observed
    }
    assert observed == expected

    consumed_per_day = {}
    for s, units in observed.items():
        consumed_per_day[s // turns_per_day] = consumed_per_day.get(s // turns_per_day, 0) + units
    assert consumed_per_day[0] == 2  # days 0-9: 1 unit x 2 ticks/day
    assert consumed_per_day[9] == 2
    assert consumed_per_day[10] == 4  # days 10-19: 2 units x 2 ticks/day
    assert consumed_per_day[19] == 4
    assert consumed_per_day[20] == 8  # days 20+: 4 units x 2 ticks/day
    assert consumed_per_day[29] == 8


def test_town_center_never_consumes_fertilizer(pass_episode):
    series = _inventory_series(pass_episode, NEVER_CONSUMED_PRODUCT)
    assert len(set(series)) == 1


# --- Town shops (TILLA_RULES.md §19) ---------------------------------------------------

RULES_SHOP_NAMES = {
    "BAKERY",
    "PIZZA_SHOP",
    "BRUNCH_SPOT",
    "YARN_STORE",
    "ICE_CREAM_SHOP",
    "PET_CAFE",
    "SMOOTHIE_SHOP",
    "FARMERS_MARKET",
}
RULES_SHOP_UNLOCK_INTERVAL_DAYS = 3


def _shops_at_step(env, step):
    return list(env.steps[step][0]["observation"]["town"]["unlocked_shops"])


def test_shops_unlock_every_3_days_without_replacement(pass_episode):
    assert pass_episode.configuration["townShopUnlockInterval"] == RULES_SHOP_UNLOCK_INTERVAL_DAYS
    turns_per_day = pass_episode.configuration["turnsPerDay"]
    for day in range(30):
        shops = _shops_at_step(pass_episode, day * turns_per_day)
        expected_count = min(len(RULES_SHOP_NAMES), day // RULES_SHOP_UNLOCK_INTERVAL_DAYS)
        assert len(shops) == expected_count, (day, shops)
        assert len(set(shops)) == len(shops), (day, shops)  # never a duplicate instance
        assert set(shops) <= RULES_SHOP_NAMES
    # Once every shop is unlocked the list stays complete and unchanged.
    assert set(_shops_at_step(pass_episode, 719)) == RULES_SHOP_NAMES


def test_shop_unlock_order_is_seed_deterministic():
    runs = []
    for _ in range(2):
        env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": 11}, debug=True)
        env.run([_pass_agent, _pass_agent])
        runs.append(_shops_at_step(env, 719))
    assert runs[0] == runs[1]


# --- Constants copied into constants.py (TILLA_RULES.md §1, §6) ---------------------------


def test_player_count_matches_official_specification(pass_episode):
    from kaggriculture_bot.constants import PLAYER_COUNT

    assert pass_episode.specification["agents"] == [PLAYER_COUNT]
    assert len(pass_episode.steps[0]) == PLAYER_COUNT
    assert len(pass_episode.steps[0][0]["observation"]["farms"]) == PLAYER_COUNT


def test_market_order_cap_matches_default_and_is_enforced():
    from kaggriculture_bot.constants import MAX_MARKET_ORDERS_PER_TURN

    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": 11}, debug=True)
    assert env.configuration["maxMarketOrdersPerTurn"] == MAX_MARKET_ORDERS_PER_TURN
    env.reset()
    orders = [["BUY_SEED", "WHEAT", 1]] * (MAX_MARKET_ORDERS_PER_TURN + 2)
    env.step([{"farmer": ["PASS"], "hands": [], "market": orders}, build_pass_action(0)])
    private = env.state[0].observation["private"]
    farm = env.state[0].observation["farms"][0]
    assert private["seeds"]["WHEAT"] == MAX_MARKET_ORDERS_PER_TURN  # extras silently dropped
    assert farm["money"] == 3000 - 10 * MAX_MARKET_ORDERS_PER_TURN


def test_board_and_day_hour_relationship(pass_episode):
    cfg = pass_episode.configuration
    assert cfg["boardSize"] == 10 and cfg["turnsPerDay"] == 24 and cfg["episodeSteps"] == 720
    for step in (0, 1, 23, 24, 25, 240, 479, 480, 718):
        obs = pass_episode.steps[step][0]["observation"]
        assert obs["step"] == step
        assert obs["day"] == step // 24 and obs["hour"] == step % 24
        for farm in obs["farms"]:
            assert len(farm["tiles"]) == 10 and all(len(row) == 10 for row in farm["tiles"])
    assert pass_episode.steps[719][0]["observation"]["day"] == 29


def test_hand_observation_shape_after_hire():
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": 11}, debug=True)
    env.reset()
    env.step(
        [{"farmer": ["PASS"], "hands": [], "market": [["HIRE"], ["HIRE"]]}, build_pass_action(0)]
    )
    farm = env.state[0].observation["farms"][0]
    private = env.state[0].observation["private"]
    assert len(farm["hands"]) == 2 and farm["hires_today"] == 2
    assert all(len(pos) == 2 for pos in farm["hands"])
    assert len(private["inventories"]) == 1 + len(farm["hands"])  # farmer + one per hand
    assert farm["money"] == 3000 - 1 - 1  # Fibonacci hire costs 1, 1
    # Opponent's private state is never present in our observation.
    assert "inventories" not in env.state[0].observation["farms"][1]
    assert "shed" not in env.state[0].observation["farms"][1]


# --- Movement and locked land (TILLA_RULES.md §3, §5) --------------------------------------

PASS_ACTION = {"farmer": ["PASS"], "hands": [], "market": []}


def _fresh_env(seed=3):
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": seed}, debug=True)
    env.reset()
    return env


def _p0(env, action):
    env.step([action, PASS_ACTION])
    return env.state[0].observation


def _farmer(env, market=None, farmer=None, hands=None):
    return {"farmer": farmer or ["PASS"], "hands": hands or [], "market": market or []}


def test_move_into_locked_tile_is_a_noop_and_edges_are_noops():
    env = _fresh_env()
    farm = env.state[0].observation["farms"][0]
    assert farm["farmer"] == [4, 4] and farm["tiles"][4][5] == "LOCKED"
    obs = _p0(env, _farmer(env, farmer=["EAST"]))  # (5,4) is LOCKED (NE quadrant)
    assert obs["farms"][0]["farmer"] == [4, 4]
    obs = _p0(env, _farmer(env, farmer=["SOUTH"]))  # (4,5) is LOCKED (SW quadrant)
    assert obs["farms"][0]["farmer"] == [4, 4]
    for _ in range(4):
        obs = _p0(env, _farmer(env, farmer=["NORTH"]))
    assert obs["farms"][0]["farmer"] == [4, 0]
    obs = _p0(env, _farmer(env, farmer=["NORTH"]))  # off-board: no-op
    assert obs["farms"][0]["farmer"] == [4, 0]
    for _ in range(4):
        obs = _p0(env, _farmer(env, farmer=["WEST"]))
    assert obs["farms"][0]["farmer"] == [0, 0]
    obs = _p0(env, _farmer(env, farmer=["WEST"]))
    assert obs["farms"][0]["farmer"] == [0, 0]


def test_hand_spawns_on_locked_access_tile_and_can_leave_but_not_reenter():
    env = _fresh_env()
    obs = _p0(env, _farmer(env, market=[["HIRE"]]))
    assert obs["farms"][0]["hands"] == [[5, 4]]  # NE shed-access tile, still LOCKED
    assert obs["farms"][0]["tiles"][4][5] == "LOCKED"
    obs = _p0(env, _farmer(env, hands=[["EAST"]]))  # deeper into locked land: no-op
    assert obs["farms"][0]["hands"] == [[5, 4]]
    obs = _p0(env, _farmer(env, hands=[["WEST"]]))  # leaving a locked tile works
    assert obs["farms"][0]["hands"] == [[4, 4]]
    obs = _p0(env, _farmer(env, hands=[["EAST"]]))  # cannot re-enter
    assert obs["farms"][0]["hands"] == [[4, 4]]


def test_shed_actions_are_noops_from_a_locked_access_tile():
    env = _fresh_env()
    obs = _p0(env, _farmer(env, market=[["BUY_PRODUCT", "WHEAT", 3], ["HIRE"]]))
    assert obs["private"]["shed"]["WHEAT"] == 3 and obs["farms"][0]["hands"] == [[5, 4]]
    obs = _p0(env, _farmer(env, farmer=["PICKUP", "WHEAT", 1], hands=[["PICKUP", "WHEAT", 1]]))
    assert obs["private"]["shed"]["WHEAT"] == 2  # only the farmer on unlocked (4,4) succeeded
    assert obs["private"]["inventories"] == [{"WHEAT": 1}, {}]


# --- Wheat lifecycle, feed, and sale semantics used by the baseline (§8-§10, §13, §16-§17) --


def test_wheat_seed_purchase_planting_watering_harvest_and_sale_semantics():
    from kaggriculture_bot.constants import CROPS, LAST_DAY, TURNS_PER_DAY

    assert LAST_DAY == 29 and TURNS_PER_DAY == 24
    wheat = CROPS["WHEAT"]
    env = _fresh_env()
    # Fixed seed price, seeds land in private.seeds (not the shed) after the market phase.
    obs = _p0(env, _farmer(env, market=[["BUY_SEED", "WHEAT", 2]]))
    assert obs["private"]["seeds"]["WHEAT"] == 2
    assert obs["farms"][0]["money"] == 3000 - 2 * wheat.seed
    assert obs["private"]["shed"]["WHEAT"] == 0
    # PLANT acts on the farmer's own tile, consumes one seed, and starts unwatered (=1).
    obs = _p0(env, _farmer(env, farmer=["PLANT", "WHEAT"]))
    tile = obs["farms"][0]["tiles"][4][4]
    assert tile["kind"] == "PLANT" and tile["crop"] == "WHEAT" and tile["planted_day"] == 0
    assert tile["consecutive_unwatered"] == 1 and tile["watered_today"] is False
    assert tile["yield_units"] == 1 and obs["private"]["seeds"]["WHEAT"] == 1
    assert tile["max_lifespan_step"] == (0 + wheat.max_yield_day + 1) * TURNS_PER_DAY
    # WATER on the same tile marks it watered; a second WATER is a no-op.
    obs = _p0(env, _farmer(env, farmer=["WATER"]))
    assert obs["farms"][0]["tiles"][4][4]["watered_today"] is True
    # HARVEST before first_yield_day is a no-op even with yield_units > 0.
    obs = _p0(env, _farmer(env, farmer=["HARVEST"]))
    assert obs["farms"][0]["tiles"][4][4]["kind"] == "PLANT"
    assert obs["private"]["inventories"][0] == {}
    # Advance to the start of day 1 (steps 3..23 pass); watered plant survives and resets.
    while env.state[0].observation["day"] == 0:
        obs = _p0(env, PASS_ACTION)
    tile = obs["farms"][0]["tiles"][4][4]
    assert obs["day"] == 1 and tile["consecutive_unwatered"] == 0 and tile["watered_today"] is False
    # Water once per day through max_yield_day; bonus window starts at ceil(max/2).
    yields = {}
    for day in range(1, wheat.max_yield_day + 1):
        obs = _p0(env, _farmer(env, farmer=["WATER"]))
        yields[day] = obs["farms"][0]["tiles"][4][4]["yield_units"]
        while env.state[0].observation["day"] == day:
            obs = _p0(env, PASS_ACTION)
    assert yields == {1: 1, 2: 2, 3: 3, 4: 4}  # +1 per watered day in the bonus window
    # Harvest after max_yield_day: units go to the farmer's carried inventory, tile empties.
    assert obs["day"] == wheat.max_yield_day + 1
    obs = _p0(env, _farmer(env, farmer=["HARVEST"]))
    assert obs["farms"][0]["tiles"][4][4] is None
    assert obs["private"]["inventories"][0] == {"WHEAT": 4}
    # SELL only sells from the shed: carried wheat is not sold.
    money_before = obs["farms"][0]["money"]
    obs = _p0(env, _farmer(env, market=[["SELL", "WHEAT", 4]]))
    assert obs["farms"][0]["money"] == money_before
    assert obs["private"]["inventories"][0] == {"WHEAT": 4}
    # DROP (shed-adjacent) then SELL in the same turn works: unit actions precede the market.
    obs = _p0(env, _farmer(env, farmer=["DROP"], market=[["SELL", "WHEAT", 4]]))
    assert obs["private"]["inventories"][0] == {} and obs["private"]["shed"]["WHEAT"] == 0
    assert obs["farms"][0]["money"] > money_before


def test_unwatered_new_planting_dies_at_first_refresh_and_second_miss_kills_older_plant():
    env = _fresh_env()
    _p0(env, _farmer(env, market=[["BUY_SEED", "WHEAT", 2]]))
    _p0(env, _farmer(env, farmer=["PLANT", "WHEAT"]))  # step 1 on (4,4), never watered
    _p0(env, _farmer(env, farmer=["WEST"]))
    _p0(env, _farmer(env, farmer=["PLANT", "WHEAT"]))  # step 3 on (3,4)
    obs = _p0(env, _farmer(env, farmer=["WATER"]))  # (3,4) watered on planting day
    while env.state[0].observation["day"] == 0:
        obs = _p0(env, PASS_ACTION)
    assert obs["farms"][0]["tiles"][4][4] == {"kind": "WEED"}  # planting day counted as miss #1
    survivor = obs["farms"][0]["tiles"][4][3]
    assert survivor["kind"] == "PLANT" and survivor["consecutive_unwatered"] == 0
    # Miss day 1 -> consecutive_unwatered == 1 (at risk); miss day 2 -> weed.
    while env.state[0].observation["day"] == 1:
        obs = _p0(env, PASS_ACTION)
    assert obs["farms"][0]["tiles"][4][3]["consecutive_unwatered"] == 1
    while env.state[0].observation["day"] == 2:
        obs = _p0(env, PASS_ACTION)
    assert obs["farms"][0]["tiles"][4][3] == {"kind": "WEED"}


def test_feed_requires_carried_wheat_and_two_missed_feeds_cause_escape():
    env = _fresh_env()
    _p0(env, _farmer(env, market=[["BUY_ANIMAL", "GOOSE", 1], ["BUY_PRODUCT", "WHEAT", 2]]))
    _p0(env, _farmer(env, farmer=["PICKUP", "GOOSE", 1]))
    _p0(env, _farmer(env, farmer=["NORTH"]))  # (4,3)
    _p0(env, _farmer(env, farmer=["BUILD_COOP"]))
    obs = _p0(env, _farmer(env, farmer=["PLACE", "GOOSE"]))
    goose = obs["farms"][0]["tiles"][3][4]
    assert goose["animal"] == "GOOSE" and goose["consecutive_unfed"] == 0
    # FEED without carried wheat is a no-op (wheat is in the shed, not carried).
    obs = _p0(env, _farmer(env, farmer=["FEED"]))
    assert obs["farms"][0]["tiles"][3][4]["fed_today"] is False
    while env.state[0].observation["day"] == 0:
        obs = _p0(env, PASS_ACTION)
    goose = obs["farms"][0]["tiles"][3][4]
    assert goose["consecutive_unfed"] == 1 and "animal" in goose  # one miss: at risk, still here
    # Fetch wheat (farmer respawned on (4,4), shed-adjacent), walk back and FEED.
    _p0(env, _farmer(env, farmer=["PICKUP", "WHEAT", 1]))
    _p0(env, _farmer(env, farmer=["NORTH"]))
    obs = _p0(env, _farmer(env, farmer=["FEED"]))
    assert obs["farms"][0]["tiles"][3][4]["fed_today"] is True
    assert obs["private"]["inventories"][0] == {}
    while env.state[0].observation["day"] == 1:
        obs = _p0(env, PASS_ACTION)
    assert obs["farms"][0]["tiles"][3][4]["consecutive_unfed"] == 0  # fed: counter reset
    # Two consecutive missed days -> the animal escapes and the empty coop remains.
    while env.state[0].observation["day"] in (2, 3):
        obs = _p0(env, PASS_ACTION)
    assert obs["farms"][0]["tiles"][3][4] == {"kind": "COOP"}


def test_crop_constants_match_installed_environment():
    from kaggle_environments.envs.kaggriculture.kaggriculture import CROPS as OFFICIAL_CROPS

    from kaggriculture_bot.constants import CROPS

    assert set(CROPS) == set(OFFICIAL_CROPS)
    for name, official in OFFICIAL_CROPS.items():
        ours = CROPS[name]
        assert ours.seed == official["seed"]
        assert ours.first_yield_day == official["first_yield_day"]
        assert ours.max_yield_day == official["max_yield_day"]
        assert ours.interval == official["interval"]
        assert ours.max_yield == official["max_yield"]
        assert ours.ongoing == official["ongoing"]


# --- Milestone 3: constants and mechanics the economic model interprets ------------------------


def test_animal_land_shed_and_product_constants_match_installed_environment():
    from kaggle_environments.envs.kaggriculture import kaggriculture as official

    from kaggriculture_bot.constants import ANIMALS, LAND_PRICES, PRODUCTS, SHED_CAPACITY

    assert set(ANIMALS) == set(official.ANIMALS)
    for name, spec in ANIMALS.items():
        theirs = official.ANIMALS[name]
        assert (spec.cost, spec.structure, spec.first_yield_day) == (
            theirs["cost"],
            theirs["structure"],
            theirs["first_yield_day"],
        )
        assert (spec.interval, spec.max_held, spec.product) == (
            theirs["interval"],
            theirs["max_held"],
            theirs["product"],
        )
    assert list(LAND_PRICES) == official.LAND_PRICES
    assert list(PRODUCTS) == official.PRODUCTS
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": 11}, debug=True)
    assert env.configuration["shedCapacity"] == SHED_CAPACITY


def test_fertilizer_lasts_three_days_and_doubles_the_watering_bonus():
    env = _fresh_env()
    _p0(env, _farmer(env, market=[["BUY_SEED", "WHEAT", 1], ["BUY_PRODUCT", "FERTILIZER", 1]]))
    obs = env.state[0].observation
    assert obs["private"]["shed"]["FERTILIZER"] == 1  # bought fertilizer lands in the shed
    _p0(env, _farmer(env, farmer=["PLANT", "WHEAT"]))
    obs = _p0(env, _farmer(env, farmer=["WATER"]))
    while env.state[0].observation["day"] < 2:  # reach the start of the bonus window (age 2)
        obs = env.state[0].observation
        obs = _p0(env, _farmer(env, farmer=["WATER"]) if obs["hour"] == 0 else PASS_ACTION)
    assert obs["day"] == 2 and obs["private"]["inventories"][0] == {}  # carried items auto-drop
    _p0(env, _farmer(env, farmer=["PICKUP", "FERTILIZER", 1]))
    obs = _p0(env, _farmer(env, farmer=["FERTILIZE"]))
    tile = obs["farms"][0]["tiles"][4][4]
    assert tile["fertilized_until_day"] == 2 + 2 and obs["private"]["inventories"][0] == {}
    obs = _p0(env, _farmer(env, farmer=["WATER"]))  # age 2, fertilized + watered: +2 instead of +1
    assert obs["farms"][0]["tiles"][4][4]["yield_units"] == 3
    obs = _p0(env, _farmer(env, farmer=["FERTILIZE"]))  # no fertilizer carried: no-op
    assert obs["farms"][0]["tiles"][4][4]["fertilized_until_day"] == 4


def test_animal_purchase_first_production_and_max_held_cap():
    from kaggriculture_bot.constants import ANIMALS

    goose = ANIMALS["GOOSE"]
    env = _fresh_env()
    obs = _p0(env, _farmer(env, market=[["BUY_ANIMAL", "GOOSE", 1], ["BUY_PRODUCT", "WHEAT", 12]]))
    assert obs["private"]["shed"]["GOOSE"] == 1  # a bought animal lands in the shed
    assert obs["private"]["shed"]["WHEAT"] == 12
    assert 3000 - goose.cost - 12 * 30 < obs["farms"][0]["money"] < 3000 - goose.cost - 12 * 25
    _p0(env, _farmer(env, farmer=["PICKUP", "GOOSE", 1]))
    _p0(env, _farmer(env, farmer=["BUILD_COOP"]))  # on (4,4)
    obs = _p0(env, _farmer(env, farmer=["PLACE", "GOOSE"]))
    assert obs["farms"][0]["tiles"][4][4]["animal"] == "GOOSE"
    # Feed every day from the shed (farmer respawns on (4,4), the coop tile, each day).
    eggs_by_day = {}
    while env.state[0].observation["day"] < goose.first_yield_day + goose.max_held + 2:
        obs = env.state[0].observation
        if obs["hour"] == 0:
            _p0(env, _farmer(env, farmer=["PICKUP", "WHEAT", 1]))
        elif obs["hour"] == 1:
            obs = _p0(env, _farmer(env, farmer=["FEED"]))
            eggs_by_day[obs["day"]] = obs["farms"][0]["tiles"][4][4]["yield_units"]
        else:
            _p0(env, PASS_ACTION)
    assert eggs_by_day[goose.first_yield_day - 1] == 0
    assert eggs_by_day[goose.first_yield_day] == 1  # first egg on placed_day + first_yield_day
    assert (
        eggs_by_day[goose.first_yield_day + goose.max_held] == goose.max_held
    )  # capped, not lost twice
    assert eggs_by_day[goose.first_yield_day + goose.max_held + 1] == goose.max_held


# --- Hiring mechanics used by the Milestone 4 planner (TILLA_RULES.md §5) ------------------


def test_same_turn_hires_escalate_along_fibonacci_and_reset_next_day():
    from kaggriculture_bot.economy import hire_cost

    env = _fresh_env()
    obs = _p0(env, _farmer(env, market=[["HIRE"]] * 5))
    assert obs["farms"][0]["hires_today"] == 5 and len(obs["farms"][0]["hands"]) == 5
    assert obs["farms"][0]["money"] == 3000 - sum(hire_cost(k) for k in range(5))  # 1+1+2+3+5
    assert [hire_cost(k) for k in range(8)] == [1, 1, 2, 3, 5, 8, 13, 21]
    obs = _p0(env, _farmer(env, market=[["HIRE"]]))  # sixth hire of the day costs 8
    assert obs["farms"][0]["money"] == 3000 - 12 - 8 and obs["farms"][0]["hires_today"] == 6
    while env.state[0].observation["hour"] < 23:
        _p0(env, PASS_ACTION)
    obs = _p0(env, PASS_ACTION)  # day refresh
    assert (
        obs["day"] == 1 and obs["farms"][0]["hands"] == [] and obs["farms"][0]["hires_today"] == 0
    )
    obs = _p0(env, _farmer(env, market=[["HIRE"]]))
    assert obs["farms"][0]["money"] == 3000 - 20 - 1  # sequence restarted at 1


def test_hand_acts_from_the_turn_after_its_hire():
    env = _fresh_env()
    obs = _p0(env, _farmer(env, market=[["BUY_PRODUCT", "WHEAT", 2], ["HIRE"]]))
    assert obs["farms"][0]["hands"] == [[5, 4]]  # hired in the market phase: no action yet
    _p0(env, _farmer(env, hands=[["WEST"]]))
    obs = _p0(env, _farmer(env, hands=[["PICKUP", "WHEAT", 1]]))  # from (4,4) next turn
    assert obs["private"]["inventories"][1] == {"WHEAT": 1}


def test_same_turn_spawn_order_and_the_stuck_south_east_access_tile():
    """With the farmer on (4,4), four same-turn hires spawn NE, SW, SE, then NW.
    (5,5) has only locked neighbours while NE and SW are locked: that hand
    cannot move at all for the rest of the day."""
    env = _fresh_env()
    obs = _p0(env, _farmer(env, market=[["HIRE"]] * 4))
    assert obs["farms"][0]["hands"] == [[5, 4], [4, 5], [5, 5], [4, 4]]
    for direction in ("NORTH", "SOUTH", "EAST", "WEST"):
        obs = _p0(env, _farmer(env, hands=[["PASS"], ["PASS"], [direction], ["PASS"]]))
        assert obs["farms"][0]["hands"][2] == [5, 5]
    obs = _p0(env, _farmer(env, hands=[["PASS"], ["NORTH"], ["PASS"], ["PASS"]]))
    assert obs["farms"][0]["hands"][1] == [4, 4]  # (4,5) -> (4,4) is allowed


def test_spawn_prediction_matches_the_environment():
    from kaggriculture_bot.features import can_act_from, hand_spawn_positions
    from kaggriculture_bot.models import Position
    from kaggriculture_bot.parser import parse_observation

    env = _fresh_env()
    state = parse_observation(env.state[0].observation)
    predicted = hand_spawn_positions(state.me, 4)
    obs = _p0(env, _farmer(env, market=[["HIRE"]] * 4))
    assert [[p.x, p.y] for p in predicted] == obs["farms"][0]["hands"]
    assert [can_act_from(state.me, p) for p in predicted] == [True, True, False, True]
    # Farmer away from the shed: the NW tile is free and comes first.
    _p0(env, _farmer(env, farmer=["WEST"], hands=[["PASS"]] * 4))
    state = parse_observation(env.state[0].observation)
    assert hand_spawn_positions(state.me, 1) == (Position(4, 4),)
    obs = _p0(env, _farmer(env, market=[["HIRE"]], hands=[["PASS"]] * 4))
    assert obs["farms"][0]["hands"][4] == [4, 4]
    # Occupancy is taken after the same turn's unit moves (market phase follows unit phase).
    env = _fresh_env()
    obs = _p0(env, _farmer(env, farmer=["WEST"], market=[["HIRE"]]))
    assert obs["farms"][0]["farmer"] == [3, 4] and obs["farms"][0]["hands"] == [[4, 4]]


def test_hour_23_hire_is_paid_and_vanishes_at_the_refresh():
    env = _fresh_env()
    while env.state[0].observation["hour"] < 23:
        _p0(env, PASS_ACTION)
    obs = _p0(env, _farmer(env, market=[["HIRE"]]))
    assert obs["day"] == 1 and obs["farms"][0]["hands"] == []
    assert obs["farms"][0]["money"] == 3000 - 1


def test_hand_inventory_is_dropped_into_the_shed_when_hands_vanish():
    env = _fresh_env()
    _p0(env, _farmer(env, market=[["BUY_PRODUCT", "WHEAT", 2], ["HIRE"]]))
    _p0(env, _farmer(env, hands=[["WEST"]]))
    obs = _p0(env, _farmer(env, hands=[["PICKUP", "WHEAT", 2]]))
    assert obs["private"]["shed"]["WHEAT"] == 0 and obs["private"]["inventories"][1] == {"WHEAT": 2}
    while env.state[0].observation["hour"] < 23:
        _p0(env, _farmer(env, hands=[["NORTH"]]))
    obs = _p0(env, _farmer(env, hands=[["PASS"]]))
    assert obs["day"] == 1 and obs["farms"][0]["hands"] == []
    assert obs["private"]["shed"]["WHEAT"] == 2 and obs["private"]["inventories"] == [{}]
