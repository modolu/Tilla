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


# --- Scenario builders (official schema, run through the strict parser) --------------------


def raw_plant(
    crop="WHEAT", planted_day=0, watered_today=False, consecutive_unwatered=1, yield_units=1
):
    """A plant dict exactly as kaggriculture.py emits it (max_lifespan_step for one-time crops)."""
    from kaggriculture_bot.constants import CROPS, TURNS_PER_DAY

    spec = CROPS[crop]
    lifespan = -1 if spec.ongoing else (planted_day + spec.max_yield_day + 1) * TURNS_PER_DAY
    return {
        "kind": "PLANT",
        "crop": crop,
        "planted_day": planted_day,
        "watered_today": watered_today,
        "consecutive_unwatered": consecutive_unwatered,
        "yield_units": yield_units,
        "max_lifespan_step": lifespan,
        "fertilized_until_day": -1,
    }


def raw_animal(animal="GOOSE", placed_day=0, fed_today=False, consecutive_unfed=0, yield_units=0):
    kind = "COOP" if animal == "GOOSE" else "PASTURE"
    return {
        "kind": kind,
        "animal": animal,
        "placed_day": placed_day,
        "yield_units": yield_units,
        "consecutive_unfed": consecutive_unfed,
        "fed_today": fed_today,
        "cared_today": False,
        "fertilizer_available": False,
        "pending_care_bonus": 0,
    }


def make_state(
    base: dict,
    *,
    day=None,
    hour=None,
    farmer=None,
    tiles=None,
    seeds=None,
    shed=None,
    inventory=None,
    money=None,
    hands=None,
    prices=None,
):
    """Copy a real observation, apply overrides to our own farm, and parse it.

    ``tiles`` maps ``(x, y)`` to a raw tile value (``None``, ``"LOCKED"`` or a dict).
    """
    from kaggriculture_bot.parser import parse_observation

    obs = copy.deepcopy(base)
    me = obs["farms"][obs["player"]]
    if day is not None:
        obs["day"] = day
        obs["step"] = day * 24 + (hour or 0)
    if hour is not None:
        obs["hour"] = hour
        obs["step"] = obs["day"] * 24 + hour
    if farmer is not None:
        me["farmer"] = list(farmer)
    if hands is not None:
        me["hands"] = [list(h) for h in hands]
        obs["private"]["inventories"] = [obs["private"]["inventories"][0]] + [{} for _ in hands]
    for (x, y), tile in (tiles or {}).items():
        me["tiles"][y][x] = tile
    if seeds is not None:
        obs["private"]["seeds"].update(seeds)
    if shed is not None:
        obs["private"]["shed"].update(shed)
    if inventory is not None:
        obs["private"]["inventories"][0] = dict(inventory)
    if money is not None:
        me["money"] = money
    if prices is not None:
        obs["market"]["prices"].update(prices)
    return parse_observation(obs)
