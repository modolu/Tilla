"""Milestone 0 submission-contract tests: main.agent, packaging, stdlib-only runtime."""

import ast
import copy
import importlib
import importlib.util
import json
import sys
import tarfile
from pathlib import Path

import pytest

import main
from tests.test_actions import assert_valid_action_shape
from tools.package_submission import (
    RUNTIME_ENTRYPOINT,
    RUNTIME_PACKAGE,
    build_submission_archive,
    runtime_source_files,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_FILES = [REPO_ROOT / "main.py", *sorted((REPO_ROOT / "kaggriculture_bot").glob("*.py"))]


# --- main.agent contract -----------------------------------------------------


def test_main_agent_is_importable_callable():
    module = importlib.import_module("main")
    assert callable(module.agent)


def test_agent_returns_pass_shape_with_no_hands(obs_no_hands):
    action = main.agent(obs_no_hands)
    assert_valid_action_shape(action, expected_hands=0)
    assert action == {"farmer": ["PASS"], "hands": [], "market": []}


def test_agent_returns_one_pass_per_hired_hand(obs_two_hands):
    action = main.agent(obs_two_hands)
    assert_valid_action_shape(action, expected_hands=2)
    assert action["farmer"] == ["PASS"]
    assert action["hands"] == [["PASS"], ["PASS"]]
    assert action["market"] == []


def test_agent_uses_observing_players_farm_for_hand_count(obs_two_hands):
    """Player 1 with no hands must not inherit player 0's two hands."""
    obs = copy.deepcopy(obs_two_hands)
    obs["player"] = 1
    action = main.agent(obs)
    assert action["hands"] == []


def test_agent_is_deterministic_and_does_not_mutate_observation(obs_two_hands):
    before = json.dumps(obs_two_hands, sort_keys=True)
    first = main.agent(obs_two_hands)
    second = main.agent(obs_two_hands)
    assert first == second
    assert json.dumps(obs_two_hands, sort_keys=True) == before


@pytest.mark.parametrize(
    "bad_obs",
    [
        None,
        {},
        [],
        "not an obs",
        {"player": 0},
        {"player": 0, "farms": None},
        {"player": 5, "farms": [{}]},
    ],
)
def test_agent_returns_valid_pass_for_malformed_observation(bad_obs):
    action = main.agent(bad_obs)
    assert_valid_action_shape(action, expected_hands=0)
    assert action["farmer"] == ["PASS"]


def test_agent_hand_count_matches_official_environment_after_hire():
    """Hire hands through the real environment and check our hands list tracks them."""
    from kaggle_environments import make

    env = make("kaggriculture", configuration={"episodeSteps": 720, "seed": 3}, debug=True)
    env.reset()
    pass_action = {"farmer": ["PASS"], "hands": [], "market": []}
    env.step([{"farmer": ["PASS"], "hands": [], "market": [["HIRE"], ["HIRE"]]}, pass_action])
    obs_p0 = env.state[0].observation
    obs_p1 = env.state[1].observation
    assert len(obs_p0["farms"][0]["hands"]) == 2
    assert len(obs_p0["farms"][1]["hands"]) == 0

    action_p0 = main.agent(obs_p0)
    action_p1 = main.agent(obs_p1)
    assert action_p0["hands"] == [["PASS"], ["PASS"]]
    assert action_p1["hands"] == []
    # The environment must accept our shaped actions without error.
    env.step([action_p0, action_p1])
    assert all(s.status in ("ACTIVE", "INACTIVE") for s in env.state)


# --- stdlib-only runtime -----------------------------------------------------


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize("path", RUNTIME_FILES, ids=lambda p: p.name)
def test_runtime_modules_import_only_stdlib_and_own_package(path):
    allowed = set(sys.stdlib_module_names) | {RUNTIME_PACKAGE}
    forbidden = _top_level_imports(path) - allowed
    assert not forbidden, f"{path.name} imports non-stdlib modules: {sorted(forbidden)}"


@pytest.mark.parametrize("path", RUNTIME_FILES, ids=lambda p: p.name)
def test_runtime_modules_never_import_offline_directories(path):
    assert not _top_level_imports(path) & {"tools", "tests", "benchmarks", "agents"}


# --- packaging smoke test ----------------------------------------------------


def test_runtime_source_files_are_entrypoint_plus_package():
    files = [str(p) for p in runtime_source_files(REPO_ROOT)]
    assert files[0] == RUNTIME_ENTRYPOINT
    assert all(f.startswith(f"{RUNTIME_PACKAGE}/") and f.endswith(".py") for f in files[1:])
    assert f"{RUNTIME_PACKAGE}/__init__.py" in files


def test_packaged_archive_has_main_at_root_and_runs_agent(tmp_path, obs_two_hands):
    archive = build_submission_archive(tmp_path / "submission.tar.gz", REPO_ROOT)

    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert "main.py" in names, names
    assert f"{RUNTIME_PACKAGE}/__init__.py" in names
    forbidden_prefixes = ("tools", "tests", "benchmarks", "agents", ".venv", ".git")
    assert not [n for n in names if n.startswith(forbidden_prefixes)], names
    assert not [n for n in names if n.startswith("/") or ".." in n], names

    extract_dir = tmp_path / "extracted"
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(extract_dir, filter="data")
    assert (extract_dir / "main.py").is_file()

    # Import the extracted copy in isolation from the repository checkout.
    saved_modules = {
        k: v for k, v in sys.modules.items() if k == "main" or k.startswith(RUNTIME_PACKAGE)
    }
    for name in saved_modules:
        del sys.modules[name]
    sys.path.insert(0, str(extract_dir))
    try:
        extracted_main = importlib.import_module("main")
        assert Path(extracted_main.__file__).resolve().is_relative_to(extract_dir.resolve())
        action = extracted_main.agent(obs_two_hands)
    finally:
        sys.path.remove(str(extract_dir))
        for name in list(sys.modules):
            if name == "main" or name.startswith(RUNTIME_PACKAGE):
                del sys.modules[name]
        sys.modules.update(saved_modules)
    assert action == {"farmer": ["PASS"], "hands": [["PASS"], ["PASS"]], "market": []}


def test_packaging_is_deterministic(tmp_path):
    first = build_submission_archive(tmp_path / "a.tar.gz", REPO_ROOT).read_bytes()
    second = build_submission_archive(tmp_path / "b.tar.gz", REPO_ROOT).read_bytes()
    assert first == second
