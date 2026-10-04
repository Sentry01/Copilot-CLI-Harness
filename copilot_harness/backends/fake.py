"""Scripted backend for tests and dry runs: handlers play the agent's part."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from copilot_harness.backends.base import AgentBackend, SessionOutcome, SessionSpec


class PolicyViolation(PermissionError):
    pass


@dataclass
class FakeTurn:
    spec: SessionSpec
    turn: int  # 0 = initial prompt, n = n-th continuation after a blocked stop
    reason: str = ""

    def write(self, rel: str, content: str) -> None:
        """Write like an agent would, subject to the session's permission policy."""
        decision = self.spec.policy.check_write(rel)
        if not decision.allow:
            raise PolicyViolation(decision.reason)
        path = self.spec.working_directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def force_write(self, rel: str, content: str) -> None:
        """Write bypassing the policy (simulates an agent escaping its tool permissions)."""
        path = self.spec.working_directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


Handler = Callable[[FakeTurn], Awaitable[None] | None]


@dataclass
class FakeBackend(AgentBackend):
    handlers: dict[str, Handler] = field(default_factory=dict)
    calls: list[FakeTurn] = field(default_factory=list)
    name: str = "fake"

    async def run(self, spec: SessionSpec) -> SessionOutcome:
        handler = self.handlers.get(spec.role)
        outcome = SessionOutcome()
        if handler is None:
            return outcome
        turn = FakeTurn(spec, 0)
        try:
            await self._call(handler, turn)
            while spec.stop_check is not None and outcome.stop_blocks < spec.max_stop_blocks:
                reason = await spec.stop_check()
                if not reason:
                    break
                outcome.stop_blocks += 1
                await self._call(handler, FakeTurn(spec, outcome.stop_blocks, reason))
        except PolicyViolation as exc:
            outcome.denials.append(str(exc))
        return outcome

    async def _call(self, handler: Handler, turn: FakeTurn) -> None:
        self.calls.append(turn)
        result = handler(turn)
        if inspect.isawaitable(result):
            await result
