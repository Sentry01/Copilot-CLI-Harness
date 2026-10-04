"""Turn runner reports into per-test-ID outcomes.

Supported inputs:
* Playwright JSON reporter output (``PLAYWRIGHT_JSON_OUTPUT_FILE``). Repeats
  (``--repeat-each``) and multiple projects appear as several entries for one ID;
  they are aggregated, and mixed results become ``flaky``.
* JUnit XML (any runner: pytest, vitest, go-junit-report, ...). The test ID must
  prefix the test name the same way (``FUNC-001: ...``).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

ID_IN_TITLE = re.compile(r"^((?:FUNC|PERF|SEC|UX)-\d{3,4}):")
ANSI = re.compile(r"\x1b\[[0-9;]*m")

Outcome = Literal["passed", "failed", "flaky", "skipped"]

# Failure messages that indicate a bug in the test itself rather than missing behaviour.
TEST_BUG = re.compile(
    r"\b(TypeError|ReferenceError|SyntaxError)\b|is not a function|Cannot find module|is not defined",
)


def strip_ansi(text: str) -> str:
    return ANSI.sub("", text or "")


@dataclass
class TestOutcome:
    __test__ = False  # not a pytest test class

    id: str
    status: Outcome
    runs: int = 0
    passes: int = 0
    duration_ms: float = 0.0
    error: str = ""
    file: str = ""

    @property
    def looks_like_test_bug(self) -> bool:
        return self.status == "failed" and bool(TEST_BUG.search(self.error))


@dataclass
class RunReport:
    outcomes: dict[str, TestOutcome] = field(default_factory=dict)
    collection_errors: list[str] = field(default_factory=list)
    unidentified: list[str] = field(default_factory=list)

    def ids_with(self, *statuses: Outcome) -> set[str]:
        return {i for i, o in self.outcomes.items() if o.status in statuses}

    @property
    def passed(self) -> set[str]:
        return self.ids_with("passed")


def _aggregate(raw: dict[str, list[tuple[str, float, str]]], files: dict[str, str]) -> dict[str, TestOutcome]:
    out = {}
    for tid, results in raw.items():
        statuses = [s for s, _, _ in results]
        passes = statuses.count("passed")
        ran = [s for s in statuses if s != "skipped"]
        if not ran:
            status: Outcome = "skipped"
        elif passes == len(ran):
            status = "passed"
        elif passes == 0:
            status = "failed"
        else:
            status = "flaky"
        error = next((e for s, _, e in results if s != "passed" and e), "")
        out[tid] = TestOutcome(
            id=tid,
            status=status,
            runs=len(ran),
            passes=passes,
            duration_ms=sum(d for _, d, _ in results),
            error=strip_ansi(error)[:4000],
            file=files.get(tid, ""),
        )
    return out


def parse_playwright_json(data: dict[str, Any]) -> RunReport:
    report = RunReport()
    for err in data.get("errors") or []:
        report.collection_errors.append(strip_ansi(err.get("message") or err.get("value") or str(err))[:2000])

    raw: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
    files: dict[str, str] = {}

    def walk(suite: dict[str, Any]) -> None:
        for spec in suite.get("specs") or []:
            title = spec.get("title", "")
            m = ID_IN_TITLE.match(title)
            if not m:
                report.unidentified.append(title)
                continue
            tid = m.group(1)
            files.setdefault(tid, spec.get("file", ""))
            for test in spec.get("tests") or []:
                results = test.get("results") or []
                if not results:
                    raw[tid].append(("skipped", 0.0, ""))
                    continue
                # Retries live inside one test entry; the last result is the verdict, any earlier
                # failure followed by a pass is flakiness.
                final = results[-1]
                status = _pw_status(final.get("status", "failed"))
                if status == "passed" and any(_pw_status(r.get("status")) == "failed" for r in results[:-1]):
                    raw[tid].append(("failed", 0.0, _pw_error(results[0])))
                raw[tid].append((status, float(final.get("duration") or 0), _pw_error(final)))
        for child in suite.get("suites") or []:
            walk(child)

    for suite in data.get("suites") or []:
        walk(suite)
    report.outcomes = _aggregate(raw, files)
    return report


def _pw_status(status: str | None) -> str:
    if status == "passed":
        return "passed"
    if status == "skipped":
        return "skipped"
    return "failed"  # failed, timedOut, interrupted


def _pw_error(result: dict[str, Any]) -> str:
    err = result.get("error") or {}
    msg = err.get("message") or ""
    if not msg and result.get("errors"):
        msg = (result["errors"][0] or {}).get("message", "")
    if result.get("status") == "timedOut" and not msg:
        msg = "Test timed out"
    return msg


def parse_junit_xml(text: str) -> RunReport:
    report = RunReport()
    raw: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
    files: dict[str, str] = {}
    root = ET.fromstring(text)
    for case in root.iter("testcase"):
        name = case.get("name", "")
        m = ID_IN_TITLE.match(name)
        if not m:
            report.unidentified.append(name)
            continue
        tid = m.group(1)
        files.setdefault(tid, case.get("file") or case.get("classname") or "")
        duration = float(case.get("time") or 0) * 1000
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        if failure is not None:
            raw[tid].append(("failed", duration, (failure.get("message") or "") + "\n" + (failure.text or "")))
        elif case.find("skipped") is not None:
            raw[tid].append(("skipped", duration, ""))
        else:
            raw[tid].append(("passed", duration, ""))
    report.outcomes = _aggregate(raw, files)
    return report


def list_ids_from_playwright_json(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """IDs discovered by ``playwright test --list --reporter=json`` (+ collection errors)."""
    report = parse_playwright_json(data)
    return sorted(report.outcomes), report.collection_errors
