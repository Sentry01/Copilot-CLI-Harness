"""GitHub Copilot SDK backend (recommended).

Uses ``github-copilot-sdk`` to drive the Copilot runtime over JSON-RPC:
* every tool permission request goes through the harness ``Policy`` (parsed shell
  commands, write paths, URLs, MCP servers), not a regex over shell strings;
* ``on_agent_stop`` lets the harness refuse to let the agent stop while its target
  tests still fail (bounded by ``max_stop_blocks``);
* sessions are hermetic: no config discovery, custom instructions, file hooks or memory,
  so an agent cannot plant configuration that steers a later session;
* typed events are logged to JSONL, and AI-credit usage is accumulated.
"""

from __future__ import annotations

import contextlib
from typing import Any

from copilot_harness.backends.base import AgentBackend, EventLog, SessionOutcome, SessionSpec, short
from copilot_harness.policy import Decision, Policy, ShellCommand


def _import_sdk() -> Any:
    try:
        import copilot  # noqa: F401
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RuntimeError("github-copilot-sdk is not installed: pip install github-copilot-sdk") from exc
    import copilot

    return copilot


def decide(policy: Policy, request: Any) -> Decision:
    """Map an SDK ``PermissionRequest`` variant onto a policy decision."""
    from copilot.generated import session_events as ev

    if getattr(request, "request_sandbox_bypass", False):
        return Decision.deny("sandbox bypass requests are not allowed")

    if isinstance(request, ev.PermissionRequestShell):
        segments = {s.identifier: s.full_command_text for s in (request.command_segments or [])}
        commands = [
            ShellCommand(c.identifier, bool(c.read_only), segments.get(c.identifier, request.full_command_text))
            for c in request.commands
        ]
        urls = [getattr(u, "url", str(u)) for u in (request.possible_urls or [])]
        return policy.check_shell(
            commands,
            full_text=request.full_command_text,
            possible_paths=list(request.possible_paths or []),
            possible_urls=urls,
            has_write_redirection=bool(request.has_write_file_redirection),
            cwd=request.resolved_working_directory,
        )
    if isinstance(request, ev.PermissionRequestWrite):
        return policy.check_write(request.resolved_path or request.file_name)
    if isinstance(request, ev.PermissionRequestRead):
        return policy.check_read(request.resolved_path or request.path)
    if isinstance(request, ev.PermissionRequestUrl):
        return policy.check_url(request.url)
    if isinstance(request, ev.PermissionRequestMcp):
        return policy.check_mcp(request.server_name, request.tool_name, request.args)
    if isinstance(request, ev.PermissionRequestCustomTool):
        return Decision.ok()
    if isinstance(request, ev.PermissionRequestMemory):
        return Decision.deny("persistent memory is disabled so sessions stay reproducible")
    kind = getattr(request, "kind", type(request).__name__)
    return Decision.deny(f"permission kind {kind!r} is not allowed in harness sessions")


class SdkBackend(AgentBackend):
    name = "sdk"

    def __init__(self, cli_path: str = "", verbose: bool = True):
        self.cli_path = cli_path
        self.verbose = verbose
        self._client: Any = None

    async def _ensure_client(self, working_directory: str) -> Any:
        if self._client is None:
            copilot = _import_sdk()
            kwargs: dict[str, Any] = {"working_directory": working_directory}
            if self.cli_path:
                kwargs["connection"] = copilot.RuntimeConnection.for_stdio(path=self.cli_path)
            self._client = copilot.CopilotClient(**kwargs)
            await self._client.start()
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.stop()
            self._client = None

    async def run(self, spec: SessionSpec) -> SessionOutcome:
        _import_sdk()
        from copilot.generated import session_events as ev
        from copilot.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

        client = await self._ensure_client(str(spec.working_directory))
        log = EventLog(spec.log_dir / "events.jsonl" if spec.log_dir else None)
        outcome = SessionOutcome()

        def on_permission(request: Any, _invocation: Any) -> Any:
            decision = decide(spec.policy, request)
            detail = getattr(request, "full_command_text", None) or getattr(request, "file_name", None) \
                or getattr(request, "url", None) or getattr(request, "path", None) or getattr(request, "tool_name", "")
            log.write("permission", request=getattr(request, "kind", "?"), detail=detail,
                      allow=decision.allow, reason=decision.reason)
            if decision.allow:
                return PermissionDecisionApproveOnce()
            outcome.denials.append(f"{getattr(request, 'kind', '?')}: {short(detail, 120)} -> {decision.reason}")
            self._say(f"  ⛔ denied {short(detail, 80)}: {decision.reason}")
            return PermissionDecisionReject(feedback=decision.reason)

        async def on_agent_stop(_input: Any, _invocation: Any) -> Any:
            if spec.stop_check is None or outcome.stop_blocks >= spec.max_stop_blocks:
                return None
            reason = await spec.stop_check()
            log.write("stop_check", blocked=bool(reason), reason=reason or "")
            if not reason:
                return None
            outcome.stop_blocks += 1
            self._say(f"  ↩ not done yet ({outcome.stop_blocks}/{spec.max_stop_blocks}): targets still failing")
            return {"decision": "block", "reason": reason}

        def on_event(event: Any) -> None:
            data = event.data
            etype = getattr(event.type, "value", str(event.type))
            match data:
                case ev.ToolExecutionStartData():
                    outcome.tool_calls += 1
                    log.write("tool_start", tool=data.tool_name, args=short(data.arguments, 400))
                    self._say(f"  🔧 {data.tool_name} {short(data.arguments, 100)}")
                case ev.ToolExecutionCompleteData():
                    err = data.error and getattr(data.error, "message", str(data.error))
                    log.write("tool_end", ok=data.success, error=err or "")
                case ev.AssistantMessageData():
                    outcome.final_message = data.content or outcome.final_message
                    log.write("assistant", content=data.content)
                    if data.content:
                        self._say(f"  💬 {short(data.content, 200)}")
                case ev.AssistantUsageData():
                    usage = data.copilot_usage
                    if usage is not None and usage.total_nano_aiu:
                        outcome.credits += usage.total_nano_aiu / 1e9
                    log.write("usage", model=data.model, cost=data.cost)
                case ev.SessionErrorData():
                    outcome.ok = False
                    outcome.error = f"{data.error_type}: {data.message}"
                    log.write("error", error_type=data.error_type, message=data.message)
                    self._say(f"  ❌ session error: {data.message}")
                case _:
                    log.write("event", type=etype)

        session_kwargs: dict[str, Any] = dict(
            on_permission_request=on_permission,
            working_directory=str(spec.working_directory),
            system_message={"mode": "append", "content": spec.instructions},
            hooks={"on_agent_stop": on_agent_stop},
            infinite_sessions={"enabled": True},
            enable_config_discovery=False,
            skip_custom_instructions=True,
            enable_file_hooks=False,
            memory={"enabled": False},
            enable_session_store=False,
            streaming=False,
            on_event=on_event,
        )
        if spec.model:
            session_kwargs["model"] = spec.model
        if spec.reasoning_effort:
            session_kwargs["reasoning_effort"] = spec.reasoning_effort
        if spec.max_ai_credits > 0:
            session_kwargs["session_limits"] = {"max_ai_credits": spec.max_ai_credits}
        if spec.mcp_servers:
            session_kwargs["mcp_servers"] = spec.mcp_servers

        log.write("session_start", role=spec.role, model=spec.model or "<default>")
        try:
            session = await client.create_session(**session_kwargs)
        except Exception as exc:
            outcome.ok = False
            outcome.error = f"could not create session: {exc}"
            log.write("error", message=outcome.error)
            return outcome
        try:
            await session.send_and_wait(spec.prompt, timeout=spec.timeout_s)
        except TimeoutError:
            outcome.timed_out = True
            outcome.ok = False
            outcome.error = f"session exceeded {spec.timeout_s / 60:.0f} minutes and was aborted"
            with contextlib.suppress(Exception):
                await session.abort()
        except Exception as exc:
            outcome.ok = False
            outcome.error = f"{type(exc).__name__}: {exc}"
        finally:
            with contextlib.suppress(Exception):
                await session.disconnect()
            log.write("session_end", ok=outcome.ok, error=outcome.error, credits=outcome.credits,
                      tool_calls=outcome.tool_calls, stop_blocks=outcome.stop_blocks)
        return outcome

    def _say(self, line: str) -> None:
        if self.verbose:
            print(line, flush=True)


async def list_models(cli_path: str = "") -> list[dict[str, Any]]:
    backend = SdkBackend(cli_path=cli_path, verbose=False)
    try:
        client = await backend._ensure_client(".")
        models = await client.list_models()
        out = []
        for m in models:
            out.append({"id": getattr(m, "id", str(m)), "name": getattr(m, "name", "")})
        return out
    finally:
        await backend.close()


async def auth_status(cli_path: str = "") -> tuple[bool, str]:
    """(authenticated, description) without sending any prompt (no AI credits)."""
    backend = SdkBackend(cli_path=cli_path, verbose=False)
    try:
        client = await backend._ensure_client(".")
        status = await client.get_auth_status()
        who = status.login or "?"
        detail = f"{who} via {status.authType or '?'} on {status.host or 'github.com'}"
        return bool(status.isAuthenticated), (detail if status.isAuthenticated else (status.statusMessage or "not logged in"))
    finally:
        await backend.close()
