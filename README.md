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
