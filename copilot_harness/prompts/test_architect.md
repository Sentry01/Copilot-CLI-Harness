# Task: design the acceptance test plan

Inputs: `harness/requirements.json`, `harness/contract.json`, `harness/PRD.md`$change_note.

Write `harness/test_plan.json`:

```json
{
  "version": 1,
  "tests": [
    {
      "id": "FUNC-001",
      "title": "Registering with a valid email and password lands on the dashboard",
      "category": "functional",
      "group": "registration",
      "priority": "P0",
      "kind": "positive",
      "layer": "e2e",
      "req_ids": ["REQ-001"],
      "preconditions": ["App state reset (automatic)"],
      "steps": ["Open /register", "Fill Email with a unique address and Password with a valid password", "Click 'Create account'"],
      "expected": ["URL is /dashboard", "Heading 'Welcome' is visible", "GET /api/me returns the new email"],
      "tags": [],
      "origin": "$origin",
      "status": "active"
    }
  ]
}
```

Rules:
- **Size**: $size_rule
- **Categories and ID prefixes**: `functional` → `FUNC-###`, `performance` → `PERF-###`,
  `security` → `SEC-###`, `usability` → `UX-###` (accessibility, keyboard, responsive, error
  and empty states). Number IDs sequentially within each prefix, starting at $next_ids.
- **Distribution**: $distribution_rule
- **Traceability**: every test lists the requirement IDs it verifies in `req_ids`. Every
  active, non-constraint requirement must have at least one test. Every P0 functional or
  security requirement needs at least two, including one `negative` or `boundary` test.
- **Groups**: `group` is a kebab-case feature area. Each `(category, group)` becomes one spec
  file, `acceptance/specs/<category>/<group>.spec.ts`, so keep groups cohesive and at most
  $max_group tests.
- **Oracles**: `expected` lists concrete, observable assertions: exact text, URL, status code,
  element state, header value, or a numeric budget. Avoid vague words like "works", "correct"
  or "properly".
- **Determinism**: each test sets up its own data through the UI or API. It does not rely on
  other tests or their order. App state is reset automatically before every test (contract
  `test_hooks.reset`).
- **Performance tests** must set `threshold`, taken from the requirement, e.g.
  `{"metric": "api_p95_ms", "operator": "<=", "value": 300, "unit": "ms"}`. They will be
  measured with the kit's sampling helpers (p50/p95 over many samples after warm-up).
- **Security tests** are black-box: authz (access another user's resource → 403/404), authn
  (no session → 401), XSS (payload rendered inert), SQL injection (no 500 and no data leak),
  headers, cookie flags, rate limiting, no error-detail leaks, oversized input.
- **Usability tests**: axe (no serious or critical violations) on every key page, keyboard-only
  completion of key flows, visible focus, labelled inputs, clear validation messages, no
  horizontal overflow at 375px.
- Use `layer: "api"` for tests that only exercise HTTP endpoints, and `"e2e"` for browser
  tests.

## Working method (the file is large)
Build the file incrementally rather than in one giant write: create it with the first
section (e.g. one category or feature area), then append the remaining sections in further edits. After each step, validate it:
`node -e "JSON.parse(require('fs').readFileSync('harness/test_plan.json','utf8'))" && echo valid`.
Fix any JSON error before continuing.
$delta_rules
When the file is complete and valid JSON, stop.
