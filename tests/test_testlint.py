from pathlib import Path

from copilot_harness.models import TestPlan
from copilot_harness.testlint import extract_tests, lint_source, lint_specs, match_brace, strip_comments


def errors(src: str) -> list[str]:
    _, issues = lint_source(src, "acceptance/specs/functional/x.spec.ts")
    return [i.message for i in issues if i.severity == "error"]


GOOD = """import { test, expect } from '../../support';
test('FUNC-001: works', async ({ page, data }) => {
  const label = `x-${data.id('a')}`;
  await page.goto('/');
  await expect(page.getByText(label)).toBeVisible();
});
"""


def test_good_spec_is_clean():
    assert errors(GOOD) == []


def test_body_extraction_handles_nested_braces_strings_and_templates():
    src = """test('FUNC-002: t', async ({ request }) => {
  const s = "}{";
  const t = `${JSON.stringify({ a: { b: 1 } })} }`;
  if (s) { expect(t).toContain('a'); }
});
test('FUNC-003: next', async () => { expect(1 + 1).toBe(2); });
"""
    tests, issues = extract_tests(src, "f.spec.ts")
    assert [t.id for t in tests] == ["FUNC-002", "FUNC-003"]
    assert "toContain" in tests[0].body and "FUNC-003" not in tests[0].body
    assert not issues


def test_title_must_start_with_id():
    assert any("title must start" in e for e in errors("test('works', async () => { expect(1).toBe(1); });"))


def test_forbidden_constructs():
    src = """test('FUNC-001: a', async ({ page }) => {
  await page.waitForTimeout(500);
  const n = Math.random();
  await expect(page).toHaveURL('/');
});
test.only('FUNC-002: b', async () => { expect(2).toBe(2); });
test.skip('FUNC-003: c', async () => { expect(3).toBe(3); });
"""
    msgs = errors(src)
    assert any("waitForTimeout" in m for m in msgs)
    assert any("Math.random" in m for m in msgs)
    assert any(".only" in m for m in msgs)
    assert any("skipped" in m for m in msgs)


def test_missing_and_tautological_assertions():
    msgs = errors("""test('FUNC-001: a', async ({ page }) => { await page.goto('/'); });
test('FUNC-002: b', async () => { expect(true).toBe(true); });
""")
    assert any("no assertion" in m for m in msgs)
    assert any("tautological" in m for m in msgs)


def test_kit_helpers_count_as_assertions():
    assert errors("test('SEC-001: h', async ({ request }) => { expectSecurityHeaders(await request.get('/')); });") == []


def test_commented_out_code_is_ignored():
    src = GOOD.replace("await page.goto('/');", "// await page.waitForTimeout(1000);\n  await page.goto('/');")
    assert errors(src) == []
    assert "waitForTimeout" not in strip_comments("// await page.waitForTimeout(1)")


def test_regex_test_calls_are_not_test_declarations():
    tests, _ = extract_tests("test('FUNC-001: a', async () => { expect(/a/.test('a')).toBe(true); });", "f")
    assert [t.id for t in tests] == ["FUNC-001"]


def test_match_brace():
    src = "{ a: '}', b: `${ {c: 1} }` }"
    assert match_brace(src, 0) == len(src) - 1


def _plan(*cases) -> TestPlan:
    tests = []
    for tid, cat, group in cases:
        tests.append({"id": tid, "title": "a title", "category": cat, "group": group, "priority": "P1",
                      "req_ids": ["REQ-001"], "steps": ["s"], "expected": ["e"]})
    return TestPlan.model_validate({"tests": tests})


def test_lint_specs_against_plan(tmp_path: Path):
    specs = tmp_path / "acceptance" / "specs"
    (specs / "functional").mkdir(parents=True)
    (specs / "security").mkdir()
    (specs / "functional" / "notes.spec.ts").write_text(GOOD + """
test('FUNC-009: not planned', async () => { expect(1 + 1).toBe(2); });
test('SEC-001: wrong folder', async () => { expect(1 + 1).toBe(2); });
""")
    (specs / "security" / "dup.spec.ts").write_text("test('FUNC-001: dup', async () => { expect(1 + 1).toBe(2); });")
    plan = _plan(("FUNC-001", "functional", "notes"), ("FUNC-002", "functional", "notes"), ("SEC-001", "security", "headers"))
    report = lint_specs(specs, plan, tmp_path)
    msgs = {(i.test_id, i.message.split(";")[0]) for i in report.errors}
    assert ("FUNC-009", "test id is not in harness/test_plan.json") in msgs
    assert ("SEC-001", "security tests belong under specs/security/") in msgs
    assert any(tid == "FUNC-001" and m.startswith("duplicate") for tid, m in msgs)
    assert ("FUNC-002", "planned test is not implemented") in msgs


def test_requests_leave_the_app_only_as_payload_data():
    src = """import { test, expect } from '../../support';
test('SEC-001: a', async ({ page, request, playwright }) => {
  await page.goto('/login?next=https://evil.example/');
  await request.get(`/search?q=${encodeURIComponent('<a href="https://x.example">')}`);
  await page.goto('https://evil.example/');
  await request.post("//evil.example/collect", { data: {} });
  await request.get(`http://127.0.0.1:3000/api`);
  await fetch('https://evil.example');
  await playwright.request.newContext({ baseURL: 'https://evil.example' });
  expect(1 + 1).toBe(2);
});
"""
    _, issues = lint_source(src, "acceptance/specs/security/x.spec.ts")
    destination = [i.line for i in issues if i.severity == "error" and "relative paths" in i.message]
    assert destination == [5, 6, 7, 8, 9]
    assert {i.line for i in issues if i.severity == "warning"} >= {3, 4}  # payloads: reported, not blocking


def test_specs_import_only_from_the_kit():
    src = """import { test, expect } from '../../support';
import type { Page } from '@playwright/test';
import { request as raw } from '@playwright/test';
import * as http from 'node:http';
import 'node:child_process';
test('FUNC-001: a', async () => {
  const fs = await import('node:fs');
  const https = require('https');
  expect(fs && https).toBeTruthy();
});
"""
    msgs = errors(src)
    rejected = sorted(m.split("'")[1] for m in msgs if m.startswith("import from"))
    assert rejected == ["@playwright/test", "node:child_process", "node:http"]  # the type-only import is fine
    assert sum("require()/dynamic import()" in m for m in msgs) == 2


def test_symlinked_specs_are_rejected(tmp_path: Path):
    import os

    specs = tmp_path / "acceptance" / "specs" / "functional"
    specs.mkdir(parents=True)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.spec.ts").write_text(GOOD)
    os.symlink(tmp_path / "app" / "x.spec.ts", specs / "linked.spec.ts")
    report = lint_specs(tmp_path / "acceptance" / "specs", _plan(("FUNC-001", "functional", "notes")), tmp_path, expected_ids=[])
    assert [i.message for i in report.errors] == ["symlinks are not allowed in the suite; write a regular spec file"]
    assert report.found == {}
