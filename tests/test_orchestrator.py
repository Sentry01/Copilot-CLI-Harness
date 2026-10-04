"""Orchestrator behaviour with scripted agents and a fake (instant) test runner."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from copilot_harness.backends.fake import FakeTurn
from copilot_harness.config import HarnessConfig
from copilot_harness.models import Baseline, State, TestLock, TestPlan, load_model
from copilot_harness.orchestrator import Harness
from copilot_harness.paths import ProjectPaths
from copilot_harness.project import create_project
from copilot_harness.results import RunReport, TestOutcome
from copilot_harness.runner import RunResult
from copilot_harness.testlint import lint_specs

from . import notes_project


class FakeRunner:
    """A test passes iff its id is listed in app/passing.json; ids in `flaky` fail under repeats."""

    def __init__(self, paths: ProjectPaths, flaky: set[str] | None = None):
        self.paths = paths
        self.flaky = flaky or set()
        self.calls: list[tuple[str, list[str] | None, int]] = []

    def _found(self) -> set[str]:
        plan = load_model(TestPlan, self.paths.test_plan)
        return set(lint_specs(self.paths.specs, plan, self.paths.root).found) if plan else set()

    async def ensure_acceptance_deps(self, _log) -> str:
        return ""

    async def smoke(self) -> str:
        return ""

    async def list_ids(self):
        return sorted(self._found()), []

    async def run(self, ids=None, repeat_each=1, label="run", prepare=True) -> RunResult:
        self.calls.append((label, ids, repeat_each))
        f = self.paths.app / "passing.json"
        passing = set(json.loads(f.read_text())) if f.exists() else set()
        outcomes = {}
        for tid in sorted(ids if ids is not None else self._found()):
            if tid not in self._found():
                continue
            if tid in passing and tid in self.flaky and repeat_each > 1:
                outcomes[tid] = TestOutcome(tid, "flaky", repeat_each, 1, error="intermittent")
            elif tid in passing:
                outcomes[tid] = TestOutcome(tid, "passed", repeat_each, repeat_each)
            else:
                outcomes[tid] = TestOutcome(tid, "failed", repeat_each, 0, error=f"expect(received).toBe(expected) [{tid}]")
        return RunResult(report=RunReport(outcomes=outcomes), targeted=list(ids or []))


def targets_in(prompt: str) -> list[str]:
    return re.findall(r'"id": "((?:FUNC|PERF|SEC|UX)-\d{3})"', prompt)


def set_passing(t: FakeTurn, ids) -> None:
    t.write("app/passing.json", json.dumps(sorted(set(ids))))


def current_passing(t: FakeTurn) -> set[str]:
    f = t.spec.working_directory / "app" / "passing.json"
    return set(json.loads(f.read_text())) if f.exists() else set()


def config(**loop) -> HarnessConfig:
    cfg = HarnessConfig()
    cfg.tests.min_total, cfg.tests.max_total = 4, 20
    cfg.tests.min_functional_ratio = 0.3
    cfg.tests.min_per_category = {"performance": 1, "security": 1, "usability": 1}
    cfg.tests.stability_repeats = 2
    for k, v in loop.items():
        setattr(cfg.loop, k, v)
    return cfg


def make(tmp_path: Path, coder=None, cfg: HarnessConfig | None = None, flaky=None, **handlers):
    prd = tmp_path / "PRD.md"
    prd.write_text(notes_project.PRD)
    paths = create_project(tmp_path / "proj", prd, install=False, out=lambda *_: None)
    backend = notes_project.make_backend()
    if coder is not None:
        backend.handlers["coder"] = coder
    backend.handlers.update(handlers)
    cfg = cfg or config()
    log: list[str] = []
    runner = FakeRunner(paths, flaky)
    harness = Harness(paths.root, cfg, backend=backend, runner=runner, out=log.append)
    return harness, backend, runner, log


def run(h: Harness, **kw) -> str:
    return asyncio.run(h.run(**kw))


def honest_coder(t: FakeTurn) -> None:
    set_passing(t, current_passing(t) | set(targets_in(t.spec.prompt)))


def state_of(h: Harness) -> State:
    return load_model(State, h.paths.state)


# --------------------------------------------------------------------------


def test_happy_path_reaches_done_and_commits_each_green_step(tmp_path):
    h, backend, runner, log = make(tmp_path, coder=honest_coder)
    assert run(h).startswith("done")
    baseline = load_model(Baseline, h.paths.baseline)
    assert len(baseline.tests) == 6
    subjects = h.git.run("log", "--format=%s").splitlines()
    assert sum(s.startswith("harness: baseline +") for s in subjects) >= 2
    # every promotion went through a stability run with repeats
    assert any(label == "stability" and repeats == 2 for label, _, repeats in runner.calls)
    # the red check ran all new tests before the freeze
    planned = {t.id for t in load_model(TestPlan, h.paths.test_plan).active()}
    assert any(label == "red-check" and set(ids) == planned for label, ids, _ in runner.calls)


def test_stop_hook_sends_agent_back_until_targets_pass(tmp_path):
    def lazy_then_diligent(t: FakeTurn) -> None:
        ids = targets_in(t.spec.prompt) if t.turn == 0 else re.findall(r"### ((?:FUNC|PERF|SEC|UX)-\d{3})", t.reason)
        done = current_passing(t) | set(ids[:1] if t.turn == 0 else ids)
        set_passing(t, done)

    h, backend, *_ = make(tmp_path, coder=lazy_then_diligent)
    assert run(h).startswith("done")
    turns = [c.turn for c in backend.calls if c.spec.role == "coder"]
    assert 1 in turns  # at least one blocked stop
    assert all("not all passing yet" in c.reason for c in backend.calls if c.spec.role == "coder" and c.turn)


def test_regression_is_repaired_in_a_regression_session(tmp_path):
    broke = {"done": False}

    def coder(t: FakeTurn) -> None:
        passing = current_passing(t) | set(targets_in(t.spec.prompt))
        if "repair regressions" in t.spec.prompt:
            passing |= {"FUNC-002"}
        elif "SEC-001" in targets_in(t.spec.prompt) and not broke["done"]:
            broke["done"] = True
            passing -= {"FUNC-002"}
        set_passing(t, passing)

    h, *_ , log = make(tmp_path, coder=coder, cfg=config(max_stop_blocks=0))
    assert run(h).startswith("done")
    modes = [s.mode for s in state_of(h).sessions if s.role == "coder"]
    assert "regression" in modes
    assert any("regressions: FUNC-002" in line for line in log)


def test_unrepaired_regression_rolls_back_to_last_green(tmp_path):
    def vandal(t: FakeTurn) -> None:
        if "repair regressions" in t.spec.prompt:
            return  # never manages to repair what it broke
        passing = current_passing(t) | set(targets_in(t.spec.prompt))
        if "FUNC-001" in passing and "FUNC-001" not in targets_in(t.spec.prompt):
            passing -= {"FUNC-001"}  # breaks a baseline test while building the next feature
        set_passing(t, passing)

    cfg = config(max_stop_blocks=0, regression_fix_attempts=1, max_attempts_per_test=1, no_progress_limit=10)
    h, *_, log = make(tmp_path, coder=vandal, cfg=cfg)
    result = run(h)
    assert any("rolling back to last green commit" in line for line in log)
    # the rollback restored the last green app state, so the baseline test passes again
    assert "FUNC-001" in json.loads((h.paths.app / "passing.json").read_text())
    assert "blocked" in result or result.startswith("no progress")
    assert any(st.status == "blocked" for st in state_of(h).tests.values())


def test_frozen_test_tampering_is_reverted_and_recorded(tmp_path):
    def cheater(t: FakeTurn) -> None:
        spec = t.spec.working_directory / "acceptance/specs/functional/notes.spec.ts"
        t.force_write("acceptance/specs/functional/notes.spec.ts", spec.read_text().replace("toBe(422)", "toBe(201)"))
        t.force_write("harness/baseline.json", json.dumps({"tests": {"FUNC-002": {}}}))
        honest_coder(t)

    h, *_ = make(tmp_path, coder=cheater)
    assert run(h).startswith("done")
    assert "toBe(422)" in (h.paths.specs / "functional" / "notes.spec.ts").read_text()
    coder_sessions = [s for s in state_of(h).sessions if s.role == "coder"]
    violations = [v for s in coder_sessions for v in s.violations]
    assert any("harness/baseline.json" in v for v in violations)
    assert any("notes.spec.ts" in v for v in violations)
    lock = load_model(TestLock, h.paths.lock)
    from copilot_harness.lock import verify_lock

    assert verify_lock(h.paths, lock).ok


def test_policy_denies_coder_writes_to_tests(tmp_path):
    def polite_cheater(t: FakeTurn) -> None:
        honest_coder(t)
        t.write("acceptance/specs/functional/notes.spec.ts", "// gone")  # raises PolicyViolation → recorded as denial

    h, *_ = make(tmp_path, coder=polite_cheater)
    assert run(h).startswith("done")
    denials = [v for s in state_of(h).sessions for v in s.violations if v.startswith("denied:")]
    assert denials and "coder role may not write acceptance/specs" in denials[0]


def test_dispute_amend_updates_the_test_and_records_the_amendment(tmp_path):
    def coder(t: FakeTurn) -> None:
        ids = set(targets_in(t.spec.prompt))
        if "FUNC-002" in ids and not (t.spec.working_directory / ".harness/disputes/claims/FUNC-002.processed").exists():
            t.write(".harness/disputes/claims/FUNC-002.json",
                    json.dumps({"test_id": "FUNC-002", "claim": "PRD says 400", "evidence": "..."}))
            ids -= {"FUNC-002"}
        set_passing(t, current_passing(t) | ids)

    def adjudicator(t: FakeTurn) -> None:
        t.write(".harness/disputes/decisions/FUNC-002.json", json.dumps(
            {"test_id": "FUNC-002", "decision": "amend", "rationale": "contract says 400", "required_change": "expect 400"}))

    original_author = notes_project.make_backend().handlers["test_author"]

    def test_author(t: FakeTurn) -> None:
        if "Amendment ordered" in t.spec.prompt:
            rel = "acceptance/specs/functional/notes.spec.ts"
            t.write(rel, (t.spec.working_directory / rel).read_text().replace("toBe(422)", "toBe(400)"))
        else:
            original_author(t)

    h, *_ = make(tmp_path, coder=coder, adjudicator=adjudicator, test_author=test_author)
    assert run(h).startswith("done")
    lock = load_model(TestLock, h.paths.lock)
    assert [a.test_id for a in lock.amendments] == ["FUNC-002"]
    assert "toBe(400)" in (h.paths.specs / "functional" / "notes.spec.ts").read_text()
    assert state_of(h).adjudications["FUNC-002"].startswith("AMENDED")
    assert "harness: amend FUNC-002 (adjudicated dispute)" in h.git.run("log", "--format=%s")


def test_dispute_upheld_is_passed_back_to_the_coder(tmp_path):
    seen = []

    def coder(t: FakeTurn) -> None:
        if "FUNC-002" in targets_in(t.spec.prompt) and not seen:
            seen.append(1)
            t.write(".harness/disputes/claims/FUNC-002.json", json.dumps({"test_id": "FUNC-002", "claim": "too strict"}))
            set_passing(t, current_passing(t) | {"FUNC-001", "FUNC-003"})
            return
        if "Earlier dispute decisions" in t.spec.prompt:
            seen.append("told")
        honest_coder(t)

    def adjudicator(t: FakeTurn) -> None:
        t.write(".harness/disputes/decisions/FUNC-002.json",
                json.dumps({"test_id": "FUNC-002", "decision": "uphold", "rationale": "PRD requires rejection"}))

    h, *_ = make(tmp_path, coder=coder, adjudicator=adjudicator, cfg=config(max_stop_blocks=0))
    assert run(h).startswith("done")
    assert state_of(h).adjudications["FUNC-002"].startswith("UPHELD")
    assert "told" in seen


def test_flaky_tests_are_not_promoted(tmp_path):
    h, *_ = make(tmp_path, coder=honest_coder, flaky={"PERF-001"}, cfg=config(max_attempts_per_test=2, max_stop_blocks=0))
    result = run(h)
    assert "PERF-001" not in load_model(Baseline, h.paths.baseline).tests
    assert state_of(h).tests["PERF-001"].status in ("flaky", "blocked")
    assert "blocked" in result


def test_session_budget_stops_gracefully_and_resumes(tmp_path):
    h, *_ = make(tmp_path, coder=honest_coder)
    assert "session budget reached" in run(h, max_sessions=2)
    assert len(state_of(h).sessions) == 2
    h2 = Harness(h.paths.root, h.cfg, backend=h.backend, runner=h.runner, out=lambda *_: None)
    assert run(h2).startswith("done")


def test_invalid_artifacts_are_retried_with_feedback(tmp_path):
    attempts = []
    good = notes_project.make_backend().handlers["analyst"]

    def analyst(t: FakeTurn) -> None:
        attempts.append(t.spec.prompt)
        if len(attempts) == 1:
            t.write("harness/requirements.json", '{"requirements": []}')
        else:
            good(t)

    h, *_ = make(tmp_path, coder=honest_coder, analyst=analyst)
    assert run(h).startswith("done")
    assert len(attempts) == 2 and "Validation errors" in attempts[1]


def test_pause_after_plan_requires_approval(tmp_path):
    h, *_ = make(tmp_path, coder=honest_coder)
    assert "ready for human review" in run(h, pause_after_plan=True)
    assert not h.paths.lock.exists()
    st = state_of(h)
    st.approved.append("PRD")
    from copilot_harness.models import write_json

    write_json(h.paths.state, st)
    h2 = Harness(h.paths.root, h.cfg, backend=h.backend, runner=h.runner, out=lambda *_: None)
    assert run(h2, pause_after_plan=True).startswith("done")


def test_until_stops_after_requested_phase(tmp_path):
    h, *_ = make(tmp_path, coder=honest_coder)
    assert run(h, until="plan") == "reached phase 'plan'"
    assert h.paths.test_plan.exists() and not h.paths.lock.exists()
