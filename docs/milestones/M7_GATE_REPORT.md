# Milestone 7 Promotion Gate

> **M7 STRATEGY: PASS.**
> **M7 RUNTIME: PASS** (controlled AC probe, 2026-09-29).
> **M7 OVERALL: PASS — PROMOTE M7.**
> **Accepted implementation commit: `6ed9030860b779edfb32185c932d8bec09d4ca6c`** (milestone-7: optimize final-day liquidation); strategy record S-003 `20b843d`.

Milestone 7 (phase/endgame policy), minimal scope: a bounded final-day policy on top of the accepted M6 (TILLA_STRATEGY.md §17 "Milestone 7 final-day policy"):

- no feed-wheat purchase and no FEED on day 29;
- ineffective final-day maintenance suppressed (no ongoing-crop watering; a one-time crop is watered only when its bonus unit can still be harvested and sold);
- final-day HARVEST/COLLECT targets carry a delivery deadline (`22 − distance to the nearest usable shed access tile`);
- a carrying unit whose drop is due gets DELIVER at survival priority;
- `ENDGAME_POLICY` offline ablation switch (off reproduces M6 exactly);
- M6 opponent/market logic, days 0–28 and land policy unchanged; land expansion and broader scaling out of scope.

## Candidate and comparators

- Accepted M7 runtime hash (`shasum -a 256 kaggriculture_bot/*.py main.py | shasum -a 256`): `11e026d3aa3b6bd9272c91fcbc56a0a81238965709ffc3d2aa9c8483bc73dc9a`
- Evaluated in worktree `parallel/m7-endgame` (base `23b95b4` + the locked M6 patches + M7); gate locks (runtime, `git diff HEAD` `657ed3a277f4…c826`, `tests/test_endgame.py` `092a6a6faabe…7288`, comparator package) held at every phase boundary.
- Landed on `main` as the M7-only diff against `21195e7` (accepted M6): every runtime file, `TILLA_STRATEGY.md` and `tests/test_endgame.py` byte-identical to the evaluated tree; runtime hash reproduced exactly.
- Comparator: the accepted M6 (`21195e7`, runtime `0efbcab457bc…1366`). The gate ran against a reconstruction of the locked M6 that was proven identical to the accepted and frozen M6 (`agents/incumbent_m6`, `edf7d93`): same runtime hash, all 13 runtime files identical after namespace normalization, identical actions on 6/6 fixtures and identical banks on 3 full games. The first 44 stable episodes (run before M6 was accepted) therefore remained valid.
- Diversity: frozen M5 (`agents/incumbent_m5`), frozen M4 (`agents/incumbent_m4`), `agents/baseline.py`.

## Development evidence (supporting only; not gate statistics)

Seeds 60000–60049, both seats, 100 episodes vs the reconstructed locked M6:

- 77 / 23 / 0, win 77.0%, Wilson lower 67.85%, median margin +533;
- M7 own-bank delta vs switch-off M6 (same seed and seat): median +541; 92/100 improved, 0 equal, 8 worse;
- ablation (ENDGAME_POLICY off vs M6, seeds 60000–60024): exact mirror;
- 0 crashes, TIMEOUTs, invalid or malformed actions; 0 true care losses.

## Formal gate (paired, both seats per seed, vs accepted M6 unless noted)

Criteria for stable, holdout and stable + holdout: win rate > 53%, Wilson 95% lower > 50%, median terminal margin > 0, median M7-minus-switch-off-M6 own-bank delta > 0, 0 crashes, 0 TIMEOUTs, 0 invalid, 0 malformed, 0 true care losses, complete valid evidence, all locks unchanged. Diversity: the same statistical and integrity criteria. Ablation: exact M6 mirror.

| Partition | Episodes / pairs | W / L / T | Win rate | Wilson lower | Median / mean margin | Seat 0 W-L | Seat 1 W-L | Paired + / 0 / − | Paired median | Own-bank delta median | Improved / worse | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Stable (60100–60399) | 600 / 300 | 435 / 165 / 0 | 72.50% | 68.79% | +539 / +672 | 208-92 | 227-73 | 292 / 0 / 8 | +1,101 | +548 | 558 / 42 | PASS |
| Holdout (60500–60599, untouched until stable passed) | 200 / 100 | 148 / 52 / 0 | 74.00% | 67.51% | +539 / +587 | 72-28 | 76-24 | 96 / 0 / 4 | +1,077 | +550 | 189 / 11 | PASS |
| Stable + holdout | 800 / 400 | 583 / 217 / 0 | 72.88% | 69.69% | +539 / +651 | 280-120 | 303-97 | 388 / 0 / 12 | +1,093 | +549 | 747 / 53 | PASS |
| vs M5 (60100–60124) | 50 / 25 | 37 / 13 / 0 | 74.0% | 60.45% | +2,756 | 19-6 | 18-7 | 19 / 0 / 6 | +5,701 | — | — | PASS |
| vs M4 (60100–60124) | 50 / 25 | 50 / 0 / 0 | 100% | 92.86% | +10,226 | 25-0 | 25-0 | 25 / 0 / 0 | +20,684 | — | — | PASS |
| vs baseline (60100–60124) | 50 / 25 | 50 / 0 / 0 | 100% | 92.86% | +57,377 | 25-0 | 25-0 | 25 / 0 / 0 | +113,937 | — | — | PASS |
| Ablation: ENDGAME_POLICY off vs M6 (60100–60124) | 50 / 25 | exact mirror | — | — | 0 | — | — | 0 / 25 / 0 | 0 | — | — | PASS |

Own-bank delta ranges: stable −813 to +3,071; holdout −1,584 to +3,091. The switch-off baseline for each seed comes from its ENDGAME_POLICY-off vs M6 game (an M6-vs-M6 game, identical from either seat); the ablation partition verifies that symmetry explicitly (paired margin 0 and seat-swapped banks equal on all 25 seeds).

**Integrity (all partitions):** 0 crashes, 0 TIMEOUTs, 0 invalid actions, 0 malformed actions, 0 true care losses (corrected care checker), all games 720 steps DONE/DONE, no duplicate/corrupt/missing rows, all locks intact at every phase boundary.

**Execution history:** the gate was launched provisionally while M6 awaited its AC runtime confirmation, paused at 44/600 stable episodes to run that confirmation, and resumed without replaying completed episodes after M6 was accepted unchanged and the comparator was proven identical to it. Runner order: stable → holdout → diversity + ablation, stopping on any failure. No tuning was performed at any point.

## Final-day diagnostics (per game; M6 = the switch-off baseline)

| Measure | M6 stable | M7 stable | M6 holdout | M7 holdout |
|---|---|---|---|---|
| Carried inventory value stranded at the end | 676.8 | 268.9 | 622.8 | 374.3 |
| Day-29 feed-wheat bought | 6.44 | 0 | 6.45 | 0 |
| Day-29 FEED actions | 3.41 | 0 | 3.41 | 0 |
| Day-29 ineffective WATER actions (all on ongoing crops under M6) | 3.01 | 0 | 3.23 | 0 |
| Harvestable yield left on the field | 0.45 | 0.07 | 0.48 | 0.07 |

About 7% of same-seed/seat cases (42/600 stable, 11/200 holdout) ended slightly worse than switch-off M6 (typically a carried unit or 1–2 field units left where M6's greedy harvesting happened to deliver). They were recorded, not tuned.

## Runtime compliance — PASS

Loaded gate timing is advisory only. **Controlled AC probe** (2026-09-29; AC, fully charged, Low Power Mode off, single process, no benchmark workers, browsers idle, a macOS indexing daemon ~0.8 of one core; exact locked candidate; 16 episodes preregistered before the run: 10 evenly spaced stable, 3 holdout, 1 each vs M5, M4, baseline, each against its original opponent; every candidate call recorded with seed, seat, step, wall/CPU/GC ms and action):

| Calls | p50 | p95 | p99 | Max | ≥ 500 ms | ≥ 1000 ms | ≥ 2000 ms |
|---|---|---|---|---|---|---|---|
| 11,504 | 7.18 ms | 9.80 ms | 10.83 ms | 166.1 ms | 0 | 0 | 0 |

- Slowest call: `60220` seat 0, step 333 — 166.1 ms wall, 134.3 ms CPU, 159.2 ms GC; all 22 calls ≥ 50 ms are GC-dominated.
- Final-day decisions: median 2.0 ms, max 2.7 ms.
- Replay fidelity: 16/16 episodes reproduced the gate's banks exactly.

## Final checks (main, after landing)

pytest 505 passed (1 intended skip: the day-29 official fixture in the days-0–28 equivalence test); ruff check clean; ruff format --check clean; git diff --check clean; `git diff 4db49e4 -- agents/baseline.py` empty; `agents/incumbent_m4`, `_m5`, `_m6` manifests 14/14; ENDGAME_POLICY off reproduces the frozen M6 (6/6 fixtures, exact mirrors on 2 seeds); no opponent private state read.

Raw evidence (gitignored, local to the M7 worktree): `benchmarks/results/gate_m7_{stable,holdout,diversity,off}.jsonl`, `gate_m7_final_report.json`, `m7_gate_lock.json`, `m7_gate_metadata.txt`, `m7_gate.log`, `m7_dev.jsonl`, `m7_timing_jobs.json`, `m7_timing_probe_calls.jsonl`.

## Verdict

**M7 STRATEGY: PASS. M7 RUNTIME: PASS. M7 OVERALL: PASS — PROMOTE M7.**
