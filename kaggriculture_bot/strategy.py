"""Strategic objectives and plans. No raw observations, no pathfinding, no formatting.

Milestone 2 farm-care baseline (TILLA_STRATEGY.md §3, §5, §8 wheat, §16):
a single acting unit (the farmer) works through this fixed priority order and
the task layer executes the chosen objective. Hired hands are not planned yet.

    1. save at-risk crops/animals (die/escape tonight without care)
    2. mandatory daily care of existing plants/animals
    3. harvest ready output
    4. (final day) deliver carried produce to the shed so it can be sold
    5. plant the baseline crop (wheat) with a credible same-day watering slot
    6. deliver carried produce when otherwise idle
    7. PASS

Market orders are free of unit time: excess shed wheat (beyond the feed
reserve) is sold immediately, seed is bought only when a viable planting slot
exists and the hard cash floor holds, and feed wheat is bought only to prevent
an avoidable escape. No price forecasting, land, livestock or fertilizer.
"""

from __future__ import annotations

from kaggriculture_bot.constants import (
    BASELINE_CROP,
    BASELINE_MAX_PLANTS,
    CROPS,
    FEED_WHEAT_RESERVE_PER_ANIMAL,
    LAST_DAY,
    TURNS_PER_DAY,
    WHEAT,
)
from kaggriculture_bot.features import (
    animal_at_risk,
    animal_harvest_ready,
    animal_needs_feed,
    animals,
    carried,
    carried_total,
    crop_can_mature_before_end,
    distance_to_shed,
    empty_tiles,
    min_cash_reserve,
    plant_at_risk,
    plant_decaying,
    plant_harvest_ready,
    plant_needs_water,
    plants,
    usable_shed_access,
)
from kaggriculture_bot.models import (
    PASS_OBJECTIVE,
    EpisodeMemory,
    GameState,
    MarketOp,
    MarketOrder,
    Objective,
    ObjectiveKind,
    Position,
    StrategicPlan,
)

# A fresh planting must still be watered before the day refresh, so the
# on-tile PLANT may happen no later than the second-to-last hour.
PLANT_DEADLINE_HOUR = TURNS_PER_DAY - 2


def _feed_objective(
    state: GameState, targets: tuple[Position, ...]
) -> tuple[Objective | None, list]:
    """How to feed the animals at ``targets``: FEED if wheat is carried, else
    fetch from the shed, else buy wheat (market) while heading to the shed.

    Returns (objective, extra market orders). ``None`` when feeding is impossible
    right now (no wheat anywhere and none affordable).
    """
    farm = state.me
    farmer = farm.farmer
    if carried(farmer, WHEAT) >= 1:
        return Objective(ObjectiveKind.FEED_ANIMAL, targets), []
    access = usable_shed_access(farm)
    if not access:
        return None, []
    shed_wheat = state.private.shed.get(WHEAT, 0)
    if shed_wheat >= 1:
        qty = min(len(targets), shed_wheat)
        return Objective(ObjectiveKind.FETCH_FEED, access, WHEAT, qty), []
    qty = len(targets)
    unit_price = state.market.prices.get(WHEAT, 0) + 1  # buy quotes use post-buy inventory
    if farm.money >= unit_price * qty:
        order = MarketOrder(MarketOp.BUY_PRODUCT, WHEAT, qty)
        return Objective(ObjectiveKind.REPOSITION, access), [order]
    return None, []


def _plant_objective(state: GameState) -> tuple[Objective, list]:
    """Baseline wheat planting with the same-day watering and season guards."""
    farm = state.me
    day, hour = state.day, state.hour
    if hour > PLANT_DEADLINE_HOUR or not crop_can_mature_before_end(day, BASELINE_CROP):
        return PASS_OBJECTIVE, []
    slots = BASELINE_MAX_PLANTS - len(plants(farm))
    empties = empty_tiles(farm)
    if slots <= 0 or not empties:
        return PASS_OBJECTIVE, []
    empties.sort(key=lambda p: (distance_to_shed(p, farm.board_size), p.y, p.x))
    targets = tuple(empties[:slots])
    seeds = state.private.seeds.get(BASELINE_CROP, 0)
    market = []
    wanted = slots - seeds
    if wanted > 0:
        affordable = (farm.money - min_cash_reserve(day)) // CROPS[BASELINE_CROP].seed
        qty = min(wanted, affordable)
        if qty >= 1:
            market.append(MarketOrder(MarketOp.BUY_SEED, BASELINE_CROP, qty))
    if seeds >= 1:
        return Objective(
            ObjectiveKind.PLANT, targets, BASELINE_CROP, None, PLANT_DEADLINE_HOUR
        ), market
    if market:
        return Objective(ObjectiveKind.REPOSITION, targets), market  # seeds arrive after this turn
    return PASS_OBJECTIVE, []


def _sell_orders(state: GameState, objective: Objective) -> list[MarketOrder]:
    """Sell shed wheat beyond the feed reserve. Wheat dropped this turn counts:
    unit actions are applied before market orders (TILLA_RULES.md §20)."""
    farm = state.me
    available = state.private.shed.get(WHEAT, 0)
    acting_at_shed = farm.farmer.position in objective.targets
    if objective.kind is ObjectiveKind.DELIVER and acting_at_shed:
        available += carried(farm.farmer, WHEAT)
    if objective.kind is ObjectiveKind.FETCH_FEED and acting_at_shed and objective.item == WHEAT:
        available -= objective.quantity or 0
    reserve = FEED_WHEAT_RESERVE_PER_ANIMAL * len(animals(farm))
    excess = available - reserve
    return [MarketOrder(MarketOp.SELL, WHEAT, excess)] if excess > 0 else []


def choose_plan(state: GameState, memory: EpisodeMemory) -> StrategicPlan:
    farm = state.me
    day = state.day
    our_plants = plants(farm)
    our_animals = animals(farm)
    market: list[MarketOrder] = []
    objective: Objective | None = None

    # 1. Survival, ordered by time-to-loss: a decaying crop loses yield within
    #    turns, an unfed/unwatered asset is lost at tonight's refresh. Feeding
    #    comes before watering only when wheat is already carried (no detour);
    #    otherwise save crops before making a shed trip for feed.
    decaying = tuple(pos for pos, p in our_plants if plant_decaying(day, p))
    if decaying:
        objective = Objective(ObjectiveKind.HARVEST, decaying)
    risk_animals = tuple(pos for pos, t in our_animals if animal_at_risk(t))
    risk_plants = tuple(pos for pos, p in our_plants if plant_at_risk(p))
    if objective is None and risk_animals and carried(farm.farmer, WHEAT) >= 1:
        objective, _ = _feed_objective(state, risk_animals)
    if objective is None and risk_plants:
        objective = Objective(ObjectiveKind.WATER_CROP, risk_plants)
    if objective is None and risk_animals:
        objective, extra = _feed_objective(state, risk_animals)
        market.extend(extra)

    # 2. Mandatory daily care of existing productive assets.
    if objective is None:
        unwatered = tuple(pos for pos, p in our_plants if plant_needs_water(day, p))
        if unwatered:
            objective = Objective(ObjectiveKind.WATER_CROP, unwatered)
    if objective is None:
        unfed = tuple(pos for pos, t in our_animals if animal_needs_feed(t))
        if unfed:
            objective, extra = _feed_objective(state, unfed)
            market.extend(extra)

    # 3. Harvest ready output (crops and animal products).
    if objective is None:
        ready = tuple(pos for pos, p in our_plants if plant_harvest_ready(day, p))
        ready += tuple(pos for pos, t in our_animals if animal_harvest_ready(t))
        if ready:
            objective = Objective(
                ObjectiveKind.HARVEST, tuple(sorted(ready, key=lambda p: (p.y, p.x)))
            )

    # 4. Final day: get carried produce into the shed so it can still be sold.
    carrying = carried_total(farm.farmer) > 0
    access = usable_shed_access(farm)
    if objective is None and day >= LAST_DAY and carrying and access:
        objective = Objective(ObjectiveKind.DELIVER, access)

    # 5. Continue the simple production cycle: plant baseline wheat.
    if objective is None:
        objective, extra = _plant_objective(state)
        market.extend(extra)
        if objective.kind is ObjectiveKind.PASS:
            objective = None

    # 6. Idle: deliver carried produce; otherwise PASS.
    if objective is None:
        objective = (
            Objective(ObjectiveKind.DELIVER, access) if carrying and access else PASS_OBJECTIVE
        )

    market = _sell_orders(state, objective) + market
    return StrategicPlan(objective=objective, market=tuple(market))
