# Task: turn the PRD into atomic, testable requirements

Read `harness/PRD.md`$change_note.

Write `harness/requirements.json` with this exact shape:

```json
{
  "version": 1,
  "product": "Short product name",
  "summary": "Two or three sentences on what the product does and for whom.",
  "requirements": [
    {
      "id": "REQ-001",
      "title": "User can register with email and password",
      "type": "functional",
      "priority": "P0",
      "description": "What must be true, in one or two sentences.",
      "acceptance_criteria": [
        "Given a visitor on /register, when they submit a valid email and an 8+ character password, then an account is created and they land on /dashboard",
        "Given an email that is already registered, when they submit it, then they see 'Email already registered' and no account is created"
      ],
      "thresholds": [],
      "origin": "$origin",
      "status": "active",
      "superseded_by": [],
      "assumption": ""
    }
  ],
  "out_of_scope": ["..."],
  "assumptions": ["..."]
}
```

Field rules:
- `type`: `functional`, `performance`, `security`, `usability` (includes accessibility and
  responsive design) or `constraint` (technology or stack choices, not directly testable).
- `priority`: `P0` = core flow, the product is useless without it. `P1` = expected.
  `P2` = nice to have.
- `acceptance_criteria`: observable Given/When/Then statements. Each one should be checkable
  by a black-box test through the UI or HTTP API. Name concrete values, messages and routes.
- `thresholds` (required for every performance requirement, optional otherwise), e.g.
  `{"metric": "api_p95_ms", "operator": "<=", "value": 300, "unit": "ms"}`,
  `{"metric": "lcp_ms", "operator": "<=", "value": 2500, "unit": "ms"}`,
  `{"metric": "axe_serious_violations", "operator": "==", "value": 0, "unit": "count"}`.
- `assumption`: non-empty when the requirement is not stated in the PRD but you inferred it.
  Say why.

Coverage expectations: a PRD rarely spells out non-functional needs, so derive them.
- **Security**: authentication and session handling, authorisation (users cannot read or
  modify others' data), input validation, output encoding (XSS), injection, security headers,
  secure cookies, rate limiting of auth endpoints, and no internal error details in responses.
- **Performance**: API latency percentiles for key endpoints, page load (LCP/FCP/TTFB) for key
  pages, behaviour under modest concurrency, and page weight budgets.
- **Usability**: WCAG 2.1 AA (axe), keyboard operability, visible focus, form labels and
  error messages, responsive layouts (375px / 768px / 1280px), empty and loading states.
- Functional: every user-visible capability in the PRD, including validation and error paths.

Aim for 40–90 requirements: atomic (one behaviour each), unambiguous, and traceable to the PRD.

## Working method (the file is large)
Build the file incrementally rather than in one giant write: create it with the first
section (e.g. one category or feature area), then append the remaining sections in further edits. After each step, validate it:
`node -e "JSON.parse(require('fs').readFileSync('harness/requirements.json','utf8'))" && echo valid`.
Fix any JSON error before continuing.
$delta_rules
When the file is complete and valid JSON, stop.
