"""
GitHub Issues feature backend
=============================

Replaces V1's ``feature_list.json``. The backlog of features-to-build *is* a set
of GitHub issues:

* Each feature is an issue labelled ``feature`` plus a category label, created as
  a sub-issue of a single Epic issue.
* A feature is **verified** when its issue is **closed**.
* Progress = closed feature issues / all feature issues.

Why this is better than a JSON file:

* One source of truth. V1 tracked progress in JSON *and* told the agent to mirror
  it onto an Epic issue, so the two drifted.
* No "writing 200 items at once corrupts the JSON" failure mode (the reason V1's
  initializer prompt had a fragile 7-stage append dance).
* The timeline, assignees and board view come for free, and humans can edit the
  backlog mid-run.

The harness only ever *reads* state here (to compute progress and detect
completion). The agent creates and closes the issues from inside its sessions.
This module talks to the REST API directly with the standard library, so the
harness has no dependency on the ``gh`` CLI being installed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import FEATURE_LABEL, REPO_STATE_FILENAME

GITHUB_API = "https://api.github.com"
# Same precedence the Copilot SDK/CLI use for headless auth.
TOKEN_ENV_VARS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")


def resolve_token(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit
    for var in TOKEN_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    return None


@dataclass
class RepoState:
    """Coordinates the initializer records in ``.harness/repo.json``."""

    owner: str
    repo: str
    epic_issue: Optional[int] = None

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


def repo_state_path(project_dir: Path) -> Path:
    return Path(project_dir) / ".harness" / REPO_STATE_FILENAME


def read_repo_state(project_dir: Path) -> Optional[RepoState]:
    path = repo_state_path(project_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    owner, repo = data.get("owner"), data.get("repo")
    if not owner or not repo:
        return None
    return RepoState(owner=owner, repo=repo, epic_issue=data.get("epic_issue"))


def write_repo_state(project_dir: Path, state: RepoState) -> None:
    path = repo_state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"owner": state.owner, "repo": state.repo, "epic_issue": state.epic_issue},
            indent=2,
        )
    )


@dataclass
class ProgressSummary:
    verified: int = 0
    total: int = 0
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.verified == self.total

    @property
    def percent(self) -> float:
        return (self.verified / self.total * 100) if self.total else 0.0


def _category_of(issue: dict[str, Any]) -> str:
    for label in issue.get("labels", []):
        name = label.get("name") if isinstance(label, dict) else str(label)
        if name and name != FEATURE_LABEL:
            return name
    return "uncategorized"


def summarize_issues(issues: Iterable[dict[str, Any]]) -> ProgressSummary:
    """Pure aggregation over issue dicts. Network-free, so it is unit-tested.

    Pull requests are skipped (the issues endpoint returns them too). Only issues
    carrying the ``feature`` label are counted.
    """
    summary = ProgressSummary()
    for issue in issues:
        if "pull_request" in issue:
            continue
        names = {
            (lbl.get("name") if isinstance(lbl, dict) else str(lbl))
            for lbl in issue.get("labels", [])
        }
        if FEATURE_LABEL not in names:
            continue
        category = _category_of(issue)
        bucket = summary.by_category.setdefault(category, {"total": 0, "verified": 0})
        summary.total += 1
        bucket["total"] += 1
        if issue.get("state") == "closed":
            summary.verified += 1
            bucket["verified"] += 1
    return summary


class GitHubBackend:
    """Thin REST client scoped to one repo. Lazy: constructing it does no I/O."""

    def __init__(self, state: RepoState, token: Optional[str] = None, timeout: float = 30.0):
        self.state = state
        self.token = resolve_token(token)
        self.timeout = timeout

    # -- HTTP -------------------------------------------------------------
    def _request(self, method: str, path: str) -> tuple[Any, dict[str, str]]:
        url = path if path.startswith("http") else f"{GITHUB_API}{path}"
        req = urllib.request.Request(url, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "copilot-cli-harness-v2")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8")
            headers = {k.lower(): v for k, v in resp.headers.items()}
        return (json.loads(body) if body else None), headers

    @staticmethod
    def _next_link(headers: dict[str, str]) -> Optional[str]:
        link = headers.get("link", "")
        for part in link.split(","):
            if 'rel="next"' in part:
                start, end = part.find("<"), part.find(">")
                if start != -1 and end != -1:
                    return part[start + 1 : end]
        return None

    def fetch_feature_issues(self) -> list[dict[str, Any]]:
        """All issues labelled ``feature`` (open and closed), following pages."""
        issues: list[dict[str, Any]] = []
        path = (
            f"/repos/{self.state.owner}/{self.state.repo}/issues"
            f"?labels={FEATURE_LABEL}&state=all&per_page=100"
        )
        while path:
            page, headers = self._request("GET", path)
            if isinstance(page, list):
                issues.extend(page)
            path = self._next_link(headers)
        return issues

    # -- High level -------------------------------------------------------
    def progress(self) -> ProgressSummary:
        """Return current progress, or an empty summary if the repo is unreachable."""
        try:
            return summarize_issues(self.fetch_feature_issues())
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
            return ProgressSummary()


def get_progress(project_dir: Path, token: Optional[str] = None) -> ProgressSummary:
    """Convenience: read repo state from the project and return progress."""
    state = read_repo_state(project_dir)
    if state is None:
        return ProgressSummary()
    return GitHubBackend(state, token=token).progress()
