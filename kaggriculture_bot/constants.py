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

# --- Market and town mechanics (TILLA_RULES.md §16-§19; verified 1.30.2) ----------------

# Official market price model (kaggriculture.py MARKET_PARAMS):
#   price(inv) = base + sign * amp * f(|inv - I0|), amp = target * base / f(T),
#   sign +1 below I0 (scarcity) and -1 above (glut); f in linear/sq/sqrt/log
#   (log = ln(1 + x)); rounded to the nearest coin and floored at PRICE_FLOOR.
MARKET_I0 = 10000
PRICE_FLOOR = 1
# product: (base, T, below_func, below_target, above_func, above_target)
MARKET_PARAMS = {
    "WHEAT": (25, 400, "sqrt", 0.80, "log", 0.20),
    "CARROT": (35, 450, "log", 0.20, "sqrt", 0.70),
    "TOMATO": (60, 200, "linear", 0.40, "sqrt", 0.60),
    "STRAWBERRY": (120, 100, "sqrt", 0.70, "linear", 1.60),
    "MELON": (250, 300, "log", 0.20, "sq", 3.60),
    "EGG": (50, 332, "linear", 0.40, "log", 0.20),
    "MILK": (160, 122, "sqrt", 0.60, "linear", 1.60),
    "WOOL": (200, 105, "log", 0.20, "sq", 3.20),
    "FERTILIZER": (100, 200, "linear", 0.40, "linear", 0.40),
}
# Products only ever bought/sold at the dynamic market price (BUY_PRODUCT).
DYNAMIC_BUY_PRODUCTS = ("WHEAT", "FERTILIZER")

# Town demand (kaggriculture.py SHOPS, TOWN_CENTER_*; TILLA_RULES.md §19).
SHOPS = {
    "BAKERY": ("EGG", "WHEAT"),
    "PIZZA_SHOP": ("MILK", "TOMATO", "WHEAT"),
    "BRUNCH_SPOT": ("EGG", "WHEAT", "STRAWBERRY"),
    "YARN_STORE": ("WOOL",),
    "ICE_CREAM_SHOP": ("STRAWBERRY", "MILK", "WHEAT"),
    "PET_CAFE": ("CARROT",),
    "SMOOTHIE_SHOP": ("STRAWBERRY", "MILK"),
    "FARMERS_MARKET": ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY"),
}
TOWN_CENTER_INTERVAL = 12  # turns between town-center ticks (step % 12 == 0)
SHOP_SELL_INTERVAL = 4  # turns between shop ticks (step % 4 == 0)
SHOP_UNLOCK_INTERVAL = 3  # one shop unlocks at the end of day d when (d + 1) % 3 == 0
TOWN_CENTER_DEMAND_SCHEDULE = ((20, 4), (10, 2), (0, 1))  # (first day, units per product per tick)
TOWN_CENTER_PRODUCTS = tuple(p for p in PRODUCTS if p != FERTILIZER)

# Premium goods whose official glut curves fall fastest (TILLA_STRATEGY.md §13).
PREMIUM_PRODUCTS = ("STRAWBERRY", "MELON", "MILK", "WOOL")

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

# Neutral placeholder that keeps the source-of-truth equation complete until
# the phase engine (M7) exists. The market term is computed by the market
# model (Milestone 5, economy.market_penalty).
PHASE_WEIGHT = 1.0

# Share of an animal's daily fertilizer byproduct assumed collected and sold.
FERTILIZER_BYPRODUCT_REALIZATION = 0.5

# Dynamic cash reserve components (TILLA_STRATEGY.md §6). The hand budget is
# computed from the hiring policy (economy.expected_hand_spend).
RESERVE_EMERGENCY_BUFFER = 50

# --- Milestone 4 hiring / multi-unit parameters (TILLA_STRATEGY.md §12) --------------

# Hands the planner budgets for when sizing production it will have to care
# for daily (hired only when the same-day marginal value clears the cost).
PLANNED_DAILY_HANDS = 3

# Amortized daily care actions one hired hand contributes to the labor budget.
HAND_DAILY_ACTIONS = 16.0

# Unit actions a job costs a hand: the action itself plus expected travel.
HAND_ACTIONS_PER_JOB = 2.5

# Turns a new hand spends spawning and walking to its first job.
HAND_SETUP_ACTIONS = 3

# A hand is hired only if it can perform at least this many useful actions today.
MIN_HAND_USEFUL_ACTIONS = 4

# Hard cap on hires per day (the Fibonacci cost curve makes more pointless).
MAX_DAILY_HIRES = 6
# A job that must start within this many turns to still finish today is
# scheduled ahead of routine work when the other units can cover that work.
PROMOTION_SLACK_TURNS = 1

# Bound on retained opponent summaries in EpisodeMemory (implementation
# parameter, not a game rule). Summaries themselves arrive with Milestone 6.
OPPONENT_HISTORY_LIMIT = 64

# --- Milestone 5 market and town parameters (TILLA_STRATEGY.md §13) -----------------------

# Bounded public market history kept in EpisodeMemory (one snapshot per turn).
MARKET_HISTORY_TURNS = 24
# Aggregate flow estimator: median residual inventory change per turn (town
# consumption removed) over the most recent observed deltas ...
TREND_WINDOW_TURNS = 12
# ... clamped per turn to this fraction of the product's T (the official
# one-field season capacity) and damped before extrapolation, so one dump
# never dominates a horizon.
TREND_CAP_FRACTION_OF_T = 0.02
TREND_DAMPING = 0.5
# ... and extrapolated for at most this many turns of a horizon (a recent flow
# is not assumed to persist for days).
TREND_MAX_EXTRAPOLATION_TURNS = 24
# Sale-timing horizon for shed stock: hold is evaluated at the turn after the
# next town-center tick, never further than this many turns ahead.
HOLD_HORIZON_TURNS = 13
# A unit is held only if its projected later price beats selling it now by at
# least this fraction (execution/uncertainty buffer for the interleaved market).
SELL_HOLD_MIN_UPLIFT = 0.05
# Share of a projected price *improvement* counted in an opportunity's revenue.
# 0: investment revenue never assumes prices will rise (scarcity is not bonus
# money; today's price already reflects today's scarcity). Projected
# improvements are used only for sell timing (hold decisions).
MAX_SCARCITY_UPLIFT_FRACTION = 0.0
# Market pressure bands on price / base (the official curves make them product-specific):
PRESSURE_SCARCE_RATIO = 1.10
PRESSURE_GLUT_RATIO = 0.90
PRESSURE_SEVERE_RATIO = 0.60
# Premium glut protection: a new premium investment is rejected when the price
# after selling its own output into the projected market falls to this share of base.
PREMIUM_FLOOR_RISK_RATIO = 0.25
