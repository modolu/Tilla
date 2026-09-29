# Milestone 10 Submission Report

> **Verdict: SUBMISSION READY.**

Final strategy: accepted **M7** (implementation `6ed9030860b779edfb32185c932d8bec09d4ca6c`, main at validation start `0065e02ccf541e574fb509bf607da0fc23cef62e`, frozen as `agents/incumbent_m7`).
Accepted M7 runtime hash (`shasum -a 256 kaggriculture_bot/*.py main.py | shasum -a 256`): `11e026d3aa3b6bd9272c91fcbc56a0a81238965709ffc3d2aa9c8483bc73dc9a`.

## Tooling

- Source: M10 prework `parallel/m10-hardening` `8c9da36` (built on the M5-era main). `tools/package_submission.py` and `tests/test_packaging.py` were brought over verbatim (neither file had changed on main since `a31864a`); README gained only the packaging section, all M6/M7 documentation preserved.
- Integrated as `628886f600334c054958f86b1008da913a8c3dab` (milestone-10: harden submission packaging). No runtime file (`main.py`, `kaggriculture_bot/`) changed; the runtime hash was verified unchanged immediately after integration and after every later step.

## Final artifact

| Item | Value |
|---|---|
| File | `benchmarks/results/submission/tilla_m7_submission.tar.gz` (gitignored; absolute path `/Users/Apple/tilla/benchmarks/results/submission/tilla_m7_submission.tar.gz`) |
| Archive SHA-256 | `78e8e22690c4c8b74f4426f918328ffb53ac91b7d1e689d8ed07fcf70b0ed17b` |
| Manifest SHA-256 | `6cfb5835f4a2e4322dfa5bf1d27b1d38fad2fe3f3464fe1dd2ce0e5d06613287` |
| Size | 46,216 bytes compressed; 168,285 bytes uncompressed |
| Files | 14: `main.py` + 13 `kaggriculture_bot/*.py` (including `__init__.py`) |
| Command | `python -m tools.package_submission benchmarks/results/submission/a/tilla_m7_submission.tar.gz` (and `/b/`) |

| Packaged file | Bytes | SHA-256 |
|---|---:|---|
| `kaggriculture_bot/__init__.py` | 182 | `e46b22bcfef33050d44dfa7741a315925272e8e41e3ff849f4fbd91788e00ecd` |
| `kaggriculture_bot/actions.py` | 2,275 | `84dc085c15b1a94bdb593b11befa9c6ec6a663b698ff69a8a1577033b4e12ccf` |
| `kaggriculture_bot/constants.py` | 11,682 | `d7bbbcb07a9158f489bbf6526f2cc81d5cb3571b7fb1617b51f4118121c384c6` |
| `kaggriculture_bot/economy.py` | 48,836 | `64897b215fbceb7648b8e18b8a450c88547f53e5e8c72ee2959e510f0fb4b27a` |
| `kaggriculture_bot/features.py` | 14,783 | `a0c97aa6ea202af1a95afddee74824328efc440cd1f0e661e367c43394c550a0` |
| `kaggriculture_bot/models.py` | 17,734 | `b0deb138cc77f5243768765cb3f55012f3ad0efae8319d1d394ade17297ae3a9` |
| `kaggriculture_bot/opponent.py` | 15,856 | `653a5580926f9b72f06d98a173e4a58496570b1b03ba371d27a7557bb7c3da3e` |
| `kaggriculture_bot/parser.py` | 10,484 | `2f10340b6fdc227751157e692264aa38a7feaffeb56529bfdb8221646d04c816` |
| `kaggriculture_bot/pathing.py` | 3,606 | `cdf9c98daa8e48dcafccbd45b3bec850e23e34c4e6e4e6300c37113902d55000` |
| `kaggriculture_bot/runtime.py` | 1,913 | `63f75aa57d7a7aaa7cec8a9c1b05859d7e0b60a03e873d191e0f749d2db1b5d3` |
| `kaggriculture_bot/strategy.py` | 21,978 | `2c1024cf42fbdb77b608d6f40512f7c2790363ca9debb8bf067ddbc74dee2a85` |
| `kaggriculture_bot/tasks.py` | 14,344 | `92368b713dfce203bc8938cd8786dd7a564088117885a72093c7452938f0aac4` |
| `kaggriculture_bot/validator.py` | 3,174 | `7f7935158466742060778fcb94cf9adb3c9d64c3916e8e5c521a02ff19af97de` |
| `main.py` | 1,438 | `ce0c71b2c48b6b91bbf064f6e77c7df032c27cae2e72fd91f0dd417386f70b7e` |

**Contract:** archive root is exactly `main.py` + `kaggriculture_bot/*.py`; regular files only; no tests, tools, benchmarks, agents, docs, `.git`, `.venv`, caches, credentials, environment files, symlinks/hardlinks, subdirectories, unsafe paths or case collisions (all enforced by the packager and covered by `tests/test_packaging.py`).

**Deterministic build:** two independent builds (`a/`, `b/`) are byte-identical (same archive SHA-256 above) with identical manifests.

## Clean isolated import

Packager validation: archive extracted into a fresh temporary directory and imported by `python -I -S -B` (isolated, no `site`, no bytecode): Python 3.11.16; `main`, `kaggriculture_bot` and `kaggriculture_bot.strategy` imported; all 14 runtime modules resolved inside the extraction; **external runtime-module leaks: 0**; smoke `agent()` call on `obs_midgame_p1_populated.json` returned a legal action.

## Packaged runtime equals the accepted M7

The archive was extracted to a fresh directory; the runtime hash computed there with the same command is **`11e026d3aa3b6bd9272c91fcbc56a0a81238965709ffc3d2aa9c8483bc73dc9a`**, and every one of the 14 files is byte-identical to accepted commit `6ed9030` (the package directory holds exactly the 13 modules of that commit).

## Kaggriculture loader verification

- The installed `kaggle_environments==1.30.2` **does not load `.tar.gz` archives directly** (no archive handling in its agent loader). The nearest faithful test is its real path loader: `build_agent(path)` → `read_file` → `get_last_callable`, which `exec`s `main.py` with `dirname(main.py)` **appended** to `sys.path` and takes the last callable as the agent. Because the directory is appended, any earlier `kaggriculture_bot` on `sys.path` would win; the test therefore removed the repository and the editable install from import resolution.
- Test process: `python -I -S -B`, working directory = the extracted submission; `sys.path` = the Python 3.11 standard library + the `site-packages` **directory** (added by hand, so its `.pth` files — including the editable finder — never run) + an opponent directory containing only copies of the frozen incumbents and the offline care checker (no `kaggriculture_bot`); the packaged agent passed to `env.run` as the path `<extraction>/main.py`.
- Result: the agent's code object comes from `<extraction>/main.py`; all 13 `kaggriculture_bot` modules load from `<extraction>/kaggriculture_bot/`; Tilla modules outside the submission: none; repository source modules loaded: 0; editable finder loaded: no; `main` is not a module (the loader `exec`s it, exactly as Kaggle does).

## Packaged games (all through the real loader, packaged agent only)

| Check | Games | Result |
|---|---|---|
| Smoke vs frozen M7 (60100 seat 0), two independent processes | 2 | 720 steps, DONE/DONE, 0 ERROR / INVALID / TIMEOUT / malformed, 0 care losses; identical bank both runs (47,265 vs 51,650) |
| Self-match vs frozen M7 (60100, 60220, 60566, both seats) | 6 | every bank identical to the repository `main.agent` reference; paired margin 0 on all 3 seeds (exact mirror); clean integrity |
| Baseline sanity (60100–60102, both seats) | 6 | 6/6 wins (≈ +56k each), every bank identical to the repository reference; clean integrity; 0 care losses (diagnostic `crops_lost_decay` 1–2 units in 3 games). The baseline is itself built on `kaggriculture_bot`, so it used the packaged modules. |

## Packaged timing (controlled; real loader durations)

AC connected, Low Power Mode off, single process, no tournament workers, browsers idle; durations are those the Kaggle loader itself records per call (the first call includes `exec` of `main.py` and all imports). Episodes fixed before the run: 60130, 60340, 60533, both seats, vs frozen M7.

| Calls | p50 | p95 | p99 | Max | ≥ 500 ms | ≥ 1000 ms | ≥ 2000 ms |
|---|---|---|---|---|---|---|---|
| 4,314 | 7.27 ms | 9.84 ms | 11.17 ms | 177.84 ms | 0 | 0 | 0 |

First call of the process (loader exec + imports): 35.7 ms. Replay fidelity: 6/6 episodes identical to the repository reference with clean integrity.

## Dependency / security audit

`audit_runtime_sources` over the 14 packaged files: **0 problems** (Python 3.11 syntax; standard library + own package only; no pytest, `kaggle_environments`, `tools`, `tests`, `benchmarks` or `agents`; no network, subprocess or multiprocessing; no `eval`/`exec`/`compile`/`__import__`/`open`; no `os.environ`/`os.getenv`/`os.system`; no local absolute paths).

Every import in the package: `__future__`, `collections`, `collections.abc`, `dataclasses`, `enum`, `math` (standard library); `kaggriculture_bot` and `kaggriculture_bot.{actions, constants, features, models, opponent, parser, pathing, runtime, strategy, tasks, validator}` (own package).

## Tests and checks

pytest 570 passed, 1 skipped (intended: the day-29 official fixture in the days-0–28 equivalence test), including 101 packaging/submission tests; ruff check clean; ruff format --check clean; git diff --check clean; `agents/incumbent_m4`–`_m7` manifests 14/14; `git diff 4db49e4 -- agents/baseline.py` empty; no opponent private state read.

Evidence (gitignored): `benchmarks/results/submission/` — `a/`, `b/` build reports, `tilla_m7_submission.tar.gz`, `loader_{smoke_run1,smoke_run2,selfmatch,baseline,timing}.json`, `repo_reference.json`.

## Verdict

**SUBMISSION READY** — submit `tilla_m7_submission.tar.gz` (SHA-256 `78e8e22690c4c8b74f4426f918328ffb53ac91b7d1e689d8ed07fcf70b0ed17b`).
