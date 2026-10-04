"""Schemas for every artifact the harness reads or writes.

Agents produce requirements, contracts and the test plan as JSON files; the harness
validates them against these models and feeds precise errors back. Lock, baseline and
state are written only by the harness.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["functional", "performance", "security", "usability"]
CATEGORIES: tuple[Category, ...] = ("functional", "performance", "security", "usability")
CATEGORY_PREFIX: dict[str, str] = {
    "functional": "FUNC",
    "performance": "PERF",
    "security": "SEC",
    "usability": "UX",
}
PREFIX_CATEGORY = {v: k for k, v in CATEGORY_PREFIX.items()}
TEST_ID_PATTERN = r"^(FUNC|PERF|SEC|UX)-\d{3,4}$"
REQ_ID_PATTERN = r"^REQ-\d{3,4}$"

RequirementType = Literal["functional", "performance", "security", "usability", "constraint"]
Priority = Literal["P0", "P1", "P2"]
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Requirements (analyst output)
# --------------------------------------------------------------------------


class Threshold(_Strict):
    """A measurable budget, e.g. api_latency_p95_ms <= 300."""

    metric: str = Field(min_length=2)
    operator: Literal["<", "<=", ">", ">=", "=="] = "<="
    value: float
    unit: str = ""


class Requirement(_Strict):
    id: str = Field(pattern=REQ_ID_PATTERN)
    title: str = Field(min_length=3)
    type: RequirementType
    priority: Priority
    description: str = Field(min_length=3)
    acceptance_criteria: list[str] = Field(min_length=1)
    thresholds: list[Threshold] = Field(default_factory=list)
    origin: str = "PRD"
    status: Literal["active", "superseded"] = "active"
    superseded_by: list[str] = Field(default_factory=list)
    assumption: str = ""


class RequirementsDoc(_Strict):
    version: int = 1
    product: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    requirements: list[Requirement] = Field(min_length=1)
    out_of_scope: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    def by_id(self) -> dict[str, Requirement]:
        return {r.id: r for r in self.requirements}


# --------------------------------------------------------------------------
# Contracts (architect output)
# --------------------------------------------------------------------------


class ApiEndpoint(BaseModel):
    model_config = ConfigDict(extra="allow")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: str = Field(pattern=r"^/")
    summary: str = ""
    auth: Literal["none", "required", "admin"] = "none"
    request: dict[str, Any] | None = None
    responses: dict[str, Any] = Field(default_factory=dict)


class UiRoute(BaseModel):
    model_config = ConfigDict(extra="allow")
    path: str = Field(pattern=r"^/")
    name: str
    purpose: str = ""
    auth: Literal["none", "required", "admin"] = "none"


class UiContract(BaseModel):
    model_config = ConfigDict(extra="allow")
    routes: list[UiRoute] = Field(default_factory=list)
    # data-testid -> what it identifies; the coder must render exactly these.
    testids: dict[str, str] = Field(default_factory=dict)


class TestHooks(BaseModel):
    """Test-only affordances the app must expose when started with NODE_ENV=test / APP_ENV=test."""

    __test__ = False  # not a pytest test class

    model_config = ConfigDict(extra="allow")
    reset: str | None = Field(default=None, description="e.g. 'POST /__test__/reset'")
    seed_users: list[dict[str, Any]] = Field(default_factory=list)


class Contract(BaseModel):
    model_config = ConfigDict(extra="allow")
    version: int = 1
    api: list[ApiEndpoint] = Field(default_factory=list)
    ui: UiContract = Field(default_factory=UiContract)
    test_hooks: TestHooks = Field(default_factory=TestHooks)


class AppContract(_Strict):
    """How to install, build and start the app. Read by the runner and by CI."""

    stack: str = Field(min_length=2)
    install: list[str] = Field(default_factory=list)
    build: list[str] = Field(default_factory=list)
    start: str = Field(min_length=1)
    base_url: str = Field(default="http://127.0.0.1:3000", pattern=r"^https?://")
    health_path: str = Field(default="/health", pattern=r"^/")
    env: dict[str, str] = Field(default_factory=dict)
    startup_timeout_seconds: int = Field(default=120, ge=5, le=900)


# --------------------------------------------------------------------------
# Test plan (test architect output)
# --------------------------------------------------------------------------


class TestCase(_Strict):
    __test__ = False  # not a pytest test class
    id: str = Field(pattern=TEST_ID_PATTERN)
    title: str = Field(min_length=3)
    category: Category
    group: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,40}$")
    priority: Priority
    kind: Literal["positive", "negative", "boundary"] = "positive"
    layer: Literal["e2e", "api"] = "e2e"
    req_ids: list[str] = Field(min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(min_length=1)
    expected: list[str] = Field(min_length=1)
    threshold: Threshold | None = None
    tags: list[str] = Field(default_factory=list)
    origin: str = "PRD"
    status: Literal["active", "retired"] = "active"

    @property
    def spec_path(self) -> str:
        return f"acceptance/specs/{self.category}/{self.group}.spec.ts"


class TestPlan(_Strict):
    __test__ = False  # not a pytest test class
    version: int = 1
    tests: list[TestCase] = Field(min_length=1)

    def by_id(self) -> dict[str, TestCase]:
        return {t.id: t for t in self.tests}

    def active(self) -> list[TestCase]:
        return [t for t in self.tests if t.status == "active"]


# --------------------------------------------------------------------------
# Harness-owned records
# --------------------------------------------------------------------------


class Amendment(_Strict):
    test_id: str
    reason: str
    decided_at: str = Field(default_factory=utcnow)


class TestLock(_Strict):
    """SHA-256 manifest of the frozen suite. Any drift is a violation."""

    __test__ = False  # not a pytest test class

    version: int = 1
    frozen_at: str = Field(default_factory=utcnow)
    files: dict[str, str]
    test_ids: list[str]
    amendments: list[Amendment] = Field(default_factory=list)


class BaselineEntry(_Strict):
    promoted_at: str = Field(default_factory=utcnow)
    session: int = 0


class Baseline(_Strict):
    """Tests that have passed stably at least once. CI fails if any of them fails."""

    version: int = 1
    updated_at: str = Field(default_factory=utcnow)
    tests: dict[str, BaselineEntry] = Field(default_factory=dict)
    retired: dict[str, str] = Field(default_factory=dict)


TestStatus = Literal["pending", "failing", "passing", "flaky", "blocked"]


class TestState(_Strict):
    __test__ = False  # not a pytest test class
    status: TestStatus = "pending"
    attempts: int = 0
    last_error: str = ""
    last_run: str = ""
    disputed: bool = False


class SessionRecord(_Strict):
    n: int
    role: str
    mode: str = ""
    started_at: str = Field(default_factory=utcnow)
    ended_at: str = ""
    targets: list[str] = Field(default_factory=list)
    newly_passing: list[str] = Field(default_factory=list)
    regressions: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    credits: float = 0.0
    timed_out: bool = False
    error: str = ""
    commit: str = ""


class State(_Strict):
    version: int = 1
    sessions: list[SessionRecord] = Field(default_factory=list)
    tests: dict[str, TestState] = Field(default_factory=dict)
    last_green_commit: str = ""
    total_credits: float = 0.0
    no_progress_streak: int = 0
    disputes_filed: int = 0
    phase_attempts: dict[str, int] = Field(default_factory=dict)
    # Plan keys ("PRD" or a change id) that were reviewed / approved by a human.
    reviewed: list[str] = Field(default_factory=list)
    approved: list[str] = Field(default_factory=list)
    vacuity_reviewed: list[str] = Field(default_factory=list)
    # Feature delta in progress (set by `copilot-harness feature`).
    active_change: str = ""
    change_base: str = ""
    change_steps: list[str] = Field(default_factory=list)
    allow_retire: bool = False
    # Test-bug failures found by the red check, fed back to the test author.
    red_feedback: dict[str, str] = Field(default_factory=dict)
    architecture_feedback: str = ""
    adjudications: dict[str, str] = Field(default_factory=dict)
    regression_streak: int = 0

    @property
    def plan_key(self) -> str:
        return self.active_change or "PRD"

    def next_session_number(self) -> int:
        return (self.sessions[-1].n + 1) if self.sessions else 1


# --------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """Atomic write so a crash never leaves a half-written record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="json", exclude_none=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


M = TypeVar("M", bound=BaseModel)


def load_model(model: type[M], path: Path) -> M | None:
    if not Path(path).exists():
        return None
    return model.model_validate(read_json(path))
