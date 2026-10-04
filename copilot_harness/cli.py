"""copilot-harness command line."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import platform
import shutil
import subprocess
import sys

from copilot_harness import __version__
from copilot_harness.config import load_config
from copilot_harness.lock import verify_lock
from copilot_harness.models import State, load_model, write_json
from copilot_harness.orchestrator import PHASES, Harness, Stop
from copilot_harness.paths import ProjectPaths
from copilot_harness.project import ProjectError, create_project, start_feature
from copilot_harness.report import write_report
from copilot_harness.testlint import lint_specs


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="copilot-harness",
        description="Test-driven autonomous development with GitHub Copilot: "
        "PRD → requirements → frozen acceptance suite → implementation, verified by the harness.",
    )
    p.add_argument("--version", action="version", version=f"copilot-harness {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("new", help="create a project from a PRD")
    s.add_argument("dir")
    s.add_argument("--prd", required=True, help="path to the product requirements document (markdown)")
    s.add_argument("--name")
    s.add_argument("--force", action="store_true", help="scaffold into a non-empty directory")
    s.add_argument("--no-install", action="store_true", help="skip `npm install` of the acceptance kit")

    s = sub.add_parser("run", help="run (or resume) the pipeline")
    s.add_argument("dir")
    s.add_argument("--until", choices=PHASES, help="stop after this phase")
    s.add_argument("--max-sessions", type=int, help="agent sessions allowed in this run")
    s.add_argument("--pause-after-plan", action="store_true", help="stop for human approval once the test plan is ready")
    s.add_argument("--backend", choices=["sdk", "cli"], help="override harness.toml backend")
    s.add_argument("--model", help="override the default model for every role")
    s.add_argument("--quiet", action="store_true", help="do not stream agent activity")

    s = sub.add_parser("status", help="show phase and progress")
    s.add_argument("dir")

    s = sub.add_parser("verify", help="run the suite (no agents) and check the regression baseline")
    s.add_argument("dir")
    s.add_argument("--promote", action="store_true", help="promote stably passing backlog tests to the baseline")

    s = sub.add_parser("feature", help="start a feature change (PRD delta), then `run` to build it test-first")
    s.add_argument("dir")
    s.add_argument("--prd-delta", required=True, help="markdown describing the change")
    s.add_argument("--allow-retire", action="store_true", help="allow the change to retire baseline tests")

    s = sub.add_parser("approve", help="approve the current test plan (after --pause-after-plan)")
    s.add_argument("dir")
    s.add_argument("--allow-retire", action="store_true", help="accept retiring baseline tests in the active change")

    s = sub.add_parser("report", help="write harness/REPORT.md")
    s.add_argument("dir")

    s = sub.add_parser("lint", help="statically check acceptance specs against the plan")
    s.add_argument("dir")

    s = sub.add_parser("models", help="list models available to your Copilot account")
    s.add_argument("--cli-path", default="")

    s = sub.add_parser("doctor", help="check prerequisites (no AI credits used)")
    s.add_argument("--cli-path", default="")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except (ProjectError, Stop) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted; run the same command again to resume", file=sys.stderr)
        return 130


async def _dispatch(args: argparse.Namespace) -> int:
    match args.command:
        case "new":
            paths = create_project(args.dir, args.prd, args.name, args.force, not args.no_install)
            print(f"✓ created {paths.root}\n  next: copilot-harness run {paths.root}")
            return 0
        case "run":
            return await _run(args)
        case "status":
            return _status(ProjectPaths.at(args.dir))
        case "verify":
            return await _verify(args)
        case "feature":
            change = start_feature(args.dir, args.prd_delta, args.allow_retire)
            print(f"✓ started {change}\n  next: copilot-harness run {args.dir}")
            return 0
        case "approve":
            paths = ProjectPaths.at(args.dir)
            state = load_model(State, paths.state) or State()
            if state.plan_key not in state.approved:
                state.approved.append(state.plan_key)
            state.allow_retire = state.allow_retire or args.allow_retire
            write_json(paths.state, state)
            print(f"✓ approved plan {state.plan_key}")
            return 0
        case "report":
            paths = ProjectPaths.at(args.dir)
            write_report(paths, Harness(paths.root).current_phase())
            print(f"✓ wrote {paths.report}")
            return 0
        case "lint":
            return _lint(ProjectPaths.at(args.dir))
        case "models":
            from copilot_harness.backends.sdk import list_models

            for m in await list_models(args.cli_path):
                print(f"{m['id']:40} {m['name']}")
            return 0
        case "doctor":
            return await _doctor(args.cli_path)
    return 1


async def _run(args: argparse.Namespace) -> int:
    paths = ProjectPaths.at(args.dir)
    cfg = load_config(paths)
    if args.backend:
        cfg.backend.kind = args.backend
    if args.model:
        cfg.models.default = args.model
        for role in ("analyst", "architect", "test_architect", "plan_reviewer", "test_author", "coder", "adjudicator"):
            setattr(cfg.models, role, "")
    from copilot_harness.backends import make_backend

    harness = Harness(paths.root, cfg, backend=make_backend(cfg, verbose=not args.quiet))
    result = await harness.run(until=args.until, max_sessions=args.max_sessions, pause_after_plan=args.pause_after_plan)
    print(f"\n{result}\nreport: {paths.report}")
    return 0 if result.startswith(("done", "reached")) else 3


def _status(paths: ProjectPaths) -> int:
    h = Harness(paths.root)
    plan, baseline = h.plan(), h.baseline()
    print(f"project: {paths.root}\nphase:   {h.current_phase()}")
    if h.state.active_change:
        print(f"change:  {h.state.active_change} in progress")
    if plan:
        active = plan.active()
        blocked = [t.id for t in active if h.test_state(t.id).status == "blocked"]
        print(f"tests:   {len(active)} active, {len([t for t in active if t.id in baseline.tests])} in baseline, "
              f"{len(blocked)} blocked")
    lock = h.lock()
    if lock:
        drift = verify_lock(paths, lock)
        print(f"lock:    v{lock.version}, {'intact' if drift.ok else 'DRIFT: ' + '; '.join(drift.describe()[:3])}")
    print(f"sessions: {len(h.state.sessions)}, AI credits ≈ {h.state.total_credits:.1f}")
    return 0


async def _verify(args: argparse.Namespace) -> int:
    paths = ProjectPaths.at(args.dir)
    h = Harness(paths.root)
    plan, lock = h.plan(), h.lock()
    if plan is None or lock is None:
        print("error: no frozen suite yet", file=sys.stderr)
        return 2
    code = 0
    drift = verify_lock(paths, lock)
    if not drift.ok and not h.state.active_change:
        print("✗ frozen suite drifted:\n  " + "\n  ".join(drift.describe()))
        code = 1
    result = await h.verify("verify")
    if result.infra_error or result.report.collection_errors:
        print("✗ suite could not run:\n" + (result.infra_error or "\n".join(result.report.collection_errors))[:3000])
        return 1
    baseline = h.baseline()
    regressions = h.regressions(result, baseline)
    active = {t.id for t in plan.active()}
    passing = result.report.passed & active
    print(f"passing {len(passing)}/{len(active)}; baseline {len(baseline.tests)}; regressions {len(regressions)}")
    for tid in regressions:
        o = result.report.outcomes.get(tid)
        print(f"  ✗ REGRESSION {tid}: {(o.error.splitlines() or [''])[0][:160] if o else 'did not run'}")
    if regressions:
        code = 1
    elif args.promote:
        stable = await h.promote(passing - set(baseline.tests))
        if stable:
            h.state.last_green_commit = h.commit(f"harness: baseline +{len(stable)} (verify --promote)")
        print(f"promoted {len(stable)} tests")
    h.save_state()
    write_report(paths, h.current_phase())
    return code


def _lint(paths: ProjectPaths) -> int:
    h = Harness(paths.root)
    plan = h.plan()
    if plan is None:
        print("error: no valid harness/test_plan.json", file=sys.stderr)
        return 2
    report = lint_specs(paths.specs, plan, paths.root)
    for issue in report.issues:
        print(issue)
    print(f"{len(report.found)} tests found, {len(report.errors)} errors, {len(report.warnings)} warnings")
    return 1 if report.errors else 0


async def _doctor(cli_path: str) -> int:
    ok = True

    def check(label: str, passed: bool, detail: str = "", required: bool = True) -> None:
        nonlocal ok
        mark = "✓" if passed else ("✗" if required else "!")
        print(f"{mark} {label}{f': {detail}' if detail else ''}")
        if required and not passed:
            ok = False

    py = sys.version_info
    check("Python ≥ 3.11", py >= (3, 11), platform.python_version())
    try:
        check("github-copilot-sdk", True, importlib.metadata.version("github-copilot-sdk"))
    except importlib.metadata.PackageNotFoundError:
        check("github-copilot-sdk", False, "pip install github-copilot-sdk")
    for tool, minimum in (("git", None), ("node", 20), ("npm", None), ("npx", None)):
        path = shutil.which(tool)
        version = ""
        if path:
            version = subprocess.run([tool, "--version"], capture_output=True, text=True).stdout.strip()
        good = bool(path)
        if good and minimum and version.startswith("v"):
            good = int(version[1:].split(".")[0]) >= minimum
        check(tool, good, version or "not found")
    copilot = cli_path or shutil.which("copilot")
    check("copilot CLI (needed for backend=cli)", bool(copilot), copilot or "npm i -g @github/copilot", required=False)
    try:
        from copilot_harness.backends.sdk import auth_status

        authenticated, detail = await asyncio.wait_for(auth_status(cli_path), timeout=120)
        check("Copilot authentication (SDK)", authenticated, detail if authenticated else f"{detail} — run `copilot login`")
    except Exception as exc:  # noqa: BLE001 - report any failure
        check("Copilot authentication (SDK)", False, f"{type(exc).__name__}: {exc}"[:300])
    print("\nall required checks passed" if ok else "\nsome required checks failed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
