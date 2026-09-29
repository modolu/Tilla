"""Derived farm, market, season, and state features.

Pure read-only helpers over ``GameState``; no decisions, no pathfinding. The
mechanics they encode are those verified in ``TILLA_RULES.md`` §4, §8-§10, §13.
"""

from __future__ import annotations

from agents.incumbent_m7.constants import (
    ANIMALS,
    CROPS,
    EARLY_MIN_CASH_RESERVE,
    EARLY_PHASE_END_DAY,
    HARVEST_MIN_CASH_RESERVE,
    HARVEST_PHASE_END_DAY,
    LAST_DAY,
    MID_MIN_CASH_RESERVE,
    SCALE_PHASE_END_DAY,
    SHED_CAPACITY,
    SHED_EMERGENCY,
    SHED_PRESSURE_START,
    SHOP_SELL_INTERVAL,
    SHOPS,
    TOWN_CENTER_DEMAND_SCHEDULE,
    TOWN_CENTER_INTERVAL,
    TOWN_CENTER_PRODUCTS,
    TURNS_PER_DAY,
    CropSpec,
)
from agents.incumbent_m7.models import (
    FarmState,
    GameState,
    MarketSnapshot,
    PlantTile,
    Position,
    StructureTile,
    Tile,
    TileKind,
    UnitState,
)

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


def empty_structures(farm: FarmState, kind: TileKind) -> list[Position]:
    """Coops/pastures of ``kind`` with no animal placed."""
    return [pos for pos, tile in tiles_of_kind(farm, kind) if tile.animal is None]


def unlocked_tile_count(farm: FarmState) -> int:
    return sum(1 for row in farm.tiles for tile in row if tile.kind is not TileKind.LOCKED)


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


def hand_spawn_positions(farm: FarmState, count: int) -> tuple[Position, ...]:
    """Where the next ``count`` hires would spawn given the units' current
    positions (TILLA_RULES.md §5): each on the least-occupied shed access
    tile, NW→NE→SW→SE on ties, counting the hands hired just before it."""
    access = shed_access_positions(farm.board_size)
    occupants = {pos: 0 for pos in access}
    for unit in farm.units:
        if unit.position in occupants:
            occupants[unit.position] += 1
    spawns: list[Position] = []
    for _ in range(count):
        pos = min(access, key=lambda p: (occupants[p], access.index(p)))
        occupants[pos] += 1
        spawns.append(pos)
    return tuple(spawns)


def can_act_from(farm: FarmState, pos: Position) -> bool:
    """A unit at ``pos`` can do work somewhere: the tile is unlocked, or an
    adjacent tile is (a hand spawned on a locked access tile whose neighbours
    are all locked is stuck there for the day, TILLA_RULES.md §5)."""
    size = farm.board_size
    candidates = (pos, Position(pos.x, pos.y - 1), Position(pos.x, pos.y + 1))
    candidates += (Position(pos.x + 1, pos.y), Position(pos.x - 1, pos.y))
    for cand in candidates:
        if 0 <= cand.x < size and 0 <= cand.y < size:
            if farm.tiles[cand.y][cand.x].kind is not TileKind.LOCKED:
                return True
    return False


def distance_to_shed(pos: Position, size: int) -> int:
    """Manhattan distance to the closest shed access tile (a metric, not a path)."""
    return min(abs(pos.x - p.x) + abs(pos.y - p.y) for p in shed_access_positions(size))


def distance_to_usable_shed(farm: FarmState, pos: Position) -> int | None:
    """Manhattan distance to the closest *usable* (unlocked) shed access tile,
    or None when none is usable (a metric, not a path)."""
    access = usable_shed_access(farm)
    if not access:
        return None
    return min(abs(pos.x - p.x) + abs(pos.y - p.y) for p in access)


# --- Final day (Milestone 7, TILLA_STRATEGY.md §17) ---------------------------------------


def is_final_day(day: int) -> bool:
    """No day refresh follows today's last turn: nothing that only pays off at
    or after a refresh (feeding, ongoing-crop watering, fertilizer) has value."""
    return day >= LAST_DAY


def final_delivery_hour(hour_of_action: int, distance: int) -> int:
    """Hour at which a unit acting on a tile ``distance`` steps from a usable
    shed access tile at ``hour_of_action`` can DROP there (then SELL in the
    same turn's market phase, TILLA_RULES.md §17, §20)."""
    return hour_of_action + distance + 1


def final_day_harvest_deadline(distance: int) -> int:
    """Last hour a HARVEST/COLLECT on the final day can happen and still be
    dropped and sold by the last turn (hour 23)."""
    return TURNS_PER_DAY - 1 - distance - 1


def final_day_water_useful(day: int, hour: int, plant: PlantTile, distance: int) -> bool:
    """On the final day, WATER still pays only on a one-time crop inside its
    bonus window below the yield cap (the bonus unit appears when watered,
    TILLA_RULES.md §10) that can then be harvested and delivered today."""
    spec = CROPS.get(plant.crop)
    if spec is None or spec.ongoing or plant.watered_today:
        return False
    age = plant_age(day, plant)
    if not (bonus_window_start(spec) <= age <= spec.max_yield_day):
        return False
    if plant.yield_units >= spec.max_yield:
        return False
    return hour <= final_day_harvest_deadline(distance) - 1  # water, then harvest next turn


def final_day_harvestable(day: int, plant: PlantTile) -> bool:
    """HARVEST succeeds today (TILLA_RULES.md §7): anything left on the field
    at the end is lost, so on the final day partial yield is harvested too."""
    spec = CROPS.get(plant.crop)
    return (
        spec is not None and plant.yield_units > 0 and plant_age(day, plant) >= spec.first_yield_day
    )


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


def bonus_window_start(spec: CropSpec) -> int:
    """First age of a one-time crop's watering bonus window (TILLA_RULES.md §10)."""
    return (spec.max_yield_day + 1) // 2


def one_time_harvest_age(spec: CropSpec) -> int:
    """Age at which a daily-watered, unfertilized one-time crop stops gaining
    yield: its yield cap or its max-yield day, whichever comes first."""
    return min(spec.max_yield_day, bonus_window_start(spec) + spec.max_yield - 2)


def one_time_units_at_age(spec: CropSpec, age: int) -> int:
    """Yield of a daily-watered, unfertilized one-time crop harvested at ``age``."""
    if age < spec.first_yield_day:
        return 0
    bonus_days = max(0, min(age, spec.max_yield_day) - bonus_window_start(spec) + 1)
    return min(spec.max_yield, 1 + bonus_days)


def plant_at_risk(plant: PlantTile) -> bool:
    """Dies at tonight's refresh unless watered today (includes fresh plantings)."""
    return not plant.watered_today and plant.consecutive_unwatered >= 1


def plant_needs_water(day: int, plant: PlantTile) -> bool:
    """Unwatered today and still able to benefit: one-time crops past their
    bonus window are decaying and only need harvesting."""
    if plant.watered_today:
        return False
    spec = CROPS.get(plant.crop)
    if spec is not None and not spec.ongoing:
        if plant_age(day, plant) > spec.max_yield_day or plant.yield_units >= spec.max_yield:
            return False  # decaying or already at the yield cap: harvest instead
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
    if plant.yield_units >= spec.max_yield:
        return True  # cap reached: further watering adds nothing
    return age > spec.max_yield_day or (age == spec.max_yield_day and plant.watered_today)


def animal_at_risk(structure: StructureTile) -> bool:
    """Escapes at tonight's refresh unless fed today."""
    a = structure.animal
    return a is not None and not a.fed_today and a.consecutive_unfed >= 1


def animal_needs_feed(structure: StructureTile) -> bool:
    return structure.animal is not None and not structure.animal.fed_today


def animal_harvest_ready(structure: StructureTile, day: int | None = None) -> bool:
    """Product is worth a trip: the next production would hit the tile's
    ``max_held`` cap (lost output), or the season is ending and any product is
    unharvested. ``day=None`` means "any product" (Milestone 2 behaviour)."""
    a = structure.animal
    if a is None or a.yield_units <= 0:
        return False
    if day is None:
        return True
    spec = ANIMALS.get(a.animal)
    if spec is None or day >= LAST_DAY - 1:
        return True
    return a.yield_units >= spec.max_held - 1


def animal_fertilizer_ready(structure: StructureTile) -> bool:
    """One fertilizer unit is waiting; it does not accumulate, so collect it today."""
    return structure.animal is not None and structure.animal.fertilizer_available


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


# --- Shed capacity (TILLA_RULES.md §4; thresholds TILLA_STRATEGY.md §5) -----------------


def shed_occupancy(state: GameState) -> int:
    """Non-seed items in the shed right now."""
    return sum(state.private.shed.values())


def shed_free_capacity(state: GameState) -> int:
    return max(0, SHED_CAPACITY - shed_occupancy(state))


def shed_pressure(state: GameState) -> float:
    """0 below SHED_PRESSURE_START, rising linearly to 1 at SHED_EMERGENCY and above."""
    occupancy = shed_occupancy(state)
    if occupancy < SHED_PRESSURE_START:
        return 0.0
    span = max(1, SHED_EMERGENCY - SHED_PRESSURE_START)
    return min(1.0, (occupancy - SHED_PRESSURE_START) / span)


def overflow_risk(state: GameState) -> int:
    """Items that would be discarded if every carried unit were dropped now
    (the end-of-day auto-drop does exactly that)."""
    carried_units = sum(carried_total(unit) for unit in state.me.units)
    return max(0, carried_units - shed_free_capacity(state))


# --- Town demand schedule and observed market flow (TILLA_RULES.md §19) -------------------


def town_center_units(day: int) -> int:
    """Units of every non-fertilizer product one town-center tick removes on ``day``."""
    for first_day, units in TOWN_CENTER_DEMAND_SCHEDULE:
        if day >= first_day:
            return units
    return 0


def shop_units(shop: str, product: str) -> int:
    """Units of ``product`` one tick of ``shop`` removes (2 for single-product shops)."""
    products = SHOPS[shop]
    if product not in products:
        return 0
    return 2 if len(products) == 1 else 1


def market_residuals(snapshots: list[MarketSnapshot], product: str) -> list[tuple[int, int]]:
    """Observed per-turn market inventory change of ``product`` with the
    scheduled town consumption of that turn added back: ``(after_step,
    residual)`` for each consecutive pair of snapshots. A positive residual is
    units some player sold into the market that turn (net of purchases); it is
    never attributed to a player here."""
    residuals: list[tuple[int, int]] = []
    for before, after in zip(snapshots, snapshots[1:], strict=False):
        if after.step != before.step + 1:
            continue  # gap in observations: no delta
        delta = after.inventory.get(product, 0) - before.inventory.get(product, 0)
        town = 0
        if before.step % SHOP_SELL_INTERVAL == 0:
            town += sum(shop_units(shop, product) for shop in before.unlocked_shops)
        if before.step % TOWN_CENTER_INTERVAL == 0 and product in TOWN_CENTER_PRODUCTS:
            town += town_center_units(before.day)
        residuals.append((after.step, delta + town))
    return residuals
