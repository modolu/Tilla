"""Tests for the Milestone 5 market and town model (economy.py market section).

Town-demand matrix, price/revenue estimates, market history and trend, own
supply pipeline, projected inventory, scarcity/glut scoring, premium
protection, opportunity integration and the sell/hold model.
"""

import copy

import pytest

from kaggriculture_bot import economy
from kaggriculture_bot.constants import (
    HOLD_HORIZON_TURNS,
    MARKET_HISTORY_TURNS,
    MARKET_I0,
    MARKET_PARAMS,
    PREMIUM_PRODUCTS,
    SELL_HOLD_MIN_UPLIFT,
    SHOPS,
    TREND_CAP_FRACTION_OF_T,
    TREND_DAMPING,
    TREND_WINDOW_TURNS,
)
from kaggriculture_bot.models import MarketPressure, MarketSnapshot, OpportunityKind
from kaggriculture_bot.runtime import get_episode_memory, remember_turn, reset_episode_memory
from tests.conftest import make_state, raw_animal, raw_plant

ALL_SHOPS = tuple(SHOPS)


def state_at(obs, day, hour, **kw):
    return make_state(obs, day=day, hour=hour, **kw)


# --- Town demand matrix -------------------------------------------------------------------------


def test_town_center_next_tick_and_this_turn_tick(obs_no_hands):
    tick = state_at(obs_no_hands, 6, 0)  # step 144 % 12 == 0: this turn's orders precede the tick
    assert economy.expected_town_demand(tick, "WHEAT", 1).town_center == 1
    assert economy.expected_town_demand(tick, "WHEAT", 12).town_center == 1
    assert economy.expected_town_demand(tick, "WHEAT", 13).town_center == 2
    off = state_at(obs_no_hands, 6, 5)  # step 149: next tick at 156, 7 turns away
    assert economy.expected_town_demand(off, "WHEAT", 7).town_center == 0
    assert economy.expected_town_demand(off, "WHEAT", 8).town_center == 1


def test_town_center_quantity_follows_the_day_of_each_tick(obs_no_hands):
    late9 = state_at(obs_no_hands, 9, 13)  # step 229: ticks at 240 (day 10) ...
    demand = economy.expected_town_demand(late9, "EGG", 24)
    assert demand.town_center == 2 + 2  # 240 and 252 are both day 10 -> 2 each
    late19 = state_at(obs_no_hands, 19, 23)  # step 479: ticks at 480 (day 20), 492
    assert economy.expected_town_demand(late19, "EGG", 14).town_center == 4 + 4
    early = state_at(obs_no_hands, 8, 23)  # step 215: 216 (day 9 -> 1), 228 (1), 240 (day 10 -> 2)
    assert economy.expected_town_demand(early, "EGG", 26).town_center == 1 + 1 + 2


def test_fertilizer_has_no_town_center_demand(obs_no_hands):
    state = state_at(obs_no_hands, 6, 0, shops=ALL_SHOPS)
    demand = economy.expected_town_demand(state, "FERTILIZER", 48)
    assert demand.town_center == 0 and demand.known_shops == 0 and demand.future_shops == 0.0


def test_known_shops_tick_every_four_turns_with_single_product_double(obs_no_hands):
    one = state_at(obs_no_hands, 6, 0, shops=("BAKERY",))  # step 144 % 4 == 0
    assert economy.expected_town_demand(one, "EGG", 4).known_shops == 1
    assert economy.expected_town_demand(one, "EGG", 5).known_shops == 2
    assert economy.expected_town_demand(one, "CARROT", 5).known_shops == 0
    many = state_at(obs_no_hands, 6, 0, shops=("BAKERY", "BRUNCH_SPOT", "PET_CAFE"))
    assert economy.expected_town_demand(many, "EGG", 1).known_shops == 2
    assert economy.expected_town_demand(many, "CARROT", 1).known_shops == 2  # single-product 2x
    assert economy.expected_town_demand(many, "WHEAT", 9).known_shops == 3 * 2


def test_future_unknown_shops_use_the_without_replacement_expectation(obs_no_hands):
    # Day 4 hour 0: the next unlock happens at the end of day 5 (active from day 6).
    state = state_at(obs_no_hands, 4, 0, shops=("BAKERY",))
    within = economy.expected_town_demand(state, "WOOL", 48)  # up to end of day 5: none yet
    assert within.future_shops == 0.0
    crossing = economy.expected_town_demand(state, "WOOL", 49)  # step 145: day 6, tick at 144? no
    remaining = [s for s in SHOPS if s != "BAKERY"]
    per_tick = sum(economy.shop_units(s, "WOOL") for s in remaining) / len(remaining)
    ticks_on_day6 = sum(1 for step in range(144, 96 + 49) if step % 4 == 0)
    assert crossing.future_shops == pytest.approx(per_tick * ticks_on_day6)
    assert per_tick == pytest.approx(2 / 7)  # only the Yarn Store (2x) wants wool
    # Two unknown unlocks inside the horizon double the expectation per tick.
    long = economy.expected_town_demand(state, "WOOL", 24 * 6)
    last_tick_units = economy.unknown_shop_unlocks(4, 9, 1) * per_tick
    assert economy.unknown_shop_unlocks(4, 9, 1) == 2 and last_tick_units == pytest.approx(4 / 7)
    assert long.future_shops > crossing.future_shops


def test_all_shops_unlocked_leaves_no_future_expectation(obs_no_hands):
    state = state_at(obs_no_hands, 24, 0, shops=ALL_SHOPS)
    demand = economy.expected_town_demand(state, "WHEAT", 72)
    assert demand.future_shops == 0.0
    assert economy.expected_unknown_shop_units("WHEAT", ALL_SHOPS) == 0.0
    assert economy.unknown_shop_unlocks(24, 30, len(ALL_SHOPS)) == 0


def test_unknown_unlock_count_is_bounded_by_the_pool(obs_no_hands):
    assert economy.unknown_shop_unlocks(0, 30, 0) == 8  # 9 unlock days, 8 shops
    assert economy.unknown_shop_unlocks(0, 3, 0) == 1  # end of day 2 only
    assert economy.unknown_shop_unlocks(3, 3, 1) == 0


# --- Price, revenue and pressure -------------------------------------------------------------


def test_bulk_sale_revenue_is_below_the_naive_quote_for_premium_goods():
    inv = MARKET_I0
    naive = 20 * economy.market_price_at_inventory("STRAWBERRY", inv)
    revenue, after = economy.estimate_sell_revenue("STRAWBERRY", 20, inv)
    assert revenue < naive and after == inv + 20
    wheat_naive = 20 * economy.market_price_at_inventory("WHEAT", inv)
    wheat_revenue, _ = economy.estimate_sell_revenue("WHEAT", 20, inv)
    assert (naive - revenue) / naive > (wheat_naive - wheat_revenue) / wheat_naive


def test_buy_cost_climbs_unit_by_unit():
    cost, after = economy.estimate_buy_cost("WHEAT", 10, MARKET_I0)
    assert after == MARKET_I0 - 10
    assert cost > 10 * economy.market_price_at_inventory("WHEAT", MARKET_I0 - 1)


def test_market_pressure_bands_follow_each_products_curve():
    assert economy.market_pressure("WHEAT", MARKET_I0) is MarketPressure.BALANCED
    assert economy.market_pressure("WHEAT", MARKET_I0 - 200) is MarketPressure.SCARCE
    assert economy.market_pressure("MELON", MARKET_I0 + 60) is MarketPressure.GLUT
    assert economy.market_pressure("MELON", MARKET_I0 + 110) is MarketPressure.SEVERE_GLUT
    assert economy.market_pressure("MELON", MARKET_I0 + 140) is MarketPressure.FLOOR_RISK
    # The same +140 units only dent wheat (log glut curve): a mild glut, no floor risk.
    assert economy.market_pressure("WHEAT", MARKET_I0 + 140) is MarketPressure.GLUT
    assert economy.market_pressure("WHEAT", MARKET_I0 + 10) is MarketPressure.BALANCED


def test_price_anchor_is_zero_in_the_pinned_environment_and_shifts_otherwise(obs_no_hands):
    state = state_at(obs_no_hands, 1, 0)
    assert all(economy.price_anchor(state, p) == 0 for p in economy.PRODUCTS)
    shifted = state_at(obs_no_hands, 1, 0, prices={"WHEAT": 60})
    assert economy.price_anchor(shifted, "WHEAT") == 35
    assert economy.market_price_at_inventory("WHEAT", MARKET_I0, 35) == 60


# --- Market history and trend -------------------------------------------------------------------


def snapshot(step, inventory, shops=()):
    return MarketSnapshot(step=step, day=step // 24, inventory=inventory, unlocked_shops=shops)


def test_market_history_is_bounded_reset_and_player_isolated(obs_no_hands, obs_seat1_step1):
    from kaggriculture_bot.parser import parse_observation

    memory = reset_episode_memory(0)
    for step in range(30):
        obs = copy.deepcopy(obs_no_hands)
        obs["step"] = step
        obs["market"]["inventory"]["WHEAT"] = MARKET_I0 - step
        remember_turn(memory, parse_observation(obs))
    assert len(memory.market_history) == MARKET_HISTORY_TURNS
    assert memory.market_history[-1].step == 29 and memory.market_history[0].step == 6
    assert memory.market_history[-1].inventory["WHEAT"] == MARKET_I0 - 29
    # Snapshots copy the inventory (no aliasing) and record the shops.
    obs["market"]["inventory"]["WHEAT"] = 0
    assert memory.market_history[-1].inventory["WHEAT"] == MARKET_I0 - 29
    assert memory.market_history[-1].unlocked_shops == tuple(obs["town"]["unlocked_shops"])
    # Same step twice is recorded once; step 0 starts a fresh (empty) history.
    remember_turn(memory, parse_observation(obs))
    assert len(memory.market_history) == MARKET_HISTORY_TURNS
    assert get_episode_memory(0, step=0).market_history == memory.market_history.__class__(
        maxlen=24
    )
    other = get_episode_memory(1, step=1)
    remember_turn(other, parse_observation(obs_seat1_step1))
    assert len(other.market_history) == 1 and len(get_episode_memory(0, step=1).market_history) == 0


def test_trend_is_zero_for_flat_or_missing_history():
    memory = reset_episode_memory(0)
    assert economy.market_trend(None, "WHEAT") == 0.0
    assert economy.market_trend(memory, "WHEAT") == 0.0
    for step in range(1, 12):
        memory.market_history.append(snapshot(step, {"WHEAT": MARKET_I0}))
    assert economy.market_trend(memory, "WHEAT") == 0.0


def test_trend_removes_scheduled_town_consumption():
    """Inventory falling only by the town center's own ticks is a flat market."""
    memory = reset_episode_memory(0)
    inv = MARKET_I0
    for step in range(0, 14):
        memory.market_history.append(snapshot(step, {"WHEAT": inv}, ("BAKERY",)))
        if step % 4 == 0:
            inv -= 1  # bakery
        if step % 12 == 0:
            inv -= 1  # town center day 0
    assert economy.market_trend(memory, "WHEAT") == 0.0


def test_steady_inflow_and_drawdown_are_damped_and_capped():
    memory = reset_episode_memory(0)
    for step in range(1, 14):
        memory.market_history.append(snapshot(step, {"WHEAT": MARKET_I0 + 2 * step}))
    assert economy.market_trend(memory, "WHEAT") == pytest.approx(2 * TREND_DAMPING)
    memory = reset_episode_memory(0)
    for step in range(1, 14):
        memory.market_history.append(snapshot(step, {"WHEAT": MARKET_I0 - 3 * step}))
    assert economy.market_trend(memory, "WHEAT") == pytest.approx(-3 * TREND_DAMPING)
    memory = reset_episode_memory(0)
    for step in range(1, 14):
        memory.market_history.append(snapshot(step, {"MELON": MARKET_I0 + 50 * step}))
    cap = TREND_CAP_FRACTION_OF_T * MARKET_PARAMS["MELON"][1]
    assert economy.market_trend(memory, "MELON") == pytest.approx(cap * TREND_DAMPING)


def test_one_turn_dump_or_scarcity_shock_does_not_dominate():
    memory = reset_episode_memory(0)
    inv = MARKET_I0
    for step in range(1, 14):
        if step == 7:
            inv += 40  # one dump
        memory.market_history.append(snapshot(step, {"STRAWBERRY": inv}))
    assert economy.market_trend(memory, "STRAWBERRY") == 0.0  # median of flat deltas
    memory = reset_episode_memory(0)
    inv = MARKET_I0
    for step in range(1, 14):
        if step == 7:
            inv -= 40  # one scarcity shock
        memory.market_history.append(snapshot(step, {"STRAWBERRY": inv}))
    assert economy.market_trend(memory, "STRAWBERRY") == 0.0
    assert len(memory.market_history) <= TREND_WINDOW_TURNS + 1


# --- Own supply pipeline -----------------------------------------------------------------------


def test_own_supply_counts_shed_carried_and_maturing_crops_once(obs_no_hands):
    tiles = {
        (1, 1): raw_plant(crop="WHEAT", planted_day=2, watered_today=True, consecutive_unwatered=0),
        (2, 2): raw_plant(crop="WHEAT", planted_day=5, watered_today=True, consecutive_unwatered=0),
    }
    state = state_at(obs_no_hands, 5, 0, tiles=tiles, shed={"WHEAT": 4}, inventory={"WHEAT": 2})
    # (1,1) is age 3 (yield 1 in the fixture builder) and matures within a day; (2,2) is fresh.
    assert economy.own_supply(state, "WHEAT", 24) == 4 + 2 + 4
    assert economy.own_supply(state, "WHEAT", 1) == 4 + 2 + 1  # only current yield counts today
    assert economy.own_supply(state, "CARROT", 48) == 0


def test_own_supply_uses_base_scheduled_production_only(obs_no_hands):
    tomato = raw_plant(
        crop="TOMATO", planted_day=0, watered_today=True, consecutive_unwatered=0, yield_units=0
    )
    tomato["fertilized_until_day"] = 12  # fertilizer bonus is never assumed
    goose = raw_animal(fed_today=True, yield_units=1)
    goose["pending_care_bonus"] = 3  # CARE bonus is never guaranteed
    state = state_at(obs_no_hands, 8, 0, tiles={(1, 1): tomato, (2, 2): goose})
    assert economy.own_supply(state, "TOMATO", 24) == 1  # production at age 9 (day 9)
    assert economy.own_supply(state, "TOMATO", 72) == 3  # ages 9, 10, 11
    assert economy.own_supply(state, "EGG", 24) == 1 + 1  # held egg + tomorrow's base egg


# --- Projected inventory and same-turn ordering ------------------------------------------------


def test_projected_inventory_combines_trend_town_and_own_supply(obs_no_hands):
    state = state_at(obs_no_hands, 6, 0, shops=("BAKERY",))
    memory = reset_episode_memory(0)
    for step in range(132, 145):
        memory.market_history.append(snapshot(step, {"EGG": MARKET_I0 + 4 * (step - 132)}))
    horizon = 13
    demand = economy.expected_town_demand(state, "EGG", horizon)
    trend = economy.market_trend(memory, "EGG") * horizon
    expected = round(MARKET_I0 + trend - demand.total + 5)
    assert economy.projected_inventory(state, memory, "EGG", horizon, extra_supply=5) == expected
    assert demand.town_center == 2 and demand.known_shops == 4  # 144 and 156; 144,148,152,156


def test_selling_before_a_tick_versus_after_it_changes_expected_revenue(obs_no_hands):
    """Market runs before town consumption: this turn's sale is priced at the
    current inventory; waiting one turn sells after the tick's demand (§20)."""
    state = state_at(obs_no_hands, 6, 0, shed={"WHEAT": 3})  # step 144 is a tick
    now, _ = economy.estimate_sell_revenue("WHEAT", 3, state.market.inventory["WHEAT"])
    later_inv = economy.projected_inventory(state, None, "WHEAT", 1)
    later, _ = economy.estimate_sell_revenue("WHEAT", 3, later_inv)
    assert later_inv == MARKET_I0 - 1 and later > now
    outlook = economy.market_outlook(state, None, "WHEAT", 3)
    assert outlook.horizon_turns == 1 and outlook.hold >= 1


# --- Scarcity/glut scenarios (TILLA_STRATEGY.md §8, §10, §13) --------------------------------


def crop(state, name, memory=None):
    return economy.estimate_crop(state, name, memory)


def test_wheat_scarcity_strengthens_the_wheat_opportunity(obs_no_hands):
    base = state_at(obs_no_hands, 1, 0)
    scarce = state_at(obs_no_hands, 1, 0, inventory_market={"WHEAT": MARKET_I0 - 300})
    assert crop(scarce, "WHEAT").score > crop(base, "WHEAT").score
    assert crop(scarce, "WHEAT").expected_revenue > crop(base, "WHEAT").expected_revenue


def test_carrot_glut_makes_wheat_preferable(obs_no_hands):
    glut = state_at(obs_no_hands, 1, 0, inventory_market={"CARROT": MARKET_I0 + 400})
    assert crop(glut, "WHEAT").score > crop(glut, "CARROT").score
    fair = state_at(obs_no_hands, 1, 0)
    assert crop(fair, "CARROT").score > crop(fair, "WHEAT").score  # the glut flips the M3 order


def test_tomato_repeated_production_is_unattractive_into_a_worsening_glut(obs_no_hands):
    fair = state_at(obs_no_hands, 1, 0)
    memory = reset_episode_memory(0)
    for step in range(12, 25):
        memory.market_history.append(snapshot(step, {"TOMATO": MARKET_I0 + 6 * (step - 12)}))
    glutting = state_at(obs_no_hands, 1, 0, inventory_market={"TOMATO": MARKET_I0 + 150})
    assert crop(glutting, "TOMATO", memory).score < crop(fair, "TOMATO").score
    assert crop(glutting, "TOMATO", memory).market_penalty > crop(fair, "TOMATO").market_penalty


def test_strawberry_high_price_but_self_induced_glut_is_penalized_or_rejected(obs_no_hands):
    """The quote is high (scarce market) but we already hold 140 strawberries that
    will be sold ahead of any new planting's output: the new tile's revenue is
    projected into our own glut and penalized; a flooded pipeline is rejected."""
    market = {"STRAWBERRY": MARKET_I0 - 40}
    lone = state_at(obs_no_hands, 1, 0, inventory_market=market)
    assert lone.market.prices["STRAWBERRY"] > MARKET_PARAMS["STRAWBERRY"][0]
    stocked = state_at(obs_no_hands, 1, 0, inventory_market=market, shed={"STRAWBERRY": 140})
    est, alone = crop(stocked, "STRAWBERRY"), crop(lone, "STRAWBERRY")
    assert est.market_penalty > 0 and est.market_penalty > alone.market_penalty
    assert est.score < alone.score
    flooded = state_at(obs_no_hands, 1, 0, inventory_market=market, shed={"STRAWBERRY": 300})
    assert crop(flooded, "STRAWBERRY").reason == "premium glut protection"


def test_strawberry_scarcity_with_town_demand_can_rank_highly(obs_no_hands):
    state = state_at(
        obs_no_hands, 3, 0, inventory_market={"STRAWBERRY": MARKET_I0 - 60},
        shops=("BRUNCH_SPOT", "ICE_CREAM_SHOP", "SMOOTHIE_SHOP"),
    )  # fmt: skip
    ranked = economy.rank_opportunities(state)
    top_crops = [e.product for e in ranked if e.kind is OpportunityKind.CROP][:2]
    assert "STRAWBERRY" in top_crops
    assert crop(state, "STRAWBERRY").market_penalty >= 0  # projected uplift is never bonus money


def test_melon_nominal_price_high_but_severe_projected_glut_is_rejected(obs_no_hands):
    field = {
        (x, y): raw_plant(crop="MELON", planted_day=0, watered_today=True, consecutive_unwatered=0)
        for x in range(5)
        for y in range(4)
    }
    state = state_at(obs_no_hands, 1, 0, tiles=field, inventory_market={"MELON": MARKET_I0 + 60})
    est = crop(state, "MELON")
    assert est.realization_probability == 0.0 and est.reason == "premium glut protection"
    assert economy.premium_glut_blocked(state, None, "MELON", 6, 240)


def test_cow_investment_rejected_in_milk_glut(obs_no_hands):
    fair = state_at(obs_no_hands, 2, 0, money=5000)
    glut = state_at(obs_no_hands, 2, 0, money=5000, inventory_market={"MILK": MARKET_I0 + 130})
    assert economy.estimate_animal(glut, "COW").realization_probability == 0.0
    assert economy.estimate_animal(glut, "COW").reason == "premium glut protection"
    assert economy.estimate_animal(fair, "COW").realization_probability == 1.0


def test_sheep_attractive_under_wool_scarcity_and_yarn_store(obs_no_hands):
    base = state_at(obs_no_hands, 2, 0, money=5000)
    scarce = state_at(
        obs_no_hands,
        2,
        0,
        money=5000,
        inventory_market={"WOOL": MARKET_I0 - 80},
        shops=("YARN_STORE",),
    )
    assert (
        economy.estimate_animal(scarce, "SHEEP").score
        > economy.estimate_animal(base, "SHEEP").score
    )
    assert economy.estimate_animal(scarce, "SHEEP").market_penalty >= 0


def test_premium_good_near_floor_gets_strong_protection(obs_no_hands):
    for product, inv in (("WOOL", MARKET_I0 + 90), ("MILK", MARKET_I0 + 150)):
        state = state_at(obs_no_hands, 2, 0, money=5000, inventory_market={product: inv})
        assert economy.market_pressure(product, inv) in (
            MarketPressure.SEVERE_GLUT,
            MarketPressure.FLOOR_RISK,
        )
        animal = "SHEEP" if product == "WOOL" else "COW"
        assert economy.estimate_animal(state, animal).realization_probability == 0.0
    assert set(PREMIUM_PRODUCTS) == {"STRAWBERRY", "MELON", "MILK", "WOOL"}


def test_town_consumption_can_turn_a_temporary_glut_into_a_hold(obs_no_hands):
    """Eggs 3 above equilibrium with two egg shops and the town center inside the
    horizon: the projected price after the tick beats selling now, so eggs are held."""
    state = state_at(
        obs_no_hands, 10, 0, shed={"EGG": 3}, inventory_market={"EGG": MARKET_I0 + 3},
        shops=("BAKERY", "BRUNCH_SPOT"),
    )  # fmt: skip
    outlook = economy.market_outlook(state, None, "EGG", 3)
    assert outlook.demand.total >= 4 and outlook.projected_price > outlook.price
    assert outlook.hold >= 1 and "town tick" in outlook.reason


# --- Sell/hold scenarios (TILLA_STRATEGY.md §13, §17) ------------------------------------------


def test_sell_now_when_price_is_projected_to_worsen(obs_no_hands):
    memory = reset_episode_memory(0)
    for step in range(132, 145):
        memory.market_history.append(snapshot(step, {"MELON": MARKET_I0 + 4 * (step - 132)}))
    state = state_at(
        obs_no_hands, 6, 0, shed={"MELON": 8}, inventory_market={"MELON": MARKET_I0 + 48}
    )
    outlook = economy.market_outlook(state, memory, "MELON", 8)
    assert outlook.trend_per_turn > 0 and outlook.projected_price <= outlook.price
    assert outlook.sell_now == 8 and outlook.hold == 0


def test_no_hold_in_shed_emergency_or_on_the_final_days(obs_no_hands):
    tick = state_at(obs_no_hands, 6, 0, shed={"WHEAT": 60, "EGG": 36})
    assert economy.shed_occupancy(tick) == 96
    plan = economy.sell_plan(tick)
    assert plan == {"WHEAT": 60, "EGG": 36}
    final = state_at(obs_no_hands, 29, 0, shed={"WHEAT": 3, "MELON": 6})
    assert economy.sell_plan(final) == {"WHEAT": 3, "MELON": 6}
    liquidation = state_at(obs_no_hands, 27, 12, shed={"WHEAT": 3})
    assert economy.market_outlooks(liquidation)["WHEAT"].reason.startswith("liquidation")


def test_partial_sale_restores_the_cash_reserve(obs_no_hands):
    state = state_at(obs_no_hands, 6, 0, shed={"WHEAT": 30}, money=250)
    outlooks = economy.market_outlooks(state)
    o = outlooks["WHEAT"]
    assert o.sell_now_revenue >= economy.cash_reserve(state) - 250 or o.hold == 0
    assert "cash reserve" in o.reason or o.hold == 0


def test_partial_sale_creates_shed_space_most_glutted_first(obs_no_hands):
    state = state_at(
        obs_no_hands, 6, 0, shed={"WHEAT": 50, "EGG": 38},
        inventory_market={"EGG": MARKET_I0 + 40},
    )  # fmt: skip
    outlooks = economy.market_outlooks(state)
    sold = sum(o.sell_now for o in outlooks.values())
    assert 88 - sold <= 84
    assert outlooks["EGG"].reason.startswith("shed pressure") or outlooks["EGG"].hold == 0


def test_quantity_aware_bulk_sale_avoids_optimistic_valuation():
    naive = 20 * economy.market_price_at_inventory("WOOL", MARKET_I0)
    revenue, _ = economy.estimate_sell_revenue("WOOL", 20, MARKET_I0)
    assert revenue < naive
    revenue60, _ = economy.estimate_sell_revenue("WOOL", 60, MARKET_I0)
    assert revenue60 < 60 * economy.market_price_at_inventory("WOOL", MARKET_I0) * 0.8


def test_premium_glut_liquidates_but_scarce_premium_can_be_held(obs_no_hands):
    glut = state_at(
        obs_no_hands, 6, 0, shed={"WOOL": 5}, inventory_market={"WOOL": MARKET_I0 + 100}
    )
    assert economy.sell_plan(glut) == {"WOOL": 5}
    scarce = state_at(
        obs_no_hands, 12, 0, shed={"MILK": 5}, inventory_market={"MILK": MARKET_I0 - 10},
        shops=("PIZZA_SHOP", "ICE_CREAM_SHOP", "SMOOTHIE_SHOP"),
    )  # fmt: skip
    outlook = economy.market_outlook(scarce, None, "MILK", 5)
    assert outlook.demand.known_shops >= 3 and outlook.projected_price > outlook.price
    assert outlook.hold >= 1


def test_hold_requires_the_minimum_uplift(obs_no_hands):
    inv = MARKET_I0
    later = inv - 1
    now_price = economy.market_price_at_inventory("EGG", inv)
    later_price = economy.market_price_at_inventory("EGG", later)
    assert later_price <= now_price * (1 + SELL_HOLD_MIN_UPLIFT)
    sell_now, hold, _, _ = economy.split_sale("EGG", 3, inv, later)
    assert (sell_now, hold) == (3, 0)
    assert HOLD_HORIZON_TURNS == 13


# --- No opponent leakage (M6 owns opponent inference) ------------------------------------------


def test_market_decisions_ignore_opponent_state(obs_no_hands):
    base_obs = copy.deepcopy(obs_no_hands)
    base = make_state(base_obs, day=6, hour=0, shed={"WHEAT": 5, "MELON": 4})
    altered_obs = copy.deepcopy(obs_no_hands)
    altered_obs["farms"][1]["money"] = 999999.0
    altered_obs["farms"][1]["hands"] = [[5, 4], [4, 5]]
    altered_obs["farms"][1]["hires_today"] = 2
    for y in range(5):
        for x in range(5):
            altered_obs["farms"][1]["tiles"][y][x] = raw_plant(
                crop="MELON", planted_day=0, watered_today=True, consecutive_unwatered=0
            )
    altered_obs["farms"][1]["hidden_shed"] = {"MELON": 500}
    altered = make_state(altered_obs, day=6, hour=0, shed={"WHEAT": 5, "MELON": 4})
    memory = reset_episode_memory(0)
    assert economy.market_outlooks(base, memory) == economy.market_outlooks(altered, memory)
    assert economy.rank_opportunities(base, memory) == economy.rank_opportunities(altered, memory)


def test_market_model_source_never_reads_the_opponent_farm():
    import inspect

    source = inspect.getsource(economy)
    market = source[source.index("# --- Market model") : source.index("# --- Cost primitives")]
    assert "state.opponent" not in market
    sell = source[source.index("# --- Sell/hold (Milestone 5") :]
    assert "state.opponent" not in sell


# --- Investment quantity follows the marginal tile (TILLA_STRATEGY.md §13) ------------------


def test_plantable_tiles_stop_where_the_marginal_melon_sells_into_its_own_glut(obs_no_hands):
    state = state_at(obs_no_hands, 1, 0, money=9000, inventory_market={"MELON": MARKET_I0 + 40})
    tiles = economy.plantable_tiles(state, "MELON", None, 25)
    assert 1 <= tiles < 25
    # The tile after the cut-off is worthless or glut-protected; the ones before are not.
    last_ok = economy.estimate_crop(state, "MELON", None, extra_tiles=tiles - 1)
    first_bad = economy.estimate_crop(state, "MELON", None, extra_tiles=tiles)
    assert last_ok.score > 0 and last_ok.realization_probability > 0
    assert first_bad.score <= 0 or first_bad.realization_probability == 0
    assert economy.plantable_tiles(state, "WHEAT", None, 25) == 25  # wheat's log glut curve is flat


def test_seed_purchases_are_sized_by_the_marginal_tile_and_held_seeds(obs_no_hands):
    from kaggriculture_bot.models import MarketOp, MarketOrder
    from kaggriculture_bot.runtime import reset_episode_memory
    from kaggriculture_bot.strategy import choose_plan

    # Cash for a few seeds only (no animal clears the reserve): at equilibrium the
    # affordable melons are bought; in a glutted melon market none are, and the
    # money goes to the best crop whose marginal tile still clears the bar.
    state = state_at(obs_no_hands, 1, 0, money=300 + 80 * 3)
    plan = choose_plan(state, reset_episode_memory(0))
    buys = [o for o in plan.market if o.op is MarketOp.BUY_SEED]
    assert buys == [MarketOrder(MarketOp.BUY_SEED, "MELON", 3)]
    glut = state_at(
        obs_no_hands, 1, 0, money=300 + 80 * 3, inventory_market={"MELON": MARKET_I0 + 160}
    )
    assert economy.plantable_tiles(glut, "MELON", None, 3) == 0
    plan = choose_plan(glut, reset_episode_memory(0))
    buys = [o for o in plan.market if o.op is MarketOp.BUY_SEED]
    assert not any(o.item == "MELON" for o in buys)
    # Seeds of another crop already waiting to be planted take tiles first.
    holding = state_at(obs_no_hands, 1, 0, money=300 + 80 * 25, seeds={"WHEAT": 20})
    plan = choose_plan(holding, reset_episode_memory(0))
    buys = [o for o in plan.market if o.op is MarketOp.BUY_SEED]
    assert not buys or buys[0].quantity <= 5


def test_held_seeds_are_planted_most_valuable_crop_first(obs_no_hands):
    from kaggriculture_bot.models import ObjectiveKind
    from kaggriculture_bot.runtime import reset_episode_memory
    from kaggriculture_bot.strategy import choose_plan

    state = state_at(obs_no_hands, 3, 4, seeds={"CARROT": 3, "MELON": 3, "STRAWBERRY": 3})
    plan = choose_plan(state, reset_episode_memory(0))
    plants = [o for o in plan.all_objectives() if o.kind is ObjectiveKind.PLANT]
    scores = {c: economy.estimate_crop(state, c).score for c in ("CARROT", "MELON", "STRAWBERRY")}
    assert [o.item for o in plants] == sorted(scores, key=lambda c: (-scores[c], c))
    assert plants[0].targets[0] == economy.Position(4, 4)  # best crop gets the nearest tile
