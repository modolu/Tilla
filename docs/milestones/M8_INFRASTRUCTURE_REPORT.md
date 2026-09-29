# Milestone 8 Infrastructure Report

> **M8 INFRASTRUCTURE: PASS.** The submission runtime and the validated M10 artifact are unchanged.

Milestone 8 (tournament/replay loop): a one-command paired, seat-swapped tournament and promotion-gate pipeline with resumable append-only results, locking, worker lifecycle, statistics, fail-closed gate evaluation and reports (`tools/tournament.py`; usage in README "Tournament and promotion gate").

## Source and integration

- Original prework: `parallel/m8-infra` at `1b505130117ac655b370e9f3d8622f80f2d0cddc` (commits `93f9c86`, `1b50513`), built on the Milestone 4 freeze `1b5b00f`.
- Files it changed: `tools/tournament.py` (rewritten, ~2,800 lines), `tests/test_tournament.py` (new), `README.md`. It never touched `main.py`, `kaggriculture_bot/`, `tools/harness.py` or the care-loss tests.
- Integration method: no merge or cherry-pick (the branch predates M5–M7 and M10). Main at `18ddbc4` was the base; the M8 files were brought over and the conflicts below resolved by hand.

| File | Classification | Resolution |
|---|---|---|
| `tools/tournament.py` | SAFE INFRASTRUCTURE + CONFLICT WITH CURRENT MAIN | M8 version adopted, with two additions: (1) the M6 care-checker correction in `care_loss_counters`, reusing `tools.harness._harvested_one_time_crop` so there is one source of truth (a one-time crop harvested on the day's last turn whose emptied tile gets the random end-of-day weed is `harvested_then_weed_spawn`, never `crops_lost_unwatered`); (2) main's Milestone 5-era helpers (`wilson_lower_bound`, `_play`, `run`, `gate_report`, `_premium`, `gate_passes`) appended unchanged as a compatibility section for the recorded M5–M7 gate tooling and existing tests. M8's CLI replaces the old single-command CLI. |
| `tests/test_tournament.py` | TESTS ONLY | Adopted; two assertions updated for current main (the champion is now the frozen M7 snapshot; `tools/harness.py` is now part of the runner's import closure/identity because the care checker is shared). One test added for the M6 correction in `care_loss_counters`. One order-dependent assertion fixed: `test_duplicate_results_in_a_file_are_rejected` expected the first written record to be seat 0, but with concurrent workers either seat can finish first (it failed intermittently, naming `stable:1:1`); it now reads the key from the duplicated line. Duplicate detection itself was correct in every run. |
| `README.md` | DOCS ONLY + CONFLICT | Main's short "Promotion gate" section (old CLI) replaced by M8's "Tournament and promotion gate" section plus a note that the M5-era helpers stay importable; all M6/M7 and M10 packaging text preserved. |
| `main.py`, `kaggriculture_bot/` | RUNTIME-SENSITIVE | Not touched by M8. |

The current `tools/harness.py` care checker (M6 correction) is preserved unchanged and is now also used by M8's counters.

## Checks

- pytest **641 passed**, 1 skipped (intended); includes 71 M8 tournament tests (stable across 6 consecutive runs), the M10 packaging tests and all earlier suites.
- ruff check, ruff format --check, git diff --check clean.
- `agents/incumbent_m4`–`_m7` manifests 14/14; `git diff 4db49e4 -- agents/baseline.py` empty; no opponent private-state access.
- Runtime hash before and after integration: `11e026d3aa3b6bd9272c91fcbc56a0a81238965709ffc3d2aa9c8483bc73dc9a`.

## Feature validation

Unit coverage (all pass in the integrated tree): resume plays only missing episodes; torn final line quarantined and replayed; SIGINT and restart; exclusive `flock` run lock; heartbeat never carries outcomes; dead runner reported stale; worker crash retried and recorded once; hung episode killed and left pending after max attempts; Wilson reference values; only completed pairs count; duplicates cannot pad pairs; gate boundaries (exactly 53% fails, Wilson exactly 50% fails, median must be > 0, 1,999 pairs fail, 2,000 can pass); weaker policies never pass; candidate failures fail; incumbent failures block promotion and never become candidate wins; harness errors/incomplete/duplicate results block; malformed lines are corruption; scenario evidence fails closed (missing/empty suite = MISSING_EVIDENCE); reports reproducible and independent of record order; holdout reveal logged.

Real-stack validation with the actual CLI (persistent gitignored run directories under `benchmarks/results/m8_smoke/`, candidate `agents.incumbent_m7.agent:agent`, incumbent `agents.incumbent_m6.agent:agent`, `--stable 70000:12`, 2 workers):

- **Paired seat swaps:** each of the 12 seeds played in both seats (12 per seat, 12 completed pairs). A mirror run (frozen M7 vs the accepted M7 `main:agent`, seeds 70100–70102) inverted margins exactly (pair sums 0, 0, 0).
- **Append-only resume:** an interrupted run was hard-killed (`kill -9`) at 8/24 records with no partial line; `status` then reported `STALE (runner died without a clean shutdown; rerun the command to resume)`; rerunning the same command kept the 8 records and appended the remaining 16. The resumed result set equals an uninterrupted reference run on every episode (same keys, banks, statuses, outcomes, final steps; 0 duplicates).
- **Locking:** a second runner on the same directory was refused immediately (exit 2, "locked by another runner (pid=… host=…)") and wrote nothing.
- **Heartbeat/status:** live status showed counts, in-flight jobs, throughput and ETA, and no outcomes.
- **Statistics:** recomputed independently from the raw records — 17/7/0, win 70.83%, Wilson lower 0.508323 (identical from M8's `wilson_interval` and the M5 `wilson_lower_bound`), median margin +611, seat 0 7-5, seat 1 10-2, paired 12/0/0 with median +1,347, `margin` field consistent — all equal to M8's report.
- **Report and fail-closed gate:** `report` wrote `REPORT.stable.md` / `report.stable.json`; verdict FAIL (exit 1) exactly as designed for a 12-pair smoke: stable eligibility (12 < 1,500 pairs), holdout not revealed, minimum pairs/episodes, and mandatory scenarios `MISSING_EVIDENCE`; statistical and integrity checks themselves passed. `local_time_budget` (max 362 ms under two concurrent runs) is an advisory, not a gating check, consistent with the architecture's 200 ms "hard local warning".

### Real M7-vs-M6 smoke

24 episodes / 12 pairs (seeds 70000–70011): 17 / 7 / 0, seat 0 7-5, seat 1 10-2, paired 12 / 0 / 0; 24/24 episodes `ok`, all 720 steps DONE/DONE, 0 candidate crashes, TIMEOUTs or invalid actions, 0 incumbent or harness failures, candidate `crops_lost_unwatered` 0. This is an infrastructure check, not a performance claim; M7 was not changed.

## Known limitation

Care-loss counters are recorded and reported per episode but are not a blocking gate check: the M8 gate follows `TILLA_STRATEGY.md` §19, which has no care criterion. The M6/M7 promotion gates added "0 true care losses" through their own checkers. Making it blocking in M8 is a policy change for a future decision.

## Submission artifact

- Locked artifact `benchmarks/results/submission/tilla_m7_submission.tar.gz` SHA-256 before and after M8: `78e8e22690c4c8b74f4426f918328ffb53ac91b7d1e689d8ed07fcf70b0ed17b` (read-only backup with a record file at `/Users/Apple/tilla_submission_backup/`).
- A temporary package built from the post-M8 tree is byte-identical: SHA-256 `78e8e22690c4c8b74f4426f918328ffb53ac91b7d1e689d8ed07fcf70b0ed17b`, manifest `6cfb5835f4a2e4322dfa5bf1d27b1d38fad2fe3f3464fe1dd2ce0e5d06613287`, 14/14 files identical to the accepted runtime, isolated `-I -S` import with 0 leaks, 0 dependency-audit problems.

## Verdict

**M8 INFRASTRUCTURE: PASS.**
