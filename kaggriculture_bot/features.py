"""Derived farm, market, season, and state features.

Pure read-only helpers over ``GameState``; no decisions, no pathfinding. The
mechanics they encode are those verified in ``TILLA_RULES.md`` §4, §8-§10, §13.
"""

from __future__ import annotations

from kaggriculture_bot.constants import (
    CROPS,
    EARLY_MIN_CASH_RESERVE,
    EARLY_PHASE_END_DAY,
    HARVEST_MIN_CASH_RESERVE,
    HARVEST_PHASE_END_DAY,
    LAST_DAY,
    MID_MIN_CASH_RESERVE,
    SCALE_PHASE_END_DAY,
    TURNS_PER_DAY,
)
from kaggriculture_bot.models import (
    FarmState,
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
