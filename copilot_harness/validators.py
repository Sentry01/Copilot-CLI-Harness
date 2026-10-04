"""Validation of agent-produced artifacts.

Every validator returns ``(model_or_None, issues)``. Issues are short, specific sentences
that are fed back to the agent verbatim, so they say exactly what to fix.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from copilot_harness.config import TestsConfig
from copilot_harness.models import (
    CATEGORY_PREFIX,
    AppContract,
    Contract,
    RequirementsDoc,
    TestPlan,
)

MAX_ISSUES = 40


def _pydantic_issues(err: ValidationError) -> list[str]:
    out = []
    for e in err.errors()[:MAX_ISSUES]:
        loc = ".".join(str(p) for p in e["loc"]) or "<root>"
        out.append(f"{loc}: {e['msg']}")
    return out


def _load(path: Path) -> tuple[Any, list[str]]:
    if not path.exists():
        return None, [f"{path.name} does not exist; write it at {path}"]
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except json.JSONDecodeError as exc:
        return None, [f"{path.name} is not valid JSON: {exc}"]


M = TypeVar("M", bound=BaseModel)


def _parse(model: type[M], data: Any) -> tuple[M | None, list[str]]:
    try:
        return model.model_validate(data), []
    except ValidationError as exc:
        return None, _pydantic_issues(exc)


# --------------------------------------------------------------------------
# Requirements
# --------------------------------------------------------------------------


def validate_requirements(
    path: Path, previous: RequirementsDoc | None = None, delta_origin: str | None = None
) -> tuple[RequirementsDoc | None, list[str]]:
    data, issues = _load(path)
    if issues:
        return None, issues
    doc, issues = _parse(RequirementsDoc, data)
    if doc is None:
        return None, issues

    ids = [r.id for r in doc.requirements]
    for rid, n in Counter(ids).items():
        if n > 1:
            issues.append(f"requirement id {rid} is used {n} times; ids must be unique")
    known = set(ids)
    for r in doc.requirements:
        if r.type == "performance" and not r.thresholds:
            issues.append(f"{r.id} is a performance requirement but has no measurable thresholds")
        if r.status == "superseded":
            if not r.superseded_by:
                issues.append(f"{r.id} is superseded but superseded_by is empty")
            for s in r.superseded_by:
                if s not in known:
                    issues.append(f"{r.id}.superseded_by references unknown requirement {s}")

    if previous is not None:
        issues += _check_append_only_requirements(previous, doc, delta_origin)
    return (doc if not issues else None), issues[:MAX_ISSUES]


def _check_append_only_requirements(
    previous: RequirementsDoc, doc: RequirementsDoc, delta_origin: str | None
) -> list[str]:
    issues = []
    current = doc.by_id()
    for old in previous.requirements:
        new = current.get(old.id)
        if new is None:
            issues.append(f"{old.id} was removed; existing requirements are append-only (mark it superseded instead)")
            continue
        a = old.model_dump(exclude={"status", "superseded_by"})
        b = new.model_dump(exclude={"status", "superseded_by"})
        if a != b:
            issues.append(f"{old.id} was edited; existing requirements may only change status/superseded_by")
        if new.status == "superseded" and old.status == "active":
            for s in new.superseded_by:
                if s in previous.by_id():
                    issues.append(f"{old.id} can only be superseded by a requirement added in this change, not {s}")
    added = [r for r in doc.requirements if r.id not in previous.by_id()]
    if delta_origin is not None:
        if not added:
            issues.append(f"no new requirements were added for change {delta_origin}")
        for r in added:
            if r.origin != delta_origin:
                issues.append(f"{r.id} is new in this change; set origin to \"{delta_origin}\"")
    return issues


# --------------------------------------------------------------------------
# Contracts
# --------------------------------------------------------------------------


def validate_contract(path: Path) -> tuple[Contract | None, list[str]]:
    data, issues = _load(path)
    if issues:
        return None, issues
    contract, issues = _parse(Contract, data)
    if contract is None:
        return None, issues
    seen = Counter((e.method, e.path) for e in contract.api)
    for (method, p), n in seen.items():
        if n > 1:
            issues.append(f"api endpoint {method} {p} is declared {n} times")
    return (contract if not issues else None), issues


def validate_app_contract(path: Path) -> tuple[AppContract | None, list[str]]:
    data, issues = _load(path)
    if issues:
        return None, issues
    return _parse(AppContract, data)


# --------------------------------------------------------------------------
# Test plan
# --------------------------------------------------------------------------


def validate_test_plan(
    path: Path,
    requirements: RequirementsDoc,
    cfg: TestsConfig,
    previous: TestPlan | None = None,
    delta_origin: str | None = None,
) -> tuple[TestPlan | None, list[str]]:
    data, issues = _load(path)
    if issues:
        return None, issues
    plan, issues = _parse(TestPlan, data)
    if plan is None:
        return None, issues

    reqs = requirements.by_id()
    ids = [t.id for t in plan.tests]
    for tid, n in Counter(ids).items():
        if n > 1:
            issues.append(f"test id {tid} is used {n} times; ids must be unique")

    for t in plan.tests:
        prefix = t.id.split("-")[0]
        if CATEGORY_PREFIX[t.category] != prefix:
            issues.append(f"{t.id}: category {t.category} requires prefix {CATEGORY_PREFIX[t.category]}-")
        for rid in t.req_ids:
            req = reqs.get(rid)
            if req is None:
                issues.append(f"{t.id}: req_ids references unknown requirement {rid}")
            elif t.status == "active" and req.status != "active":
                issues.append(f"{t.id} is active but traces to superseded requirement {rid}; retire it")
        if t.category == "performance" and t.threshold is None:
            issues.append(f"{t.id}: performance tests must declare a threshold (metric, operator, value, unit)")

    group_sizes = Counter((t.category, t.group) for t in plan.active())
    for (cat, group), n in group_sizes.items():
        if n > cfg.max_group_size:
            issues.append(f"group {cat}/{group} has {n} tests; split it (max {cfg.max_group_size} per spec file)")

    if previous is None:
        issues += _check_distribution(plan, cfg)
    else:
        issues += _check_append_only_plan(previous, plan, cfg, delta_origin)

    issues += coverage_issues(requirements, plan)
    return (plan if not issues else None), issues[:MAX_ISSUES]


def _check_distribution(plan: TestPlan, cfg: TestsConfig) -> list[str]:
    issues = []
    active = plan.active()
    total = len(active)
    if not cfg.min_total <= total <= cfg.max_total:
        issues.append(f"plan has {total} active tests; it must have between {cfg.min_total} and {cfg.max_total}")
    counts = Counter(t.category for t in active)
    if total and counts["functional"] / total < cfg.min_functional_ratio:
        issues.append(
            f"functional tests are {counts['functional']}/{total} "
            f"({counts['functional'] / total:.0%}); at least {cfg.min_functional_ratio:.0%} required"
        )
    for cat, minimum in cfg.min_per_category.items():
        if counts[cat] < minimum:
            issues.append(f"only {counts[cat]} {cat} tests; at least {minimum} required")
    return issues


def _check_append_only_plan(
    previous: TestPlan, plan: TestPlan, cfg: TestsConfig, delta_origin: str | None
) -> list[str]:
    issues = []
    current = plan.by_id()
    for old in previous.tests:
        new = current.get(old.id)
        if new is None:
            issues.append(f"{old.id} was removed; the plan is append-only (set status to \"retired\" instead)")
            continue
        if old.model_dump(exclude={"status"}) != new.model_dump(exclude={"status"}):
            issues.append(f"{old.id} was edited; existing tests may only change status to retired")
        if old.status == "retired" and new.status == "active":
            issues.append(f"{old.id} was retired and cannot be reactivated; add a new test instead")
    added = [t for t in plan.tests if t.id not in previous.by_id()]
    if delta_origin is not None:
        if not cfg.delta_min <= len(added) <= cfg.delta_max:
            issues.append(f"change {delta_origin} adds {len(added)} tests; it must add {cfg.delta_min}-{cfg.delta_max}")
        for t in added:
            if t.origin != delta_origin:
                issues.append(f"{t.id} is new in this change; set origin to \"{delta_origin}\"")
    return issues


def coverage_issues(
    requirements: RequirementsDoc, plan: TestPlan, only_origin: str | None = None
) -> list[str]:
    """Every active, testable requirement needs tests; P0 behaviour needs a negative path too."""
    by_req: dict[str, list] = defaultdict(list)
    for t in plan.active():
        for rid in t.req_ids:
            by_req[rid].append(t)
    issues = []
    for r in requirements.requirements:
        if r.status != "active" or r.type == "constraint":
            continue
        if only_origin is not None and r.origin != only_origin:
            continue
        tests = by_req.get(r.id, [])
        if not tests:
            issues.append(f"{r.id} ({r.title}) has no tests")
            continue
        if r.priority == "P0" and r.type in ("functional", "security"):
            if len(tests) < 2:
                issues.append(f"{r.id} is P0 and needs at least 2 tests (has {len(tests)})")
            if not any(t.kind in ("negative", "boundary") for t in tests):
                issues.append(f"{r.id} is P0 and needs at least one negative or boundary test")
    return issues


def coverage_matrix(requirements: RequirementsDoc, plan: TestPlan) -> dict[str, list[str]]:
    matrix: dict[str, list[str]] = {r.id: [] for r in requirements.requirements}
    for t in plan.active():
        for rid in t.req_ids:
            matrix.setdefault(rid, []).append(t.id)
    return matrix
