"""Human-readable status: requirement traceability, category coverage, blockers, cost."""

from __future__ import annotations

from collections import Counter, defaultdict

from copilot_harness.models import (
    CATEGORIES,
    Baseline,
    RequirementsDoc,
    State,
    TestLock,
    TestPlan,
    load_model,
    utcnow,
)
from copilot_harness.paths import ProjectPaths


def _first_line(text: str, limit: int = 140) -> str:
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    return (line[: limit - 1] + "…") if len(line) > limit else line


def _safe(model, path):
    try:
        return load_model(model, path)
    except Exception:
        return None


def build_report(paths: ProjectPaths, phase: str = "") -> str:
    reqs = _safe(RequirementsDoc, paths.requirements)
    plan = _safe(TestPlan, paths.test_plan)
    lock = _safe(TestLock, paths.lock)
    baseline = _safe(Baseline, paths.baseline) or Baseline()
    state = _safe(State, paths.state) or State()

    lines = [f"# Harness report — {paths.root.name}", "", f"_Generated {utcnow()}._", ""]
    if phase:
        lines += [f"**Phase:** {phase}", ""]
    if plan is None:
        lines.append("No test plan yet.")
        return "\n".join(lines) + "\n"

    active = plan.active()
    passing = {t for t in baseline.tests}
    blocked = {k for k, v in state.tests.items() if v.status == "blocked"}
    flaky = {k for k, v in state.tests.items() if v.status == "flaky"}
    total = len(active)
    done = len([t for t in active if t.id in passing])
    lines += [
        f"**Baseline:** {done}/{total} active tests passing stably ({(done / total * 100) if total else 0:.0f}%)  ",
        f"**Frozen suite:** v{lock.version} ({len(lock.test_ids)} tests, {len(lock.amendments)} amendments)  " if lock else "**Frozen suite:** not yet frozen  ",
        f"**Sessions:** {len(state.sessions)}  ·  **AI credits (approx.):** {state.total_credits:.1f}",
        "",
        "## By category",
        "",
        "| Category | Planned | Passing | Backlog | Blocked | Flaky |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    by_cat = defaultdict(list)
    for t in active:
        by_cat[t.category].append(t.id)
    for cat in CATEGORIES:
        ids = by_cat.get(cat, [])
        p = sum(1 for i in ids if i in passing)
        b = sum(1 for i in ids if i in blocked)
        f = sum(1 for i in ids if i in flaky)
        lines.append(f"| {cat} | {len(ids)} | {p} | {len(ids) - p - b} | {b} | {f} |")

    if reqs is not None:
        tests_by_req = defaultdict(list)
        for t in active:
            for rid in t.req_ids:
                tests_by_req[rid].append(t.id)
        lines += ["", "## Requirement traceability", "",
                  "| Requirement | Pri | Type | Title | Tests | Passing |", "|---|---|---|---|---|---:|"]
        for r in reqs.requirements:
            ids = tests_by_req.get(r.id, [])
            p = sum(1 for i in ids if i in passing)
            mark = "✅" if ids and p == len(ids) else ("—" if not ids else "")
            status = " (superseded)" if r.status == "superseded" else ""
            shown = ", ".join(ids[:6]) + (f" +{len(ids) - 6}" if len(ids) > 6 else "")
            lines.append(f"| {r.id}{status} | {r.priority} | {r.type} | {r.title} | {shown} | {p}/{len(ids)} {mark} |")

    if blocked:
        lines += ["", "## Blocked tests", "", "These exhausted their attempts; they need a human or a dispute.", ""]
        for tid in sorted(blocked):
            lines.append(f"- **{tid}**: {_first_line(state.tests[tid].last_error)}")
    if lock and lock.amendments:
        lines += ["", "## Test amendments", ""]
        lines += [f"- {a.test_id} ({a.decided_at}): {a.reason}" for a in lock.amendments]
    if baseline.retired:
        lines += ["", "## Retired tests", ""]
        lines += [f"- {tid}: {why}" for tid, why in sorted(baseline.retired.items())]
    if state.sessions:
        roles = Counter(s.role for s in state.sessions)
        lines += ["", "## Sessions", "", "Count by role: " + ", ".join(f"{r} {n}" for r, n in roles.most_common()), "",
                  "| # | Role | Mode | Targets | Newly passing | Regressions | Violations | Credits |",
                  "|---:|---|---|---:|---:|---:|---:|---:|"]
        for s in state.sessions[-25:]:
            lines.append(f"| {s.n} | {s.role} | {s.mode} | {len(s.targets)} | {len(s.newly_passing)} | "
                         f"{len(s.regressions)} | {len(s.violations)} | {s.credits:.1f} |")
    return "\n".join(lines) + "\n"


def write_report(paths: ProjectPaths, phase: str = "") -> str:
    text = build_report(paths, phase)
    paths.report.parent.mkdir(parents=True, exist_ok=True)
    paths.report.write_text(text, encoding="utf-8")
    return text
