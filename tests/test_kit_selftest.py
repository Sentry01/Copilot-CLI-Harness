"""Run the acceptance support kit's self-test with real Playwright (opt-in: HARNESS_E2E=1)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from copilot_harness.project import create_project
from copilot_harness.runner import free_port

HERE = Path(__file__).parent / "kit_selftest"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("HARNESS_E2E") != "1", reason="set HARNESS_E2E=1 to run Playwright tests"),
    pytest.mark.skipif(shutil.which("npm") is None, reason="needs Node.js/npm"),
]


def test_every_kit_helper_passes_good_and_catches_bad(tmp_path: Path) -> None:
    prd = tmp_path / "PRD.md"
    prd.write_text("# kit self-test\n")
    paths = create_project(tmp_path / "kit", prd, install=True, out=lambda *_: None)
    shutil.copy(HERE / "server.js", paths.app / "server.js")
    (paths.specs / "functional" / "kit.spec.ts").write_text((HERE / "kit.spec.ts").read_text())
    # Minimal contract: health_path, startup timeout etc. come from the kit's defaults.
    paths.app_contract.write_text(json.dumps({"stack": "node", "start": "node app/server.js"}))
    paths.contract.write_text(json.dumps({"test_hooks": {"reset": "POST /__test__/reset"}}))
    env = {**os.environ, "APP_PORT": str(free_port())}
    if not env.get("PW_CHROMIUM_EXECUTABLE") and Path("/opt/pw-browsers/chromium").exists():
        env["PW_CHROMIUM_EXECUTABLE"] = "/opt/pw-browsers/chromium"

    typecheck = subprocess.run(["npx", "tsc", "--noEmit", "-p", "."], cwd=paths.acceptance, capture_output=True, text=True)
    assert typecheck.returncode == 0, typecheck.stdout

    run = subprocess.run(["npx", "playwright", "test", "--reporter=list"], cwd=paths.acceptance, env=env,
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stdout[-6000:] + run.stderr[-2000:]
    assert "26 passed" in run.stdout, run.stdout[-3000:]
