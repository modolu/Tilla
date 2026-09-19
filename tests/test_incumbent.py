"""Guards for the frozen Milestone 4 incumbent snapshot (agents/incumbent_m4).

The incumbent is comparison code: it must stay byte-identical to the freeze
and must never import the mutable candidate runtime.
"""

import ast
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "agents" / "incumbent_m4"
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
    "path", sorted(SNAPSHOT.glob("*.py")) + [ROOT / "agents" / "incumbent.py"], ids=lambda p: p.name
)
def test_incumbent_never_imports_the_candidate_runtime(path):
    assert not (_imports(path) & FORBIDDEN_ROOTS), path


def test_incumbent_snapshot_matches_its_manifest():
    """agents/incumbent_m4 is frozen: every file hashes to the recorded value and
    no file was added or removed."""
    manifest = {}
    for line in (SNAPSHOT / "MANIFEST.sha256").read_text().splitlines():
        digest, name = line.split()
        manifest[name] = digest
    files = {p.name for p in SNAPSHOT.glob("*.py")}
    assert files == set(manifest)
    for name, digest in manifest.items():
        assert hashlib.sha256((SNAPSHOT / name).read_bytes()).hexdigest() == digest, name


def test_incumbent_adapter_exposes_the_snapshot_agent():
    from agents import incumbent
    from agents.incumbent_m4 import agent as snapshot

    assert incumbent.agent is snapshot.agent
    # The snapshot keeps its own episode memory, separate from the candidate's.
    from agents.incumbent_m4 import runtime as inc_runtime
    from kaggriculture_bot import runtime as cand_runtime

    assert inc_runtime._MEMORIES is not cand_runtime._MEMORIES


def test_incumbent_returns_legal_actions_for_official_fixtures():
    from agents.incumbent import agent
    from kaggriculture_bot.validator import validate_or_fallback
    from tests.conftest import OFFICIAL_FIXTURES, load_fixture

    for name in OFFICIAL_FIXTURES:
        obs = load_fixture(name)
        action = agent(obs)
        hands = len(obs["farms"][obs["player"]]["hands"])
        assert validate_or_fallback(action, hands) == action
