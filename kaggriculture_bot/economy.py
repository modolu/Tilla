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

from dataclasses import dataclass

from kaggriculture_bot.constants import (
    ANIMALS,
    CROPS,
    EXECUTION_RISK_PER_DAY,
    FARMER_DAILY_ACTION_BUDGET,
    FEED_WHEAT_RESERVE_PER_ANIMAL,
    FERTILIZER,
    FERTILIZER_BYPRODUCT_REALIZATION,
    LABOR_COST_PER_ACTION,
    LAND_SCARCITY_FREE_TILES,
    LAND_TILE_DAY_VALUE,
    LAST_DAY,
    LIQUIDATE_START_DAY,
    MARKET_GLUT_PENALTY,
    PHASE_WEIGHT,
    PRODUCTS,
    RESERVE_EMERGENCY_BUFFER,
    RESERVE_HAND_BUDGET,
    SHED_EMERGENCY,
    TURNS_PER_DAY,
    WHEAT,
    CropSpec,
)
from kaggriculture_bot.features import (
    animals,
    empty_structures,
    empty_tiles,
    min_cash_reserve,
    plant_age,
    plants,
    shed_occupancy,
    shed_pressure,
    unlocked_tile_count,
)
from kaggriculture_bot.models import (
    GameState,
    OpportunityEstimate,
    OpportunityKind,
    PlantTile,
    Position,
    TileKind,
)

# The last day on which a harvest still leaves time to deliver and sell.
LAST_HARVEST_DAY = LAST_DAY - 1

# Amortized daily care actions charged for assets already on the farm.
PLANT_DAILY_ACTIONS = 2.0  # WATER + one travel step
ANIMAL_DAILY_ACTIONS = 3.0  # FEED + COLLECT_FERTILIZER + amortized HARVEST/travel (batched)


def current_price(state: GameState, product: str) -> int:
    """Current observable market sale price (0 if the product is unknown)."""
    return state.market.prices.get(product, 0)


def buy_price(state: GameState, product: str) -> int:
    """Quote for buying one unit now; buy quotes use post-buy inventory, so add one coin."""
    return current_price(state, product) + 1


# --- Cost primitives -------------------------------------------------------------------------


def farmer_utilization(state: GameState) -> float:
    return min(1.0, committed_daily_actions(state) / FARMER_DAILY_ACTION_BUDGET)


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


def labor_capacity_remaining(state: GameState) -> float:
    return FARMER_DAILY_ACTION_BUDGET - committed_daily_actions(state)


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
) -> OpportunityEstimate:
    labor = labor_cost(actions, action_price)
    land = land_cost(land_days if land_days is not None else occupancy_days, free_tiles)
    risk = execution_risk(occupancy_days)
    net = revenue + byproduct - setup_cost - input_cost - labor - land - MARKET_GLUT_PENALTY - risk
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
        market_penalty=MARKET_GLUT_PENALTY,
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


def estimate_crop(state: GameState, crop: str) -> OpportunityEstimate:
    """Value of planting one tile of ``crop`` today. Seeds already held cost nothing more."""
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
    revenue = plan.units * current_price(state, crop)
    return _finish(
        OpportunityKind.CROP,
        crop,
        setup_cost=float(setup_cash),
        setup_cash=setup_cash,
        input_cost=0.0,
        revenue=revenue,
        byproduct=0.0,
        actions=actions,
        occupancy_days=occupancy,
        free_tiles=free_tiles,
        turns_to_realize=_turns_until(day, hour, plan.first_value_age),
        probability=probability,
        units=plan.units,
        reason=reason,
        action_price=labor_price(state),
    )


# --- Animals (TILLA_RULES.md §12-§14) -------------------------------------------------------


def animal_production_events(first_yield_day: int, interval: int, placed_day: int) -> int:
    first = placed_day + first_yield_day
    if first > LAST_HARVEST_DAY:
        return 0
    return (LAST_HARVEST_DAY - first) // interval + 1


def estimate_animal(state: GameState, animal: str) -> OpportunityEstimate:
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
    revenue = events * current_price(state, spec.product)
    return _finish(
        OpportunityKind.ANIMAL,
        animal,
        setup_cost=float(setup_cash),
        setup_cash=setup_cash,
        input_cost=feed_units * wheat_price,
        revenue=revenue,
        byproduct=byproduct,
        actions=actions,
        occupancy_days=occupancy,
        free_tiles=free_tiles if not have_structure else free_tiles + 1,
        turns_to_realize=_turns_until(day, hour, spec.first_yield_day),
        probability=probability,
        units=events,
        reason=reason,
        action_price=labor_price(state),
    )


# --- Fertilizer (TILLA_RULES.md §10-§11) --------------------------------------------------


def fertilizer_incremental_units(spec: CropSpec, plant: PlantTile, day: int) -> int:
    """Extra units from fertilizing ``plant`` today (active today, +1, +2), with daily watering."""
    age = plant_age(day, plant)
    active_ages = [age, age + 1, age + 2]
    if spec.ongoing:
        production_ages = {spec.first_yield_day + k * spec.interval for k in range(spec.max_yield)}
        return sum(
            1 for a in active_ages if a in production_ages and day + (a - age) <= LAST_HARVEST_DAY
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
    state: GameState, position: Position, plant: PlantTile
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
    return _finish(
        OpportunityKind.FERTILIZE,
        plant.crop,
        setup_cost=float(setup_cash),
        setup_cash=setup_cash,
        input_cost=input_cost,
        revenue=units * price,
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
    )


# --- Ranking ---------------------------------------------------------------------------------


def rank_opportunities(state: GameState) -> list[OpportunityEstimate]:
    """All current opportunities, best score first (deterministic tie order)."""
    estimates = [estimate_crop(state, crop) for crop in CROPS]
    estimates += [estimate_animal(state, animal) for animal in ANIMALS]
    for position, plant in plants(state.me):
        estimates.append(estimate_fertilize(state, position, plant))
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


def cash_reserve(state: GameState) -> int:
    """Dynamic reserve with the hard floors of TILLA_STRATEGY.md §6/§20."""
    floor = min_cash_reserve(state.day)
    if state.day >= LIQUIDATE_START_DAY:
        return floor  # no fixed reserve beyond mandatory obligations
    dynamic = (
        expected_feed_purchases(state)
        + expected_seed_replenishment(state)
        + RESERVE_HAND_BUDGET
        + RESERVE_EMERGENCY_BUFFER
    )
    return max(floor, dynamic)


# --- Basic sell/hold (Milestone 3; market timing is Milestone 5) ----------------------------


def feed_wheat_hold(state: GameState) -> int:
    """Wheat kept for existing animals' known feed obligations; none remain on the final day."""
    if state.day >= LAST_DAY:
        return 0
    return FEED_WHEAT_RESERVE_PER_ANIMAL * len(animals(state.me))


def fertilizer_hold(state: GameState) -> int:
    """Fertilizer worth keeping for currently positive fertilize opportunities."""
    if shed_occupancy(state) >= SHED_EMERGENCY:
        return 0
    positive = 0
    for position, plant in plants(state.me):
        est = estimate_fertilize(state, position, plant)
        if est.score > 0 and est.realization_probability > 0:
            positive += 1
    return positive


def sell_plan(state: GameState) -> dict[str, int]:
    """Shed quantities to sell now at the current price: everything without an
    immediate internal use. Held back: wheat for existing animals' feed and
    fertilizer that a positive fertilize opportunity will consume. Under shed
    emergency pressure only the feed reserve is kept."""
    plan: dict[str, int] = {}
    pressure = shed_pressure(state)
    for product in PRODUCTS:
        have = state.private.shed.get(product, 0)
        hold = 0
        if product == WHEAT:
            hold = feed_wheat_hold(state)
        elif product == FERTILIZER:
            hold = 0 if pressure >= 1.0 else fertilizer_hold(state)
        excess = have - hold
        if excess > 0:
            plan[product] = excess
    return plan


def unlocked_tiles(state: GameState) -> int:
    return unlocked_tile_count(state.me)
