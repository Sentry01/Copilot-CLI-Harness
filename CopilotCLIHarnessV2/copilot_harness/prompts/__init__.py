"""Prompt loading utilities."""

from __future__ import annotations

import shutil
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


def get_initializer_prompt() -> str:
    return load_prompt("initializer_prompt")


def get_coding_prompt() -> str:
    return load_prompt("coding_prompt")


def copy_spec_to_project(project_dir: Path, spec_file: str = "app_spec.txt") -> None:
    """Copy the chosen spec into ``.harness/app_spec.txt`` for the agent to read."""
    source = PROMPTS_DIR / spec_file
    if not source.exists():
        for candidate in (f"{spec_file}.txt", f"{spec_file}.md"):
            if (PROMPTS_DIR / candidate).exists():
                source = PROMPTS_DIR / candidate
                break
        else:
            print(f"Warning: spec file {spec_file} not found in {PROMPTS_DIR}")
            return

    harness_dir = Path(project_dir) / ".harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    (harness_dir / "sessions").mkdir(exist_ok=True)
    dest = harness_dir / "app_spec.txt"
    if not dest.exists():
        shutil.copy(source, dest)
        print(f"Copied {source.name} -> .harness/app_spec.txt")
