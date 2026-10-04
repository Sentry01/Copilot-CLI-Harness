"""Freeze the acceptance suite and detect any drift from it.

The lock is a SHA-256 manifest over everything that defines "correct": the PRD and
change requests, requirements, contract, test plan, the acceptance suite (specs,
support kit, Playwright config, dependency lockfile) and the acceptance CI workflow.
Coding agents cannot change these files; if they do, the harness restores them.
"""

from __future__ import annotations

import hashlib
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
    root = paths.root
    found: set[str] = set()
    for d in LOCKED_DIRS:
        base = root / d
        if base.exists():
            for p in base.rglob("*"):
                rel = p.relative_to(root)
                if p.is_file() and not EXCLUDED_PARTS.intersection(rel.parts):
                    found.add(rel.as_posix())
    for f in LOCKED_FILES:
        if (root / f).is_file():
            found.add(f)
    for g in LOCKED_GLOBS:
        for p in root.glob(g):
            if p.is_file():
                found.add(p.relative_to(root).as_posix())
    return sorted(found)


def is_locked_path(rel: str) -> bool:
    parts = Path(rel).parts
    if not parts or EXCLUDED_PARTS.intersection(parts):
        return False
    if parts[0] in LOCKED_DIRS or rel in LOCKED_FILES:
        return True
    return any(Path(rel).match(g) for g in LOCKED_GLOBS)


def compute_manifest(paths: ProjectPaths) -> dict[str, str]:
    return {rel: sha256_file(paths.root / rel) for rel in locked_files(paths)}


def create_lock(
    paths: ProjectPaths,
    test_ids: Iterable[str],
    previous: TestLock | None = None,
    amendments: Iterable[Amendment] = (),
    kit_changes: Iterable[KitChange] = (),
) -> TestLock:
    return TestLock(
        version=(previous.version + 1) if previous else 1,
        frozen_at=utcnow(),
        files=compute_manifest(paths),
        test_ids=sorted(set(test_ids)),
        amendments=[*(previous.amendments if previous else []), *amendments],
        kit_changes=[*(previous.kit_changes if previous else []), *kit_changes],
    )


@dataclass
class LockDrift:
    modified: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.modified or self.missing or self.added)

    def describe(self) -> list[str]:
        return (
            [f"modified frozen file: {p}" for p in self.modified]
            + [f"deleted frozen file: {p}" for p in self.missing]
            + [f"added file inside frozen area: {p}" for p in self.added]
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
    return drift


def quarantine(paths: ProjectPaths, rels: Iterable[str], tag: str) -> list[str]:
    """Move files out of the project tree into .harness/quarantine/<tag>/ (never deletes)."""
    moved = []
    for rel in rels:
        src = paths.root / rel
        if not src.exists():
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
    git.checkout_paths(ref, [p for p in drift.modified + drift.missing if p not in keep and git.tracked_at(ref, p)])
    quarantine(paths, [p for p in drift.added if p not in keep], tag)
    return verify_lock(paths, lock)
