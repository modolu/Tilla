"""Kaggle adapter for Tilla. Exposes ``agent(obs)``.

Adapter only: no strategy, economics, pathfinding, or duplicated game
constants live here. This module owns the outer exception boundary and the
structurally valid PASS fallback.
"""

from kaggriculture_bot.actions import build_action, fallback_pass_action
from kaggriculture_bot.parser import parse_observation
from kaggriculture_bot.runtime import get_episode_memory, remember_turn
from kaggriculture_bot.strategy import choose_plan
from kaggriculture_bot.tasks import assign_jobs
from kaggriculture_bot.validator import validate_or_fallback


def agent(obs):
    """Return one Kaggriculture action dict for the current observation.

    parse -> episode memory -> strategy plan -> task assignment -> format ->
    validate. Features/opponent/economy layers join this chain in later
    milestones.
    """
    hand_count = 0
    try:
        state = parse_observation(obs)
        hand_count = len(state.me.hands)
        memory = get_episode_memory(state.player_id, state.step)
        plan = choose_plan(state, memory)
        turn = assign_jobs(state, plan, memory)
        action = validate_or_fallback(build_action(turn), hand_count)
        remember_turn(memory, state)
        return action
    except Exception:  # competition survival boundary, not normal control flow
        return fallback_pass_action(hand_count)
