"""Strategic objectives and plans. No raw observations, no pathfinding, no formatting.

Single acting unit (the farmer; hands are planned in Milestone 4). Priority
order (TILLA_STRATEGY.md §3, §5, §16); economics (Milestone 3) only chooses
among viable opportunities once every higher tier is satisfied:

    1. survival: harvest decaying crops, feed/water assets lost tonight
    2. mandatory daily care of existing plants/animals
    3. harvest ready crops/products and collect waiting fertilizer
    4. (final day) deliver carried produce to the shed so it can be sold
    5. execute the best positive-score opportunity from economy.py that the
       cash reserve allows: plant a crop, add an animal, or fertilize a plant
    6. deliver carried produce when otherwise idle
    7. PASS

Market orders cost no unit time: the basic sell/hold plan sells every shed
product without an immediate internal use (economy.sell_plan); purchases are
made only for the executing opportunity, and feed wheat only to prevent an
avoidable escape.
"""

from __future__ import annotations

from kaggriculture_bot import economy
from kaggriculture_bot.constants import ANIMALS, CROPS, FERTILIZER, LAST_DAY, TURNS_PER_DAY, WHEAT
from kaggriculture_bot.features import (
    animal_at_risk,
    animal_fertilizer_ready,
    animal_harvest_ready,
    animal_needs_feed,
    animals,
    carried,
    carried_total,
    distance_to_shed,
    empty_structures,
    empty_tiles,
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
    OpportunityEstimate,
    OpportunityKind,
    Position,
    StrategicPlan,
    TileKind,
)

# A fresh planting must still be watered before the day refresh, so the
# on-tile PLANT may happen no later than the second-to-last hour.
PLANT_DEADLINE_HOUR = TURNS_PER_DAY - 2


def _by_shed_distance(farm, positions):
    return tuple(sorted(positions, key=lambda p: (distance_to_shed(p, farm.board_size), p.y, p.x)))


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
    if farm.money >= economy.buy_price(state, WHEAT) * qty:
        order = MarketOrder(MarketOp.BUY_PRODUCT, WHEAT, qty)
        return Objective(ObjectiveKind.REPOSITION, access), [order]
    return None, []


# --- Started work: sunk investments to complete (TILLA_STRATEGY.md §3 tier 2, §16 item 4) ---


def _started_work(state: GameState) -> list[Objective]:
    """Plant seeds already held and place animals already bought. These are
    mandatory daily work, not new investment, so they never wait for idle time."""
    farm = state.me
    work: list[Objective] = []
    empties = _by_shed_distance(farm, empty_tiles(farm))
    if state.hour <= PLANT_DEADLINE_HOUR and empties:
        for crop, count in sorted(state.private.seeds.items()):
            if (
                count <= 0
                or crop not in CROPS
                or not economy.plan_crop(CROPS[crop], state.day).feasible
            ):
                continue
            work.append(
                Objective(ObjectiveKind.PLANT, empties[:count], crop, None, PLANT_DEADLINE_HOUR)
            )
    for animal in sorted(ANIMALS):
        held = state.private.shed.get(animal, 0) >= 1 or carried(farm.farmer, animal) >= 1
        if held:
            est = economy.estimate_animal(state, animal)
            objective, _ = _animal_execution(state, est, 0)
            if objective is not None:
                work.append(objective)
    return work


# --- Economic tier: execute the chosen opportunity ------------------------------------------


def _crop_execution(
    state: GameState, est: OpportunityEstimate, reserve: int
) -> tuple[Objective | None, list]:
    farm = state.me
    if state.hour > PLANT_DEADLINE_HOUR:
        return None, []
    empties = _by_shed_distance(farm, empty_tiles(farm))
    if not empties:
        return None, []
    slots = max(1, int(economy.labor_capacity_remaining(state) // max(est.daily_actions, 0.1)))
    slots = min(slots, len(empties))
    targets = empties[:slots]
    seeds = state.private.seeds.get(est.product, 0)
    if seeds >= 1:
        return Objective(ObjectiveKind.PLANT, targets, est.product, None, PLANT_DEADLINE_HOUR), []
    seed_price = CROPS[est.product].seed
    qty = min(slots, (farm.money - reserve) // seed_price)
    if qty < 1:
        return None, []
    order = MarketOrder(MarketOp.BUY_SEED, est.product, qty)
    return Objective(ObjectiveKind.REPOSITION, targets), [order]  # seeds arrive after this turn


def _animal_execution(
    state: GameState, est: OpportunityEstimate, reserve: int
) -> tuple[Objective | None, list]:
    """Stateless chain read off the observable farm: build the structure, buy
    the animal, fetch it from the shed, place it. Each turn does the next step."""
    farm = state.me
    spec = ANIMALS[est.product]
    kind = TileKind(spec.structure)
    access = usable_shed_access(farm)
    structures = _by_shed_distance(farm, empty_structures(farm, kind))
    if carried(farm.farmer, est.product) >= 1:
        if structures:
            return Objective(ObjectiveKind.PLACE_ANIMAL, structures, est.product), []
        empties = _by_shed_distance(farm, empty_tiles(farm))
        if empties:
            return Objective(ObjectiveKind.BUILD_STRUCTURE, empties[:1], spec.structure), []
        return None, []
    if state.private.shed.get(est.product, 0) >= 1:
        if not access:
            return None, []
        return Objective(ObjectiveKind.FETCH_ITEM, access, est.product, 1), []
    if not structures:
        empties = _by_shed_distance(farm, empty_tiles(farm))
        if not empties:
            return None, []
        return Objective(ObjectiveKind.BUILD_STRUCTURE, empties[:1], spec.structure), []
    if not access or farm.money - spec.cost < reserve:
        return None, []
    order = MarketOrder(MarketOp.BUY_ANIMAL, est.product, 1)
    return Objective(ObjectiveKind.REPOSITION, access), [order]


def _fertilize_execution(
    state: GameState, est: OpportunityEstimate, reserve: int
) -> tuple[Objective | None, list]:
    farm = state.me
    if est.target is None:
        return None, []
    if carried(farm.farmer, FERTILIZER) >= 1:
        return Objective(ObjectiveKind.FERTILIZE_CROP, (est.target,)), []
    access = usable_shed_access(farm)
    if not access:
        return None, []
    if state.private.shed.get(FERTILIZER, 0) >= 1:
        return Objective(ObjectiveKind.FETCH_ITEM, access, FERTILIZER, 1), []
    if farm.money - economy.buy_price(state, FERTILIZER) < reserve:
        return None, []
    order = MarketOrder(MarketOp.BUY_PRODUCT, FERTILIZER, 1)
    return Objective(ObjectiveKind.REPOSITION, access), [order]


_EXECUTORS = {
    OpportunityKind.CROP: _crop_execution,
    OpportunityKind.ANIMAL: _animal_execution,
    OpportunityKind.FERTILIZE: _fertilize_execution,
}


def _economic_objective(state: GameState) -> tuple[Objective | None, list]:
    """Best positive, realizable, reserve-respecting opportunity that can act now."""
    reserve = economy.cash_reserve(state)
    for est in economy.rank_opportunities(state):
        if est.score <= 0 or est.realization_probability <= 0:
            break  # sorted: nothing further clears the bar
        if not economy.affordable(state, est, reserve):
            continue
        objective, market = _EXECUTORS[est.kind](state, est, reserve)
        if objective is not None:
            return objective, market
    return None, []


# --- Market: basic sell/hold ------------------------------------------------------------------


def _sell_orders(state: GameState, objective: Objective) -> list[MarketOrder]:
    """Sell shed products per economy.sell_plan. Items dropped this turn count
    and items picked up this turn are excluded: unit actions are applied before
    market orders (TILLA_RULES.md §20)."""
    farm = state.me
    plan = economy.sell_plan(state)
    acting_at_shed = farm.farmer.position in objective.targets
    if objective.kind is ObjectiveKind.DELIVER and acting_at_shed and farm.farmer.inventory:
        holds = {WHEAT: economy.feed_wheat_hold(state), FERTILIZER: economy.fertilizer_hold(state)}
        for item, qty in farm.farmer.inventory.items():
            if item not in economy.PRODUCTS:
                continue
            shed_have = state.private.shed.get(item, 0)
            plan[item] = max(0, shed_have + qty - holds.get(item, 0))
    if objective.kind in (ObjectiveKind.FETCH_FEED, ObjectiveKind.FETCH_ITEM) and acting_at_shed:
        item = objective.item
        if item in plan:
            plan[item] = max(0, plan[item] - (objective.quantity or 0))
    return [MarketOrder(MarketOp.SELL, item, qty) for item, qty in plan.items() if qty > 0]


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

    # 2+3. Mandatory daily work: care of existing assets, harvest of ready
    #      output and collection of waiting fertilizer. All of it must happen
    #      today and none of it is urgent, so the task layer services the
    #      nearest of these objectives first (spatial batching, §16).
    daily: list[Objective] = []
    if objective is None:
        unwatered = tuple(pos for pos, p in our_plants if plant_needs_water(day, p))
        if unwatered:
            daily.append(Objective(ObjectiveKind.WATER_CROP, unwatered))
        unfed = tuple(pos for pos, t in our_animals if animal_needs_feed(t))
        if unfed:
            feed, extra = _feed_objective(state, unfed)
            if feed is not None:
                daily.append(feed)
                market.extend(extra)
        ready = tuple(pos for pos, p in our_plants if plant_harvest_ready(day, p))
        ready += tuple(pos for pos, t in our_animals if animal_harvest_ready(t, day))
        if ready:
            daily.append(
                Objective(ObjectiveKind.HARVEST, tuple(sorted(ready, key=lambda p: (p.y, p.x))))
            )
        waiting = tuple(pos for pos, t in our_animals if animal_fertilizer_ready(t))
        if waiting:
            daily.append(Objective(ObjectiveKind.COLLECT_FERTILIZER, waiting))
        daily.extend(_started_work(state))
        if daily:
            objective = daily[0]

    # 4. Final day: get carried produce into the shed so it can still be sold.
    carrying = carried_total(farm.farmer) > 0
    access = usable_shed_access(farm)
    if objective is None and day >= LAST_DAY and carrying and access:
        objective = Objective(ObjectiveKind.DELIVER, access)

    # 5. Economics: the best opportunity that clears the bar and the reserve.
    if objective is None:
        objective, extra = _economic_objective(state)
        market.extend(extra)

    # 6. Idle: deliver carried produce; otherwise PASS.
    if objective is None:
        objective = (
            Objective(ObjectiveKind.DELIVER, access) if carrying and access else PASS_OBJECTIVE
        )

    market = _sell_orders(state, objective) + market
    equal = tuple(daily[1:]) if daily and objective is daily[0] else ()
    return StrategicPlan(objective=objective, market=tuple(market), equal_priority=equal)
