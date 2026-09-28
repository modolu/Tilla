"""ROI, opportunity scores, cash reserve, production and market-value estimates.

Milestone 3 economic model. Every opportunity is scored with the common
equation from TILLA_STRATEGY.md §7::

    expected_net_value = expected_revenue + byproduct_value
                         - setup_cost - input_cost - labor_cost - land_cost
                         - market_penalty - execution_risk
    score = expected_net_value * realization_probability * phase_weight
            / max(1, turns_to_realize)

Inputs are the typed ``GameState`` and verified mechanics only (crop/animal
tables, current observable market prices, remaining season, our own farm and
shed). Market-glut and phase terms are neutral parameters until the market
(M5) and opponent (M6) models exist. No movement, no action formatting, no
opponent inference, no hidden state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from kaggriculture_bot.constants import (
    ANIMAL_DAILY_ACTIONS,
    ANIMALS,
    CROPS,
    EXECUTION_RISK_PER_DAY,
    FARMER_DAILY_ACTION_BUDGET,
    FEED_WHEAT_RESERVE_PER_ANIMAL,
    FERTILIZER,
    FERTILIZER_BYPRODUCT_REALIZATION,
    HAND_ACTIONS_PER_JOB,
    HAND_DAILY_ACTIONS,
    HAND_SETUP_ACTIONS,
    HOLD_HORIZON_TURNS,
    LABOR_COST_PER_ACTION,
    LAND_SCARCITY_FREE_TILES,
    LAND_TILE_DAY_VALUE,
    LAST_DAY,
    LIQUIDATE_START_DAY,
    MARKET_I0,
    MARKET_PARAMS,
    MAX_DAILY_HIRES,
    MAX_SCARCITY_UPLIFT_FRACTION,
    MIN_HAND_USEFUL_ACTIONS,
    PHASE_WEIGHT,
    PLANNED_DAILY_HANDS,
    PLANT_DAILY_ACTIONS,
    PREMIUM_FLOOR_RISK_RATIO,
    PREMIUM_PRODUCTS,
    PRESSURE_GLUT_RATIO,
    PRESSURE_SCARCE_RATIO,
    PRESSURE_SEVERE_RATIO,
    PRICE_FLOOR,
    PRODUCTS,
    RESERVE_EMERGENCY_BUFFER,
    SELL_HOLD_MIN_UPLIFT,
    SHED_EMERGENCY,
    SHED_PRESSURE_START,
    SHOP_SELL_INTERVAL,
    SHOP_UNLOCK_INTERVAL,
    SHOPS,
    TOWN_CENTER_DEMAND_SCHEDULE,
    TOWN_CENTER_INTERVAL,
    TOWN_CENTER_PRODUCTS,
    TREND_CAP_FRACTION_OF_T,
    TREND_DAMPING,
    TREND_MAX_EXTRAPOLATION_TURNS,
    TREND_WINDOW_TURNS,
    TURNS_PER_DAY,
    WHEAT,
    CropSpec,
)
from kaggriculture_bot.features import (
    animals,
    can_act_from,
    empty_structures,
    empty_tiles,
    hand_spawn_positions,
    min_cash_reserve,
    plant_age,
    plants,
    shed_occupancy,
    shed_pressure,
    unlocked_tile_count,
)
from kaggriculture_bot.models import (
    EpisodeMemory,
    GameState,
    HiringDecision,
    MarketOp,
    MarketOrder,
    MarketOutlook,
    MarketPressure,
    OpportunityEstimate,
    OpportunityKind,
    PlantTile,
    Position,
    TileKind,
    TownDemand,
)

# The last day on which a harvest still leaves time to deliver and sell.
LAST_HARVEST_DAY = LAST_DAY - 1


def current_price(state: GameState, product: str) -> int:
    """Current observable market sale price (0 if the product is unknown)."""
    return state.market.prices.get(product, 0)


def buy_price(state: GameState, product: str) -> int:
    """Quote for buying one unit now; buy quotes use post-buy inventory, so add one coin."""
    return current_price(state, product) + 1


def order_cost(state: GameState, order: MarketOrder) -> int:
    """Cash a purchase order commits this turn (0 for sales; hires are priced by hire_cost)."""
    qty = order.quantity or 0
    if order.op is MarketOp.BUY_SEED and order.item in CROPS:
        return CROPS[order.item].seed * qty
    if order.op is MarketOp.BUY_ANIMAL and order.item in ANIMALS:
        return ANIMALS[order.item].cost * qty
    if order.op is MarketOp.BUY_PRODUCT and order.item is not None:
        inventory = state.market.inventory.get(order.item, MARKET_I0)
        cost, _ = estimate_buy_cost(order.item, qty, inventory, price_anchor(state, order.item))
        return cost
    return 0


# --- Market model (Milestone 5; TILLA_RULES.md §16-§19, TILLA_STRATEGY.md §13-§14) ----------
#
# Everything here is a deterministic estimate from the public shared-market
# state, verified town mechanics and our own farm. It never uses opponent
# private state or opponent farm state (M6 owns opponent attribution) and it
# never assumes the opponent's same-turn market queue.


def _shape(func: str, x: float) -> float:
    x = max(0.0, x)
    if func == "linear":
        return x
    if func == "sq":
        return x * x
    if func == "sqrt":
        return math.sqrt(x)
    if func == "log":
        return math.log(1.0 + x)
    if func == "log10":
        return math.log10(1.0 + x)
    return x


def market_price_at_inventory(product: str, inventory: int, anchor: int = 0) -> int:
    """The official market price of ``product`` at ``inventory`` units (exact
    pinned formula, rounded to the nearest coin, floored at PRICE_FLOOR).
    ``anchor`` shifts the curve so it passes through the price actually
    observed (see price_anchor); it is 0 in the pinned environment."""
    base, cap, below_func, below_target, above_func, above_target = MARKET_PARAMS[product]
    if inventory < MARKET_I0:
        amp = below_target * base / _shape(below_func, cap)
        price = base + amp * _shape(below_func, MARKET_I0 - inventory)
    else:
        amp = above_target * base / _shape(above_func, cap)
        price = base - amp * _shape(above_func, inventory - MARKET_I0)
    return max(PRICE_FLOOR, int(round(price)) + anchor)


def price_anchor(state: GameState, product: str) -> int:
    """Observed price minus the formula price at the observed inventory. Zero in
    the pinned environment; keeps estimates anchored to the real quote if the
    competition runtime ever used different market parameters."""
    observed = state.market.prices.get(product)
    if observed is None:
        return 0
    inventory = state.market.inventory.get(product, MARKET_I0)
    return observed - market_price_at_inventory(product, inventory)


def estimate_sell_revenue(
    product: str, quantity: int, inventory: int, anchor: int = 0
) -> tuple[int, int]:
    """Coins received for selling ``quantity`` units one at a time into a market
    holding ``inventory`` units, and the inventory afterwards: each unit is sold
    at the price of the pre-sale inventory and adds one unit unless it sold at
    the floor (official SELL semantics). An estimate: the opponent's interleaved
    orders are unknown."""
    revenue = 0
    for _ in range(max(0, quantity)):
        price = market_price_at_inventory(product, inventory, anchor)
        revenue += price
        if price > PRICE_FLOOR:
            inventory += 1
    return revenue, inventory


def estimate_buy_cost(
    product: str, quantity: int, inventory: int, anchor: int = 0
) -> tuple[int, int]:
    """Coins paid to buy ``quantity`` dynamic-price units one at a time (each at
    the post-buy inventory quote), and the inventory afterwards."""
    cost = 0
    for _ in range(max(0, quantity)):
        inventory -= 1
        cost += market_price_at_inventory(product, inventory, anchor)
    return cost, inventory


def town_center_units(day: int) -> int:
    for first_day, units in TOWN_CENTER_DEMAND_SCHEDULE:
        if day >= first_day:
            return units
    return 0


def shop_units(shop: str, product: str) -> int:
    products = SHOPS[shop]
    if product not in products:
        return 0
    return 2 if len(products) == 1 else 1


def unknown_shop_unlocks(day: int, until_day: int, already: int) -> int:
    """Shops that will have unlocked by ``until_day`` whose identity is not yet
    observable: one unlocks at the end of every day d with (d + 1) % 3 == 0,
    from today's end up to the end of ``until_day - 1``, bounded by the pool."""
    pool = len(SHOPS) - already
    count = sum(1 for d in range(day, until_day) if (d + 1) % SHOP_UNLOCK_INTERVAL == 0)
    return max(0, min(pool, count))


def expected_unknown_shop_units(product: str, unlocked: tuple[str, ...]) -> float:
    """Expected consumption per tick of one not-yet-identified shop: uniform
    without replacement over the shops still in the pool."""
    remaining = [shop for shop in SHOPS if shop not in unlocked]
    if not remaining:
        return 0.0
    return sum(shop_units(shop, product) for shop in remaining) / len(remaining)


def expected_town_demand(state: GameState, product: str, horizon_turns: int) -> TownDemand:
    """Units of ``product`` the town removes from the market during the next
    ``horizon_turns`` turns, starting with the current turn (this turn's tick
    happens after this turn's market orders, TILLA_RULES.md §20). Exact for
    the town center and the shops already unlocked; the expectation over the
    remaining pool for shops that will unlock inside the horizon."""
    center = 0
    known = 0
    future = 0.0
    unlocked = state.town.unlocked_shops
    known_per_tick = sum(shop_units(shop, product) for shop in unlocked)
    per_unknown = expected_unknown_shop_units(product, unlocked)
    for step in range(state.step, state.step + max(0, horizon_turns)):
        day = step // TURNS_PER_DAY
        if step % SHOP_SELL_INTERVAL == 0:
            known += known_per_tick
            future += unknown_shop_unlocks(state.day, day, len(unlocked)) * per_unknown
        if step % TOWN_CENTER_INTERVAL == 0 and product in TOWN_CENTER_PRODUCTS:
            center += town_center_units(day)
    return TownDemand(center, known, future)


def market_trend(memory: EpisodeMemory | None, product: str) -> float:
    """Aggregate public market flow per turn: the median residual inventory
    change (scheduled town consumption removed) over the most recent observed
    deltas, clamped and damped. Pure aggregate: never attributed to a player."""
    if memory is None or len(memory.market_history) < 2:
        return 0.0
    snaps = list(memory.market_history)[-(TREND_WINDOW_TURNS + 1) :]
    residuals: list[float] = []
    for before, after in zip(snaps, snaps[1:], strict=False):
        if after.step != before.step + 1:
            continue  # gap in observations: no delta
        delta = after.inventory.get(product, 0) - before.inventory.get(product, 0)
        town = 0
        if before.step % SHOP_SELL_INTERVAL == 0:
            town += sum(shop_units(shop, product) for shop in before.unlocked_shops)
        if before.step % TOWN_CENTER_INTERVAL == 0 and product in TOWN_CENTER_PRODUCTS:
            town += town_center_units(before.day)
        residuals.append(delta + town)
    if not residuals:
        return 0.0
    residuals.sort()
    mid = len(residuals) // 2
    median = residuals[mid] if len(residuals) % 2 else (residuals[mid - 1] + residuals[mid]) / 2
    cap = TREND_CAP_FRACTION_OF_T * MARKET_PARAMS[product][1]
    return max(-cap, min(cap, median)) * TREND_DAMPING


def projected_inventory(
    state: GameState,
    memory: EpisodeMemory | None,
    product: str,
    horizon_turns: int,
    extra_supply: int = 0,
) -> int:
    """Market inventory expected ``horizon_turns`` turns from now: current stock
    plus the damped aggregate trend, minus expected town demand, plus
    ``extra_supply`` (our own units expected to be sold into it first)."""
    demand = expected_town_demand(state, product, horizon_turns)
    trend = market_trend(memory, product) * min(horizon_turns, TREND_MAX_EXTRAPOLATION_TURNS)
    inv = state.market.inventory.get(product, MARKET_I0) + trend - demand.total + extra_supply
    return max(0, int(round(inv)))


def market_pressure(product: str, inventory: int, anchor: int = 0) -> MarketPressure:
    """Scarcity/glut band from the official price the inventory implies."""
    base = MARKET_PARAMS[product][0]
    price = market_price_at_inventory(product, inventory, anchor)
    ratio = price / base
    if ratio <= PREMIUM_FLOOR_RISK_RATIO or price <= PRICE_FLOOR:
        return MarketPressure.FLOOR_RISK
    if ratio < PRESSURE_SEVERE_RATIO:
        return MarketPressure.SEVERE_GLUT
    if ratio < PRESSURE_GLUT_RATIO:
        return MarketPressure.GLUT
    if ratio > PRESSURE_SCARCE_RATIO:
        return MarketPressure.SCARCE
    return MarketPressure.BALANCED


def own_supply(state: GameState, product: str, horizon_turns: int) -> int:
    """Units of ``product`` we already hold or will produce within the horizon
    from observable assets: shed stock, carried stock, harvestable yield on
    tiles, and base scheduled production of our crops/animals (no fertilizer
    or CARE bonus assumed, no unbought or unplanted assets)."""
    farm = state.me
    units = state.private.shed.get(product, 0)
    units += sum((u.inventory or {}).get(product, 0) for u in farm.units)
    horizon_days = (state.hour + horizon_turns) // TURNS_PER_DAY  # whole day boundaries crossed
    for _, plant in plants(farm):
        if plant.crop != product:
            continue
        spec = CROPS[plant.crop]
        age = plant_age(state.day, plant)
        if spec.ongoing:
            for k in range(spec.max_yield):
                production_age = spec.first_yield_day + k * spec.interval
                if age < production_age <= age + horizon_days:
                    units += 1
            units += plant.yield_units
        else:
            harvest_age = min(spec.max_yield_day, bonus_window_start(spec) + spec.max_yield - 2)
            if age + horizon_days >= max(harvest_age, spec.first_yield_day):
                units += max(plant.yield_units, one_time_units_at_age(spec, harvest_age))
            elif age >= spec.first_yield_day:
                units += plant.yield_units
    for _, tile in animals(farm):
        animal = tile.animal
        if animal is None:
            continue
        spec = ANIMALS[animal.animal]
        if spec.product != product:
            continue
        units += animal.yield_units
        age = state.day - animal.placed_day
        for k in range(0, 40):
            production_age = spec.first_yield_day + k * spec.interval
            if production_age > age + horizon_days:
                break
            if production_age > age:
                units += 1
    return units


def market_aware_revenue(
    state: GameState,
    memory: EpisodeMemory | None,
    product: str,
    units: int,
    horizon_turns: int,
    extra_supply: int = 0,
) -> int:
    """Coins ``units`` of ``product`` are expected to realize when sold in
    ``horizon_turns`` turns: quantity-aware sale into the projected market,
    after our own other supply due by then (plus ``extra_supply`` units being
    committed in the same decision) has been sold into it."""
    if units <= 0:
        return 0
    pipeline = own_supply(state, product, horizon_turns) + max(0, extra_supply)
    inventory = projected_inventory(state, memory, product, horizon_turns, extra_supply=pipeline)
    revenue, _ = estimate_sell_revenue(product, units, inventory, price_anchor(state, product))
    return revenue


def market_penalty(state: GameState, naive_revenue: float, realizable: float) -> float:
    """Market term of the opportunity equation: the shortfall of the
    market-aware revenue below the current-price revenue (a glut penalty), or a
    capped uplift when scarcity is projected (never bonus money beyond
    MAX_SCARCITY_UPLIFT_FRACTION of the current-price revenue)."""
    penalty = naive_revenue - realizable
    return max(penalty, -MAX_SCARCITY_UPLIFT_FRACTION * naive_revenue)


def premium_glut_blocked(
    state: GameState,
    memory: EpisodeMemory | None,
    product: str,
    units: int,
    horizon_turns: int,
    extra_supply: int = 0,
) -> bool:
    """Premium glut protection: reject a new premium investment whose own
    output, sold into the projected market after our existing pipeline (and
    ``extra_supply`` committed alongside it), ends at floor-risk prices
    (TILLA_STRATEGY.md §13)."""
    if product not in PREMIUM_PRODUCTS or units <= 0:
        return False
    pipeline = own_supply(state, product, horizon_turns) + max(0, extra_supply)
    inventory = projected_inventory(state, memory, product, horizon_turns, extra_supply=pipeline)
    anchor = price_anchor(state, product)
    _, after = estimate_sell_revenue(product, units, inventory, anchor)
    return market_pressure(product, after, anchor) is MarketPressure.FLOOR_RISK


def hold_horizon(state: GameState) -> int:
    """Turns until the turn after the next town-center tick (the first moment a
    sale sees that tick's demand), capped at HOLD_HORIZON_TURNS."""
    until_tick = (-state.step) % TOWN_CENTER_INTERVAL  # 0 when this turn is a tick
    return min(HOLD_HORIZON_TURNS, until_tick + 1)


def split_sale(
    product: str, quantity: int, inventory_now: int, inventory_later: int, anchor: int = 0
) -> tuple[int, int, int, int]:
    """Deterministic sell-now / hold split of ``quantity`` units: unit by unit,
    a unit is held only when its projected later price beats the price it
    fetches now by SELL_HOLD_MIN_UPLIFT (both marginal prices fall as more
    is sold). Returns (sell_now, hold, revenue_now, revenue_later)."""
    sell_now = hold = revenue_now = revenue_later = 0
    for _ in range(max(0, quantity)):
        now = market_price_at_inventory(product, inventory_now, anchor)
        later = market_price_at_inventory(product, inventory_later, anchor)
        if later > now * (1.0 + SELL_HOLD_MIN_UPLIFT):
            hold += 1
            revenue_later += later
            if later > PRICE_FLOOR:
                inventory_later += 1
        else:
            sell_now += 1
            revenue_now += now
            if now > PRICE_FLOOR:
                inventory_now += 1
    return sell_now, hold, revenue_now, revenue_later


# --- Cost primitives -------------------------------------------------------------------------


def reference_action_value(state: GameState) -> float:
    """Best gross value per unit action any season-feasible crop offers now
    (revenue minus seed, before labor/land): what an action could earn."""
    best = 0.0
    for crop, spec in CROPS.items():
        plan = plan_crop(spec, state.day)
        if not plan.feasible:
            continue
        gross = plan.units * current_price(state, crop) - spec.seed
        best = max(best, gross / max(1.0, crop_actions(plan)))
    return best


def labor_price(state: GameState) -> float:
    """Marginal value of one action: the idle floor rising linearly with
    farmer utilization to the reference crop value per action."""
    reference = max(LABOR_COST_PER_ACTION, reference_action_value(state))
    return LABOR_COST_PER_ACTION + (reference - LABOR_COST_PER_ACTION) * farmer_utilization(state)


def labor_cost(actions: float, price: float = LABOR_COST_PER_ACTION) -> float:
    return actions * price


def land_scarcity(free_tiles: int) -> float:
    """0 while plenty of empty tiles remain, rising to 1 when none do."""
    if free_tiles >= LAND_SCARCITY_FREE_TILES:
        return 0.0
    return (LAND_SCARCITY_FREE_TILES - free_tiles) / LAND_SCARCITY_FREE_TILES


def land_cost(occupancy_days: int, free_tiles: int) -> float:
    return occupancy_days * LAND_TILE_DAY_VALUE * land_scarcity(free_tiles)


def execution_risk(occupancy_days: int) -> float:
    return occupancy_days * EXECUTION_RISK_PER_DAY


def committed_daily_actions(state: GameState) -> float:
    """Amortized daily care already owed to plants and animals on our farm."""
    return PLANT_DAILY_ACTIONS * len(plants(state.me)) + ANIMAL_DAILY_ACTIONS * len(
        animals(state.me)
    )


def daily_action_budget() -> float:
    """Daily care capacity the planner sizes production for: the farmer plus
    the hands it plans to hire while there is work (TILLA_STRATEGY.md §12)."""
    return FARMER_DAILY_ACTION_BUDGET + PLANNED_DAILY_HANDS * HAND_DAILY_ACTIONS


def labor_capacity_remaining(state: GameState) -> float:
    return daily_action_budget() - committed_daily_actions(state)


def farmer_utilization(state: GameState) -> float:
    """Share of the planned daily care capacity already committed."""
    return min(1.0, committed_daily_actions(state) / daily_action_budget())


# --- Crop production timing (verified mechanics, TILLA_RULES.md §8-§10) ----------------


@dataclass(frozen=True)
class CropPlan:
    """Realizable production of one planting made on ``day``."""

    units: int
    harvest_age: int  # one-time: age at harvest; ongoing: age of the last realizable production
    first_value_age: int
    waterings: int
    harvests: int
    feasible: bool
    reason: str = ""


def bonus_window_start(spec: CropSpec) -> int:
    return (spec.max_yield_day + 1) // 2


def one_time_units_at_age(spec: CropSpec, age: int) -> int:
    """Yield of a daily-watered, unfertilized one-time crop harvested at ``age``."""
    if age < spec.first_yield_day:
        return 0
    bonus_days = max(0, min(age, spec.max_yield_day) - bonus_window_start(spec) + 1)
    return min(spec.max_yield, 1 + bonus_days)


def plan_crop(spec: CropSpec, day: int) -> CropPlan:
    last_age = LAST_HARVEST_DAY - day
    if spec.ongoing:
        ages = [spec.first_yield_day + k * spec.interval for k in range(spec.max_yield)]
        realizable = [a for a in ages if a <= last_age]
        if not realizable:
            return CropPlan(0, 0, 0, 0, 0, False, "no production before season end")
        last = realizable[-1]
        return CropPlan(len(realizable), last, realizable[0], last + 1, len(realizable), True)
    # One-time: harvest as soon as the yield cap is reached, else at max_yield_day,
    # else at the latest age the season still allows (partial yield).
    cap_age = bonus_window_start(spec) + spec.max_yield - 2
    harvest_age = min(spec.max_yield_day, cap_age, last_age)
    if harvest_age < spec.first_yield_day:
        return CropPlan(0, 0, 0, 0, 0, False, "cannot reach first yield before season end")
    units = one_time_units_at_age(spec, harvest_age)
    return CropPlan(units, harvest_age, harvest_age, harvest_age + 1, 1, True)


def crop_actions(plan: CropPlan) -> float:
    """PLANT + daily WATER + HARVESTs + one travel step per visit + shed trips."""
    return 1 + plan.waterings + plan.harvests + plan.waterings + 2


def _turns_until(day: int, hour: int, days_ahead: int) -> int:
    return max(1, days_ahead * TURNS_PER_DAY + (TURNS_PER_DAY - hour))


def _finish(
    kind: OpportunityKind,
    product: str,
    *,
    setup_cost: float,
    setup_cash: int,
    input_cost: float,
    revenue: float,
    byproduct: float,
    actions: float,
    occupancy_days: int,
    free_tiles: int,
    turns_to_realize: int,
    probability: float,
    units: float,
    target: Position | None = None,
    reason: str = "",
    land_days: int | None = None,
    action_price: float = LABOR_COST_PER_ACTION,
    market: float = 0.0,
) -> OpportunityEstimate:
    labor = labor_cost(actions, action_price)
    land = land_cost(land_days if land_days is not None else occupancy_days, free_tiles)
    risk = execution_risk(occupancy_days)
    net = revenue + byproduct - setup_cost - input_cost - labor - land - market - risk
    score = net * probability * PHASE_WEIGHT / max(1, turns_to_realize)
    daily = actions / max(1, occupancy_days)
    return OpportunityEstimate(
        kind=kind,
        product=product,
        setup_cost=setup_cost,
        setup_cash=setup_cash,
        input_cost=input_cost,
        expected_revenue=revenue,
        byproduct_value=byproduct,
        labor_cost=labor,
        land_cost=land,
        market_penalty=market,
        execution_risk=risk,
        expected_net_value=net,
        turns_to_realize=turns_to_realize,
        realization_probability=probability,
        phase_weight=PHASE_WEIGHT,
        score=score,
        actions_required=actions,
        daily_actions=daily,
        occupancy_days=occupancy_days,
        expected_units=units,
        target=target,
        reason=reason,
    )


def _crop_sale_windows(spec: CropSpec, plan: CropPlan, hour: int) -> list[tuple[int, int]]:
    """(turns until sale, units) for each realizable production of one planting."""
    if not spec.ongoing:
        return [(_turns_until(0, hour, plan.harvest_age), plan.units)]
    ages = [spec.first_yield_day + k * spec.interval for k in range(plan.harvests)]
    return [(_turns_until(0, hour, age), 1) for age in ages]


def estimate_crop(
    state: GameState, crop: str, memory: EpisodeMemory | None = None, extra_tiles: int = 0
) -> OpportunityEstimate:
    """Value of planting one tile of ``crop`` today. Seeds already held cost
    nothing more. Revenue is market-aware: each production window is sold
    into the market projected for that window (Milestone 5), after the output
    of ``extra_tiles`` further tiles committed in the same decision."""
    spec = CROPS[crop]
    day, hour = state.day, state.hour
    free_tiles = len(empty_tiles(state.me))
    plan = plan_crop(spec, day)
    seeds_held = state.private.seeds.get(crop, 0) > 0
    setup_cash = 0 if seeds_held else spec.seed
    actions = crop_actions(plan) if plan.feasible else 0.0
    occupancy = plan.harvest_age + 1 if plan.feasible else 0
    probability, reason = 1.0, ""
    if not plan.feasible:
        probability, reason = 0.0, plan.reason
    elif free_tiles == 0:
        probability, reason = 0.0, "no empty unlocked tile"
    elif labor_capacity_remaining(state) < actions / max(1, occupancy):
        probability, reason = 0.0, "daily labor budget exhausted"
    naive = plan.units * current_price(state, crop)
    realizable = 0
    if plan.feasible:
        for turns, units in _crop_sale_windows(spec, plan, hour):
            realizable += market_aware_revenue(
                state, memory, crop, units, turns, extra_supply=extra_tiles * units
            )
        if probability > 0 and premium_glut_blocked(
            state,
            memory,
            crop,
            plan.units,
            _turns_until(day, hour, plan.harvest_age),
            extra_supply=extra_tiles * plan.units,
        ):
            probability, reason = 0.0, "premium glut protection"
    return _finish(
        OpportunityKind.CROP,
        crop,
        setup_cost=float(setup_cash),
        setup_cash=setup_cash,
        input_cost=0.0,
        revenue=naive,
        byproduct=0.0,
        actions=actions,
        occupancy_days=occupancy,
        free_tiles=free_tiles,
        turns_to_realize=_turns_until(day, hour, plan.first_value_age),
        probability=probability,
        units=plan.units,
        reason=reason,
        action_price=labor_price(state),
        market=market_penalty(state, naive, realizable) if plan.feasible else 0.0,
    )


# --- Animals (TILLA_RULES.md §12-§14) -------------------------------------------------------


def animal_production_events(first_yield_day: int, interval: int, placed_day: int) -> int:
    first = placed_day + first_yield_day
    if first > LAST_HARVEST_DAY:
        return 0
    return (LAST_HARVEST_DAY - first) // interval + 1


def estimate_animal(
    state: GameState, animal: str, memory: EpisodeMemory | None = None
) -> OpportunityEstimate:
    """Value of adding one ``animal`` now: structure + purchase + place, then daily
    feed, product harvests and a conservatively realized fertilizer byproduct."""
    spec = ANIMALS[animal]
    day, hour = state.day, state.hour
    farm = state.me
    structure_kind = TileKind(spec.structure)
    have_structure = bool(empty_structures(farm, structure_kind))
    have_animal = state.private.shed.get(animal, 0) > 0 or any(
        (u.inventory or {}).get(animal, 0) > 0 for u in farm.units
    )
    free_tiles = len(empty_tiles(farm))
    placed_day = day if hour < TURNS_PER_DAY - 4 else day + 1  # setup needs a few turns
    events = animal_production_events(spec.first_yield_day, spec.interval, placed_day)
    remaining_days = LAST_DAY - placed_day
    feed_units = max(0, remaining_days)
    wheat_price = current_price(state, WHEAT)
    fert_price = current_price(state, FERTILIZER)
    setup_cash = 0 if have_animal else spec.cost
    setup_actions = (
        (0 if have_structure else 1) + (0 if have_animal else 1) + 1 + 4
    )  # build, pickup, place, travel
    care_actions = feed_units * 2.5 + events * 1.5  # feed+travel+pickup share; harvest+travel share
    collect_units = FERTILIZER_BYPRODUCT_REALIZATION * feed_units
    byproduct = max(0.0, collect_units * (fert_price - labor_price(state)))
    actions = setup_actions + care_actions
    occupancy = max(1, remaining_days + 1)
    probability, reason = 1.0, ""
    if events == 0:
        probability, reason = 0.0, "no production before season end"
    elif not have_structure and free_tiles == 0:
        probability, reason = 0.0, "no empty tile for the structure"
    elif labor_capacity_remaining(state) < ANIMAL_DAILY_ACTIONS:
        probability, reason = 0.0, "daily labor budget exhausted"
    elif day >= LIQUIDATE_START_DAY:
        probability, reason = 0.0, "no new livestock in the final days"
    naive = events * current_price(state, spec.product)
    realizable = 0
    for k in range(events):  # one unit per scheduled production, sold when produced
        days_ahead = placed_day - day + spec.first_yield_day + k * spec.interval
        realizable += market_aware_revenue(
            state, memory, spec.product, 1, _turns_until(day, hour, days_ahead)
        )
    if probability > 0 and premium_glut_blocked(
        state, memory, spec.product, events, _turns_until(day, hour, spec.first_yield_day)
    ):
        probability, reason = 0.0, "premium glut protection"
    return _finish(
        OpportunityKind.ANIMAL,
        animal,
        setup_cost=float(setup_cash),
        setup_cash=setup_cash,
        input_cost=feed_units * wheat_price,
        revenue=naive,
        byproduct=byproduct,
        actions=actions,
        occupancy_days=occupancy,
        free_tiles=free_tiles if not have_structure else free_tiles + 1,
        turns_to_realize=_turns_until(day, hour, spec.first_yield_day),
        probability=probability,
        units=events,
        reason=reason,
        action_price=labor_price(state),
        market=market_penalty(state, naive, realizable),
    )


# --- Fertilizer (TILLA_RULES.md §10-§11) --------------------------------------------------


def fertilizer_incremental_units(spec: CropSpec, plant: PlantTile, day: int) -> int:
    """Extra units from fertilizing ``plant`` today (active today, +1, +2), with daily watering."""
    age = plant_age(day, plant)
    active_ages = [age, age + 1, age + 2]
    if spec.ongoing:
        production_ages = {spec.first_yield_day + k * spec.interval for k in range(spec.max_yield)}
        # Production days still covered by an earlier application are not incremental.
        return sum(
            1
            for a in active_ages
            if a in production_ages
            and plant.fertilized_until_day < day + (a - age) <= LAST_HARVEST_DAY
        )
    start = bonus_window_start(spec)
    harvest_age = min(spec.max_yield_day, LAST_HARVEST_DAY - day + age)
    base_units = one_time_units_at_age(spec, harvest_age)
    boosted_bonus = sum(1 for a in active_ages if start <= a <= harvest_age)
    remaining_bonus_days = max(0, harvest_age - max(start, age) + 1)
    if plant.fertilized_until_day >= day:
        return 0
    unfertilized = min(spec.max_yield, plant.yield_units + remaining_bonus_days)
    fertilized = min(spec.max_yield, plant.yield_units + remaining_bonus_days + boosted_bonus)
    return max(0, fertilized - unfertilized) if base_units else 0


def estimate_fertilize(
    state: GameState, position: Position, plant: PlantTile, memory: EpisodeMemory | None = None
) -> OpportunityEstimate:
    spec = CROPS.get(plant.crop)
    day, hour = state.day, state.hour
    price = current_price(state, plant.crop)
    have = state.private.shed.get(FERTILIZER, 0) > 0 or any(
        (u.inventory or {}).get(FERTILIZER, 0) > 0 for u in state.me.units
    )
    units = fertilizer_incremental_units(spec, plant, day) if spec else 0
    setup_cash = 0 if have else buy_price(state, FERTILIZER)
    # Held fertilizer could be sold instead: its opportunity cost is its sale price.
    input_cost = float(current_price(state, FERTILIZER)) if have else 0.0
    actions = 1 + 2 + 1  # pickup, travel, FERTILIZE
    probability, reason = 1.0, ""
    if units <= 0:
        probability, reason = 0.0, "no incremental yield"
    naive = units * price
    realizable = market_aware_revenue(state, memory, plant.crop, units, _turns_until(day, hour, 3))
    if probability > 0 and premium_glut_blocked(
        state, memory, plant.crop, units, _turns_until(day, hour, 3)
    ):
        probability, reason = 0.0, "premium glut protection"
    return _finish(
        OpportunityKind.FERTILIZE,
        plant.crop,
        setup_cost=float(setup_cash),
        setup_cash=setup_cash,
        input_cost=input_cost,
        revenue=naive,
        byproduct=0.0,
        actions=actions,
        occupancy_days=1,
        free_tiles=LAND_SCARCITY_FREE_TILES,  # no new tile is consumed
        turns_to_realize=_turns_until(day, hour, 3),
        probability=probability,
        units=units,
        target=position,
        reason=reason,
        action_price=labor_price(state),
        market=market_penalty(state, naive, realizable) if units > 0 else 0.0,
    )


# --- Ranking ---------------------------------------------------------------------------------


def plantable_tiles(
    state: GameState, crop: str, memory: EpisodeMemory | None, max_tiles: int
) -> int:
    """How many tiles of ``crop`` can be started now before the marginal tile,
    valued after the output of the tiles committed before it, stops clearing
    the bar (score > 0, realizable, not glut-protected)."""
    count = 0
    while count < max_tiles:
        est = estimate_crop(state, crop, memory, extra_tiles=count)
        if est.score <= 0 or est.realization_probability <= 0:
            break
        count += 1
    return count


def rank_opportunities(
    state: GameState, memory: EpisodeMemory | None = None
) -> list[OpportunityEstimate]:
    """All current opportunities, best score first (deterministic tie order)."""
    estimates = [estimate_crop(state, crop, memory) for crop in CROPS]
    estimates += [estimate_animal(state, animal, memory) for animal in ANIMALS]
    for position, plant in plants(state.me):
        estimates.append(estimate_fertilize(state, position, plant, memory))
    estimates.sort(
        key=lambda e: (
            -e.score,
            e.kind.value,
            e.product,
            e.target.y if e.target else -1,
            e.target.x if e.target else -1,
        )
    )
    return estimates


def affordable(state: GameState, estimate: OpportunityEstimate, reserve: int) -> bool:
    """Discretionary spending must leave the cash reserve intact."""
    return state.me.money - estimate.setup_cash >= reserve


# --- Cash reserve (TILLA_STRATEGY.md §6) ------------------------------------------------


def expected_feed_purchases(state: GameState) -> int:
    """Wheat that must be bought for tomorrow's feeding if the shed cannot cover it."""
    herd = len(animals(state.me))
    shortfall = max(0, herd - state.private.shed.get(WHEAT, 0))
    return shortfall * buy_price(state, WHEAT)


def expected_seed_replenishment(state: GameState) -> int:
    """Seed cost of plants that will be harvested within a day and replanted."""
    total = 0
    for _, plant in plants(state.me):
        spec = CROPS.get(plant.crop)
        if spec is None:
            continue
        plan = plan_crop(spec, plant.planted_day)
        if plan.feasible and plant_age(state.day, plant) >= plan.harvest_age - 1:
            total += spec.seed
    return total


# --- Hiring (TILLA_STRATEGY.md §12) --------------------------------------------------------


def hire_cost(hires_already_today: int) -> int:
    """Fibonacci hire cost 1, 1, 2, 3, 5, ... indexed by hires made today (TILLA_RULES.md §5)."""
    a, b = 1, 1
    for _ in range(hires_already_today):
        a, b = b, a + b
    return a


def hands_needed_for_care(state: GameState) -> int:
    """Hands whose daily capacity the committed care already relies on."""
    excess = committed_daily_actions(state) - FARMER_DAILY_ACTION_BUDGET
    if excess <= 0:
        return 0
    return min(PLANNED_DAILY_HANDS, int(-(-excess // HAND_DAILY_ACTIONS)))


def expected_hand_spend(state: GameState) -> int:
    """Cheap-hand budget: tomorrow's hire cost for the hands current care relies on."""
    if state.day >= LAST_DAY:
        return 0
    return sum(hire_cost(k) for k in range(hands_needed_for_care(state)))


def hiring_plan(
    state: GameState, backlog_actions: float, reserve: int, care_actions: float = 0.0
) -> HiringDecision:
    """How many hands to hire this turn.

    A hand hired now can act from the next turn until the day refresh, minus
    spawn/travel setup. Its value is the backlog it can absorb that the
    current workforce cannot finish today, priced at the marginal action value
    (labor_price). Hire while that value exceeds the next Fibonacci cost, the
    reserve holds, the predicted spawn tile is not stuck, and the daily cap is
    not reached. ``care_actions`` is the part of the backlog that keeps
    existing assets alive; hands it still needs are paid from the reserve
    like survival feed (an avoidable loss is irreversible), never below zero.
    """
    farm = state.me
    remaining = TURNS_PER_DAY - 1 - state.hour
    existing_units = len(farm.units)
    existing_capacity = existing_units * max(0, remaining)
    uncovered = max(0.0, backlog_actions - existing_capacity)
    care_uncovered = max(0.0, min(care_actions, backlog_actions) - existing_capacity)
    usable = max(0, remaining - HAND_SETUP_ACTIONS)
    action_value = labor_price(state)
    spawns = hand_spawn_positions(farm, MAX_DAILY_HIRES)
    costs: list[int] = []
    values: list[float] = []
    money = farm.money
    hires_today = farm.hires_today
    reason = ""
    while True:
        cost = hire_cost(hires_today + len(costs))
        if len(costs) + hires_today >= MAX_DAILY_HIRES:
            reason = "daily hire cap"
            break
        enabled = min(usable, uncovered)
        if enabled < MIN_HAND_USEFUL_ACTIONS:
            reason = "no realizable backlog for another hand today"
            break
        value = enabled * action_value
        if value <= cost:
            reason = f"marginal value {value:.1f} <= cost {cost}"
            break
        floor = 0 if care_uncovered >= MIN_HAND_USEFUL_ACTIONS else reserve
        if money - cost < floor:
            reason = "cash reserve"
            break
        if not can_act_from(farm, spawns[len(costs)]):
            reason = f"spawn tile {spawns[len(costs)]} is stuck this turn"
            break
        costs.append(cost)
        values.append(value)
        money -= cost
        uncovered -= enabled
        care_uncovered = max(0.0, care_uncovered - enabled)
    return HiringDecision(
        existing_units=existing_units,
        hires_today=hires_today,
        remaining_turns=remaining,
        backlog_actions=backlog_actions,
        existing_capacity=existing_capacity,
        uncovered_actions=max(0.0, backlog_actions - existing_capacity),
        action_value=action_value,
        costs=tuple(costs),
        values=tuple(values),
        next_cost=hire_cost(hires_today + len(costs)),
        hires=len(costs),
        reason=reason,
        care_actions=care_actions,
        spawns=spawns[: len(costs)],
    )


def job_backlog_actions(job_count: int) -> float:
    """Estimated unit actions to clear ``job_count`` jobs (action + travel each)."""
    return job_count * HAND_ACTIONS_PER_JOB


def cash_reserve(state: GameState) -> int:
    """Dynamic reserve with the hard floors of TILLA_STRATEGY.md §6/§20."""
    floor = min_cash_reserve(state.day)
    if state.day >= LIQUIDATE_START_DAY:
        return floor  # no fixed reserve beyond mandatory obligations
    dynamic = (
        expected_feed_purchases(state)
        + expected_seed_replenishment(state)
        + expected_hand_spend(state)
        + RESERVE_EMERGENCY_BUFFER
    )
    return max(floor, dynamic)


# --- Sell/hold (Milestone 5; TILLA_STRATEGY.md §13, §17) -------------------------------------


def feed_wheat_hold(state: GameState) -> int:
    """Wheat kept for existing animals' known feed obligations; none remain on the final day."""
    if state.day >= LAST_DAY:
        return 0
    return FEED_WHEAT_RESERVE_PER_ANIMAL * len(animals(state.me))


def fertilizer_hold(state: GameState, memory: EpisodeMemory | None = None) -> int:
    """Fertilizer worth keeping for currently positive fertilize opportunities."""
    if shed_occupancy(state) >= SHED_EMERGENCY:
        return 0
    positive = 0
    for position, plant in plants(state.me):
        est = estimate_fertilize(state, position, plant, memory)
        if est.score > 0 and est.realization_probability > 0:
            positive += 1
    return positive


def internal_hold(state: GameState, product: str, memory: EpisodeMemory | None = None) -> int:
    """Units of ``product`` reserved for our own use before any sale."""
    if product == WHEAT:
        return feed_wheat_hold(state)
    if product == FERTILIZER:
        return 0 if shed_pressure(state) >= 1.0 else fertilizer_hold(state, memory)
    return 0


def market_outlook(
    state: GameState, memory: EpisodeMemory | None, product: str, available: int
) -> MarketOutlook:
    """Sell-now / hold decision for ``available`` shed units of ``product``.

    Hold is evaluated at the turn after the next town-center tick against the
    market projected then (aggregate trend, exact town demand, our own supply
    due by then sold first). Waiting is never chosen from the liquidation days
    on, and a held unit must beat selling now by SELL_HOLD_MIN_UPLIFT.
    """
    inventory = state.market.inventory.get(product, MARKET_I0)
    anchor = price_anchor(state, product)
    price = market_price_at_inventory(product, inventory, anchor)
    horizon = hold_horizon(state)
    demand = expected_town_demand(state, product, horizon)
    trend = market_trend(memory, product)
    supply = own_supply(state, product, horizon) - state.private.shed.get(product, 0)
    later_inventory = projected_inventory(
        state, memory, product, horizon, extra_supply=max(0, supply)
    )
    projected_price = market_price_at_inventory(product, later_inventory, anchor)
    if state.day >= LIQUIDATE_START_DAY:
        sell_now, hold = available, 0
        revenue_now, _ = estimate_sell_revenue(product, available, inventory, anchor)
        revenue_later = 0
        reason = "liquidation days: waiting is unsafe"
    else:
        sell_now, hold, revenue_now, revenue_later = split_sale(
            product, available, inventory, later_inventory, anchor
        )
        if hold and sell_now:
            reason = "partial hold: later units beat selling now after the town tick"
        elif hold:
            reason = "hold: projected price after the town tick beats selling now"
        elif available:
            reason = "sell now: no credible improvement inside the horizon"
        else:
            reason = "nothing available"
    return MarketOutlook(
        product=product,
        inventory=inventory,
        price=price,
        trend_per_turn=trend,
        demand=demand,
        own_supply=max(0, supply),
        horizon_turns=horizon,
        projected_inventory=later_inventory,
        projected_price=projected_price,
        pressure=market_pressure(product, inventory, anchor),
        projected_pressure=market_pressure(product, later_inventory, anchor),
        available=available,
        anchor=anchor,
        sell_now=sell_now,
        hold=hold,
        sell_now_revenue=revenue_now,
        hold_revenue=revenue_later,
        reason=reason,
    )


def market_outlooks(
    state: GameState,
    memory: EpisodeMemory | None = None,
    stock_adjustments: dict[str, int] | None = None,
) -> dict[str, MarketOutlook]:
    """Per-product sell/hold outlook for the shed stock beyond internal holds,
    then the shed and cash overrides (TILLA_STRATEGY.md §5, §6): under shed
    pressure held units are released, most glutted first, until occupancy is
    below the pressure threshold (all of them in an emergency); below the
    cash reserve held units are released, least promising first, until the
    reserve is restored."""
    outlooks: dict[str, MarketOutlook] = {}
    adjust = stock_adjustments or {}
    for product in PRODUCTS:
        # Units in the shed when this turn's market orders run: unit actions
        # (DROP/PICKUP) are applied first (TILLA_RULES.md §20).
        have = state.private.shed.get(product, 0) + adjust.get(product, 0)
        available = max(0, have - internal_hold(state, product, memory))
        outlooks[product] = market_outlook(state, memory, product, available)
    occupancy = shed_occupancy(state) + sum(adjust.values())
    held = sum(o.hold for o in outlooks.values())
    if occupancy >= SHED_PRESSURE_START and held:
        excess = occupancy - (SHED_PRESSURE_START - 1)  # units to get below the threshold
        release = held if occupancy >= SHED_EMERGENCY else min(held, excess)
        _release(outlooks, release, key=lambda o: o.projected_price / max(1, o.price))
    shortfall = cash_reserve(state) - state.me.money
    if shortfall > 0:
        _release_for_cash(outlooks, shortfall)
    return outlooks


def _release(outlooks: dict[str, MarketOutlook], units: int, key) -> None:
    """Move ``units`` held units into sell-now, products ordered by ``key`` ascending."""
    for product in sorted(outlooks, key=lambda p: (key(outlooks[p]), p)):
        if units <= 0:
            break
        o = outlooks[product]
        if o.hold <= 0:
            continue
        take = min(o.hold, units)
        outlooks[product] = _released(o, take, "shed pressure: space before market timing")
        units -= take


def _release_for_cash(outlooks: dict[str, MarketOutlook], shortfall: int) -> None:
    for product in sorted(outlooks, key=lambda p: (outlooks[p].projected_price, p)):
        if shortfall <= 0:
            break
        o = outlooks[product]
        if o.hold <= 0:
            continue
        take, cash = 0, 0
        inventory = o.inventory
        for _ in range(o.hold):
            if cash >= shortfall:
                break
            price = market_price_at_inventory(product, inventory, o.anchor)
            cash += price
            take += 1
            if price > PRICE_FLOOR:
                inventory += 1
        outlooks[product] = _released(o, take, "cash reserve: realize enough now")
        shortfall -= cash


def _released(o: MarketOutlook, take: int, reason: str) -> MarketOutlook:
    revenue_now, _ = estimate_sell_revenue(o.product, o.sell_now + take, o.inventory, o.anchor)
    return MarketOutlook(
        product=o.product,
        inventory=o.inventory,
        price=o.price,
        trend_per_turn=o.trend_per_turn,
        demand=o.demand,
        own_supply=o.own_supply,
        horizon_turns=o.horizon_turns,
        projected_inventory=o.projected_inventory,
        projected_price=o.projected_price,
        pressure=o.pressure,
        projected_pressure=o.projected_pressure,
        available=o.available,
        sell_now=o.sell_now + take,
        hold=o.hold - take,
        sell_now_revenue=revenue_now,
        hold_revenue=0 if o.hold - take == 0 else o.hold_revenue,
        reason=reason,
        anchor=o.anchor,
    )


def sell_plan(
    state: GameState,
    memory: EpisodeMemory | None = None,
    stock_adjustments: dict[str, int] | None = None,
) -> dict[str, int]:
    """Shed quantities to sell this turn (market-aware, quantity-aware)."""
    outlooks = market_outlooks(state, memory, stock_adjustments)
    return {p: o.sell_now for p, o in outlooks.items() if o.sell_now > 0}


def unlocked_tiles(state: GameState) -> int:
    return unlocked_tile_count(state.me)
