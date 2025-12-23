"""
Prompt Loading Utilities
========================

Functions for loading prompt templates from the prompts directory.
"""

import shutil
from pathlib import Path


PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompt_path = PROMPTS_DIR / f"{name}.md"
    return prompt_path.read_text()


def get_initializer_prompt() -> str:
    """Load the initializer prompt."""
    return load_prompt("initializer_prompt")


def get_coding_prompt() -> str:
    """Load the coding agent prompt."""
    return load_prompt("coding_prompt")


def copy_spec_to_project(project_dir: Path, spec_file: str = "app_spec.txt") -> None:
    """Copy the app spec file into the harness directory only.
    
    The spec file is placed in .harness/ (not app/) to keep the app folder
    clean and deployable. The agent reads from ../.harness/app_spec.txt.
    """
    spec_source = PROMPTS_DIR / spec_file
    
    if not spec_source.exists():
        # Try adding .txt or .md extension if not found
        if (PROMPTS_DIR / f"{spec_file}.txt").exists():
            spec_source = PROMPTS_DIR / f"{spec_file}.txt"
        elif (PROMPTS_DIR / f"{spec_file}.md").exists():
            spec_source = PROMPTS_DIR / f"{spec_file}.md"
        else:
            print(f"Warning: Spec file {spec_file} not found in prompts directory")
            return

    # Create directory structure
    harness_dir = project_dir / ".harness"
    app_dir = project_dir / "app"
    harness_dir.mkdir(parents=True, exist_ok=True)
    app_dir.mkdir(parents=True, exist_ok=True)
    
    # Create subdirectories for tests and sessions
    (harness_dir / "tests").mkdir(exist_ok=True)
    (harness_dir / "sessions").mkdir(exist_ok=True)
    
    # Copy to .harness only (not app/)
    harness_spec = harness_dir / "app_spec.txt"
    if not harness_spec.exists():
        shutil.copy(spec_source, harness_spec)
        print(f"Copied {spec_source.name} to .harness/app_spec.txt")
