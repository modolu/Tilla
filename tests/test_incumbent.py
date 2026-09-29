"""Guards for the frozen incumbent snapshots (agents/incumbent_m4, _m5, _m6).

Incumbents are comparison code: each must stay byte-identical to its freeze
and must never import the mutable candidate runtime. ``agents.incumbent`` is
the current champion (Milestone 6).
"""

import ast
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = tuple(ROOT / "agents" / f"incumbent_m{m}" for m in (4, 5, 6))
FORBIDDEN_ROOTS = {"kaggriculture_bot", "main", "tools", "tests", "benchmarks"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize(
    "path",
    [p for snap in SNAPSHOTS for p in sorted(snap.glob("*.py"))]
    + [ROOT / "agents" / "incumbent.py"],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_incumbent_never_imports_the_candidate_runtime(path):
    assert not (_imports(path) & FORBIDDEN_ROOTS), path


@pytest.mark.parametrize("snapshot", SNAPSHOTS, ids=lambda p: p.name)
def test_incumbent_snapshot_matches_its_manifest(snapshot):
    """Each snapshot is frozen: every file hashes to the recorded value and no
    file was added or removed."""
    manifest = {}
    for line in (snapshot / "MANIFEST.sha256").read_text().splitlines():
        digest, name = line.split()
        manifest[name] = digest
    files = {p.name for p in snapshot.glob("*.py")}
    assert files == set(manifest)
    for name, digest in manifest.items():
        assert hashlib.sha256((snapshot / name).read_bytes()).hexdigest() == digest, name


def test_incumbent_adapter_exposes_the_snapshot_agent():
    from agents import incumbent
    from agents.incumbent_m4 import runtime as m4_runtime
    from agents.incumbent_m5 import runtime as m5_runtime
    from agents.incumbent_m6 import agent as snapshot
    from agents.incumbent_m6 import runtime as m6_runtime
    from kaggriculture_bot import runtime as cand_runtime

    assert incumbent.agent is snapshot.agent
    # Every snapshot keeps its own episode memory, separate from the candidate's.
    memories = {
        id(m4_runtime._MEMORIES),
        id(m5_runtime._MEMORIES),
        id(m6_runtime._MEMORIES),
        id(cand_runtime._MEMORIES),
    }
    assert len(memories) == 4


@pytest.mark.parametrize("module", [f"agents.incumbent_m{m}.agent" for m in (4, 5, 6)])
def test_incumbent_returns_legal_actions_for_official_fixtures(module):
    import importlib

    from kaggriculture_bot.validator import validate_or_fallback
    from tests.conftest import OFFICIAL_FIXTURES, load_fixture

    agent = importlib.import_module(module).agent
    for name in OFFICIAL_FIXTURES:
        obs = load_fixture(name)
        action = agent(obs)
        hands = len(obs["farms"][obs["player"]]["hands"])
        assert validate_or_fallback(action, hands) == action


@pytest.mark.parametrize("milestone, commit", [(5, "5c3f1ef"), (6, "21195e7")])
def test_snapshot_is_the_accepted_runtime(milestone, commit):
    """agents/incumbent_m<N> is the accepted runtime of that milestone with only
    its imports namespaced: same source modulo the package prefix."""
    import subprocess

    package = f"agents.incumbent_m{milestone}"
    snap = ROOT / "agents" / f"incumbent_m{milestone}"
    pairs = {"agent.py": "main.py"}
    for path in sorted(snap.glob("*.py")):
        if path.name == "__init__.py":
            continue
        src = pairs.get(path.name, f"kaggriculture_bot/{path.name}")
        try:
            accepted = subprocess.run(
                ["git", "show", f"{commit}:{src}"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            pytest.skip("git history unavailable")
        expected = "".join(
            line.replace("kaggriculture_bot", package)
            if line.startswith(("from ", "import "))
            else line
            for line in accepted.splitlines(keepends=True)
        )
        assert path.read_text(encoding="utf-8") == expected, path.name
