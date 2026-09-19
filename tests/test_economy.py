"""Tests for kaggriculture_bot.economy (Milestone 3: ROI, reserve, labor/land, shed, sell/hold)."""

import pytest

from kaggriculture_bot import economy
from kaggriculture_bot.constants import (
    ANIMALS,
    CROPS,
    EARLY_MIN_CASH_RESERVE,
    FARMER_DAILY_ACTION_BUDGET,
    FEED_WHEAT_RESERVE_PER_ANIMAL,
    HARVEST_MIN_CASH_RESERVE,
    LABOR_COST_PER_ACTION,
    LAND_SCARCITY_FREE_TILES,
    RESERVE_EMERGENCY_BUFFER,
    SHED_EMERGENCY,
    SHED_PRESSURE_START,
)
from kaggriculture_bot.features import (
    overflow_risk,
    shed_free_capacity,
    shed_occupancy,
    shed_pressure,
)
from kaggriculture_bot.models import OpportunityKind, Position
from tests.conftest import make_state, raw_animal, raw_plant

P = Position


def crop(state, name):
    return economy.estimate_crop(state, name)


def animal(state, name):
    return economy.estimate_animal(state, name)


def field(n):
    """n watered plants on the tiles nearest the shed (labor commitment n * 2 actions/day)."""
    tiles = {}
    for y in range(4, -1, -1):
        for x in range(4, -1, -1):
            if len(tiles) < n:
                tiles[(x, y)] = raw_plant(
                    planted_day=0, watered_today=True, consecutive_unwatered=0
                )
    return tiles


# --- Crop production timing (verified mechanics) ---------------------------------------------


def test_one_time_crop_plans_match_verified_yield_timing():
    wheat = economy.plan_crop(CROPS["WHEAT"], 0)
    assert (wheat.units, wheat.harvest_age, wheat.waterings, wheat.harvests) == (4, 4, 5, 1)
    carrot = economy.plan_crop(CROPS["CARROT"], 0)
    assert (carrot.units, carrot.harvest_age) == (3, 3)
    melon = economy.plan_crop(CROPS["MELON"], 0)
    assert (melon.units, melon.harvest_age) == (6, 10)  # cap of 6 reached at age 10, not 12
    assert economy.one_time_units_at_age(CROPS["WHEAT"], 1) == 0  # before first_yield_day


def test_ongoing_crop_plans_count_only_realizable_productions():
    tomato = economy.plan_crop(CROPS["TOMATO"], 0)
    assert (tomato.units, tomato.first_value_age, tomato.harvest_age) == (4, 8, 11)
    late_tomato = economy.plan_crop(CROPS["TOMATO"], 19)  # productions at ages 8..11 -> days 27..30
    assert late_tomato.feasible and late_tomato.units == 2  # only days 27 and 28 remain
    assert not economy.plan_crop(CROPS["TOMATO"], 21).feasible
    strawberry = economy.plan_crop(CROPS["STRAWBERRY"], 0)
    assert (strawberry.units, strawberry.harvest_age) == (4, 16)


def test_partial_late_season_one_time_yield():
    wheat = economy.plan_crop(CROPS["WHEAT"], 26)  # last harvest day 28 -> age 2 -> 2 units
    assert wheat.feasible and wheat.units == 2 and wheat.harvest_age == 2
    assert not economy.plan_crop(CROPS["WHEAT"], 27).feasible


# --- Crop economics -----------------------------------------------------------------------------


def test_positive_wheat_case_includes_seed_labor_and_time(obs_no_hands):
    state = make_state(obs_no_hands, day=1, hour=0)
    est = crop(state, "WHEAT")
    assert est.kind is OpportunityKind.CROP and est.product == "WHEAT"
    assert est.setup_cost == CROPS["WHEAT"].seed == est.setup_cash
    assert est.expected_units == 4 and est.expected_revenue == 4 * state.market.prices["WHEAT"]
    assert est.labor_cost > 0 and est.actions_required > 10  # plant, 5 waters, harvest, travel
    assert est.expected_net_value > 0 and est.score > 0 and est.realization_probability == 1.0
    assert est.turns_to_realize >= 4 * 24


def test_carrot_vs_wheat_ranking_follows_realizable_value_not_seed_price(obs_no_hands):
    state = make_state(obs_no_hands, day=1, hour=0)
    wheat, carrot = crop(state, "WHEAT"), crop(state, "CARROT")
    # Equal-ish gross (100 vs 105) but carrot is a day faster with one fewer watering.
    assert carrot.score > wheat.score
    pricey = make_state(obs_no_hands, day=1, hour=0, prices={"WHEAT": 60})
    assert crop(pricey, "WHEAT").score > crop(pricey, "CARROT").score


def test_current_market_price_changes_ranking(obs_no_hands):
    base = make_state(obs_no_hands, day=1, hour=0)
    assert crop(base, "MELON").score > crop(base, "TOMATO").score
    crashed = make_state(obs_no_hands, day=1, hour=0, prices={"MELON": 20})
    assert crop(crashed, "MELON").score < crop(crashed, "TOMATO").score
    assert crop(crashed, "MELON").expected_revenue == 6 * 20


def test_tomato_with_enough_vs_insufficient_season(obs_no_hands):
    early = crop(make_state(obs_no_hands, day=2, hour=0), "TOMATO")
    assert early.realization_probability == 1.0 and early.expected_units == 4
    late = crop(make_state(obs_no_hands, day=19, hour=0), "TOMATO")
    assert late.expected_units == 2 and late.score < early.score
    impossible = crop(make_state(obs_no_hands, day=22, hour=0), "TOMATO")
    assert impossible.realization_probability == 0.0 and impossible.score == 0.0


def test_strawberry_is_rejected_late_in_the_season(obs_no_hands):
    est = crop(make_state(obs_no_hands, day=20, hour=0), "STRAWBERRY")
    assert est.realization_probability == 0.0 and "season" in est.reason


def test_melon_high_nominal_value_loses_when_time_runs_out(obs_no_hands):
    day18 = make_state(obs_no_hands, day=18, hour=0)
    melon, wheat = crop(day18, "MELON"), crop(day18, "WHEAT")
    assert melon.expected_units == 6 and melon.expected_revenue > wheat.expected_revenue
    assert melon.score > wheat.score  # still realizable in full: still the better tile use
    day19 = make_state(obs_no_hands, day=19, hour=0)
    assert (
        crop(day19, "MELON").realization_probability == 0.0
    )  # first yield (age 10) is past day 28
    assert crop(day19, "WHEAT").score > 0 > crop(day19, "MELON").score - 1
    # Labor and time, not nominal price, decide: a saturated farmer prefers cheaper cycles.
    busy = make_state(obs_no_hands, day=1, hour=0, tiles=field(7))
    assert crop(busy, "MELON").labor_cost > crop(busy, "CARROT").labor_cost * 2


def test_seed_cost_is_waived_for_seeds_already_held(obs_no_hands):
    without = crop(make_state(obs_no_hands, day=1, hour=0), "WHEAT")
    held = crop(make_state(obs_no_hands, day=1, hour=0, seeds={"WHEAT": 1}), "WHEAT")
    assert held.setup_cash == 0 and held.expected_net_value == pytest.approx(
        without.expected_net_value + 10
    )


def test_watering_and_harvest_labor_included(obs_no_hands):
    state = make_state(obs_no_hands, day=1, hour=0)
    melon, carrot = crop(state, "MELON"), crop(state, "CARROT")
    assert melon.actions_required > carrot.actions_required
    assert melon.labor_cost == pytest.approx(melon.actions_required * economy.labor_price(state))


def test_impossible_crop_is_excluded_with_zero_realizability(obs_no_hands):
    no_tiles = {(x, y): {"kind": "WEED"} for x in range(5) for y in range(5)}
    est = crop(make_state(obs_no_hands, day=1, hour=0, tiles=no_tiles), "WHEAT")
    assert est.realization_probability == 0.0 and est.score == 0.0 and "tile" in est.reason


# --- Animal economics ---------------------------------------------------------------------------


def test_goose_has_positive_payback_early(obs_no_hands):
    est = animal(make_state(obs_no_hands, day=0, hour=0), "GOOSE")
    assert est.kind is OpportunityKind.ANIMAL and est.setup_cash == ANIMALS["GOOSE"].cost
    assert est.expected_units == 25  # eggs from day 4 through day 28
    assert est.input_cost > 0  # feed wheat at the current price
    assert est.expected_net_value > 0 and est.score > 0


def test_animal_negative_or_rejected_late(obs_no_hands):
    late = animal(make_state(obs_no_hands, day=25, hour=0), "COW")
    assert late.realization_probability == 0.0 or late.expected_net_value < 0
    final = animal(make_state(obs_no_hands, day=27, hour=0), "GOOSE")
    assert final.realization_probability == 0.0


def test_feed_cost_lowers_animal_value(obs_no_hands):
    cheap = animal(make_state(obs_no_hands, day=0, hour=0, prices={"WHEAT": 5}), "GOOSE")
    dear = animal(make_state(obs_no_hands, day=0, hour=0, prices={"WHEAT": 80}), "GOOSE")
    assert dear.input_cost > cheap.input_cost and dear.expected_net_value < cheap.expected_net_value


def test_cow_and_sheep_slower_payoff_is_reflected(obs_no_hands):
    state = make_state(obs_no_hands, day=0, hour=0)
    goose, cow, sheep = animal(state, "GOOSE"), animal(state, "COW"), animal(state, "SHEEP")
    assert goose.turns_to_realize < sheep.turns_to_realize < cow.turns_to_realize
    assert cow.expected_units == 11 and sheep.expected_units == 8 and goose.expected_units == 25


def test_animal_needs_labor_capacity(obs_no_hands):
    busy = make_state(obs_no_hands, day=2, hour=0, tiles=field(10))  # 20 committed actions
    assert economy.labor_capacity_remaining(busy) <= 0
    est = animal(busy, "GOOSE")
    assert est.realization_probability == 0.0 and "labor" in est.reason


def test_byproduct_fertilizer_is_conservative_not_free_money(obs_no_hands):
    state = make_state(obs_no_hands, day=0, hour=0)
    est = animal(state, "GOOSE")
    days = 29
    gross_if_all_sold = days * state.market.prices["FERTILIZER"]
    assert 0 < est.byproduct_value < gross_if_all_sold / 2 + 1
    no_fert = animal(make_state(obs_no_hands, day=0, hour=0, prices={"FERTILIZER": 1}), "GOOSE")
    assert no_fert.byproduct_value == 0.0


# --- Reserve -------------------------------------------------------------------------------------


def test_reserve_respects_hard_floors(obs_no_hands):
    assert economy.cash_reserve(make_state(obs_no_hands, day=3, hour=0)) >= EARLY_MIN_CASH_RESERVE
    assert (
        economy.cash_reserve(make_state(obs_no_hands, day=23, hour=0)) >= HARVEST_MIN_CASH_RESERVE
    )
    assert economy.cash_reserve(make_state(obs_no_hands, day=28, hour=0)) == 0


def test_feed_obligation_raises_reserve(obs_no_hands):
    tiles = {(1, 1): raw_animal(fed_today=True), (2, 1): raw_animal(fed_today=True)}
    with_animals = make_state(obs_no_hands, day=3, hour=0, tiles=tiles, prices={"WHEAT": 200})
    without = make_state(obs_no_hands, day=3, hour=0, prices={"WHEAT": 200})
    assert economy.expected_feed_purchases(with_animals) == 2 * 201
    assert economy.cash_reserve(with_animals) > economy.cash_reserve(without)
    stocked = make_state(obs_no_hands, day=3, hour=0, tiles=tiles, shed={"WHEAT": 5})
    assert economy.expected_feed_purchases(stocked) == 0


def test_seed_replenishment_raises_reserve(obs_no_hands):
    mature = {(4, 4): raw_plant(crop="MELON", planted_day=0, watered_today=True, yield_units=5)}
    state = make_state(obs_no_hands, day=9, hour=0, tiles=mature)  # harvest tomorrow (age 10)
    assert economy.expected_seed_replenishment(state) == CROPS["MELON"].seed
    assert economy.cash_reserve(state) == max(EARLY_MIN_CASH_RESERVE, 80 + RESERVE_EMERGENCY_BUFFER)
    young = make_state(obs_no_hands, day=2, hour=0, tiles=mature)
    assert economy.expected_seed_replenishment(young) == 0


def test_speculative_purchase_is_rejected_below_reserve(obs_no_hands):
    state = make_state(obs_no_hands, day=3, hour=0, money=EARLY_MIN_CASH_RESERVE + 50)
    reserve = economy.cash_reserve(state)
    melon = crop(state, "MELON")
    assert melon.score > 0 and not economy.affordable(state, melon, reserve)
    wheat = crop(state, "WHEAT")
    assert economy.affordable(state, wheat, reserve)


# --- Labor -------------------------------------------------------------------------------------


def test_labor_price_rises_with_utilization(obs_no_hands):
    idle = make_state(obs_no_hands, day=1, hour=0)
    busy = make_state(obs_no_hands, day=1, hour=0, tiles=field(8))
    assert economy.labor_price(idle) == LABOR_COST_PER_ACTION
    assert economy.labor_price(busy) > economy.labor_price(idle)
    assert economy.farmer_utilization(busy) == pytest.approx(16 / FARMER_DAILY_ACTION_BUDGET)


def test_higher_action_burden_lowers_otherwise_equal_opportunity(obs_no_hands):
    state = make_state(obs_no_hands, day=1, hour=0, tiles=field(6))
    price = economy.labor_price(state)
    assert price > LABOR_COST_PER_ACTION
    # Same gross value: the crop needing more actions nets less.
    cheap = economy._finish(
        OpportunityKind.CROP, "A", setup_cost=10, setup_cash=10, input_cost=0, revenue=300,
        byproduct=0, actions=10, occupancy_days=5, free_tiles=20, turns_to_realize=100,
        probability=1.0, units=1, action_price=price,
    )  # fmt: skip
    heavy = economy._finish(
        OpportunityKind.CROP, "B", setup_cost=10, setup_cash=10, input_cost=0, revenue=300,
        byproduct=0, actions=30, occupancy_days=5, free_tiles=20, turns_to_realize=100,
        probability=1.0, units=1, action_price=price,
    )  # fmt: skip
    assert heavy.labor_cost > cheap.labor_cost and heavy.score < cheap.score


def test_travel_and_action_burden_changes_ranking(obs_no_hands):
    """A saturated farmer prices actions so a labor-heavy goose no longer beats a melon."""
    idle = make_state(obs_no_hands, day=0, hour=0)
    assert animal(idle, "GOOSE").score > crop(idle, "MELON").score
    busy = make_state(obs_no_hands, day=0, hour=0, tiles=field(5))
    assert crop(busy, "MELON").score > animal(busy, "GOOSE").score


# --- Land ----------------------------------------------------------------------------------------


def test_land_cost_is_zero_with_abundant_tiles_and_rises_with_scarcity():
    assert economy.land_cost(10, LAND_SCARCITY_FREE_TILES) == 0.0
    assert economy.land_cost(10, 25) == 0.0
    scarce = economy.land_cost(10, 2)
    scarcer = economy.land_cost(10, 0)
    assert 0 < scarce < scarcer
    assert economy.land_cost(20, 2) == pytest.approx(2 * scarce)  # longer occupancy costs more


def test_scarce_land_lowers_long_occupancy_crop_estimates(obs_no_hands):
    weeds = {(x, y): {"kind": "WEED"} for x in range(5) for y in range(5)}
    for pos in [(4, 4), (3, 4)]:
        weeds.pop(pos)
    scarce = make_state(obs_no_hands, day=1, hour=0, tiles=weeds)
    roomy = make_state(obs_no_hands, day=1, hour=0)
    assert crop(scarce, "MELON").land_cost > crop(roomy, "MELON").land_cost == 0.0
    assert crop(scarce, "MELON").land_cost > crop(scarce, "WHEAT").land_cost > 0


# --- Shed ----------------------------------------------------------------------------------------


def test_shed_pressure_thresholds(obs_no_hands):
    low = make_state(obs_no_hands, shed={"WHEAT": 40, "EGG": 30})
    assert shed_occupancy(low) == 70 and shed_free_capacity(low) == 30 and shed_pressure(low) == 0.0
    at_start = make_state(obs_no_hands, shed={"WHEAT": SHED_PRESSURE_START})
    assert shed_pressure(at_start) == 0.0
    mid = make_state(obs_no_hands, shed={"WHEAT": 90})
    assert 0.0 < shed_pressure(mid) < 1.0
    emergency = make_state(obs_no_hands, shed={"WHEAT": SHED_EMERGENCY})
    assert shed_pressure(emergency) == 1.0
    assert shed_pressure(make_state(obs_no_hands, shed={"WHEAT": 100})) == 1.0


def test_overflow_risk_counts_carried_units_beyond_free_capacity(obs_no_hands):
    state = make_state(obs_no_hands, shed={"WHEAT": 96}, inventory={"MELON": 6})
    assert overflow_risk(state) == 2
    assert overflow_risk(make_state(obs_no_hands, shed={"WHEAT": 50}, inventory={"MELON": 6})) == 0


# --- Sell/hold ------------------------------------------------------------


def test_wheat_needed_for_animal_feed_is_retained(obs_no_hands):
    tiles = {(1, 1): raw_animal(fed_today=True)}
    state = make_state(obs_no_hands, day=6, hour=0, tiles=tiles, shed={"WHEAT": 10})
    assert economy.sell_plan(state) == {"WHEAT": 10 - FEED_WHEAT_RESERVE_PER_ANIMAL}
    scarce = make_state(obs_no_hands, day=6, hour=0, tiles=tiles, shed={"WHEAT": 1})
    assert "WHEAT" not in economy.sell_plan(scarce)


def test_excess_products_sell_immediately_without_speculation(obs_no_hands):
    state = make_state(obs_no_hands, day=6, hour=0, shed={"WHEAT": 3, "MELON": 6, "EGG": 4})
    assert economy.sell_plan(state) == {"WHEAT": 3, "MELON": 6, "EGG": 4}
    # A higher or lower current price never causes holding for a better future price.
    low = make_state(obs_no_hands, day=6, hour=0, shed={"MELON": 6}, prices={"MELON": 5})
    assert economy.sell_plan(low) == {"MELON": 6}


def test_fertilizer_is_held_only_for_a_positive_fertilize_opportunity(obs_no_hands):
    tomato = {
        (4, 3): raw_plant(crop="TOMATO", planted_day=0, watered_today=True, consecutive_unwatered=0)
    }
    state = make_state(obs_no_hands, day=8, hour=0, tiles=tomato, shed={"FERTILIZER": 3})
    plan = economy.sell_plan(state)
    assert plan.get("FERTILIZER", 0) == 2  # one unit kept for the tomato's production window
    idle = make_state(obs_no_hands, day=8, hour=0, shed={"FERTILIZER": 3})
    assert economy.sell_plan(idle) == {"FERTILIZER": 3}


def test_nonessential_stock_sells_readily_under_shed_pressure(obs_no_hands):
    tomato = {
        (4, 3): raw_plant(crop="TOMATO", planted_day=0, watered_today=True, consecutive_unwatered=0)
    }
    packed = make_state(
        obs_no_hands, day=7, hour=0, tiles=tomato, shed={"FERTILIZER": 3, "EGG": 93}
    )
    assert shed_pressure(packed) == 1.0
    assert economy.sell_plan(packed) == {"FERTILIZER": 3, "EGG": 93}  # nothing held back but feed


def test_final_day_releases_the_feed_reserve(obs_no_hands):
    tiles = {(1, 1): raw_animal(fed_today=True)}
    state = make_state(obs_no_hands, day=29, hour=0, tiles=tiles, shed={"WHEAT": 2})
    assert economy.sell_plan(state) == {"WHEAT": 2}


# --- Fertilizer economics ------------------------------------------------------------------------


def test_fertilizer_selected_only_when_incremental_yield_exceeds_its_cost(obs_no_hands):
    tomato = raw_plant(crop="TOMATO", planted_day=0, watered_today=True, consecutive_unwatered=0)
    state = make_state(obs_no_hands, day=8, hour=0, tiles={(4, 3): tomato})
    est = economy.estimate_fertilize(state, P(4, 3), state.me.tiles[3][4])
    assert est.expected_units == 3  # productions at ages 8, 9, 10 fall in the 3-day window
    assert est.expected_revenue == 3 * state.market.prices["TOMATO"]
    assert est.setup_cash == state.market.prices["FERTILIZER"] + 1 and est.expected_net_value > 0
    # Wheat: +2 units at 25 each never pays for ~100 of fertilizer plus actions.
    wheat = raw_plant(crop="WHEAT", planted_day=0, watered_today=True, consecutive_unwatered=0)
    state = make_state(obs_no_hands, day=2, hour=0, tiles={(4, 3): wheat})
    est = economy.estimate_fertilize(state, P(4, 3), state.me.tiles[3][4])
    assert est.expected_units == 2 and est.expected_net_value < 0
    # Melon already reaches its cap unfertilized: no incremental units.
    melon = raw_plant(crop="MELON", planted_day=0, watered_today=True, consecutive_unwatered=0)
    state = make_state(obs_no_hands, day=6, hour=0, tiles={(4, 3): melon})
    est = economy.estimate_fertilize(state, P(4, 3), state.me.tiles[3][4])
    assert est.expected_units == 0 and est.realization_probability == 0.0


def test_held_fertilizer_carries_its_sale_value_as_opportunity_cost(obs_no_hands):
    tomato = raw_plant(crop="TOMATO", planted_day=0, watered_today=True, consecutive_unwatered=0)
    held = make_state(obs_no_hands, day=8, hour=0, tiles={(4, 3): tomato}, shed={"FERTILIZER": 1})
    est = economy.estimate_fertilize(held, P(4, 3), held.me.tiles[3][4])
    assert est.setup_cash == 0 and est.input_cost == held.market.prices["FERTILIZER"]


# --- Ranking hygiene ------------------------------------------------------


def test_ranking_is_sorted_deterministic_and_covers_all_types(obs_no_hands):
    state = make_state(obs_no_hands, day=1, hour=0)
    ranked = economy.rank_opportunities(state)
    assert [e.score for e in ranked] == sorted((e.score for e in ranked), reverse=True)
    assert {e.product for e in ranked if e.kind is OpportunityKind.CROP} == set(CROPS)
    assert {e.product for e in ranked if e.kind is OpportunityKind.ANIMAL} == set(ANIMALS)
    assert ranked == economy.rank_opportunities(state)


def test_no_future_model_leakage(obs_no_hands):
    """Fabricated unobservable opponent/town data must not change estimates."""
    import copy

    base = make_state(obs_no_hands, day=1, hour=0)
    obs = copy.deepcopy(obs_no_hands)
    obs["farms"][1]["hidden_shed"] = {"MELON": 999}  # unknown key: ignored by the parser
    obs["town"]["unlocked_shops"] = ["FARMERS_MARKET", "PET_CAFE", "YARN_STORE"]
    obs["farms"][1]["money"] = 999999.0
    altered = make_state(obs, day=1, hour=0)
    assert economy.rank_opportunities(altered) == economy.rank_opportunities(base)
    assert economy.cash_reserve(altered) == economy.cash_reserve(base)


def test_fertilizer_does_not_double_count_days_already_covered(obs_no_hands):
    """An ongoing crop fertilized on day 8 (covering days 8-10) gains only the
    uncovered production day when re-fertilized on day 9."""
    tomato = raw_plant(crop="TOMATO", planted_day=0, watered_today=True, consecutive_unwatered=0)
    tomato["fertilized_until_day"] = 10
    state = make_state(obs_no_hands, day=9, hour=0, tiles={(4, 3): tomato})
    est = economy.estimate_fertilize(state, P(4, 3), state.me.tiles[3][4])
    assert est.expected_units == 1  # only age 11 (day 11) is incremental
    tomato["fertilized_until_day"] = 11
    state = make_state(obs_no_hands, day=9, hour=0, tiles={(4, 3): tomato})
    est = economy.estimate_fertilize(state, P(4, 3), state.me.tiles[3][4])
    assert est.expected_units == 0 and est.realization_probability == 0.0
    tomato["fertilized_until_day"] = -1
    state = make_state(obs_no_hands, day=9, hour=0, tiles={(4, 3): tomato})
    assert economy.estimate_fertilize(state, P(4, 3), state.me.tiles[3][4]).expected_units == 3
