"""Paired, seeded candidate-vs-incumbent tournament and promotion gate (Milestone 8).

Offline only: never imported by the submitted runtime.

    python -m tools.tournament gate   --run-dir DIR --stable 100000:1500 --holdout-epoch 0 \
                                      --holdout-count 500 --scenario-results FILE
    python -m tools.tournament run    --run-dir DIR --stable FIRST:COUNT [--holdout FIRST:COUNT]
    python -m tools.tournament status --run-dir DIR
    python -m tools.tournament report --run-dir DIR [--reveal-holdout] [--scenario-results FILE]

Terminology (used consistently in code, records and reports):

* episode         - one official 720-step game with the candidate in one seat;
* paired seed     - a planned seed, played as two episodes with the seats swapped
                    (``--stable 100000:1500`` is 1,500 paired seeds = 3,000 episodes);
* completed pair  - a paired seed with exactly one valid (``ok``) episode for candidate seat 0
                    and one for seat 1. Only completed pairs count as evidence.

Formal flow: all stable episodes are played first; the complete stable partition must pass
its own eligibility gate (``stable_gate.json``); only then is the holdout played or revealed;
the final verdict uses the combined stable + holdout evidence (Tilla: 2,000 completed pairs =
4,000 episodes, of which 1,500 stable and 500 holdout pairs). A strong holdout never rescues a
stable partition that failed its gate.

``--candidate`` / ``--incumbent`` take an agent entrypoint: ``module:attr`` importable from the
repository root (default candidate ``main:agent``, default incumbent ``agents.incumbent:agent``)
or a built-in agent name (``pass``, ``random``, ``starter``).

Run directory (persistent; refused under temporary directories unless ``--allow-temp``)::

    manifest.json          immutable run identity: config (incl. policy) and content hashes
    run.lock               flock held by the one runner process allowed to write this run
    heartbeat.json         progress and in-flight episodes (never outcomes), rewritten atomically
    results-<part>.jsonl   append-only episode records, one file per seed partition
    stable_gate.json       the stable eligibility decision that gates the holdout
    events.jsonl           append-only infrastructure events (workers, replays, interrupts)
    quarantine.jsonl       partial trailing lines removed when a crashed run is resumed
    replays/               optional replay JSON for losses/problem episodes
    reports/               report.<scope>.json and REPORT.<scope>.md

Ownership: only the parent runner process writes ``results-*``, ``events``, ``heartbeat``,
``stable_gate`` and ``quarantine``; it holds ``run.lock`` (``fcntl.flock``, released by the
kernel if the process dies) for its whole lifetime, so two runners can never write one run.
Workers are plain ``spawn`` processes owned by the parent (no executor pool): each plays one
episode at a time, returns the result over its own pipe and is replaced after
``--max-episodes-per-worker`` episodes, or when it dies, hangs past ``--episode-timeout`` or
fails to start. An episode lost that way is retried; an episode that finished with a failure
that is not the candidate's (incumbent failure, harness error, incomplete episode) is
replayed up to ``--max-attempts`` times and, if still failing, recorded so that it blocks
promotion. An episode is written exactly once. Each record is appended with one ``write`` +
``fsync``; a crash can at most leave a partial last line, which readers ignore and the next
runner quarantines before resuming.

Timing semantics (``perf_counter``/``monotonic``; on macOS neither advances during sleep):

* agent call     - one ``agent(obs)`` call as timed by kaggle-environments itself, i.e. the
                   value it charges against ``actTimeout`` and the ``remainingOverageTime`` bank;
* turn           - one whole environment step: both agents' calls plus the interpreter;
* episode        - ``env.run`` for one full episode (``env_make_s`` reported separately);
* post-episode   - the worker's episode analysis and optional replay write after ``env.run``;
* worker startup - worker process start until the agents are imported and it is ready;
* scheduling/IPC overhead - the parent's dispatch-to-result time minus the worker's own
                   measured make + episode + post-episode time.

Timeouts are only the environment's enforced ``TIMEOUT`` status (overage bank exhausted).
Calls longer than ``actTimeout`` that the overage bank absorbed are reported separately as
``calls_over_act_timeout``, an advisory that never changes the verdict.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import errno
import fcntl
import hashlib
import importlib
import importlib.metadata
import importlib.util
import io
import json
import math
import multiprocessing as mp
import multiprocessing.connection
import os
import platform
import signal
import socket
import statistics
import sys
import tempfile
import time
import traceback
from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from kaggriculture_bot.constants import (
    MAX_MARKET_ORDERS_PER_TURN,
    PLAYER_COUNT,
    SEASON_DAYS,
    TURNS_PER_DAY,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1
EPISODE_STEPS = TURNS_PER_DAY * SEASON_DAYS
ENVIRONMENT_NAME = "kaggriculture"
DEFAULT_CANDIDATE = "main:agent"
DEFAULT_INCUMBENT = "agents.incumbent:agent"
DEFAULT_EPISODE_FN = "tools.tournament:play_episode"
BUILTIN_AGENTS = ("pass", "random", "starter")
NONDETERMINISTIC_BUILTINS = ("random",)  # kaggriculture.random_agent uses an unseeded Random()
SCENARIO_DEFINITIONS = REPO_ROOT / "benchmarks" / "scenarios.json"
DEFAULT_LEDGER = REPO_ROOT / "benchmarks" / "results" / "holdout_ledger.jsonl"

STABLE, HOLDOUT = "stable", "holdout"
PARTITION_ORDER = (STABLE, HOLDOUT)
# Standard rotating holdout blocks: epoch e covers HOLDOUT_BASE + e * HOLDOUT_STRIDE onwards.
HOLDOUT_BASE = 900_000
HOLDOUT_STRIDE = 10_000

AGENT_FAILURE_STATUSES = ("ERROR", "INVALID", "TIMEOUT")
# Advisory local decision-time budget (TILLA_ARCHITECTURE.md §8); not a gate criterion.
TIME_BUDGET_MS = {"p50": 20.0, "p99": 100.0, "warn": 200.0}
# An episode whose wall-clock duration exceeds its monotonic duration by this much ran across a
# host sleep. Its timings stay valid (monotonic clocks pause) but the episode is flagged.
SUSPEND_SLACK_S = 5.0
TEMP_ROOTS = tuple(
    sorted(
        {
            os.path.realpath(p)
            for p in ("/tmp", "/private/tmp", "/var/tmp", "/var/folders", tempfile.gettempdir())
        }
    )
)


class TournamentError(Exception):
    """A refusal that must stop the runner or the report."""


class RunLockedError(TournamentError):
    pass


class ResultsCorruptError(TournamentError):
    pass


class DuplicateResultError(ResultsCorruptError):
    pass


class IdentityMismatchError(TournamentError):
    pass


class ConfigMismatchError(TournamentError):
    pass


# ============================================================================================
# Seeds, partitions, jobs
# ============================================================================================


@dataclass(frozen=True)
class Partition:
    """A contiguous deterministic seed range with a role (``stable`` or ``holdout``)."""

    name: str
    first: int
    count: int

    @property
    def seeds(self) -> range:
        return range(self.first, self.first + self.count)

    def label(self) -> str:
        return f"{self.first}:{self.count}"


def parse_seed_range(text: str) -> tuple[int, int]:
    """``"FIRST:COUNT"`` -> ``(first, count)``."""
    try:
        first, count = (int(part) for part in text.split(":"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected FIRST:COUNT, got {text!r}") from exc
    if first < 0 or count <= 0:
        raise argparse.ArgumentTypeError(f"seed range needs FIRST >= 0 and COUNT > 0: {text!r}")
    return first, count


def holdout_partition(epoch: int, count: int) -> Partition:
    """The standard rotating holdout block for ``epoch``; blocks of different epochs never
    overlap while ``count <= HOLDOUT_STRIDE``."""
    if epoch < 0 or not 0 < count <= HOLDOUT_STRIDE:
        raise ValueError("holdout epoch must be >= 0 and 0 < count <= HOLDOUT_STRIDE")
    return Partition(HOLDOUT, HOLDOUT_BASE + epoch * HOLDOUT_STRIDE, count)


def validate_partitions(partitions: Iterable[Partition]) -> tuple[Partition, ...]:
    parts = tuple(partitions)
    names = [p.name for p in parts]
    if STABLE not in names:
        raise ValueError("a stable partition is required")
    if len(set(names)) != len(names) or not set(names) <= set(PARTITION_ORDER):
        raise ValueError(f"partitions must be distinct names from {PARTITION_ORDER}: {names}")
    for p in parts:
        if p.first < 0 or p.count <= 0:
            raise ValueError(f"invalid seed range for {p.name}: {p.label()}")
    ordered = sorted(parts, key=lambda p: PARTITION_ORDER.index(p.name))
    for a, b in zip(ordered, ordered[1:], strict=False):
        if a.first < b.first + b.count and b.first < a.first + a.count:
            raise ValueError(f"{a.name} and {b.name} seed ranges overlap")
    return tuple(ordered)


@dataclass(frozen=True)
class Job:
    partition: str
    seed: int
    seat: int  # the candidate's seat

    @property
    def key(self) -> str:
        return job_key(self.partition, self.seed, self.seat)


def job_key(partition: str, seed: int, seat: int) -> str:
    return f"{partition}:{seed}:{seat}"


def plan_jobs(partitions: Iterable[Partition]) -> list[Job]:
    """Every (partition, seed, seat) episode in deterministic dispatch order: stable before
    holdout, seeds ascending, both seats of a seed adjacent."""
    return [
        Job(p.name, seed, seat)
        for p in validate_partitions(partitions)
        for seed in p.seeds
        for seat in range(PLAYER_COUNT)
    ]


# ============================================================================================
# Identity: agents, environment, runner
# ============================================================================================


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_digest(obj) -> str:
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


def _module_file(root: Path, dotted: str) -> Path | None:
    base = root.joinpath(*dotted.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def import_closure(module: str, root: Path = REPO_ROOT) -> list[str]:
    """Repository-local source files reachable from ``module`` through its static imports
    (including parent package ``__init__`` files), as sorted root-relative POSIX paths.

    Resolution is by file layout under ``root`` only; nothing is imported or executed, and
    imports that do not resolve under ``root`` (stdlib, third-party) are ignored.
    """
    root = Path(root).resolve()
    seen: dict[str, Path] = {}
    queue = deque([module])
    while queue:
        name = queue.popleft()
        if name in seen:
            continue
        path = _module_file(root, name)
        if path is None:
            continue
        seen[name] = path
        parts = name.split(".")
        queue.extend(".".join(parts[:i]) for i in range(1, len(parts)))
        package = name if path.name == "__init__.py" else ".".join(parts[:-1])
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                queue.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    anchor = package.split(".") if package else []
                    anchor = anchor[: len(anchor) - (node.level - 1)]
                    base = ".".join([*anchor, *([node.module] if node.module else [])])
                else:
                    base = node.module or ""
                if base:
                    queue.append(base)
                    queue.extend(f"{base}.{alias.name}" for alias in node.names)
    return sorted(p.relative_to(root).as_posix() for p in seen.values())


def parse_agent_spec(spec: str) -> tuple[str, str | None]:
    """``"module:attr"`` -> ``(module, attr)``; a built-in name -> ``(name, None)``."""
    if spec in BUILTIN_AGENTS:
        return spec, None
    module, sep, attr = spec.partition(":")
    if not sep or not module or not attr.isidentifier():
        raise ValueError(f"agent spec must be 'module:attr' or one of {BUILTIN_AGENTS}: {spec!r}")
    return module, attr


def agent_identity(spec: str, root: Path = REPO_ROOT) -> dict:
    """Content identity of an agent entrypoint: the hash of every repository source file its
    entry module statically imports. Equal digests mean byte-identical agent code."""
    module, attr = parse_agent_spec(spec)
    if attr is None:
        ident = {
            "spec": spec,
            "kind": "builtin",
            "deterministic": spec not in NONDETERMINISTIC_BUILTINS,
            "environment": environment_identity()["digest"],
        }
    else:
        if _module_file(Path(root).resolve(), module) is None:
            raise ValueError(f"agent module {module!r} not found under {root}")
        ident = {
            "spec": spec,
            "kind": "python",
            "deterministic": True,
            "files": {rel: sha256_file(Path(root) / rel) for rel in import_closure(module, root)},
        }
    ident["digest"] = canonical_digest(ident)
    return ident


def environment_identity() -> dict:
    """kaggle-environments version plus hashes of the Kaggriculture source and config,
    resolved without importing kaggle_environments (its import is slow and noisy)."""
    ident: dict = {"python": platform.python_version(), "kaggle_environments": None}
    spec = importlib.util.find_spec("kaggle_environments")
    if spec is not None and spec.submodule_search_locations:
        env_dir = Path(next(iter(spec.submodule_search_locations))) / "envs" / ENVIRONMENT_NAME
        ident["kaggle_environments"] = importlib.metadata.version("kaggle-environments")
        for name in (f"{ENVIRONMENT_NAME}.py", f"{ENVIRONMENT_NAME}.json"):
            path = env_dir / name
            ident[name] = sha256_file(path) if path.is_file() else None
    ident["digest"] = canonical_digest(ident)
    return ident


def runner_identity(episode_fn: str, root: Path = REPO_ROOT) -> dict:
    """Hash of the runner's own sources plus the episode function's import closure."""
    ident = {
        "runner_files": {
            rel: sha256_file(REPO_ROOT / rel) for rel in import_closure("tools.tournament")
        },
        "episode_fn": episode_fn,
        "episode_fn_files": {
            rel: sha256_file(Path(root) / rel)
            for rel in import_closure(episode_fn.partition(":")[0], root)
        },
    }
    ident["digest"] = canonical_digest(ident)
    return ident


# ============================================================================================
# Run configuration and manifest
# ============================================================================================


@dataclass(frozen=True)
class RunConfig:
    """Everything that determines which episodes are played, by whom and how. Frozen per run."""

    partitions: tuple[Partition, ...]
    candidate: str = DEFAULT_CANDIDATE
    incumbent: str = DEFAULT_INCUMBENT
    episode_steps: int = EPISODE_STEPS
    episode_fn: str = DEFAULT_EPISODE_FN
    # Recorded in the manifest: the runner applies it to the stable eligibility gate before
    # any holdout episode is played, so it cannot change once a run has started.
    policy: PromotionPolicy = field(default_factory=lambda: TILLA_PROMOTION_POLICY)

    def as_dict(self) -> dict:
        return {
            "candidate": self.candidate,
            "incumbent": self.incumbent,
            "partitions": {p.name: {"first": p.first, "count": p.count} for p in self.partitions},
            "episode_steps": self.episode_steps,
            "environment": ENVIRONMENT_NAME,
            "episode_fn": self.episode_fn,
            "policy": self.policy.as_dict(),
        }


def build_identity(config: RunConfig, root: Path = REPO_ROOT) -> dict:
    return {
        "candidate": agent_identity(config.candidate, root),
        "incumbent": agent_identity(config.incumbent, root),
        "environment": environment_identity(),
        "runner": runner_identity(config.episode_fn, root),
    }


def build_manifest(config: RunConfig, root: Path = REPO_ROOT) -> dict:
    config_dict = config.as_dict()
    return {
        "schema": SCHEMA_VERSION,
        "config": config_dict,
        "config_digest": canonical_digest(config_dict),
        "identity": build_identity(config, root),
        "created_at": _utc_now(),
        "created_on": socket.gethostname(),
    }


def manifest_partitions(manifest: dict) -> tuple[Partition, ...]:
    return validate_partitions(
        Partition(name, spec["first"], spec["count"])
        for name, spec in manifest["config"]["partitions"].items()
    )


def identity_differences(stored: dict, current: dict) -> list[str]:
    diffs = []
    for role in ("candidate", "incumbent", "environment", "runner"):
        before, after = stored.get(role, {}), current[role]
        if before.get("digest") == after["digest"]:
            continue
        files = ("files", "runner_files", "episode_fn_files")
        changed = sorted(
            {
                name
                for key in files
                for name in set(before.get(key, {})) | set(after.get(key, {}))
                if before.get(key, {}).get(name) != after.get(key, {}).get(name)
            }
        )
        diffs.append(f"{role} changed" + (f" ({', '.join(changed)})" if changed else ""))
    return diffs


def ensure_manifest(run_dir: Path, config: RunConfig, root: Path = REPO_ROOT) -> dict:
    """Create the run manifest, or verify that ``config`` and the current code match it."""
    path = Path(run_dir) / "manifest.json"
    if not path.exists():
        manifest = build_manifest(config, root)
        atomic_write_text(path, json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        return manifest
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA_VERSION:
        raise ConfigMismatchError(f"manifest schema {manifest.get('schema')} != {SCHEMA_VERSION}")
    if manifest["config"] != config.as_dict():
        raise ConfigMismatchError(
            "run config differs from the existing manifest; use a new --run-dir.\n"
            f"  manifest: {canonical_json(manifest['config'])}\n"
            f"  current:  {canonical_json(config.as_dict())}"
        )
    diffs = identity_differences(manifest["identity"], build_identity(config, root))
    if diffs:
        raise IdentityMismatchError(
            "code under evaluation changed since this run started, results cannot be mixed: "
            + "; ".join(diffs)
        )
    return manifest


def load_manifest(run_dir: Path) -> dict:
    path = Path(run_dir) / "manifest.json"
    if not path.exists():
        raise TournamentError(f"no manifest.json in {run_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


# ============================================================================================
# Crash-safe storage
# ============================================================================================


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` so readers see either the old or the new file, never a mixture."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def append_line(path: Path, obj) -> None:
    """Append one JSON line with a single ``write`` and an ``fsync``: a crash can leave at
    most a partial final line, which ``read_jsonl`` detects."""
    data = (canonical_json(obj) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass
class ReadResult:
    records: list[dict]
    partial_tail: bytes = b""  # bytes after the last newline: an interrupted append
    body_sha256: str = sha256_bytes(b"")


def read_jsonl(path: Path) -> ReadResult:
    """Parse an append-only JSONL file. A trailing fragment without a newline is an
    interrupted append and is returned separately; a complete line that is not a JSON object
    is corruption and raises ``ResultsCorruptError``."""
    path = Path(path)
    if not path.exists():
        return ReadResult([])
    data = path.read_bytes()
    cut = data.rfind(b"\n") + 1
    body, tail = data[:cut], data[cut:]
    records = []
    for lineno, raw in enumerate(body.split(b"\n")[:-1], start=1):
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ResultsCorruptError(f"{path.name}:{lineno}: malformed JSON line ({exc})") from exc
        if not isinstance(obj, dict):
            raise ResultsCorruptError(f"{path.name}:{lineno}: line is not a JSON object")
        records.append(obj)
    return ReadResult(records, tail, sha256_bytes(body))


def repair_partial_tail(path: Path, quarantine: Path) -> bytes:
    """Writer-side recovery: move an interrupted final fragment to ``quarantine`` and
    truncate ``path`` to its last complete line. Only call while holding the run lock."""
    path = Path(path)
    if not path.exists():
        return b""
    data = path.read_bytes()
    cut = data.rfind(b"\n") + 1
    tail = data[cut:]
    if tail:
        append_line(
            quarantine,
            {"file": path.name, "offset": cut, "fragment": tail.decode("utf-8", "replace")},
        )
        with open(path, "r+b") as f:
            f.truncate(cut)
            f.flush()
            os.fsync(f.fileno())
    return tail


REQUIRED_RECORD_FIELDS = {
    "schema": int,
    "key": str,
    "partition": str,
    "seed": int,
    "seat": int,
    "candidate_id": str,
    "incumbent_id": str,
    "status": str,
    "terminal_valid": bool,
}


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def record_problems(record: dict, manifest: dict) -> list[str]:
    """Reasons ``record`` does not belong to the run described by ``manifest``."""
    problems = []
    for name, kind in REQUIRED_RECORD_FIELDS.items():
        value = record.get(name)
        if not (_is_int(value) if kind is int else isinstance(value, kind)):
            problems.append(f"field {name!r} missing or not {kind.__name__}")
    if problems:
        return problems
    if record["schema"] != SCHEMA_VERSION:
        problems.append(f"schema {record['schema']} != {SCHEMA_VERSION}")
    if record["key"] != job_key(record["partition"], record["seed"], record["seat"]):
        problems.append("key does not match partition/seed/seat")
    part = {p.name: p for p in manifest_partitions(manifest)}.get(record["partition"])
    if part is None:
        problems.append(f"unknown partition {record['partition']!r}")
    elif record["seed"] not in part.seeds:
        problems.append(f"seed {record['seed']} outside {part.name} range {part.label()}")
    if record["seat"] not in range(PLAYER_COUNT):
        problems.append(f"seat {record['seat']} invalid")
    identity = manifest["identity"]
    if record["candidate_id"] != identity["candidate"]["digest"]:
        problems.append("candidate identity mismatch")
    if record["incumbent_id"] != identity["incumbent"]["digest"]:
        problems.append("incumbent identity mismatch")
    if record["status"] == "ok":
        banks = [record.get(k) for k in ("candidate_bank", "incumbent_bank", "margin")]
        if not all(_is_int(v) for v in banks):
            problems.append("ok record has non-integer banks/margin")
        elif banks[2] != banks[0] - banks[1]:
            problems.append("margin != candidate_bank - incumbent_bank")
        elif record.get("outcome") != outcome_of(banks[2]):
            problems.append("outcome inconsistent with margin")
        if record["terminal_valid"] is not True:
            problems.append("ok record is not terminal-valid")
    return problems


def results_path(run_dir: Path, partition: str) -> Path:
    return Path(run_dir) / f"results-{partition}.jsonl"


@dataclass
class LoadedResults:
    records: list[dict]
    notes: list[str] = field(default_factory=list)
    files: dict[str, dict] = field(default_factory=dict)


def load_results(run_dir: Path, manifest: dict, partitions: Iterable[str]) -> LoadedResults:
    """Read and validate the records of ``partitions`` only; other partitions' files are never
    opened. Raises on corruption, duplicates and identity mismatches."""
    loaded = LoadedResults([])
    seen: dict[str, str] = {}
    for name in partitions:
        path = results_path(run_dir, name)
        result = read_jsonl(path)
        loaded.files[name] = {
            "file": path.name,
            "records": len(result.records),
            "sha256": result.body_sha256,
        }
        if result.partial_tail:
            loaded.notes.append(
                f"{path.name}: ignored a {len(result.partial_tail)}-byte partial final line "
                "(interrupted append, or a runner still writing)"
            )
        for lineno, record in enumerate(result.records, start=1):
            where = f"{path.name}:{lineno}"
            problems = record_problems(record, manifest)
            if not problems and record["partition"] != name:
                problems.append(f"record of partition {record['partition']!r}")
            if problems:
                mismatch = any("identity mismatch" in p for p in problems)
                kind = IdentityMismatchError if mismatch else ResultsCorruptError
                raise kind(f"{where}: {'; '.join(problems)}")
            if record["key"] in seen:
                raise DuplicateResultError(
                    f"{where}: duplicate result for {record['key']} "
                    f"(first at {seen[record['key']]})"
                )
            seen[record["key"]] = where
            loaded.records.append(record)
    return loaded


class RunLock:
    """Exclusive, non-blocking ``flock`` on ``<run_dir>/run.lock``.

    The kernel drops the lock whenever the holder exits (crash, kill, reboot), so a leftover
    lock file never blocks a restart, while a live runner always blocks a second one.
    """

    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / "run.lock"
        self._fd: int | None = None

    def acquire(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                holder = self.path.read_text(encoding="utf-8", errors="replace").strip()
                raise RunLockedError(
                    f"{self.path.parent} is locked by another runner ({holder or 'unknown'})"
                ) from exc
            raise
        os.ftruncate(fd, 0)
        os.write(fd, f"pid={os.getpid()} host={socket.gethostname()} since={_utc_now()}".encode())
        os.fsync(fd)
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is not None:
            with contextlib.suppress(OSError):
                os.ftruncate(self._fd, 0)
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> RunLock:
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


def is_locked(run_dir: Path) -> bool:
    """True when a live runner holds the run lock (the probe acquires and releases at once)."""
    try:
        RunLock(run_dir).acquire().release()
    except RunLockedError:
        return True
    return False


def check_persistent_dir(run_dir: Path, allow_temp: bool = False) -> None:
    real = os.path.realpath(run_dir)
    if not allow_temp and any(real == t or real.startswith(t + os.sep) for t in TEMP_ROOTS):
        raise TournamentError(
            f"{run_dir} is under a temporary directory that may not survive a reboot; use a "
            "persistent location such as benchmarks/results/<run-name>"
        )


# ============================================================================================
# Playing one episode (inside a worker process)
# ============================================================================================


def _quiet_import(name: str):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return importlib.import_module(name)


def resolve_agent(spec: str):
    """A built-in name stays a string (the environment resolves it); ``module:attr`` is
    imported from the repository."""
    module, attr = parse_agent_spec(spec)
    if attr is None:
        return spec
    agent = getattr(importlib.import_module(module), attr)
    if not callable(agent):
        raise TypeError(f"{spec} is not callable")
    return agent


def resolve_callable(spec: str) -> Callable:
    module, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module), attr)


def outcome_of(margin: int) -> str:
    return "win" if margin > 0 else "loss" if margin < 0 else "tie"


def _as_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


def shape_violation(action, hand_count: int) -> str | None:
    """Structural check of one emitted action against the official action shape
    (TILLA_RULES.md §6): one farmer action, one action per hired hand, at most the
    market-order cap. The legality of individual ops is not judged here."""
    if not isinstance(action, dict):
        return "action is not a dict"

    def unit_ok(a) -> bool:
        return isinstance(a, list) and bool(a) and isinstance(a[0], str)

    if not unit_ok(action.get("farmer")):
        return "farmer action malformed"
    hands = action.get("hands")
    if not isinstance(hands, list) or len(hands) != hand_count:
        return f"expected {hand_count} hand actions"
    if not all(unit_ok(h) for h in hands):
        return "hand action malformed"
    market = action.get("market")
    if not isinstance(market, list) or len(market) > MAX_MARKET_ORDERS_PER_TURN:
        return "market list malformed or over the order cap"
    if not all(unit_ok(order) for order in market):
        return "market order malformed"
    return None


def nearest_rank(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    return sorted_values[min(len(sorted_values), max(1, math.ceil(q * len(sorted_values)))) - 1]


# Log-spaced duration histogram: bucket i holds (edge(i - 1), edge(i)] with
# edge(i) = HIST_BASE_MS * 10 ** (i / HIST_BINS_PER_DECADE), about 12% resolution.
HIST_BASE_MS = 0.01
HIST_BINS_PER_DECADE = 20


def hist_bucket(ms: float) -> int:
    if ms <= HIST_BASE_MS:
        return 0
    return math.ceil(HIST_BINS_PER_DECADE * math.log10(ms / HIST_BASE_MS) - 1e-9)


def hist_edge(bucket: int) -> float:
    return HIST_BASE_MS * 10 ** (bucket / HIST_BINS_PER_DECADE)


def duration_stats(values_ms: list[float]) -> dict:
    """Exact per-episode duration statistics plus a mergeable histogram."""
    values = sorted(values_ms)
    hist = Counter(hist_bucket(v) for v in values)
    return {
        "n": len(values),
        "sum_ms": round(sum(values), 3),
        "max_ms": round(values[-1], 3) if values else 0.0,
        "p50_ms": round(nearest_rank(values, 0.50), 3),
        "p95_ms": round(nearest_rank(values, 0.95), 3),
        "p99_ms": round(nearest_rank(values, 0.99), 3),
        "hist": {str(k): v for k, v in sorted(hist.items())},
    }


def merge_duration_stats(stats: Iterable[dict]) -> dict:
    """Aggregate per-episode ``duration_stats``: n, mean and max are exact; quantiles are the
    containing histogram bucket's upper edge capped at the exact max (conservative)."""
    hist: Counter = Counter()
    n, total, peak = 0, 0.0, 0.0
    for s in stats:
        hist.update({int(k): v for k, v in s.get("hist", {}).items()})
        n += s.get("n", 0)
        total += s.get("sum_ms", 0.0)
        peak = max(peak, s.get("max_ms", 0.0))

    def quantile(q: float) -> float:
        need, running = max(1, math.ceil(q * n)), 0
        for bucket in sorted(hist):
            running += hist[bucket]
            if running >= need:
                return round(min(hist_edge(bucket), peak), 3)
        return round(peak, 3)

    return {
        "n": n,
        "mean_ms": round(total / n, 3) if n else 0.0,
        "p50_ms": quantile(0.50) if n else 0.0,
        "p95_ms": quantile(0.95) if n else 0.0,
        "p99_ms": quantile(0.99) if n else 0.0,
        "max_ms": round(peak, 3),
    }


def inspect_episode(
    steps: list,
    logs: list,
    seat: int,
    episode_steps: int,
    act_timeout: float,
    raw_actions: list[tuple[object, int | None]] | None = None,
):
    """Pure post-episode analysis of an episode's recorded states and logs from the candidate's
    (``seat``) side: terminal validation, banks, failure statuses, per-call timing and
    action-shape violations.

    ``raw_actions`` are the candidate's own return values in call order with the hand count
    it observed (see ``_recording``). They are preferred for the shape check because the
    environment fills schema defaults into the actions it records in ``steps``.
    """
    other = 1 - seat
    errors: list[str] = []
    if len(steps) != episode_steps:
        errors.append(f"episode has {len(steps)} recorded steps, expected {episode_steps}")
    final = steps[-1] if steps else []
    statuses = [final[i].get("status") if i < len(final) else None for i in range(PLAYER_COUNT)]
    if statuses != ["DONE"] * PLAYER_COUNT:
        errors.append(f"final statuses {statuses}, expected all DONE")
    final_obs = final[0].get("observation", {}) if final else {}
    if final_obs.get("step") != episode_steps - 1:
        errors.append(f"final step {final_obs.get('step')}, expected {episode_steps - 1}")
    farms = final_obs.get("farms") or []
    banks: list[int | None] = [None] * PLAYER_COUNT
    for i in range(PLAYER_COUNT):
        reward = _as_int(final[i].get("reward")) if i < len(final) else None
        money = _as_int(farms[i].get("money")) if i < len(farms) else None
        if reward is None:
            errors.append(f"player {i} reward is not an integral number")
        elif money is None or money != reward:
            errors.append(f"player {i} reward {reward} != final bank {money}")
        banks[i] = reward

    first_failure = {seat: None, other: None}
    for t, state in enumerate(steps):
        for i in (seat, other):
            if first_failure[i] is None and i < len(state):
                if state[i].get("status") in AGENT_FAILURE_STATUSES:
                    first_failure[i] = {"step": t, "status": state[i]["status"]}

    def observed_hands(t: int) -> int:
        seen = steps[t - 1][seat].get("observation", {}).get("farms") or []
        return len(seen[seat].get("hands", [])) if seat < len(seen) else 0

    emitted = []
    if raw_actions is not None:
        for t, (action, hands) in enumerate(raw_actions, start=1):
            if t < len(steps):
                emitted.append((t, action, observed_hands(t) if hands is None else hands))
    else:
        for t in range(1, len(steps)):
            if seat < len(steps[t]) and steps[t][seat].get("status") not in AGENT_FAILURE_STATUSES:
                emitted.append((t, steps[t][seat].get("action"), observed_hands(t)))
    shape_count, shape_examples = 0, []
    for t, action, hands in emitted:
        problem = shape_violation(action, hands)
        if problem:
            shape_count += 1
            if len(shape_examples) < 5:
                shape_examples.append({"step": t, "problem": problem})

    calls: dict[int, list[float]] = {seat: [], other: []}
    for entry in logs:
        for i in (seat, other):
            if i < len(entry) and isinstance(entry[i], dict) and "duration" in entry[i]:
                calls[i].append(float(entry[i]["duration"]) * 1000.0)
    over = [ms for ms in calls[seat] if ms > act_timeout * 1000.0]
    remaining = None
    if seat < len(final):
        remaining = final[seat].get("observation", {}).get("remainingOverageTime")
    failure = first_failure[seat]
    fields = {
        "terminal_valid": not errors and first_failure[seat] is None and not first_failure[other],
        "validation_errors": errors,
        "steps": len(steps),
        "final_step": final_obs.get("step"),
        "candidate_status": statuses[seat],
        "incumbent_status": statuses[other],
        "candidate_failure": failure,
        "incumbent_failure": first_failure[other],
        "candidate_bank": banks[seat],
        "incumbent_bank": banks[other],
        "invalid_actions": {
            "env_invalid": int(bool(failure) and failure["status"] == "INVALID"),
            "shape_violations": shape_count,
            "shape_checked": len(emitted),
            "shape_source": "raw" if raw_actions is not None else "env-recorded",
            "examples": shape_examples,
        },
        "timing": {
            "act_timeout_s": act_timeout,
            "candidate_call": duration_stats(calls[seat]),
            "incumbent_call": duration_stats(calls[other]),
            "candidate_calls_over_act_timeout": len(over),
            "candidate_overage_used_s": round(sum(ms / 1000.0 - act_timeout for ms in over), 6),
            "candidate_remaining_overage_s": remaining,
        },
    }
    finalize_outcome(fields, run_error=None)
    return fields


def finalize_outcome(fields: dict, run_error: str | None) -> None:
    """Set ``status`` (``ok`` only for a clean terminal episode), ``margin`` and ``outcome``."""
    status = "ok"
    for role in ("candidate", "incumbent"):
        failure = fields.get(f"{role}_failure")
        if failure and status == "ok":
            status = f"{role}_{failure['status'].lower()}"
    if status == "ok" and run_error:
        status = "harness_error"
    if status == "ok" and not fields.get("terminal_valid"):
        status = "incomplete"
    fields["status"] = status
    fields["run_error"] = run_error
    margin = fields["candidate_bank"] - fields["incumbent_bank"] if status == "ok" else None
    fields["margin"] = margin
    fields["outcome"] = outcome_of(margin) if margin is not None else None


def _is_tile(tile, kind: str) -> bool:
    return isinstance(tile, dict) and tile.get("kind") == kind


def care_loss_counters(steps: list, seat: int) -> dict[str, int]:
    """Read-only diagnostic from the public farm tiles of ``seat`` across an episode: crops
    lost to missed watering (and how many were plantings from that same day), crops lost to
    decay, and animals that escaped. Never used by any agent.

    A one-time crop harvested on the day's last turn whose emptied tile then receives the
    random end-of-day weed looks like PLANT -> WEED across the refresh; it is recorded as
    ``harvested_then_weed_spawn``, never as a crop lost to missed watering (the Milestone 6
    care-checker correction, shared with ``tools.harness``)."""
    from tools.harness import _harvested_one_time_crop

    losses: Counter = Counter()
    for a, b in zip(steps, steps[1:], strict=False):
        before_obs, after_obs = a[0].get("observation", {}), b[0].get("observation", {})
        try:
            before_tiles = before_obs["farms"][seat]["tiles"]
            after_tiles = after_obs["farms"][seat]["tiles"]
        except (KeyError, IndexError, TypeError):
            continue
        day_changed = after_obs.get("day") != before_obs.get("day")
        action = b[seat].get("action") if len(b) > seat and isinstance(b[seat], dict) else None
        for y, (row_before, row_after) in enumerate(zip(before_tiles, after_tiles, strict=False)):
            for x, (before, after) in enumerate(zip(row_before, row_after, strict=False)):
                if _is_tile(before, "PLANT") and _is_tile(after, "WEED"):
                    if day_changed and not before.get("watered_today"):
                        if _harvested_one_time_crop(before_obs, action, seat, x, y):
                            losses["harvested_then_weed_spawn"] += 1
                            continue
                        losses["crops_lost_unwatered"] += 1
                        if before.get("planted_day") == before_obs.get("day"):
                            losses["fresh_plantings_unwatered"] += 1
                    elif not day_changed and before.get("yield_units", 0) <= 1:
                        losses["crops_lost_decay"] += 1
                if isinstance(before, dict) and "animal" in before and day_changed:
                    if isinstance(after, dict) and "animal" not in after:
                        losses["animals_escaped"] += 1
    return dict(sorted(losses.items()))


def _recording(agent: Callable, sink: list) -> Callable:
    """Pass-through wrapper that keeps a reference to each raw action the agent returns and
    the hand count it observed. It forwards arguments exactly as kaggle-environments would
    (by the agent's own ``co_argcount``); its cost, a list append, is inside the timed call."""
    code = getattr(agent, "__code__", None)
    argc = code.co_argcount if code is not None else 2

    def recorded(obs, configuration=None):
        action = agent(*(obs, configuration)[:argc])
        try:
            hands = len(obs["farms"][obs["player"]]["hands"])
        except (KeyError, IndexError, TypeError):
            hands = None
        sink.append((action, hands))
        return action

    return recorded


def play_episode(job: dict, agents: dict, options: dict) -> dict:
    """Play one official Kaggriculture episode (the default episode function).

    ``agents`` maps ``candidate``/``incumbent`` to resolved agents; the candidate sits in
    ``job["seat"]``. ``debug=False`` makes an agent exception the environment's ``ERROR``
    status, as on Kaggle, instead of aborting the run. Returns the episode part of a record.
    """
    kaggle_environments = _quiet_import("kaggle_environments")
    seat = job["seat"]
    raw_actions: list[tuple[object, int | None]] = []
    candidate = agents["candidate"]
    if callable(candidate):
        candidate = _recording(candidate, raw_actions)
    order = [candidate, agents["incumbent"]]
    if seat == 1:
        order.reverse()
    t0 = time.perf_counter()
    env = kaggle_environments.make(
        ENVIRONMENT_NAME,
        configuration={"episodeSteps": options["episode_steps"], "seed": job["seed"]},
        debug=False,
    )
    make_s = time.perf_counter() - t0
    marks: list[float] = []
    original_step = env.step

    def timed_step(actions, logs=None):
        state = original_step(actions, logs)
        marks.append(time.perf_counter())
        return state

    env.step = timed_step
    run_error = None
    wall0, mono0 = time.time(), time.monotonic()
    start = time.perf_counter()
    try:
        env.run(order)
    except Exception as exc:  # environment failure (e.g. runTimeout): recorded, not retried
        run_error = f"{type(exc).__name__}: {exc}"
    episode_s = time.perf_counter() - start
    wall_s, mono_s = time.time() - wall0, time.monotonic() - mono0
    turn_ms = [(b - a) * 1000.0 for a, b in zip([start, *marks], marks, strict=False)]

    act_timeout = float(env.configuration.actTimeout)
    fields = inspect_episode(
        env.steps,
        env.logs,
        seat,
        options["episode_steps"],
        act_timeout,
        raw_actions if callable(agents["candidate"]) else None,
    )
    if run_error:
        fields["validation_errors"].append(f"env.run raised {run_error}")
        fields["terminal_valid"] = False
    finalize_outcome(fields, run_error)
    fields["timing"].update(
        {
            "run_timeout_s": float(env.configuration.runTimeout),
            "turn": duration_stats(turn_ms),
            "env_make_s": round(make_s, 4),
            "episode_s": round(episode_s, 4),
            "episode_wall_s": round(wall_s, 4),
            "host_suspended": wall_s - mono_s > SUSPEND_SLACK_S,
        }
    )
    fields["counters"] = {
        "candidate": care_loss_counters(env.steps, seat) if len(env.steps) > 1 else {},
        "incumbent": care_loss_counters(env.steps, 1 - seat) if len(env.steps) > 1 else {},
    }
    fields["replay"] = _maybe_save_replay(env, job, fields, options)
    # Worker-side analysis and replay writing after env.run (not scheduling overhead).
    fields["timing"]["post_episode_s"] = round(time.perf_counter() - start - episode_s, 4)
    return fields


def _maybe_save_replay(env, job: dict, fields: dict, options: dict) -> dict:
    mode = options.get("save_replays", "problems")
    wanted = (
        mode == "all"
        or (mode in ("problems", "losses") and fields["status"] != "ok")
        or (mode == "losses" and fields.get("outcome") == "loss")
    )
    info: dict = {"saved": None}
    if wanted:
        rel = f"replays/{job['partition']}-{job['seed']}-{job['seat']}.json"
        try:
            atomic_write_text(Path(options["run_dir"]) / rel, json.dumps(env.toJSON()))
            info["saved"] = rel
        except Exception as exc:  # a replay is diagnostics; never fail the episode for it
            info["error"] = f"{type(exc).__name__}: {exc}"
    return info


# ============================================================================================
# Worker processes and the single-writer runner
# ============================================================================================


def _worker_main(conn, worker_config: dict) -> None:
    """Resolve the episode function and agents once, then play one job per message until told to
    stop or the parent disappears (closed pipe)."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent owns interruption
    started = time.perf_counter()
    try:
        for path in reversed(worker_config["sys_path"]):
            if path not in sys.path:
                sys.path.insert(0, path)
        episode_fn = resolve_callable(worker_config["episode_fn"])
        if worker_config["episode_fn"] == DEFAULT_EPISODE_FN:
            _quiet_import("kaggle_environments")
        with contextlib.redirect_stdout(io.StringIO()):
            agents = {
                role: resolve_agent(worker_config[role]) for role in ("candidate", "incumbent")
            }
    except Exception:
        with contextlib.suppress(Exception):
            conn.send(("fatal", traceback.format_exc()))
        return
    conn.send(("ready", {"pid": os.getpid(), "startup_s": round(time.perf_counter() - started, 4)}))
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            return
        if message[0] == "stop":
            return
        _, job, options = message
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                reply = ("result", job["key"], episode_fn(job, agents, options))
        except Exception:
            reply = ("episode_error", job["key"], traceback.format_exc())
        try:
            conn.send(reply)
        except (EOFError, OSError):
            return


@dataclass
class _Worker:
    proc: mp.process.BaseProcess
    conn: multiprocessing.connection.Connection
    spawned: float
    ready: bool = False
    pid: int | None = None
    startup_s: float | None = None
    job: Job | None = None
    dispatched: float = 0.0
    episodes: int = 0


@dataclass
class RunOptions:
    """Execution settings. They do not affect results, so they may change between resumes."""

    workers: int = 1
    max_episodes_per_worker: int = 50
    episode_timeout_s: float = 3600.0
    startup_timeout_s: float = 300.0
    max_attempts: int = 3
    heartbeat_s: float = 5.0
    max_new_episodes: int | None = None
    save_replays: str = "problems"  # none | problems | losses | all
    allow_temp: bool = False


@dataclass
class RunOutcome:
    # finished | stable_ineligible | incomplete | interrupted | aborted
    state: str
    total: int
    completed: int
    new_episodes: int
    pending: list[str]
    failed: list[str]
    infra_failures: int
    reason: str | None = None
    stable_eligible: bool | None = None


def is_replayable_failure(status: str) -> bool:
    """Episode failures that are not the candidate's: incumbent failures, harness/environment
    exceptions and incomplete episodes. They are replayed (up to ``max_attempts``) before
    being recorded, and recorded ones block promotion. Candidate failures are never replayed."""
    return status.startswith("incumbent_") or status in ("harness_error", "incomplete")


class _Runner:
    """The only writer of a run directory while it holds the run lock."""

    def __init__(self, run_dir: Path, manifest: dict, options: RunOptions, root: Path, log):
        self.run_dir = run_dir
        self.manifest = manifest
        self.options = options
        self.log = log
        config = manifest["config"]
        self.worker_config = {
            "candidate": config["candidate"],
            "incumbent": config["incumbent"],
            "episode_fn": config["episode_fn"],
            "sys_path": [str(root), str(REPO_ROOT)],
        }
        self.episode_options = {
            "episode_steps": config["episode_steps"],
            "save_replays": options.save_replays,
            "run_dir": str(run_dir),
        }
        self.stores = {p.name: results_path(run_dir, p.name) for p in manifest_partitions(manifest)}
        self.total = sum(p.count * PLAYER_COUNT for p in manifest_partitions(manifest))
        self.ctx = mp.get_context("spawn")
        self.workers: list[_Worker] = []
        self.attempts: Counter = Counter()
        self.prior_attempts: dict[str, list[dict]] = {}
        self.failed: list[str] = []
        self.infra_failures = 0
        self.startup_failures = 0
        self.stop_reason: str | None = None
        self.abort_reason: str | None = None
        self.started_mono = time.monotonic()
        self.started_at = _utc_now()
        self.new_episodes = 0
        self.phase = STABLE
        self.stable_eligible: bool | None = None
        self.last_beat = -math.inf

    def event(self, kind: str, **data) -> None:
        append_line(self.run_dir / "events.jsonl", {"at": _utc_now(), "event": kind, **data})

    def heartbeat(self, state: str, completed: int) -> None:
        """Progress only. It never carries episode outcomes, so it is safe to watch while the
        holdout runs; the stable eligibility decision is shown once it has been made."""
        elapsed = time.monotonic() - self.started_mono
        rate = self.new_episodes / elapsed * 3600 if elapsed > 0 else 0.0
        in_flight = [
            {
                "key": w.job.key,
                "worker_pid": w.pid,
                "elapsed_s": round(time.monotonic() - w.dispatched, 1),
                "attempt": self.attempts[w.job.key] + 1,
            }
            for w in self.workers
            if w.job is not None
        ]
        remaining = self.total - completed
        beat = {
            "state": state,
            "phase": self.phase,
            "stable_eligible": self.stable_eligible,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "session_started_at": self.started_at,
            "updated_at": _utc_now(),
            "session_elapsed_s": round(elapsed, 1),
            "total_episodes": self.total,
            "completed_episodes": completed,
            "remaining_episodes": remaining,
            "new_episodes_this_session": self.new_episodes,
            "in_flight": in_flight,
            "workers_alive": sum(1 for w in self.workers if w.proc.is_alive()),
            "infra_failures": self.infra_failures,
            "failed_jobs": self.failed,
            "episodes_per_hour": round(rate, 1),
            "eta_s": round(remaining / rate * 3600) if rate > 0 else None,
        }
        atomic_write_text(
            self.run_dir / "heartbeat.json", json.dumps(beat, indent=1, sort_keys=True) + "\n"
        )
        self.last_beat = time.monotonic()

    def spawn(self) -> None:
        parent, child = self.ctx.Pipe(duplex=True)
        proc = self.ctx.Process(
            target=_worker_main, args=(child, self.worker_config), daemon=True, name="tournament"
        )
        proc.start()
        child.close()
        self.workers.append(_Worker(proc, parent, time.monotonic()))

    def retire(self, worker: _Worker, graceful: bool) -> None:
        if graceful and worker.proc.is_alive():
            with contextlib.suppress(OSError, EOFError):
                worker.conn.send(("stop",))
            worker.proc.join(timeout=10)
        for stop in (worker.proc.terminate, worker.proc.kill):
            if worker.proc.is_alive():
                stop()
                worker.proc.join(timeout=5)
        with contextlib.suppress(OSError):
            worker.conn.close()
        if worker in self.workers:
            self.workers.remove(worker)

    def retry_or_give_up(self, job: Job, pending: deque) -> None:
        if self.attempts[job.key] < self.options.max_attempts:
            pending.appendleft(job)
        else:
            self.failed.append(job.key)
            self.log(f"giving up on {job.key} this session after {self.attempts[job.key]} tries")

    def lose_job(self, worker: _Worker, reason: str, pending: deque) -> None:
        """A job's worker died, hung or raised: retry the job; nothing is recorded for it."""
        job, worker.job = worker.job, None
        if job is None:
            return
        self.infra_failures += 1
        self.attempts[job.key] += 1
        self.event(
            "job_lost", key=job.key, reason=reason, pid=worker.pid, attempt=self.attempts[job.key]
        )
        self.retry_or_give_up(job, pending)

    def in_flight(self) -> int:
        return sum(1 for w in self.workers if w.job is not None)

    def play(self, jobs: list[Job], completed: set[str]) -> str:
        """Play ``jobs`` until none is left (``drained``), the episode budget is spent
        (``budget``), or the session is interrupted/aborted."""
        pending = deque(jobs)
        budget = self.options.max_new_episodes
        while not (self.stop_reason or self.abort_reason):
            busy = self.in_flight()
            allowance = len(pending)
            if budget is not None:
                allowance = max(0, min(allowance, budget - self.new_episodes - busy))
            if not busy and not allowance:
                return "budget" if pending else "drained"
            while len(self.workers) < min(self.options.workers, busy + allowance):
                self.spawn()
            for w in self.workers:
                if allowance and w.ready and w.job is None:
                    job = pending.popleft()
                    message = ("job", {**asdict(job), "key": job.key}, self.episode_options)
                    try:
                        w.conn.send(message)
                    except (OSError, EOFError):
                        pending.appendleft(job)
                        continue
                    w.job, w.dispatched = job, time.monotonic()
                    allowance -= 1
            waitables = [w.conn for w in self.workers] + [w.proc.sentinel for w in self.workers]
            ready = set(multiprocessing.connection.wait(waitables, timeout=1.0))
            for w in list(self.workers):
                self.poll_worker(w, ready, pending, completed)
            self.check_deadlines(pending)
            if time.monotonic() - self.last_beat >= self.options.heartbeat_s:
                self.heartbeat("running", len(completed))
        for w in self.workers:
            if w.job is not None:  # interrupted: the unfinished episode is discarded, never written
                self.event("job_discarded", key=w.job.key, reason=self.stop_reason)
                w.job = None
        return "interrupted" if self.stop_reason else "aborted"

    def decide_stable(self, stable: Partition) -> dict:
        """Evaluate the stable eligibility gate from the recorded stable results and persist the
        decision. The holdout is only played when this passes."""
        policy = manifest_policy(self.manifest)
        loaded = load_results(self.run_dir, self.manifest, [STABLE])
        decision = stable_eligibility(summarize(loaded.records, [stable]), policy)
        self.stable_eligible = decision["eligible"]
        atomic_write_text(
            self.run_dir / "stable_gate.json",
            json.dumps(
                {
                    **decision,
                    "stable_results_sha256": loaded.files[STABLE]["sha256"],
                    "policy": policy.as_dict(),
                },
                indent=1,
                sort_keys=True,
            )
            + "\n",
        )
        self.event("stable_gate", eligible=decision["eligible"], failed=decision["failed"])
        return decision

    def finish(self, state: str, completed: set[str], planned: list[Job]) -> RunOutcome:
        for w in list(self.workers):
            self.retire(w, graceful=state not in ("interrupted", "aborted"))
        reason = self.stop_reason or self.abort_reason
        if state == "stable_ineligible":
            reason = "stable partition failed its eligibility gate; holdout not played"
        self.heartbeat(state, len(completed))
        self.event("session_end", state=state, completed=len(completed), reason=reason)
        return RunOutcome(
            state,
            len(planned),
            len(completed),
            self.new_episodes,
            [j.key for j in planned if j.key not in completed and j.key not in self.failed],
            list(self.failed),
            self.infra_failures,
            reason,
            self.stable_eligible,
        )

    def poll_worker(self, w: _Worker, ready: set, pending: deque, completed: set[str]) -> None:
        message = None
        if w.conn in ready or w.proc.sentinel in ready:
            with contextlib.suppress(EOFError, OSError):
                if w.conn.poll():
                    message = w.conn.recv()
        if message is None:
            if not w.proc.is_alive():
                reason = f"worker exited with code {w.proc.exitcode}"
                if not w.ready:
                    self.note_startup_failure(reason)
                self.lose_job(w, reason, pending)
                self.retire(w, graceful=False)
            return
        kind = message[0]
        if kind == "ready":
            w.ready, w.pid, w.startup_s = True, message[1]["pid"], message[1]["startup_s"]
            spawn_to_ready = round(time.monotonic() - w.spawned, 4)
            self.event("worker_ready", pid=w.pid, startup_s=w.startup_s, spawn_s=spawn_to_ready)
        elif kind == "fatal":
            self.event("worker_fatal", traceback=message[1])
            self.log(f"worker failed to initialise:\n{message[1]}")
            self.abort_reason = "worker initialisation failed (agent import or episode function)"
            self.retire(w, graceful=False)
        elif kind == "episode_error":
            self.event("episode_function_error", key=message[1], traceback=message[2])
            self.lose_job(w, "episode function raised", pending)
        elif kind == "result":
            self.record_result(w, message[1], message[2], completed, pending)
            if w.episodes >= self.options.max_episodes_per_worker:
                self.event("worker_recycled", pid=w.pid, episodes=w.episodes)
                self.retire(w, graceful=True)

    def note_startup_failure(self, reason: str) -> None:
        self.startup_failures += 1
        self.event("worker_start_failed", reason=reason)
        if self.startup_failures >= 3:
            self.abort_reason = f"workers repeatedly failed to start ({reason})"

    def record_result(
        self, w: _Worker, key: str, episode: dict, completed: set[str], pending: deque
    ) -> None:
        job = w.job
        if job is None or job.key != key:
            raise TournamentError(f"worker {w.pid} returned {key} while assigned {job}")
        if key in completed:
            raise DuplicateResultError(f"refusing to write a second result for {key}")
        w.job = None
        w.episodes += 1
        status = episode.get("status", "")
        if is_replayable_failure(status) and self.attempts[key] + 1 < self.options.max_attempts:
            # Not the candidate's failure: replay it rather than record an unusable episode.
            self.attempts[key] += 1
            attempt = {
                "attempt": self.attempts[key],
                "status": status,
                "incumbent_failure": episode.get("incumbent_failure"),
                "validation_errors": episode.get("validation_errors", []),
                "run_error": episode.get("run_error"),
            }
            self.prior_attempts.setdefault(key, []).append(attempt)
            self.event("episode_replayed", key=key, **attempt)
            pending.appendleft(job)
            return
        identity = self.manifest["identity"]
        dispatch_to_result = time.monotonic() - w.dispatched
        timing = episode.get("timing") or {}
        # Everything between dispatch and result that is not the worker's own measured work:
        # pipe transfer, pickling and parent/worker scheduling latency.
        worker_s = sum(timing.get(k) or 0.0 for k in ("env_make_s", "episode_s", "post_episode_s"))
        record = {
            **episode,
            "schema": SCHEMA_VERSION,
            "key": key,
            "partition": job.partition,
            "seed": job.seed,
            "seat": job.seat,
            "candidate_id": identity["candidate"]["digest"],
            "incumbent_id": identity["incumbent"]["digest"],
            "attempt": self.attempts[key] + 1,
            "prior_attempts": self.prior_attempts.get(key, []),
            "worker": {"pid": w.pid, "episode_index": w.episodes, "startup_s": w.startup_s},
            "sched": {
                "dispatch_to_result_s": round(dispatch_to_result, 4),
                "overhead_s": round(max(0.0, dispatch_to_result - worker_s), 4),
            },
            "finished_at": _utc_now(),
        }
        problems = record_problems(record, self.manifest)
        if problems:
            raise ResultsCorruptError(f"episode function produced an invalid record: {problems}")
        append_line(self.stores[job.partition], record)
        completed.add(key)
        self.new_episodes += 1
        self.log(f"[{len(completed)}] recorded {key}")

    def check_deadlines(self, pending: deque) -> None:
        now = time.monotonic()
        for w in list(self.workers):
            if w.job is not None and now - w.dispatched > self.options.episode_timeout_s:
                self.log(f"{w.job.key} exceeded the episode timeout; killing worker {w.pid}")
                self.lose_job(w, f"episode exceeded {self.options.episode_timeout_s} s", pending)
                self.retire(w, graceful=False)
            elif not w.ready and now - w.spawned > self.options.startup_timeout_s:
                self.note_startup_failure("worker did not become ready in time")
                self.retire(w, graceful=False)


def run_tournament(
    run_dir: Path,
    config: RunConfig,
    options: RunOptions | None = None,
    root: Path = REPO_ROOT,
    log: Callable[[str], None] = lambda message: None,
) -> RunOutcome:
    """Create or resume a run.

    Formal flow: every stable episode is played first; once the stable partition is complete
    its eligibility gate is evaluated (``stable_gate.json``); the holdout is played only if
    the stable partition is eligible. SIGINT/SIGTERM discard in-flight episodes, flush state
    and return ``state="interrupted"``; rerunning the same command resumes.
    """
    options = options or RunOptions()
    if options.workers < 1 or options.max_episodes_per_worker < 1 or options.max_attempts < 1:
        raise ValueError("workers, max_episodes_per_worker and max_attempts must be >= 1")
    if options.save_replays not in ("none", "problems", "losses", "all"):
        raise ValueError(f"unknown save_replays mode {options.save_replays!r}")
    run_dir = Path(run_dir).resolve()
    check_persistent_dir(run_dir, options.allow_temp)
    validate_partitions(config.partitions)
    run_dir.mkdir(parents=True, exist_ok=True)
    with RunLock(run_dir):
        manifest = ensure_manifest(run_dir, config, root)
        partitions = {p.name: p for p in manifest_partitions(manifest)}
        quarantine = run_dir / "quarantine.jsonl"
        for name in partitions:
            fragment = repair_partial_tail(results_path(run_dir, name), quarantine)
            if fragment:
                log(f"quarantined a {len(fragment)}-byte partial line of results-{name}.jsonl")
        repair_partial_tail(run_dir / "events.jsonl", quarantine)
        loaded = load_results(run_dir, manifest, list(partitions))
        completed = {r["key"] for r in loaded.records}
        planned = plan_jobs(partitions.values())
        runner = _Runner(run_dir, manifest, options, Path(root).resolve(), log)

        def request_stop(signum, frame):
            runner.stop_reason = f"signal {signal.Signals(signum).name}"

        previous = {}
        for signum in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(ValueError):  # only possible in the main thread
                previous[signum] = signal.signal(signum, request_stop)

        def todo(name: str) -> list[Job]:
            return [j for j in planned if j.partition == name and j.key not in completed]

        try:
            runner.event("session_start", pid=os.getpid(), total=len(planned))
            state = runner.play(todo(STABLE), completed)
            if state == "drained" and not todo(STABLE):
                decision = runner.decide_stable(partitions[STABLE])
                if HOLDOUT in partitions:
                    if decision["eligible"]:
                        runner.phase = HOLDOUT
                        state = runner.play(todo(HOLDOUT), completed)
                    else:
                        state = "stable_ineligible"
            if state in ("drained", "budget"):
                state = "finished" if len(completed) == len(planned) else "incomplete"
            return runner.finish(state, completed, planned)
        except BaseException as exc:
            with contextlib.suppress(Exception):
                runner.event("session_error", error=f"{type(exc).__name__}: {exc}")
                runner.heartbeat("aborted", len(completed))
            raise
        finally:
            for w in list(runner.workers):
                runner.retire(w, graceful=False)
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def run_status(run_dir: Path) -> dict:
    """Progress and liveness without outcomes (safe while holdout episodes are running)."""
    run_dir = Path(run_dir)
    manifest = load_manifest(run_dir)
    beat_path = run_dir / "heartbeat.json"
    beat = json.loads(beat_path.read_text(encoding="utf-8")) if beat_path.exists() else {}
    counts = {
        p.name: {
            "recorded_episodes": len(read_jsonl(results_path(run_dir, p.name)).records),
            "planned_episodes": p.count * PLAYER_COUNT,
            "planned_paired_seeds": p.count,
        }
        for p in manifest_partitions(manifest)
    }
    state = beat.get("state", "never started")
    if is_locked(run_dir):
        liveness = "RUNNING (lock held by a live runner)"
    elif state == "running":
        liveness = "STALE (runner died without a clean shutdown; rerun the command to resume)"
    else:
        liveness = f"STOPPED ({state})"
    return {"liveness": liveness, "counts": counts, "heartbeat": beat}


# ============================================================================================
# Statistics
#
# Terminology: an *episode* is one 720-step game with the candidate in one seat; a *paired
# seed* is a planned seed, played twice with the seats swapped; a *completed pair* is a paired
# seed with exactly one terminal-valid ("ok") episode for each candidate seat. Only
# completed pairs count as evidence.
# ============================================================================================

Z_95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion; ``(0.0, 1.0)`` when ``n == 0``."""
    if n <= 0:
        return 0.0, 1.0
    if not 0 <= successes <= n:
        raise ValueError("successes must be within [0, n]")
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def exact_median(values: list[int]) -> Fraction | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return Fraction(ordered[mid])
    return Fraction(ordered[mid - 1] + ordered[mid], 2)


def _number(value: Fraction | None):
    if value is None:
        return None
    return int(value) if value.denominator == 1 else float(value)


def outcome_block(records: list[dict]) -> dict:
    """Wins/losses/ties per episode, win rate (ties are not wins), Wilson 95% and margins."""
    margins = [r["margin"] for r in records]
    n = len(margins)
    wins, ties = sum(m > 0 for m in margins), sum(m == 0 for m in margins)
    lo, hi = wilson_interval(wins, n)
    return {
        "episodes": n,
        "wins": wins,
        "losses": n - wins - ties,
        "ties": ties,
        "win_rate": wins / n if n else None,
        "wilson95": [lo, hi],
        "median_margin": _number(exact_median(margins)),
        "mean_margin": round(statistics.fmean(margins), 3) if margins else None,
        "min_margin": min(margins) if margins else None,
        "max_margin": max(margins) if margins else None,
    }


def _counter_sum(dicts: Iterable[dict | None]) -> dict:
    total: Counter = Counter()
    for d in dicts:
        total.update({k: v for k, v in (d or {}).items() if _is_int(v)})
    return dict(sorted(total.items()))


def _quantiles(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50": round(nearest_rank(ordered, 0.5), 4),
        "p95": round(nearest_rank(ordered, 0.95), 4),
        "max": round(ordered[-1], 4) if ordered else 0.0,
    }


def _record_order(r: dict) -> tuple:
    return PARTITION_ORDER.index(r["partition"]), r["seed"], r["seat"]


def summarize(records: list[dict], partitions: Iterable[Partition]) -> dict:
    """Aggregate records (in any order) for the given planned partitions.

    The outcome basis (``pairs``) contains only completed pairs. A paired seed with a missing
    seat, a non-ok episode or more than one result for a seat is never counted, and every
    such seed is listed in ``incomplete_seeds`` with the reason.
    """
    partitions = list(partitions)
    planned_keys = {
        job_key(p.name, seed, seat)
        for p in partitions
        for seed in p.seeds
        for seat in range(PLAYER_COUNT)
    }
    ordered = sorted(records, key=_record_order)
    by_seed: dict[tuple[str, int], dict[int, list[dict]]] = {}
    for r in ordered:
        by_seed.setdefault((r["partition"], r["seed"]), {}).setdefault(r["seat"], []).append(r)
    for p in partitions:
        for seed in p.seeds:
            by_seed.setdefault((p.name, seed), {})
    pair_records, pair_margins, pair_outcomes, incomplete = [], [], [], []
    duplicates = 0
    for (part, seed), seats in sorted(
        by_seed.items(), key=lambda kv: (PARTITION_ORDER.index(kv[0][0]), kv[0][1])
    ):
        extra = sum(len(v) - 1 for v in seats.values())
        duplicates += extra
        episodes = [seats.get(i, [None])[0] for i in range(PLAYER_COUNT)]
        if extra:
            reason = "duplicate seat result"
        elif any(e is None for e in episodes):
            reason = "missing seat " + ", ".join(
                str(i) for i, e in enumerate(episodes) if e is None
            )
        elif any(e["status"] != "ok" for e in episodes):
            reason = "non-ok episode"
        else:
            pair_records.extend(episodes)
            pair_margins.append(sum(e["margin"] for e in episodes))
            pair_outcomes.append(tuple(e["outcome"] for e in episodes))
            continue
        incomplete.append(
            {
                "partition": part,
                "seed": seed,
                "reason": reason,
                "seats": {str(s): [e["status"] for e in v] for s, v in sorted(seats.items())},
            }
        )
    pairs = outcome_block(pair_records)
    both_won = sum(o == ("win", "win") for o in pair_outcomes)
    both_lost = sum(o == ("loss", "loss") for o in pair_outcomes)
    pairs["completed_pairs"] = len(pair_margins)
    pairs["per_seed"] = {
        "median_pair_margin": _number(exact_median(pair_margins)),
        "mean_pair_margin": round(statistics.fmean(pair_margins), 3) if pair_margins else None,
        "pairs_won": sum(m > 0 for m in pair_margins),
        "pairs_tied": sum(m == 0 for m in pair_margins),
        "pairs_lost": sum(m < 0 for m in pair_margins),
        "both_seats_won": both_won,
        "both_seats_lost": both_lost,
        "split": len(pair_outcomes) - both_won - both_lost,
    }

    ok = [r for r in ordered if r["status"] == "ok"]
    statuses = Counter(r["status"] for r in ordered)
    shape = sum((r.get("invalid_actions") or {}).get("shape_violations", 0) for r in ordered)
    recorded_keys = {r["key"] for r in ordered}
    incumbent = {k: v for k, v in sorted(statuses.items()) if k.startswith("incumbent_")}
    failures = {
        "episodes_by_status": dict(sorted(statuses.items())),
        "candidate_crashes": statuses["candidate_error"],
        "candidate_timeouts": statuses["candidate_timeout"],
        "candidate_invalid_episodes": statuses["candidate_invalid"],
        "candidate_shape_violations": shape,
        "candidate_invalid_actions": statuses["candidate_invalid"] + shape,
        "incumbent_failures": sum(incumbent.values()),
        "incumbent_failures_by_status": incumbent,
        "harness_errors": statuses["harness_error"],
        "incomplete_episodes": statuses["incomplete"],
        "duplicate_results": duplicates,
        "missing_episodes": len(planned_keys - recorded_keys),
        "non_ok_episodes": len(ordered) - len(ok),
        "recovered_episodes": sum(1 for r in ordered if r.get("prior_attempts")),
        "retried_episodes": sum(1 for r in ordered if r.get("attempt", 1) > 1),
    }

    timings = [r.get("timing") or {} for r in ordered]
    startups = {
        (r.get("worker") or {}).get("pid"): (r.get("worker") or {}).get("startup_s")
        for r in ordered
    }
    remaining = [
        t["candidate_remaining_overage_s"]
        for t in timings
        if isinstance(t.get("candidate_remaining_overage_s"), int | float)
    ]
    timing = {
        "act_timeout_s": next((t["act_timeout_s"] for t in timings if "act_timeout_s" in t), None),
        "candidate_agent_call_ms": merge_duration_stats(
            t.get("candidate_call", {}) for t in timings
        ),
        "incumbent_agent_call_ms": merge_duration_stats(
            t.get("incumbent_call", {}) for t in timings
        ),
        "turn_ms": merge_duration_stats(t.get("turn", {}) for t in timings),
        "episode_s": _quantiles([t["episode_s"] for t in timings if "episode_s" in t]),
        "env_make_s": _quantiles([t["env_make_s"] for t in timings if "env_make_s" in t]),
        "post_episode_s": _quantiles(
            [t["post_episode_s"] for t in timings if "post_episode_s" in t]
        ),
        "worker_startup_s": _quantiles([v for v in startups.values() if v is not None]),
        "scheduling_overhead_s": _quantiles(
            [r["sched"]["overhead_s"] for r in ordered if r.get("sched")]
        ),
        "candidate_calls_over_act_timeout": sum(
            t.get("candidate_calls_over_act_timeout", 0) for t in timings
        ),
        "episodes_with_candidate_overage": sum(
            1 for t in timings if t.get("candidate_calls_over_act_timeout", 0)
        ),
        "candidate_overage_used_s": round(
            sum(t.get("candidate_overage_used_s", 0.0) for t in timings), 3
        ),
        "min_candidate_remaining_overage_s": min(remaining, default=None),
        "host_suspended_episodes": sum(1 for t in timings if t.get("host_suspended")),
    }
    return {
        "planned_paired_seeds": sum(p.count for p in partitions),
        "planned_episodes": len(planned_keys),
        "recorded_episodes": len(ordered),
        "ok_episodes": len(ok),
        "pairs": pairs,
        "all_ok_episodes": outcome_block(ok),
        "by_seat": {
            str(seat): outcome_block([r for r in pair_records if r["seat"] == seat])
            for seat in range(PLAYER_COUNT)
        },
        "failures": failures,
        "timing": timing,
        "counters": {
            role: _counter_sum((r.get("counters") or {}).get(role) for r in ok)
            for role in ("candidate", "incumbent")
        },
        "incomplete_seeds": incomplete,
    }


def episode_diagnostics(r: dict, manifest: dict) -> dict:
    """What is needed to inspect or reproduce one episode."""
    config = manifest["config"]
    return {
        "key": r["key"],
        "partition": r["partition"],
        "seed": r["seed"],
        "seat": r["seat"],
        "status": r["status"],
        "candidate_bank": r.get("candidate_bank"),
        "incumbent_bank": r.get("incumbent_bank"),
        "margin": r.get("margin"),
        "candidate_failure": r.get("candidate_failure"),
        "incumbent_failure": r.get("incumbent_failure"),
        "validation_errors": r.get("validation_errors", []),
        "run_error": r.get("run_error"),
        "attempt": r.get("attempt", 1),
        "prior_attempts": r.get("prior_attempts", []),
        "invalid_actions": {
            k: v for k, v in (r.get("invalid_actions") or {}).items() if k != "examples"
        },
        "calls_over_act_timeout": (r.get("timing") or {}).get("candidate_calls_over_act_timeout"),
        "counters": (r.get("counters") or {}).get("candidate", {}),
        "replay": (r.get("replay") or {}).get("saved"),
        "regenerate": (
            f"kaggriculture seed={r['seed']} episodeSteps={config['episode_steps']}: "
            f"{config['candidate']} in seat {r['seat']} vs {config['incumbent']}"
        ),
    }


def worst_losses(records: list[dict], manifest: dict, limit: int = 20) -> list[dict]:
    """The largest ``ok`` losses by margin, with replay/regeneration metadata."""
    losses = sorted(
        (r for r in records if r["status"] == "ok" and r["outcome"] == "loss"),
        key=lambda r: (r["margin"], r["key"]),
    )
    return [episode_diagnostics(r, manifest) for r in losses[:limit]]


# ============================================================================================
# Promotion policy
# ============================================================================================


@dataclass(frozen=True)
class PromotionPolicy:
    """Promotion thresholds. Comparisons are strict where TILLA_STRATEGY.md §19 says ``>``
    and use exact ``Fraction`` arithmetic, so a boundary can never round in our favour.

    "At least 2,000 paired games" means 2,000 completed pairs: 2,000 seeds, each with one
    valid episode per candidate seat, i.e. 4,000 episodes. The stable/holdout split follows
    the formal plan (1,500 stable + 500 holdout paired seeds); the statistical thresholds
    apply both to the stable eligibility gate and to the combined final verdict.
    """

    name: str = "tilla-default"
    min_paired_seeds: int = 2000  # completed pairs, stable + holdout combined
    min_episodes: int = 4000  # episodes inside completed pairs, combined
    min_stable_paired_seeds: int = 1500
    min_holdout_paired_seeds: int = 500
    win_rate_above: Fraction = Fraction(53, 100)
    wilson_lower_above: Fraction = Fraction(1, 2)
    median_margin_above: Fraction = Fraction(0)
    max_candidate_crashes: int = 0
    max_candidate_timeouts: int = 0  # environment-enforced TIMEOUT statuses only
    max_candidate_invalid_actions: int = 0
    max_incumbent_failures: int = 0  # recorded after replay; they invalidate the evidence
    max_harness_failures: int = 0  # harness errors, incomplete episodes, duplicate results
    require_complete_evidence: bool = True  # every planned episode recorded and paired
    require_stable_eligibility: bool = True
    require_mandatory_scenarios: bool = True
    # Only an explicit policy may declare that no mandatory scenarios exist. An empty or
    # missing suite is never read as that declaration.
    mandatory_scenarios_declared_empty: bool = False

    def as_dict(self) -> dict:
        return {
            key: str(value) if isinstance(value, Fraction) else value
            for key, value in asdict(self).items()
        }

    @classmethod
    def from_dict(cls, data: dict) -> PromotionPolicy:
        kwargs = dict(data)
        for key in ("win_rate_above", "wilson_lower_above", "median_margin_above"):
            if key in kwargs:
                kwargs[key] = Fraction(str(kwargs[key]))
        return cls(**kwargs)


# The Tilla promotion gate (TILLA_STRATEGY.md §19, TILLA_ARCHITECTURE.md §10).
TILLA_PROMOTION_POLICY = PromotionPolicy()

_AT_LEAST = (
    "min_paired_seeds",
    "min_episodes",
    "min_stable_paired_seeds",
    "min_holdout_paired_seeds",
    "win_rate_above",
    "wilson_lower_above",
    "median_margin_above",
)
_AT_MOST = (
    "max_candidate_crashes",
    "max_candidate_timeouts",
    "max_candidate_invalid_actions",
    "max_incumbent_failures",
    "max_harness_failures",
)
_REQUIRED = (
    "require_complete_evidence",
    "require_stable_eligibility",
    "require_mandatory_scenarios",
)


def manifest_policy(manifest: dict) -> PromotionPolicy:
    return PromotionPolicy.from_dict(manifest["config"]["policy"])


def weaker_than_default(policy: PromotionPolicy) -> list[str]:
    """Fields in which ``policy`` is more lenient than the Tilla gate."""
    base = TILLA_PROMOTION_POLICY
    weaker = [f for f in _AT_LEAST if getattr(policy, f) < getattr(base, f)]
    weaker += [f for f in _AT_MOST if getattr(policy, f) > getattr(base, f)]
    weaker += [f for f in _REQUIRED if getattr(base, f) and not getattr(policy, f)]
    if policy.mandatory_scenarios_declared_empty and not base.mandatory_scenarios_declared_empty:
        weaker.append("mandatory_scenarios_declared_empty")
    return weaker


PASS, FAIL, MISSING_EVIDENCE = "PASS", "FAIL", "MISSING_EVIDENCE"


def check_scenarios(
    results_file: Path | None,
    manifest: dict,
    policy: PromotionPolicy = TILLA_PROMOTION_POLICY,
    definitions: Path = SCENARIO_DEFINITIONS,
) -> dict:
    """Mandatory-scenario evidence: ``PASS``, ``FAIL`` (a material regression) or
    ``MISSING_EVIDENCE`` (anything short of complete, attributable evidence). Only ``PASS``
    satisfies the gate, so the check fails closed.

    ``definitions`` (``benchmarks/scenarios.json``) names the suite; scenarios are mandatory
    unless marked ``"mandatory": false``. ``results_file`` (written by a scenario runner)::

        {"candidate_id": <digest>, "incumbent_id": <digest>,
         "scenarios": [{"name": str, "material_regression": bool, ...}]}
    """

    def result(status: str, reason: str, **extra) -> dict:
        return {"status": status, "reason": reason, **extra}

    if not Path(definitions).exists():
        return result(MISSING_EVIDENCE, f"scenario definitions {Path(definitions).name} not found")
    defs = json.loads(Path(definitions).read_text(encoding="utf-8"))
    required = sorted(s["name"] for s in defs.get("scenarios", []) if s.get("mandatory", True))
    if not required:
        if policy.mandatory_scenarios_declared_empty:
            return result(PASS, "the policy explicitly declares zero mandatory scenarios")
        return result(
            MISSING_EVIDENCE,
            f"{Path(definitions).name} defines 0 mandatory scenarios and the policy does not "
            "declare an empty suite; an empty suite is not evidence of no regression",
            required=required,
        )
    if results_file is None:
        return result(
            MISSING_EVIDENCE,
            f"no scenario results supplied (--scenario-results) for {len(required)} mandatory "
            "scenarios",
            required=required,
        )
    try:
        data = json.loads(Path(results_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return result(MISSING_EVIDENCE, f"unreadable scenario results: {exc}", required=required)
    identity = manifest["identity"]
    for role in ("candidate", "incumbent"):
        if data.get(f"{role}_id") != identity[role]["digest"]:
            return result(
                MISSING_EVIDENCE,
                f"scenario results were produced for another {role}",
                required=required,
            )
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list):
        return result(MISSING_EVIDENCE, "scenario results have no 'scenarios' list")
    by_name = {s.get("name"): s for s in scenarios if isinstance(s, dict)}
    missing = [n for n in required if n not in by_name]
    unjudged = [
        n
        for n in required
        if n in by_name and not isinstance(by_name[n].get("material_regression"), bool)
    ]
    regressions = [
        n for n in required if n in by_name and by_name[n].get("material_regression") is True
    ]
    extra = {"required": required, "missing": missing, "unjudged": unjudged}
    if regressions:
        return result(
            FAIL,
            f"material regressions: {', '.join(regressions)}",
            regressions=regressions,
            **extra,
        )
    if missing or unjudged:
        parts = [f"no result for {', '.join(missing)}" if missing else ""]
        parts.append(
            f"no material_regression verdict for {', '.join(unjudged)}" if unjudged else ""
        )
        return result(MISSING_EVIDENCE, "; ".join(p for p in parts if p), regressions=[], **extra)
    return result(
        PASS,
        f"{len(required)} mandatory scenarios, no material regression",
        regressions=[],
        **extra,
    )


def _check(name: str, passed: bool, actual, requirement: str) -> dict:
    return {"name": name, "passed": bool(passed), "actual": actual, "requirement": requirement}


def _statistical_checks(summary: dict, policy: PromotionPolicy, label: str) -> list[dict]:
    pairs = summary["pairs"]
    n = pairs["episodes"]
    win_rate = Fraction(pairs["wins"], n) if n else None
    median = pairs["median_margin"]
    return [
        _check(
            f"{label}win_rate",
            win_rate is not None and win_rate > policy.win_rate_above,
            float(win_rate) if win_rate is not None else None,
            f"> {float(policy.win_rate_above):.2%} of completed-pair episodes (ties are not wins)",
        ),
        _check(
            f"{label}wilson95_lower",
            n > 0 and Fraction(pairs["wilson95"][0]) > policy.wilson_lower_above,
            pairs["wilson95"][0] if n else None,
            f"> {float(policy.wilson_lower_above):.2%}",
        ),
        _check(
            f"{label}median_margin",
            median is not None and Fraction(median) > policy.median_margin_above,
            median,
            f"> {policy.median_margin_above}",
        ),
    ]


def _integrity_checks(summary: dict, policy: PromotionPolicy, label: str) -> list[dict]:
    f = summary["failures"]
    harness = f["harness_errors"] + f["incomplete_episodes"] + f["duplicate_results"]
    complete = (
        f["missing_episodes"] == 0
        and summary["pairs"]["completed_pairs"] == summary["planned_paired_seeds"]
    )
    return [
        _check(
            f"{label}candidate_crashes",
            f["candidate_crashes"] <= policy.max_candidate_crashes,
            f["candidate_crashes"],
            f"<= {policy.max_candidate_crashes} ERROR statuses",
        ),
        _check(
            f"{label}candidate_timeouts",
            f["candidate_timeouts"] <= policy.max_candidate_timeouts,
            f["candidate_timeouts"],
            f"<= {policy.max_candidate_timeouts} environment-enforced TIMEOUT statuses",
        ),
        _check(
            f"{label}candidate_invalid_actions",
            f["candidate_invalid_actions"] <= policy.max_candidate_invalid_actions,
            f["candidate_invalid_actions"],
            f"<= {policy.max_candidate_invalid_actions} (INVALID statuses + shape violations)",
        ),
        _check(
            f"{label}incumbent_integrity",
            f["incumbent_failures"] <= policy.max_incumbent_failures,
            f["incumbent_failures_by_status"] or 0,
            f"<= {policy.max_incumbent_failures} unresolved incumbent failures (after replay)",
        ),
        _check(
            f"{label}harness_integrity",
            harness <= policy.max_harness_failures,
            {
                "harness_errors": f["harness_errors"],
                "incomplete": f["incomplete_episodes"],
                "duplicates": f["duplicate_results"],
            },
            f"<= {policy.max_harness_failures} harness errors/incomplete/duplicate results",
        ),
        _check(
            f"{label}evidence_complete",
            complete or not policy.require_complete_evidence,
            {
                "completed_pairs": summary["pairs"]["completed_pairs"],
                "planned_paired_seeds": summary["planned_paired_seeds"],
                "missing_episodes": f["missing_episodes"],
            },
            "every planned paired seed is a completed pair",
        ),
    ]


def stable_eligibility(stable: dict, policy: PromotionPolicy) -> dict:
    """The stable partition's own gate: complete, clean, and meeting the statistical
    thresholds on its own. The holdout may only be played/revealed after this passes, and a
    strong holdout can never rescue a stable partition that failed it."""
    pairs = stable["pairs"]
    checks = [
        _check(
            "stable_min_paired_seeds",
            pairs["completed_pairs"] >= policy.min_stable_paired_seeds,
            {"completed_pairs": pairs["completed_pairs"], "episodes": pairs["episodes"]},
            f">= {policy.min_stable_paired_seeds} completed pairs "
            f"({2 * policy.min_stable_paired_seeds} episodes)",
        ),
        *_integrity_checks(stable, policy, "stable_"),
        *_statistical_checks(stable, policy, "stable_"),
    ]
    failed = [c["name"] for c in checks if not c["passed"]]
    return {"eligible": not failed, "failed": failed, "checks": checks}


def evaluate_gate(
    stable: dict,
    holdout: dict | None,
    combined: dict,
    eligibility: dict,
    policy: PromotionPolicy,
    scenarios: dict,
    manifest: dict,
) -> dict:
    """Explicit PASS/FAIL of the complete evidence. PASS needs every gating check; advisories
    are reported alongside but never change the verdict."""
    weaker = weaker_than_default(policy)
    config = manifest["config"]
    pairs = combined["pairs"]
    checks = [
        _check(
            "policy_not_weaker_than_tilla_gate",
            not weaker,
            weaker or "equal or stricter",
            "no threshold more lenient than TILLA_STRATEGY.md §19",
        ),
        _check(
            "official_episode_runner",
            config["episode_fn"] == DEFAULT_EPISODE_FN and config["episode_steps"] == EPISODE_STEPS,
            f"{config['episode_fn']}, {config['episode_steps']} steps",
            f"{DEFAULT_EPISODE_FN}, {EPISODE_STEPS}-step official episodes",
        ),
        _check(
            "stable_eligibility",
            eligibility["eligible"] or not policy.require_stable_eligibility,
            "eligible"
            if eligibility["eligible"]
            else f"failed: {', '.join(eligibility['failed'])}",
            "the stable partition passes its own gate before the holdout counts",
        ),
        _check(
            "holdout_revealed",
            holdout is not None,
            "revealed" if holdout is not None else "not revealed",
            "the final verdict uses stable + holdout evidence",
        ),
        _check(
            "min_paired_seeds",
            pairs["completed_pairs"] >= policy.min_paired_seeds,
            pairs["completed_pairs"],
            f">= {policy.min_paired_seeds} completed pairs (stable + holdout)",
        ),
        _check(
            "min_episodes",
            pairs["episodes"] >= policy.min_episodes,
            pairs["episodes"],
            f">= {policy.min_episodes} episodes in completed pairs",
        ),
        _check(
            "min_stable_paired_seeds",
            stable["pairs"]["completed_pairs"] >= policy.min_stable_paired_seeds,
            stable["pairs"]["completed_pairs"],
            f">= {policy.min_stable_paired_seeds} stable completed pairs",
        ),
        _check(
            "min_holdout_paired_seeds",
            holdout is not None
            and holdout["pairs"]["completed_pairs"] >= policy.min_holdout_paired_seeds,
            holdout["pairs"]["completed_pairs"] if holdout is not None else 0,
            f">= {policy.min_holdout_paired_seeds} holdout completed pairs",
        ),
        *_statistical_checks(combined, policy, ""),
        *_integrity_checks(combined, policy, ""),
        _check(
            "mandatory_scenarios",
            scenarios["status"] == PASS or not policy.require_mandatory_scenarios,
            f"{scenarios['status']}: {scenarios['reason']}",
            "complete mandatory-scenario evidence with no material regression",
        ),
    ]
    timing = combined["timing"]
    call = timing["candidate_agent_call_ms"]
    advisories = [
        _check(
            "calls_within_act_timeout",
            timing["candidate_calls_over_act_timeout"] == 0,
            timing["candidate_calls_over_act_timeout"],
            f"no agent call over actTimeout ({timing['act_timeout_s']} s); such calls drew on "
            "the overage bank (runtime compliance is reviewed separately)",
        ),
        _check(
            "local_time_budget",
            call["p50_ms"] < TIME_BUDGET_MS["p50"]
            and call["p99_ms"] < TIME_BUDGET_MS["p99"]
            and call["max_ms"] < TIME_BUDGET_MS["warn"],
            {k: call[k] for k in ("p50_ms", "p99_ms", "max_ms")},
            "median < 20 ms, p99 < 100 ms, max < 200 ms (TILLA_ARCHITECTURE.md §8)",
        ),
        _check(
            "no_host_suspend",
            timing["host_suspended_episodes"] == 0,
            timing["host_suspended_episodes"],
            "no episode ran across a host sleep",
        ),
    ]
    failed = [c["name"] for c in checks if not c["passed"]]
    return {
        "verdict": PASS if not failed else FAIL,
        "failed_checks": failed,
        "policy": policy.as_dict(),
        "policy_is_tilla_default": policy == TILLA_PROMOTION_POLICY,
        "checks": checks,
        "advisories": advisories,
    }


# ============================================================================================
# Holdout ledger
# ============================================================================================


def holdout_reveal(ledger: Path, manifest: dict, run_dir: Path) -> dict:
    """Log that this run's holdout results are being revealed, and report whether the same
    holdout seeds were revealed before for a different candidate (then they are no longer a
    clean holdout)."""
    part = {p.name: p for p in manifest_partitions(manifest)}[HOLDOUT]
    candidate = manifest["identity"]["candidate"]["digest"]
    others = sorted(
        {
            entry["candidate_id"][:16]
            for entry in read_jsonl(ledger).records
            if entry.get("holdout_seeds") == part.label() and entry.get("candidate_id") != candidate
        }
    )
    Path(ledger).parent.mkdir(parents=True, exist_ok=True)
    append_line(
        ledger,
        {
            "holdout_seeds": part.label(),
            "candidate_id": candidate,
            "incumbent_id": manifest["identity"]["incumbent"]["digest"],
            "run_dir": str(run_dir),
            "revealed_at": _utc_now(),
        },
    )
    return {
        "revealed": True,
        "seeds": part.label(),
        "fresh": not others,
        "previously_revealed_for_candidates": others,
    }


# ============================================================================================
# Reports
# ============================================================================================


def build_report(
    run_dir: Path,
    *,
    reveal_holdout: bool = False,
    scenario_results: Path | None = None,
    ledger: Path = DEFAULT_LEDGER,
) -> dict:
    """Assemble the promotion report under the run's recorded policy.

    The stable partition is always evaluated first. The holdout results file is opened only
    when ``reveal_holdout`` is set *and* the stable partition passed its eligibility gate, so
    tuning workflows can use the default report safely. The report holds no wall-clock data:
    the same results always give the same report.
    """
    run_dir = Path(run_dir).resolve()
    manifest = load_manifest(run_dir)
    policy = manifest_policy(manifest)
    parts = {p.name: p for p in manifest_partitions(manifest)}
    loaded = load_results(run_dir, manifest, [STABLE])
    stable = summarize(loaded.records, [parts[STABLE]])
    eligibility = stable_eligibility(stable, policy)
    records = list(loaded.records)
    holdout = None
    if HOLDOUT not in parts:
        holdout_state = {"revealed": False, "reason": "this run has no holdout partition"}
    elif not reveal_holdout:
        holdout_state = {"revealed": False, "reason": "withheld: --reveal-holdout not given"}
    elif not eligibility["eligible"]:
        holdout_state = {
            "revealed": False,
            "reason": "withheld: the stable partition failed its eligibility gate "
            f"({', '.join(eligibility['failed'])}); a holdout cannot rescue it",
        }
    else:
        hold = load_results(run_dir, manifest, [HOLDOUT])
        loaded.files.update(hold.files)
        loaded.notes.extend(hold.notes)
        holdout = summarize(hold.records, [parts[HOLDOUT]])
        records += hold.records
        holdout_state = holdout_reveal(ledger, manifest, run_dir)
    included = [parts[STABLE]] + ([parts[HOLDOUT]] if holdout is not None else [])
    combined = summarize(records, included)
    scenarios = check_scenarios(scenario_results, manifest, policy)
    gate = evaluate_gate(stable, holdout, combined, eligibility, policy, scenarios, manifest)
    identity = manifest["identity"]
    records.sort(key=_record_order)
    return {
        "schema": SCHEMA_VERSION,
        "scope": "+".join(p.name for p in included),
        "run_dir": str(run_dir),
        "config": manifest["config"],
        "identity": {
            **{
                role: {
                    "spec": identity[role]["spec"],
                    "digest": identity[role]["digest"],
                    "files": identity[role].get("files"),
                }
                for role in ("candidate", "incumbent")
            },
            "environment": identity["environment"],
            "runner_digest": identity["runner"]["digest"],
        },
        "results_files": loaded.files,
        "notes": loaded.notes,
        "stable": {"summary": stable, "eligibility": eligibility},
        "holdout": {"state": holdout_state, "summary": holdout},
        "combined": {"summary": combined},
        "gate": gate,
        "scenarios": scenarios,
        "worst_losses": worst_losses(records, manifest),
        "problem_episodes": [
            episode_diagnostics(r, manifest) for r in records if r["status"] != "ok"
        ],
        "recovered_episodes": [
            episode_diagnostics(r, manifest) for r in records if r.get("prior_attempts")
        ],
    }


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def _check_rows(checks: list[dict], ok: str = "PASS", bad: str = "FAIL") -> list[str]:
    rows = ["| check | result | actual | requirement |", "|---|---|---|---|"]
    rows += [
        f"| {c['name']} | {ok if c['passed'] else bad} | {c['actual']} | {c['requirement']} |"
        for c in checks
    ]
    return rows


def render_markdown(report: dict) -> str:
    g, ident = report["gate"], report["identity"]
    stable, combined = report["stable"], report["combined"]["summary"]
    holdout = report["holdout"]
    env = ident["environment"]
    policy_note = "" if g["policy_is_tilla_default"] else " (non-default policy)"
    lines = [
        f"# Tournament report: {report['scope']}",
        "",
        f"**Promotion verdict: {g['verdict']}**{policy_note}",
        "",
    ]
    if g["failed_checks"]:
        lines += [f"Failed checks: {', '.join(g['failed_checks'])}.", ""]
    lines += [
        "A PASS is promotion evidence only; this tool never promotes or freezes an incumbent.",
        "",
        "Terms: an *episode* is one 720-step game; a *paired seed* is a seed played twice with "
        "the seats swapped; a *completed pair* has exactly one valid episode per candidate "
        "seat. Only completed pairs count.",
        "",
        "## Identity",
        "",
        "| role | entrypoint | digest |",
        "|---|---|---|",
        f"| candidate | `{ident['candidate']['spec']}` | `{ident['candidate']['digest'][:16]}` |",
        f"| incumbent | `{ident['incumbent']['spec']}` | `{ident['incumbent']['digest'][:16]}` |",
        f"| environment | kaggle-environments {env.get('kaggle_environments')}, python "
        f"{env.get('python')} | `{env['digest'][:16]}` |",
        f"| runner | `{report['config']['episode_fn']}` | `{ident['runner_digest'][:16]}` |",
        "",
        "Seed partitions: "
        + ", ".join(
            f"{name} {spec['first']}:{spec['count']} ({spec['count']} paired seeds)"
            for name, spec in report["config"]["partitions"].items()
        )
        + ".",
        "",
        "## Evidence flow: stable, then holdout, then combined",
        "",
        "Stable eligibility: **"
        + ("ELIGIBLE" if stable["eligibility"]["eligible"] else "NOT ELIGIBLE")
        + "**",
        "",
        *_check_rows(stable["eligibility"]["checks"]),
        "",
    ]
    state = holdout["state"]
    if state.get("revealed"):
        lines.append(f"Holdout {state['seeds']}: revealed.")
        if not state.get("fresh"):
            lines.append(
                "> **Holdout reuse:** these holdout seeds were revealed before for candidate(s) "
                + ", ".join(state["previously_revealed_for_candidates"])
                + "; treat them as tuned-on, not as a clean holdout."
            )
    else:
        lines.append(f"Holdout: not revealed ({state['reason']}).")
    lines += ["", "## Promotion checks (combined evidence)", "", *_check_rows(g["checks"]), ""]
    lines += ["Advisories (never change the verdict):", ""]
    lines += [*_check_rows(g["advisories"], "ok", "REVIEW"), ""]
    lines += [
        "## Results",
        "",
        "| scope | completed pairs | episodes | W/L/T | win rate | Wilson 95% | median margin | "
        "mean margin | median pair margin |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    rows = [("stable", stable["summary"])]
    if holdout["summary"] is not None:
        rows.append(("holdout", holdout["summary"]))
    rows.append(("combined", combined))
    for label, s in rows:
        b = s["pairs"]
        lines.append(
            f"| {label} | {b['completed_pairs']} / {s['planned_paired_seeds']} | {b['episodes']} | "
            f"{b['wins']}/{b['losses']}/{b['ties']} | {_pct(b['win_rate'])} | "
            f"[{_pct(b['wilson95'][0])}, {_pct(b['wilson95'][1])}] | {b['median_margin']} | "
            f"{b['mean_margin']} | {b['per_seed']['median_pair_margin']} |"
        )
    lines += ["", "| combined, by candidate seat | episodes | W/L/T | win rate | median margin |"]
    lines += ["|---|---|---|---|---|"]
    lines += [
        f"| seat {k} | {b['episodes']} | {b['wins']}/{b['losses']}/{b['ties']} | "
        f"{_pct(b['win_rate'])} | {b['median_margin']} |"
        for k, b in combined["by_seat"].items()
    ]
    per_seed = combined["pairs"]["per_seed"]
    f = combined["failures"]
    lines += [
        "",
        f"Per completed pair: won/tied/lost {per_seed['pairs_won']}/{per_seed['pairs_tied']}/"
        f"{per_seed['pairs_lost']}; both seats won {per_seed['both_seats_won']}, split "
        f"{per_seed['split']}, both lost {per_seed['both_seats_lost']}.",
        "",
        "## Failures and integrity (combined)",
        "",
        f"- episodes by status: {f['episodes_by_status']}",
        f"- candidate: crashes (ERROR) {f['candidate_crashes']}, environment TIMEOUTs "
        f"{f['candidate_timeouts']}, INVALID episodes {f['candidate_invalid_episodes']}, "
        f"action-shape violations {f['candidate_shape_violations']}",
        f"- incumbent failures (never candidate wins): {f['incumbent_failures_by_status'] or 0}",
        f"- harness errors {f['harness_errors']}, incomplete episodes {f['incomplete_episodes']}, "
        f"duplicate results {f['duplicate_results']}, missing episodes {f['missing_episodes']}",
        f"- episodes recovered by replay after a non-candidate failure: "
        f"{f['recovered_episodes']}; retried after an infrastructure loss or replay: "
        f"{f['retried_episodes']}",
        f"- candidate care losses (ok episodes): {combined['counters']['candidate']}",
        f"- incumbent care losses (ok episodes): {combined['counters']['incumbent']}",
        "",
        "## Mandatory scenarios",
        "",
        f"**{report['scenarios']['status']}**: {report['scenarios']['reason']}",
        "",
        "## Timing (combined)",
        "",
        "Agent call = one `agent(obs)` call as timed by kaggle-environments (the time charged "
        "against `actTimeout`); turn = one environment step (both agents plus the "
        "interpreter); episode = `env.run`. Aggregated quantiles are histogram upper bounds "
        "(about 12% resolution); max is exact. The timeout checks count environment-"
        "enforced TIMEOUT statuses only.",
        "",
        "| measure | n | p50 | p95 | p99 | max | mean |",
        "|---|---|---|---|---|---|---|",
    ]
    t = combined["timing"]
    for label, key in (
        ("candidate agent call (ms)", "candidate_agent_call_ms"),
        ("incumbent agent call (ms)", "incumbent_agent_call_ms"),
        ("whole turn (ms)", "turn_ms"),
    ):
        d = t[key]
        lines.append(
            f"| {label} | {d['n']} | {d['p50_ms']} | {d['p95_ms']} | {d['p99_ms']} | "
            f"{d['max_ms']} | {d['mean_ms']} |"
        )
    for label, key in (
        ("whole episode (s)", "episode_s"),
        ("env make (s)", "env_make_s"),
        ("post-episode analysis + replay write (s)", "post_episode_s"),
        ("worker startup (s)", "worker_startup_s"),
        ("scheduling/IPC overhead per episode (s)", "scheduling_overhead_s"),
    ):
        d = t[key]
        lines.append(f"| {label} | {d['n']} | {d['p50']} | {d['p95']} | | {d['max']} | |")
    lines += [
        "",
        f"- candidate calls over actTimeout ({t['act_timeout_s']} s): "
        f"{t['candidate_calls_over_act_timeout']} in {t['episodes_with_candidate_overage']} "
        f"episodes; overage used {t['candidate_overage_used_s']} s; lowest remaining overage "
        f"bank {t['min_candidate_remaining_overage_s']} s",
        f"- episodes that ran across a host sleep: {t['host_suspended_episodes']}",
        "",
    ]
    if report["notes"]:
        lines += ["## Notes", "", *[f"- {note}" for note in report["notes"]], ""]
    lines += ["## Worst losses", ""]
    if report["worst_losses"]:
        lines += [
            "| episode | candidate bank | incumbent bank | margin | care losses | replay |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {d['key']} | {d['candidate_bank']} | {d['incumbent_bank']} | {d['margin']} | "
            f"{d['counters']} | {d['replay'] or '-'} |"
            for d in report["worst_losses"]
        ]
    else:
        lines.append("None.")
    for title, key in (
        ("Problem episodes", "problem_episodes"),
        ("Recovered episodes", "recovered_episodes"),
    ):
        lines += ["", f"## {title}", ""]
        if report[key]:
            lines += [
                f"- {d['key']}: {d['status']} (attempt {d['attempt']}); candidate failure "
                f"{d['candidate_failure']}; incumbent failure {d['incumbent_failure']}; errors "
                f"{d['validation_errors']}; prior attempts "
                f"{[a['status'] for a in d['prior_attempts']]}; replay {d['replay'] or '-'}"
                for d in report[key][:100]
            ]
        else:
            lines.append("None.")
    lines += [
        "",
        "Every episode is reproducible from its seed and seat with the same entrypoints and "
        "environment digest (see each entry's `regenerate` field in the JSON report).",
        "",
    ]
    return "\n".join(lines)


def write_report(run_dir: Path, report: dict) -> tuple[Path, Path]:
    out = Path(run_dir) / "reports"
    json_path = out / f"report.{report['scope']}.json"
    md_path = out / f"REPORT.{report['scope']}.md"
    atomic_write_text(json_path, json.dumps(report, indent=1, sort_keys=True) + "\n")
    atomic_write_text(md_path, render_markdown(report))
    return json_path, md_path


# ============================================================================================
# CLI
# ============================================================================================


def _add_run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--candidate", default=DEFAULT_CANDIDATE)
    p.add_argument("--incumbent", default=DEFAULT_INCUMBENT)
    p.add_argument(
        "--stable", type=parse_seed_range, metavar="FIRST:COUNT", required=True,
        help="stable paired seeds (COUNT seeds = 2*COUNT episodes)",
    )  # fmt: skip
    group = p.add_mutually_exclusive_group()
    group.add_argument("--holdout", type=parse_seed_range, metavar="FIRST:COUNT")
    group.add_argument(
        "--holdout-epoch",
        type=int,
        metavar="E",
        help=f"standard holdout block {HOLDOUT_BASE} + E * {HOLDOUT_STRIDE}",
    )
    p.add_argument("--holdout-count", type=int, default=500, help="holdout paired seeds")
    p.add_argument("--policy", type=Path, default=None, help="JSON PromotionPolicy override")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--max-episodes-per-worker", type=int, default=50)
    p.add_argument("--episode-timeout", type=float, default=3600.0, help="seconds per episode")
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--max-episodes", type=int, default=None, help="stop after N new episodes")
    p.add_argument(
        "--save-replays", choices=("none", "problems", "losses", "all"), default="problems"
    )
    p.add_argument("--allow-temp", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--episode-fn", default=DEFAULT_EPISODE_FN, help=argparse.SUPPRESS)


def _add_report_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--scenario-results", type=Path, default=None)
    p.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)


def _config_from_args(args) -> RunConfig:
    partitions = [Partition(STABLE, *args.stable)]
    if args.holdout:
        partitions.append(Partition(HOLDOUT, *args.holdout))
    elif args.holdout_epoch is not None:
        partitions.append(holdout_partition(args.holdout_epoch, args.holdout_count))
    policy = TILLA_PROMOTION_POLICY
    if args.policy is not None:
        policy = PromotionPolicy.from_dict(json.loads(args.policy.read_text(encoding="utf-8")))
    return RunConfig(
        partitions=validate_partitions(partitions),
        candidate=args.candidate,
        incumbent=args.incumbent,
        episode_fn=args.episode_fn,
        policy=policy,
    )


def _options_from_args(args) -> RunOptions:
    return RunOptions(
        workers=args.workers,
        max_episodes_per_worker=args.max_episodes_per_worker,
        episode_timeout_s=args.episode_timeout,
        max_attempts=args.max_attempts,
        max_new_episodes=args.max_episodes,
        save_replays=args.save_replays,
        allow_temp=args.allow_temp,
    )


def _report(args, reveal: bool) -> int:
    report = build_report(
        args.run_dir,
        reveal_holdout=reveal,
        scenario_results=args.scenario_results,
        ledger=args.ledger,
    )
    json_path, md_path = write_report(args.run_dir, report)
    print(f"promotion verdict {report['gate']['verdict']} ({report['scope']})")
    if report["gate"]["failed_checks"]:
        print("failed checks: " + ", ".join(report["gate"]["failed_checks"]))
    print(json_path)
    print(md_path)
    return 0 if report["gate"]["verdict"] == PASS else 1


def main_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="create or resume a run (stable first, then holdout)")
    gate_p = sub.add_parser("gate", help="run (resumable), then write the full promotion report")
    status_p = sub.add_parser("status", help="progress and liveness, no outcomes")
    report_p = sub.add_parser("report", help="write reports (stable only unless revealed)")
    for p in (run_p, gate_p, status_p, report_p):
        p.add_argument("--run-dir", type=Path, required=True)
    _add_run_args(run_p)
    _add_run_args(gate_p)
    _add_report_args(gate_p)
    _add_report_args(report_p)
    report_p.add_argument("--reveal-holdout", action="store_true")
    args = parser.parse_args(argv)

    def log(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    try:
        if args.command == "status":
            print(json.dumps(run_status(args.run_dir), indent=1, sort_keys=True))
            return 0
        if args.command == "report":
            return _report(args, args.reveal_holdout)
        outcome = run_tournament(
            args.run_dir, _config_from_args(args), _options_from_args(args), log=log
        )
        print(json.dumps(asdict(outcome), sort_keys=True))
        if outcome.state == "interrupted":
            return 130
        if outcome.state not in ("finished", "stable_ineligible"):
            return 3
        if args.command == "gate":
            return _report(args, reveal=True)
        return 0 if outcome.state == "finished" else 1
    except (TournamentError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


# --- Milestone 5-era gate helpers (kept for compatibility) -------------------------------------
#
# The minimal promotion-gate runner used for the recorded Milestone 5-7 gates (their offline
# drivers import ``_play``, ``gate_report`` and ``gate_passes``; tests pin ``wilson_lower_bound``
# and ``gate_passes``). The Milestone 8 pipeline above (``tournament gate``) supersedes it for
# new runs; these helpers are unchanged from the accepted Milestone 7 main.


def wilson_lower_bound(wins: int, games: int, z: float = Z_95) -> float:
    """Two-sided 95% Wilson score interval, lower bound (ties count as non-wins)."""
    if games == 0:
        return 0.0
    p = wins / games
    denom = 1 + z * z / games
    centre = p + z * z / (2 * games)
    spread = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games))
    return (centre - spread) / denom


def _play(args: tuple[int, int]) -> dict:
    seed, seat = args
    import main
    from agents.incumbent import agent as incumbent
    from tools.harness import run_game

    result = run_game(main.agent, incumbent, seed, seat)
    result.pop("duplicate_events", None)
    return result


def run(
    first: int, last: int, workers: int, out: str | None = None, resume: bool = False
) -> list[dict]:
    """Play every (seed, seat) job across ``workers`` processes. With ``out``
    each finished game is appended immediately as one JSON line, so a long
    gate survives interruption; ``resume`` skips games already in ``out``."""
    done: dict[tuple[int, int], dict] = {}
    if out and resume:
        try:
            with open(out, encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    done[(r["seed"], r["seat"])] = r
        except FileNotFoundError:
            pass
    jobs = [
        (seed, seat)
        for seed in range(first, last + 1)
        for seat in (0, 1)
        if (seed, seat) not in done
    ]
    results = list(done.values())
    sink = open(out, "a", encoding="utf-8") if out else None
    try:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers, max_tasks_per_child=50) as pool:
            for i, result in enumerate(pool.map(_play, jobs, chunksize=1), start=1):
                results.append(result)
                if sink:
                    sink.write(json.dumps(result) + "\n")
                    sink.flush()
                if i % 50 == 0 or i == len(jobs):
                    print(f"{i}/{len(jobs)} games done", file=sys.stderr, flush=True)
    finally:
        if sink:
            sink.close()
    return results


def gate_report(results: list[dict]) -> dict:
    margins = [r["candidate"] - r["opponent"] for r in results]
    wins = sum(m > 0 for m in margins)
    ties = sum(m == 0 for m in margins)
    losses = sum(m < 0 for m in margins)
    pairs: dict[int, float] = {}
    for r in results:
        pairs[r["seed"]] = pairs.get(r["seed"], 0.0) + r["candidate"] - r["opponent"]
    paired = sorted(pairs.items(), key=lambda kv: kv[1])

    def seat(s, sign):
        return sum(1 for r in results if r["seat"] == s and sign(r["candidate"] - r["opponent"]))

    def care(key):
        return sum(r.get(key, 0) for r in results)

    return {
        "games": len(results),
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "win_rate": wins / len(results) if results else 0.0,
        "wilson_lower_95": wilson_lower_bound(wins, len(results)),
        "seat0": [seat(0, lambda m: m > 0), seat(0, lambda m: m == 0), seat(0, lambda m: m < 0)],
        "seat1": [seat(1, lambda m: m > 0), seat(1, lambda m: m == 0), seat(1, lambda m: m < 0)],
        "median_margin": statistics.median(margins) if margins else 0.0,
        "mean_margin": statistics.mean(margins) if margins else 0.0,
        "min_margin": min(margins, default=0.0),
        "max_margin": max(margins, default=0.0),
        "candidate_bank": [
            min(r["candidate"] for r in results),
            max(r["candidate"] for r in results),
        ],
        "incumbent_bank": [
            min(r["opponent"] for r in results),
            max(r["opponent"] for r in results),
        ],
        "paired_positive": sum(1 for _, m in paired if m > 0),
        "paired_zero": sum(1 for _, m in paired if m == 0),
        "paired_negative": sum(1 for _, m in paired if m < 0),
        "median_paired_margin": statistics.median([m for _, m in paired]) if paired else 0.0,
        "worst_paired_seeds": paired[:10],
        "crashes": sum(r["ERROR"] + r["INVALID"] for r in results),
        "timeouts": sum(r["TIMEOUT"] for r in results),
        "malformed": sum(r["malformed"] for r in results),
        "parse_failures": sum(r["parse_failures"] for r in results),
        "all_complete": all(
            r["steps"] == 720 and r["statuses"] == ["DONE", "DONE"] for r in results
        ),
        "crops_lost_unwatered": care("crops_lost_unwatered"),
        "fresh_plantings_unwatered": care("fresh_plantings_unwatered"),
        "animals_escaped": care("animals_escaped"),
        "crops_lost_decay": care("crops_lost_decay"),
        "duplicate_single_use_assignments": care("duplicate_single_use_assignments"),
        "duplicate_single_use_actions": care("duplicate_single_use_actions"),
        "same_turn_noops": care("same_turn_noops"),
        "hand_pass_rate": statistics.mean(r["hand_pass_rate"] for r in results) if results else 0.0,
        "ms_median": statistics.median(r["ms_median"] for r in results) if results else 0.0,
        "ms_p95_max": max((r["ms_p95"] for r in results), default=0.0),
        "ms_p99_max": max((r["ms_p99"] for r in results), default=0.0),
        "ms_max": max((r["ms_max"] for r in results), default=0.0),
        "premium_investments_while_glutted": care("premium_investments_while_glutted"),
        "glut_protection_rejections": care("glut_protection_rejections"),
        "town_model_changed_sales": care("town_model_changed_sales"),
        "future_shops_changed_sales": care("future_shops_changed_sales"),
        "sales_checks": care("sales_checks"),
        "premium": _premium(results),
    }


def _premium(results: list[dict]) -> dict:
    from kaggriculture_bot.constants import PREMIUM_PRODUCTS

    out = {}
    for product in PREMIUM_PRODUCTS:
        sold = Counter()
        revenue = 0
        mins = []
        for r in results:
            sales = r["realized_sales"]
            sold[product] += sales["units_sold"].get(product, 0)
            revenue += sales["revenue"].get(product, 0)
            if product in sales["min_price"]:
                mins.append(sales["min_price"][product])
        units = sold[product]
        out[product] = {
            "units_sold": units,
            "units_held_at_end": sum(r["final_shed"].get(product, 0) for r in results),
            "avg_price": round(revenue / units, 1) if units else None,
            "min_price": min(mins) if mins else None,
            "floor_sales": sum(r["realized_sales"]["floor_sales"].get(product, 0) for r in results),
        }
    return out


def gate_passes(report: dict) -> bool:
    return (
        report["win_rate"] > 0.53
        and report["wilson_lower_95"] > 0.50
        and report["median_margin"] > 0
        and report["crashes"] == 0
        and report["timeouts"] == 0
    )


if __name__ == "__main__":
    sys.exit(main_cli())
