"""Deterministic Kaggle submission packaging (offline only).

Builds a ``.tar.gz`` whose archive root contains ``main.py`` and the
``kaggriculture_bot/`` package, and nothing else. Full packaging hardening
(timing benchmarks, self-match validation, checklist) arrives in Milestone 10;
Milestone 0 only needs a reproducible archive for the packaging smoke test.
"""

from __future__ import annotations

import gzip
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_PACKAGE = "kaggriculture_bot"
RUNTIME_ENTRYPOINT = "main.py"

# Fixed metadata so the same source always produces byte-identical archives.
_FIXED_MTIME = 0


def runtime_source_files(repo_root: Path = REPO_ROOT) -> list[Path]:
    """Return the runtime source files, relative to ``repo_root``, in sorted order."""
    files = [Path(RUNTIME_ENTRYPOINT)]
    package_dir = repo_root / RUNTIME_PACKAGE
    files.extend(sorted(p.relative_to(repo_root) for p in package_dir.glob("*.py")))
    return files


def _normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.mtime = _FIXED_MTIME
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o644 if info.isfile() else 0o755
    return info


def build_submission_archive(destination: Path, repo_root: Path = REPO_ROOT) -> Path:
    """Write the submission archive to ``destination`` and return its path."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Write gzip ourselves so the header carries no filename or timestamp.
    with (
        open(destination, "wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=_FIXED_MTIME) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar,
    ):
        for relative in runtime_source_files(repo_root):
            tar.add(repo_root / relative, arcname=str(relative), filter=_normalize)
    return destination


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    destination = Path(args[0]) if args else REPO_ROOT / "submission.tar.gz"
    print(build_submission_archive(destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
