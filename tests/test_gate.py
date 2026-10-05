"""The CI regression gate (acceptance/scripts/gate.mjs), exercised with synthetic reports."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from copilot_harness.gitops import Git
from copilot_harness.lock import create_lock
from copilot_harness.models import (
    Amendment,
    Baseline,
    BaselineEntry,
    KitChange,
    RequirementsDoc,
    TestLock,
    TestPlan,
    load_model,
    write_json,
)
from copilot_harness.paths import ProjectPaths
from copilot_harness.project import TEMPLATES

from .notes_project import CONTRACT, PLAN, PRD, REQUIREMENTS, SPECS

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
    write_json(paths.requirements, RequirementsDoc.model_validate(REQUIREMENTS))
    paths.contract.write_text(json.dumps(CONTRACT))
    paths.prd.write_text(PRD)
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
    old = load_model(TestLock, project.lock)
    write_json(project.lock, create_lock(project, old.test_ids, previous=old))
    r = gate(project, report(ALL_PASS), "--base-ref", base)
    assert r.returncode == 1 and "changed without a recorded amendment" in r.stdout
    write_json(project.lock, create_lock(project, old.test_ids, previous=old,
                                         amendments=[Amendment(test_id="FUNC-002", reason="adjudicated")]))
    assert gate(project, report(ALL_PASS), "--base-ref", base).returncode == 0


def relock(project, **kw):
    old = load_model(TestLock, project.lock)
    write_json(project.lock, create_lock(project, old.test_ids, previous=old, **kw))


def test_missing_baseline_fails(project):
    project.baseline.unlink()
    r = gate(project, report(ALL_PASS))
    assert r.returncode == 1 and "harness/baseline.json is missing" in r.stdout


def test_unresolvable_base_ref_fails(project):
    r = gate(project, report(ALL_PASS), "--base-ref", "origin/does-not-exist")
    assert r.returncode == 1 and "cannot be resolved" in r.stdout


def test_skipped_repetitions_are_a_regression(project):
    rep = report(ALL_PASS)
    rep["suites"][0]["specs"] += [{"title": "SEC-001: t", "tests": [{"results": [{"status": "skipped"}]}]}]
    r = gate(project, rep)
    assert r.returncode == 1 and "REGRESSION SEC-001: skipped" in r.stdout


def test_retiring_a_baselined_test_in_the_plan_does_not_hide_it(project):
    plan = json.loads(project.test_plan.read_text())
    next(t for t in plan["tests"] if t["id"] == "SEC-001")["status"] = "retired"
    project.test_plan.write_text(json.dumps(plan, indent=2))
    relock(project)
    r = gate(project, report({k: v for k, v in ALL_PASS.items() if k != "SEC-001"}))
    assert r.returncode == 1 and "REGRESSION SEC-001: did not run" in r.stdout


def test_base_ref_rejects_retiring_tests_of_active_requirements(project):
    base = Git(project.root).head()
    plan = json.loads(project.test_plan.read_text())
    next(t for t in plan["tests"] if t["id"] == "UX-001")["status"] = "retired"
    project.test_plan.write_text(json.dumps(plan, indent=2))
    relock(project)
    r = gate(project, report(ALL_PASS), "--base-ref", base)
    assert r.returncode == 1 and "UX-001 was retired but none of its requirements were superseded: REQ-004" in r.stdout


def test_base_ref_accepts_retiring_tests_of_superseded_requirements(project):
    plan = json.loads(project.test_plan.read_text())
    next(t for t in plan["tests"] if t["id"] == "UX-001")["req_ids"] = ["REQ-004", "REQ-001"]
    project.test_plan.write_text(json.dumps(plan, indent=2))
    relock(project)
    base = Git(project.root).commit_all("UX-001 also traces to REQ-001")
    reqs = json.loads(project.requirements.read_text())
    old = next(r for r in reqs["requirements"] if r["id"] == "REQ-004")
    reqs["requirements"].append({**old, "id": "REQ-005", "origin": "CHG-001"})
    old.update(status="superseded", superseded_by=["REQ-005"])
    project.requirements.write_text(json.dumps(reqs, indent=2))
    plan = json.loads(project.test_plan.read_text())
    next(t for t in plan["tests"] if t["id"] == "UX-001")["status"] = "retired"
    project.test_plan.write_text(json.dumps(plan, indent=2))
    relock(project)
    r = gate(project, report(ALL_PASS), "--base-ref", base)
    assert "UX-001 was retired" not in r.stdout
    assert "REQ-004 was edited" not in r.stdout


def test_base_ref_requires_authorization_for_kit_changes(project):
    base = Git(project.root).head()
    kit = project.acceptance / "support" / "security.ts"
    kit.write_text(kit.read_text().replace("expect(problems", "expect([] as string[]"))  # neuter the helper
    relock(project)
    r = gate(project, report(ALL_PASS), "--base-ref", base)
    assert r.returncode == 1 and "frozen kit file acceptance/support/security.ts was changed without authorization" in r.stdout
    old = load_model(TestLock, project.lock)
    write_json(project.lock, old.model_copy(update={"kit_changes": [KitChange(path="acceptance/support/security.ts", reason="upgrade")]}))
    assert gate(project, report(ALL_PASS), "--base-ref", base).returncode == 0


def test_base_ref_rejects_contract_removals_and_prd_edits(project):
    base = Git(project.root).head()
    contract = json.loads(project.contract.read_text())
    contract["api"] = contract["api"][:1]
    project.contract.write_text(json.dumps(contract))
    project.prd.write_text(PRD.replace("Empty notes are rejected.", "Empty notes are fine."))
    relock(project)
    r = gate(project, report(ALL_PASS), "--base-ref", base)
    assert r.returncode == 1
    assert "contract endpoint POST /api/notes was removed" in r.stdout
    assert "harness/PRD.md was edited" in r.stdout


def test_symlinks_in_the_frozen_suite_fail_without_crashing_the_gate(project):
    import os

    os.symlink("support", project.acceptance / "alias")
    os.symlink(".", project.acceptance / "loop")
    r = gate(project, report(ALL_PASS))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "symlink inside the frozen suite (not allowed): acceptance/alias" in r.stdout
    assert "symlink inside the frozen suite (not allowed): acceptance/loop" in r.stdout
    assert "acceptance/alias/" not in r.stdout  # never followed


def test_gate_and_lock_agree_on_the_manifest(project):
    """The Python lock and gate.mjs must walk the same files, or a fresh lock fails CI."""
    r = gate(project, report(ALL_PASS))
    assert r.returncode == 0, r.stdout
    assert "unfrozen file" not in r.stdout and "modified without" not in r.stdout


def _node_strips_types() -> bool:
    out = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip().lstrip("v")
    major, minor, *_ = (int(x) for x in out.split("."))
    return (major, minor) >= (22, 6)


def test_kit_applies_the_same_app_contract_defaults_as_the_harness(tmp_path):
    if not _node_strips_types():
        pytest.skip("needs Node.js >= 22.6 (--experimental-strip-types)")
    from copilot_harness.models import AppContract

    (tmp_path / "harness").mkdir()
    minimal = {"stack": "node", "start": "node app/server.js"}
    (tmp_path / "harness" / "app-contract.json").write_text(json.dumps(minimal))
    shutil.copy(TEMPLATES / "acceptance" / "support" / "contract.ts", tmp_path / "contract.ts")
    r = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "-e",
         "import('./contract.ts').then((m) => console.log(JSON.stringify(m.appContract)))"],
        cwd=tmp_path, capture_output=True, text=True, env={**os.environ, "HARNESS_PROJECT_ROOT": str(tmp_path)},
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == AppContract.model_validate(minimal).model_dump()


def test_base_ref_rejects_reactivating_a_superseded_requirement(project):
    reqs = json.loads(project.requirements.read_text())
    old = next(r for r in reqs["requirements"] if r["id"] == "REQ-004")
    reqs["requirements"].append({**old, "id": "REQ-005", "origin": "CHG-001"})
    old.update(status="superseded", superseded_by=["REQ-005"])
    project.requirements.write_text(json.dumps(reqs, indent=2))
    relock(project)
    base = Git(project.root).commit_all("supersede REQ-004")
    old.update(status="active", superseded_by=[])
    project.requirements.write_text(json.dumps(reqs, indent=2))
    relock(project)
    r = gate(project, report(ALL_PASS), "--base-ref", base)
    assert r.returncode == 1 and "requirement REQ-004 was superseded and cannot be reactivated" in r.stdout
