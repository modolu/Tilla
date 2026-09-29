# Milestone 6 Promotion Gate

> **Strategy gate: PASS.**
> **Runtime compliance: PASS** (controlled AC confirmation, 2026-09-29).
> **Overall M6: PASS** — accepted as `21195e7`.

Milestone 6 (opponent model): visible opponent pipeline inference, harvest windows, confidence-weighted market pressure (TILLA_ARCHITECTURE.md §20; policy TILLA_STRATEGY.md §15). Definition of Done: opponent-aware candidate passes the promotion gate against a diverse incumbent/baseline mix.

## Candidate and comparators

- Candidate runtime hash (`shasum -a 256 kaggriculture_bot/*.py main.py | shasum -a 256`): `0efbcab457bcc7b4d3edbdd8a0531fc6f11b07fb77221c63382e419e82591366`
- Candidate diff hash (`git diff HEAD` excluding `tools/harness.py`, `tests/test_tools.py`; HEAD `23b95b4`): `dea181fa57ac469863eb26027fa515d88c859da9fe625d53717ccbef7dd3c827`
- Tooling diff hash (offline care-checker fix, `tools/harness.py` + `tests/test_tools.py`; locked after the amendment below): `51194308a705f99acdc40a9078e783552689138fd9cb9ded30f230546c1c6ba3`
- Incumbent: frozen accepted M5 (`agents/incumbent_m5`, commit `23b95b4` "chore: freeze milestone-5 incumbent"; byte-identical to `5c3f1ef` modulo import namespace; replay-identical)
- Diversity: frozen M4 (`agents/incumbent_m4`), `agents/baseline.py` (unchanged vs `4db49e4`), built-in `starter`, `random`
- Ablation: the candidate with `OPPONENT_INFLUENCE = 0` (offline switch), expected to reproduce M5 exactly
- Accepted M6 implementation commit: `21195e73fa77d510032dc5db66b535f0b68fb227` (milestone-6: implement opponent-aware market model), preceded by `27aacba` (M5 strategy record S-002) and `2782de1` (offline care-checker correction); the committed diff from `23b95b4` reproduces both locked hashes exactly

## Strategy gate (paired, both seats per seed)

Criteria for every partition and for stable + holdout combined: win rate > 53%, Wilson 95% lower > 50%, median terminal margin > 0, 0 crashes (ERROR), 0 TIMEOUT, 0 invalid, 0 malformed, 0 parse failures, 0 true care losses, all episodes 720 steps DONE/DONE, no duplicate/corrupt/missing rows, candidate hashes unchanged. Ablation: exact M5 mirror (paired margin 0 on every seed).

| Partition | Seeds | Episodes / pairs | W/L/T | Win rate | Wilson lower | Median / mean margin | Seat 0 W-L-T | Seat 1 W-L-T | Paired +/0/− | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| Stable vs M5 | 40000–41499 | 3000 / 1500 | 1808 / 1191 / 1 | 60.27% | 58.50% | +1,205 / +1,574 | 891-608-1 | 917-583-0 | 966 / 0 / 534 | PASS |
| Holdout vs M5 | 50000–50099 | 200 / 100 | 115 / 85 / 0 | 57.50% | 50.57% | +760 / +1,384 | 58-42-0 | 57-43-0 | 66 / 0 / 34 | PASS |
| Stable + holdout | — | 3200 / 1600 | 1923 / 1276 / 1 | 60.09% | 58.39% | +1,202 / +1,562 | 949-650-1 | 974-626-0 | 1032 / 0 / 568 | PASS |
| vs M4 | 40000–40249 | 500 / 250 | 500 / 0 / 0 | 100% | 99.24% | +9,742 / +9,269 | 250-0-0 | 250-0-0 | 250 / 0 / 0 | PASS |
| vs baseline | 40000–40050 | 102 / 51 | 102 / 0 / 0 | 100% | 96.37% | +55,682 / +55,844 | 51-0-0 | 51-0-0 | 51 / 0 / 0 | PASS |
| vs starter | 40000–40024 | 50 / 25 | 50 / 0 / 0 | 100% | 92.86% | +59,628 / +59,729 | 25-0-0 | 25-0-0 | 25 / 0 / 0 | PASS |
| vs random | 40000–40024 | 50 / 25 | 50 / 0 / 0 | 100% | 92.86% | +62,842 / +62,937 | 25-0-0 | 25-0-0 | 25 / 0 / 0 | PASS |
| Ablation (influence off) vs M5 | 40000–40024 | 50 / 25 | 15 / 15 / 20 | — | — | 0 / 0 | 7-8-10 | 8-7-10 | 0 / 25 / 0 | PASS (exact mirror) |

All partitions: 0 errors, 0 invalid, 0 TIMEOUT, 0 malformed, 0 parse failures; care losses (crops lost unwatered / fresh plantings unwatered / animals escaped) 0 / 0 / 0 after the care-checker correction; integrity (runtime, diff, tooling hashes) matched at every phase boundary.

Stable 100-seed blocks (200 episodes each) all positive: win rate 54.0%–65.0%, paired-seed medians +1,666 to +4,669.

Holdout note: its Wilson lower bound (50.57%) clears the threshold narrowly at 200 episodes; the combined 3,200-episode comparison is 58.39%.

## Gate history and amendments

1. **Preregistered matrix** (`benchmarks/results/m6_gate_metadata.txt`): stable 3000 → diversity (M4 500, baseline 200, starter 200, random 100, ablation 50) → holdout 1000 if stable passes.
2. **Stable completed** (3000/3000, valid). The runner's criteria check exited 1. Before any outcome statistic was read, the runner was stopped (diversity had 602 episodes: M4 500, baseline 102); holdout had not started.
3. **Cause: offline measurement defect, not candidate behaviour.** The only failing criterion was 7 `crops_lost_unwatered`. Deterministic replays of all 7 showed the same event: a hand HARVESTs a mature melon (yield 6, credited 18 → 24) on day 12 hour 23; the emptied tile receives the official random end-of-day weed; the old `tools/harness.py` `_care_losses` compared the pre-action PLANT with the post-refresh WEED and counted it as unwatered. The checker now records that case as `harvested_then_weed_spawn` (a strict subset of the old count; every genuine unwatered death still counts), with real-environment regression tests (`tests/test_tools.py`). The 7 episodes, replayed with the corrected checker, reproduce their recorded banks with 0 true losses; corrections are recorded in `gate_m6_care_corrections.json` and the raw rows are unmodified. Integrity was amended: runtime hash unchanged; candidate diff hash computed excluding the two tooling files equals the original lock (both files were unchanged at lock time); the tooling fix has its own locked hash.
4. **Reduced remaining matrix, locked before any holdout/diversity outcome was computed**: holdout 200 episodes (still untouched); all existing diversity episodes kept (M4 500, baseline 102); starter, random and ablation 50 each. Only missing work was played. Stable was not shrunk or replaced.

## Development evidence (supporting only; not gate statistics)

Seeds 30000–30099 (fresh), candidate hash `3ffe2683…` (behaviour-identical to the gate candidate; the only later runtime change was a behaviour-identical helper extraction verified by 10 replays with identical banks and action-trace hashes): M6 vs M5 121/79/0 (60.5%, Wilson 53.6%, median +1,236); M6 vs M4 80/0; baseline, starter identical to M5; ablation exact M5 mirror. Mechanism: M6 sells ~156 melons/game at avg 169.4 vs the M5 mirror's ~180 at 139.7, floor-price melon sales ~2.5 vs ~13 per game — it avoids synchronized melon gluts visible on the opponent farm.

## Runtime compliance — PASS

Loaded 4-worker gate timing is not compliance evidence (gate: 2 episodes with a call ≥ 1 s — holdout 50080:0 1,461 ms, M4 40248:0 1,094 ms).

**Controlled battery probe** (single process; Low Power Mode off; battery 78% → 65%; browsers open, ~0.5–1 core; exact candidate; every gate episode with `ms_max ≥ 500 ms` × 3 plus 30 controls, each against its original opponent):

| Set | Calls | p50 | p95 | p99 | Max | ≥500 ms | ≥1000 ms | ≥2000 ms |
|---|---|---|---|---|---|---|---|---|
| All | 86,280 | 7.87 | 12.26 | 19.50 | 1,344.8 | 2 | 1 | 0 |
| Flagged (30 × 3) | 64,710 | 7.48 | 11.31 | 18.24 | 280.2 | 0 | 0 | 0 |
| Control (30) | 21,570 | 8.98 | 13.98 | 21.94 | 1,344.8 | 2 | 1 | 0 |

- Slowest call: control `40958` seat 1, step 655, rep 0 — **1,344.8 ms wall, 25.2 ms CPU, 0 ms GC** (step 660: 560.8 ms wall, 24.7 ms CPU): host preemption signature, not candidate computation.
- Other slow calls are cyclic-GC pauses (134 of 160 calls ≥ 100 ms spend ≥ 50% in GC); max CPU of any call 220.6 ms.
- Recheck of `40958` seat 1 × 3 (separate file): steps 655/660 take 9–12 ms each rep; episode max 85 / 64 / 116 ms; banks identical to the gate.
- The two loaded-gate > 1 s cases do not reproduce: `50080:0` → 181 / 169 / 219 ms; `40248:0` → 159 / 192 / 221 ms.
- Replay fidelity: 117/120 exact (banks, statuses, 720 steps); the 3 exceptions are `m6-vs-random 40022:1`, whose built-in `random` opponent is unseeded.

Under the preregistered battery policy any call ≥ 1000 ms required review, so runtime compliance was held at NEEDS REVIEW until a short AC confirmation, preregistered in `benchmarks/results/m6_ac_confirm_jobs.json` before any AC run: `40958:1` (the battery host stall), `50080:0`, `40248:0` × 3 each, plus 10 controls (preregistered controls `[1::3]`, disjoint from the cases); PASS iff 0 controlled calls ≥ 1000 ms.

**Controlled AC confirmation** (2026-09-29; AC connected and charging, Low Power Mode off, single process, M7 benchmark workers stopped, browsers idle, load average 2.7 → 1.4; exact locked candidate — runtime, diff and tooling hashes verified; every candidate call recorded with seed, seat, step, rep, wall/CPU/GC ms and action; evidence `benchmarks/results/m6_ac_confirm_calls.jsonl`):

| Set | Calls | p50 | p95 | p99 | Max | ≥500 ms | ≥1000 ms | ≥2000 ms |
|---|---|---|---|---|---|---|---|---|
| All | 13,661 | 7.21 | 9.76 | 10.60 | 284.7 | 0 | 0 | 0 |
| Cases (3 × 3) | 6,471 | 7.26 | 9.78 | 10.71 | 151.3 | 0 | 0 | 0 |
| Controls (10) | 7,190 | 7.17 | 9.73 | 10.59 | 284.7 | 0 | 0 | 0 |

- Slowest call: control `40533` seat 0, step 714, rep 0 — 284.7 ms wall, 139.7 ms CPU, 282.3 ms GC. All 17 calls ≥ 50 ms spent ≥ 50% of their wall time in cyclic GC; max CPU of any call 139.7 ms; max wall minus GC 42.1 ms.
- The battery stall case `40958:1` peaked at 19 / 35 / 25 ms (was 1,344.8 ms wall / 25.2 ms CPU on battery); the loaded-gate cases `50080:0` → 104 / 109 / 100 ms and `40248:0` → 151 / 132 / 93 ms.
- Replay fidelity: 19/19 episodes reproduced the gate's banks, statuses and 720 steps exactly.

**Runtime compliance: PASS** — 0 controlled candidate calls ≥ 1000 ms (and 0 ≥ 500 ms; ~3.5× headroom at the maximum, which is GC-dominated).

## Final checks (rerun 2026-09-29 on the unchanged locked tree)

pytest 464 passed; ruff check clean; ruff format --check clean; git diff --check clean; `git diff 4db49e4 -- agents/baseline.py` empty; `agents/incumbent_m4` and `agents/incumbent_m5` manifests 14/14; `docs/milestones/M5_GATE_REPORT.md` unchanged; runtime stdlib-only, no randomness/network/wall-clock; no opponent private state read.

Raw evidence (gitignored, local): `benchmarks/results/gate_m6_{stable,holdout,diversity}.jsonl`, `gate_m6_final_report.json`, `gate_m6_care_corrections.json`, `m6_gate_metadata.txt`, `m6_gate.log`, `m6_timing_probe_calls.jsonl`, `m6_timing_probe_recheck_40958.jsonl`, `m6_ac_confirm_jobs.json`, `m6_ac_confirm_calls.jsonl`, `m6_dev.jsonl`.

All three locked hashes matched after the checks; git identity `modolu <heritage143@gmail.com>`.

## Verdict

**M6 PASS** — every strategy partition passed (stable, holdout, combined, M4, baseline, starter, random, exact-mirror ablation) with clean integrity and 0 true care losses, and runtime compliance passed under controlled AC conditions. Accepted implementation commit: `21195e7`.
