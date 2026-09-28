# Milestone 5 Promotion Gate

**Accepted M5 commit:** `5c3f1ef1e17842459813aa5c99d94d0dc58eabc3` (milestone-5: implement market and town model)

Evaluated candidate (verified identical to the accepted commit):
- runtime hash (`shasum -a 256 kaggriculture_bot/*.py main.py | shasum -a 256`): `7e7da8e280d1c3d1e7b31a327668e456aebbc89337f69b1414862a7eb206a016`
- candidate diff hash (`git diff 1b5b00f` of tracked files; equals `git diff 1b5b00f 5c3f1ef -- . ':(exclude)tests/test_market.py'`): `7df44f9d60237310ba057cf6261dc38382a2947a480e267739e4d2907cf7f8cf`
- `tests/test_market.py` sha256: `72ff13c828d54a7213e1eab7dff649374273a8254e5a5529a84d0a56c3ecd1cb`

Summary (all figures reproduced from the sections below):

| set | episodes | paired seeds | W / L / T | win rate | Wilson 95% lower | median margin | paired +/0/− |
|---|---|---|---|---|---|---|---|
| stable (10000–11499) | 3000 | 1500 | 2948 / 52 / 0 | 98.27% | 97.73% | +7,351 | 1499 / 0 / 1 |
| holdout (20000–20499) | 1000 | 500 | 976 / 24 / 0 | 97.60% | 96.45% | +7,444 | 500 / 0 / 0 |
| combined | 4000 | 2000 | 3924 / 76 / 0 | 98.10% | 97.63% | +7,426 | 1999 / 0 / 1 |

Candidate errors (Kaggle ERROR) 0, invalid actions (Kaggle INVALID) 0, Kaggle TIMEOUT 0, malformed actions 0,
parse failures 0 in all 4000 episodes (the "crashes" rows below are ERROR + INVALID).
Runtime compliance: PASS — 86,280 timed candidate calls under controlled conditions, max 271.6 ms,
0 calls >= 500 ms, 0 calls >= 1 s.

This is the tracked copy of `benchmarks/results/M5_GATE_REPORT.md`. The raw gate/probe datasets and logs it
cites under `benchmarks/results/` are gitignored local artifacts and are intentionally not committed.

- Candidate runtime hash: `7e7da8e280d1c3d1e7b31a327668e456aebbc89337f69b1414862a7eb206a016` (current: `7e7da8e280d1c3d1e7b31a327668e456aebbc89337f69b1414862a7eb206a016`, match: True)
- Candidate diff hash: `7df44f9d60237310ba057cf6261dc38382a2947a480e267739e4d2907cf7f8cf` (current: `7df44f9d60237310ba057cf6261dc38382a2947a480e267739e4d2907cf7f8cf`, match: True)
- Incumbent commit: `1b5b00f`
- Incumbent snapshot: frozen M4 (`agents/incumbent_m4/`, MANIFEST verified)
- Metadata: `benchmarks/results/m5_gate_metadata.txt`; runner log: `benchmarks/results/m5_gate.log`
- Report generated: 2026-09-28T20:00:44Z

## Stable (seeds 10000–11499)

| metric | value |
|---|---|
| completed | 3000 / 3000 |
| wins / ties / losses | 2948 / 0 / 52 |
| win rate | 98.27% |
| Wilson 95% lower | 97.73% |
| seat 0 (W/T/L) | 1474 / 0 / 26 |
| seat 1 (W/T/L) | 1474 / 0 / 26 |
| median margin | +7,351 |
| mean margin | +7,384.6 |
| min / max margin | -6,378 / +15,270 |
| candidate bank range | 33,709.0 – 51,124.0 |
| incumbent bank range | 30,470.0 – 44,762.0 |
| paired seeds +/0/− | 1499 / 0 / 1 |
| median paired margin | +14,661 |
| worst paired seeds | 10693:-3,544, 10846:+48, 10406:+2,157, 10287:+2,181, 11322:+2,351 |
| crashes / timeouts / malformed / parse failures | 0 / 0 / 0 / 0 |
| all episodes 720 steps, DONE/DONE | True |
| crops lost unwatered / fresh plantings unwatered / animals escaped / decay | 0 / 0 / 0 / 33 |
| duplicate assignments / duplicate actions / same-turn no-ops | 0 / 0 / 0 |
| hand PASS rate (mean) | 9.58% |
| ms/turn median / p95 max / p99 max / max | 16.45 / 134.79 / 688.31 / 24840.47 |
| formal criteria met (this set alone) | True |

## Holdout (seeds 20000–20499)

| metric | value |
|---|---|
| completed | 1000 / 1000 |
| wins / ties / losses | 976 / 0 / 24 |
| win rate | 97.60% |
| Wilson 95% lower | 96.45% |
| seat 0 (W/T/L) | 492 / 0 / 8 |
| seat 1 (W/T/L) | 484 / 0 / 16 |
| median margin | +7,444 |
| mean margin | +7,427.5 |
| min / max margin | -4,436 / +14,021 |
| candidate bank range | 34,416.0 – 50,932.0 |
| incumbent bank range | 31,477.0 – 45,141.0 |
| paired seeds +/0/− | 500 / 0 / 0 |
| median paired margin | +14,690 |
| worst paired seeds | 20419:+5,016, 20040:+5,046, 20064:+5,258, 20182:+5,314, 20398:+5,338 |
| crashes / timeouts / malformed / parse failures | 0 / 0 / 0 / 0 |
| all episodes 720 steps, DONE/DONE | True |
| crops lost unwatered / fresh plantings unwatered / animals escaped / decay | 0 / 0 / 0 / 8 |
| duplicate assignments / duplicate actions / same-turn no-ops | 0 / 0 / 0 |
| hand PASS rate (mean) | 9.61% |
| ms/turn median / p95 max / p99 max / max | 19.01 / 77.19 / 202.33 / 1783.36 |
| formal criteria met (this set alone) | True |

## Combined (4000 episodes)

| metric | value |
|---|---|
| completed | 4000 / 4000 |
| wins / ties / losses | 3924 / 0 / 76 |
| win rate | 98.10% |
| Wilson 95% lower | 97.63% |
| seat 0 (W/T/L) | 1966 / 0 / 34 |
| seat 1 (W/T/L) | 1958 / 0 / 42 |
| median margin | +7,426 |
| mean margin | +7,395.3 |
| min / max margin | -6,378 / +15,270 |
| candidate bank range | 33,709.0 – 51,124.0 |
| incumbent bank range | 30,470.0 – 45,141.0 |
| paired seeds +/0/− | 1999 / 0 / 1 |
| median paired margin | +14,668 |
| worst paired seeds | 10693:-3,544, 10846:+48, 10406:+2,157, 10287:+2,181, 11322:+2,351 |
| crashes / timeouts / malformed / parse failures | 0 / 0 / 0 / 0 |
| all episodes 720 steps, DONE/DONE | True |
| crops lost unwatered / fresh plantings unwatered / animals escaped / decay | 0 / 0 / 0 / 41 |
| duplicate assignments / duplicate actions / same-turn no-ops | 0 / 0 / 0 |
| hand PASS rate (mean) | 9.59% |
| ms/turn median / p95 max / p99 max / max | 16.68 / 134.79 / 688.31 / 24840.47 |
| formal criteria met (this set alone) | True |

## Premium product sales (combined)

```
{
 "STRAWBERRY": {
  "units_sold": 308524,
  "units_held_at_end": 0,
  "avg_price": 282.1,
  "min_price": 226,
  "floor_sales": 0
 },
 "MELON": {
  "units_sold": 692626,
  "units_held_at_end": 0,
  "avg_price": 143.0,
  "min_price": 1,
  "floor_sales": 35347
 },
 "MILK": {
  "units_sold": 56614,
  "units_held_at_end": 0,
  "avg_price": 299.2,
  "min_price": 217,
  "floor_sales": 0
 },
 "WOOL": {
  "units_sold": 28975,
  "units_held_at_end": 0,
  "avg_price": 245.1,
  "min_price": 236,
  "floor_sales": 0
 }
}
```

## Promotion criteria (combined)

| criterion | result |
|---|---|
| win rate > 53% | PASS |
| Wilson lower > 50% | PASS |
| median margin > 0 | PASS |
| zero crashes | PASS |
| zero timeouts | PASS |
| mandatory scenarios preserved | yes (pytest 408 passed; scenario suite in tests) |
| all 4000 episodes present | True |
| candidate integrity preserved | True |

## STRATEGY GATE VERDICT: PASS

## Runtime compliance (1 s actTimeout) — separate check

### Why a separate check
Gate `ms_max` is the slowest single `main.agent(obs)` call per episode (tools/harness.py, perf_counter,
4 workers on a loaded laptop). Kaggle's `actTimeout = 1` is soft: kaggle_environments 1.30.2 marks
TIMEOUT only when `duration - 1 > remainingOverageTime` (60 s bank), so gate `timeouts = 0` alone
does not prove every call < 1 s. Gate observation: 30 of 4000 episodes had ms_max >= 1000 ms
(26 stable, 4 holdout); max 24,840 ms (seed 11195 seat 1). The three largest (11195:0, 11195:1,
11196:1, each ~24.7 s) were neighbouring episodes on different workers, consistent with a host-wide pause.

### Controlled probe
- Tool: `benchmarks/results/m5_timing_probe.py` (offline; same env, same candidate, same incumbent,
  one process, sequential, per-call wall/CPU/GC timing with seat, step, rep, action).
- Conditions: after the gate stopped; AC power; no tournament/test workers; background ~1.3 cores
  (Chrome / VS Code / WindowServer); Python 3.11.16, kaggle_environments 1.30.2.
- Jobs: all 30 flagged episodes x 3 repetitions (incl. 10259:0, 10258:0, 11195:1) + 30 evenly spaced
  unflagged controls x 1 (`m5_timing_jobs.json`). Evidence: `m5_timing_probe_calls.jsonl` (sha256 `61d02fdabb18d1d58e1623002cc5e8fcc76fbd681dc6bfcc42351647a17a4af3`),
  summarised by `m5_timing_analyze.py`.
- Every replay reproduced the gate's terminal banks, statuses and 720 steps exactly (0 mismatches / 120).
- Probe defect (recorded, not hidden): in this run the per-call `seed` field captured Kaggle's
  configuration object (positional-argument capture in the wrapper); seat/step/rep/timings are
  correct and the analyzer takes the seed from the episode row that immediately follows each
  episode's calls. The probe was fixed afterwards (keyword-only capture); candidate unaffected.

| set | calls | p50 | p95 | p99 | max | >=500 ms | >=1000 ms | >=2000 ms |
|---|---|---|---|---|---|---|---|---|
| all | 86,280 | 6.72 | 9.46 | 11.75 | 271.61 | 0 | 0 | 0 |
| flagged (x3) | 64,710 | 6.77 | 9.52 | 12.28 | 271.61 | 0 | 0 | 0 |
| control | 21,570 | 6.53 | 9.26 | 10.45 | 168.29 | 0 | 0 | 0 |

Slowest call: seed 10040, seat 0, step 625, rep 2 — wall 271.6 ms, CPU 169.5 ms, GC 264.2 ms.
72 of the 79 calls >= 100 ms spent >= 50% of wall time in cyclic GC; max wall-minus-GC 143.1 ms.
Reproducibility: no flagged episode exceeded 272 ms in any repetition (e.g. 11195:1 gate 24,840 ms ->
65 / 12 / 37 ms; 10259:0 gate 11,222 ms -> 97 / 204 / 103 ms; 10258:0 gate 7,491 ms -> 140 / 87 / 148 ms).

### Interpretation
No controlled candidate invocation reached 1 s (or 500 ms); the >= 1 s gate outliers did not
reproduce and are documented as loaded-host observations (4 concurrent workers, battery, sleep/reboot
episodes during the run). Note for future work: the worst controlled calls are GC-dominated (~0.27 s),
leaving ~3.7x headroom under 1 s on this machine.

**RUNTIME COMPLIANCE: PASS**

## Execution history
Stable was interrupted by a ProcessPoolExecutor recycling deadlock (200 episodes), a reboot (520),
and completed via `m5_gate_driver.py` (same games/records, multiprocessing.Pool). Holdout was held back
until stable finished, then stopped by clamshell sleep (669) and a full disk (938); each time the
persisted records validated clean and the driver resumed without replaying completed episodes.
Full timeline: `m5_gate.log`. Integrity (runtime hash, candidate diff hash, tests/test_market.py hash
`72ff13c8…d1cb`) was verified before every launch and at completion.

## Final checks
pytest 408 passed; ruff check clean; ruff format --check clean; git diff --check clean;
`git diff 4db49e4 -- agents/baseline.py` empty; frozen M4 incumbent MANIFEST 14/14 OK and `agents/`
unchanged vs HEAD; git identity modolu <heritage143@gmail.com>.

# FINAL M5 VERDICT: PASS (strategy gate PASS + runtime compliance PASS)
