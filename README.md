# Tilla

Deterministic, stateful, market-aware economic planner for the advanced
Kaggriculture Kaggle environment. Read `TILLA_ARCHITECTURE.md`,
`TILLA_RULES.md`, and `TILLA_STRATEGY.md` before changing anything.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The submitted runtime (`main.py` + `kaggriculture_bot/`) uses the Python
standard library only. The `dev` extra installs local tooling:
`kaggle-environments==1.30.2`, `pytest==9.*`, `ruff==0.16.*`.

## Verify

```bash
pytest
ruff check .
ruff format --check .
git diff --check
```

## Run a local episode

```python
from kaggle_environments import make
import main

env = make("kaggriculture", configuration={"episodeSteps": 720}, debug=True)
env.run([main.agent, "pass"])
```

## Baseline comparison agent

`agents/baseline.py` is a frozen snapshot of the accepted Milestone 2
farm-care baseline (offline comparison only; never packaged):

```python
from agents.baseline import agent as baseline_agent
env.run([main.agent, baseline_agent])
```

## Explain decisions

```bash
python -m tools.replay_analysis --opponent pass --seed 2026 --steps 0 264 480
```

Prints, for the requested turns of one official episode, the ranked
opportunities (revenue, costs, labor, land, net value, score), the cash
reserve and shed pressure, every objective the strategy listed, the hiring
decision with its reasoning, the generated jobs and the unit assignments
(including which were carried over from the previous turn).

## Instrumented matches and the hiring ablation

```bash
python -m tools.harness --candidate main --opponent control --seeds 4000 4039
python -m tools.harness --candidate main --opponent baseline --seeds 4000 4039
```

Plays seat-swapped paired official episodes and reports wins, terminal-cash
margins, hires per day, hand-action utilization, care losses (crops lost to
missed watering, fresh plantings left unwatered, animals escaped), malformed
outputs and per-turn timing. `control` is the same `main.agent` with only its
`HIRE` market orders removed (`tools.harness.hiring_disabled`), the Milestone 4
ablation control.

## Regenerate observation fixtures

```bash
python -m tools.make_fixtures
```

Rewrites `tests/fixtures/obs_*.json` from seeded official episodes.

## Package for submission

```bash
python -m tools.package_submission submission.tar.gz
```

The archive root contains `main.py` and `kaggriculture_bot/` only.
