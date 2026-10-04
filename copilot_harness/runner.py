"""Run the acceptance suite and report per-test-ID outcomes.

The harness, never the agent, decides what passes. Each verification run:
1. prepares the app (``install`` when dependency manifests changed, then ``build``),
2. runs the suite against a *fresh* app instance on a free port (so a dev server an
   agent left running can never serve stale code to the verifier),
3. parses the machine-readable report into ``RunReport``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import signal
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from copilot_harness.config import HarnessConfig
from copilot_harness.models import AppContract, Contract, load_model
from copilot_harness.paths import ProjectPaths
from copilot_harness.results import RunReport, parse_junit_xml, parse_playwright_json

DEPENDENCY_MANIFESTS = (
    "harness/app-contract.json",
    "app/package.json",
    "app/package-lock.json",
    "app/pnpm-lock.yaml",
    "app/yarn.lock",
    "app/requirements.txt",
    "app/pyproject.toml",
    "app/poetry.lock",
    "app/uv.lock",
    "app/go.sum",
)


@dataclass
class RunResult:
    report: RunReport
    exit_code: int = 0
    timed_out: bool = False
    duration_s: float = 0.0
    run_dir: Path | None = None
    infra_error: str = ""
    output_tail: str = ""
    targeted: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """False when the run could not judge tests at all (infra or collection failure)."""
        return not self.infra_error and not self.report.collection_errors and not self.timed_out


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def grep_for(ids: Iterable[str]) -> str:
    """Regex matching titles that start with one of the ids (``FUNC-001:``)."""
    return "(" + "|".join(sorted(re.escape(i) for i in ids)) + "):"


async def run_command(
    argv: list[str] | str,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_s: float = 600,
    log_path: Path | None = None,
) -> tuple[int, str, bool]:
    """Run a command in its own process group; kill the whole group on timeout."""
    shell = isinstance(argv, str)
    full_env = {**os.environ, **(env or {})}
    if shell:
        proc = await asyncio.create_subprocess_shell(
            argv, cwd=cwd, env=full_env, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=full_env, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True,
        )
    timed_out = False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        timed_out = True
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, _ = await proc.communicate()
    text = (out or b"").decode("utf-8", errors="replace")
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"$ {argv if shell else ' '.join(argv)}\n{text}\n")
    return (proc.returncode if proc.returncode is not None else -1), text, timed_out


class TestRunner:
    __test__ = False  # not a pytest test class

    def __init__(self, paths: ProjectPaths, cfg: HarnessConfig):
        self.paths = paths
        self.cfg = cfg
        self._stamp_file = paths.local / "deps.sha256"

    # ------------------------------------------------------------------
    def app_contract(self) -> AppContract | None:
        try:
            return load_model(AppContract, self.paths.app_contract)
        except Exception:
            return None

    def _deps_digest(self) -> str:
        h = hashlib.sha256()
        for rel in DEPENDENCY_MANIFESTS:
            p = self.paths.root / rel
            if p.exists():
                h.update(rel.encode())
                h.update(p.read_bytes())
        return h.hexdigest()

    async def prepare_app(self, log_path: Path) -> str:
        """Install (if dependency manifests changed) and build. Returns an error or ''."""
        contract = self.app_contract()
        if contract is None:
            return "harness/app-contract.json is missing or invalid"
        env = {**contract.env, "CI": "1"}
        digest = self._deps_digest()
        stamp = self._stamp_file.read_text().strip() if self._stamp_file.exists() else ""
        if digest != stamp:
            for cmd in contract.install:
                code, out, to = await run_command(cmd, self.paths.root, env, 900, log_path)
                if code != 0 or to:
                    return f"install command failed ({cmd!r}):\n{out[-2500:]}"
            self._stamp_file.parent.mkdir(parents=True, exist_ok=True)
            self._stamp_file.write_text(digest)
        for cmd in contract.build:
            code, out, to = await run_command(cmd, self.paths.root, env, 900, log_path)
            if code != 0 or to:
                return f"build command failed ({cmd!r}):\n{out[-2500:]}"
        return ""

    async def ensure_acceptance_deps(self, log_path: Path) -> str:
        wd = self.paths.root / self.cfg.runner.workdir
        if self.cfg.runner.kind != "playwright" or (wd / "node_modules" / "@playwright" / "test").exists():
            return ""
        cmd = ["npm", "ci"] if (wd / "package-lock.json").exists() else ["npm", "install"]
        code, out, to = await run_command(cmd, wd, None, 900, log_path)
        if code != 0 or to:
            return f"installing acceptance dependencies failed:\n{out[-2500:]}"
        if not os.environ.get("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"):
            code, out, to = await run_command(["npx", "playwright", "install", "chromium"], wd, None, 900, log_path)
            if code != 0 or to:
                return f"installing the Playwright browser failed:\n{out[-2500:]}"
        return ""

    # ------------------------------------------------------------------
    async def run(
        self,
        ids: Iterable[str] | None = None,
        repeat_each: int = 1,
        label: str = "run",
        prepare: bool = True,
    ) -> RunResult:
        ids = sorted(set(ids)) if ids is not None else None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        run_dir = self.paths.runs / f"{stamp}-{label}"
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "output.log"
        started = time.monotonic()
        result = RunResult(report=RunReport(), run_dir=run_dir, targeted=list(ids or []))

        if ids is not None and not ids:
            return result
        for step in (self.ensure_acceptance_deps, self.prepare_app) if prepare else (self.ensure_acceptance_deps,):
            err = await step(log_path)
            if err:
                result.infra_error = err
                result.duration_s = time.monotonic() - started
                return result

        if self.cfg.runner.kind == "playwright":
            await self._run_playwright(result, ids, repeat_each, run_dir, log_path)
        else:
            await self._run_junit(result, run_dir, log_path)
        result.duration_s = time.monotonic() - started
        (run_dir / "summary.json").write_text(json.dumps({
            "label": label,
            "targeted": result.targeted,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "infra_error": result.infra_error,
            "collection_errors": result.report.collection_errors,
            "outcomes": {k: v.status for k, v in sorted(result.report.outcomes.items())},
        }, indent=2))
        return result

    def _playwright_env(self, report_path: Path) -> dict[str, str]:
        contract = self.app_contract()
        env = {
            "PLAYWRIGHT_JSON_OUTPUT_FILE": str(report_path),
            "HARNESS_PROJECT_ROOT": str(self.paths.root),
            "APP_PORT": str(free_port()),
            "HARNESS_REUSE_SERVER": "0",
            "FORCE_COLOR": "0",
        }
        if contract is not None:
            env.update({k: v for k, v in contract.env.items() if k not in env})
        return env

    async def _run_playwright(
        self, result: RunResult, ids: list[str] | None, repeat_each: int, run_dir: Path, log_path: Path
    ) -> None:
        report_path = run_dir / "report.json"
        argv = ["npx", "playwright", "test", "--reporter=json", f"--workers={self.cfg.runner.workers}"]
        if repeat_each > 1:
            argv.append(f"--repeat-each={repeat_each}")
        if ids:
            argv += ["--grep", grep_for(ids)]
        code, out, timed_out = await run_command(
            argv, self.paths.root / self.cfg.runner.workdir, self._playwright_env(report_path),
            self.cfg.runner.timeout_minutes * 60, log_path,
        )
        result.exit_code, result.timed_out, result.output_tail = code, timed_out, out[-4000:]
        if not report_path.exists():
            result.infra_error = f"runner produced no report (exit {code}):\n{out[-2500:]}"
            return
        result.report = parse_playwright_json(json.loads(report_path.read_text(encoding="utf-8")))
        if ids is None and not result.report.outcomes and not result.report.collection_errors and code != 0:
            result.infra_error = f"runner failed before running tests (exit {code}):\n{out[-2500:]}"

    async def _run_junit(self, result: RunResult, run_dir: Path, log_path: Path) -> None:
        rc = self.cfg.runner
        if not rc.command or not rc.junit_path:
            result.infra_error = "runner.kind = 'junit' requires runner.command and runner.junit_path"
            return
        junit = self.paths.root / rc.junit_path
        junit.unlink(missing_ok=True)
        code, out, timed_out = await run_command(
            rc.command, self.paths.root / rc.workdir, {"HARNESS_PROJECT_ROOT": str(self.paths.root)},
            rc.timeout_minutes * 60, log_path,
        )
        result.exit_code, result.timed_out, result.output_tail = code, timed_out, out[-4000:]
        if not junit.exists():
            result.infra_error = f"runner produced no JUnit report at {rc.junit_path} (exit {code}):\n{out[-2500:]}"
            return
        result.report = parse_junit_xml(junit.read_text(encoding="utf-8"))
        (run_dir / "junit.xml").write_text(junit.read_text(encoding="utf-8"), encoding="utf-8")

    async def smoke(self) -> str:
        """Install/build, start the app on a free port, and check health (+ reset hook). '' = healthy."""
        contract = self.app_contract()
        if contract is None:
            return "harness/app-contract.json is missing or invalid"
        log_path = self.paths.runs / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-smoke" / "output.log"
        err = await self.prepare_app(log_path)
        if err:
            return err
        port = free_port()
        parsed = urlparse(contract.base_url)
        host = parsed.hostname or "127.0.0.1"
        base = f"{parsed.scheme}://{host}:{port}"
        env = {**os.environ, **contract.env, "PORT": str(port), "HOST": host, "NODE_ENV": "test", "APP_ENV": "test"}
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log:
            proc = await asyncio.create_subprocess_shell(
                contract.start, cwd=self.paths.root, env=env, stdout=log, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                healthy = await self._wait_healthy(proc, base + contract.health_path, contract.startup_timeout_seconds)
                if not healthy:
                    log.flush()
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-2500:]
                    return f"app did not answer 200 on {contract.health_path} within {contract.startup_timeout_seconds}s:\n{tail}"
                api = load_model(Contract, self.paths.contract) if self.paths.contract.exists() else None
                hook = api.test_hooks.reset if api else None
                if hook:
                    method, _, path = hook.strip().partition(" ")
                    status = await asyncio.to_thread(_http_status, method or "POST", base + path.strip())
                    if not 200 <= status < 300:
                        return f"reset hook {hook!r} returned {status} in test mode; it must return 2xx"
                return ""
            finally:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()

    async def _wait_healthy(self, proc: asyncio.subprocess.Process, url: str, timeout_s: int) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if proc.returncode is not None:
                return False
            if await asyncio.to_thread(_http_status, "GET", url) == 200:
                return True
            await asyncio.sleep(0.5)
        return False

    async def list_ids(self) -> tuple[list[str], list[str]]:
        """IDs the runner can discover, plus collection errors (compile errors, bad imports)."""
        if self.cfg.runner.kind != "playwright":
            return [], []
        log = self.paths.runs / "list.log"
        err = await self.ensure_acceptance_deps(log)
        if err:
            return [], [err]
        report_path = self.paths.runs / "list.json"
        report_path.unlink(missing_ok=True)
        code, out, _ = await run_command(
            ["npx", "playwright", "test", "--list", "--reporter=json"],
            self.paths.root / self.cfg.runner.workdir, self._playwright_env(report_path), 300, log,
        )
        if not report_path.exists():
            return [], [f"`playwright test --list` failed (exit {code}):\n{out[-2500:]}"]
        report = parse_playwright_json(json.loads(report_path.read_text(encoding="utf-8")))
        return sorted(report.outcomes), report.collection_errors


def _http_status(method: str, url: str) -> int:
    req = urllib.request.Request(url, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0
