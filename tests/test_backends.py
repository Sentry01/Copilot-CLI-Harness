import json
from pathlib import Path

import pytest

from copilot_harness.backends.base import SessionSpec
from copilot_harness.backends.cli import CliBackend, _credits_from_usage, deny_rules
from copilot_harness.policy import Policy

copilot = pytest.importorskip("copilot")


def test_sdk_permission_mapping_uses_real_sdk_types():
    from copilot.generated.session_events import (
        PermissionRequestMcp,
        PermissionRequestMemory,
        PermissionRequestShell,
        PermissionRequestShellCommand,
        PermissionRequestShellPossibleUrl,
        PermissionRequestUrl,
        PermissionRequestWrite,
    )

    from copilot_harness.backends.sdk import decide

    pol = Policy(root="/home/dev/proj", role="coder")

    def shell(text, ident, read_only, paths=(), urls=()):
        return PermissionRequestShell(
            can_offer_session_approval=False, commands=[PermissionRequestShellCommand(ident, read_only)],
            full_command_text=text, has_write_file_redirection=False, intention="", possible_paths=list(paths),
            possible_urls=[PermissionRequestShellPossibleUrl(url=u) for u in urls])

    assert decide(pol, shell("npm test", "npm", False, ["app"])).allow
    assert not decide(pol, shell("git commit -m x", "git commit", False)).allow
    assert not decide(pol, shell("curl https://x.io", "curl", True, urls=["https://x.io"])).allow
    write = PermissionRequestWrite(can_offer_session_approval=False, diff="", intention="",
                                   file_name="acceptance/specs/functional/a.spec.ts",
                                   resolved_path="/home/dev/proj/acceptance/specs/functional/a.spec.ts")
    assert not decide(pol, write).allow
    assert decide(pol, PermissionRequestUrl(intention="", url="http://localhost:3000")).allow
    assert not decide(pol, PermissionRequestMcp(read_only=False, server_name="github-mcp-server",
                                                tool_name="create_issue", tool_title="")).allow
    assert not decide(pol, PermissionRequestMemory(fact="x")).allow


def test_cli_deny_rules_cover_dangerous_commands():
    rules = deny_rules(allow_git_push=False)
    for expected in ("shell(sudo)", "shell(gh)", "shell(git push)", "shell(git commit)", "shell(npm publish)"):
        assert expected in rules
    assert "shell(git status)" not in rules
    assert "shell(git push)" not in deny_rules(allow_git_push=True)


def test_cli_args(tmp_path: Path):
    spec = SessionSpec(role="coder", prompt="p", instructions="i", policy=Policy(root=tmp_path, role="coder",
                       allow_urls=["registry.npmjs.org"]), working_directory=tmp_path, model="m", max_ai_credits=50,
                       mcp_servers={"playwright": {"command": "npx", "args": [], "tools": ["*"]}})
    args = CliBackend(cli_path="copilot")._args(spec, tmp_path)
    assert args[:3] == ["copilot", "--output-format", "json"]
    for flag in ("--no-ask-user", "--no-custom-instructions", "--disable-builtin-mcps", "--allow-all-tools"):
        assert flag in args
    assert "--allow-all-paths" not in args and "--yolo" not in args
    assert "--allow-url=registry.npmjs.org" in args and "--deny-tool=shell(sudo)" in args
    assert args[args.index("--max-ai-credits") + 1] == "50"
    assert json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["playwright"]["command"] == "npx"


def test_credits_from_usage(tmp_path: Path):
    f = tmp_path / "usage.json"
    f.write_text(json.dumps({"models": [{"totalNanoAiu": 2_500_000_000}, {"totalNanoAiu": 500_000_000}]}))
    assert _credits_from_usage(f) == pytest.approx(3.0)
    assert _credits_from_usage(tmp_path / "missing.json") == 0.0


def test_cli_deny_rules_follow_the_effective_policy(tmp_path: Path):
    pol = Policy(root=tmp_path, role="coder", extra_denied_commands=["python"], extra_allowed_commands=["docker"])
    spec = SessionSpec(role="coder", prompt="p", instructions="i", policy=pol, working_directory=tmp_path)
    args = CliBackend(cli_path="copilot")._args(spec, tmp_path)
    assert "--deny-tool=shell(python)" in args
    assert "--deny-tool=shell(docker)" not in args


def test_cli_session_deadline_covers_continuations(tmp_path: Path):
    import asyncio
    import time

    fake = tmp_path / "copilot"
    fake.write_text("#!/bin/sh\nsleep 1\necho '{\"type\":\"assistant.message\",\"data\":{\"content\":\"working\"}}'\n")
    fake.chmod(0o755)
    checks = []

    async def never_done():
        checks.append(1)
        return "still failing"

    spec = SessionSpec(role="coder", prompt="p", instructions="i", policy=Policy(root=tmp_path, role="coder"),
                       working_directory=tmp_path, timeout_s=2.5, stop_check=never_done, max_stop_blocks=50,
                       log_dir=tmp_path / "log")
    started = time.monotonic()
    outcome = asyncio.run(CliBackend(cli_path=str(fake), verbose=False).run(spec))
    assert time.monotonic() - started < 6  # bounded by the deadline, not by 50 continuations
    assert outcome.timed_out and not outcome.ok
    assert len(checks) < 5


def test_cli_stop_check_shares_the_session_deadline(tmp_path: Path):
    """A stop check (the suite run) that outlives the deadline is cancelled, and the
    process group it started is killed rather than left running."""
    import asyncio
    import os
    import time

    from copilot_harness.runner import run_command

    fake = tmp_path / "copilot"
    fake.write_text("#!/bin/sh\necho '{\"type\":\"assistant.message\",\"data\":{\"content\":\"done\"}}'\n")
    fake.chmod(0o755)
    pid_file = tmp_path / "suite.pid"

    async def slow_suite():
        await run_command(f"echo $$ > {pid_file}; exec sleep 30", tmp_path, timeout_s=60)
        return "still failing"

    spec = SessionSpec(role="coder", prompt="p", instructions="i", policy=Policy(root=tmp_path, role="coder"),
                       working_directory=tmp_path, timeout_s=1.0, stop_check=slow_suite, max_stop_blocks=5,
                       log_dir=tmp_path / "log")
    started = time.monotonic()
    outcome = asyncio.run(CliBackend(cli_path=str(fake), verbose=False).run(spec))
    assert time.monotonic() - started < 5
    assert outcome.timed_out and not outcome.ok
    assert "stop check" in (outcome.error or "")
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the suite's process group survived the cancelled stop check")
