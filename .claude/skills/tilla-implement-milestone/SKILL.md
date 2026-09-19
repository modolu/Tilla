---

name: tilla-implement-milestone
description: Implement exactly one Tilla milestone from TILLA_ARCHITECTURE.md while preserving architecture, verified Kaggriculture rules, current strategy, focused testing, integration safety, and clean git history. Use whenever implementing or completing a Tilla milestone.
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# Tilla Milestone Implementation

Implement one Tilla milestone at a time.

Your goal is not to write the most code or create the most sophisticated architecture.

Your goal is:

> Satisfy the active milestone's exact Definition of Done with the smallest correct implementation that preserves all existing Tilla contracts.

## 1. Mandatory preflight

Before modifying any file, read in full:

* `TILLA_ARCHITECTURE.md`
* `TILLA_RULES.md`
* `TILLA_STRATEGY.md`
* root `CLAUDE.md`

Do not rely on remembered versions of these documents.

Then inspect:

```bash
git status --short
git log --oneline -10
```

Inspect the existing repository tree and relevant implementation/tests.

Do not overwrite unrelated working-tree changes.

## 2. Identify the active milestone

Locate the Build Order section in `TILLA_ARCHITECTURE.md`.

Determine:

* requested milestone number and title;
* required deliverables;
* exact Definition of Done;
* prior milestone contracts that must remain valid;
* which prescribed modules own the required behavior.

If the user did not specify a milestone, identify the next incomplete milestone from repository evidence.

Do not skip ahead.

Do not implement later-milestone behavior merely because it may be useful eventually.

If the requested milestone is already complete, verify its Definition of Done rather than rewriting it unnecessarily.

## 3. Establish scope before editing

Create an internal scope consisting of:

### Required

Everything necessary to satisfy the active milestone.

### Allowed

Small supporting changes required to keep tests, imports, packaging, or existing contracts correct.

### Out of scope

Everything belonging to later milestones, unrelated refactors, speculative abstractions, stylistic rewrites, new product features, or strategy changes not required by the milestone.

Stay inside this boundary.

## 4. Preserve source-of-truth separation

Use:

* `TILLA_RULES.md` for environment truth;
* `TILLA_STRATEGY.md` for policy;
* `TILLA_ARCHITECTURE.md` for implementation location and dependency direction.

Do not encode a strategic preference as a game mechanic.

Do not rewrite a game rule to accommodate the implementation.

Do not treat implementation behavior as evidence that the rule document is wrong.

If a mechanic is uncertain, verify it before relying on it.

## 5. Architectural enforcement

Keep the runtime flow consistent with:

```text
observation
→ parser
→ typed state
→ derived features
→ opponent forecast
→ economic scoring
→ strategic plan
→ task allocation
→ pathing
→ action construction
→ validation
→ Kaggle action
```

Respect prescribed module ownership.

Hard prohibitions include:

* strategy logic in `main.py`;
* decision logic in `parser.py`;
* decision logic in `actions.py`;
* raw Kaggle observation dictionaries inside strategy;
* pathfinding inside strategy;
* market-economics formulas inside tasks;
* runtime imports from `tools/`, `tests/`, or `benchmarks/`;
* hidden environment state as a strategic input;
* third-party submitted-runtime dependencies;
* unapproved top-level directories;
* modification of the frozen incumbent during candidate evaluation.

Do not create a new abstraction simply because it makes the active edit more elegant.

Prefer the existing architecture.

## 6. Dependency policy

Do not add a runtime dependency.

Do not add a development dependency unless the milestone explicitly requires it and the user has approved it.

If you believe a new dependency is necessary:

1. do not install it immediately;
2. explain why stdlib/current tooling is insufficient;
3. identify the architectural decision it would change;
4. wait for explicit approval.

For normal milestone implementation, work within the existing dependency decisions.

## 7. Implementation style

Prefer:

* small deterministic functions;
* explicit typed data;
* pure calculations where practical;
* integer arithmetic for real money/counts;
* floats only for estimates/scores;
* bounded episode memory;
* deterministic tie-breaking;
* clear module ownership;
* direct implementations over unnecessary frameworks.

Do not optimize prematurely.

Do not add search/look-ahead unless the active milestone explicitly authorizes it.

Do not redesign Tilla.

## 8. Tests must accompany behavior

For every meaningful behavior added or changed, add the narrowest useful regression test.

Depending on the milestone, use:

* unit tests;
* rule-conformance tests;
* deterministic scenario tests;
* integration episodes;
* benchmark evaluation.

Tests should protect source-of-truth behavior, not merely mirror the implementation.

Where a rule is duplicated as a constant or calculation, compare against the official installed Kaggriculture environment when practical.

Do not use hidden evaluator state to make runtime tests easier.

## 9. Full-episode verification

Before completion, run at least one complete Kaggriculture episode.

Use the episode required by the active milestone's Definition of Done whenever one is specified.

Examples:

* Milestone 0 requires the official environment to complete a full 720-turn PASS-vs-PASS episode using Tilla's `main.py`.
* Later milestones must use the integration opponents or candidate/incumbent evaluation required by their milestone and testing policy.

A shortened episode is not evidence for a full-episode Definition of Done.

Record:

* opponent(s);
* episode length;
* whether completion succeeded;
* crashes;
* malformed outputs;
* timeouts;
* relevant terminal results.

Do not fabricate or infer unrun results.

## 10. Mandatory verification commands

Before declaring the milestone complete, run:

```bash
pytest
ruff check .
ruff format --check .
git diff --check
```

Also run any milestone-specific tests and complete-episode verification.

If any mandatory check fails:

* fix failures caused by the milestone;
* do not hide, skip, or weaken relevant tests;
* rerun the complete required verification set.

Do not claim completion with a failing required check.

## 11. Strategy-change gate

If implementation reveals a potential strategy improvement, separate it from the active milestone unless the milestone itself is strategy work.

A material policy change must not be merged because it appears better.

When evaluating a candidate policy, follow `TILLA_STRATEGY.md`.

The current promotion process requires controlled paired evaluation against a frozen incumbent, including seat swaps and the configured statistical/terminal-cash guardrails.

Do not call smoke games, starter games, or a small seed sample a promotion benchmark.

Do not modify the incumbent while evaluating the candidate.

## 12. Rule discrepancy procedure

If observed environment behavior conflicts with `TILLA_RULES.md`:

1. stop depending on the disputed assumption;
2. inspect official Kaggriculture documentation/source;
3. write the smallest useful reproduction/conformance test;
4. establish the implementation truth;
5. update `TILLA_RULES.md` and the conformance test together if necessary;
6. only then adapt dependent code.

Do not silently compensate in strategy.

## 13. Diff review

Before committing, inspect:

```bash
git status --short
git diff --check
git diff
```

Confirm:

* no unrelated refactors;
* no accidental source-of-truth edits;
* no runtime dependency additions;
* no forbidden imports;
* no generated junk/caches/secrets;
* no future-milestone implementation;
* no unexplained public API changes.

Remove accidental changes before committing.

## 14. Commit

When all required checks pass and the Definition of Done is demonstrated, create one focused milestone commit.

Use a clear message such as:

```text
milestone-0: establish repository contract
```

or the equivalent title for the active milestone.

After committing, verify:

```bash
git status --short
git log -1 --oneline
```

The working tree should be clean unless an explicit, justified exception is reported.

## 15. Required implementation report

Finish with a concise structured report using this format:

```text
Milestone:
Definition of Done:

Files created:
- ...

Files modified:
- ...

Implemented:
- ...

Focused tests added:
- ...

Verification:
- pytest: ...
- ruff check .: ...
- ruff format --check .: ...
- git diff --check: ...
- full Kaggriculture episode: ...

Benchmark evidence:
- Not applicable
```

For benchmark milestones, replace `Not applicable` with actual measured evidence.

Then include:

```text
Source-of-truth deviations:
- None
```

or list each approved deviation precisely.

Then:

```text
Unresolved issues:
- None
```

or document actual unresolved items.

Finally:

```text
Commit:
- <hash> <message>

Final git status:
- clean
```

Do not describe the milestone as complete unless its exact Definition of Done has been demonstrated.

