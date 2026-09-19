"""Tests for kaggriculture_bot.runtime (Milestone 1: episode memory reset and bounds)."""

import copy

from kaggriculture_bot.constants import OPPONENT_HISTORY_LIMIT
from kaggriculture_bot.models import EpisodeMemory
from kaggriculture_bot.parser import parse_observation
from kaggriculture_bot.runtime import (
    clear_all_memory,
    get_episode_memory,
    remember_turn,
    reset_episode_memory,
)


def test_step0_creates_fresh_memory():
    memory = get_episode_memory(0, step=0)
    assert isinstance(memory, EpisodeMemory)
    assert memory.player_id == 0
    assert memory.last_step == -1
    assert memory.previous_market_inventory == {}
    assert len(memory.opponent_history) == 0
    assert memory.inferred_opponent_pipeline == {}
    assert memory.current_plan is None and memory.plan_created_step is None


def test_same_player_reuses_memory_within_episode(obs_no_hands, obs_two_hands):
    memory = get_episode_memory(0, step=0)
    remember_turn(memory, parse_observation(obs_no_hands))
    again = get_episode_memory(0, step=1)
    assert again is memory
    assert again.last_step == 0
    remember_turn(again, parse_observation(obs_two_hands))
    assert get_episode_memory(0, step=2).last_step == 1


def test_players_have_isolated_memory(obs_two_hands, obs_seat1_step1):
    m0 = get_episode_memory(0, step=0)
    m1 = get_episode_memory(1, step=0)
    assert m0 is not m1
    remember_turn(m0, parse_observation(obs_two_hands))
    assert m1.last_step == -1 and m1.previous_market_inventory == {}
    seat1 = parse_observation(obs_seat1_step1)
    remember_turn(m1, seat1)
    m0.previous_market_inventory["WHEAT"] = -1
    assert m1.previous_market_inventory["WHEAT"] == seat1.market.inventory["WHEAT"] == 9999
    assert get_episode_memory(0, step=5) is m0
    assert get_episode_memory(1, step=5) is m1


def test_new_step0_resets_previous_episode_state(obs_two_hands):
    memory = get_episode_memory(0, step=0)
    remember_turn(memory, parse_observation(obs_two_hands))
    memory.inferred_opponent_pipeline["WHEAT"] = 0.5
    memory.opponent_history.append("summary")
    fresh = get_episode_memory(0, step=0)
    assert fresh is not memory
    assert fresh.last_step == -1
    assert fresh.previous_market_inventory == {}
    assert fresh.inferred_opponent_pipeline == {}
    assert len(fresh.opponent_history) == 0


def test_missing_memory_mid_episode_starts_fresh():
    """Process started mid-episode (no step 0 seen): memory is created, not an error."""
    memory = get_episode_memory(1, step=300)
    assert memory.player_id == 1 and memory.last_step == -1


def test_previous_market_inventory_is_a_copy_not_an_alias(obs_midgame_p1):
    state = parse_observation(obs_midgame_p1)
    memory = get_episode_memory(1, step=state.step)
    remember_turn(memory, state)
    assert memory.previous_market_inventory == state.market.inventory
    assert memory.previous_market_inventory is not state.market.inventory
    memory.previous_market_inventory["WOOL"] = 0
    assert state.market.inventory["WOOL"] == 9963
    assert memory.last_step == 121


def test_opponent_history_is_bounded():
    memory = reset_episode_memory(0)
    for i in range(OPPONENT_HISTORY_LIMIT + 25):
        memory.opponent_history.append(i)
    assert len(memory.opponent_history) == OPPONENT_HISTORY_LIMIT
    assert memory.opponent_history[0] == 25  # oldest entries are dropped first


def test_no_persistence_across_games(obs_two_hands):
    memory = get_episode_memory(0, step=0)
    remember_turn(memory, parse_observation(obs_two_hands))
    clear_all_memory()
    fresh = get_episode_memory(0, step=7)
    assert fresh is not memory and fresh.last_step == -1


def test_memory_does_not_touch_game_state(obs_midgame_p1):
    raw = copy.deepcopy(obs_midgame_p1)
    state = parse_observation(raw)
    memory = get_episode_memory(1, step=0)
    remember_turn(memory, state)
    assert state == parse_observation(obs_midgame_p1)
