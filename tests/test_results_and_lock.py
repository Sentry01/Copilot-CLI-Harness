import subprocess
from pathlib import Path

from copilot_harness.gitops import Git
from copilot_harness.lock import create_lock, is_locked_path, restore_lock, verify_lock
from copilot_harness.paths import ProjectPaths
from copilot_harness.results import parse_junit_xml, parse_playwright_json
from copilot_harness.runner import grep_for


def _spec(title, *results, file="a.spec.ts"):
    return {"title": title, "file": file, "tests": [{"results": [{"status": s, "duration": 5,
                                                                  "error": {"message": f"\x1b[31m{s} msg\x1b[0m"} if s != "passed" else None}
                                                                 for s in results]}]}


def test_playwright_json_aggregation():
    report = {
        "errors": [],
        "suites": [{"title": "a.spec.ts", "specs": [
            _spec("FUNC-001: ok", "passed"),
            _spec("FUNC-001: ok", "passed"),  # --repeat-each duplicate
            _spec("FUNC-002: bad", "failed"),
            _spec("FUNC-003: retried", "failed", "passed"),  # passed only on retry -> flaky
            _spec("FUNC-004: mixed", "passed"),
            _spec("FUNC-004: mixed", "timedOut"),
            _spec("untagged test", "passed"),
        ], "suites": [{"title": "nested", "specs": [_spec("SEC-001: deep", "passed")]}]}],
    }
    r = parse_playwright_json(report)
    status = {k: v.status for k, v in r.outcomes.items()}
    assert status == {"FUNC-001": "passed", "FUNC-002": "failed", "FUNC-003": "flaky",
                      "FUNC-004": "flaky", "SEC-001": "passed"}
    assert r.outcomes["FUNC-001"].runs == 2
    assert "\x1b" not in r.outcomes["FUNC-002"].error
    assert r.unidentified == ["untagged test"]


def test_collection_errors_and_test_bug_detection():
    r = parse_playwright_json({"errors": [{"message": "SyntaxError: Unexpected token"}], "suites": []})
    assert r.collection_errors and not r.outcomes
    r = parse_playwright_json({"suites": [{"specs": [
        {"title": "FUNC-001: x", "tests": [{"results": [{"status": "failed", "error": {"message": "TypeError: x is not a function"}}]}]},
        {"title": "FUNC-002: y", "tests": [{"results": [{"status": "failed", "error": {"message": "expect(received).toBe(expected)"}}]}]},
    ]}]})
    assert r.outcomes["FUNC-001"].looks_like_test_bug
    assert not r.outcomes["FUNC-002"].looks_like_test_bug


def test_junit():
    xml = """<testsuites><testsuite>
      <testcase name="FUNC-001: a" time="0.1"/>
      <testcase name="FUNC-002: b" time="0.2"><failure message="boom">trace</failure></testcase>
      <testcase name="UX-001: c"><skipped/></testcase>
    </testsuite></testsuites>"""
    r = parse_junit_xml(xml)
    assert {k: v.status for k, v in r.outcomes.items()} == {"FUNC-001": "passed", "FUNC-002": "failed", "UX-001": "skipped"}


def test_grep_for_matches_exact_ids_only():
    import re

    rx = re.compile(grep_for(["FUNC-001", "SEC-010"]))
    assert rx.search("chromium > a.spec.ts > FUNC-001: x")
    assert not rx.search("FUNC-0011: x")
    assert not rx.search("FUNC-002: x")


def test_is_locked_path():
    assert is_locked_path("acceptance/specs/functional/a.spec.ts")
    assert is_locked_path("harness/test_plan.json")
    assert is_locked_path(".github/workflows/acceptance.yml")
    assert not is_locked_path("acceptance/node_modules/x/y.js")
    assert not is_locked_path("acceptance/reports/report.json")
    assert not is_locked_path("harness/baseline.json")
    assert not is_locked_path("app/server.js")


def test_lock_detects_and_restores_drift(tmp_path: Path):
    paths = ProjectPaths.at(tmp_path)
    spec = paths.specs / "functional" / "a.spec.ts"
    spec.parent.mkdir(parents=True)
    spec.write_text("original")
    paths.harness.mkdir()
    paths.test_plan.write_text("{}")
    (tmp_path / ".gitignore").write_text(".harness/\n")
    git = Git(tmp_path)
    git.init()
    git.commit_all("init")
    lock = create_lock(paths, ["FUNC-001"])
    assert set(lock.files) == {"acceptance/specs/functional/a.spec.ts", "harness/test_plan.json"}
    assert verify_lock(paths, lock).ok

    spec.write_text("weakened")
    (paths.specs / "functional" / "extra.spec.ts").write_text("sneaky")
    paths.test_plan.unlink()
    drift = verify_lock(paths, lock)
    assert drift.modified == ["acceptance/specs/functional/a.spec.ts"]
    assert drift.added == ["acceptance/specs/functional/extra.spec.ts"]
    assert drift.missing == ["harness/test_plan.json"]

    after = restore_lock(paths, lock, git, "HEAD", "t1")
    assert after.ok
    assert spec.read_text() == "original"
    assert (paths.quarantine / "t1" / "acceptance/specs/functional/extra.spec.ts").read_text() == "sneaky"
    assert subprocess.run(["git", "-C", str(tmp_path), "status", "--porcelain"], capture_output=True, text=True).stdout == ""
