"""Strategic objectives and plans. No raw observations, no pathfinding, no formatting.

Every turn the plan lists *all* current objectives in priority order
(TILLA_STRATEGY.md §3, §5, §12, §16) so the task layer can give every unit a
distinct job; economics (Milestone 3) only adds opportunities once survival
and care are listed above them:

    1. survival: harvest decaying crops, feed/water assets lost tonight
    2. mandatory daily work: routine care, harvest ready crops/products,
       collect waiting fertilizer, complete started work (held seeds,
       bought animals)
    3. (final day) deliver carried produce to the shed so it can be sold
    4. the best positive-score opportunity from economy.py that the cash
       reserve allows: plant a crop, add an animal, or fertilize a plant
    5. deliver carried produce when otherwise idle

Hiring (Milestone 4): the plan carries the number of hands to hire this turn,
from economy.hiring_plan on today's job backlog. Market orders cost no unit
time and are emitted in explicit priority order: sales, survival feed
purchases, economic purchases, hires; the list is capped at the official
maximum so nothing is silently dropped by the environment.
"""

from __future__ import annotations

from kaggriculture_bot import economy
from kaggriculture_bot.constants import (
    ANIMALS,
    CROPS,
    FERTILIZER,
    LAST_DAY,
    MAX_MARKET_ORDERS_PER_TURN,
    TURNS_PER_DAY,
    WHEAT,
)
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
    PRIORITY_DAILY_WORK,
    PRIORITY_DELIVERY,
    PRIORITY_ECONOMIC,
    PRIORITY_IDLE,
    PRIORITY_SURVIVAL,
    EpisodeMemory,
    GameState,
    HiringDecision,
    MarketOp,
    MarketOrder,
    Objective,
    ObjectiveKind,
    OpportunityEstimate,
    OpportunityKind,
    StrategicPlan,
    TileKind,
)

# A fresh planting must still be watered before the day refresh, so the
# on-tile PLANT may happen no later than the second-to-last hour.
PLANT_DEADLINE_HOUR = TURNS_PER_DAY - 2


def _by_shed_distance(farm, positions):
    return tuple(sorted(positions, key=lambda p: (distance_to_shed(p, farm.board_size), p.y, p.x)))


def _positions(pairs):
    return tuple(sorted((pos for pos, _ in pairs), key=lambda p: (p.y, p.x)))


def _wheat_available(state: GameState) -> int:
    return state.private.shed.get(WHEAT, 0) + sum(carried(u, WHEAT) for u in state.me.units)


def _feed_purchase(state: GameState, mouths: int, survival: bool) -> list[MarketOrder]:
    """Buy the wheat today's feeding still lacks. Survival feeding may dip
    below the reserve (an avoidable escape is an irreversible loss)."""
    shortfall = mouths - _wheat_available(state)
    if shortfall <= 0:
        return []
    cost = economy.buy_price(state, WHEAT) * shortfall
    floor = 0 if survival else economy.cash_reserve(state)
    if state.me.money - cost < floor:
        return []
    return [MarketOrder(MarketOp.BUY_PRODUCT, WHEAT, shortfall)]


# --- Started work: sunk investments to complete (TILLA_STRATEGY.md §3 tier 2, §16 item 4) ---


def _same_day_watering_capacity(state: GameState) -> int:
    """New plantings the current workforce can still water today: one action
    per fresh planting by the unit already standing there, after the plants
    that still need water (about two actions each including travel)."""
    turns_after_this = TURNS_PER_DAY - 1 - state.hour
    if turns_after_this <= 0:
        return 0
    unwatered = sum(1 for _, p in plants(state.me) if plant_needs_water(state.day, p))
    return max(0, turns_after_this * len(state.me.units) - 2 * unwatered)


def _plant_objective(state: GameState, crop: str, count: int, priority: int) -> Objective | None:
    farm = state.me
    if state.hour > PLANT_DEADLINE_HOUR or count <= 0:
        return None
    empties = _by_shed_distance(farm, empty_tiles(farm))
    count = min(count, len(empties), _same_day_watering_capacity(state))
    if count <= 0:
        return None
    return Objective(
        ObjectiveKind.PLANT, empties[:count], crop, None, PLANT_DEADLINE_HOUR, priority
    )


def _animal_objectives(state: GameState, animal: str, priority: int) -> list[Objective]:
    """Place an already-owned animal (shed or carried): PLACE on an empty
    matching structure, or BUILD one first."""
    farm = state.me
    spec = ANIMALS[animal]
    structures = _by_shed_distance(farm, empty_structures(farm, TileKind(spec.structure)))
    held = state.private.shed.get(animal, 0) + sum(carried(u, animal) for u in farm.units)
    if held <= 0:
        return []
    if structures:
        return [Objective(ObjectiveKind.PLACE_ANIMAL, structures[:held], animal, 1, None, priority)]
    empties = _by_shed_distance(farm, empty_tiles(farm))
    if not empties:
        return []
    return [
        Objective(ObjectiveKind.BUILD_STRUCTURE, empties[:1], spec.structure, None, None, priority)
    ]


def _started_work(state: GameState) -> list[Objective]:
    """Plant seeds already held and place animals already bought: mandatory
    daily work, never waiting for idle time."""
    work: list[Objective] = []
    for crop, count in sorted(state.private.seeds.items()):
        if count <= 0 or crop not in CROPS:
            continue
        if not economy.plan_crop(CROPS[crop], state.day).feasible:
            continue
        objective = _plant_objective(state, crop, count, PRIORITY_DAILY_WORK)
        if objective is not None:
            work.append(objective)
    for animal in sorted(ANIMALS):
        work.extend(_animal_objectives(state, animal, PRIORITY_DAILY_WORK))
    return work


# --- Economic tier: start the chosen opportunity ----------------------------------------------


def _crop_execution(
    state: GameState, est: OpportunityEstimate, reserve: int
) -> tuple[list[Objective], list[MarketOrder]]:
    farm = state.me
    if state.hour > PLANT_DEADLINE_HOUR or not empty_tiles(farm):
        return [], []
    slots = max(1, int(economy.labor_capacity_remaining(state) // max(est.daily_actions, 0.1)))
    slots = min(slots, len(empty_tiles(farm)))
    if state.private.seeds.get(est.product, 0) >= 1:
        return [], []  # held seeds are already planted as started work
    seed_price = CROPS[est.product].seed
    qty = min(slots, (farm.money - reserve) // seed_price)
    if qty < 1:
        return [], []
    return [], [MarketOrder(MarketOp.BUY_SEED, est.product, qty)]  # seeds arrive after this turn


def _animal_execution(
    state: GameState, est: OpportunityEstimate, reserve: int
) -> tuple[list[Objective], list[MarketOrder]]:
    """Build the structure first, then buy the animal (placed as started work)."""
    farm = state.me
    spec = ANIMALS[est.product]
    kind = TileKind(spec.structure)
    held = state.private.shed.get(est.product, 0) + sum(carried(u, est.product) for u in farm.units)
    if held >= 1:
        return [], []  # placement is started work
    if not empty_structures(farm, kind):
        empties = _by_shed_distance(farm, empty_tiles(farm))
        if not empties:
            return [], []
        return [
            Objective(
                ObjectiveKind.BUILD_STRUCTURE,
                empties[:1],
                spec.structure,
                None,
                None,
                PRIORITY_ECONOMIC,
            )
        ], []
    if not usable_shed_access(farm) or farm.money - spec.cost < reserve:
        return [], []
    return [], [MarketOrder(MarketOp.BUY_ANIMAL, est.product, 1)]


def _fertilize_execution(
    state: GameState, est: OpportunityEstimate, reserve: int
) -> tuple[list[Objective], list[MarketOrder]]:
    farm = state.me
    if est.target is None or not usable_shed_access(farm):
        return [], []
    held = state.private.shed.get(FERTILIZER, 0) + sum(carried(u, FERTILIZER) for u in farm.units)
    objective = Objective(
        ObjectiveKind.FERTILIZE_CROP, (est.target,), FERTILIZER, 1, None, PRIORITY_ECONOMIC
    )
    if held >= 1:
        return [objective], []
    if farm.money - economy.buy_price(state, FERTILIZER) < reserve:
        return [], []
    return [], [MarketOrder(MarketOp.BUY_PRODUCT, FERTILIZER, 1)]


_EXECUTORS = {
    OpportunityKind.CROP: _crop_execution,
    OpportunityKind.ANIMAL: _animal_execution,
    OpportunityKind.FERTILIZE: _fertilize_execution,
}


def _economic_tier(state: GameState, reserve: int) -> tuple[list[Objective], list[MarketOrder]]:
    """Best positive, realizable, reserve-respecting opportunity that can act now."""
    for est in economy.rank_opportunities(state):
        if est.score <= 0 or est.realization_probability <= 0:
            break  # sorted: nothing further clears the bar
        if not economy.affordable(state, est, reserve):
            continue
        objectives, market = _EXECUTORS[est.kind](state, est, reserve)
        if objectives or market:
            return objectives, market
    return [], []


# --- Market: basic sell/hold ------------------------------------------------------------------


def _sell_orders(state: GameState, delivering: bool, feeding: int) -> list[MarketOrder]:
    """Sell shed products per economy.sell_plan. Wheat that today's feeding
    must still pick up from the shed is kept back. When a delivery is planned,
    items carried by units already standing on an access tile count too:
    unit actions are applied before market orders (TILLA_RULES.md §20)."""
    farm = state.me
    plan = economy.sell_plan(state)
    fetch = max(0, feeding - sum(carried(u, WHEAT) for u in farm.units))
    if fetch and plan.get(WHEAT, 0) > 0:
        plan[WHEAT] = max(0, plan[WHEAT] - fetch)
    if delivering:
        access = set(usable_shed_access(farm))
        holds = {WHEAT: economy.feed_wheat_hold(state), FERTILIZER: economy.fertilizer_hold(state)}
        extra: dict[str, int] = {}
        for unit in farm.units:
            if unit.position in access and unit.inventory:
                for item, qty in unit.inventory.items():
                    if item in economy.PRODUCTS:
                        extra[item] = extra.get(item, 0) + qty
        for item, qty in extra.items():
            shed_have = state.private.shed.get(item, 0)
            plan[item] = max(0, shed_have + qty - holds.get(item, 0))
    return [MarketOrder(MarketOp.SELL, item, qty) for item, qty in plan.items() if qty > 0]


def hiring_decision(state: GameState, objectives: list[Objective], reserve: int) -> HiringDecision:
    """Strategy owns whether to hire: value today's backlog (every target of
    every objective except idle work) with the economy's estimate. The care
    part of it (survival and daily work on existing assets) may be staffed
    from the reserve, like survival feed purchases (TILLA_STRATEGY.md §12)."""
    job_count = sum(len(o.targets) for o in objectives if o.priority <= PRIORITY_ECONOMIC)
    care_count = sum(len(o.targets) for o in objectives if o.priority <= PRIORITY_DAILY_WORK)
    return economy.hiring_plan(
        state,
        economy.job_backlog_actions(job_count),
        reserve,
        care_actions=economy.job_backlog_actions(care_count),
    )


def choose_plan(state: GameState, memory: EpisodeMemory) -> StrategicPlan:
    farm = state.me
    day = state.day
    our_plants = plants(farm)
    our_animals = animals(farm)
    objectives: list[Objective] = []
    survival_orders: list[MarketOrder] = []
    economic_orders: list[MarketOrder] = []

    # 1. Survival, ordered by time-to-loss: a decaying crop loses yield within
    #    turns; an unfed/unwatered asset is lost at tonight's refresh.
    decaying = _positions((pos, p) for pos, p in our_plants if plant_decaying(day, p))
    if decaying:
        objectives.append(Objective(ObjectiveKind.HARVEST, decaying, priority=PRIORITY_SURVIVAL))
    risk_animals = _positions((pos, t) for pos, t in our_animals if animal_at_risk(t))
    if risk_animals:
        objectives.append(
            Objective(ObjectiveKind.FEED_ANIMAL, risk_animals, WHEAT, 1, None, PRIORITY_SURVIVAL)
        )
        survival_orders += _feed_purchase(state, len(risk_animals), survival=True)
    risk_plants = _positions((pos, p) for pos, p in our_plants if plant_at_risk(p))
    if risk_plants:
        objectives.append(
            Objective(ObjectiveKind.WATER_CROP, risk_plants, priority=PRIORITY_SURVIVAL)
        )

    # 2. Mandatory daily work: routine care, harvest/collect, started work.
    unwatered = _positions(
        (pos, p) for pos, p in our_plants if plant_needs_water(day, p) and not plant_at_risk(p)
    )
    if unwatered:
        objectives.append(
            Objective(ObjectiveKind.WATER_CROP, unwatered, priority=PRIORITY_DAILY_WORK)
        )
    unfed = _positions(
        (pos, t) for pos, t in our_animals if animal_needs_feed(t) and not animal_at_risk(t)
    )
    if unfed:
        objectives.append(
            Objective(ObjectiveKind.FEED_ANIMAL, unfed, WHEAT, 1, None, PRIORITY_DAILY_WORK)
        )
        survival_orders += _feed_purchase(state, len(risk_animals) + len(unfed), survival=False)
    ready = [pos for pos, p in our_plants if plant_harvest_ready(day, p) and pos not in decaying]
    ready += [pos for pos, t in our_animals if animal_harvest_ready(t, day)]
    if ready:
        objectives.append(
            Objective(
                ObjectiveKind.HARVEST,
                tuple(sorted(ready, key=lambda p: (p.y, p.x))),
                priority=PRIORITY_DAILY_WORK,
            )
        )
    waiting = _positions((pos, t) for pos, t in our_animals if animal_fertilizer_ready(t))
    if waiting:
        objectives.append(
            Objective(ObjectiveKind.COLLECT_FERTILIZER, waiting, priority=PRIORITY_DAILY_WORK)
        )
    objectives.extend(_started_work(state))

    # 3. Final day: get carried produce into the shed so it can still be sold.
    carrying = any(carried_total(u) > 0 for u in farm.units)
    access = usable_shed_access(farm)
    if day >= LAST_DAY and carrying and access:
        objectives.append(Objective(ObjectiveKind.DELIVER, access, priority=PRIORITY_DELIVERY))

    # 4. Economics: the best opportunity that clears the bar and the reserve.
    reserve = economy.cash_reserve(state)
    econ_objectives, econ_orders = _economic_tier(state, reserve)
    objectives.extend(econ_objectives)
    economic_orders.extend(econ_orders)

    # 5. Idle: deliver carried produce.
    if carrying and access and day < LAST_DAY:
        objectives.append(Objective(ObjectiveKind.DELIVER, access, priority=PRIORITY_IDLE))

    # Hiring: hands for today's backlog, after the reserve and the purchases above.
    committed = sum(economy.order_cost(state, o) for o in survival_orders + economic_orders)
    decision = hiring_decision(state, objectives, reserve + committed)
    hire_orders = [MarketOrder(MarketOp.HIRE) for _ in range(decision.hires)]

    delivering = any(o.kind is ObjectiveKind.DELIVER for o in objectives)
    feeding = len(risk_animals) + len(unfed)
    market = (
        _sell_orders(state, delivering, feeding) + survival_orders + economic_orders + hire_orders
    )
    market = market[:MAX_MARKET_ORDERS_PER_TURN]  # explicit priority order; never rely on the env
    hires = sum(1 for o in market if o.op is MarketOp.HIRE)
    top = objectives[0] if objectives else PASS_OBJECTIVE
    same_tier = tuple(o for o in objectives[1:] if o.priority == top.priority)
    return StrategicPlan(
        objective=top,
        market=tuple(market),
        equal_priority=same_tier,
        objectives=tuple(objectives),
        hires=hires,
    )
