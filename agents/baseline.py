"""Frozen Milestone 2 farm-care baseline: the stable local comparison agent.

This is a snapshot of Tilla's accepted Milestone 2 behaviour (commit
milestone-2). It deliberately does NOT import ``kaggriculture_bot.strategy``,
``tasks`` or ``features`` and embeds its own copy of the decision logic and
policy parameters, so later strategy work cannot silently change what this
baseline does. It imports only the stable typed lower layers (parser, models,
pathing, actions, validator) and the official game constants.

Offline use only (never imported by the submitted runtime):

    from agents.baseline import agent
    env.run([agent, "pass"])
"""

from __future__ import annotations

from kaggriculture_bot.actions import build_action, fallback_pass_action
from kaggriculture_bot.constants import (
    CROPS,
    LAST_DAY,
    TURNS_PER_DAY,
    WHEAT,
)
from kaggriculture_bot.models import (
    PASS_OBJECTIVE,
    PASS_UNIT_ACTION,
    FarmState,
    GameState,
    MarketOp,
    MarketOrder,
    Objective,
    ObjectiveKind,
    PlantTile,
    Position,
    StrategicPlan,
    StructureTile,
    Tile,
    TileKind,
    TurnAction,
    UnitAction,
    UnitOp,
    UnitState,
)
from kaggriculture_bot.parser import parse_observation
from kaggriculture_bot.pathing import nearest, next_step_toward
from kaggriculture_bot.validator import validate_or_fallback

# --- Frozen Milestone 2 policy parameters (copied from constants.py at snapshot time) ---

BASELINE_CROP = WHEAT
BASELINE_MAX_PLANTS = 6
FEED_WHEAT_RESERVE_PER_ANIMAL = 2
EARLY_PHASE_END_DAY = 7
SCALE_PHASE_END_DAY = 20
HARVEST_PHASE_END_DAY = 26
EARLY_MIN_CASH_RESERVE = 300
MID_MIN_CASH_RESERVE = 300
HARVEST_MIN_CASH_RESERVE = 200

# --- Frozen copy of features.py ---------------------------------------------------------------

# --- Board scans (stable (y, x) order) ---------------------------------------------------


def tiles_of_kind(farm: FarmState, kind: TileKind) -> list[tuple[Position, Tile]]:
    found = []
    for y, row in enumerate(farm.tiles):
        for x, tile in enumerate(row):
            if tile.kind is kind:
                found.append((Position(x, y), tile))
    return found


def plants(farm: FarmState) -> list[tuple[Position, PlantTile]]:
    return tiles_of_kind(farm, TileKind.PLANT)


def animals(farm: FarmState) -> list[tuple[Position, StructureTile]]:
    """Occupied coops/pastures (structures with an animal placed)."""
    found = []
    for kind in (TileKind.COOP, TileKind.PASTURE):
        for pos, tile in tiles_of_kind(farm, kind):
            if tile.animal is not None:
                found.append((pos, tile))
    found.sort(key=lambda item: (item[0].y, item[0].x))
    return found


def empty_tiles(farm: FarmState) -> list[Position]:
    """Empty unlocked tiles (the only tiles PLANT/BUILD succeed on)."""
    return [pos for pos, _ in tiles_of_kind(farm, TileKind.EMPTY)]


def shed_access_positions(size: int) -> tuple[Position, ...]:
    """The four inner-corner tiles around the shed (TILLA_RULES.md §4), NW→NE→SW→SE."""
    half = size // 2
    return (
        Position(half - 1, half - 1),
        Position(half, half - 1),
        Position(half - 1, half),
        Position(half, half),
    )


def usable_shed_access(farm: FarmState) -> tuple[Position, ...]:
    """Shed access tiles a unit can stand on and use (unlocked ones only)."""
    return tuple(
        pos
        for pos in shed_access_positions(farm.board_size)
        if farm.tiles[pos.y][pos.x].kind is not TileKind.LOCKED
    )


def distance_to_shed(pos: Position, size: int) -> int:
    """Manhattan distance to the closest shed access tile (a metric, not a path)."""
    return min(abs(pos.x - p.x) + abs(pos.y - p.y) for p in shed_access_positions(size))


# --- Unit / inventory ------------------------------------------------------------------


def carried(unit: UnitState, item: str) -> int:
    """Units carried by one of our units (0 when the inventory is unobservable)."""
    if unit.inventory is None:
        return 0
    return unit.inventory.get(item, 0)


def carried_total(unit: UnitState) -> int:
    if unit.inventory is None:
        return 0
    return sum(unit.inventory.values())


# --- Crop / animal care state (mechanics, TILLA_RULES.md §9-§10, §13) -------------------


def plant_age(day: int, plant: PlantTile) -> int:
    return day - plant.planted_day


def plant_at_risk(plant: PlantTile) -> bool:
    """Dies at tonight's refresh unless watered today (includes fresh plantings)."""
    return not plant.watered_today and plant.consecutive_unwatered >= 1


def plant_needs_water(day: int, plant: PlantTile) -> bool:
    """Unwatered today and still able to benefit: one-time crops past their
    bonus window are decaying and only need harvesting."""
    if plant.watered_today:
        return False
    spec = CROPS.get(plant.crop)
    if spec is not None and not spec.ongoing and plant_age(day, plant) > spec.max_yield_day:
        return False
    return True


def plant_decaying(day: int, plant: PlantTile) -> bool:
    """One-time crop past its bonus window: it loses one unit every other turn
    and becomes a weed at zero, so unharvested yield is an irreversible loss."""
    spec = CROPS.get(plant.crop)
    if spec is None or spec.ongoing or plant.yield_units <= 0:
        return False
    return plant_age(day, plant) > spec.max_yield_day


def plant_harvest_ready(day: int, plant: PlantTile) -> bool:
    """HARVEST is legal and (for one-time crops) the bonus window is complete."""
    spec = CROPS.get(plant.crop)
    if spec is None or plant.yield_units <= 0:
        return False
    age = plant_age(day, plant)
    if age < spec.first_yield_day:
        return False
    if spec.ongoing:
        return True
    return age > spec.max_yield_day or (age == spec.max_yield_day and plant.watered_today)


def animal_at_risk(structure: StructureTile) -> bool:
    """Escapes at tonight's refresh unless fed today."""
    a = structure.animal
    return a is not None and not a.fed_today and a.consecutive_unfed >= 1


def animal_needs_feed(structure: StructureTile) -> bool:
    return structure.animal is not None and not structure.animal.fed_today


def animal_harvest_ready(structure: StructureTile) -> bool:
    return structure.animal is not None and structure.animal.yield_units > 0


# --- Season / cash ----------------------------------------------------------------------


def turns_left_in_day(hour: int) -> int:
    """Turns remaining in the day after the current one."""
    return TURNS_PER_DAY - 1 - hour


def crop_can_mature_before_end(day: int, crop: str) -> bool:
    """Planted today, a one-time crop reaches its full-yield day and still
    leaves a following day to harvest, deliver and sell before the season ends."""
    spec = CROPS[crop]
    return day + spec.max_yield_day < LAST_DAY


def min_cash_reserve(day: int) -> int:
    """Hard cash floor for discretionary spending (TILLA_STRATEGY.md §6, §20)."""
    if day <= SCALE_PHASE_END_DAY:
        return EARLY_MIN_CASH_RESERVE if day <= EARLY_PHASE_END_DAY else MID_MIN_CASH_RESERVE
    if day <= HARVEST_PHASE_END_DAY:
        return HARVEST_MIN_CASH_RESERVE
    return 0


# --- Frozen copy of strategy.py ---------------------------------------------------------

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


def choose_plan(state: GameState) -> StrategicPlan:
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


# --- Frozen copy of tasks.py --------------------------------------------------------------

_ON_TILE_OPS = {
    ObjectiveKind.WATER_CROP: UnitOp.WATER,
    ObjectiveKind.FEED_ANIMAL: UnitOp.FEED,
    ObjectiveKind.HARVEST: UnitOp.HARVEST,
    ObjectiveKind.DELIVER: UnitOp.DROP,
}


def _on_tile_action(objective: Objective, hour: int) -> UnitAction:
    kind = objective.kind
    if kind is ObjectiveKind.PLANT:
        if objective.item is None:
            return PASS_UNIT_ACTION
        if objective.deadline_hour is not None and hour > objective.deadline_hour:
            return PASS_UNIT_ACTION  # too late to water it today; do not plant
        return UnitAction(UnitOp.PLANT, objective.item)
    if kind is ObjectiveKind.FETCH_FEED:
        if objective.item is None:
            return PASS_UNIT_ACTION
        return UnitAction(UnitOp.PICKUP, objective.item, objective.quantity)
    op = _ON_TILE_OPS.get(kind)
    return UnitAction(op) if op is not None else PASS_UNIT_ACTION


def farmer_job(state: GameState, objective: Objective) -> UnitAction:
    """The farmer's concrete action for ``objective``: act if standing on the
    nearest reachable target, otherwise take one shortest-path step toward it."""
    if objective.kind is ObjectiveKind.PASS or not objective.targets:
        return PASS_UNIT_ACTION
    tiles = state.me.tiles
    start = state.me.farmer.position
    found = nearest(tiles, start, objective.targets)
    if found is None:
        return PASS_UNIT_ACTION  # no target reachable: degrade safely
    target, _ = found
    if target == start:
        return _on_tile_action(objective, state.hour)
    step = next_step_toward(tiles, start, target)
    return UnitAction(step) if step is not None else PASS_UNIT_ACTION


def assign_jobs(state: GameState, plan: StrategicPlan) -> TurnAction:
    return TurnAction(
        farmer=farmer_job(state, plan.objective),
        hands=tuple(PASS_UNIT_ACTION for _ in state.me.hands),
        market=plan.market,
    )


# --- Adapter --------------------------------------------------------------------------------


def agent(obs):
    """Baseline agent with the same outer safety boundary as main.agent."""
    hand_count = 0
    try:
        state = parse_observation(obs)
        hand_count = len(state.me.hands)
        plan = choose_plan(state)
        turn = assign_jobs(state, plan)
        return validate_or_fallback(build_action(turn), hand_count)
    except Exception:
        return fallback_pass_action(hand_count)
