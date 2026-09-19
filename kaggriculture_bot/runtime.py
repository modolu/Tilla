"""Bounded per-episode memory and reset behavior. No persistence across games.

Memory lives in this process only, keyed by ``player_id`` so that local
self-play with the same imported agent never mixes the two players' histories.
A new episode is detected when ``obs["step"] == 0`` (TILLA_ARCHITECTURE.md §5).
"""

from __future__ import annotations

from kaggriculture_bot.models import EpisodeMemory, GameState

_MEMORIES: dict[int, EpisodeMemory] = {}


def reset_episode_memory(player_id: int) -> EpisodeMemory:
    """Discard any memory for ``player_id`` and start a fresh one."""
    memory = EpisodeMemory(player_id=player_id)
    _MEMORIES[player_id] = memory
    return memory


def get_episode_memory(player_id: int, step: int) -> EpisodeMemory:
    """Return this player's memory for the current episode.

    Step 0 always starts a new episode. A player with no memory yet (e.g. the
    process was started mid-episode) also gets a fresh one.
    """
    if step == 0 or player_id not in _MEMORIES:
        return reset_episode_memory(player_id)
    return _MEMORIES[player_id]


def remember_turn(memory: EpisodeMemory, state: GameState) -> None:
    """Record the bounded per-turn summary needed by later turns.

    Copies the market inventory so the memory never aliases ``GameState``.
    """
    memory.last_step = state.step
    memory.previous_market_inventory = dict(state.market.inventory)


def clear_all_memory() -> None:
    """Forget every player's memory (offline testing/harness use)."""
    _MEMORIES.clear()
