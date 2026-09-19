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

# Items and ops the baseline refers to by name.
WHEAT = "WHEAT"

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

# --- Milestone 2 baseline implementation parameters ------------------------------------

# Crop the farm-care baseline cycles (TILLA_STRATEGY.md §8: wheat is the
# short-turnaround, reliable-liquidity crop).
BASELINE_CROP = WHEAT

# Number of concurrent baseline plantings one unit can service in a day: a
# synchronized max-yield day costs about one move + WATER + one move + HARVEST
# per plant (~4 turns), so 6 plants fit inside the 24-turn day with margin.
BASELINE_MAX_PLANTS = 6

# Wheat kept in the shed per existing animal so feed is never sold away
# (today's and tomorrow's known feed obligation).
FEED_WHEAT_RESERVE_PER_ANIMAL = 2

# Bound on retained opponent summaries in EpisodeMemory (implementation
# parameter, not a game rule). Summaries themselves arrive with Milestone 6.
OPPONENT_HISTORY_LIMIT = 64
