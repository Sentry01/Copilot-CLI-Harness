"""
Copilot SDK client
==================

V1 shelled out to ``copilot -p ...`` and reverse-engineered what happened by
string-matching stdout for "✓", "Create", "[Tool: X]" etc. That was brittle:
completion/error detection keyed off the literal words "error"/"failed"
appearing anywhere in the model's prose, and the whole ``AssistantMessage`` /
``ToolUseBlock`` dataclass layer was a hand-rolled imitation of an SDK.

The Copilot SDK went GA in June 2026 (``pip install github-copilot-sdk``). It
gives us real streamed events, real session lifecycle, and — crucially — a
permission handler, which is where the command allowlist actually belongs and is
actually enforced.

SDK imports are isolated to this module so the rest of the package (security,
github_backend, their tests) runs without the SDK or its runtime installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import security
from .config import DEFAULT_MODEL

_SDK_INSTALL_HINT = (
    "The Copilot SDK is required.\n"
    "  pip install github-copilot-sdk\n"
    "  python -m copilot download-runtime"
)


@dataclass
class SessionResult:
    """Structured outcome of one agent session."""

    text: str = ""
    tool_calls: int = 0
    errors: list[str] = field(default_factory=list)
    blocked_commands: list[str] = field(default_factory=list)

    @property
    def errored(self) -> bool:
        return bool(self.errors)


def make_permission_handler(
    permission_mode: str,
    on_block: Optional[Callable[[str, str], None]] = None,
):
    """Build a Copilot SDK ``on_permission_request`` callback.

    ``permission_mode``:
      * ``"allowlist"`` (default) — shell commands run through
        :func:`security.evaluate_command`; everything else is approved once.
      * ``"approve-all"`` — approve everything (V1's de-facto behaviour, opt-in).

    Imports SDK permission types lazily so importing this module never requires
    the SDK.
    """
    from copilot.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
    from copilot.session_events import PermissionRequestShell

    def handler(request, invocation=None):
        if permission_mode == "approve-all":
            return PermissionDecisionApproveOnce()
        if isinstance(request, PermissionRequestShell):
            command = getattr(request, "full_command_text", "") or ""
            allowed, reason = security.evaluate_command(command)
            if not allowed:
                if on_block:
                    on_block(command, reason)
                return PermissionDecisionReject(feedback=f"Blocked by harness allowlist: {reason}")
        return PermissionDecisionApproveOnce()

    return handler


async def run_session(
    *,
    app_dir: Path,
    model: str = DEFAULT_MODEL,
    prompt: str,
    permission_mode: str = "allowlist",
    streaming: bool = True,
    line_sink: Optional[Callable[[str], None]] = None,
    github_token: Optional[str] = None,
) -> SessionResult:
    """Run a single agent turn against the Copilot SDK and return a result.

    ``line_sink`` receives streamed text fragments for live logging.
    """
    try:
        import asyncio

        from copilot import CopilotClient
        from copilot.session_events import (
            AssistantMessageData,
            AssistantMessageDeltaData,
            SessionIdleData,
            ToolCallData,
        )
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on env
        raise RuntimeError(_SDK_INSTALL_HINT) from exc

    result = SessionResult()
    handler = make_permission_handler(
        permission_mode,
        on_block=lambda cmd, reason: result.blocked_commands.append(f"{cmd} :: {reason}"),
    )

    client_kwargs = {"working_directory": str(Path(app_dir).resolve())}
    if github_token:
        client_kwargs["github_token"] = github_token

    async with CopilotClient(**client_kwargs) as client:
        async with await client.create_session(
            on_permission_request=handler,
            model=model,
            streaming=streaming,
        ) as session:
            done = asyncio.Event()

            def on_event(event):
                data = getattr(event, "data", None)
                if isinstance(data, AssistantMessageDeltaData):
                    chunk = data.delta_content or ""
                    result.text += chunk
                    if line_sink and chunk:
                        line_sink(chunk)
                elif isinstance(data, AssistantMessageData):
                    # Non-streaming fall-back: whole message at once.
                    if not streaming and getattr(data, "content", None):
                        result.text += data.content
                        if line_sink:
                            line_sink(data.content)
                elif isinstance(data, ToolCallData):
                    result.tool_calls += 1
                elif isinstance(data, SessionIdleData):
                    done.set()

            session.on(on_event)
            await session.send(prompt)
            await done.wait()

    return result
