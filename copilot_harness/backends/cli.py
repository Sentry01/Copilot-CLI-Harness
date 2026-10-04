"""Copilot CLI subprocess backend (fallback when the SDK runtime is unavailable).

Enforcement is weaker than the SDK backend: the CLI's own deny rules block dangerous
commands and URLs, but write scopes are enforced only after the session, by the
harness's git-status scope check and lock verification. The stop hook is emulated by
resuming the same session (``--resume <id>``) with the remaining failures.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from copilot_harness.backends.base import AgentBackend, EventLog, SessionOutcome, SessionSpec, short
from copilot_harness.policy import DENIED_COMMANDS, DENIED_SUBCOMMANDS, GIT_READ_ONLY
from copilot_harness.runner import run_command

GIT_WRITE_SUBCOMMANDS = (
    "push", "commit", "add", "reset", "checkout", "restore", "switch", "rebase", "merge", "cherry-pick",
    "revert", "stash", "clean", "rm", "mv", "config", "remote", "tag", "branch", "am", "apply",
    "filter-branch", "filter-repo", "update-ref", "worktree", "submodule", "gc", "prune", "notes", "replace",
)


def deny_rules(allow_git_push: bool, denied_commands: Iterable[str] = DENIED_COMMANDS) -> list[str]:
    rules = [f"shell({c})" for c in sorted(denied_commands)]
    rules += [f"shell({base} {sub})" for base, subs in sorted(DENIED_SUBCOMMANDS.items()) for sub in sorted(subs)]
    for sub in GIT_WRITE_SUBCOMMANDS:
        if sub in GIT_READ_ONLY or (sub == "push" and allow_git_push):
            continue
        rules.append(f"shell(git {sub})")
    return rules


class CliBackend(AgentBackend):
    name = "cli"

    def __init__(self, cli_path: str = "", allow_git_push: bool = False, verbose: bool = True):
        self.cli_path = cli_path or shutil.which("copilot") or "copilot"
        self.allow_git_push = allow_git_push
        self.verbose = verbose

    def _args(self, spec: SessionSpec, log_dir: Path) -> list[str]:
        args = [
            self.cli_path,
            "--output-format", "json",
            "--no-ask-user",
            "--no-custom-instructions",
            "--no-color",
            "--no-auto-update",
            "--disable-builtin-mcps",
            "--allow-all-tools",
            "-C", str(spec.working_directory),
            "--log-dir", str(log_dir / "cli-logs"),
            "--usage-output-file", str(log_dir / "usage.json"),
        ]
        if spec.model:
            args += ["--model", spec.model]
        if spec.reasoning_effort:
            args += ["--reasoning-effort", spec.reasoning_effort]
        if spec.max_ai_credits > 0:
            args += ["--max-ai-credits", str(spec.max_ai_credits)]
        for host in ("http://localhost", "http://127.0.0.1", *spec.policy.allow_urls):
            args.append(f"--allow-url={host}")
        for rule in deny_rules(self.allow_git_push, spec.policy.denied_commands):
            args.append(f"--deny-tool={rule}")
        if spec.mcp_servers:
            mcp_file = log_dir / "mcp.json"
            mcp_file.write_text(json.dumps({"mcpServers": spec.mcp_servers}, indent=2))
            args += ["--additional-mcp-config", f"@{mcp_file}"]
        return args

    async def run(self, spec: SessionSpec) -> SessionOutcome:
        log_dir = spec.log_dir or (spec.working_directory / ".harness" / "sessions" / "cli")
        log_dir.mkdir(parents=True, exist_ok=True)
        log = EventLog(log_dir / "events.jsonl")
        outcome = SessionOutcome()
        session_id = str(uuid.uuid4())
        prompt_file = log_dir / "prompt.md"
        prompt_file.write_text(f"{spec.instructions}\n\n---\n\n{spec.prompt}", encoding="utf-8")
        message = f"Read {prompt_file} and follow its instructions exactly; it is your complete task."
        base = self._args(spec, log_dir)
        deadline = time.monotonic() + spec.timeout_s
        turn = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome.ok, outcome.timed_out = False, True
                outcome.error = f"session exceeded {spec.timeout_s / 60:.0f} minutes and was stopped"
                break
            argv = base + ([f"--session-id={session_id}"] if turn == 0 else [f"--resume={session_id}"]) + ["-p", message]
            log.write("cli_invoke", turn=turn)
            code, out, timed_out = await run_command(argv, spec.working_directory, None, remaining, log_dir / "cli-output.log")
            self._consume(out, outcome, log)
            if timed_out:
                outcome.ok, outcome.timed_out = False, True
                outcome.error = f"session exceeded {spec.timeout_s / 60:.0f} minutes and was killed"
                break
            if code != 0:
                outcome.ok = False
                outcome.error = f"copilot exited with {code}: {short(out[-600:], 600)}"
                break
            if spec.stop_check is None or outcome.stop_blocks >= spec.max_stop_blocks:
                break
            try:
                # The stop check runs the test suite; it shares the session's deadline.
                reason = await asyncio.wait_for(spec.stop_check(), max(deadline - time.monotonic(), 0.001))
            except TimeoutError:
                outcome.ok, outcome.timed_out = False, True
                outcome.error = f"session exceeded {spec.timeout_s / 60:.0f} minutes during the stop check"
                break
            if not reason:
                break
            outcome.stop_blocks += 1
            turn += 1
            cont = log_dir / f"continue-{turn}.md"
            cont.write_text(reason, encoding="utf-8")
            message = f"You are not done. Read {cont} for what still fails, fix it, then stop."
        outcome.credits = _credits_from_usage(log_dir / "usage.json")
        return outcome

    def _consume(self, output: str, outcome: SessionOutcome, log: EventLog) -> None:
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = str(obj.get("type", ""))
            data = obj.get("data") or {}
            log.write("cli_event", type=etype, data=short(data, 600))
            if etype == "tool.execution_start":
                outcome.tool_calls += 1
                if self.verbose:
                    print(f"  🔧 {data.get('toolName', '?')} {short(data.get('arguments'), 100)}", flush=True)
            elif etype == "assistant.message" and data.get("content"):
                outcome.final_message = data["content"]
                if self.verbose:
                    print(f"  💬 {short(data['content'], 200)}", flush=True)
            elif etype == "session.error":
                outcome.ok = False
                outcome.error = str(data.get("message", "session error"))


def _credits_from_usage(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0.0

    def find(obj: Any) -> float:
        if isinstance(obj, dict):
            for key in ("totalNanoAiu", "total_nano_aiu"):
                if isinstance(obj.get(key), (int, float)):
                    return float(obj[key]) / 1e9
            for key in ("aiCredits", "ai_credits", "totalAiCredits"):
                if isinstance(obj.get(key), (int, float)):
                    return float(obj[key])
            return sum(find(v) for v in obj.values())
        if isinstance(obj, list):
            return sum(find(v) for v in obj)
        return 0.0

    return find(data)
