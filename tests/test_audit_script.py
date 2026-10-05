"""acceptance/scripts/audit.sh: every supported dependency layout is audited or fails loudly."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from copilot_harness.project import TEMPLATES

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
SYSTEM_PATH = "/usr/bin:/bin"


def run_audit(tmp_path: Path, files: dict[str, str], failing: tuple[str, ...] = ()) -> tuple[int, str, list[str]]:
    """Run the script with stub npm/npx/pipx that log their arguments (and fail if listed)."""
    project = tmp_path / "p"
    (project / "acceptance" / "scripts").mkdir(parents=True)
    shutil.copy(TEMPLATES / "acceptance" / "scripts" / "audit.sh", project / "acceptance" / "scripts" / "audit.sh")
    (project / "app").mkdir()
    for rel, text in files.items():
        (project / "app" / rel).write_text(text)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    for tool in ("npm", "npx", "pipx"):
        code = 1 if tool in failing else 0
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\nexit {code}\n')
        stub.chmod(0o755)
    r = subprocess.run(["bash", "acceptance/scripts/audit.sh"], cwd=project, capture_output=True, text=True,
                       env={"PATH": f"{bin_dir}:{SYSTEM_PATH}", "HOME": str(tmp_path)})
    logged = calls.read_text().splitlines() if calls.exists() else []
    return r.returncode, r.stdout + r.stderr, logged


def test_npm_lockfile_is_audited(tmp_path):
    code, _, calls = run_audit(tmp_path, {"package.json": "{}", "package-lock.json": "{}"})
    assert code == 0 and calls == ["npm audit --audit-level=high --omit=dev"]


def test_findings_fail_the_audit(tmp_path):
    code, _, _ = run_audit(tmp_path, {"package.json": "{}", "package-lock.json": "{}"}, failing=("npm",))
    assert code == 1


def test_pnpm_lockfile_is_audited(tmp_path):
    code, _, calls = run_audit(tmp_path, {"package.json": "{}", "pnpm-lock.yaml": ""})
    assert code == 0 and calls == ["npx --yes pnpm@10 audit --prod --audit-level high"]


def test_requirements_txt_is_audited(tmp_path):
    code, _, calls = run_audit(tmp_path, {"requirements.txt": "flask==3.0.0\n"})
    assert code == 0 and calls == ["pipx run pip-audit -r app/requirements.txt"]


def test_uv_lock_is_exported_then_audited(tmp_path):
    code, _, calls = run_audit(tmp_path, {"pyproject.toml": "", "uv.lock": ""})
    assert code == 0 and len(calls) == 2
    assert calls[0].startswith("pipx run uv export --project app --frozen --no-dev --no-emit-project")
    assert calls[1].startswith("pipx run pip-audit -r ") and calls[1].endswith("--disable-pip --no-deps")


@pytest.mark.parametrize("files, message", [
    ({"package.json": "{}", "yarn.lock": ""}, "Yarn projects are not audited"),
    ({"package.json": "{}"}, "has no lockfile"),
    ({"pyproject.toml": "", "poetry.lock": ""}, "poetry.lock is not audited directly"),
    ({"pyproject.toml": ""}, "Python dependencies are not pinned"),
])
def test_unaudited_layouts_fail_explicitly(tmp_path, files, message):
    code, out, calls = run_audit(tmp_path, files)
    assert code == 1 and message in out and calls == []


def test_go_without_a_toolchain_fails(tmp_path):
    if shutil.which("go", path=SYSTEM_PATH):
        pytest.skip("go is installed in the system path")
    code, out, _ = run_audit(tmp_path, {"go.mod": "module x\n"})
    assert code == 1 and "add actions/setup-go" in out


def test_nothing_to_audit(tmp_path):
    code, out, calls = run_audit(tmp_path, {"server.js": ""})
    assert code == 0 and "nothing to audit" in out and calls == []
