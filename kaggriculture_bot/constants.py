"""Stable copied/derived game constants and policy parameters.

Each official constant is defined here exactly once. See ``TILLA_RULES.md`` for
mechanics and ``TILLA_STRATEGY.md`` for the meaning of policy parameters.
Official action/op vocabularies live as enums in ``models.py``.
"""

from dataclasses import dataclass

# --- Official game constants (verified by tests/test_rules_conformance.py) ------------

# Kaggriculture is a 2-player environment (kaggriculture.json "agents": [2]).
PLAYER_COUNT = 2

# Default maximum market orders processed per player per turn; extra orders are
# silently dropped by the environment (TILLA_RULES.md §6).
MAX_MARKET_ORDERS_PER_TURN = 10

# Season timing (TILLA_RULES.md §1): 24 turns per day, 30 days, days 0-indexed.
TURNS_PER_DAY = 24
SEASON_DAYS = 30
LAST_DAY = SEASON_DAYS - 1


@dataclass(frozen=True)
class CropSpec:
    seed: int  # fixed seed purchase price
    first_yield_day: int  # earliest age (days since planting) HARVEST succeeds
    max_yield_day: int  # last bonus-window day for one-time crops
    interval: int  # production interval in days for ongoing crops (0 = one-time)
    max_yield: int  # one-time: yield cap; ongoing: cumulative scheduled productions
    ongoing: bool


# Official crop table (kaggriculture.py CROPS, 1.30.2; TILLA_RULES.md §8).
CROPS = {
    "WHEAT": CropSpec(10, 2, 4, 0, 6, False),
    "CARROT": CropSpec(20, 2, 3, 0, 4, False),
    "TOMATO": CropSpec(50, 8, 8, 1, 4, True),
    "STRAWBERRY": CropSpec(100, 10, 10, 2, 4, True),
    "MELON": CropSpec(80, 10, 12, 0, 6, False),
}


@dataclass(frozen=True)
class AnimalSpec:
    cost: int  # fixed purchase price
    structure: str  # "COOP" or "PASTURE"
    first_yield_day: int  # days after placement until the first production
    interval: int  # days between productions
    max_held: int  # unharvested product cap on the tile
    product: str


# Official animal table (kaggriculture.py ANIMALS, 1.30.2; TILLA_RULES.md §12).
ANIMALS = {
    "GOOSE": AnimalSpec(300, "COOP", 4, 1, 4, "EGG"),
    "COW": AnimalSpec(400, "PASTURE", 8, 2, 6, "MILK"),
    "SHEEP": AnimalSpec(500, "PASTURE", 6, 3, 6, "WOOL"),
}

# Land unlock prices in fixed order NE, SW, SE (TILLA_RULES.md §3).
LAND_PRICES = (1000, 2000, 4000)

# Non-seed shed capacity (TILLA_RULES.md §4).
SHED_CAPACITY = 100

# Items referred to by name.
WHEAT = "WHEAT"
FERTILIZER = "FERTILIZER"
PRODUCTS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON", "EGG", "MILK", "WOOL", "FERTILIZER")

# --- Policy parameters (TILLA_STRATEGY.md §20; meaning documented there) ---------------

EARLY_PHASE_END_DAY = 7
SCALE_PHASE_END_DAY = 20
HARVEST_PHASE_END_DAY = 26
LIQUIDATE_START_DAY = 27

EARLY_MIN_CASH_RESERVE = 300
MID_MIN_CASH_RESERVE = 300
HARVEST_MIN_CASH_RESERVE = 200

SHED_PRESSURE_START = 85
SHED_EMERGENCY = 95

# --- Economic model parameters (Milestone 3; TILLA_STRATEGY.md §6, §7, §9, §10) ------

# Wheat kept in the shed per existing animal so feed is never sold away
# (today's and tomorrow's known feed obligation).
FEED_WHEAT_RESERVE_PER_ANIMAL = 2

# Labor opportunity cost: every unit action (including a travel step) is
# charged the current marginal action value, which rises linearly with farmer
# utilization from this idle floor to the best available crop's value per
# action (economy.labor_price). Explainable, no shadow-price optimizer.
LABOR_COST_PER_ACTION = 3.0

# Amortized unit actions per day the single farmer can commit to recurring
# care (water/feed/harvest plus a travel step each). Opportunities that would
# push commitments past this budget are not realizable yet.
FARMER_DAILY_ACTION_BUDGET = 20.0

# Amortized daily care charged for assets already on the farm.
PLANT_DAILY_ACTIONS = 2.0  # WATER + one travel step
ANIMAL_DAILY_ACTIONS = 3.0  # FEED + COLLECT_FERTILIZER + amortized HARVEST/travel (batched)

# Land opportunity cost: value of one tile-day when tiles are scarce, scaled by
# scarcity (zero while at least LAND_SCARCITY_FREE_TILES empty tiles remain).
LAND_TILE_DAY_VALUE = 18.0
LAND_SCARCITY_FREE_TILES = 8

# Execution risk: each day an opportunity stays exposed (weed/care/timing risk).
EXECUTION_RISK_PER_DAY = 0.5

# Neutral placeholders that keep the source-of-truth equation complete until
# the market (M5) and opponent (M6) models exist.
MARKET_GLUT_PENALTY = 0.0
PHASE_WEIGHT = 1.0

# Share of an animal's daily fertilizer byproduct assumed collected and sold.
FERTILIZER_BYPRODUCT_REALIZATION = 0.5

# Dynamic cash reserve components (TILLA_STRATEGY.md §6): hired-hand budget is a
# documented zero until hiring is part of policy (Milestone 4).
RESERVE_HAND_BUDGET = 0
RESERVE_EMERGENCY_BUFFER = 50

# Bound on retained opponent summaries in EpisodeMemory (implementation
# parameter, not a game rule). Summaries themselves arrive with Milestone 6.
OPPONENT_HISTORY_LIMIT = 64
