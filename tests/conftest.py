"""Shared test helpers. Offline only; never imported by the submitted runtime."""

import copy
import json
from pathlib import Path

import pytest

from kaggriculture_bot.runtime import clear_all_memory

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Every real-observation fixture (captured from kaggle-environments 1.30.2 by
# tools/make_fixtures.py), used wherever a test should hold for all of them.
OFFICIAL_FIXTURES = sorted(p.name for p in FIXTURES_DIR.glob("obs_*.json"))


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _isolated_episode_memory():
    """Each test starts and ends with no episode memory in the process."""
    clear_all_memory()
    yield
    clear_all_memory()


@pytest.fixture
def obs_no_hands() -> dict:
    """Real step-0 observation (player 0, no hands)."""
    return load_fixture("obs_step0_no_hands.json")


@pytest.fixture
def obs_two_hands() -> dict:
    """Real step-1 observation: player 0 after hiring two hands at step 0."""
    return load_fixture("obs_step1_p0_two_hands.json")


@pytest.fixture
def obs_seat1_step1() -> dict:
    """Real step-1 observation from seat 1 while the opponent (seat 0) has two hands."""
    return load_fixture("obs_step1_p1_opponent_has_hands.json")


@pytest.fixture
def obs_midgame_p1() -> dict:
    """Real day-5 observation from seat 1 with every tile variant, land, hands, stock."""
    return load_fixture("obs_midgame_p1_populated.json")


@pytest.fixture
def obs_midgame_p0() -> dict:
    """Real day-5 observation from seat 0 (built-in starter agent's farm)."""
    return load_fixture("obs_midgame_p0_starter.json")


@pytest.fixture
def obs_final() -> dict:
    """Real final recorded observation (step 719, day 29, hour 23, all shops unlocked)."""
    return load_fixture("obs_final_step_p0.json")


@pytest.fixture
def deep(obs_no_hands):
    return copy.deepcopy(obs_no_hands)
