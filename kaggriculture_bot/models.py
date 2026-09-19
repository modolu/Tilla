"""Typed internal state, action, and value objects.

Parsed state objects are frozen dataclasses so that ``GameState`` is immutable
after parsing. Mapping fields (``dict[str, int]``) are fresh copies built by the
parser and never alias the raw Kaggle observation.

All real game quantities (money, counts, positions, days) are ``int``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from kaggriculture_bot.constants import OPPONENT_HISTORY_LIMIT

# --- Board tiles ---------------------------------------------------------------
#
# Official tile variants (kaggriculture.py 1.30.2): ``None`` (empty unlocked),
# ``"LOCKED"``, ``{"kind": "PLANT", ...}``, ``{"kind": "WEED"}``, and
# ``{"kind": "COOP" | "PASTURE", ...}`` which carries animal fields only while
# an animal is placed (an empty structure has no ``animal`` key).


class TileKind(StrEnum):
    EMPTY = "EMPTY"
    LOCKED = "LOCKED"
    PLANT = "PLANT"
    WEED = "WEED"
    COOP = "COOP"
    PASTURE = "PASTURE"


STRUCTURE_KINDS = frozenset({TileKind.COOP, TileKind.PASTURE})


@dataclass(frozen=True)
class EmptyTile:
    kind: TileKind = TileKind.EMPTY


@dataclass(frozen=True)
class LockedTile:
    kind: TileKind = TileKind.LOCKED


@dataclass(frozen=True)
class WeedTile:
    kind: TileKind = TileKind.WEED


@dataclass(frozen=True)
class PlantTile:
    crop: str
    planted_day: int
    watered_today: bool
    consecutive_unwatered: int
    yield_units: int
    max_lifespan_step: int  # -1 for ongoing crops
    fertilized_until_day: int  # -1 when not fertilized
    kind: TileKind = TileKind.PLANT


@dataclass(frozen=True)
class AnimalState:
    """Fields present on a coop/pasture tile only while an animal occupies it."""

    animal: str
    placed_day: int
    yield_units: int
    consecutive_unfed: int
    fed_today: bool
    cared_today: bool
    fertilizer_available: bool
    pending_care_bonus: int


@dataclass(frozen=True)
class StructureTile:
    kind: TileKind  # COOP or PASTURE
    animal: AnimalState | None  # None while the structure is empty


Tile = EmptyTile | LockedTile | WeedTile | PlantTile | StructureTile

EMPTY_TILE = EmptyTile()
LOCKED_TILE = LockedTile()
WEED_TILE = WeedTile()


# --- Farm, private, market, town, game state ---------------------------------------


@dataclass(frozen=True)
class Position:
    x: int
    y: int


@dataclass(frozen=True)
class UnitState:
    index: int  # 0 farmer, 1+ hired hands in official hand order
    position: Position
    # Carried inventory. ``None`` means unobservable: opponent units' carried
    # inventories are private and are never invented as empty.
    inventory: dict[str, int] | None


@dataclass(frozen=True)
class FarmState:
    player_id: int
    money: int
    tiles: tuple[tuple[Tile, ...], ...]  # tiles[y][x]
    units: tuple[UnitState, ...]  # (farmer, *hands)
    unlocked_quadrants: frozenset[str]
    hires_today: int

    @property
    def farmer(self) -> UnitState:
        return self.units[0]

    @property
    def hands(self) -> tuple[UnitState, ...]:
        return self.units[1:]

    @property
    def board_size(self) -> int:
        return len(self.tiles)


@dataclass(frozen=True)
class PrivateState:
    shed: dict[str, int]
    seeds: dict[str, int]


@dataclass(frozen=True)
class MarketState:
    inventory: dict[str, int]
    prices: dict[str, int]


@dataclass(frozen=True)
class TownState:
    unlocked_shops: tuple[str, ...]  # official order; multiplicity preserved


@dataclass(frozen=True)
class GameState:
    step: int
    day: int
    hour: int
    player_id: int
    me: FarmState
    opponent: FarmState
    private: PrivateState
    market: MarketState
    town: TownState


# --- Typed actions -------------------------------------------------------------------
#
# Official op vocabularies (TILLA_RULES.md §6-§7). ``actions.py`` is the only
# module that turns these into Kaggle list/string shape.


class UnitOp(StrEnum):
    PASS = "PASS"
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST = "EAST"
    WEST = "WEST"
    PICKUP = "PICKUP"
    DROP = "DROP"
    PLANT = "PLANT"
    WATER = "WATER"
    HARVEST = "HARVEST"
    FERTILIZE = "FERTILIZE"
    DIG = "DIG"
    BUILD_COOP = "BUILD_COOP"
    BUILD_PASTURE = "BUILD_PASTURE"
    PLACE = "PLACE"
    FEED = "FEED"
    COLLECT_FERTILIZER = "COLLECT_FERTILIZER"
    CARE = "CARE"


class MarketOp(StrEnum):
    BUY_SEED = "BUY_SEED"
    BUY_PRODUCT = "BUY_PRODUCT"
    BUY_ANIMAL = "BUY_ANIMAL"
    SELL = "SELL"
    HIRE = "HIRE"
    BUY_LAND = "BUY_LAND"


# Ops whose official form carries an item argument (``PICKUP``/``PLACE`` may
# also carry a quantity). Market ops other than HIRE/BUY_LAND carry item + qty.
UNIT_OPS_WITH_ITEM = frozenset({UnitOp.PICKUP, UnitOp.PLANT, UnitOp.PLACE})
MARKET_OPS_WITHOUT_ITEM = frozenset({MarketOp.HIRE, MarketOp.BUY_LAND})


@dataclass(frozen=True)
class UnitAction:
    op: UnitOp
    item: str | None = None
    quantity: int | None = None


@dataclass(frozen=True)
class MarketOrder:
    op: MarketOp
    item: str | None = None
    quantity: int | None = None


@dataclass(frozen=True)
class TurnAction:
    """One turn's complete typed decision: farmer, hands (in hand order), market."""

    farmer: UnitAction
    hands: tuple[UnitAction, ...] = ()
    market: tuple[MarketOrder, ...] = ()


PASS_UNIT_ACTION = UnitAction(UnitOp.PASS)


def pass_turn_action(hand_count: int) -> TurnAction:
    """Typed turn in which every unit passes and no market orders are placed."""
    if hand_count < 0:
        raise ValueError(f"hand_count must be non-negative, got {hand_count}")
    return TurnAction(
        farmer=PASS_UNIT_ACTION,
        hands=tuple(PASS_UNIT_ACTION for _ in range(hand_count)),
        market=(),
    )


@dataclass(frozen=True)
class HiringDecision:
    """Explainable outcome of the same-day hiring estimate (TILLA_STRATEGY.md §12)."""

    existing_units: int
    hires_today: int
    remaining_turns: int  # turns a hand hired now could still act today
    backlog_actions: float  # estimated actions in today's job backlog
    existing_capacity: float  # actions the current workforce can still perform today
    uncovered_actions: float
    action_value: float  # marginal value of one action (economy.labor_price)
    costs: tuple[int, ...]  # sequential Fibonacci cost of each recommended hire
    values: tuple[float, ...]  # marginal value of each recommended hire
    next_cost: int  # cost of the first hire NOT recommended (or the next one)
    hires: int
    reason: str = ""
    care_actions: float = 0.0  # part of the backlog that keeps existing assets alive
    spawns: tuple[Position, ...] = ()  # predicted spawn tile of each recommended hire


# --- Episode memory --------------------------------------------------------------------


# --- Strategic objectives and plans ------------------------------------------------------


class ObjectiveKind(StrEnum):
    """What the acting unit should accomplish this turn (strategy decides which)."""

    WATER_CROP = "WATER_CROP"  # WATER on a target plant tile
    FEED_ANIMAL = "FEED_ANIMAL"  # FEED on a target animal tile (wheat must be carried)
    FETCH_FEED = "FETCH_FEED"  # PICKUP item/quantity at a shed access tile
    FETCH_ITEM = "FETCH_ITEM"  # PICKUP any item/quantity at a shed access tile
    HARVEST = "HARVEST"  # HARVEST on a target tile
    COLLECT_FERTILIZER = "COLLECT_FERTILIZER"  # COLLECT_FERTILIZER on a target animal tile
    PLANT = "PLANT"  # PLANT item on a target empty tile, only until deadline_hour
    BUILD_STRUCTURE = "BUILD_STRUCTURE"  # BUILD_COOP / BUILD_PASTURE (item) on an empty tile
    PLACE_ANIMAL = "PLACE_ANIMAL"  # PLACE item on a matching empty structure
    FERTILIZE_CROP = "FERTILIZE_CROP"  # FERTILIZE on a target plant (fertilizer carried)
    DELIVER = "DELIVER"  # DROP carried inventory at a shed access tile
    REPOSITION = "REPOSITION"  # move toward a target, no on-tile action
    PASS = "PASS"


# Objective priorities (TILLA_STRATEGY.md §3/§16): lower is more urgent.
PRIORITY_SURVIVAL = 1  # lost tonight (or within turns) without action
PRIORITY_DAILY_WORK = 2  # mandatory care, harvest/collect, started work
PRIORITY_DELIVERY = 3  # final-day delivery of carried produce
PRIORITY_ECONOMIC = 4  # planned production / expansion
PRIORITY_IDLE = 5  # reposition / idle delivery


@dataclass(frozen=True)
class Objective:
    """A typed objective. ``targets`` are candidate tiles in stable (y, x)
    order; the task layer turns each target into one job and assigns units."""

    kind: ObjectiveKind
    targets: tuple[Position, ...] = ()
    item: str | None = None
    quantity: int | None = None
    deadline_hour: int | None = None  # last hour the on-tile action may still be performed
    priority: int = PRIORITY_ECONOMIC


PASS_OBJECTIVE = Objective(ObjectiveKind.PASS, priority=PRIORITY_IDLE)


@dataclass(frozen=True)
class StrategicPlan:
    """One turn's strategic decision.

    ``objective`` and ``equal_priority`` describe the top priority tier (the
    Milestone 2/3 contract, still used by the frozen baseline). ``objectives``
    (Milestone 4) lists every objective of every tier in priority order so
    several units can work at once; when empty it is derived from the top
    tier. ``hires`` is the number of HIRE orders included in ``market``.
    """

    objective: Objective = PASS_OBJECTIVE
    market: tuple[MarketOrder, ...] = ()
    equal_priority: tuple[Objective, ...] = ()
    objectives: tuple[Objective, ...] = ()
    hires: int = 0

    def all_objectives(self) -> tuple[Objective, ...]:
        """Every objective in priority order (PASS objectives excluded)."""
        listed = self.objectives or (self.objective, *self.equal_priority)
        return tuple(o for o in listed if o.kind is not ObjectiveKind.PASS)


# --- Unit-level jobs (Milestone 4) ------------------------------------------------------------


class JobKind(StrEnum):
    WATER = "WATER"
    FEED = "FEED"  # requires carried WHEAT
    HARVEST = "HARVEST"
    COLLECT = "COLLECT"  # COLLECT_FERTILIZER
    PLANT = "PLANT"  # item = crop
    BUILD = "BUILD"  # item = structure kind
    PLACE = "PLACE"  # item = animal, requires it carried
    FERTILIZE = "FERTILIZE"  # requires carried FERTILIZER
    DELIVER = "DELIVER"  # DROP at a shed access tile; bound to one unit
    MOVE = "MOVE"  # reposition only


@dataclass(frozen=True)
class Job:
    """One concrete unit-level piece of work on one tile.

    ``key`` is the stable identity used for persistence and conflict
    avoidance; ``requires`` names a carried item the acting unit must hold
    (fetched from the shed first when missing); ``unit`` pins a job to one
    unit (delivery of what that unit carries)."""

    kind: JobKind
    target: Position
    priority: int
    item: str | None = None
    quantity: int | None = None
    deadline_hour: int | None = None
    requires: str | None = None
    unit: int | None = None

    @property
    def key(self) -> tuple:
        return (self.kind.value, self.target.x, self.target.y, self.item, self.unit)


# --- Economic value objects (Milestone 3) --------------------------------------------------


class OpportunityKind(StrEnum):
    CROP = "CROP"  # plant one tile of `product`
    ANIMAL = "ANIMAL"  # build structure, buy and place one `product`
    FERTILIZE = "FERTILIZE"  # fertilize the existing plant at `target`


@dataclass(frozen=True)
class OpportunityEstimate:
    """One scored opportunity (TILLA_STRATEGY.md §7). Monetary fields are
    estimates in coins (float); ``setup_cash`` is the integer cash outlay still
    required (0 when seeds/animal are already held)."""

    kind: OpportunityKind
    product: str
    setup_cost: float
    setup_cash: int
    input_cost: float
    expected_revenue: float
    byproduct_value: float
    labor_cost: float
    land_cost: float
    market_penalty: float
    execution_risk: float
    expected_net_value: float
    turns_to_realize: int
    realization_probability: float
    phase_weight: float
    score: float
    actions_required: float
    daily_actions: float
    occupancy_days: int
    expected_units: float
    target: Position | None = None  # FERTILIZE: the plant tile
    reason: str = ""  # why realization is zero, for diagnostics


@dataclass
class EpisodeMemory:
    """Bounded in-memory state for one player within one episode.

    ``opponent_history`` is a bounded deque of summaries; the summary type is
    defined by the opponent-model milestone and nothing is recorded before then.
    """

    player_id: int
    last_step: int = -1
    previous_market_inventory: dict[str, int] = field(default_factory=dict)
    opponent_history: deque = field(default_factory=lambda: deque(maxlen=OPPONENT_HISTORY_LIMIT))
    inferred_opponent_pipeline: dict[str, float] = field(default_factory=dict)
    current_plan: StrategicPlan | None = None
    plan_created_step: int | None = None
    # Movement-to-task persistence: the job each of our units (by current
    # unit index) was assigned last turn, valid only for ``assignment_day``.
    unit_assignments: dict[int, Job] = field(default_factory=dict)
    assignment_day: int | None = None
