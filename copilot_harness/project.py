"""Create harness-managed projects and start feature changes."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from string import Template

from copilot_harness.config import default_config_toml
from copilot_harness.gitops import Git
from copilot_harness.models import State, load_model, write_json
from copilot_harness.paths import ProjectPaths

TEMPLATES = Path(__file__).parent / "templates"


class ProjectError(RuntimeError):
    pass


def create_project(root: str | Path, prd: str | Path, name: str | None = None, force: bool = False,
                   install: bool = True, out=print) -> ProjectPaths:
    paths = ProjectPaths.at(root)
    prd_path = Path(prd).expanduser()
    if not prd_path.is_file():
        raise ProjectError(f"PRD file not found: {prd_path}")
    if paths.root.exists() and any(paths.root.iterdir()) and not force:
        raise ProjectError(f"{paths.root} is not empty (use --force to scaffold into it)")
    name = name or paths.root.name

    paths.root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATES / "acceptance", paths.acceptance, dirs_exist_ok=True)
    for category in ("functional", "performance", "security", "usability"):
        (paths.specs / category).mkdir(parents=True, exist_ok=True)
    paths.workflows.mkdir(parents=True, exist_ok=True)
    for wf in (TEMPLATES / "github" / "workflows").glob("*.yml"):
        shutil.copy(wf, paths.workflows / wf.name)
    if not (paths.root / ".gitignore").exists():
        shutil.copy(TEMPLATES / "gitignore", paths.root / ".gitignore")
    if not (paths.root / "README.md").exists():
        readme = Template((TEMPLATES / "project-README.md").read_text()).safe_substitute(name=name)
        (paths.root / "README.md").write_text(readme)
    paths.harness.mkdir(parents=True, exist_ok=True)
    paths.changes.mkdir(parents=True, exist_ok=True)
    shutil.copy(prd_path, paths.prd)
    if not paths.config.exists():
        paths.config.write_text(default_config_toml(name))
    paths.app.mkdir(exist_ok=True)
    (paths.app / ".gitkeep").touch()
    paths.local.mkdir(exist_ok=True)

    if install and shutil.which("npm"):
        out("Installing the acceptance kit (npm install)…")
        proc = subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=paths.acceptance,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            out(f"⚠ npm install failed; the harness will retry on first run:\n{proc.stderr[-800:]}")
    elif install:
        out("⚠ npm not found; install Node.js 22+ before running the harness")

    git = Git(paths.root)
    git.init()
    git.commit_all(f"harness: new project {name}")
    return paths


def start_feature(root: str | Path, delta: str | Path, allow_retire: bool = False) -> str:
    paths = ProjectPaths.at(root)
    delta_path = Path(delta).expanduser()
    if not delta_path.is_file():
        raise ProjectError(f"change description not found: {delta_path}")
    if not paths.lock.exists():
        raise ProjectError("the initial suite is not frozen yet; finish `copilot-harness run` first")
    state = load_model(State, paths.state) or State()
    if state.active_change:
        raise ProjectError(f"change {state.active_change} is still in progress; run `copilot-harness run` to finish it")

    paths.changes.mkdir(parents=True, exist_ok=True)
    existing = sorted(paths.changes.glob("CHG-*.md"))
    change_id = f"CHG-{len(existing) + 1:03d}"
    body = delta_path.read_text(encoding="utf-8").strip()
    (paths.changes / f"{change_id}.md").write_text(body + "\n", encoding="utf-8")
    with open(paths.prd, "a", encoding="utf-8") as fh:
        fh.write(f"\n\n## Change {change_id}\n\n{body}\n")

    git = Git(paths.root)
    state.active_change = change_id
    state.change_steps = []
    state.allow_retire = allow_retire
    state.change_base = git.commit_all(f"harness: start change {change_id}")
    write_json(paths.state, state)
    return change_id
