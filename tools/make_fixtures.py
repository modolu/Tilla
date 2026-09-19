"""Regenerate the real-observation fixtures under tests/fixtures (offline only).

Every fixture is an observation captured from the installed official
environment (kaggle-environments 1.30.2) with a fixed seed, so the files are
deterministic and reviewable. Only the transport-only ``remainingOverageTime``
key is removed. No hidden state is captured: each file is exactly what the
named player's ``agent(obs)`` would receive.

The scripted driver below is fixture tooling, not strategy: it exists purely
to reach observations containing every tile variant, unlocked land, hired
hands, carried inventory, shed/seed stock, unlocked shops and moved prices.
"""

from __future__ import annotations

import json
from pathlib import Path

from kaggle_environments import make

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
PASS = {"farmer": ["PASS"], "hands": [], "market": []}

# Player 1's scripted day-0 unit actions, one per turn from step 1 (farmer starts on (4,4)).
DAY0_UNIT_SCRIPT = [
    ["PICKUP", "GOOSE", 1],
    ["PICKUP", "WHEAT", 2],
    ["PICKUP", "FERTILIZER", 1],
    ["PLANT", "WHEAT"],
    ["WATER"],
    ["FERTILIZE"],
    ["WEST"],  # -> (3,4)
    ["PLANT", "CARROT"],
    ["WATER"],
    ["WEST"],  # -> (2,4)
    ["PLANT", "TOMATO"],
    ["WATER"],
    ["WEST"],  # -> (1,4)
    ["BUILD_COOP"],
    ["PLACE", "GOOSE"],
    ["FEED"],
    ["CARE"],
    ["WEST"],  # -> (0,4)
    ["BUILD_PASTURE"],
]
# Daily care loop from day 1 on: farmer respawns on (4,4) each day.
DAILY_UNIT_SCRIPT = [
    ["PICKUP", "WHEAT", 1],
    ["WATER"],
    ["WEST"],
    ["WATER"],
    ["WEST"],
    ["WATER"],
    ["WEST"],
    ["FEED"],
    ["CARE"],
    ["COLLECT_FERTILIZER"],
]
STEP0_MARKET = [
    ["BUY_LAND"],
    ["BUY_SEED", "WHEAT", 3],
    ["BUY_SEED", "CARROT", 2],
    ["BUY_SEED", "TOMATO", 1],
    ["BUY_ANIMAL", "GOOSE", 1],
    ["BUY_PRODUCT", "FERTILIZER", 1],
    ["BUY_PRODUCT", "WHEAT", 8],
]


def scripted_player_one(obs):
    step = obs["step"]
    hour = obs["hour"]
    day = obs["day"]
    farmer = ["PASS"]
    market = []
    if step == 0:
        market = STEP0_MARKET
    elif day == 0 and 1 <= hour <= len(DAY0_UNIT_SCRIPT):
        farmer = DAY0_UNIT_SCRIPT[hour - 1]
    elif day >= 1 and hour < len(DAILY_UNIT_SCRIPT):
        farmer = DAILY_UNIT_SCRIPT[hour]
        if day == 5 and hour == 0:
            farmer = ["HARVEST"]  # carry wheat for the capture
            market = [["HIRE"], ["HIRE"]]
    if day == 5 and hour == 2:
        market = [["SELL", "WHEAT", 2]]
    hands = [["PASS"] for _ in obs["farms"][obs["player"]]["hands"]]
    return {"farmer": farmer, "hands": hands, "market": market}


def _clean(observation) -> dict:
    obs = json.loads(json.dumps(observation))
    obs.pop("remainingOverageTime", None)
    return obs


def _dump(name: str, obs: dict) -> None:
    path = FIXTURES_DIR / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obs, f, indent=1, sort_keys=True)
        f.write("\n")
    print(path)


def _capturing(agent, wanted_steps: set[int], captured: dict):
    """Wrap ``agent`` so the exact observation it receives is captured by step."""

    def wrapped(obs):
        if obs["step"] in wanted_steps:
            captured[obs["step"]] = _clean(obs)
        return agent(obs)

    return wrapped


def _hiring_player_zero(obs):
    if obs["step"] == 0:
        return {"farmer": ["PASS"], "hands": [], "market": [["HIRE"], ["HIRE"]]}
    hands = [["PASS"] for _ in obs["farms"][0]["hands"]]
    return {"farmer": ["PASS"], "hands": hands, "market": []}


def _pass(obs):
    return dict(PASS)


def main() -> int:
    from kaggle_environments.envs.kaggriculture.kaggriculture import starter_agent

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    # 1-3. Player 0 hires two hands at step 0. Capture player 0 at step 0 (no
    # hands) and both seats at step 1 (player 0 has two hands).
    p0, p1 = {}, {}
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": 7}, debug=True)
    env.run([_capturing(_hiring_player_zero, {0, 1}, p0), _capturing(_pass, {1}, p1)])
    _dump("obs_step0_no_hands.json", p0[0])
    _dump("obs_step1_p0_two_hands.json", p0[1])
    _dump("obs_step1_p1_opponent_has_hands.json", p1[1])

    # 4-5. Mid-game: starter (player 0) vs scripted driver (player 1), both
    # seats captured on day 5 hour 1 (step 121).
    p0, p1 = {}, {}
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": 7}, debug=True)
    env.run([_capturing(starter_agent, {121}, p0), _capturing(scripted_player_one, {121}, p1)])
    _dump("obs_midgame_p0_starter.json", p0[121])
    _dump("obs_midgame_p1_populated.json", p1[121])

    # 6. Last observation agents receive in a PASS-vs-PASS game (step 718) and
    # the final recorded state (step 719, day 29 hour 23) for player 0.
    p0 = {}
    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": 7}, debug=True)
    env.run([_capturing(_pass, {718}, p0), _pass])
    _dump("obs_final_step_p0.json", _clean(env.steps[719][0]["observation"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
