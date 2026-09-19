"""Shared test helpers. Offline only; never imported by the submitted runtime."""

import copy
import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def obs_no_hands() -> dict:
    """Real step-0 observation captured from kaggle-environments 1.30.2 (player 0, no hands)."""
    return load_fixture("obs_step0_no_hands.json")


@pytest.fixture
def obs_two_hands(obs_no_hands) -> dict:
    """The step-0 observation with two hired hands standing next to the farmer."""
    obs = copy.deepcopy(obs_no_hands)
    obs["farms"][0]["hands"] = [[4, 5], [5, 4]]
    obs["farms"][0]["hires_today"] = 2
    obs["private"]["inventories"] = [{}, {}, {}]
    return obs
