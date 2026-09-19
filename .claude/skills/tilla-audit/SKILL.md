---

name: tilla-audit
description: Adversarially audit a Tilla implementation, milestone, diff, or coding-agent report against TILLA_ARCHITECTURE.md, TILLA_RULES.md, TILLA_STRATEGY.md, milestone Definition of Done, test evidence, benchmark integrity, and module boundaries. Use before accepting milestone completion or promotion claims.
--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# Tilla Adversarial Audit

Audit Tilla as a skeptical maintainer.

Your job is not to explain why the implementation is probably fine.

Your job is to determine whether the implementation and its evidence actually satisfy the repository contracts.

Assume subtle architectural drift is possible even when tests pass.

## 1. Read the controlling documents first

Before reviewing implementation or accepting a report, read in full:

* `TILLA_ARCHITECTURE.md`
* `TILLA_RULES.md`
* `TILLA_STRATEGY.md`
* root `CLAUDE.md`

Then identify the active milestone and read its exact Definition of Done.

Do not audit from memory.

## 2. Inspect repository state

Run or inspect:

```bash
git status --short
git log --oneline -10
```

Identify the commit or diff under review.

Review the actual code and tests, not only the coding agent's implementation summary.

A report is evidence of what the agent claims it did; it is not proof that the code does it.

## 3. Determine review scope

Establish:

* active milestone;
* expected files/modules;
* milestone Definition of Done;
* implementation commit(s);
* strategy changes, if any;
* mechanics changes, if any;
* claimed tests;
* claimed integration episodes;
* claimed benchmark evidence.

Distinguish:

* implementation correctness;
* architectural correctness;
* rule correctness;
* strategic-policy correctness;
* benchmark/promotion evidence.

Do not merge these into one vague assessment.

## 4. Audit source-of-truth compliance

Check whether implementation agrees with each document for the category that document owns.

### Architecture

Check:

* repository structure;
* module ownership;
* dependency direction;
* runtime/offline separation;
* data-flow boundaries;
* milestone sequencing;
* testing requirements;
* submission/runtime constraints.

### Rules

Check:

* mechanics encoded by constants/calculations;
* visibility assumptions;
* action shape and limits;
* crop/animal timing;
* market mechanics;
* town demand assumptions;
* end-of-day behavior;
* submission contract.

Do not accept a strategic explanation as justification for changing a mechanic.

### Strategy

Check:

* season-phase behavior;
* survival priority;
* cash reserve;
* opportunity scoring;
* crop/animal policy;
* hiring/land policy;
* market/town policy;
* opponent modelling;
* endgame policy;
* promotion criteria.

Do not accept a new policy merely because it seems reasonable.

## 5. Architectural drift checks

Search specifically for these violations.

### `main.py`

Flag strategy, economics, pathfinding, duplicated constants, or substantial game logic.

It should remain an adapter and outer safety boundary.

### `parser.py`

Flag:

* decisions;
* opportunity scoring;
* strategic defaults masquerading as parsing;
* policy-driven normalization.

Parser means parsing.

### `actions.py`

Flag strategy or decision-making.

Formatting an internal action into Kaggle syntax is valid.

Choosing what Tilla should do is not.

### `strategy.py`

Flag:

* raw observation dictionary access;
* BFS/pathfinding;
* hidden-state access;
* action formatting;
* direct environment shortcuts.

### `tasks.py`

Flag:

* market-price formulas;
* ROI calculations;
* strategic opportunity scoring;
* duplicated strategy decisions.

### Offline/runtime boundary

Flag any submitted-runtime import from:

* `tools`;
* `tests`;
* `benchmarks`.

Flag any runtime reliance on local-only files, network calls, subprocesses, databases, or external services.

## 6. Information-leak audit

This is high priority.

Opponent public information may inform estimates.

Opponent private state must not become factual input.

Specifically check for access, direct or indirect, to:

* opponent shed;
* opponent seeds;
* opponent carried inventory;
* hidden evaluator/environment objects;
* replay-only hidden information;
* local harness internals unavailable in Kaggle observations.

Any forecast based on public state must remain an estimate.

Check that confidence or uncertainty is preserved where the architecture/strategy requires it.

Test infrastructure must not accidentally create capabilities unavailable in submission.

## 7. Determinism audit

Look for:

* unseeded `random`;
* dependence on wall-clock time;
* unordered-set/dict iteration used as an intentional tie-break where output may drift;
* nondeterministic task assignment;
* concurrency that can affect action choice;
* process-global state mixing players in self-play.

Given the same observation and equivalent episode memory, output should be deterministic.

## 8. Money/state-model audit

Check:

* actual balances use integers;
* quantities/counts use integers;
* affordability uses integers;
* floating point is confined to estimates/scores where appropriate;
* `GameState` is not being mutated after parsing;
* episode memory is bounded;
* episode reset is correct;
* self-play does not mix both players' runtime memory.

## 9. Rule duplication audit

Search for official game constants duplicated across multiple modules.

Check especially:

* episode length;
* board dimensions;
* market-order cap;
* land costs;
* crop constants;
* animal constants;
* shed capacity;
* phase-independent mechanics.

A game constant should have one intended source in implementation where practical.

Do not allow slightly different copies to drift.

## 10. Strategy-drift audit

Determine whether the implementation materially changed Tilla policy without acknowledging it.

Look for changes to:

* phase thresholds;
* cash reserve;
* shed thresholds;
* crop rankings;
* animal preferences;
* fertilizer logic;
* land-buy criteria;
* hand-hiring criteria;
* market hold/sell behavior;
* opponent reaction;
* endgame liquidation;
* risk/realization assumptions.

If behavior changed materially, ask:

1. Was the change intentional?
2. Is it documented in `TILLA_STRATEGY.md`?
3. Was a candidate benchmark run?
4. Did it pass the promotion gate?

If not, classify it as unpromoted strategy drift.

## 11. Rule-change audit

If implementation behavior differs from `TILLA_RULES.md`, determine whether:

* official source was inspected;
* behavior was reproduced where necessary;
* conformance test exists;
* `TILLA_RULES.md` was corrected;
* strategy was kept separate from mechanics.

Flag any guessed mechanic.

Flag any change that modifies strategy first and verifies mechanics later.

## 12. Test-quality audit

Do not merely count tests.

Inspect whether tests prove the important contract.

Check for:

* focused regression coverage;
* parser normalization;
* legality/schema protection;
* rule-conformance tests where mechanics are copied;
* relevant deterministic scenarios;
* runtime reset;
* milestone-specific behavior;
* integration/full episodes.

Flag tests that only restate implementation internals without validating source-of-truth behavior.

Flag weakened assertions added merely to make a failing implementation pass.

Flag skipped tests hiding relevant regressions.

## 13. Full-episode evidence audit

Verify the active milestone's required integration evidence.

Check:

* actual official Kaggriculture environment was used;
* requested episode length was used;
* correct opponent was used;
* `main.agent` was actually involved where required;
* episode completed;
* no crash;
* no malformed action;
* no timeout;
* terminal result is reported accurately.

For Milestone 0 specifically, the required proof is a complete official 720-turn PASS-vs-PASS episode using Tilla's `main.py` agent.

A unit test invoking `agent()` once does not satisfy this.

A shortened episode does not satisfy this.

## 14. Mandatory command audit

Confirm actual evidence for:

```bash
pytest
ruff check .
ruff format --check .
git diff --check
```

Also confirm any milestone-specific verification.

Do not assume success because the implementation report says "all checks pass" if logs or reproducible execution contradict it.

Where practical, rerun the checks independently.

## 15. Benchmark integrity audit

For any material strategy promotion claim, verify the benchmark against `TILLA_STRATEGY.md`.

Current promotion requires at minimum the configured paired-game count and all configured statistical/performance guardrails.

Check:

* candidate and incumbent identities;
* incumbent immutability;
* paired seeds;
* seat swaps;
* game count;
* candidate wins/losses/ties;
* win-rate calculation;
* Wilson lower bound;
* terminal cash margins;
* crash count;
* timeout count;
* mandatory scenarios;
* stable versus holdout seed handling.

Flag:

* cherry-picked seeds;
* one-seat testing;
* incumbent modification;
* unreported ties;
* average cash substituted for paired win rate;
* insufficient sample size;
* benchmark results without reproducible seed/config information;
* claims such as "better" or "promoted" without passing the gate.

Do not reproduce a positive promotion conclusion unless the evidence actually satisfies every required criterion.

## 16. Scope-creep audit

Compare changed files against the active milestone.

Flag:

* future-milestone implementations;
* unrelated refactors;
* renames not required by the milestone;
* new abstractions without architectural need;
* broad formatting churn;
* dependency additions;
* documentation rewrites unrelated to the change.

Small diffs are not automatically good, but every changed file should have a reason tied to the milestone.

## 17. Submission-safety audit

Check for:

* accidental credentials;
* `.env`;
* network calls;
* filesystem-write requirements;
* subprocesses;
* dynamic execution;
* runtime third-party imports;
* benchmark/test imports in runtime;
* stdout spam;
* malformed fallback behavior.

Where packaging is in scope, verify the actual archive contents rather than inferring them.

## 18. Severity classification

Classify each finding as:

### BLOCKER

The milestone must not be accepted.

Examples:

* Definition of Done not demonstrated;
* architecture boundary violation affecting design;
* hidden opponent state;
* incorrect verified mechanic;
* malformed/unsafe runtime output;
* required checks failing;
* crash/timeout;
* unapproved runtime dependency;
* false benchmark/promotion claim.

### MAJOR

Should be fixed before proceeding because it creates likely correctness or drift risk.

Examples:

* missing focused tests;
* material strategy drift without benchmark;
* duplicated mechanic likely to diverge;
* incomplete integration evidence;
* future-milestone implementation that complicates current scope.

### MINOR

Real issue but does not invalidate milestone completion by itself.

Examples:

* naming inconsistency;
* unnecessarily awkward local code;
* weak comments;
* small test clarity issue.

### NOTE

Observation or future consideration that is not a defect in the active milestone.

Do not inflate stylistic preferences into blockers.

## 19. Audit verdict

Use one of only these verdicts:

```text
ACCEPT
```

The exact milestone Definition of Done is demonstrated and there are no BLOCKER or unresolved MAJOR findings that invalidate completion.

```text
ACCEPT WITH MINOR FIXES
```

Definition of Done is demonstrated; only MINOR issues remain.

```text
REJECT
```

One or more BLOCKER findings exist, or the Definition of Done has not been demonstrated.

Do not use vague verdicts such as "mostly good."

## 20. Required audit report

Return:

```text
Tilla Audit

Milestone:
Definition of Done:
Commit/diff reviewed:

Verdict:
- ACCEPT | ACCEPT WITH MINOR FIXES | REJECT

Blockers:
- None
```

or enumerate them.

Then:

```text
Major findings:
- None

Minor findings:
- None

Architecture compliance:
- ...

Rules compliance:
- ...

Strategy compliance:
- ...

Test evidence:
- ...

Full-episode evidence:
- ...

Benchmark evidence:
- Not applicable
```

When benchmarks are applicable, summarize the exact measured evidence.

Then:

```text
Scope/drift check:
- ...

Required fixes before acceptance:
- None
```

or provide specific actionable fixes.

Finally:

```text
Recommended next milestone:
- <only if current milestone is accepted>
```

Do not recommend proceeding to the next milestone when the current Definition of Done has not been demonstrated.

