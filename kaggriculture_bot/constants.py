"""Stable copied/derived game constants and policy parameters.

Each official constant is defined here exactly once. See ``TILLA_RULES.md`` for
mechanics and ``TILLA_STRATEGY.md`` for the meaning of policy parameters.
Official action/op vocabularies live as enums in ``models.py``.
"""

# Kaggriculture is a 2-player environment (kaggriculture.json "agents": [2]).
PLAYER_COUNT = 2

# Default maximum market orders processed per player per turn; extra orders are
# silently dropped by the environment (TILLA_RULES.md §6).
MAX_MARKET_ORDERS_PER_TURN = 10

# Bound on retained opponent summaries in EpisodeMemory (implementation
# parameter, not a game rule). Summaries themselves arrive with Milestone 6.
OPPONENT_HISTORY_LIMIT = 64
