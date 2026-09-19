"""Raw Kaggle observation -> typed ``GameState``. Parsing only; no decisions.

Strict: official-schema observations parse deterministically; anything that
does not match the official schema raises ``ObservationError``. The safe
competition fallback lives at the outer boundary in ``main.py``, not here.

The parser never invents state. Opponent shed, seeds and carried inventories
are not observable and are represented as unobservable (``None``), not empty.
Unknown extra keys are ignored; missing required keys are errors.

Schema source: installed kaggle-environments 1.30.2 ``kaggriculture.py``
(``_new_farm``, ``_new_private``, ``_new_market``, ``_new_town``,
``_new_plant``, ``_new_animal``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from kaggriculture_bot.constants import PLAYER_COUNT
from kaggriculture_bot.models import (
    EMPTY_TILE,
    LOCKED_TILE,
    WEED_TILE,
    AnimalState,
    FarmState,
    GameState,
    MarketState,
    PlantTile,
    Position,
    PrivateState,
    StructureTile,
    Tile,
    TileKind,
    TownState,
    UnitState,
)


class ObservationError(ValueError):
    """The observation does not match the official Kaggriculture schema."""


# --- Scalar normalization -------------------------------------------------------


def as_int(value, field: str) -> int:
    """Normalize an official numeric state value to ``int``.

    The environment stores money as ``float`` (e.g. ``3000.0``); integral floats
    are converted exactly. Non-integral values, bools and non-numbers are errors
    rather than being truncated.
    """
    if isinstance(value, bool):
        raise ObservationError(f"{field}: expected integer, got bool {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ObservationError(f"{field}: expected integral value, got {value!r}")
    raise ObservationError(f"{field}: expected integer, got {type(value).__name__}")


def as_bool(value, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ObservationError(f"{field}: expected bool, got {type(value).__name__}")


def as_str(value, field: str) -> str:
    if isinstance(value, str):
        return value
    raise ObservationError(f"{field}: expected str, got {type(value).__name__}")


def _mapping(value, field: str) -> Mapping:
    if isinstance(value, Mapping):
        return value
    raise ObservationError(f"{field}: expected mapping, got {type(value).__name__}")


def _sequence(value, field: str) -> Sequence:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    raise ObservationError(f"{field}: expected list, got {type(value).__name__}")


def _require(mapping: Mapping, key: str, field: str):
    if key not in mapping:
        raise ObservationError(f"{field}: missing required key {key!r}")
    return mapping[key]


def _int_counts(value, field: str) -> dict[str, int]:
    """``{item: count}`` mapping with a fresh dict and normalized int counts."""
    mapping = _mapping(value, field)
    return {as_str(k, f"{field} key"): as_int(v, f"{field}[{k!r}]") for k, v in mapping.items()}


def _position(value, field: str) -> Position:
    seq = _sequence(value, field)
    if len(seq) != 2:
        raise ObservationError(f"{field}: expected [x, y], got length {len(seq)}")
    return Position(as_int(seq[0], f"{field}.x"), as_int(seq[1], f"{field}.y"))


# --- Tiles -----------------------------------------------------------------------------


def parse_tile(raw, field: str) -> Tile:
    if raw is None:
        return EMPTY_TILE
    if raw == TileKind.LOCKED.value:
        return LOCKED_TILE
    tile = _mapping(raw, field)
    kind = as_str(_require(tile, "kind", field), f"{field}.kind")
    if kind == TileKind.WEED.value:
        return WEED_TILE
    if kind == TileKind.PLANT.value:
        return PlantTile(
            crop=as_str(_require(tile, "crop", field), f"{field}.crop"),
            planted_day=as_int(_require(tile, "planted_day", field), f"{field}.planted_day"),
            watered_today=as_bool(_require(tile, "watered_today", field), f"{field}.watered_today"),
            consecutive_unwatered=as_int(
                _require(tile, "consecutive_unwatered", field), f"{field}.consecutive_unwatered"
            ),
            yield_units=as_int(_require(tile, "yield_units", field), f"{field}.yield_units"),
            max_lifespan_step=as_int(
                _require(tile, "max_lifespan_step", field), f"{field}.max_lifespan_step"
            ),
            fertilized_until_day=as_int(
                _require(tile, "fertilized_until_day", field), f"{field}.fertilized_until_day"
            ),
        )
    if kind in (TileKind.COOP.value, TileKind.PASTURE.value):
        animal_name = tile.get("animal")
        animal = None
        if animal_name is not None:
            animal = AnimalState(
                animal=as_str(animal_name, f"{field}.animal"),
                placed_day=as_int(_require(tile, "placed_day", field), f"{field}.placed_day"),
                yield_units=as_int(_require(tile, "yield_units", field), f"{field}.yield_units"),
                consecutive_unfed=as_int(
                    _require(tile, "consecutive_unfed", field), f"{field}.consecutive_unfed"
                ),
                fed_today=as_bool(_require(tile, "fed_today", field), f"{field}.fed_today"),
                cared_today=as_bool(_require(tile, "cared_today", field), f"{field}.cared_today"),
                fertilizer_available=as_bool(
                    _require(tile, "fertilizer_available", field), f"{field}.fertilizer_available"
                ),
                pending_care_bonus=as_int(
                    _require(tile, "pending_care_bonus", field), f"{field}.pending_care_bonus"
                ),
            )
        return StructureTile(kind=TileKind(kind), animal=animal)
    raise ObservationError(f"{field}: unknown tile kind {kind!r}")


def parse_tiles(raw, field: str) -> tuple[tuple[Tile, ...], ...]:
    rows = _sequence(raw, field)
    if not rows:
        raise ObservationError(f"{field}: board has no rows")
    parsed = tuple(
        tuple(
            parse_tile(cell, f"{field}[{y}][{x}]")
            for x, cell in enumerate(_sequence(row, f"{field}[{y}]"))
        )
        for y, row in enumerate(rows)
    )
    size = len(parsed)
    for y, row in enumerate(parsed):
        if len(row) != size:
            raise ObservationError(f"{field}[{y}]: expected {size} columns, got {len(row)}")
    return parsed


# --- Farms -----------------------------------------------------------------------------


def parse_farm(raw, player_id: int, inventories: Sequence | None, field: str) -> FarmState:
    """Parse one public farm. ``inventories`` is our private per-unit inventory
    list for our own farm, or ``None`` for the opponent (unobservable)."""
    farm = _mapping(raw, field)
    farmer_pos = _position(_require(farm, "farmer", field), f"{field}.farmer")
    hand_positions = [
        _position(p, f"{field}.hands[{i}]")
        for i, p in enumerate(_sequence(_require(farm, "hands", field), f"{field}.hands"))
    ]
    positions = [farmer_pos, *hand_positions]
    units = []
    for index, position in enumerate(positions):
        if inventories is None:
            inventory = None
        elif index < len(inventories):
            inventory = _int_counts(inventories[index], f"private.inventories[{index}]")
        else:
            # The environment's own accessor treats a missing entry as empty.
            inventory = {}
        units.append(UnitState(index=index, position=position, inventory=inventory))
    quadrants = _sequence(
        _require(farm, "unlocked_quadrants", field), f"{field}.unlocked_quadrants"
    )
    return FarmState(
        player_id=player_id,
        money=as_int(_require(farm, "money", field), f"{field}.money"),
        tiles=parse_tiles(_require(farm, "tiles", field), f"{field}.tiles"),
        units=tuple(units),
        unlocked_quadrants=frozenset(as_str(q, f"{field}.unlocked_quadrants[]") for q in quadrants),
        hires_today=as_int(_require(farm, "hires_today", field), f"{field}.hires_today"),
    )


# --- Top level ----------------------------------------------------------------------------


def parse_observation(obs) -> GameState:
    """Parse one official Kaggriculture observation into an immutable ``GameState``."""
    top = _mapping(obs, "obs")
    player_id = as_int(_require(top, "player", "obs"), "obs.player")
    if not 0 <= player_id < PLAYER_COUNT:
        raise ObservationError(f"obs.player: expected 0..{PLAYER_COUNT - 1}, got {player_id}")

    farms = _sequence(_require(top, "farms", "obs"), "obs.farms")
    if len(farms) != PLAYER_COUNT:
        raise ObservationError(f"obs.farms: expected {PLAYER_COUNT} farms, got {len(farms)}")

    private = _mapping(_require(top, "private", "obs"), "obs.private")
    inventories = _sequence(
        _require(private, "inventories", "obs.private"), "obs.private.inventories"
    )
    market = _mapping(_require(top, "market", "obs"), "obs.market")
    town = _mapping(_require(top, "town", "obs"), "obs.town")
    shops = _sequence(_require(town, "unlocked_shops", "obs.town"), "obs.town.unlocked_shops")

    opponent_id = 1 - player_id
    return GameState(
        step=as_int(_require(top, "step", "obs"), "obs.step"),
        day=as_int(_require(top, "day", "obs"), "obs.day"),
        hour=as_int(_require(top, "hour", "obs"), "obs.hour"),
        player_id=player_id,
        me=parse_farm(farms[player_id], player_id, inventories, f"obs.farms[{player_id}]"),
        opponent=parse_farm(farms[opponent_id], opponent_id, None, f"obs.farms[{opponent_id}]"),
        private=PrivateState(
            shed=_int_counts(_require(private, "shed", "obs.private"), "obs.private.shed"),
            seeds=_int_counts(_require(private, "seeds", "obs.private"), "obs.private.seeds"),
        ),
        market=MarketState(
            inventory=_int_counts(
                _require(market, "inventory", "obs.market"), "obs.market.inventory"
            ),
            prices=_int_counts(_require(market, "prices", "obs.market"), "obs.market.prices"),
        ),
        town=TownState(
            unlocked_shops=tuple(as_str(s, "obs.town.unlocked_shops[]") for s in shops),
        ),
    )
