"""`verify --promote` and `relock` guard the baseline and the frozen kit."""

from __future__ import annotations

import asyncio
import json

from copilot_harness.cli import relock_kit, verify_project
from copilot_harness.lock import verify_lock
from copilot_harness.models import Baseline, TestLock, load_model
from copilot_harness.orchestrator import Harness

from .test_orchestrator import FakeRunner, honest_coder, make


def frozen_project(tmp_path):
    """A project frozen by the pipeline, stopped right before implementation."""

    def nothing(_t):
        return None

    h, *_ = make(tmp_path, coder=nothing)
    assert asyncio.run(h.run(until="freeze")) == "reached phase 'freeze'"
    h2 = Harness(h.paths.root, h.cfg, backend=h.backend, runner=FakeRunner(h.paths), out=lambda *_: None)
    return h2


def set_passing(h, ids):
    (h.paths.app / "passing.json").write_text(json.dumps(sorted(ids)))
    h.git.commit_all("app work")


def test_promote_requires_intact_lock(tmp_path):
    h = frozen_project(tmp_path)
    set_passing(h, ["FUNC-001"])
    spec = h.paths.specs / "functional" / "notes.spec.ts"
    spec.write_text(spec.read_text().replace("toBe(422)", "toBeGreaterThan(0)"))
    lines = []
    assert asyncio.run(verify_project(h, promote=True, out=lines.append)) == 1
    assert "FUNC-001" not in load_model(Baseline, h.paths.baseline).tests
    assert any("not promoting" in ln for ln in lines)
    assert "toBeGreaterThan" in spec.read_text()  # nothing was committed or reverted behind the user's back
    assert "verify --promote" not in h.git.run("log", "--format=%s")


def test_promote_requires_committed_work(tmp_path):
    h = frozen_project(tmp_path)
    (h.paths.app / "passing.json").write_text(json.dumps(["FUNC-001"]))  # uncommitted
    lines = []
    assert asyncio.run(verify_project(h, promote=True, out=lines.append)) == 1
    assert any("commit or stash" in ln for ln in lines)


def test_promote_commits_only_the_baseline(tmp_path):
    h = frozen_project(tmp_path)
    set_passing(h, ["FUNC-001", "SEC-001"])
    assert asyncio.run(verify_project(h, promote=True, out=lambda *_: None)) == 0
    assert set(load_model(Baseline, h.paths.baseline).tests) == {"FUNC-001", "SEC-001"}
    changed = h.git.run("show", "--name-only", "--format=", "HEAD").split()
    assert changed == ["harness/baseline.json"]


def test_relock_authorizes_kit_changes_but_not_spec_changes(tmp_path):
    h = frozen_project(tmp_path)
    kit = h.paths.acceptance / "support" / "perf.ts"
    kit.write_text(kit.read_text() + "\n// tuned sampling\n")
    assert relock_kit(h.paths, "tune perf sampling", out=lambda *_: None) == 0
    lock = load_model(TestLock, h.paths.lock)
    assert [k.path for k in lock.kit_changes] == ["acceptance/support/perf.ts"]
    assert verify_lock(h.paths, lock).ok
    assert "harness: relock kit (tune perf sampling)" in h.git.run("log", "--format=%s")

    spec = h.paths.specs / "functional" / "notes.spec.ts"
    spec.write_text(spec.read_text() + "\n// weaken\n")
    lines = []
    assert relock_kit(h.paths, "sneaky", out=lines.append) == 2
    assert any("cannot be relocked" in ln for ln in lines)


def test_relock_refuses_symlinks(tmp_path):
    import os

    h = frozen_project(tmp_path)
    os.symlink("support", h.paths.acceptance / "alias")
    lines = []
    assert relock_kit(h.paths, "link the kit", out=lines.append) == 2
    assert "symlinks are not allowed" in lines[0] and "acceptance/alias" in lines[0]


def test_honest_pipeline_still_completes(tmp_path):
    h, *_ = make(tmp_path, coder=honest_coder)
    assert asyncio.run(h.run()).startswith("done")
