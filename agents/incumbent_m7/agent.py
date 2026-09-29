"""Kaggle adapter for Tilla. Exposes ``agent(obs)``.

Adapter only: no strategy, economics, pathfinding, or duplicated game
constants live here. This module owns the outer exception boundary and the
structurally valid PASS fallback.
"""

from agents.incumbent_m7.actions import build_action, fallback_pass_action
from agents.incumbent_m7.opponent import update_model
from agents.incumbent_m7.parser import parse_observation
from agents.incumbent_m7.runtime import get_episode_memory, remember_turn
from agents.incumbent_m7.strategy import choose_plan
from agents.incumbent_m7.tasks import assign_jobs
from agents.incumbent_m7.validator import validate_or_fallback


def agent(obs):
    """Return one Kaggriculture action dict for the current observation.

    parse -> episode memory -> opponent forecast -> strategy plan (features,
    economy) -> task assignment -> format -> validate.
    """
    hand_count = 0
    try:
        state = parse_observation(obs)
        hand_count = len(state.me.hands)
        memory = get_episode_memory(state.player_id, state.step)
        update_model(state, memory)
        plan = choose_plan(state, memory)
        turn = assign_jobs(state, plan, memory)
        action = validate_or_fallback(build_action(turn), hand_count)
        remember_turn(memory, state)
        return action
    except Exception:  # competition survival boundary, not normal control flow
        return fallback_pass_action(hand_count)
