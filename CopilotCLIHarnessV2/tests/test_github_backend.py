"""Unit tests for the GitHub Issues feature backend."""

import json

from copilot_harness import github_backend as gb
from copilot_harness.github_backend import RepoState


def _issue(number, state, labels, is_pr=False):
    issue = {
        "number": number,
        "state": state,
        "labels": [{"name": name} for name in labels],
    }
    if is_pr:
        issue["pull_request"] = {"url": "..."}
    return issue


def test_summarize_counts_closed_as_verified():
    issues = [
        _issue(1, "closed", ["feature", "core"]),
        _issue(2, "open", ["feature", "core"]),
        _issue(3, "closed", ["feature", "style"]),
    ]
    summary = gb.summarize_issues(issues)
    assert summary.total == 3
    assert summary.verified == 2
    assert summary.by_category["core"] == {"total": 2, "verified": 1}
    assert summary.by_category["style"] == {"total": 1, "verified": 1}
    assert not summary.complete


def test_summarize_complete_when_all_closed():
    issues = [
        _issue(1, "closed", ["feature", "core"]),
        _issue(2, "closed", ["feature", "edge"]),
    ]
    summary = gb.summarize_issues(issues)
    assert summary.complete
    assert summary.percent == 100.0


def test_summarize_ignores_non_feature_and_prs():
    issues = [
        _issue(1, "closed", ["feature", "core"]),
        _issue(2, "open", ["bug"]),                       # not a feature
        _issue(3, "closed", ["feature", "core"], is_pr=True),  # a PR, skip
    ]
    summary = gb.summarize_issues(issues)
    assert summary.total == 1
    assert summary.verified == 1


def test_empty_summary():
    summary = gb.summarize_issues([])
    assert summary.total == 0
    assert not summary.complete
    assert summary.percent == 0.0


def test_repo_state_round_trip(tmp_path):
    state = RepoState(owner="octocat", repo="hello", epic_issue=7)
    gb.write_repo_state(tmp_path, state)
    loaded = gb.read_repo_state(tmp_path)
    assert loaded == state
    assert loaded.slug == "octocat/hello"
    # File really landed in .harness/
    assert json.loads((tmp_path / ".harness" / "repo.json").read_text())["epic_issue"] == 7


def test_read_repo_state_missing(tmp_path):
    assert gb.read_repo_state(tmp_path) is None


def test_read_repo_state_invalid(tmp_path):
    path = gb.repo_state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")
    assert gb.read_repo_state(tmp_path) is None


def test_token_precedence(monkeypatch):
    for var in gb.TOKEN_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "gh_tok")
    assert gb.resolve_token() == "gh_tok"
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "copilot_tok")
    assert gb.resolve_token() == "copilot_tok"   # highest precedence
    assert gb.resolve_token("explicit") == "explicit"


def test_next_link_parsing():
    headers = {
        "link": '<https://api.github.com/x?page=2>; rel="next", '
                '<https://api.github.com/x?page=5>; rel="last"'
    }
    assert gb.GitHubBackend._next_link(headers) == "https://api.github.com/x?page=2"
    assert gb.GitHubBackend._next_link({}) is None
