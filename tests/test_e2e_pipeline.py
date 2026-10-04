"""End-to-end: the full pipeline with scripted agents and a REAL Playwright run.

Opt-in (needs Node 22 + npm registry access + a Chromium for Playwright):
    HARNESS_E2E=1 pytest -m e2e
Set PW_CHROMIUM_EXECUTABLE if Playwright's own browser download is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from copilot_harness.config import HarnessConfig
from copilot_harness.models import Baseline, State, TestLock, load_model
from copilot_harness.orchestrator import Harness
from copilot_harness.project import create_project, start_feature
from copilot_harness.runner import TestRunner, free_port

from . import notes_project

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("HARNESS_E2E") != "1", reason="set HARNESS_E2E=1 to run the Playwright e2e test"),
    pytest.mark.skipif(shutil.which("npm") is None, reason="needs Node.js/npm"),
]


def small_config() -> HarnessConfig:
    cfg = HarnessConfig()
    cfg.tests.min_total, cfg.tests.max_total = 4, 20
    cfg.tests.min_functional_ratio = 0.3
    cfg.tests.min_per_category = {"performance": 1, "security": 1, "usability": 1}
    cfg.tests.delta_min, cfg.tests.delta_max = 1, 10
    cfg.tests.stability_repeats = 2
    cfg.loop.max_sessions = 30
    return cfg


def gate(root: Path, *extra: str) -> subprocess.CompletedProcess:
    acc = root / "acceptance"
    env = {**os.environ, "APP_PORT": str(free_port()), "HARNESS_PROJECT_ROOT": str(root)}
    subprocess.run(["npx", "playwright", "test"], cwd=acc, env=env, capture_output=True, text=True)
    return subprocess.run(["node", "scripts/gate.mjs", "--report", "reports/report.json", *extra],
                          cwd=acc, capture_output=True, text=True)


def test_full_pipeline_and_feature_delta(tmp_path: Path) -> None:
    if not os.environ.get("PW_CHROMIUM_EXECUTABLE") and Path("/opt/pw-browsers/chromium").exists():
        os.environ["PW_CHROMIUM_EXECUTABLE"] = "/opt/pw-browsers/chromium"
    prd = tmp_path / "PRD.md"
    prd.write_text(notes_project.PRD)
    root = tmp_path / "notes"
    paths = create_project(root, prd, install=True, out=lambda *_: None)
    assert (paths.acceptance / "node_modules" / "@playwright" / "test").exists()

    cfg = small_config()
    log: list[str] = []
    backend = notes_project.make_backend()
    harness = Harness(root, cfg, backend=backend, runner=TestRunner(paths, cfg), out=log.append)
    result = asyncio.run(harness.run())
    print("\n".join(log))
    assert result.startswith("done"), result

    # The frozen suite and the baseline are complete and committed.
    lock = load_model(TestLock, paths.lock)
    baseline = load_model(Baseline, paths.baseline)
    state = load_model(State, paths.state)
    assert lock is not None and baseline is not None and state is not None
    assert set(baseline.tests) == {"FUNC-001", "FUNC-002", "FUNC-003", "SEC-001", "PERF-001", "UX-001"}
    roles = [s.role for s in state.sessions]
    assert roles[:4] == ["analyst", "architect", "test_architect", "plan_reviewer"]
    assert "test_author" in roles and "coder" in roles
    # SEC-001 passed against the skeleton, so the harness asked for a vacuity review.
    assert any(s.mode == "vacuity-review" for s in state.sessions)
    # The coder's first attempt lacked validation; the stop hook sent it back once.
    coder_turns = [c for c in backend.calls if c.spec.role == "coder"]
    assert [c.turn for c in coder_turns] == [0, 1]
    log_text = subprocess.run(["git", "-C", str(root), "log", "--format=%s"], capture_output=True, text=True).stdout
    assert "harness: freeze acceptance suite v1" in log_text
    assert "harness: baseline +" in log_text

    # CI gate: green on the finished project.
    ok = gate(root)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    # Tampering with a frozen test fails the gate.
    spec = paths.specs / "functional" / "notes.spec.ts"
    original = spec.read_text()
    spec.write_text(original.replace("toBe(422)", "toBeGreaterThan(0)"))
    tampered = gate(root)
    assert tampered.returncode == 1 and "frozen file modified" in tampered.stdout
    spec.write_text(original)

    # Breaking the app fails the gate with a REGRESSION.
    server = paths.app / "server.js"
    good = server.read_text()
    server.write_text(good.replace("'X-Content-Type-Options': 'nosniff',", ""))
    broken = gate(root)
    assert broken.returncode == 1 and "REGRESSION SEC-001" in broken.stdout
    server.write_text(good)

    # Feature delta: new tests are appended, frozen, built, and the old baseline still gates.
    delta = tmp_path / "delete.md"
    delta.write_text(notes_project.DELTA)
    base_ref = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    change = start_feature(root, delta)
    assert change == "CHG-001"
    harness = Harness(root, cfg, backend=backend, runner=TestRunner(paths, cfg), out=log.append)
    result = asyncio.run(harness.run())
    assert result.startswith("done"), "\n".join(log[-40:])
    lock2 = load_model(TestLock, paths.lock)
    assert lock2.version == 2 and {"FUNC-004", "FUNC-005"} <= set(lock2.test_ids)
    assert lock2.files["acceptance/specs/functional/notes.spec.ts"] == lock.files["acceptance/specs/functional/notes.spec.ts"]
    baseline2 = load_model(Baseline, paths.baseline)
    assert set(baseline.tests) | {"FUNC-004", "FUNC-005"} == set(baseline2.tests)

    # The PR-style ratchet against the pre-change commit passes (append-only change)...
    assert gate(root, "--base-ref", base_ref).returncode == 0
    # ...and fails if someone edits an existing planned test.
    plan = json.loads(paths.test_plan.read_text())
    plan["tests"][0]["expected"] = ["anything"]
    paths.test_plan.write_text(json.dumps(plan, indent=2))
    edited = gate(root, "--base-ref", base_ref)
    assert edited.returncode == 1 and "FUNC-001 was edited" in edited.stdout
