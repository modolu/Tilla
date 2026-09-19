# Tilla — Claude Code Project Instructions

Tilla is a competition agent for the advanced Kaggriculture Kaggle environment.

The primary reader of this repository is an AI coding agent with no reliable memory between sessions. Do not rely on previous conversation context. Reconstruct project intent from the repository before making changes.

## Source of truth

Before implementing or modifying Tilla, read these files in full:

1. `TILLA_ARCHITECTURE.md`
2. `TILLA_RULES.md`
3. `TILLA_STRATEGY.md`

Their responsibilities are different:

* `TILLA_ARCHITECTURE.md` defines repository structure, module boundaries, dependency direction, implementation sequence, testing policy, benchmark process, and milestone Definitions of Done.
* `TILLA_RULES.md` defines verified Kaggriculture mechanics. It must remain strategy-free.
* `TILLA_STRATEGY.md` defines Tilla's current decision policy, strategic assumptions, parameters, opponent modelling policy, and promotion requirements.

Do not treat these documents as interchangeable.

If implementation convenience conflicts with `TILLA_ARCHITECTURE.md`, the architecture wins.

If `TILLA_RULES.md` conflicts with the official Kaggriculture environment, the official environment wins. Verify the discrepancy and update the rules file and relevant conformance tests in the same change before adapting strategy.

A material strategy change requires benchmark evidence. Do not modify strategy because an alternative merely appears smarter.

## Project identity

Tilla v1 is a:

> deterministic, stateful, market-aware economic planner with lightweight opponent forecasting.

The competition objective is:

> Finish the 720-turn season with more banked money than the opponent.

Do not optimize farm size, nominal asset value, yield, production, or code sophistication unless doing so improves head-to-head terminal cash.

The submitted runtime remains Python-standard-library only unless an explicit architecture revision supported by benchmark evidence changes that decision.

Do not introduce:

* reinforcement learning;
* neural networks or model weights;
* LLM calls;
* remote APIs;
* databases;
* third-party runtime dependencies;
* full Monte Carlo tree search;
* unseeded runtime randomness;
* hidden-environment-state dependencies.

## Required runtime flow

Preserve this conceptual flow:

```text
Kaggle observation
→ parser
→ typed GameState
→ feature derivation
→ opponent forecast
→ economic scoring
→ strategic plan
→ task allocation
→ pathing
→ action construction
→ legality validation
→ Kaggle action
```

Do not collapse layers for convenience.

## Module ownership

Respect these boundaries.

`main.py`

* Kaggle adapter only.
* Exposes `agent(obs)`.
* Contains no strategy, ROI calculations, pathfinding, or duplicated game constants.
* Owns the outer exception boundary and structurally valid PASS fallback.

`kaggriculture_bot/constants.py`

* Stable copied/derived game constants and policy parameters.
* Do not duplicate the same official constant across modules.

`kaggriculture_bot/models.py`

* Typed internal state, action, and value objects.

`kaggriculture_bot/parser.py`

* Raw Kaggle observation → typed `GameState`.
* Parsing only.
* No decisions, economics, or strategy.

`kaggriculture_bot/runtime.py`

* Bounded per-episode state and reset behavior.
* No persistence across games.

`kaggriculture_bot/features.py`

* Derived farm, market, season, and state features.

`kaggriculture_bot/economy.py`

* ROI, opportunity scores, cash runway, production estimates, market-value calculations.
* No movement or action formatting.

`kaggriculture_bot/opponent.py`

* Public-state opponent inference only.
* Estimates remain estimates and carry appropriate confidence.
* Never reconstruct opponent private inventory as fact.

`kaggriculture_bot/strategy.py`

* Chooses strategic objectives and plans.
* Never receives raw Kaggle observation dictionaries.
* No BFS/pathfinding.

`kaggriculture_bot/tasks.py`

* Converts strategic objectives into concrete unit jobs.
* May use pathing.
* Must not contain market-price formulas or strategic economics.

`kaggriculture_bot/pathing.py`

* Deterministic movement and shortest-path primitives.

`kaggriculture_bot/actions.py`

* Converts internal actions into Kaggle action shape.
* No decision policy.

`kaggriculture_bot/validator.py`

* Final legality and sanity layer.
* May downgrade an invalid plan to PASS.
* Must not invent strategy.

`tools/`, `tests/`, and `benchmarks/`

* Offline only.
* Submitted runtime must never import from them.

## Dependency discipline

Follow the dependency direction defined in `TILLA_ARCHITECTURE.md`.

Lower layers must not import higher strategic layers.

In particular:

* parser must not import economy;
* economy must not import tasks;
* lower layers must not import strategy;
* runtime code must not import `tools`, `tests`, or `benchmarks`;
* do not create convenience abstractions that bypass these boundaries.

Prefer modifying an existing prescribed module over creating a new abstraction.

Do not create a new top-level directory without an explicit architecture change.

## State and information rules

`GameState` is treated as immutable after parsing.

Real bank balances, quantities, inventory counts, and affordability checks use integers.

Floating point is allowed only for estimated values, probabilities, scores, or expected economics.

Episode state is in memory only.

Reset episode memory when the environment begins a new game as defined by the architecture.

Keep opponent history bounded.

Only use information legitimately present in Kaggle observations.

Opponent shed contents, seeds, and carried inventories are private and must never be treated as known.

Local testing infrastructure must not expose hidden environment state to runtime strategy.

## Determinism

Given the same observation and equivalent `EpisodeMemory`, Tilla must return the same action.

Do not use wall-clock time as a strategic input.

Do not introduce unseeded randomness into runtime policy.

## Strategy changes

Do not silently change:

* phase boundaries;
* reserve policy;
* shed thresholds;
* opportunity-scoring assumptions;
* crop or animal preferences;
* market policy;
* opponent-response policy;
* hiring policy;
* land policy;
* endgame behavior.

If a requested implementation appears to require a policy change, stop treating it as an implementation detail.

Identify the proposed change explicitly.

Update `TILLA_STRATEGY.md` only when the user has approved the policy change and the required benchmark evidence exists.

Material candidate strategy changes are promoted only through the benchmark process defined in `TILLA_STRATEGY.md`.

The incumbent is frozen comparison code. Never modify the incumbent while evaluating a candidate.

## Mechanics changes

Do not guess about Kaggriculture mechanics.

When behavior is uncertain or appears inconsistent with `TILLA_RULES.md`:

1. inspect the relevant official Kaggriculture source;
2. reproduce the behavior with the smallest useful environment test where necessary;
3. determine the actual mechanic;
4. update `TILLA_RULES.md` if the verified rule changed or was incorrect;
5. add or update a conformance test;
6. only then adapt implementation.

Never change strategy to compensate for an unverified understanding of a game rule.

## Milestone discipline

Implementation follows the milestone order in `TILLA_ARCHITECTURE.md`.

Work on the next incomplete milestone unless the user explicitly requests otherwise.

Before beginning a milestone:

1. read all three source-of-truth files;
2. inspect the current repository and git status;
3. identify the milestone's exact scope;
4. identify its exact Definition of Done;
5. confirm earlier milestone contracts remain intact.

Implement only what is needed for the active milestone.

Do not implement future milestones early merely because a convenient abstraction suggests doing so.

Avoid unrelated refactors, renames, formatting churn, speculative abstractions, or cleanup outside the active scope.

Every milestone must leave `main.agent` runnable.

## Testing requirements

Every behavior change needs a focused test, conformance test, scenario, or benchmark appropriate to the change.

Before declaring a milestone complete, run:

```bash
pytest
ruff check .
ruff format --check .
git diff --check
```

Also run at least one complete Kaggriculture episode appropriate to the active milestone.

Run the milestone-specific tests and Definition-of-Done verification required by `TILLA_ARCHITECTURE.md`.

A unit-test pass is not sufficient if the agent crashes during a complete episode.

Do not claim a benchmark result that was not actually run.

Do not summarize partial or smoke-test results as promotion evidence.

## Benchmark integrity

Primary promotion metric:

> paired win rate against the frozen incumbent.

Secondary metric:

> median terminal cash margin.

Current default promotion gate is defined in `TILLA_STRATEGY.md`.

Do not:

* promote based on appearance;
* promote based only on average bank balance;
* tune repeatedly on a holdout set and continue calling it holdout;
* modify the incumbent during candidate evaluation;
* omit seat swaps from paired evaluation;
* hide crashes, timeouts, or mandatory-scenario regressions.

Benchmark tooling must remain offline and must never be imported by submitted runtime code.

## Runtime safety

The runtime must make no network calls.

`agent` must require no filesystem writes.

No subprocesses, dynamic code execution, API credentials, `.env` configuration, or external services are required.

Submission runtime should not spam stdout.

Maintain a structurally valid PASS fallback at the outer boundary, but do not use that fallback as normal control flow.

## Git and change hygiene

Before editing, inspect existing changes and do not overwrite unrelated user work.

Keep milestone commits focused.

Do not modify source-of-truth documents merely to make an implementation fit.

If an architectural decision genuinely changes, update `TILLA_ARCHITECTURE.md` in the same commit.

If a verified mechanic changes, update `TILLA_RULES.md` and its conformance test together.

If a policy is promoted, update `TILLA_STRATEGY.md` and its strategy-change record appropriately.

At milestone completion:

1. verify the diff contains only intended changes;
2. run all required checks;
3. run required episode/integration verification;
4. create one clean milestone commit;
5. ensure the working tree is clean unless explicitly documented otherwise.

## Completion report

When finishing implementation work, report concisely:

* active milestone;
* milestone Definition of Done;
* files created;
* files modified;
* behavior implemented;
* focused tests added;
* exact verification commands run;
* full-episode result;
* benchmark evidence, if applicable;
* deviations from source-of-truth documents;
* unresolved issues;
* commit hash;
* final `git status`.

Never claim completion when the milestone Definition of Done has not been demonstrated.

