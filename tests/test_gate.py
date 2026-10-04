"""The CI regression gate (acceptance/scripts/gate.mjs), exercised with synthetic reports."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from copilot_harness.gitops import Git
from copilot_harness.lock import create_lock
from copilot_harness.models import Amendment, Baseline, BaselineEntry, TestPlan, write_json
from copilot_harness.paths import ProjectPaths
from copilot_harness.project import TEMPLATES

from .notes_project import PLAN, SPECS

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@pytest.fixture
def project(tmp_path: Path) -> ProjectPaths:
    paths = ProjectPaths.at(tmp_path / "p")
    shutil.copytree(TEMPLATES / "acceptance", paths.acceptance)
    for rel, src in SPECS.items():
        (paths.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (paths.root / rel).write_text(src)
    paths.harness.mkdir(parents=True)
    write_json(paths.test_plan, TestPlan.model_validate(PLAN))
    write_json(paths.baseline, Baseline(tests={"FUNC-001": BaselineEntry(), "SEC-001": BaselineEntry()}))
    write_json(paths.lock, create_lock(paths, [t["id"] for t in PLAN["tests"]]))
    (paths.root / ".gitignore").write_text("acceptance/reports/\n")
    git = Git(paths.root)
    git.init()
    git.commit_all("frozen")
    return paths


def report(statuses: dict[str, str], errors: list[str] | None = None) -> dict:
    specs = [{"title": f"{tid}: t", "file": "x.spec.ts", "tests": [{"results": [{"status": s}]}]}
             for tid, s in statuses.items()]
    return {"errors": [{"message": e} for e in errors or []], "suites": [{"title": "x", "specs": specs}]}


def gate(paths: ProjectPaths, rep: dict, *args: str) -> subprocess.CompletedProcess:
    out = paths.acceptance / "reports" / "report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep))
    return subprocess.run(["node", str(paths.acceptance / "scripts" / "gate.mjs"), "--report", str(out), *args],
                          capture_output=True, text=True)


ALL_PASS = {"FUNC-001": "passed", "FUNC-002": "failed", "FUNC-003": "failed", "SEC-001": "passed",
            "PERF-001": "failed", "UX-001": "failed"}


def test_backlog_failures_do_not_fail_the_gate(project):
    r = gate(project, report(ALL_PASS))
    assert r.returncode == 0, r.stdout
    assert "Acceptance gate: PASSED" in r.stdout


def test_baseline_regression_fails(project):
    r = gate(project, report({**ALL_PASS, "SEC-001": "failed"}))
    assert r.returncode == 1 and "REGRESSION SEC-001" in r.stdout


def test_flaky_baseline_test_fails(project):
    rep = report(ALL_PASS)
    rep["suites"][0]["specs"].append({"title": "SEC-001: t", "tests": [{"results": [{"status": "failed"}]}]})
    r = gate(project, rep)
    assert r.returncode == 1 and "REGRESSION SEC-001: flaky" in r.stdout


def test_missing_baseline_test_fails(project):
    statuses = {k: v for k, v in ALL_PASS.items() if k != "FUNC-001"}
    r = gate(project, report(statuses))
    assert r.returncode == 1 and "REGRESSION FUNC-001: did not run" in r.stdout


def test_unplanned_tests_and_load_errors_fail(project):
    r = gate(project, report({**ALL_PASS, "FUNC-999": "passed"}, errors=["SyntaxError: boom"]))
    assert r.returncode == 1
    assert "FUNC-999 is not in harness/test_plan.json" in r.stdout
    assert "suite failed to load: SyntaxError: boom" in r.stdout


def test_tampering_with_frozen_suite_fails(project):
    spec = project.specs / "functional" / "notes.spec.ts"
    spec.write_text(spec.read_text().replace("toBe(422)", "toBeGreaterThan(0)"))
    (project.specs / "functional" / "sneaky.spec.ts").write_text("// extra")
    r = gate(project, report(ALL_PASS))
    assert r.returncode == 1
    assert "frozen file modified without re-freezing: acceptance/specs/functional/notes.spec.ts" in r.stdout
    assert "unfrozen file inside the acceptance suite: acceptance/specs/functional/sneaky.spec.ts" in r.stdout


def test_strict_mode_requires_everything(project):
    assert gate(project, report(ALL_PASS), "--strict").returncode == 1
    everything = dict.fromkeys(ALL_PASS, "passed")
    assert gate(project, report(everything), "--strict").returncode == 0


def test_newly_passing_backlog_is_reported(project):
    r = gate(project, report({**ALL_PASS, "UX-001": "passed"}))
    assert r.returncode == 0 and "copilot-harness verify --promote" in r.stdout


def test_base_ref_ratchet(project):
    git = Git(project.root)
    base = git.head()
    # Removing a baseline test is refused.
    write_json(project.baseline, Baseline(tests={"FUNC-001": BaselineEntry()}))
    r = gate(project, report(ALL_PASS), "--base-ref", base)
    assert r.returncode == 1 and "baseline test SEC-001 was removed" in r.stdout
    # A changed frozen spec, even re-locked, needs a recorded amendment.
    write_json(project.baseline, Baseline(tests={"FUNC-001": BaselineEntry(), "SEC-001": BaselineEntry()}))
    spec = project.specs / "functional" / "notes.spec.ts"
    spec.write_text(spec.read_text() + "\n// tweak\n")
    from copilot_harness.models import TestLock, load_model

    old = load_model(TestLock, project.lock)
    write_json(project.lock, create_lock(project, old.test_ids, previous=old))
    r = gate(project, report(ALL_PASS), "--base-ref", base)
    assert r.returncode == 1 and "changed without a recorded amendment" in r.stdout
    write_json(project.lock, create_lock(project, old.test_ids, previous=old,
                                         amendments=[Amendment(test_id="FUNC-002", reason="adjudicated")]))
    assert gate(project, report(ALL_PASS), "--base-ref", base).returncode == 0
