"""
Autonomous loop controller
==========================

Plan -> Code -> Verify -> Repeat, the same shape as V1, but:

* Completion and progress come from GitHub Issues (closed feature issues), not a
  JSON file parsed with heuristics.
* Sessions run through the Copilot SDK, so errors are real session errors, not
  the word "error" appearing in prose.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from . import github_backend
from .config import AUTO_CONTINUE_DELAY_SECONDS, DEFAULT_MODEL, MAX_ERROR_RETRIES
from .monitor import ProgressMonitor, get_monitor
from .prompts import copy_spec_to_project, get_coding_prompt, get_initializer_prompt
from .sdk_client import run_session


def _current_progress(project_dir: Path, token: Optional[str]) -> github_backend.ProgressSummary:
    return github_backend.get_progress(project_dir, token=token)


async def run_autonomous_agent(
    project_dir: Path,
    model: str = DEFAULT_MODEL,
    max_iterations: Optional[int] = None,
    spec_file: str = "app_spec.txt",
    permission_mode: str = "allowlist",
    github_token: Optional[str] = None,
    verbose: bool = True,
) -> None:
    project_dir = Path(project_dir)
    app_dir = project_dir / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".harness").mkdir(parents=True, exist_ok=True)

    monitor: ProgressMonitor = get_monitor(project_dir, verbose=verbose)
    monitor._emit(
        f"\nProject: {project_dir}\nModel: {model}\n"
        f"Permission mode: {permission_mode}\nBackend: GitHub Issues"
    )

    # First run if the initializer has not yet recorded a repo with feature issues.
    state = github_backend.read_repo_state(project_dir)
    is_first_run = state is None or _current_progress(project_dir, github_token).total == 0

    if is_first_run:
        monitor._emit(
            "\nFresh start — running the initializer.\n"
            "It will create the repo, an Epic issue, and one issue per feature."
        )
        copy_spec_to_project(project_dir, spec_file)
    else:
        monitor.progress(_current_progress(project_dir, github_token))

    iteration = 0
    consecutive_errors = 0

    while True:
        iteration += 1
        if max_iterations and iteration > max_iterations:
            monitor._emit(f"\nReached max iterations ({max_iterations}).")
            break

        kind = "initializer" if is_first_run else "coding"
        monitor.session_start(iteration, kind)

        if is_first_run:
            prompt = get_initializer_prompt()
        elif consecutive_errors >= MAX_ERROR_RETRIES:
            prompt = (
                f"⚠️ RECOVERY MODE: the last {consecutive_errors} sessions errored.\n"
                "Before new work: read the Epic issue and recent commits, run the app, "
                "and fix what is broken. Reopen any feature issue whose verification no "
                "longer holds.\n\n" + get_coding_prompt()
            )
            monitor._emit(f"\n⚠️  Recovery mode after {consecutive_errors} consecutive errors")
        else:
            prompt = get_coding_prompt()

        try:
            result = await run_session(
                app_dir=app_dir,
                model=model,
                prompt=prompt,
                permission_mode=permission_mode,
                line_sink=monitor.stream,
                github_token=github_token,
            )
            status = "error" if result.errored else "ok"
            monitor.session_end(status, result.tool_calls, result.errors, result.blocked_commands)
        except Exception as exc:  # surface SDK/runtime failures without crashing the loop
            monitor.session_end("error", 0, [str(exc)], [])
            status = "error"

        if is_first_run:
            is_first_run = False
            # Re-read state the initializer just wrote.
            state = github_backend.read_repo_state(project_dir)

        summary = _current_progress(project_dir, github_token)
        monitor.progress(summary)

        if summary.complete:
            monitor._emit("\n🎉 All feature issues closed — project complete!")
            break

        if status == "error":
            consecutive_errors += 1
            monitor._emit(
                f"\nSession errored ({consecutive_errors}/{MAX_ERROR_RETRIES}). Retrying fresh..."
            )
        else:
            consecutive_errors = 0

        await asyncio.sleep(AUTO_CONTINUE_DELAY_SECONDS)

    monitor.final_summary()
    if state:
        monitor._emit(f"\nRepo: https://github.com/{state.slug}")
        if state.epic_issue:
            monitor._emit(f"Epic: https://github.com/{state.slug}/issues/{state.epic_issue}")
