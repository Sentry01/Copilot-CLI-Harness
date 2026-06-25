"""
Harness configuration
======================

Central place for defaults and constants so they do not drift across modules
(a real problem in V1, where the demo, the README and the client each declared
a different default model).
"""

from __future__ import annotations

import os
from pathlib import Path

# Default Copilot model. Keep this in ONE place and import it everywhere.
DEFAULT_MODEL = "claude-sonnet-4.5"

# Models commonly available through Copilot CLI. Used for help text only.
KNOWN_MODELS = (
    "claude-sonnet-4.5",
    "claude-haiku-4.5",
    "gpt-5",
)

# Where relative project names are created.
PROJECTS_ROOT = Path(os.environ.get("HOME", str(Path.home()))) / "Projects"

# Labels used to model the feature backlog on GitHub Issues.
#   FEATURE_LABEL marks an issue as a tracked feature (closed == verified).
#   CATEGORY_LABELS are applied in addition for grouping/board columns.
FEATURE_LABEL = "feature"
CATEGORY_LABELS = (
    "core",
    "navigation",
    "style",
    "edge",
    "accessibility",
    "performance",
    "integration",
)

# File the initializer writes (and every later session reads) so the harness
# knows which repo/issue to talk to. Lives in .harness/ so it never ships in app/.
REPO_STATE_FILENAME = "repo.json"

# How many consecutive errored sessions before we enter recovery mode.
MAX_ERROR_RETRIES = 3

# Pause between sessions (seconds).
AUTO_CONTINUE_DELAY_SECONDS = 3


def resolve_project_dir(project_dir: Path) -> Path:
    """Absolute paths are used as-is; bare names go under ``$HOME/Projects``."""
    project_dir = Path(project_dir)
    if project_dir.is_absolute():
        return project_dir
    return PROJECTS_ROOT / project_dir.name
