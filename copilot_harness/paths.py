"""Filesystem layout of a harness-managed project.

    <root>/
      app/                      implementation (owned by the coder agent)
      acceptance/               executable acceptance suite (frozen after authoring)
        specs/<category>/*.spec.ts
        support/                vetted helpers (perf, a11y, security, fixtures)
      harness/                  committed contracts and records
        PRD.md, changes/, requirements.json, contract.json, app-contract.json,
        test_plan.json, tests.lock.json, baseline.json, harness.toml, REPORT.md
      .github/workflows/        CI (acceptance gate)
      .harness/                 local only: state, session logs, test runs, disputes
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @classmethod
    def at(cls, root: str | Path) -> ProjectPaths:
        return cls(Path(root).expanduser().resolve())

    # --- implementation -------------------------------------------------
    @property
    def app(self) -> Path:
        return self.root / "app"

    # --- acceptance suite -----------------------------------------------
    @property
    def acceptance(self) -> Path:
        return self.root / "acceptance"

    @property
    def specs(self) -> Path:
        return self.acceptance / "specs"

    # --- committed harness records ---------------------------------------
    @property
    def harness(self) -> Path:
        return self.root / "harness"

    @property
    def prd(self) -> Path:
        return self.harness / "PRD.md"

    @property
    def changes(self) -> Path:
        return self.harness / "changes"

    @property
    def requirements(self) -> Path:
        return self.harness / "requirements.json"

    @property
    def contract(self) -> Path:
        return self.harness / "contract.json"

    @property
    def app_contract(self) -> Path:
        return self.harness / "app-contract.json"

    @property
    def test_plan(self) -> Path:
        return self.harness / "test_plan.json"

    @property
    def lock(self) -> Path:
        return self.harness / "tests.lock.json"

    @property
    def baseline(self) -> Path:
        return self.harness / "baseline.json"

    @property
    def config(self) -> Path:
        return self.harness / "harness.toml"

    @property
    def report(self) -> Path:
        return self.harness / "REPORT.md"

    @property
    def workflows(self) -> Path:
        return self.root / ".github" / "workflows"

    # --- local, uncommitted ------------------------------------------------
    @property
    def local(self) -> Path:
        return self.root / ".harness"

    @property
    def state(self) -> Path:
        return self.local / "state.json"

    @property
    def sessions(self) -> Path:
        return self.local / "sessions"

    @property
    def runs(self) -> Path:
        return self.local / "runs"

    @property
    def reviews(self) -> Path:
        return self.local / "reviews"

    @property
    def dispute_claims(self) -> Path:
        return self.local / "disputes" / "claims"

    @property
    def dispute_decisions(self) -> Path:
        return self.local / "disputes" / "decisions"

    @property
    def quarantine(self) -> Path:
        return self.local / "quarantine"

    def rel(self, path: Path) -> str:
        """Project-relative POSIX path (raises if outside the project)."""
        return Path(path).resolve().relative_to(self.root).as_posix()
