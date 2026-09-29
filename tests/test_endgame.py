"""Milestone 7 endgame policy: final-day liquidation (strategy._final_day_plan).

Day 29 has no following refresh: feeding, ongoing-crop watering and fertilizer
have no value; everything harvestable must be harvested, dropped and sold by
the last turn. ENDGAME_POLICY off reproduces the Milestone 6 plan exactly.
"""

import pytest

from kaggriculture_bot import strategy
from kaggriculture_bot.constants import LAST_DAY
from kaggriculture_bot.features import (
    final_day_harvest_deadline,
    final_day_water_useful,
    is_final_day,
)
from kaggriculture_bot.models import (
    PRIORITY_DELIVERY,
    PRIORITY_SURVIVAL,
    JobKind,
    MarketOp,
    ObjectiveKind,
    Position,
)
from kaggriculture_bot.opponent import update_model
from kaggriculture_bot.runtime import reset_episode_memory
from kaggriculture_bot.tasks import assign_jobs, generate_jobs
from tests.conftest import OFFICIAL_FIXTURES, load_fixture, make_state, raw_animal, raw_plant

SHED = Position(4, 4)  # the only usable access tile while only NW is unlocked


@pytest.fixture
def base():
    return load_fixture("obs_step0_no_hands.json")


def plan_for(state, endgame=True, monkeypatch=None):
    memory = reset_episode_memory(state.player_id)
    update_model(state, memory)
    if monkeypatch is not None:
        monkeypatch.setattr(strategy, "ENDGAME_POLICY", endgame)
    return strategy.choose_plan(state, memory)


def kinds_at(plan, kind, pos=None):
    return [
        o for o in plan.all_objectives() if o.kind is kind and (pos is None or pos in o.targets)
    ]


def buys(plan, item):
    return [o for o in plan.market if o.op is MarketOp.BUY_PRODUCT and o.item == item]


def hungry_goose(consecutive_unfed=1):
    return raw_animal("GOOSE", 20, fed_today=False, consecutive_unfed=consecutive_unfed)


# --- Maintenance with only post-season benefit ------------------------------------------------


def test_day_29_blocks_the_feed_wheat_purchase(base, monkeypatch):
    kw = dict(hour=3, tiles={(1, 1): hungry_goose()}, shed={"WHEAT": 0}, money=5000)
    final = make_state(base, day=LAST_DAY, **kw)
    assert buys(plan_for(final, True, monkeypatch), "WHEAT") == []
    assert buys(plan_for(final, False, monkeypatch), "WHEAT")  # M6 bought feed on day 29


def test_feed_is_blocked_on_day_29_but_unchanged_on_day_28(base, monkeypatch):
    tiles = {(1, 1): hungry_goose()}
    day28 = make_state(base, day=LAST_DAY - 1, hour=3, tiles=tiles, shed={"WHEAT": 5})
    on, off = plan_for(day28, True, monkeypatch), plan_for(day28, False, monkeypatch)
    assert kinds_at(on, ObjectiveKind.FEED_ANIMAL, Position(1, 1))
    assert on == off
    day29 = make_state(base, day=LAST_DAY, hour=3, tiles=tiles, shed={"WHEAT": 5})
    assert kinds_at(plan_for(day29, True, monkeypatch), ObjectiveKind.FEED_ANIMAL) == []
    assert kinds_at(plan_for(day29, False, monkeypatch), ObjectiveKind.FEED_ANIMAL)


def test_ongoing_crop_watering_is_blocked_on_day_29(base, monkeypatch):
    strawberry = raw_plant("STRAWBERRY", 15, watered_today=False, consecutive_unwatered=0)
    tiles = {(2, 2): strawberry}
    day29 = make_state(base, day=LAST_DAY, hour=2, tiles=tiles)
    assert kinds_at(plan_for(day29, True, monkeypatch), ObjectiveKind.WATER_CROP) == []
    assert kinds_at(plan_for(day29, False, monkeypatch), ObjectiveKind.WATER_CROP)
    day28 = make_state(base, day=LAST_DAY - 1, hour=2, tiles=tiles)
    assert kinds_at(plan_for(day28, True, monkeypatch), ObjectiveKind.WATER_CROP)


def test_no_fertilize_or_new_investment_on_day_29(base, monkeypatch):
    tiles = {(2, 2): raw_plant("MELON", 19, watered_today=True, consecutive_unwatered=0)}
    plan = plan_for(
        make_state(base, day=LAST_DAY, hour=2, tiles=tiles, money=50000), True, monkeypatch
    )
    assert kinds_at(plan, ObjectiveKind.FERTILIZE_CROP) == []
    assert not [o for o in plan.market if o.op in (MarketOp.BUY_SEED, MarketOp.BUY_ANIMAL)]
    assert not [o for o in plan.market if o.op is MarketOp.BUY_PRODUCT]


# --- Useful final-day watering ----------------------------------------------------------------


def test_one_time_crop_watering_is_kept_when_its_bonus_can_still_be_sold(base, monkeypatch):
    # Melon planted day 19: age 10 on day 29, inside the bonus window (6-12), below the cap.
    melon = raw_plant("MELON", 19, watered_today=False, consecutive_unwatered=0, yield_units=5)
    pos = Position(3, 4)  # one step from the shed access tile
    state = make_state(base, day=LAST_DAY, hour=5, tiles={(3, 4): melon})
    plan = plan_for(state, True, monkeypatch)
    (water,) = kinds_at(plan, ObjectiveKind.WATER_CROP, pos)
    assert water.deadline_hour == final_day_harvest_deadline(1) - 1 == 20
    assert kinds_at(plan, ObjectiveKind.HARVEST, pos) == []  # harvest waits for the bonus unit
    watered = {**melon, "watered_today": True, "yield_units": 6}
    after = plan_for(
        make_state(base, day=LAST_DAY, hour=6, tiles={(3, 4): watered}), True, monkeypatch
    )
    assert kinds_at(after, ObjectiveKind.HARVEST, pos) and not kinds_at(
        after, ObjectiveKind.WATER_CROP
    )


def test_one_time_watering_is_not_useful_when_it_can_no_longer_be_sold(base):
    def melon(yield_units):
        tile = raw_plant("MELON", 19, consecutive_unwatered=0, yield_units=yield_units)
        return make_state(base, day=LAST_DAY, hour=3, tiles={(3, 4): tile}).me.tiles[4][3]

    # One step from the shed: water by 20, harvest by 21, drop and sell by 23.
    assert final_day_water_useful(LAST_DAY, 20, melon(5), 1)
    assert not final_day_water_useful(LAST_DAY, 21, melon(5), 1)
    assert not final_day_water_useful(LAST_DAY, 3, melon(6), 1)  # already at the cap


# --- Delivery deadlines -------------------------------------------------------------------------


def test_final_day_harvest_keep_and_drop_boundary(base, monkeypatch):
    far = Position(0, 0)  # 8 steps from (4, 4): harvest by hour 23 - 8 - 1 = 14
    goose = raw_animal("GOOSE", 20, fed_today=True, yield_units=2)
    keep = plan_for(
        make_state(base, day=LAST_DAY, hour=14, tiles={(0, 0): goose}), True, monkeypatch
    )
    (harvest,) = kinds_at(keep, ObjectiveKind.HARVEST, far)
    assert harvest.deadline_hour == 14
    drop = plan_for(
        make_state(base, day=LAST_DAY, hour=15, tiles={(0, 0): goose}), True, monkeypatch
    )
    assert kinds_at(drop, ObjectiveKind.HARVEST, far) == []
    # M6 kept sending a unit even when the product could no longer be sold.
    assert kinds_at(
        plan_for(
            make_state(base, day=LAST_DAY, hour=15, tiles={(0, 0): goose}), False, monkeypatch
        ),
        ObjectiveKind.HARVEST,
        far,
    )


def test_partial_one_time_yield_is_harvested_on_the_final_day(base, monkeypatch):
    # Watered melon planted day 18: age 11 is past its first yield (10) but below
    # the max-yield day (12) and the cap, so M6 never saw it as ready; anything
    # left on the field at the end is lost, so the final day harvests it.
    melon = raw_plant("MELON", 18, watered_today=True, consecutive_unwatered=0, yield_units=5)
    state = make_state(base, day=LAST_DAY, hour=2, tiles={(3, 4): melon})
    assert not kinds_at(plan_for(state, False, monkeypatch), ObjectiveKind.HARVEST, Position(3, 4))
    assert kinds_at(plan_for(state, True, monkeypatch), ObjectiveKind.HARVEST, Position(3, 4))


def test_deliver_is_promoted_once_a_carrying_unit_is_due(base, monkeypatch):
    kw = dict(day=LAST_DAY, farmer=(0, 0), inventory={"EGG": 3})
    early = make_state(base, hour=10, **kw)
    jobs = generate_jobs(early, plan_for(early, True, monkeypatch))
    assert [j.priority for j in jobs if j.kind is JobKind.DELIVER] == [PRIORITY_DELIVERY]
    due = make_state(base, hour=14, **kw)  # 14 + 8 steps >= 23 - 1 slack
    jobs = generate_jobs(due, plan_for(due, True, monkeypatch))
    (deliver,) = [j for j in jobs if j.kind is JobKind.DELIVER]
    assert deliver.priority == PRIORITY_SURVIVAL and deliver.deadline_hour == 23


def test_a_due_carrying_unit_heads_for_the_shed_instead_of_new_work(base, monkeypatch):
    goose = raw_animal("GOOSE", 20, fed_today=True, yield_units=3)
    state = make_state(
        base, day=LAST_DAY, hour=14, farmer=(0, 0), inventory={"EGG": 3}, tiles={(0, 1): goose}
    )
    memory = reset_episode_memory(state.player_id)
    update_model(state, memory)
    monkeypatch.setattr(strategy, "ENDGAME_POLICY", True)
    turn = assign_jobs(state, strategy.choose_plan(state, memory), memory)
    assert turn.farmer.op.value in ("EAST", "SOUTH")  # toward (4, 4), not HARVEST at (0, 1)


def test_units_on_the_shed_drop_and_sell_in_the_same_last_turn(base, monkeypatch):
    state = make_state(base, day=LAST_DAY, hour=23, farmer=(4, 4), inventory={"EGG": 3, "MILK": 1})
    memory = reset_episode_memory(state.player_id)
    update_model(state, memory)
    monkeypatch.setattr(strategy, "ENDGAME_POLICY", True)
    plan = strategy.choose_plan(state, memory)
    turn = assign_jobs(state, plan, memory)
    assert turn.farmer.op.value == "DROP"
    sold = {o.item: o.quantity for o in plan.market if o.op is MarketOp.SELL}
    assert sold.get("EGG") == 3 and sold.get("MILK") == 1


# --- Preserved behaviour -----------------------------------------------------------------------


def test_liquidation_sells_premium_stock_despite_opponent_pressure(base, monkeypatch):
    field = {
        (x, y): raw_plant(
            "STRAWBERRY", 15, watered_today=True, consecutive_unwatered=0, yield_units=2
        )
        for x in range(5)
        for y in range(5)
    }
    state = make_state(base, day=LAST_DAY, hour=6, shed={"STRAWBERRY": 12}, opp_tiles=field)
    sold = {
        o.item: o.quantity
        for o in plan_for(state, True, monkeypatch).market
        if o.op is MarketOp.SELL
    }
    assert sold.get("STRAWBERRY") == 12


@pytest.mark.parametrize("name", OFFICIAL_FIXTURES)
def test_days_before_the_final_day_are_unchanged(name, monkeypatch):
    obs = load_fixture(name)
    from kaggriculture_bot.parser import parse_observation

    state = parse_observation(obs)
    if is_final_day(state.day):
        pytest.skip("final-day fixture")
    assert plan_for(state, True, monkeypatch) == plan_for(state, False, monkeypatch)


@pytest.mark.parametrize("day", [0, 12, 26, 27, LAST_DAY - 1])
def test_scenario_days_0_to_28_are_unchanged(base, monkeypatch, day):
    tiles = {
        (1, 1): hungry_goose(),
        (2, 2): raw_plant("STRAWBERRY", max(0, day - 12), consecutive_unwatered=0, yield_units=1),
        (0, 0): raw_animal("COW", 0, fed_today=True, yield_units=2),
    }
    state = make_state(base, day=day, hour=7, tiles=tiles, shed={"WHEAT": 2}, inventory={"EGG": 2})
    assert plan_for(state, True, monkeypatch) == plan_for(state, False, monkeypatch)


def test_final_day_plan_is_deterministic(base, monkeypatch):
    tiles = {
        (0, 0): raw_animal("GOOSE", 20, fed_today=True, yield_units=2),
        (3, 4): raw_plant("MELON", 19, consecutive_unwatered=0, yield_units=5),
    }
    state = make_state(base, day=LAST_DAY, hour=9, tiles=tiles, inventory={"EGG": 1})
    assert plan_for(state, True, monkeypatch) == plan_for(state, True, monkeypatch)


def test_policy_off_is_exactly_the_milestone_6_plan(base, monkeypatch):
    tiles = {(1, 1): hungry_goose(), (2, 2): raw_plant("STRAWBERRY", 15, consecutive_unwatered=0)}
    state = make_state(base, day=LAST_DAY, hour=4, tiles=tiles, inventory={"EGG": 2})
    memory = reset_episode_memory(state.player_id)
    update_model(state, memory)
    monkeypatch.setattr(strategy, "ENDGAME_POLICY", False)
    assert strategy.choose_plan(state, memory) == strategy._plan(state, memory)
