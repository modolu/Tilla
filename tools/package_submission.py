"""Deterministic Kaggle submission packaging and validation (offline only).

The submission contract (TILLA_ARCHITECTURE.md §6, §11; TILLA_RULES.md §22) is
a ``.tar.gz`` whose archive root holds exactly::

    main.py                     # exposes agent(obs)
    kaggriculture_bot/*.py      # the flat runtime package (with __init__.py)

and nothing else: no tests, benchmarks, tools, agents, docs, caches, VCS or
virtualenv files. This module builds that archive reproducibly and validates
an archive the way Kaggle will use it:

* source checks: required files present, no symlinks, no unexpected files in
  the package directory (``__pycache__``/``*.pyc`` are skipped), no
  case-colliding paths;
* archive checks: regular files only, safe relative paths (no absolute paths,
  ``..``, backslashes), the exact contract layout, forbidden-file rejection,
  duplicate and case-collision rejection;
* static runtime audit of the packaged sources: Python 3.11 syntax, imports
  limited to the standard library and the package itself, no network,
  subprocess or dynamic-execution modules/calls, no file access, no local
  absolute paths;
* clean-process import: the archive is extracted to a fresh temporary
  directory and imported by a new ``python -I -S`` process (isolated mode, no
  ``site``: no site-packages, no editable installs, no current directory), so
  every module must resolve from the archive alone; any loaded module outside
  the extraction directory or the standard library is reported as leakage; an
  optional observation fixture is passed to ``agent`` as a smoke call.

Determinism: entries are sorted, tar metadata (mtime, uid/gid, names, modes)
is normalized and the gzip header carries no name or timestamp, so identical
sources give a byte-identical archive; the file manifest (path, size,
SHA-256) is reported alongside the archive SHA-256.

    python -m tools.package_submission [OUT.tar.gz] [--fixture OBS.json] [--no-smoke]
    python -m tools.package_submission --validate-only ARCHIVE.tar.gz [--fixture OBS.json]

Exit status 0 only if the build (if any) and every validation step pass.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_PACKAGE = "kaggriculture_bot"
RUNTIME_ENTRYPOINT = "main.py"
PYTHON_TARGET = (3, 11)
DEFAULT_SMOKE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "obs_midgame_p1_populated.json"

# Fixed metadata so the same source always produces byte-identical archives.
_FIXED_MTIME = 0

# Anything matching these in an archive is a packaging mistake.
FORBIDDEN_PARTS = {
    "tests",
    "benchmarks",
    "tools",
    "agents",
    "docs",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
}
FORBIDDEN_NAMES = {".env", "kaggle.json", ".DS_Store"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".md", ".jsonl", ".log", ".patch", ".tar", ".gz", ".zip"}

# Runtime import policy (TILLA_ARCHITECTURE.md §2, §15): stdlib only, and not these.
FORBIDDEN_MODULES = {
    # network, processes, foreign code, dynamic imports
    "socket",
    "ssl",
    "http",
    "urllib",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "xmlrpc",
    "asyncio",
    "subprocess",
    "multiprocessing",
    "ctypes",
    "importlib",
    # offline repository code and development-only packages
    "tools",
    "tests",
    "benchmarks",
    "agents",
    "kaggle_environments",
    "pytest",
}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "breakpoint", "input"}
FORBIDDEN_ATTRIBUTES = {("os", "system"), ("os", "popen"), ("os", "environ"), ("os", "getenv")}
ABSOLUTE_PATH_PREFIXES = ("/Users/", "/home/", "/private/", "/tmp/", "/var/", "/root/", "C:\\")


class PackagingError(Exception):
    """The sources or the archive violate the submission contract."""


@dataclass
class ValidationReport:
    archive: str
    archive_sha256: str
    archive_bytes: int
    uncompressed_bytes: int
    manifest: list[dict]
    manifest_sha256: str
    audit_problems: list[str] = field(default_factory=list)
    import_check: dict = field(default_factory=dict)
    smoke: dict | None = None

    @property
    def ok(self) -> bool:
        smoke_ok = self.smoke is None or bool(self.smoke.get("ok"))
        return not self.audit_problems and bool(self.import_check.get("ok")) and smoke_ok

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "archive": self.archive,
            "archive_sha256": self.archive_sha256,
            "archive_bytes": self.archive_bytes,
            "uncompressed_bytes": self.uncompressed_bytes,
            "files": len(self.manifest),
            "manifest_sha256": self.manifest_sha256,
            "manifest": self.manifest,
            "audit_problems": self.audit_problems,
            "import_check": self.import_check,
            "smoke": self.smoke,
        }


# --- Sources ------------------------------------------------------------------------------


def runtime_source_files(repo_root: Path = REPO_ROOT) -> list[Path]:
    """The runtime source files, relative to ``repo_root``, in archive order:
    ``main.py`` then the package modules sorted by name. Raises PackagingError
    when the source tree cannot produce a valid submission."""
    repo_root = Path(repo_root)
    entry = repo_root / RUNTIME_ENTRYPOINT
    package_dir = repo_root / RUNTIME_PACKAGE
    if entry.is_symlink() or not entry.is_file():
        raise PackagingError(f"missing or non-regular {RUNTIME_ENTRYPOINT} at the repository root")
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise PackagingError(f"missing or non-regular package directory {RUNTIME_PACKAGE}/")
    if not (package_dir / "__init__.py").is_file():
        raise PackagingError(f"{RUNTIME_PACKAGE}/__init__.py is missing")
    files = [Path(RUNTIME_ENTRYPOINT)]
    unexpected = []
    for child in sorted(package_dir.iterdir(), key=lambda p: p.name):
        if child.name == "__pycache__" or child.suffix in (".pyc", ".pyo"):
            continue  # caches are never packaged
        if child.is_symlink():
            raise PackagingError(f"symlink in the runtime package: {child.name}")
        if child.is_file() and child.suffix == ".py":
            files.append(child.relative_to(repo_root))
        else:
            unexpected.append(child.name)
    if unexpected:
        raise PackagingError(f"unexpected files in {RUNTIME_PACKAGE}/: {unexpected}")
    _check_case_collisions([f.as_posix() for f in files])
    return files


def _check_case_collisions(names: list[str]) -> None:
    seen: dict[str, str] = {}
    for name in names:
        key = name.lower()
        if key in seen and seen[key] != name:
            raise PackagingError(f"case-colliding paths: {seen[key]} / {name}")
        seen[key] = name


# --- Build --------------------------------------------------------------------------------------


def _normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.mtime = _FIXED_MTIME
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o644 if info.isfile() else 0o755
    return info


def build_submission_archive(destination: Path, repo_root: Path = REPO_ROOT) -> Path:
    """Write the submission archive to ``destination`` and return its path."""
    destination = Path(destination)
    repo_root = Path(repo_root)
    files = runtime_source_files(repo_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Write gzip ourselves so the header carries no filename or timestamp.
    with (
        open(destination, "wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=_FIXED_MTIME) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar,
    ):
        for relative in files:
            tar.add(repo_root / relative, arcname=relative.as_posix(), filter=_normalize)
    return destination


# --- Archive validation -----------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _member_problems(name: str) -> list[str]:
    problems = []
    if name.startswith("/") or "\\" in name or name.startswith("./"):
        problems.append(f"unsafe path {name!r}")
    path = PurePosixPath(name)
    if ".." in path.parts:
        problems.append(f"path traversal {name!r}")
    if set(path.parts) & FORBIDDEN_PARTS:
        problems.append(f"forbidden path {name!r}")
    if path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
        problems.append(f"forbidden file {name!r}")
    return problems


def read_archive(archive: Path) -> dict[str, bytes]:
    """Validate the archive structure and return {path: bytes} of its files.
    Raises PackagingError for anything that is not exactly the contract."""
    problems: list[str] = []
    contents: dict[str, bytes] = {}
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                problems += _member_problems(member.name)
                if not member.isfile():
                    if member.issym():
                        kind = "symlink"
                    elif member.islnk():
                        kind = "hardlink"
                    else:
                        kind = "non-file"
                    problems.append(f"{kind} entry {member.name!r}")
                    continue
                if member.name in contents:
                    problems.append(f"duplicate entry {member.name!r}")
                    continue
                handle = tar.extractfile(member)
                contents[member.name] = handle.read() if handle else b""
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise PackagingError(f"malformed archive: {exc}") from exc
    try:
        _check_case_collisions(list(contents))
    except PackagingError as exc:
        problems.append(str(exc))
    if RUNTIME_ENTRYPOINT not in contents:
        nested = [n for n in contents if n.endswith("/" + RUNTIME_ENTRYPOINT)]
        where = f" (found nested: {nested})" if nested else ""
        problems.append(f"{RUNTIME_ENTRYPOINT} is not at the archive root{where}")
    if f"{RUNTIME_PACKAGE}/__init__.py" not in contents:
        problems.append(f"{RUNTIME_PACKAGE}/ package is missing")
    for name in contents:
        path = PurePosixPath(name)
        in_package = (
            len(path.parts) == 2 and path.parts[0] == RUNTIME_PACKAGE and path.suffix == ".py"
        )
        if name != RUNTIME_ENTRYPOINT and not in_package:
            problems.append(f"unexpected file {name!r}")
    if problems:
        raise PackagingError("; ".join(sorted(set(problems))))
    return contents


def manifest_of(contents: dict[str, bytes]) -> list[dict]:
    return [
        {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(contents.items())
    ]


def manifest_sha256(manifest: list[dict]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()


# --- Static runtime audit ------------------------------------------------------------------------


def audit_runtime_sources(contents: dict[str, bytes]) -> list[str]:
    """Problems in the packaged runtime sources (empty list = clean)."""
    problems: list[str] = []
    own = {RUNTIME_PACKAGE, PurePosixPath(RUNTIME_ENTRYPOINT).stem}
    stdlib = frozenset(sys.stdlib_module_names) | {"__future__"}
    target = f"{PYTHON_TARGET[0]}.{PYTHON_TARGET[1]}"
    for name, data in sorted(contents.items()):
        try:
            tree = ast.parse(data.decode("utf-8"), filename=name, feature_version=PYTHON_TARGET)
        except (UnicodeDecodeError, SyntaxError) as exc:
            problems.append(f"{name}: not valid Python {target}: {exc}")
            continue
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if root in FORBIDDEN_MODULES:
                    problems.append(f"{name}:{node.lineno}: forbidden import {root!r}")
                elif root not in stdlib and root not in own:
                    problems.append(f"{name}:{node.lineno}: non-stdlib import {root!r}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_CALLS:
                    problems.append(f"{name}:{node.lineno}: forbidden call {node.func.id}()")
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if (node.value.id, node.attr) in FORBIDDEN_ATTRIBUTES:
                    problems.append(f"{name}:{node.lineno}: forbidden {node.value.id}.{node.attr}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.startswith(ABSOLUTE_PATH_PREFIXES):
                    problems.append(f"{name}:{node.lineno}: local absolute path {node.value!r}")
    return problems


# --- Clean-process import and smoke ------------------------------------------------------------

_CHILD = r"""
import json, os, sys, sysconfig
root = os.path.realpath(sys.argv[1])
paths = sysconfig.get_paths()
stdlib = {os.path.realpath(paths["stdlib"]), os.path.realpath(paths["platstdlib"])}
sys.path.insert(0, root)
out = {"python": list(sys.version_info[:3]),
       "flags": {"isolated": sys.flags.isolated, "no_site": sys.flags.no_site}}
try:
    import main
    import kaggriculture_bot
    import kaggriculture_bot.strategy
except Exception as exc:
    out.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    print(json.dumps(out))
    sys.exit(0)
leaks, own = [], []
for name, mod in sorted(sys.modules.items()):
    path = getattr(mod, "__file__", None)
    if not path:
        continue
    real = os.path.realpath(path)
    if real.startswith(root + os.sep):
        own.append(name)
    elif not any(real.startswith(s + os.sep) for s in stdlib):
        leaks.append(f"{name} -> {real}")
out.update(ok=not leaks and callable(getattr(main, "agent", None)), leaks=leaks, own=own,
           main_file=os.path.relpath(os.path.realpath(main.__file__), root),
           package_file=os.path.relpath(os.path.realpath(kaggriculture_bot.__file__), root))
fixture = sys.stdin.read()
if fixture:
    action = main.agent(json.loads(fixture))
    shape = (isinstance(action, dict) and set(action) == {"farmer", "hands", "market"}
             and isinstance(action["farmer"], list) and isinstance(action["hands"], list)
             and isinstance(action["market"], list) and len(action["market"]) <= 10)
    out["smoke"] = {"ok": bool(shape), "action": action}
print(json.dumps(out))
"""


def clean_import_check(
    extract_dir: Path, fixture: Path | None = None, python: str = sys.executable
) -> dict:
    """Import the extracted runtime in a fresh isolated, site-less process."""
    stdin = Path(fixture).read_text(encoding="utf-8") if fixture else ""
    proc = subprocess.run(
        [python, "-I", "-S", "-B", "-c", _CHILD, str(extract_dir)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(extract_dir),
        env={"PATH": os.environ.get("PATH", "")},
        timeout=120,
    )
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        return {"ok": False, "error": f"exit {proc.returncode}: {proc.stderr.strip()[-2000:]}"}
    result = json.loads(lines[-1])
    if tuple(result.get("python", ())[:2]) < PYTHON_TARGET:
        result["ok"] = False
        result["error"] = f"python {result['python']} is older than {PYTHON_TARGET}"
    return result


def validate_archive(archive: Path, fixture: Path | None = None) -> ValidationReport:
    """Structure + manifest + static audit + clean-process import (+ smoke)."""
    archive = Path(archive)
    contents = read_archive(archive)  # raises PackagingError on contract violations
    manifest = manifest_of(contents)
    report = ValidationReport(
        archive=str(archive),
        archive_sha256=sha256_file(archive),
        archive_bytes=archive.stat().st_size,
        uncompressed_bytes=sum(len(d) for d in contents.values()),
        manifest=manifest,
        manifest_sha256=manifest_sha256(manifest),
        audit_problems=audit_runtime_sources(contents),
    )
    with tempfile.TemporaryDirectory(prefix="tilla-submission-") as tmp:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        result = clean_import_check(Path(tmp), fixture)
    smoke = result.pop("smoke", None)
    if fixture is not None:
        report.smoke = smoke or {"ok": False, "error": "smoke call did not run"}
    report.import_check = result
    return report


# --- CLI ------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and validate the Kaggle submission archive."
    )
    parser.add_argument("destination", nargs="?", default=str(REPO_ROOT / "submission.tar.gz"))
    parser.add_argument("--validate-only", metavar="ARCHIVE", help="validate an existing archive")
    parser.add_argument("--fixture", help="observation JSON for the smoke agent() call")
    parser.add_argument("--no-smoke", action="store_true", help="skip the smoke agent() call")
    args = parser.parse_args(argv)
    fixture = None
    if not args.no_smoke:
        fixture = Path(args.fixture) if args.fixture else DEFAULT_SMOKE_FIXTURE
        if not fixture.is_file():
            fixture = None
    try:
        if args.validate_only:
            archive = Path(args.validate_only)
        else:
            archive = build_submission_archive(Path(args.destination))
        report = validate_archive(archive, fixture)
    except PackagingError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=1))
        return 1
    print(json.dumps(report.as_dict(), indent=1, default=str))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
