"""Kaggle adapter for Tilla. Exposes ``agent(obs)``.

Adapter only: no strategy, economics, pathfinding, or duplicated game
constants live here. This module owns the outer exception boundary and the
structurally valid PASS fallback.
"""

from kaggriculture_bot.actions import build_pass_action, fallback_pass_action
from kaggriculture_bot.parser import count_hired_hands


def agent(obs):
    """Return one Kaggriculture action dict for the current observation.

    Milestone 0: every unit deliberately passes. Later milestones insert the
    parser -> features -> opponent -> economy -> strategy -> tasks -> actions ->
    validator flow between observation and returned action.
    """
    hand_count = 0
    try:
        hand_count = count_hired_hands(obs)
        return build_pass_action(hand_count)
    except Exception:  # competition survival boundary, not normal control flow
        return fallback_pass_action(hand_count)
