"""Milestone 10 prework: submission packaging and validation (tools/package_submission.py).

Covers the archive contract (main.py + kaggriculture_bot/ at the root, nothing
else), source checks, archive rejection cases, the static runtime dependency
audit, clean-process import from the extracted archive alone (leakage
detection), deterministic builds and size reporting.
"""

import io
import os
import shutil
import tarfile
from pathlib import Path

import pytest

from tools import package_submission as ps
from tools.package_submission import (
    PackagingError,
    audit_runtime_sources,
    build_submission_archive,
    clean_import_check,
    read_archive,
    runtime_source_files,
    validate_archive,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "obs_midgame_p1_populated.json"
RUNTIME = ["main.py"] + sorted(
    f"kaggriculture_bot/{p.name}" for p in (REPO_ROOT / "kaggriculture_bot").glob("*.py")
)


def fake_repo(tmp_path: Path) -> Path:
    """A copy of the runtime sources in a fresh directory."""
    root = tmp_path / "repo"
    (root / "kaggriculture_bot").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "main.py", root / "main.py")
    for src in (REPO_ROOT / "kaggriculture_bot").glob("*.py"):
        shutil.copy2(src, root / "kaggriculture_bot" / src.name)
    return root


def craft(path: Path, entries) -> Path:
    """Write a .tar.gz with ``entries``: (name, bytes) regular files or TarInfo objects."""
    with tarfile.open(path, "w:gz") as tar:
        for entry in entries:
            if isinstance(entry, tarfile.TarInfo):
                tar.addfile(entry)
            else:
                name, data = entry
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return path


def runtime_entries():
    return [(name, (REPO_ROOT / name).read_bytes()) for name in RUNTIME]


# --- Contract layout ---------------------------------------------------------------------------


def test_archive_root_is_exactly_main_and_the_package(tmp_path):
    archive = build_submission_archive(tmp_path / "s.tar.gz", REPO_ROOT)
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
        assert all(m.isfile() for m in tar.getmembers())
    assert names == RUNTIME
    assert set(read_archive(archive)) == set(RUNTIME)


def test_missing_main_is_rejected_at_build_and_in_an_archive(tmp_path):
    repo = fake_repo(tmp_path)
    (repo / "main.py").unlink()
    with pytest.raises(PackagingError, match="main.py"):
        build_submission_archive(tmp_path / "s.tar.gz", repo)
    bad = craft(tmp_path / "b.tar.gz", [e for e in runtime_entries() if e[0] != "main.py"])
    with pytest.raises(PackagingError, match="not at the archive root"):
        read_archive(bad)


def test_nested_main_is_rejected(tmp_path):
    entries = [("sub/main.py" if n == "main.py" else n, d) for n, d in runtime_entries()]
    with pytest.raises(PackagingError, match="found nested"):
        read_archive(craft(tmp_path / "b.tar.gz", entries))


def test_missing_package_is_rejected_at_build_and_in_an_archive(tmp_path):
    repo = fake_repo(tmp_path)
    shutil.rmtree(repo / "kaggriculture_bot")
    with pytest.raises(PackagingError, match="package directory"):
        build_submission_archive(tmp_path / "s.tar.gz", repo)
    repo = fake_repo(tmp_path / "again")
    (repo / "kaggriculture_bot" / "__init__.py").unlink()
    with pytest.raises(PackagingError, match="__init__"):
        build_submission_archive(tmp_path / "s.tar.gz", repo)
    only_main = craft(tmp_path / "b.tar.gz", [runtime_entries()[0]])
    with pytest.raises(PackagingError, match="package is missing"):
        read_archive(only_main)


def test_forbidden_repository_files_are_never_packaged(tmp_path):
    repo = fake_repo(tmp_path)
    for rel in (
        "tests/test_x.py",
        "benchmarks/results/g.jsonl",
        "docs/milestones/R.md",
        ".venv/lib/x.py",
        "tools/harness.py",
        "agents/baseline.py",
        "README.md",
        ".env",
        "kaggriculture_bot/__pycache__/economy.cpython-311.pyc",
    ):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    archive = build_submission_archive(tmp_path / "s.tar.gz", repo)
    assert set(read_archive(archive)) == set(RUNTIME)


@pytest.mark.parametrize(
    "extra",
    [
        "README.md",
        "tools/harness.py",
        "tests/test_x.py",
        "docs/x.txt",
        "kaggriculture_bot/sub/x.py",
        "kaggriculture_bot/data.json",
        ".env",
        "kaggriculture_bot/__pycache__/m.cpython-311.pyc",
        "notes.txt",
    ],
)
def test_unexpected_or_forbidden_archive_files_are_rejected(tmp_path, extra):
    bad = craft(tmp_path / "b.tar.gz", runtime_entries() + [(extra, b"x")])
    with pytest.raises(PackagingError):
        read_archive(bad)


def test_unexpected_file_in_the_source_package_is_rejected(tmp_path):
    repo = fake_repo(tmp_path)
    (repo / "kaggriculture_bot" / "data.json").write_text("{}")
    with pytest.raises(PackagingError, match="unexpected files"):
        runtime_source_files(repo)


# --- Unsafe archives -------------------------------------------------------------------------


def test_malformed_archives_are_rejected(tmp_path):
    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"not a tarball at all")
    with pytest.raises(PackagingError, match="malformed"):
        read_archive(junk)
    good = build_submission_archive(tmp_path / "s.tar.gz", REPO_ROOT).read_bytes()
    truncated = tmp_path / "t.tar.gz"
    truncated.write_bytes(good[: len(good) // 2])
    with pytest.raises(PackagingError, match="malformed"):
        read_archive(truncated)


@pytest.mark.parametrize("name", ["../main.py", "kaggriculture_bot/../../evil.py", "/etc/evil.py"])
def test_path_traversal_and_absolute_paths_are_rejected(tmp_path, name):
    with pytest.raises(PackagingError, match="traversal|unsafe"):
        read_archive(craft(tmp_path / "b.tar.gz", runtime_entries() + [(name, b"x")]))


def test_symlink_and_hardlink_entries_are_rejected(tmp_path):
    link = tarfile.TarInfo("kaggriculture_bot/escape.py")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../../etc/passwd"
    with pytest.raises(PackagingError, match="symlink"):
        read_archive(craft(tmp_path / "s.tar.gz", runtime_entries() + [link]))
    hard = tarfile.TarInfo("kaggriculture_bot/hard.py")
    hard.type = tarfile.LNKTYPE
    hard.linkname = "main.py"
    with pytest.raises(PackagingError, match="hardlink"):
        read_archive(craft(tmp_path / "h.tar.gz", runtime_entries() + [hard]))


def test_symlink_in_the_source_package_is_rejected(tmp_path):
    repo = fake_repo(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")
    os.symlink(outside, repo / "kaggriculture_bot" / "escape.py")
    with pytest.raises(PackagingError, match="symlink"):
        build_submission_archive(tmp_path / "s.tar.gz", repo)


def test_duplicate_and_case_colliding_paths_are_rejected(tmp_path):
    dup = runtime_entries() + [("kaggriculture_bot/economy.py", b"x = 1\n")]
    with pytest.raises(PackagingError, match="duplicate"):
        read_archive(craft(tmp_path / "d.tar.gz", dup))
    case = runtime_entries() + [("kaggriculture_bot/Economy.py", b"x = 1\n")]
    with pytest.raises(PackagingError, match="case-colliding"):
        read_archive(craft(tmp_path / "c.tar.gz", case))


# --- Runtime dependency audit ---------------------------------------------------------------------


def test_current_runtime_passes_the_dependency_audit():
    contents = {name: (REPO_ROOT / name).read_bytes() for name in RUNTIME}
    assert audit_runtime_sources(contents) == []


@pytest.mark.parametrize(
    "source, expected",
    [
        ("import numpy\n", "non-stdlib import 'numpy'"),
        ("import pandas as pd\n", "non-stdlib import 'pandas'"),
        ("import pytest\n", "forbidden import 'pytest'"),
        ("from kaggle_environments import make\n", "forbidden import 'kaggle_environments'"),
        ("from tools.harness import run_game\n", "forbidden import 'tools'"),
        ("import tests.conftest\n", "forbidden import 'tests'"),
        ("import socket\n", "forbidden import 'socket'"),
        ("from urllib.request import urlopen\n", "forbidden import 'urllib'"),
        ("import subprocess\n", "forbidden import 'subprocess'"),
        ("import importlib\n", "forbidden import 'importlib'"),
        ("x = eval('1')\n", "forbidden call eval()"),
        ("exec('x = 1')\n", "forbidden call exec()"),
        ("m = __import__('json')\n", "forbidden call __import__()"),
        ("f = open('data.txt')\n", "forbidden call open()"),
        ("import os\ncmd = os.system\n", "forbidden os.system"),
        ("import os\nhome = os.environ\n", "forbidden os.environ"),
        ("PATH = '/Users/someone/tilla/data'\n", "local absolute path"),
        ("type Alias = int\n", "not valid Python 3.11"),
    ],
)
def test_dependency_audit_flags_forbidden_runtime_behaviour(source, expected):
    contents = {
        "main.py": b"def agent(obs):\n    return None\n",
        "kaggriculture_bot/__init__.py": b"",
        "kaggriculture_bot/bad.py": source.encode(),
    }
    problems = audit_runtime_sources(contents)
    assert any(expected in p for p in problems), problems


def test_docstrings_and_relative_imports_do_not_trip_the_audit():
    source = b'"""from economy import things; import the world."""\nfrom . import models\n'
    contents = {"kaggriculture_bot/__init__.py": b"", "kaggriculture_bot/x.py": source}
    assert audit_runtime_sources(contents) == []


# --- Clean extracted import and leakage ----------------------------------------------------------


def test_clean_extracted_import_uses_the_archive_alone_and_smokes(tmp_path):
    archive = build_submission_archive(tmp_path / "s.tar.gz", REPO_ROOT)
    report = validate_archive(archive, FIXTURE)
    check = report.import_check
    assert report.ok, report.as_dict()
    assert check["flags"] == {"isolated": 1, "no_site": 1}
    assert check["leaks"] == []
    assert (
        check["main_file"] == "main.py" and check["package_file"] == "kaggriculture_bot/__init__.py"
    )
    assert "main" in check["own"] and "kaggriculture_bot.strategy" in check["own"]
    assert tuple(check["python"][:2]) >= (3, 11)
    assert report.smoke["ok"] and set(report.smoke["action"]) == {"farmer", "hands", "market"}


def test_a_module_missing_from_the_archive_never_resolves_from_the_repository(tmp_path):
    """The repository (and any editable install of it) must not satisfy imports."""
    extract = tmp_path / "x"
    with tarfile.open(build_submission_archive(tmp_path / "s.tar.gz", REPO_ROOT), "r:gz") as tar:
        tar.extractall(extract, filter="data")
    (extract / "kaggriculture_bot" / "strategy.py").unlink()
    check = clean_import_check(extract)
    assert check["ok"] is False and "strategy" in check["error"]


def test_imports_resolved_outside_the_archive_are_reported_as_leaks(tmp_path):
    extract = tmp_path / "x"
    with tarfile.open(build_submission_archive(tmp_path / "s.tar.gz", REPO_ROOT), "r:gz") as tar:
        tar.extractall(extract, filter="data")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leaky_helper.py").write_text("VALUE = 1\n")
    init = extract / "kaggriculture_bot" / "__init__.py"
    init.write_text(
        init.read_text() + f"\nimport sys\nsys.path.append({str(outside)!r})\nimport leaky_helper\n"
    )
    check = clean_import_check(extract)
    assert check["ok"] is False
    assert any("leaky_helper" in leak for leak in check["leaks"])


# --- Determinism and size reporting --------------------------------------------------------------


def test_builds_are_byte_identical_and_ignore_file_metadata(tmp_path):
    first = build_submission_archive(tmp_path / "a.tar.gz", REPO_ROOT)
    second = build_submission_archive(tmp_path / "b.tar.gz", REPO_ROOT)
    assert first.read_bytes() == second.read_bytes()
    repo = fake_repo(tmp_path)
    for path in repo.rglob("*.py"):
        os.utime(path, (1_700_000_000, 1_700_000_000))
        path.chmod(0o600)
    third = build_submission_archive(tmp_path / "c.tar.gz", repo)
    assert third.read_bytes() == first.read_bytes()


def test_manifest_is_deterministic_and_sizes_are_reported(tmp_path):
    a = validate_archive(build_submission_archive(tmp_path / "a.tar.gz", REPO_ROOT))
    b = validate_archive(build_submission_archive(tmp_path / "b.tar.gz", REPO_ROOT))
    assert a.manifest == b.manifest and a.manifest_sha256 == b.manifest_sha256
    assert a.archive_sha256 == b.archive_sha256 == ps.sha256_file(tmp_path / "a.tar.gz")
    assert [m["path"] for m in a.manifest] == sorted(RUNTIME)
    assert a.archive_bytes == (tmp_path / "a.tar.gz").stat().st_size
    assert a.uncompressed_bytes == sum((REPO_ROOT / n).stat().st_size for n in RUNTIME)
    assert all(m["bytes"] == (REPO_ROOT / m["path"]).stat().st_size for m in a.manifest)
    assert a.smoke is None  # no fixture given


def test_cli_builds_and_validates_and_fails_on_a_bad_archive(tmp_path, capsys):
    assert ps.main([str(tmp_path / "s.tar.gz"), "--no-smoke"]) == 0
    assert '"ok": true' in capsys.readouterr().out
    bad = craft(tmp_path / "bad.tar.gz", runtime_entries() + [("README.md", b"x")])
    assert ps.main(["--validate-only", str(bad)]) == 1
    assert "forbidden file" in capsys.readouterr().out
