"""The test-driven pipeline.

Phases are derived from committed artifacts, so ``run`` is idempotent and resumable:

  requirements → architecture → plan → review → [approval] → authoring → freeze → implement

Implementation is a ratchet. Each coding session targets a small batch of failing tests.
The harness then re-runs the whole suite itself. Tests that newly pass are re-run
``stability_repeats`` times and only then promoted to the regression baseline, with a
commit. A session that breaks a baseline test gets ``regression_fix_attempts`` chances to
repair it, after which the work is rolled back to the last green commit.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from string import Template
from typing import Any

from copilot_harness.backends import AgentBackend, SessionOutcome, SessionSpec, make_backend
from copilot_harness.config import HarnessConfig, load_config
from copilot_harness.gitops import Git
from copilot_harness.lock import create_lock, quarantine, restore_lock, sha256_file, verify_lock
from copilot_harness.models import (
    CATEGORY_PREFIX,
    PRIORITY_ORDER,
    Amendment,
    AppContract,
    Baseline,
    BaselineEntry,
    Contract,
    RequirementsDoc,
    SessionRecord,
    State,
    TestCase,
    TestLock,
    TestPlan,
    TestState,
    load_model,
    read_json,
    utcnow,
    write_json,
)
from copilot_harness.paths import ProjectPaths
from copilot_harness.policy import ROLE_WRITE_SCOPES, Policy
from copilot_harness.report import write_report
from copilot_harness.results import RunReport
from copilot_harness.runner import RunResult, TestRunner, grep_for, run_command
from copilot_harness.testlint import lint_specs
from copilot_harness.validators import (
    validate_app_contract,
    validate_contract,
    validate_requirements,
    validate_test_plan,
)

PROMPTS = Path(__file__).parent / "prompts"
PHASES = ["requirements", "architecture", "plan", "review", "approval", "authoring", "freeze", "implement", "done"]
CHANGE_MUTABLE = {"harness/PRD.md", "harness/requirements.json", "harness/contract.json", "harness/test_plan.json"}
CATEGORY_ORDER = {"functional": 0, "security": 1, "usability": 2, "performance": 3}


class Stop(Exception):
    """Graceful stop: budget exhausted, human input needed, or nothing left to do."""


def _feedback(title: str, issues: Iterable[str], limit: int = 40) -> str:
    items = list(issues)
    if not items:
        return ""
    shown = "\n".join(f"- {i}" for i in items[:limit])
    more = f"\n- … and {len(items) - limit} more" if len(items) > limit else ""
    return f"\n\n## {title}\nYour previous attempt was rejected by the harness. Fix exactly these problems:\n{shown}{more}\n"


def _case_view(t: TestCase) -> dict[str, Any]:
    view = t.model_dump(mode="json", exclude_none=True, exclude={"origin", "status", "tags"})
    view["spec_file"] = t.spec_path
    return view


class Harness:
    def __init__(
        self,
        root: str | Path,
        cfg: HarnessConfig | None = None,
        backend: AgentBackend | None = None,
        runner: TestRunner | None = None,
        out: Callable[[str], None] = print,
    ):
        self.paths = ProjectPaths.at(root)
        self.cfg = cfg or load_config(self.paths)
        self.backend = backend or make_backend(self.cfg)
        self.runner = runner or TestRunner(self.paths, self.cfg)
        self.git = Git(self.paths.root)
        self.out = out
        self.state = load_model(State, self.paths.state) or State()
        self.sessions_this_run = 0
        self.max_sessions = self.cfg.loop.max_sessions
        self.pause_after_plan = False

    # ------------------------------------------------------------------
    # artifacts & state
    # ------------------------------------------------------------------
    def save_state(self) -> None:
        write_json(self.paths.state, self.state)

    def _load(self, model: Any, path: Path) -> Any:
        try:
            return load_model(model, path)
        except Exception:
            return None

    def requirements(self) -> RequirementsDoc | None:
        return self._load(RequirementsDoc, self.paths.requirements)

    def plan(self) -> TestPlan | None:
        return self._load(TestPlan, self.paths.test_plan)

    def lock(self) -> TestLock | None:
        return self._load(TestLock, self.paths.lock)

    def baseline(self) -> Baseline:
        return self._load(Baseline, self.paths.baseline) or Baseline()

    def _at_change_base(self, model: Any, rel: str) -> Any:
        ref = self.state.change_base
        if not ref:
            return None
        text = self.git.run("show", f"{ref}:{rel}", check=False)
        if not text.strip():
            return None
        return model.model_validate(json.loads(text))

    def commit(self, message: str) -> str:
        return self.git.commit_all(message)

    def test_state(self, tid: str) -> TestState:
        return self.state.tests.setdefault(tid, TestState())

    # ------------------------------------------------------------------
    # phase detection
    # ------------------------------------------------------------------
    def current_phase(self) -> str:
        change = self.state.active_change
        reqs = self.requirements()
        if reqs is None or (change and not any(r.origin == change for r in reqs.requirements)):
            return "requirements"
        contract_ok = validate_contract(self.paths.contract)[0] and validate_app_contract(self.paths.app_contract)[0]
        if not contract_ok or self.state.architecture_feedback or (change and "architecture" not in self.state.change_steps):
            return "architecture"
        plan = self.plan()
        if plan is None or (change and not any(t.origin == change for t in plan.tests)):
            return "plan"
        if self.cfg.tests.review_plan and self.state.plan_key not in self.state.reviewed:
            return "review"
        if self.pause_after_plan and self.state.plan_key not in self.state.approved:
            return "approval"
        lock = self.lock()
        locked = set(lock.test_ids) if lock else set()
        pending = [t.id for t in plan.active() if t.id not in locked]
        if pending:
            if self.state.red_feedback or self._authoring_needed(plan, pending):
                return "authoring"
            return "freeze"
        baseline = self.baseline()
        if all(t.id in baseline.tests for t in plan.active()):
            return "done"
        return "implement"

    def _authoring_needed(self, plan: TestPlan, pending: list[str]) -> list[str]:
        report = lint_specs(self.paths.specs, plan, self.paths.root, expected_ids=pending)
        pending_set = set(pending)
        bad = {i.test_id for i in report.errors if i.test_id in pending_set}
        files_with_errors = {i.file for i in report.errors if i.test_id is None}
        if files_with_errors:
            by_id = plan.by_id()
            bad |= {tid for tid in pending if by_id[tid].spec_path in files_with_errors}
        return sorted(bad)

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    async def run(self, until: str | None = None, max_sessions: int | None = None,
                  pause_after_plan: bool = False) -> str:
        if max_sessions is not None:
            self.max_sessions = max_sessions
        self.pause_after_plan = pause_after_plan
        if not self.git.is_repo():
            raise Stop(f"{self.paths.root} is not a harness project (run `copilot-harness new` first)")
        phase = self.current_phase()
        try:
            err = await self.runner.ensure_acceptance_deps(self.paths.runs / "setup.log")
            if err:
                raise Stop(err)
            self.git.commit_paths("harness: lock acceptance kit dependencies", [f"{self.cfg.runner.workdir}/package-lock.json"])
            while True:
                phase = self.current_phase()
                if until and until in PHASES and PHASES.index(phase) > PHASES.index(until):
                    return f"reached phase '{until}'"
                self.out(f"\n══ phase: {phase} ══")
                if phase == "done":
                    return "done: every planned test passes and is in the regression baseline"
                handler = getattr(self, f"phase_{phase}")
                result = await handler()
                if phase == "implement":
                    return result
        except Stop as stop:
            self.out(f"\n⏸  {stop}")
            return str(stop)
        finally:
            self.save_state()
            with contextlib.suppress(Exception):  # reporting must never mask the real outcome
                phase = self.current_phase()
            write_report(self.paths, phase)
            await self.backend.close()

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    def policy(self, role: str, extra_scope: Iterable[str] = ()) -> Policy:
        sec = self.cfg.security
        return Policy(
            root=self.paths.root,
            role=role,
            allow_urls=sec.allow_urls,
            allow_git_push=sec.allow_git_push,
            extra_allowed_commands=sec.extra_allowed_commands,
            extra_denied_commands=sec.extra_denied_commands,
            mcp_servers=sec.mcp_servers,
            extra_write_scope=tuple(extra_scope),
        )

    def mcp_servers(self, role: str) -> dict[str, dict[str, Any]]:
        if "playwright" not in self.cfg.security.mcp_servers or role not in ("coder", "test_author", "architect"):
            return {}
        return {
            "playwright": {
                "type": "local",
                "command": "npx",
                "args": ["-y", self.cfg.security.playwright_mcp_package, "--headless", "--isolated",
                         "--allowed-origins", "http://localhost:*;http://127.0.0.1:*"],
                "tools": ["*"],
            }
        }

    def render(self, role: str, **values: Any) -> tuple[str, str]:
        prompt = Template((PROMPTS / f"{role}.md").read_text(encoding="utf-8")).safe_substitute(
            {k: str(v) for k, v in values.items()}
        )
        common = Template((PROMPTS / "_common.md").read_text(encoding="utf-8")).safe_substitute(
            role=role, root=str(self.paths.root), scope=", ".join(ROLE_WRITE_SCOPES.get(role, ())) or "nothing"
        )
        return prompt, common

    def _check_budget(self) -> None:
        if self.sessions_this_run >= self.max_sessions:
            raise Stop(f"session budget reached ({self.max_sessions} sessions this run); run again to continue")
        cap = self.cfg.loop.max_total_ai_credits
        if cap and self.state.total_credits >= cap:
            raise Stop(f"AI credit budget reached ({self.state.total_credits:.1f}/{cap})")

    async def session(
        self,
        role: str,
        prompt: str,
        instructions: str,
        *,
        mode: str = "",
        targets: Iterable[str] = (),
        stop_check: Any = None,
        extra_scope: Iterable[str] = (),
        lock_exempt: Iterable[str] = (),
    ) -> tuple[SessionRecord, SessionOutcome]:
        self._check_budget()
        n = self.state.next_session_number()
        log_dir = self.paths.sessions / f"{n:04d}-{role}"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "prompt.md").write_text(f"{instructions}\n\n---\n\n{prompt}", encoding="utf-8")
        policy = self.policy(role, extra_scope)
        spec = SessionSpec(
            role=role,
            prompt=prompt,
            instructions=instructions,
            policy=policy,
            working_directory=self.paths.root,
            model=self.cfg.models.for_role(role),
            reasoning_effort=self.cfg.models.reasoning_effort,
            timeout_s=self.cfg.loop.session_timeout_minutes * 60,
            max_ai_credits=self.cfg.loop.max_ai_credits_per_session,
            stop_check=stop_check,
            max_stop_blocks=self.cfg.loop.max_stop_blocks if stop_check else 0,
            log_dir=log_dir,
            mcp_servers=self.mcp_servers(role),
        )
        record = SessionRecord(n=n, role=role, mode=mode, targets=list(targets))
        before = self._dirty_snapshot()
        links_before = self._escaping_symlinks()
        self.out(f"▶ session {n}: {role}{f' ({mode})' if mode else ''}{f' — {len(record.targets)} targets' if record.targets else ''}")
        outcome = await self.backend.run(spec)
        record.violations = self.enforce_scope(policy, tag=f"session-{n:04d}", before=before,
                                               lock_exempt=lock_exempt)
        record.violations += self._quarantine_new_symlinks(links_before, f"session-{n:04d}") + [f"denied: {d}" for d in outcome.denials[:20]]
        record.credits, record.timed_out, record.error = outcome.credits, outcome.timed_out, outcome.error
        record.ended_at = utcnow()
        self.state.sessions.append(record)
        self.state.total_credits += outcome.credits
        self.sessions_this_run += 1
        self.save_state()
        if outcome.error:
            self.out(f"  ⚠ session {n}: {outcome.error}")
        for v in record.violations:
            if not v.startswith("denied:"):
                self.out(f"  ⛔ {v}")
        return record, outcome

    def _dirty_snapshot(self) -> dict[str, str]:
        """Digest of every uncommitted file, so work left by earlier sessions is not blamed on this one."""
        snap: dict[str, str] = {}
        if not self.git.is_repo() or not self.git.head():
            return snap
        for entry in self.git.status():
            path = self.paths.root / entry.path
            snap[entry.path] = sha256_file(path) if path.is_file() else "<deleted>"
        return snap

    def _escaping_symlinks(self) -> set[str]:
        """Symlinks inside the project whose target resolves outside it (dependency folders excluded)."""
        root = self.paths.root
        found: set[str] = set()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", ".harness")]
            for name in [*dirnames, *filenames]:
                path = Path(dirpath) / name
                if not path.is_symlink():
                    continue
                target = Path(os.path.realpath(path))
                if target != root and root not in target.parents:
                    found.add(path.relative_to(root).as_posix())
        return found

    def _quarantine_new_symlinks(self, before: set[str], tag: str) -> list[str]:
        """Links created during a session that point outside the project would let later writes escape the
        scope checks unseen (git does not track what is written through them), so they are quarantined."""
        new = sorted(self._escaping_symlinks() - before)
        for rel in new:
            dst = self.paths.quarantine / tag / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(self.paths.root / rel, dst)
        return [f"symlink {rel} pointed outside the project (quarantined)" for rel in new]

    def enforce_scope(self, policy: Policy, tag: str, before: dict[str, str] | None = None,
                      lock_exempt: Iterable[str] = ()) -> list[str]:
        """Revert/quarantine what this session changed outside the role's scope; restore frozen files."""
        violations: list[str] = []
        if not self.git.is_repo() or not self.git.head():
            return violations
        before = before or {}
        restore, move = [], []
        for entry in self.git.status():
            rel = entry.path.rstrip("/")
            if policy.can_write_rel(rel) or rel.startswith(".harness/"):
                continue
            path = self.paths.root / entry.path
            digest = sha256_file(path) if path.is_file() else "<deleted>"
            if before.get(entry.path) == digest:
                continue  # unchanged since before the session (e.g. the coder's WIP during an amendment)
            violations.append(f"{policy.role} changed {rel} outside its write scope (reverted)")
            if not entry.untracked and self.git.tracked_at("HEAD", rel):
                restore.append(rel)
            else:
                move.append(rel)
        self.git.checkout_paths("HEAD", restore)
        quarantine(self.paths, move, tag)
        lock = self.lock()
        if lock is not None and not self.state.active_change:
            exempt = set(lock_exempt)
            drift = verify_lock(self.paths, lock)
            unexpected = [d for d in drift.describe() if d.rsplit(": ", 1)[-1] not in exempt]
            if unexpected:
                violations += unexpected
                restore_lock(self.paths, lock, self.git, self.state.last_green_commit or "HEAD", tag, exempt)
        return violations

    # ------------------------------------------------------------------
    # phase: requirements
    # ------------------------------------------------------------------
    def _change_note(self) -> str:
        c = self.state.active_change
        return f" (with particular attention to the change request `harness/changes/{c}.md`)" if c else ""

    async def phase_requirements(self) -> None:
        change = self.state.active_change
        previous = self._at_change_base(RequirementsDoc, "harness/requirements.json") if change else None
        delta_rules = ""
        if change and previous is not None:
            last = max(int(r.id.split("-")[1]) for r in previous.requirements)
            delta_rules = (
                f"\n## This is change {change}\n"
                f"Read `harness/changes/{change}.md`. `harness/requirements.json` already exists: keep every existing "
                f"requirement exactly as it is (the file is append-only). Add requirements for the change with ids "
                f"starting at REQ-{last + 1:03d} and `\"origin\": \"{change}\"`. If the change alters existing "
                f"behaviour, set the old requirement's `status` to `superseded` and list the new ids in `superseded_by`.\n"
            )
        await self._produce(
            key=f"requirements:{self.state.plan_key}",
            role="analyst",
            values=dict(change_note=self._change_note(), origin=change or "PRD", delta_rules=delta_rules),
            validate=lambda: validate_requirements(self.paths.requirements, previous, change or None),
            on_success=lambda doc: self._canonical(self.paths.requirements, doc,
                                                   f"harness: requirements ({len(doc.requirements)}){f' for {change}' if change else ''}"),
        )

    async def _produce(self, key: str, role: str, values: dict[str, Any], validate: Callable[[], tuple[Any, list[str]]],
                       on_success: Callable[[Any], Any], feedback: str = "") -> Any:
        """Run a role until its artifact validates (bounded attempts)."""
        issues: list[str] = []
        attempts = self.state.phase_attempts.get(key, 0)
        while True:
            if attempts >= self.cfg.loop.max_phase_attempts:
                raise Stop(f"{role} could not produce a valid artifact after {attempts} attempts:\n"
                           + "\n".join(f"  - {i}" for i in issues[:15]))
            prompt, common = self.render(role, **values)
            await self.session(role, prompt + feedback, common, mode=key.split(":", 1)[0])
            attempts += 1
            self.state.phase_attempts[key] = attempts
            model, issues = validate()
            if model is not None:
                self.state.phase_attempts.pop(key, None)
                await _maybe_await(on_success(model))
                self.save_state()
                return model
            self.out(f"  ✗ rejected ({len(issues)} issues): {issues[0] if issues else ''}")
            feedback = _feedback("Validation errors", issues)

    def _canonical(self, path: Path, model: Any, message: str) -> None:
        write_json(path, model)
        self.commit(message)

    # ------------------------------------------------------------------
    # phase: architecture
    # ------------------------------------------------------------------
    async def phase_architecture(self) -> None:
        change = self.state.active_change
        delta_rules = ""
        if change:
            delta_rules = (
                f"\n## This is change {change}\n"
                f"The contract and app already exist. EXTEND `harness/contract.json` for `harness/changes/{change}.md`: "
                f"keep every existing endpoint, route, accessible name, message and test id unchanged (existing frozen "
                f"tests rely on them). Do not implement the change's features in `app/`; only adjust the skeleton if the "
                f"contract requires new infrastructure (e.g. a new reset step for new data).\n"
            )
        feedback = _feedback("The app skeleton failed the harness smoke test", [self.state.architecture_feedback]) \
            if self.state.architecture_feedback else ""
        previous = self._at_change_base(Contract, "harness/contract.json") if change else None

        def validate() -> tuple[Any, list[str]]:
            c, i1 = validate_contract(self.paths.contract)
            a, i2 = validate_app_contract(self.paths.app_contract)
            issues = i1 + i2
            if c is not None and previous is not None:
                issues += contract_append_only(previous, c)
            return ((c, a) if not issues else None), issues

        async def smoke_validate() -> tuple[Any, list[str]]:
            models, issues = validate()
            if models is None:
                return None, issues
            err = await self.runner.smoke()
            return (models, []) if not err else (None, [f"smoke test failed: {err}"])

        key = f"architecture:{self.state.plan_key}"
        attempts = self.state.phase_attempts.get(key, 0)
        issues: list[str] = []
        while True:
            if attempts >= self.cfg.loop.max_phase_attempts:
                raise Stop(f"architect could not produce a runnable skeleton after {attempts} attempts:\n"
                           + "\n".join(f"  - {i[:400]}" for i in issues[:10]))
            prompt, common = self.render("architect", change_note=self._change_note(), delta_rules=delta_rules)
            await self.session("architect", prompt + feedback, common, mode="architecture")
            attempts += 1
            self.state.phase_attempts[key] = attempts
            models, issues = await smoke_validate()
            if models is not None:
                contract, app_contract = models
                write_json(self.paths.contract, contract)
                write_json(self.paths.app_contract, app_contract)
                self.state.phase_attempts.pop(key, None)
                self.state.architecture_feedback = ""
                if change:
                    self.state.change_steps.append("architecture")
                self.commit(f"harness: contract and app skeleton{f' for {change}' if change else ''}")
                self.save_state()
                return
            self.out(f"  ✗ rejected: {issues[0][:300] if issues else ''}")
            feedback = _feedback("Validation errors", issues)

    # ------------------------------------------------------------------
    # phase: plan + review
    # ------------------------------------------------------------------
    def _plan_values(self, previous: TestPlan | None) -> dict[str, Any]:
        t, change = self.cfg.tests, self.state.active_change
        if previous is None:
            size = f"between {t.min_total} and {t.max_total} active tests (aim for about {(t.min_total + t.max_total) // 2})."
            mins = ", ".join(f"{n} {c}" for c, n in t.min_per_category.items())
            dist = f"at least {t.min_functional_ratio:.0%} functional; at least {mins} tests."
            next_ids = "001 for every prefix"
            delta_rules = ""
        else:
            size = f"add {t.delta_min}-{t.delta_max} new tests for the requirements with origin \"{change}\"."
            dist = "match the change: add security, usability and performance tests wherever the new requirements call for them."
            nxt = {}
            for prefix in CATEGORY_PREFIX.values():
                nums = [int(x.id.split("-")[1]) for x in previous.tests if x.id.startswith(prefix + "-")]
                nxt[prefix] = f"{prefix}-{(max(nums) + 1) if nums else 1:03d}"
            next_ids = ", ".join(nxt.values())
            delta_rules = (
                f"\n## This is change {change}\n"
                f"`harness/test_plan.json` already exists and is append-only: keep every existing test byte-for-byte, except "
                f"set `\"status\": \"retired\"` on tests whose requirements are now superseded. New tests use "
                f"`\"origin\": \"{change}\"` and NEW group names (new spec files); never add tests to an existing group.\n"
            )
        return dict(change_note=self._change_note(), origin=change or "PRD", size_rule=size, distribution_rule=dist,
                    next_ids=next_ids, max_group=t.max_group_size, delta_rules=delta_rules)

    def _validate_plan(self, previous: TestPlan | None) -> tuple[TestPlan | None, list[str]]:
        reqs = self.requirements()
        if reqs is None:
            return None, ["harness/requirements.json is missing or invalid"]
        change = self.state.active_change or None
        plan, issues = validate_test_plan(self.paths.test_plan, reqs, self.cfg.tests, previous, change)
        if plan is not None and previous is not None:
            old_groups = {(t.category, t.group) for t in previous.tests}
            for t in plan.tests:
                if t.id not in previous.by_id() and (t.category, t.group) in old_groups:
                    issues.append(f"{t.id}: new tests must use a new group, not existing group {t.category}/{t.group}")
            if issues:
                plan = None
        return plan, issues

    async def phase_plan(self) -> None:
        previous = self._at_change_base(TestPlan, "harness/test_plan.json") if self.state.active_change else None
        await self._produce(
            key=f"plan:{self.state.plan_key}",
            role="test_architect",
            values=self._plan_values(previous),
            validate=lambda: self._validate_plan(previous),
            on_success=lambda plan: self._canonical(self.paths.test_plan, plan,
                                                    f"harness: test plan ({len(plan.active())} active tests)"),
        )

    async def phase_review(self) -> None:
        review_path = self.paths.reviews / "plan-review.json"
        review_path.unlink(missing_ok=True)
        prompt, common = self.render("plan_reviewer", change_note=self._change_note())
        await self.session("plan_reviewer", prompt, common, mode="review")
        try:
            review = read_json(review_path)
        except Exception:
            review = {"verdict": "approve", "issues": []}
            self.out("  ⚠ reviewer produced no readable review; continuing")
        issues = [i for i in review.get("issues", []) if i.get("severity") in ("high", "medium")]
        if review.get("verdict") == "revise" and issues:
            self.out(f"  ↻ reviewer requested {len(issues)} revisions")
            previous = self._at_change_base(TestPlan, "harness/test_plan.json") if self.state.active_change else None
            values = self._plan_values(previous)
            notes = [f"[{i.get('severity')}] {', '.join(i.get('test_ids', []) + i.get('req_ids', []))}: "
                     f"{i.get('problem', '')} → {i.get('fix', '')}" for i in issues]
            await self._produce(
                key=f"revise:{self.state.plan_key}",
                role="test_architect",
                values=values,
                validate=lambda: self._validate_plan(previous),
                on_success=lambda plan: self._canonical(self.paths.test_plan, plan,
                                                        f"harness: test plan revised after review ({len(plan.active())} tests)"),
                feedback=_feedback("Independent review findings to address", notes),
            )
        self.state.reviewed.append(self.state.plan_key)
        self.save_state()

    async def phase_approval(self) -> None:
        raise Stop("test plan ready for human review (harness/test_plan.json, harness/REPORT.md). "
                   "Run `copilot-harness approve <dir>` and then `copilot-harness run <dir>` to continue")

    # ------------------------------------------------------------------
    # phase: authoring
    # ------------------------------------------------------------------
    async def phase_authoring(self) -> None:
        plan = self.plan()
        assert plan is not None
        lock = self.lock()
        locked = set(lock.test_ids) if lock else set()
        pending = [t.id for t in plan.active() if t.id not in locked]
        needs = sorted(set(self._authoring_needed(plan, pending)) | set(self.state.red_feedback))
        by_id = plan.by_id()
        groups: dict[str, list[str]] = defaultdict(list)
        for tid in needs:
            groups[by_id[tid].spec_path].append(tid)
        batch: list[str] = []
        for _path, ids in sorted(groups.items()):
            if batch and len(batch) + len(ids) > self.cfg.tests.author_batch_size:
                break
            batch += ids
        if not batch:
            return
        key = f"author:{batch[0]}"
        attempts = self.state.phase_attempts.get(key, 0)
        if attempts >= self.cfg.loop.max_phase_attempts:
            raise Stop(f"test author could not produce valid tests for {', '.join(batch[:10])} after {attempts} attempts; "
                       f"see harness lint output (`copilot-harness lint`)")
        feedback = await self._authoring_feedback(plan, batch)
        files = "\n".join(f"- `{p}`" for p in sorted({by_id[t].spec_path for t in batch}))
        cases = json.dumps([_case_view(by_id[t]) for t in batch], indent=2)
        prompt, common = self.render("test_author", cases=cases, files=files, feedback=feedback)
        self.state.phase_attempts[key] = attempts + 1
        await self.session("test_author", prompt, common, mode="authoring", targets=batch)
        for tid in batch:
            self.state.red_feedback.pop(tid, None)
        remaining = set(self._authoring_needed(plan, batch))
        if not remaining:
            self.state.phase_attempts.pop(key, None)
            self.commit(f"harness: author {len(batch)} acceptance tests ({batch[0]}…{batch[-1]})")
        else:
            self.out(f"  ✗ {len(remaining)} tests still fail static checks")
        self.save_state()

    async def _authoring_feedback(self, plan: TestPlan, batch: list[str]) -> str:
        issues: list[str] = []
        report = lint_specs(self.paths.specs, plan, self.paths.root, expected_ids=batch)
        wanted = set(batch)
        issues += [str(i) for i in report.errors if i.test_id in wanted or i.test_id is None]
        issues += [f"{tid} failed against the skeleton app for a reason that looks like a bug in the test itself:\n"
                   f"{self.state.red_feedback[tid][:800]}" for tid in batch if tid in self.state.red_feedback]
        if any(self.paths.specs.rglob("*.spec.ts")):
            _ids, errors = await self.runner.list_ids()
            issues += [f"suite failed to load: {e[:800]}" for e in errors]
            issues += await self._typecheck()
        return _feedback("Problems found in the current spec files", issues) if issues else ""

    async def _typecheck(self) -> list[str]:
        wd = self.paths.root / self.cfg.runner.workdir
        if self.cfg.runner.kind != "playwright" or not (wd / "node_modules" / "typescript").exists():
            return []
        code, out, _ = await run_command(["npx", "tsc", "--noEmit", "-p", "."], wd, None, 300)
        if code == 0:
            return []
        return [f"type error: {ln.strip()}" for ln in out.splitlines() if "error TS" in ln][:30]

    # ------------------------------------------------------------------
    # phase: freeze (red check + lock)
    # ------------------------------------------------------------------
    async def phase_freeze(self) -> None:
        plan = self.plan()
        assert plan is not None
        prev = self.lock()
        locked = set(prev.test_ids) if prev else set()
        new_ids = [t.id for t in plan.active() if t.id not in locked]

        if prev is not None:
            drift = verify_lock(self.paths, prev)
            tampered = [p for p in drift.modified + drift.missing if p not in CHANGE_MUTABLE]
            if tampered:
                self.out(f"  ⛔ previously frozen files changed; restoring: {', '.join(tampered[:5])}")
                self.git.checkout_paths(self.state.last_green_commit or "HEAD", tampered)

        report = lint_specs(self.paths.specs, plan, self.paths.root)
        if report.errors:
            if self._authoring_needed(plan, new_ids):
                self.out(f"  ✗ lint errors remain ({len(report.errors)}); back to authoring")
                return
            raise Stop("frozen spec files fail static checks:\n" + "\n".join(f"  - {e}" for e in report.errors[:15]))
        found, errors = await self.runner.list_ids()
        if errors:
            for tid in new_ids:
                self.state.red_feedback[tid] = "suite failed to load: " + errors[0][:600]
            self.save_state()
            return
        missing = sorted(set(t.id for t in plan.active()) - set(found))
        if missing and self.cfg.runner.kind == "playwright":
            for tid in missing:
                self.state.red_feedback[tid] = "the runner did not discover this test; check its title starts with '<ID>: '"
            self.save_state()
            return

        result = await self.runner.run(ids=new_ids, label="red-check")
        if result.infra_error or result.report.collection_errors:
            self.state.architecture_feedback = (result.infra_error or result.report.collection_errors[0])[:2000]
            self.save_state()
            self.out("  ✗ the app could not be started for the red check; back to architecture")
            return
        bugs = {tid: o.error for tid, o in result.report.outcomes.items() if tid in new_ids and o.looks_like_test_bug}
        key = f"red:{self.state.plan_key}"
        if bugs and self.state.phase_attempts.get(key, 0) < self.cfg.loop.max_phase_attempts:
            self.state.phase_attempts[key] = self.state.phase_attempts.get(key, 0) + 1
            self.state.red_feedback.update(bugs)
            self.out(f"  ✗ {len(bugs)} tests fail because of bugs in the tests themselves; back to authoring")
            self.save_state()
            return
        if bugs:
            self.out(f"  ⚠ freezing despite {len(bugs)} tests that still look broken: {', '.join(sorted(bugs)[:10])}")
        pre_passing = sorted(result.report.passed & set(new_ids))
        if pre_passing and self.state.plan_key not in self.state.vacuity_reviewed:
            self.state.vacuity_reviewed.append(self.state.plan_key)
            self.save_state()
            await self._vacuity_review(plan, pre_passing)
            return

        baseline = self.baseline()
        retiring = [t.id for t in plan.tests if t.status == "retired" and t.id in baseline.tests]
        if retiring and not self.state.allow_retire:
            raise Stop(f"change {self.state.active_change} retires baseline tests {', '.join(retiring)}. Review "
                       f"harness/test_plan.json; to accept run `copilot-harness approve <dir> --allow-retire`")
        for tid in retiring:
            baseline.tests.pop(tid, None)
            baseline.retired[tid] = f"retired by {self.state.active_change or 'plan'} ({utcnow()})"
        baseline.updated_at = utcnow()
        write_json(self.paths.baseline, baseline)

        lock = create_lock(self.paths, [t.id for t in plan.tests], previous=prev)
        write_json(self.paths.lock, lock)
        self.state.phase_attempts.pop(key, None)
        self.state.red_feedback = {}
        change = self.state.active_change
        self.state.active_change, self.state.change_base, self.state.change_steps = "", "", []
        self.state.allow_retire = False
        self.state.last_green_commit = self.commit(
            f"harness: freeze acceptance suite v{lock.version} (+{len(new_ids)} tests{f', {change}' if change else ''})")
        self.save_state()
        self.out(f"  🔒 frozen v{lock.version}: {len(lock.test_ids)} tests, {len(new_ids)} new; {len(pre_passing)} pass already")

    async def _vacuity_review(self, plan: TestPlan, ids: list[str]) -> None:
        by_id = plan.by_id()
        files = "\n".join(f"- `{p}`" for p in sorted({by_id[t].spec_path for t in ids}))
        cases = json.dumps([_case_view(by_id[t]) for t in ids], indent=2)
        feedback = (
            "\n\n## Vacuity review\nThese tests already PASS against an app skeleton that implements no features. "
            "That usually means the assertion does not really check the planned `expected` outcome. For each test: "
            "if the behaviour genuinely exists in the skeleton already (e.g. health endpoint, baseline security "
            "headers), leave it unchanged; otherwise strengthen the assertions so the test only passes once the "
            "feature works. Do not change tests that are not listed.\n"
        )
        prompt, common = self.render("test_author", cases=cases, files=files, feedback=feedback)
        await self.session("test_author", prompt, common, mode="vacuity-review", targets=ids)
        if not self._authoring_needed(plan, ids):
            self.commit(f"harness: strengthen {len(ids)} tests after vacuity review")

    # ------------------------------------------------------------------
    # phase: implement
    # ------------------------------------------------------------------
    async def verify(self, label: str) -> RunResult:
        result = await self.runner.run(label=label)
        self._record_results(result.report)
        return result

    def _record_results(self, report: RunReport) -> None:
        now = utcnow()
        for tid, o in report.outcomes.items():
            st = self.test_state(tid)
            st.last_run = now
            if o.status == "passed":
                if st.status != "blocked":
                    st.status = "passing"
                st.last_error = ""
            else:
                if st.status not in ("blocked", "flaky"):
                    st.status = "failing"
                st.last_error = o.error[:2000]
        self.save_state()

    def regressions(self, result: RunResult, baseline: Baseline) -> list[str]:
        if not result.usable:
            return sorted(baseline.tests)
        return sorted(t for t in baseline.tests if t not in result.report.passed)

    async def promote(self, ids: Iterable[str], session: int = 0) -> set[str]:
        ids = sorted(set(ids))
        if not ids:
            return set()
        result = await self.runner.run(ids=ids, repeat_each=self.cfg.tests.stability_repeats, label="stability")
        if not result.usable:
            return set()
        stable = {i for i in ids if (o := result.report.outcomes.get(i)) is not None and o.status == "passed"}
        baseline = self.baseline()
        for tid in stable:
            baseline.tests[tid] = BaselineEntry(session=session)
            self.test_state(tid).status = "passing"
        for tid in set(ids) - stable:
            st = self.test_state(tid)
            st.status = "flaky"
            o = result.report.outcomes.get(tid)
            st.last_error = (f"flaky: passed {o.passes}/{o.runs} repeats. " if o else "flaky: ") + (o.error[:1500] if o else "")
        if stable:
            baseline.updated_at = utcnow()
            write_json(self.paths.baseline, baseline)
        self.save_state()
        return stable

    def pick_targets(self, plan: TestPlan, baseline: Baseline) -> list[TestCase]:
        candidates = [
            t for t in plan.active()
            if t.id not in baseline.tests and self.test_state(t.id).status != "blocked"
            and not self.test_state(t.id).disputed
        ]
        if not candidates:
            return []
        candidates.sort(key=lambda t: (PRIORITY_ORDER[t.priority], CATEGORY_ORDER[t.category], t.category, t.group, t.id))
        first = candidates[0]
        same = [t for t in candidates if (t.category, t.group) == (first.category, first.group)]
        return same[: self.cfg.loop.batch_size]

    def _failure_text(self, result: RunResult, ids: Iterable[str], limit: int = 9000) -> str:
        if result.infra_error:
            return f"The suite could not run:\n{result.infra_error[:3000]}"
        if result.report.collection_errors:
            return "The suite failed to load:\n" + "\n".join(result.report.collection_errors)[:3000]
        chunks = []
        for tid in ids:
            o = result.report.outcomes.get(tid)
            if o is None:
                chunks.append(f"### {tid}\n(did not run)")
            elif o.status != "passed":
                chunks.append(f"### {tid} — {o.status}\n{o.error[:1200]}")
        text = "\n\n".join(chunks) or "(all target tests currently pass)"
        return text[:limit]

    async def _stop_check(self, targets: list[str]) -> str | None:
        lock = self.lock()
        if lock is not None:
            drift = verify_lock(self.paths, lock)
            if not drift.ok:
                return ("You changed frozen acceptance files, which is not allowed. They will be restored; "
                        "implement the behaviour in app/ instead:\n" + "\n".join(drift.describe()[:10]))
        result = await self.runner.run(ids=targets, label="stop-check")
        failing = [t for t in targets if t not in result.report.passed]
        if result.usable and not failing:
            return None
        return ("The harness re-ran your target tests and they are not all passing yet. Keep working; do not stop until "
                f"they pass:\n\n{self._failure_text(result, failing or targets)}")

    def rollback(self) -> None:
        ref = self.state.last_green_commit or self.git.head()
        self.out(f"  ⏪ rolling back to last green commit {ref[:10]}")
        self.git.reset_hard(ref)
        self.git.clean("app")

    async def phase_implement(self) -> str:
        plan = self.plan()
        assert plan is not None
        if not self.state.last_green_commit:
            self.state.last_green_commit = self.git.head()
        result = await self.verify("implement-start")
        baseline = self.baseline()
        regressions = self.regressions(result, baseline)
        if not regressions:
            fresh = (result.report.passed & {t.id for t in plan.active()}) - set(baseline.tests)
            if fresh:
                stable = await self.promote(fresh)
                if stable:
                    self.state.last_green_commit = self.commit(f"harness: baseline +{len(stable)} (already passing)")
                    self.out(f"  ✅ promoted {len(stable)} already-passing tests")

        while True:
            if self.state.no_progress_streak >= self.cfg.loop.no_progress_limit:
                raise Stop(f"no progress in {self.state.no_progress_streak} consecutive sessions; see harness/REPORT.md")
            baseline = self.baseline()
            regressions = self.regressions(result, baseline)
            by_id = plan.by_id()
            if regressions:
                mode = "regression"
                targets = [by_id[t] for t in regressions if t in by_id]
            else:
                mode = "implement"
                targets = self.pick_targets(plan, baseline)
                if not targets:
                    blocked = [t.id for t in plan.active() if self.test_state(t.id).status == "blocked"]
                    if blocked:
                        raise Stop(f"all remaining tests are blocked ({len(blocked)}): {', '.join(blocked[:15])}")
                    return "done: every planned test passes and is in the regression baseline"
            target_ids = [t.id for t in targets]
            record = await self._coding_session(plan, mode, targets, result)
            await self.process_disputes(plan)
            result = await self.verify(f"after-session-{record.n}")
            result = await self._settle(plan, mode, target_ids, result, record)

    async def _coding_session(self, plan: TestPlan, mode: str, targets: list[TestCase], result: RunResult) -> SessionRecord:
        ids = [t.id for t in targets]
        if mode == "regression":
            title = "repair regressions"
            intro = ("Your previous change broke tests that were passing (they are in the regression baseline). "
                     "Fix the implementation so they pass again, without breaking anything else. Do not start new features.")
        else:
            title = "make the target acceptance tests pass"
            intro = ("Implement the behaviour these failing acceptance tests specify. They were written before the "
                     "implementation (test-driven development); they define 'done'.")
        adjudications = "".join(f"\n- {tid}: {self.state.adjudications[tid]}" for tid in ids if tid in self.state.adjudications)
        if adjudications:
            adjudications = "\n## Earlier dispute decisions (binding)\n" + adjudications + "\n"
        prompt, common = self.render(
            "coder",
            mode_title=title,
            mode_intro=intro,
            cases=json.dumps([_case_view(t) for t in targets], indent=2),
            failures=self._failure_text(result, ids),
            adjudications=adjudications,
            spec_files=", ".join(sorted({f"`{t.spec_path}`" for t in targets})),
            grep=grep_for(ids),
        )
        record, _ = await self.session("coder", prompt, common, mode=mode, targets=ids,
                                       stop_check=lambda: self._stop_check(ids))
        return record

    async def _settle(self, plan: TestPlan, mode: str, targets: list[str], result: RunResult,
                      record: SessionRecord) -> RunResult:
        """Ratchet: promote stable passes, or handle regressions (repair, then roll back). Returns the latest run."""
        baseline = self.baseline()
        regressions = self.regressions(result, baseline)
        record.regressions = regressions
        if regressions:
            self.state.regression_streak += 1
            self.out(f"  ✗ {len(regressions)} regressions: {', '.join(regressions[:10])}")
            if self.state.regression_streak > self.cfg.loop.regression_fix_attempts:
                self.rollback()
                self.state.regression_streak = 0
                self.state.no_progress_streak += 1
                culprits = next((s.targets for s in reversed(self.state.sessions) if s.mode == "implement"), [])
                for tid in culprits:
                    self._count_attempt(tid, "change rolled back because it broke baseline tests")
                result = await self.verify("after-rollback")
            self.save_state()
            return result
        self.state.regression_streak = 0
        active = {t.id for t in plan.active()}
        fresh = (result.report.passed & active) - set(baseline.tests)
        stable = await self.promote(fresh, session=record.n)
        record.newly_passing = sorted(stable)
        if stable:
            self.state.no_progress_streak = 0
            self.state.last_green_commit = self.commit(
                f"harness: baseline +{len(stable)} ({', '.join(sorted(stable)[:8])}{'…' if len(stable) > 8 else ''})")
            record.commit = self.state.last_green_commit
            self.out(f"  ✅ +{len(stable)} stable passes → baseline {len(self.baseline().tests)}/{len(active)}")
        else:
            self.state.no_progress_streak += 1
        if mode == "implement":
            for tid in targets:
                if tid not in stable:
                    self._count_attempt(tid, "")
        self.save_state()
        write_report(self.paths, "implement")
        return result

    def _count_attempt(self, tid: str, note: str) -> None:
        st = self.test_state(tid)
        st.attempts += 1
        if note:
            st.last_error = f"{note}. {st.last_error}"[:2000]
        if st.attempts >= self.cfg.loop.max_attempts_per_test and tid not in self.baseline().tests:
            st.status = "blocked"
            self.out(f"  ⛔ {tid} blocked after {st.attempts} attempts")

    # ------------------------------------------------------------------
    # disputes
    # ------------------------------------------------------------------
    async def process_disputes(self, plan: TestPlan) -> None:
        claims_dir = self.paths.dispute_claims
        if not claims_dir.exists():
            return
        by_id = plan.by_id()
        for claim_path in sorted(claims_dir.glob("*.json")):
            try:
                claim = read_json(claim_path)
                tid = str(claim.get("test_id") or claim_path.stem)
            except Exception:
                claim_path.rename(claim_path.with_suffix(".invalid"))
                continue
            claim_path.rename(claim_path.with_suffix(".processed"))
            case = by_id.get(tid)
            if case is None or case.status != "active":
                continue
            if self.state.disputes_filed >= self.cfg.loop.max_disputes or tid in self.state.adjudications:
                self.state.adjudications.setdefault(tid, "dispute limit reached or already decided: the test stands")
                continue
            self.state.disputes_filed += 1
            decision = await self._adjudicate(case, claim)
            if decision.get("decision") == "amend":
                await self._amend(plan, case, decision)
            else:
                self.state.adjudications[tid] = f"UPHELD: {decision.get('rationale', 'the test stands')}"
            self.save_state()

    async def _adjudicate(self, case: TestCase, claim: dict[str, Any]) -> dict[str, Any]:
        decision_path = self.paths.dispute_decisions / f"{case.id}.json"
        decision_path.unlink(missing_ok=True)
        prompt, common = self.render(
            "adjudicator", test_id=case.id, claim=json.dumps(claim, indent=2)[:4000],
            case=json.dumps(_case_view(case), indent=2), spec_file=case.spec_path,
            failure=self.test_state(case.id).last_error[:2500] or "(none recorded)", req_ids=", ".join(case.req_ids),
        )
        await self.session("adjudicator", prompt, common, mode="dispute", targets=[case.id])
        try:
            decision = read_json(decision_path)
        except Exception:
            decision = {"decision": "uphold", "rationale": "no valid decision was written; the test stands"}
        self.out(f"  ⚖ {case.id}: {decision.get('decision', 'uphold')}")
        return decision

    async def _amend(self, plan: TestPlan, case: TestCase, decision: dict[str, Any]) -> None:
        lock = self.lock()
        if lock is None:
            return
        feedback = (f"\n\n## Amendment ordered by the adjudicator\nChange ONLY test {case.id} in `{case.spec_path}`:\n"
                    f"{decision.get('required_change', '')}\nRationale: {decision.get('rationale', '')}\n"
                    f"Do not modify any other test in the file.\n")
        prompt, common = self.render("test_author", cases=json.dumps([_case_view(case)], indent=2),
                                     files=f"- `{case.spec_path}`", feedback=feedback)
        await self.session("test_author", prompt, common, mode="amendment", targets=[case.id],
                           lock_exempt=[case.spec_path])
        drift = verify_lock(self.paths, lock)
        other = [p for p in drift.modified + drift.missing + drift.added if p != case.spec_path]
        if other:
            self.git.checkout_paths("HEAD", [p for p in other if self.git.tracked_at("HEAD", p)])
            quarantine(self.paths, [p for p in other if not self.git.tracked_at("HEAD", p)], f"amend-{case.id}")
        if self._authoring_needed(plan, [case.id]):
            self.git.checkout_paths("HEAD", [case.spec_path])
            self.state.adjudications[case.id] = "amendment failed static checks; the original test stands"
            return
        new_lock = create_lock(self.paths, lock.test_ids, previous=lock, amendments=[
            Amendment(test_id=case.id, reason=str(decision.get("rationale", ""))[:500])])
        write_json(self.paths.lock, new_lock)
        self.state.adjudications[case.id] = f"AMENDED: {decision.get('rationale', '')}"
        self.test_state(case.id).attempts = 0
        self.commit(f"harness: amend {case.id} (adjudicated dispute)")


def contract_append_only(previous: Contract, current: Contract) -> list[str]:
    issues = []
    now_api = {(e.method, e.path) for e in current.api}
    for e in previous.api:
        if (e.method, e.path) not in now_api:
            issues.append(f"existing endpoint {e.method} {e.path} was removed from the contract")
    now_routes = {r.path for r in current.ui.routes}
    for r in previous.ui.routes:
        if r.path not in now_routes:
            issues.append(f"existing route {r.path} was removed from the contract")
    for tid in previous.ui.testids:
        if tid not in current.ui.testids:
            issues.append(f"existing test id {tid} was removed from the contract")
    return issues


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def load_app_contract(paths: ProjectPaths) -> AppContract | None:
    try:
        return load_model(AppContract, paths.app_contract)
    except Exception:
        return None
