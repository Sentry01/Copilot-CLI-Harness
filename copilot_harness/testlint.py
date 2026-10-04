"""Static checks for generated Playwright spec files.

These checks are what make LLM-written tests trustworthy enough to freeze:
every test maps to exactly one planned ID, has a real assertion, and avoids the
constructs that make suites flaky or vacuous (fixed sleeps, randomness, skips,
tautologies). The checks are deliberately lexical and conservative.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from copilot_harness.models import PREFIX_CATEGORY, TestPlan

TEST_DECL = re.compile(
    r"(?<![.\w$])test(?:\.(?P<mod>only|skip|fixme|fail|slow))?\s*\(\s*"
    r"(?P<q>['\"`])(?P<title>(?:\\.|(?!(?P=q)).)*?)(?P=q)",
    re.S,
)
TITLE_ID = re.compile(r"^(?P<id>(FUNC|PERF|SEC|UX)-\d{3,4}): \S")
FUNCTION_BODY = re.compile(r"function\s*\w*\s*\([^)]*\)\s*\{")
ASSERTION = re.compile(r"\bexpect\b|\bexpect[A-Z]\w*\s*\(|\bassert[A-Z]?\w*\s*\(")
TAUTOLOGY = re.compile(
    r"\bexpect\s*\(\s*(true|false|null|undefined|\d+|'[^']*'|\"[^\"]*\")\s*\)"
)

# (pattern, message) — errors block the freeze.
FORBIDDEN = [
    (re.compile(r"\.(only)\s*\("), "focused test/describe (.only) is forbidden"),
    (re.compile(r"\b(test|describe)(\.describe)?\.(skip|fixme)\s*\("), "skipped tests are forbidden; every planned test must run"),
    (re.compile(r"\btest\.fail\s*\("), "test.fail() inverts results and is forbidden"),
    (re.compile(r"\bwaitForTimeout\s*\("), "fixed sleeps (waitForTimeout) are forbidden; use web-first assertions or expect.poll"),
    (re.compile(r"(?<![.\w])setTimeout\s*\("), "setTimeout in specs is forbidden; use expect.poll / waitFor* with a condition"),
    (re.compile(r"\bpage\.pause\s*\("), "page.pause() blocks headless runs"),
    (re.compile(r"\bMath\.random\s*\("), "Math.random makes runs non-deterministic; use uniqueId(testInfo) from support"),
    # The suite talks only to the app under test (the runner also blocks every other host).
    (re.compile(r"(?:\.(?:goto|get|post|put|patch|delete|head|fetch|newContext)|(?<![.\w$])fetch)\s*\(\s*"
                r"(?:['\"`]\s*(?:[a-z][\w+.-]*:)?//|\{[^}]*\bbaseURL\s*:)", re.I),
     "requests must use relative paths against baseURL (e.g. page.goto('/login')); absolute and off-origin "
     "destinations are forbidden"),
    (re.compile(r"(?<![.\w$])(?:require|import)\s*\("), "require()/dynamic import() is forbidden; import from the kit"),
]

# Specs import only from the acceptance kit (plus type-only imports from Playwright), so they
# can't bypass its fixtures or open their own network/process channels.
IMPORT = re.compile(r"(?<![.\w$])import\s+(?P<type>type\s+)?(?:[^'\";]*?\bfrom\s*)?(?P<q>['\"])(?P<mod>[^'\"]+)(?P=q)")
KIT_MODULE = re.compile(r"^(?:\.\./)+support(?:/index)?$|^\./support(?:/index)?$")
# (pattern, message) — warnings are reported but do not block.
DISCOURAGED = [
    (re.compile(r"\bforce\s*:\s*true"), "force: true bypasses actionability checks and hides real UI bugs"),
    (re.compile(r"networkidle"), "networkidle is flaky; wait for a specific element or response"),
    (re.compile(r"\bDate\.now\s*\("), "Date.now() varies per run; prefer uniqueId(testInfo) for data and measure() for timing"),
    (re.compile(r"https?://(?!localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])[\w.-]+\.[a-z]{2,}"),
     "external URL in a spec: fine as payload data (e.g. an open-redirect target), but the runner blocks "
     "every host except the app"),
]


@dataclass
class LintIssue:
    file: str
    line: int
    severity: str  # "error" | "warning"
    message: str
    test_id: str | None = None

    def __str__(self) -> str:
        tid = f" [{self.test_id}]" if self.test_id else ""
        return f"{self.file}:{self.line}: {self.severity}{tid}: {self.message}"


@dataclass
class SpecTest:
    id: str
    title: str
    file: str
    line: int
    body: str


@dataclass
class LintReport:
    issues: list[LintIssue] = field(default_factory=list)
    found: dict[str, SpecTest] = field(default_factory=dict)

    @property
    def errors(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def errors_for(self, ids: Iterable[str]) -> list[LintIssue]:
        wanted = set(ids)
        return [i for i in self.errors if i.test_id in wanted or i.test_id is None]


# --------------------------------------------------------------------------
# Lexical helpers
# --------------------------------------------------------------------------


def _skip_string(src: str, i: int, quote: str) -> int:
    i += 1
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == quote or c == "\n":
            return i + 1
        i += 1
    return len(src)


def _skip_template(src: str, i: int) -> int:
    i += 1
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i + 1
        if src.startswith("${", i):
            j = match_brace(src, i + 1)
            if j == -1:
                return len(src)
            i = j + 1
            continue
        i += 1
    return len(src)


def match_brace(src: str, start: int) -> int:
    """Index of the '}' matching the '{' at ``start`` (skips strings and comments)."""
    depth = 0
    i = start
    n = len(src)
    while i < n:
        c = src[i]
        if c in "'\"":
            i = _skip_string(src, i, c)
            continue
        if c == "`":
            i = _skip_template(src, i)
            continue
        if src.startswith("//", i):
            nl = src.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def strip_comments(src: str) -> str:
    """Blank out comments (keeping newlines and offsets) so commented code isn't linted."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "'\"":
            i = _skip_string(src, i, c)
        elif c == "`":
            i = _skip_template(src, i)
        elif src.startswith("//", i):
            end = src.find("\n", i)
            end = n if end == -1 else end
            for k in range(i, end):
                out[k] = " "
            i = end
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for k in range(i, end):
                if out[k] != "\n":
                    out[k] = " "
            i = end
        else:
            i += 1
    return "".join(out)


def _line_of(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def _body_start(src: str, after: int, limit: int) -> int:
    arrow = src.find("=>", after, limit)
    if arrow != -1:
        j = arrow + 2
        while j < limit and src[j].isspace():
            j += 1
        if j < limit and src[j] == "{":
            return j
    m = FUNCTION_BODY.search(src, after, limit)
    return m.end() - 1 if m else -1


def extract_tests(src: str, file: str) -> tuple[list[SpecTest], list[LintIssue]]:
    """Find ``test('ID: title', async (...) => { ... })`` declarations."""
    code = strip_comments(src)
    decls = list(TEST_DECL.finditer(code))
    tests: list[SpecTest] = []
    issues: list[LintIssue] = []
    for idx, m in enumerate(decls):
        line = _line_of(code, m.start())
        title = m.group("title")
        tm = TITLE_ID.match(title)
        tid = tm.group("id") if tm else None
        # only/skip/fixme/fail modifiers are reported by the FORBIDDEN patterns.
        if tid is None:
            issues.append(LintIssue(file, line, "error", f"title must start with '<ID>: ' (e.g. 'FUNC-001: ...'), got {title[:60]!r}"))
            continue
        limit = decls[idx + 1].start() if idx + 1 < len(decls) else len(code)
        start = _body_start(code, m.end(), limit)
        end = match_brace(code, start) if start != -1 else -1
        if start == -1 or end == -1:
            issues.append(LintIssue(file, line, "error", "could not find the test body", tid))
            body = ""
        else:
            body = code[start + 1 : end]
        tests.append(SpecTest(tid, title, file, line, body))
    return tests, issues


# --------------------------------------------------------------------------
# Linting
# --------------------------------------------------------------------------


def lint_source(src: str, file: str) -> tuple[list[SpecTest], list[LintIssue]]:
    tests, issues = extract_tests(src, file)
    code = strip_comments(src)
    for pattern, message in FORBIDDEN:
        for m in pattern.finditer(code):
            issues.append(LintIssue(file, _line_of(code, m.start()), "error", message, _test_at(tests, code, m.start())))
    for m in IMPORT.finditer(code):
        mod = m.group("mod")
        if KIT_MODULE.match(mod) or (m.group("type") and mod == "@playwright/test"):
            continue
        issues.append(LintIssue(
            file, _line_of(code, m.start()), "error",
            f"import from {mod!r} is forbidden; specs import only from the kit ('../../support') "
            "and type-only from '@playwright/test'",
            _test_at(tests, code, m.start()),
        ))
    for pattern, message in DISCOURAGED:
        for m in pattern.finditer(code):
            issues.append(LintIssue(file, _line_of(code, m.start()), "warning", message, _test_at(tests, code, m.start())))
    for t in tests:
        if not t.body.strip():
            issues.append(LintIssue(file, t.line, "error", "test body is empty", t.id))
            continue
        if not ASSERTION.search(t.body):
            issues.append(LintIssue(file, t.line, "error", "test has no assertion (expect(...) or a support expect*/assert* helper)", t.id))
        if TAUTOLOGY.search(t.body):
            issues.append(LintIssue(file, t.line, "error", "tautological assertion on a literal value", t.id))
        if re.search(r"\btry\s*\{", t.body) and "expect" in t.body:
            issues.append(LintIssue(file, t.line, "warning", "assertions inside try/catch can be swallowed", t.id))
    return tests, issues


def _test_at(tests: list[SpecTest], code: str, pos: int) -> str | None:
    line = _line_of(code, pos)
    owner = None
    for t in tests:
        if t.line <= line:
            owner = t.id
    return owner


def lint_specs(specs_dir: Path, plan: TestPlan, root: Path, expected_ids: Iterable[str] | None = None) -> LintReport:
    """Lint every ``*.spec.ts`` under ``specs_dir`` against the plan.

    ``expected_ids``: ids that must be present (default: all active plan tests).
    """
    report = LintReport()
    planned = plan.by_id()
    seen: dict[str, SpecTest] = {}
    files = sorted(specs_dir.rglob("*.spec.ts")) if specs_dir.exists() else []
    for path in files:
        rel = path.resolve().relative_to(root).as_posix()
        tests, issues = lint_source(path.read_text(encoding="utf-8"), rel)
        report.issues += issues
        category_dir = path.parent.name
        for t in tests:
            if t.id in seen:
                report.issues.append(LintIssue(rel, t.line, "error", f"duplicate test id (also in {seen[t.id].file}:{seen[t.id].line})", t.id))
                continue
            seen[t.id] = t
            planned_case = planned.get(t.id)
            if planned_case is None:
                report.issues.append(LintIssue(rel, t.line, "error", "test id is not in harness/test_plan.json", t.id))
                continue
            # Retired tests stay in their frozen files; playwright.config.ts excludes them via grepInvert.
            expected_cat = PREFIX_CATEGORY[t.id.split("-")[0]]
            if category_dir != expected_cat:
                report.issues.append(LintIssue(rel, t.line, "error", f"{expected_cat} tests belong under specs/{expected_cat}/", t.id))
            elif path.name != f"{planned_case.group}.spec.ts":
                report.issues.append(LintIssue(rel, t.line, "warning", f"plan puts this test in {planned_case.spec_path}", t.id))
    report.found = seen
    wanted = list(expected_ids) if expected_ids is not None else [t.id for t in plan.active()]
    for tid in wanted:
        if tid not in seen:
            case = planned.get(tid)
            where = case.spec_path if case else "acceptance/specs/"
            report.issues.append(LintIssue(where, 0, "error", "planned test is not implemented", tid))
    return report
