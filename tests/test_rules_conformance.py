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
