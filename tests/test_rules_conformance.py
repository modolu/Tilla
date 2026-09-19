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
