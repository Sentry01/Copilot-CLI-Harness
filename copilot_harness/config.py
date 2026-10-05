"""Per-project configuration (``harness/harness.toml``)."""

from __future__ import annotations

import tomllib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from copilot_harness.paths import ProjectPaths


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelsConfig(_Section):
    """Model per agent role. Empty string = the Copilot account's default model.

    Pin these for reproducibility; `copilot-harness models` lists what your account offers.
    Using a different model for test authoring than for coding reduces correlated blind spots.
    """

    default: str = ""
    analyst: str = ""
    architect: str = ""
    test_architect: str = ""
    plan_reviewer: str = ""
    test_author: str = ""
    coder: str = ""
    adjudicator: str = ""
    reasoning_effort: Literal["", "low", "medium", "high", "xhigh", "max"] = "high"

    def for_role(self, role: str) -> str:
        return getattr(self, role, "") or self.default


class TestsConfig(_Section):
    __test__ = False  # not a pytest test class

    min_total: int = Field(default=200, ge=1)
    max_total: int = Field(default=300, ge=1)
    min_functional_ratio: float = Field(default=0.55, ge=0, le=1)
    min_per_category: dict[str, int] = Field(
        default_factory=lambda: {"performance": 15, "security": 25, "usability": 25}
    )
    max_group_size: int = Field(default=30, ge=1)
    # Feature deltas: how many new tests a change may add.
    delta_min: int = Field(default=10, ge=1)
    delta_max: int = Field(default=80, ge=1)
    author_batch_size: int = Field(default=25, ge=1)
    # A newly passing test is promoted to the baseline only after passing this many repeats.
    stability_repeats: int = Field(default=3, ge=1, le=20)
    review_plan: bool = True


class LoopConfig(_Section):
    batch_size: int = Field(default=8, ge=1)
    max_sessions: int = Field(default=150, ge=1)
    max_attempts_per_test: int = Field(default=3, ge=1)
    max_stop_blocks: int = Field(default=3, ge=0)
    session_timeout_minutes: float = Field(default=45, gt=0)
    max_ai_credits_per_session: float = Field(default=0, ge=0)
    max_total_ai_credits: float = Field(default=0, ge=0)
    no_progress_limit: int = Field(default=5, ge=1)
    regression_fix_attempts: int = Field(default=2, ge=0)
    max_disputes: int = Field(default=15, ge=0)
    max_phase_attempts: int = Field(default=4, ge=1)


class BackendConfig(_Section):
    kind: Literal["sdk", "cli"] = "sdk"
    # Optional explicit Copilot runtime/CLI binary (else the SDK-managed runtime / `copilot` on PATH).
    cli_path: str = ""


class RunnerConfig(_Section):
    kind: Literal["playwright", "junit"] = "playwright"
    workdir: str = "acceptance"
    # For kind="junit": the command that runs the suite and the JUnit XML it writes.
    command: list[str] = Field(default_factory=list)
    junit_path: str = ""
    timeout_minutes: float = Field(default=30, gt=0)
    workers: int = Field(default=1, ge=1)


class SecurityConfig(_Section):
    allow_urls: list[str] = Field(
        default_factory=lambda: [
            "registry.npmjs.org",
            "pypi.org",
            "files.pythonhosted.org",
            "nodejs.org",
            "developer.mozilla.org",
            "playwright.dev",
            "docs.python.org",
        ]
    )
    allow_git_push: bool = False
    extra_allowed_commands: list[str] = Field(default_factory=list)
    extra_denied_commands: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=lambda: ["playwright"])
    playwright_mcp_package: str = "@playwright/mcp@0.0.83"


class HarnessConfig(_Section):
    project: dict[str, str] = Field(default_factory=dict)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    tests: TestsConfig = Field(default_factory=TestsConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    runner: RunnerConfig = Field(default_factory=RunnerConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)


def load_config(paths: ProjectPaths) -> HarnessConfig:
    if not paths.config.exists():
        return HarnessConfig()
    with open(paths.config, "rb") as fh:
        return HarnessConfig.model_validate(tomllib.load(fh))


def default_config_toml(name: str) -> str:
    return CONFIG_TEMPLATE.replace("$name", name.replace('"', ""))


CONFIG_TEMPLATE = """\
# copilot-harness project configuration. All keys are optional; defaults shown.

[project]
name = "$name"

[models]
# Empty = your Copilot account default. Pin models for reproducible runs:
#   copilot-harness models      (lists the models your account can use)
default = ""
# test_author = ""   # a different model than the coder reduces correlated blind spots
# coder = ""
reasoning_effort = "high"

[tests]
min_total = 200
max_total = 300
min_functional_ratio = 0.55
min_per_category = { performance = 15, security = 25, usability = 25 }
author_batch_size = 25
stability_repeats = 3        # repeats a newly passing test must survive before joining the baseline
review_plan = true           # independent reviewer critiques the plan before tests are written

[loop]
batch_size = 8               # failing tests targeted per coding session
max_sessions = 150
max_attempts_per_test = 3    # then the test is marked blocked and skipped
max_stop_blocks = 3          # times the harness may refuse to let an agent stop while targets fail
session_timeout_minutes = 45
max_ai_credits_per_session = 0   # 0 = no cap
max_total_ai_credits = 0         # 0 = no cap
no_progress_limit = 5
regression_fix_attempts = 2

[backend]
kind = "sdk"                 # "sdk" (recommended) or "cli"
cli_path = ""

[runner]
kind = "playwright"
workdir = "acceptance"
timeout_minutes = 30
workers = 1                  # >1 only if every test is data-isolated

[security]
allow_git_push = false
mcp_servers = ["playwright"]
# extra_allowed_commands = ["docker"]
"""
