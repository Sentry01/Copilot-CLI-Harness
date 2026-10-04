"""Freeze the acceptance suite and detect any drift from it.

The lock is a SHA-256 manifest over everything that defines "correct": the PRD and
change requests, requirements, contract, test plan, the acceptance suite (specs,
support kit, Playwright config, dependency lockfile) and the acceptance CI workflow.
Coding agents cannot change these files; if they do, the harness restores them.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from copilot_harness.gitops import Git
from copilot_harness.models import Amendment, KitChange, TestLock, utcnow
from copilot_harness.paths import ProjectPaths

LOCKED_DIRS = ("acceptance",)
LOCKED_FILES = (
    "harness/PRD.md",
    "harness/requirements.json",
    "harness/contract.json",
    "harness/test_plan.json",
)
LOCKED_GLOBS = ("harness/changes/*.md", ".github/workflows/acceptance*.yml")
EXCLUDED_PARTS = {"node_modules", "test-results", "playwright-report", "blob-report", "reports", ".cache", "__pycache__"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def locked_files(paths: ProjectPaths) -> list[str]:
    """Every path the lock covers. Symlinks are listed as themselves and never followed
    (gate.mjs walks the same way), so a link can't pull unfrozen content into the suite."""
    root = paths.root
    found: set[str] = set()
    for d in LOCKED_DIRS:
        base = root / d
        if not base.is_dir() or base.is_symlink():
            if base.is_symlink():
                found.add(d)
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [n for n in dirnames if n not in EXCLUDED_PARTS]
            here = Path(dirpath)
            links = [n for n in dirnames if (here / n).is_symlink()]
            dirnames[:] = [n for n in dirnames if n not in links]
            for name in [*filenames, *links]:
                if name not in EXCLUDED_PARTS:
                    found.add((here / name).relative_to(root).as_posix())
    for f in LOCKED_FILES:
        if (root / f).is_file() or (root / f).is_symlink():
            found.add(f)
    for g in LOCKED_GLOBS:
        for p in root.glob(g):
            if p.is_file() or p.is_symlink():
                found.add(p.relative_to(root).as_posix())
    return sorted(found)


SYMLINK_PREFIX = "symlink:"


def file_digest(path: Path) -> str:
    """SHA-256 of a file's content; a symlink is recorded by its target, never followed."""
    if path.is_symlink():
        return SYMLINK_PREFIX + os.readlink(path)
    return sha256_file(path)


def is_locked_path(rel: str) -> bool:
    parts = Path(rel).parts
    if not parts or EXCLUDED_PARTS.intersection(parts):
        return False
    if parts[0] in LOCKED_DIRS or rel in LOCKED_FILES:
        return True
    return any(Path(rel).match(g) for g in LOCKED_GLOBS)


def compute_manifest(paths: ProjectPaths) -> dict[str, str]:
    return {rel: file_digest(paths.root / rel) for rel in locked_files(paths)}


class LockError(RuntimeError):
    pass


def create_lock(
    paths: ProjectPaths,
    test_ids: Iterable[str],
    previous: TestLock | None = None,
    amendments: Iterable[Amendment] = (),
    kit_changes: Iterable[KitChange] = (),
) -> TestLock:
    files = compute_manifest(paths)
    links = sorted(rel for rel, digest in files.items() if digest.startswith(SYMLINK_PREFIX))
    if links:
        raise LockError(f"symlinks are not allowed in frozen paths: {', '.join(links)}")
    return TestLock(
        version=(previous.version + 1) if previous else 1,
        frozen_at=utcnow(),
        files=files,
        test_ids=sorted(set(test_ids)),
        amendments=[*(previous.amendments if previous else []), *amendments],
        kit_changes=[*(previous.kit_changes if previous else []), *kit_changes],
    )


@dataclass
class LockDrift:
    modified: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    symlinks: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.modified or self.missing or self.added or self.symlinks)

    def describe(self) -> list[str]:
        return (
            [f"modified frozen file: {p}" for p in self.modified]
            + [f"deleted frozen file: {p}" for p in self.missing]
            + [f"added file inside frozen area: {p}" for p in self.added]
            + [f"symlink inside frozen area (not allowed): {p}" for p in self.symlinks]
        )


def verify_lock(paths: ProjectPaths, lock: TestLock) -> LockDrift:
    current = compute_manifest(paths)
    drift = LockDrift()
    for rel, digest in lock.files.items():
        if rel not in current:
            drift.missing.append(rel)
        elif current[rel] != digest:
            drift.modified.append(rel)
    drift.added = sorted(set(current) - set(lock.files))
    drift.symlinks = sorted(rel for rel, digest in current.items() if digest.startswith(SYMLINK_PREFIX))
    return drift


def quarantine(paths: ProjectPaths, rels: Iterable[str], tag: str) -> list[str]:
    """Move files out of the project tree into .harness/quarantine/<tag>/ (never deletes)."""
    moved = []
    for rel in rels:
        src = paths.root / rel
        if not (src.exists() or src.is_symlink()):
            continue
        dst = paths.quarantine / tag / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(rel)
    return moved


def restore_lock(
    paths: ProjectPaths, lock: TestLock, git: Git, ref: str, tag: str, exempt: Iterable[str] = ()
) -> LockDrift:
    """Undo drift: restore modified/missing files from ``ref``, quarantine additions.

    ``exempt`` paths are left alone (e.g. the one spec file an adjudicated amendment may change).
    """
    keep = set(exempt)
    drift = verify_lock(paths, lock)
    if drift.ok:
        return drift
    # A symlink is never legitimate here: move it out first, then restore what it replaced.
    quarantine(paths, [p for p in drift.symlinks if p not in keep], tag)
    drift = verify_lock(paths, lock)
    git.checkout_paths(ref, [p for p in drift.modified + drift.missing if p not in keep and git.tracked_at(ref, p)])
    quarantine(paths, [p for p in drift.added if p not in keep], tag)
    return verify_lock(paths, lock)
