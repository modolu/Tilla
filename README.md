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
ablation control. `--opponent incumbent` plays the frozen champion
(`agents.incumbent.agent`, the accepted Milestone 7 snapshot in
`agents/incumbent_m7/`; the Milestone 4, 5 and 6 champions stay frozen in
`agents/incumbent_m4/`, `agents/incumbent_m5/` and `agents/incumbent_m6/`). The report also carries market diagnostics: realized
sale prices per premium product (lockstep replay of both players' orders),
premium purchases into glutted markets, glut-protection rejections and how
often the town model changed the sell orders.

## Tournament and promotion gate

`tools/tournament.py` plays paired, seat-swapped, seeded candidate-vs-incumbent
episodes and evaluates them against the promotion gate of `TILLA_STRATEGY.md`
§19 (explicit PASS/FAIL; a PASS never promotes or freezes anything by itself).

Terms, used the same way in code, records and reports:

- **episode**: one official 720-step game with the candidate in one seat;
- **paired seed**: a seed played twice with the seats swapped
  (`--stable 100000:1500` = 1,500 paired seeds = 3,000 episodes);
- **completed pair**: a paired seed with exactly one valid episode for
  candidate seat 0 and one for seat 1. Only completed pairs count.

"At least 2,000 paired games" means **2,000 completed pairs = 4,000
episodes** (formal plan: 1,500 stable + 500 holdout paired seeds). 1,000 seeds
/ 2,000 episodes never satisfies the gate.

```bash
# One command: play (or resume) stable, gate it, play the holdout only if the
# stable partition is eligible, then write the full report.
caffeinate -i python -m tools.tournament gate --run-dir benchmarks/results/<run> \
    --candidate main:agent --incumbent agents.incumbent:agent \
    --stable 100000:1500 --holdout-epoch 0 --holdout-count 500 \
    --workers 4 --scenario-results <scenario-results.json>

python -m tools.tournament status --run-dir benchmarks/results/<run>   # progress, no outcomes
python -m tools.tournament report --run-dir benchmarks/results/<run>   # stable only
python -m tools.tournament report --run-dir benchmarks/results/<run> --reveal-holdout
```

Formal flow: **stable → stable eligibility → holdout → combined verdict.**
All stable episodes run first. The complete stable partition must pass its own
eligibility gate (at least 1,500 completed pairs, clean integrity, and the
statistical thresholds on its own); the decision is written to
`stable_gate.json`. Only then is the holdout played or revealed. The final
verdict uses the combined evidence, and stable, holdout and combined results
are reported separately. A strong holdout never rescues an ineligible stable
partition.

The gate (policy recorded in the run manifest; a policy weaker than the Tilla
default always fails) requires, with strict exact comparisons:

- ≥ 2,000 completed pairs and ≥ 4,000 episodes in them (≥ 1,500 stable,
  ≥ 500 holdout), every planned paired seed completed;
- win rate `> 53%` (ties are not wins), Wilson 95% lower bound `> 50%`,
  median terminal cash margin `> 0`;
- zero candidate crashes, environment-enforced timeouts and invalid actions;
- zero unresolved incumbent failures and zero harness errors, incomplete
  episodes or duplicate results;
- mandatory-scenario evidence with status `PASS`.

Opponent and harness failures never count for the candidate: such an episode
has no margin or outcome. It is replayed up to `--max-attempts` times; if it
still fails it is recorded and blocks promotion. Candidate failures are never
replayed.

Scenario evidence is `PASS`, `FAIL` (a material regression) or
`MISSING_EVIDENCE` (no results file, results for another candidate/incumbent,
a scenario without a verdict, or an empty suite). Only `PASS` satisfies the
gate. An empty `benchmarks/scenarios.json` is missing evidence, not a
declaration that no mandatory scenarios exist; only an explicit policy flag
(`mandatory_scenarios_declared_empty`) can declare that.

Operational guarantees:

- Entrypoints are `module:attr` importable from the repo root, or a built-in
  (`pass`, `starter`, `random`; `random` is unseeded, so not reproducible).
- The run directory must be persistent (`/tmp` and other temp roots are
  refused). It holds `manifest.json` (config, policy and content hashes of the
  candidate, incumbent, environment and runner), append-only
  `results-stable.jsonl` / `results-holdout.jsonl`, `stable_gate.json`,
  `events.jsonl`, `heartbeat.json`, `replays/` and `reports/`.
- Interrupt with Ctrl-C (or SIGTERM) at any time and rerun the same command to
  resume: finished episodes are never replayed or written twice, unfinished
  ones are replayed. A second runner on the same directory is refused
  (`flock`); changed code, config or policy is refused.
- `report` without `--reveal-holdout` never opens the holdout results, and
  the heartbeat/status never show outcomes. Revealing is logged in
  `benchmarks/results/holdout_ledger.jsonl`; a holdout already revealed for
  another candidate is flagged as reused.
- Timing separates the agent call (as charged against `actTimeout` by
  kaggle-environments), the whole turn, the whole episode, post-episode work,
  worker startup and scheduling overhead, with p50/p95/p99/max. Timeouts mean
  environment-enforced `TIMEOUT` statuses only; calls over `actTimeout`
  absorbed by the overage bank are an advisory, not a gate failure. Episodes
  that ran across a host sleep are flagged.
- `--save-replays problems` (default) keeps replays of failed episodes only;
  `losses` also keeps every loss (about 14 MB each).

The Milestone 5-era helpers `tools.tournament.run`, `gate_report`,
`gate_passes` and `wilson_lower_bound` remain importable for the recorded
Milestone 5-7 gate tooling; new promotion runs use the `gate` command above.

## Regenerate observation fixtures

```bash
python -m tools.make_fixtures
```

Rewrites `tests/fixtures/obs_*.json` from seeded official episodes.

## Package for submission

```bash
python -m tools.package_submission submission.tar.gz          # build + validate + smoke
python -m tools.package_submission --validate-only submission.tar.gz
```

The archive root contains `main.py` and `kaggriculture_bot/*.py` only. The
build is deterministic (sorted entries, normalized tar metadata, no gzip name
or timestamp), so identical sources give a byte-identical archive. Validation
rejects anything else (tests, benchmarks, tools, agents, docs, caches, VCS or
virtualenv files, symlinks, path traversal, duplicate or case-colliding paths,
nested or missing `main.py`, missing package), statically audits the packaged
runtime (Python 3.11 syntax; standard-library imports only; no network,
subprocess, dynamic execution, file access or local absolute paths), then
extracts the archive into a fresh temporary directory and imports it from a new
`python -I -S` process (no site-packages or editable installs), reporting any
module that resolved from outside the archive. A smoke `agent()` call runs on
`tests/fixtures/obs_midgame_p1_populated.json` unless `--no-smoke` is given.
The JSON report carries the archive SHA-256, compressed/uncompressed sizes and
a per-file manifest (path, bytes, SHA-256); the exit status is 0 only if every
check passes.
