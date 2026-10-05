"""Backend-neutral description of one agent session."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from copilot_harness.models import utcnow
from copilot_harness.policy import Policy

# Returns a reason to keep working (the agent is not allowed to stop yet), or None.
StopCheck = Callable[[], Awaitable[str | None]]


@dataclass
class SessionSpec:
    role: str
    prompt: str
    instructions: str
    policy: Policy
    working_directory: Path
    model: str = ""
    reasoning_effort: str = ""
    timeout_s: float = 45 * 60
    max_ai_credits: float = 0.0
    stop_check: StopCheck | None = None
    max_stop_blocks: int = 0
    log_dir: Path | None = None
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class SessionOutcome:
    ok: bool = True
    error: str = ""
    timed_out: bool = False
    credits: float = 0.0
    tool_calls: int = 0
    denials: list[str] = field(default_factory=list)
    stop_blocks: int = 0
    final_message: str = ""


class AgentBackend(ABC):
    name = "abstract"

    @abstractmethod
    async def run(self, spec: SessionSpec) -> SessionOutcome: ...

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass

    async def __aenter__(self) -> AgentBackend:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


class EventLog:
    """Append-only JSONL audit log of everything that happened in a session."""

    def __init__(self, path: Path | None):
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, **data: Any) -> None:
        if self.path is None:
            return
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": utcnow(), "kind": kind, **data}, default=str, ensure_ascii=False) + "\n")


def short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
