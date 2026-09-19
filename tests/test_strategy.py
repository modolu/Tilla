"""Tests for kaggriculture_bot.strategy: survival/care priorities and economic integration."""

import pytest

from kaggriculture_bot import economy
from kaggriculture_bot.constants import (
    EARLY_MIN_CASH_RESERVE,
    FEED_WHEAT_RESERVE_PER_ANIMAL,
    MAX_DAILY_HIRES,
    MAX_MARKET_ORDERS_PER_TURN,
)
from kaggriculture_bot.models import (
    PRIORITY_DAILY_WORK,
    JobKind,
    MarketOp,
    MarketOrder,
    ObjectiveKind,
    OpportunityKind,
    Position,
    UnitAction,
    UnitOp,
)
from kaggriculture_bot.runtime import reset_episode_memory
from kaggriculture_bot.strategy import PLANT_DEADLINE_HOUR, choose_plan
from kaggriculture_bot.tasks import assign_jobs, assign_units, generate_jobs
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
    """Feeding is planned as the FEED objective; the shed fetch is a task-layer
    prerequisite, and the wheat it will pick up is kept out of the sale."""
    state = make_state(
        obs_no_hands,
        day=3,
        hour=2,
        tiles={(1, 1): raw_animal(consecutive_unfed=1)},
        shed={"WHEAT": 6},
        seeds={"WHEAT": 5},
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.FEED_ANIMAL
    assert plan.objective.targets == (P(1, 1),)
    assert plan.objective.item == "WHEAT" and plan.objective.quantity == 1
    assert not [o for o in plan.market if o.op is MarketOp.BUY_PRODUCT]
    # The same-turn PICKUP (1) and the feed reserve (2 per animal) are kept back from the sale.
    sells = [o for o in plan.market if o.op is MarketOp.SELL]
    assert sells == [MarketOrder(MarketOp.SELL, "WHEAT", 6 - 1 - FEED_WHEAT_RESERVE_PER_ANIMAL)]
    turn = assign_jobs(state, plan, reset_episode_memory(state.player_id))
    assert turn.farmer == UnitAction(UnitOp.PICKUP, "WHEAT", 1)  # at the only access tile


def test_feed_buys_wheat_only_when_none_is_available(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=3,
        hour=2,
        tiles={(1, 1): raw_animal(consecutive_unfed=1)},
        seeds={"WHEAT": 5},
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.FEED_ANIMAL
    assert MarketOrder(MarketOp.BUY_PRODUCT, "WHEAT", 1) in plan.market
    assert not [o for o in plan.market if o.op is MarketOp.BUY_SEED]
    # Nothing to fetch yet (the wheat arrives after this turn): the farmer plants meanwhile.
    turn = assign_jobs(state, plan, reset_episode_memory(state.player_id))
    assert turn.farmer == UnitAction(UnitOp.PLANT, "WHEAT")


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
    kinds = [o.kind for o in plan.all_objectives() if o.priority == plan.objective.priority]
    assert set(kinds) == {ObjectiveKind.FEED_ANIMAL, ObjectiveKind.WATER_CROP}
    # Both are survival work; the lone farmer takes the adjacent watering before the
    # feed trip (shed pickup + walk), which is the assignment layer's job.
    turn = assign_jobs(state, plan, reset_episode_memory(state.player_id))
    assert turn.farmer == UnitAction(UnitOp.NORTH)


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


# --- Economic tier: planting the best opportunity ----------------------------------------------


def best_kind(state):
    return economy.rank_opportunities(state)[0]


def test_economic_tier_plants_the_top_ranked_crop_when_seeds_are_held(obs_no_hands):
    """Held seeds are sunk cost, so a held crop is planted before buying anything else."""
    state = make_state(obs_no_hands, day=1, hour=3, seeds={"CARROT": 2})
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.PLANT
    assert plan.objective.item == "CARROT"
    assert plan.objective.deadline_hour == PLANT_DEADLINE_HOUR == 22
    assert len(plan.objective.targets) == 2  # one target per held seed
    assert plan.objective.targets[0] == P(4, 4)  # nearest the shed
    assert not [o for o in plan.market if o.op is MarketOp.BUY_SEED]


def test_economic_tier_starts_the_top_ranked_opportunity(obs_no_hands):
    state = make_state(obs_no_hands, day=0, hour=0, money=3000)
    top = economy.rank_opportunities(state)[0]
    plan = plan_for(state)
    assert top.score > 0
    if top.kind is OpportunityKind.CROP:
        assert plan.objective.kind is ObjectiveKind.REPOSITION
        buys = [o for o in plan.market if o.op is MarketOp.BUY_SEED]
        assert buys and buys[0].item == top.product
    else:  # animal chain: build the structure first, buy only once it exists
        assert plan.objective.kind is ObjectiveKind.BUILD_STRUCTURE
        assert plan.objective.item == economy.ANIMALS[top.product].structure
        assert not [o for o in plan.market if o.op is MarketOp.BUY_ANIMAL]


def test_animal_is_bought_only_once_its_structure_exists(obs_no_hands):
    state = make_state(obs_no_hands, day=0, hour=2, money=3000, tiles={(4, 3): {"kind": "COOP"}})
    top = economy.rank_opportunities(state)[0]
    if top.kind is OpportunityKind.ANIMAL and top.product == "GOOSE":
        plan = plan_for(state)
        assert MarketOrder(MarketOp.BUY_ANIMAL, "GOOSE", 1) in plan.market
        assert not [o for o in plan.all_objectives() if o.kind is ObjectiveKind.BUILD_STRUCTURE]


@pytest.mark.parametrize("hour", [23])
def test_no_planting_without_same_day_watering_capacity(obs_no_hands, hour):
    state = make_state(obs_no_hands, day=1, hour=hour, seeds={"WHEAT": 2})
    plan = plan_for(state)
    assert plan.objective.kind is not ObjectiveKind.PLANT
    assert not [o for o in plan.market if o.op is MarketOp.BUY_SEED]


def test_planting_allowed_at_the_deadline_hour(obs_no_hands):
    state = make_state(obs_no_hands, day=1, hour=PLANT_DEADLINE_HOUR, seeds={"WHEAT": 2})
    assert plan_for(state).objective.kind is ObjectiveKind.PLANT


def test_seed_purchase_respects_the_cash_reserve(obs_no_hands):
    broke = make_state(obs_no_hands, day=0, hour=0, money=EARLY_MIN_CASH_RESERVE + 5)
    plan = plan_for(broke)
    assert not [o for o in plan.market if o.op in (MarketOp.BUY_SEED, MarketOp.BUY_ANIMAL)]
    assert plan.objective.kind is ObjectiveKind.PASS
    # Just enough for a couple of wheat seeds: buys only what the reserve allows.
    tight = make_state(obs_no_hands, day=0, hour=0, money=EARLY_MIN_CASH_RESERVE + 25)
    buys = [o for o in plan_for(tight).market if o.op is MarketOp.BUY_SEED]
    assert len(buys) <= 1
    for order in buys:
        assert order.quantity * economy.CROPS[order.item].seed <= 25


def test_no_planting_when_no_empty_tile_is_usable(obs_no_hands):
    tiles = {(x, y): {"kind": "WEED"} for x in range(5) for y in range(5)}
    tiles[(1, 1)] = {"kind": "COOP"}
    state = make_state(obs_no_hands, day=1, hour=5, tiles=tiles, seeds={"WHEAT": 3}, money=5000)
    plan = plan_for(state)
    assert plan.objective.kind is not ObjectiveKind.PLANT
    assert not [o for o in plan.market if o.op is MarketOp.BUY_SEED]


@pytest.mark.parametrize("day", [27, 28])
def test_terminal_horizon_guard_stops_planting_and_seed_buying(obs_no_hands, day):
    state = make_state(obs_no_hands, day=day, hour=2, seeds={"WHEAT": 2}, money=9000)
    plan = plan_for(state)
    assert plan.objective.kind is not ObjectiveKind.PLANT
    assert not [o for o in plan.market if o.op in (MarketOp.BUY_SEED, MarketOp.BUY_ANIMAL)]


def test_late_season_still_plants_a_fast_crop_that_can_mature(obs_no_hands):
    state = make_state(obs_no_hands, day=24, hour=2, money=9000)
    plan = plan_for(state)
    buys = [o for o in plan.market if o.op is MarketOp.BUY_SEED]
    assert buys and buys[0].item in ("WHEAT", "CARROT")


def test_held_seeds_of_an_unviable_crop_are_left_unplanted(obs_no_hands):
    state = make_state(obs_no_hands, day=25, hour=2, seeds={"MELON": 2}, money=9000)
    plan = plan_for(state)
    assert not (plan.objective.kind is ObjectiveKind.PLANT and plan.objective.item == "MELON")


def test_one_feed_purchase_covers_every_mouth_today(obs_no_hands):
    """At-risk and routine animals share one wheat order sized to the total shortfall."""
    state = make_state(
        obs_no_hands,
        day=3,
        hour=2,
        tiles={
            (1, 1): raw_animal(consecutive_unfed=1),
            (2, 2): raw_animal(consecutive_unfed=0),
            (3, 3): raw_animal(consecutive_unfed=0),
        },
        shed={"WHEAT": 1},
    )
    buys = [o for o in plan_for(state).market if o.op is MarketOp.BUY_PRODUCT]
    assert buys == [MarketOrder(MarketOp.BUY_PRODUCT, "WHEAT", 2)]


def test_routine_feed_is_bought_from_the_reserve(obs_no_hands):
    """Feeding existing animals is mandatory care: cash sitting on the hard
    floor still buys today's wheat, but never below zero."""
    state = make_state(
        obs_no_hands,
        day=3,
        hour=2,
        tiles={(1, 1): raw_animal(consecutive_unfed=0), (2, 2): raw_animal(consecutive_unfed=0)},
        money=EARLY_MIN_CASH_RESERVE,
    )
    assert MarketOrder(MarketOp.BUY_PRODUCT, "WHEAT", 2) in plan_for(state).market
    broke = make_state(
        obs_no_hands, day=3, hour=2, tiles={(1, 1): raw_animal(consecutive_unfed=0)}, money=5
    )
    assert not [o for o in plan_for(broke).market if o.op is MarketOp.BUY_PRODUCT]


# --- Started work is completed before new investment ------------------------------------


def test_objectives_never_claim_one_tile_twice(obs_no_hands):
    """Held seeds of two crops, an animal to house and an economic build all
    get distinct tiles; the watering-capacity budget is shared across crops."""
    state = make_state(
        obs_no_hands, day=3, hour=4, seeds={"WHEAT": 3, "CARROT": 3}, shed={"GOOSE": 1}, money=3000
    )
    plan = plan_for(state)
    tiles = [t for o in plan.all_objectives() for t in o.targets]
    assert len(tiles) == len(set(tiles)), tiles
    plants = [o for o in plan.all_objectives() if o.kind is ObjectiveKind.PLANT]
    assert {o.item for o in plants} == {"CARROT", "WHEAT"}
    assert [o for o in plan.all_objectives() if o.kind is ObjectiveKind.BUILD_STRUCTURE]
    # Late in the day the shared watering budget caps the total, not each crop.
    late = make_state(obs_no_hands, day=3, hour=21, seeds={"WHEAT": 3, "CARROT": 3})
    planted = sum(
        len(o.targets) for o in plan_for(late).all_objectives() if o.kind is ObjectiveKind.PLANT
    )
    assert planted == 2  # (23 - 21) turns x 1 unit


def test_bought_animal_is_fetched_and_placed_as_daily_work(obs_no_hands):
    tiles = {(4, 4): raw_plant(planted_day=0, watered_today=True, consecutive_unwatered=0)}
    tiles[(2, 2)] = {"kind": "COOP"}
    state = make_state(obs_no_hands, day=3, hour=4, tiles=tiles, shed={"GOOSE": 1})
    plan = plan_for(state)
    place = [o for o in plan.all_objectives() if o.kind is ObjectiveKind.PLACE_ANIMAL]
    assert place and place[0].targets == (P(2, 2),) and place[0].item == "GOOSE"
    assert place[0].priority == PRIORITY_DAILY_WORK
    # The shed fetch is a prerequisite handled by the task layer.
    turn = assign_jobs(state, plan, reset_episode_memory(state.player_id))
    assert turn.farmer == UnitAction(UnitOp.PICKUP, "GOOSE", 1)
    carried_state = make_state(obs_no_hands, day=3, hour=4, tiles=tiles, inventory={"GOOSE": 1})
    plan = plan_for(carried_state)
    kinds = {o.kind: o for o in plan.all_objectives()}
    assert ObjectiveKind.PLACE_ANIMAL in kinds and kinds[ObjectiveKind.PLACE_ANIMAL].targets == (
        P(2, 2),
    )


def test_carried_animal_without_structure_builds_one(obs_no_hands):
    state = make_state(obs_no_hands, day=3, hour=4, inventory={"GOOSE": 1})
    plan = plan_for(state)
    kinds = {o.kind: o for o in (plan.objective, *plan.equal_priority)}
    assert ObjectiveKind.BUILD_STRUCTURE in kinds
    assert kinds[ObjectiveKind.BUILD_STRUCTURE].item == "COOP"


def test_daily_work_is_batched_by_nearest_target(obs_no_hands):
    tiles = {
        (0, 0): raw_plant(planted_day=4, consecutive_unwatered=0),  # far: needs water
        (4, 3): raw_animal(fed_today=True, yield_units=3),  # adjacent: eggs at cap-1
    }
    state = make_state(obs_no_hands, day=6, hour=4, tiles=tiles)
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.WATER_CROP  # routine care listed first ...
    jobs = generate_jobs(state, plan)
    assigned = assign_units(state, jobs, reset_episode_memory(state.player_id))
    assert assigned[0].kind is JobKind.HARVEST  # ... but the nearest work is executed first


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
    state = make_state(
        obs_no_hands, day=29, hour=3, inventory={"WHEAT": 7, "EGG": 2}, shed={"WHEAT": 2}
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.DELIVER and plan.objective.targets == (P(4, 4),)
    assert MarketOrder(MarketOp.SELL, "WHEAT", 9) in plan.market  # drop precedes market processing
    assert MarketOrder(MarketOp.SELL, "EGG", 2) in plan.market
    away = make_state(
        obs_no_hands, day=29, hour=3, farmer=(2, 2), inventory={"WHEAT": 7}, shed={"WHEAT": 2}
    )
    assert [o for o in plan_for(away).market if o.op is MarketOp.SELL] == [
        MarketOrder(MarketOp.SELL, "WHEAT", 2)
    ]


def test_idle_farmer_delivers_carried_produce(obs_no_hands):
    """With nothing to care for and no affordable investment, carried produce goes to the shed."""
    tiles = {
        (x, y): raw_plant(planted_day=0, watered_today=True, consecutive_unwatered=0)
        for x in range(2, 5)
        for y in range(3, 5)
    }
    state = make_state(
        obs_no_hands, day=2, hour=5, tiles=tiles, farmer=(2, 3), inventory={"WHEAT": 4}, money=300
    )
    plan = plan_for(state)
    assert plan.objective.kind is ObjectiveKind.DELIVER


# --- Scope guards: nothing beyond Milestone 4 ----------------------------------------------------


def test_no_land_purchase_in_any_state_and_market_stays_within_the_cap(
    obs_no_hands, obs_midgame_p1, obs_final
):
    for base in (obs_no_hands, obs_midgame_p1, obs_final):
        for day in (0, 5, 12, 26, 29):
            for hour in (0, 12, 23):
                state = make_state(base, day=day, hour=hour, money=9000)
                plan = plan_for(state)
                assert len(plan.market) <= MAX_MARKET_ORDERS_PER_TURN
                for order in plan.market:
                    assert order.op is not MarketOp.BUY_LAND, order
                    if order.op is MarketOp.BUY_PRODUCT:
                        assert order.item in ("WHEAT", "FERTILIZER")
                hires = [o for o in plan.market if o.op is MarketOp.HIRE]
                assert len(hires) == plan.hires <= MAX_DAILY_HIRES - state.me.hires_today
                if hour == 23 or day >= 29 and hour == 23:
                    assert not hires  # a hand hired now would never act
                assert plan.objective.kind in set(ObjectiveKind)


# --- Hiring policy (TILLA_STRATEGY.md §12) --------------------------------------------------------


def _hire_orders(plan):
    return [o for o in plan.market if o.op is MarketOp.HIRE]


def test_hands_are_hired_for_a_valuable_backlog_the_farmer_cannot_finish(obs_no_hands):
    field = {
        (x, y): raw_plant(planted_day=2, consecutive_unwatered=0)
        for x in range(5)
        for y in range(5)
    }
    state = make_state(obs_no_hands, day=6, hour=0, tiles=field, money=2000)
    plan = plan_for(state)
    assert plan.hires >= 1 and len(_hire_orders(plan)) == plan.hires
    assert plan.market[-plan.hires :] == tuple(_hire_orders(plan))  # hires come last
    assert plan.hires <= 3  # the fourth same-turn spawn (5,5) is stuck (TILLA_RULES.md §5)


def test_no_hiring_without_work(obs_no_hands):
    state = make_state(obs_no_hands, day=6, hour=0, money=EARLY_MIN_CASH_RESERVE)
    assert plan_for(state).hires == 0


def test_no_hiring_when_the_current_workforce_covers_the_backlog(obs_no_hands):
    state = make_state(
        obs_no_hands,
        day=6,
        hour=0,
        tiles={(4, 3): raw_plant(planted_day=2, consecutive_unwatered=0)},
    )
    assert plan_for(state).hires == 0


def test_no_hiring_late_in_the_day(obs_no_hands):
    field = {
        (x, y): raw_plant(planted_day=2, consecutive_unwatered=0)
        for x in range(5)
        for y in range(5)
    }
    for hour in (21, 22, 23):
        state = make_state(obs_no_hands, day=6, hour=hour, tiles=field, money=2000)
        assert plan_for(state).hires == 0


def test_care_hands_are_paid_from_the_reserve(obs_no_hands):
    """Twenty-five plants to water with 300 coins (= the hard floor): the hands
    that keep them alive are still hired, never taking the bank below zero.
    Hands for work beyond care stay behind the reserve (see test_economy)."""
    field = {
        (x, y): raw_plant(planted_day=2, consecutive_unwatered=0)
        for x in range(5)
        for y in range(5)
    }
    broke = make_state(obs_no_hands, day=6, hour=0, tiles=field, money=EARLY_MIN_CASH_RESERVE)
    plan = plan_for(broke)
    assert plan.hires >= 1
    spend = sum(economy.hire_cost(k) for k in range(plan.hires))
    assert broke.me.money - spend >= 0
    care = [o for o in plan.all_objectives() if o.priority <= PRIORITY_DAILY_WORK]
    assert sum(len(o.targets) for o in care) == 25


def test_market_list_keeps_feed_and_purchases_ahead_of_hires_within_the_cap(obs_no_hands):
    """Heavy turn: eight sellable products, an at-risk animal without wheat, a
    seed purchase and a hiring backlog. Sales, the feed purchase and the
    economic purchase survive; hires are what the 10-order cap trims."""
    field = {
        (x, y): raw_plant(planted_day=2, consecutive_unwatered=0)
        for x in range(5)
        for y in range(4)
    }
    field[(1, 4)] = raw_animal(consecutive_unfed=1)
    shed = {
        p: 5
        for p in ("CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER")
    }
    state = make_state(obs_no_hands, day=6, hour=0, tiles=field, shed=shed, money=2000)
    plan = plan_for(state)
    ops = [o.op for o in plan.market]
    assert len(plan.market) <= MAX_MARKET_ORDERS_PER_TURN
    assert MarketOrder(MarketOp.BUY_PRODUCT, "WHEAT", 1) in plan.market
    assert ops.index(MarketOp.BUY_PRODUCT) > max(
        i for i, op in enumerate(ops) if op is MarketOp.SELL
    )
    assert plan.hires == ops.count(MarketOp.HIRE)
    if MarketOp.HIRE in ops:
        assert ops.index(MarketOp.HIRE) > ops.index(MarketOp.BUY_PRODUCT)
    # Without the sell wave the same turn hires; the trimmed hires come back next turn.
    plain = make_state(obs_no_hands, day=6, hour=0, tiles=field, money=2000)
    assert plan_for(plain).hires >= 1
    assert plan_for(plain).market[-1].op is MarketOp.HIRE


def test_hiring_stops_at_the_daily_cap(obs_two_hands):
    field = {
        (x, y): raw_plant(planted_day=2, consecutive_unwatered=0)
        for x in range(5)
        for y in range(5)
    }
    base = make_state(obs_two_hands, day=6, hour=0, tiles=field, money=5000)
    assert base.me.hires_today == 2
    plan = plan_for(base)
    assert plan.hires + base.me.hires_today <= MAX_DAILY_HIRES


# --- Official-environment scenarios (farm-care loop end to end) --------------------------------

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
    market_ops = {o[0] for _, _, a in actions for o in a["market"]}
    unit_ops = farmer_ops | {h[0] for _, _, a in actions for h in a["hands"]}
    assert {"PLANT", "WATER", "HARVEST"} <= unit_ops
    assert {"BUY_SEED", "SELL", "HIRE"} <= market_ops
    # Nothing outside the Milestone 4 scope was ever attempted: no land.
    assert "BUY_LAND" not in market_ops
    assert unit_ops <= {
        "PASS", "NORTH", "SOUTH", "EAST", "WEST", "PLANT", "WATER", "HARVEST", "DROP",
        "PICKUP", "BUILD_COOP", "BUILD_PASTURE", "PLACE", "FEED", "COLLECT_FERTILIZER", "FERTILIZE",
    }  # fmt: skip
    # Late viability: no planting or seed purchase once no crop can mature before the end.
    late = [a for d, _, a in actions if d >= 27]
    assert late
    assert not [a for a in late if "PLANT" in {a["farmer"][0], *(h[0] for h in a["hands"])}]
    assert not [a for a in late if any(o[0] == "BUY_SEED" for o in a["market"])]
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
    tile = _our_tiles(obs)[4][4]
    assert obs["day"] == 1 and not (isinstance(tile, dict) and tile.get("kind") == "PLANT")
    assert obs["private"]["seeds"]["WHEAT"] == 1  # the seed is still held for tomorrow


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


def test_baseline_snapshot_source_is_frozen():
    """agents/baseline.py must stay byte-identical to the accepted Milestone 2 snapshot."""
    import hashlib
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "agents" / "baseline.py"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == "bddbe37bf48f55fc59609ff084d608cb33756c9224b769a5f4073910a95c5f3d"


def test_baseline_snapshot_behaviour_is_stable_and_beats_pass():
    """The frozen Milestone 2 baseline still produces its recorded control result
    (seed 1001, both seats) after Milestone 3 changes to shared lower layers."""
    from agents import baseline

    env = _env(seed=1001)
    env.run([baseline.agent, "pass"])
    assert len(env.steps) == 720 and env.done
    assert [s["reward"] for s in env.steps[-1]] == [6478.0, 3000.0]
    env = _env(seed=1001)
    env.run(["pass", baseline.agent])
    assert [s["reward"] for s in env.steps[-1]] == [3000.0, 6478.0]


# --- Milestone 3 economic scenarios in the official environment ----------------------------------


def _run_days(env, agent, days):
    while not env.done and env.state[0].observation["day"] < days:
        obs = env.state[0].observation
        env.step([agent(obs), PASS_ACTION])


def _ops(env, seat=0):
    """Unit actions (farmer and hands) and market orders issued by ``seat``."""
    units, market = [], []
    for step in env.steps[1:]:
        action = step[seat]["action"]
        if isinstance(action, dict):
            units.append(tuple(action["farmer"]))
            units.extend(tuple(h) for h in action["hands"])
            market.extend(tuple(o) for o in action["market"])
    return units, market


def test_scenario_animal_investment_chain_executes_and_pays_out():
    """Day-0 economics rank a goose first: the stateless chain must build, buy,
    fetch, place, feed daily, collect fertilizer, harvest eggs and sell them."""
    import main

    env = _env(seed=5)
    _run_days(env, main.agent, 9)
    units, market = _ops(env)
    assert ("BUILD_COOP",) in units and ("PICKUP", "GOOSE", 1) in units
    assert ("PLACE", "GOOSE") in units and ("FEED",) in units
    assert ("COLLECT_FERTILIZER",) in units and ("HARVEST",) in units
    assert ("BUY_ANIMAL", "GOOSE", 1) in market
    assert any(o[:2] == ("SELL", "EGG") for o in market)
    assert any(o[:2] == ("SELL", "FERTILIZER") for o in market)
    farm = env.state[0].observation["farms"][0]
    geese = [
        t
        for row in farm["tiles"]
        for t in row
        if isinstance(t, dict) and t.get("animal") == "GOOSE"
    ]
    assert geese and all(g["consecutive_unfed"] == 0 for g in geese)  # fed every day, none escaped
    assert env.state[0].observation["private"]["shed"].get("GOOSE", 0) == 0  # nothing stranded


def test_scenario_crops_are_chosen_economically_not_by_habit():
    """With default prices the top crop is melon; it is planted, watered daily and harvested."""
    import main

    env = _env(seed=5)
    _run_days(env, main.agent, 13)
    units, market = _ops(env)
    assert ("PLANT", "MELON") in units
    assert any(o[0] == "BUY_SEED" and o[1] == "MELON" for o in market)
    assert ("HARVEST",) in units
    assert any(o[:2] == ("SELL", "MELON") for o in market)
    assert env.state[0].observation["farms"][0]["money"] > 3000


def test_scenario_fertilizer_is_applied_when_its_incremental_value_clears_its_cost():
    """Cheap fertilizer plus a valuable ongoing crop makes fertilizing a tomato the
    best opportunity; the chain buys, fetches and applies it."""
    from kaggle_environments import make

    import main

    env = make(
        "kaggriculture",
        configuration={
            "episodeSteps": 720,
            "seed": 5,
            "marketParams": {
                "FERTILIZER": {"base": 5},
                "TOMATO": {"base": 300},
                "MELON": {"base": 20},
            },
        },
        debug=True,
    )
    env.reset()
    _drive(env, lambda o: {"farmer": ["PASS"], "hands": [], "market": [["BUY_SEED", "TOMATO", 1]]})
    _drive(env, lambda o: {"farmer": ["NORTH"], "hands": [], "market": []})
    _drive(env, lambda o: {"farmer": ["PLANT", "TOMATO"], "hands": [], "market": []})
    _drive(env, lambda o: {"farmer": ["WATER"], "hands": [], "market": []})
    _run_days(env, main.agent, 11)  # Tilla takes over: daily care, then economics
    units, market = _ops(env)
    assert any(o[:2] == ("BUY_PRODUCT", "FERTILIZER") for o in market)
    assert ("PICKUP", "FERTILIZER", 1) in units and ("FERTILIZE",) in units
    fertilized = [
        (step[0]["observation"]["day"], t["crop"])
        for step in env.steps
        for row in step[0]["observation"]["farms"][0]["tiles"]
        for t in row
        if isinstance(t, dict) and t.get("kind") == "PLANT" and t["fertilized_until_day"] >= 0
    ]
    assert fertilized and all(crop == "TOMATO" for _, crop in fertilized)


def test_scenario_no_livestock_or_fertilizer_purchase_below_reserve(obs_no_hands):
    state = make_state(obs_no_hands, day=2, hour=2, money=EARLY_MIN_CASH_RESERVE + 20)
    plan = plan_for(state)
    assert not [o for o in plan.market if o.op in (MarketOp.BUY_ANIMAL, MarketOp.BUY_PRODUCT)]
    assert plan.objective.kind is not ObjectiveKind.BUILD_STRUCTURE


# --- Milestone 4 care-starvation scenarios in the official environment ----------------------------


def _hand_days(env, seat=0):
    """Per-day count of hands observed on ``seat``'s farm at hour 12."""
    days = {}
    for step in env.steps:
        obs = step[0]["observation"]
        if obs["hour"] == 12:
            days[obs["day"]] = len(obs["farms"][seat]["hands"])
    return days


def test_scenario_full_field_is_cared_for_by_hired_hands_without_losses():
    """A 25-tile field is more than one farmer can water: hands are hired daily
    and no crop is lost to missed watering, no fresh planting dies, no animal
    escapes, and hands do not spend the day passing."""
    import main
    from tools.harness import _care_losses

    env = _env(seed=5)
    _run_days(env, main.agent, 12)
    losses = _care_losses(env.steps, 0)
    assert losses.get("crops_lost_unwatered", 0) == 0
    assert losses.get("fresh_plantings_unwatered", 0) == 0
    assert losses.get("animals_escaped", 0) == 0
    hands = _hand_days(env)
    assert any(n >= 2 for n in hands.values())
    _, market = _ops(env)
    assert ("HIRE",) in market
    hand_ops = [tuple(h) for step in env.steps[1:] for h in step[0]["action"]["hands"]]
    assert hand_ops and sum(1 for h in hand_ops if h[0] == "PASS") / len(hand_ops) < 0.5


def test_scenario_care_hands_are_still_hired_when_cash_sits_on_the_reserve_floor():
    """Cash is forced down to the hard floor with a full field: the reserve does
    not starve care, hands keep being hired for it, and nothing is lost."""
    import main
    from tools.harness import _care_losses

    env = _env(seed=5)
    _run_days(env, main.agent, 6)
    obs = env.state[0].observation
    assert obs["day"] == 6 and obs["hour"] == 0
    tiles = [t for row in obs["farms"][0]["tiles"] for t in row]
    plants = sum(1 for t in tiles if isinstance(t, dict) and t.get("kind") == "PLANT")
    assert plants >= 20
    obs["farms"][0]["money"] = float(EARLY_MIN_CASH_RESERVE)
    start = len(env.steps)
    _run_days(env, main.agent, 9)
    losses = _care_losses(env.steps[start - 1 :], 0)
    assert losses.get("crops_lost_unwatered", 0) == 0
    assert losses.get("animals_escaped", 0) == 0
    hands = _hand_days(env)
    assert all(hands.get(day, 0) >= 1 for day in (6, 7, 8))
    money = [step[0]["observation"]["farms"][0]["money"] for step in env.steps[start:]]
    assert min(money) >= 0


def test_scenario_hands_never_spawn_stuck_on_the_south_east_tile():
    """The hiring plan never orders a hire whose spawn tile would be (5,5) while
    NE and SW are locked, so no paid hand is stuck for a day."""
    import main

    env = _env(seed=5)
    _run_days(env, main.agent, 8)
    stuck = [
        (step[0]["observation"]["day"], step[0]["observation"]["hour"])
        for step in env.steps
        if [5, 5] in step[0]["observation"]["farms"][0]["hands"]
        and step[0]["observation"]["farms"][0]["tiles"][4][5] == "LOCKED"
    ]
    assert stuck == []
